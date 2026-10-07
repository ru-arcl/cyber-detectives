"""Default (corrected) Problem 1 engine: is a story consistent with an observation history?

The search is the composite automaton of ICRA §IV, built implicitly on the region model
(``docs/DESIGN.md``, "Algorithm").  A state is ``(position, k)`` after a prefix of the
history, where ``position`` is a room, a free region, or the inside of an occupancy sensor's
region, and ``k`` is the number of story elements accounted for.  Between recordings the agent
closes over free moves; each recording then advances every state across the event.  Every
state keeps a back-pointer, so a consistent verdict comes with a witness walk (a list of
:class:`Step`) that :func:`replay` checks independently.

Movement rules (default semantics):

- room <-> region it touches.  Entering a room consumes the next story element, which must be
  that room (with ``unreported_visits=True`` an entry may also go unreported).
- single agent: every recording is made by x.  Beam ``b``: x crosses from the region of one
  side to the region of the other.  ``o A``: x enters ``o`` from a touching region and stays
  inside; ``o D``: x leaves ``o`` into a touching region.
- multiple agents: x may cross beam ``b`` only at a recording of ``b`` (at most once per
  recording, and need not cross).  x may enter/leave the region of ``o`` freely while ``o`` is
  active, and must be outside when ``o`` deactivates.  Occupancy recordings never move x.
- x starts inside ``p_1`` at ``t_0`` and must be inside ``p_n`` after the last recording, with
  the whole story accounted for.
"""

from __future__ import annotations

import importlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from .history import (Event, HistoryLike, InputError, StoryLike, check_history,
                      parse_history, parse_story)
from .maps import Map

__all__ = ["Step", "Result", "validate", "replay", "InvalidPath", "path_to_string",
           "possible_positions"]

AGENTS = ("single", "multi")


@dataclass(frozen=True)
class Step:
    """One move of agent x in a witness walk.

    Attributes
    ----------
    kind:
        ``"start"``  x is inside ``p_1`` at ``t_0`` (always the first step);
        ``"visit"``  x enters a room and reports it (the next story element);
        ``"move"``   any other free move: room -> region, or region <-> occupancy region
                     (multi-agent only);
        ``"unreported"``  x enters a room without reporting it (``unreported_visits=True``
                     only);
        ``"cross"``  x crosses a beam at recording ``event``;
        ``"enter"``  x enters an occupancy region, causing activation ``event`` (single agent);
        ``"exit"``   x leaves it, causing deactivation ``event`` (single agent).
    position:
        Where x is after the step: a room, region or occupancy sensor name.
    time:
        Number of recordings that have happened once the step is complete.  A free move
        happens in the open interval between recordings ``time`` and ``time + 1`` (1-based);
        an event step has ``time == event + 1``.
    story_index:
        Number of story elements accounted for after the step.
    sensor:
        ``"cross"``: the beam side x crosses *from* (the original's ``[b1u]`` notation);
        ``"enter"``/``"exit"``, and ``"move"`` into or out of an occupancy region: the sensor;
        else ``None``.
    event:
        0-based index of the recording this step explains (``cross``/``enter``/``exit``), else
        ``None``.
    """

    kind: str
    position: str
    time: int
    story_index: int
    sensor: Optional[str] = None
    event: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "position": self.position, "time": self.time,
                "story_index": self.story_index, "sensor": self.sensor, "event": self.event}


@dataclass
class Result:
    """Outcome of :func:`validate`.

    ``consistent`` is the verdict; ``reason`` says why not (or why the history is malformed),
    ``None`` when consistent; ``path`` is the witness walk (``None`` when inconsistent, and
    under ``compat="original"``, which yields only the original's path string).
    """

    consistent: bool
    reason: Optional[str] = None
    path: Optional[List[Step]] = None
    agents: str = "single"
    compat: Optional[str] = None
    _path_string: Optional[str] = field(default=None, repr=False)

    def path_string(self) -> Optional[str]:
        """The witness in the original's notation, e.g. ``"A[b1u]C[o1][o2]B[b2r]AC"``.

        See :func:`path_to_string`.  Under ``compat="original"`` this is the original's
        ``getAgentStory`` string (single agent; ``None`` in multi-agent mode, which computes no
        path).  ``None`` when inconsistent.
        """
        if self.compat is not None:
            return self._path_string
        if self.path is None:
            return None
        return path_to_string(self.path)

    def __bool__(self) -> bool:
        return self.consistent

    def to_dict(self) -> Dict[str, Any]:
        return {"consistent": self.consistent, "reason": self.reason,
                "agents": self.agents, "compat": self.compat,
                "path_string": self.path_string(),
                "path": [s.to_dict() for s in self.path] if self.path is not None else None}


def path_to_string(path: Sequence[Step]) -> str:
    """Render a witness walk in the original's ``getAgentStory`` notation.

    Rooms are written in visit order.  For each recording x explains, the sensor vertex x was
    on *before* the event is written in brackets: ``[b1u]`` = crossed b1 from its ``b1u`` side,
    ``[o1]`` = passed through o1 (written at the activation; the deactivation adds nothing).
    Multi-agent traversals of an occupancy region kept open by the recordings are written as
    ``{o1}``.  An unreported room entry (``unreported_visits=True``) is written ``(D)``.
    """
    out: List[str] = []
    for s in path:
        if s.kind in ("start", "visit"):
            out.append(s.position)
        elif s.kind == "cross":
            out.append("[%s]" % s.sensor)
        elif s.kind == "enter":
            out.append("[%s]" % s.sensor)
        elif s.kind == "move" and s.sensor is not None and s.position == s.sensor:
            out.append("{%s}" % s.sensor)
        elif s.kind == "unreported":
            out.append("(%s)" % s.position)
    return "".join(out)


# ---------------------------------------------------------------------- the search


class _Problem:
    """Pre-computed, read-only data for one search."""

    def __init__(self, m: Map, story: List[str], events: List[Event], multi: bool,
                 loose: bool) -> None:
        self.m = m
        self.story = story
        self.events = events
        self.multi = multi
        self.loose = loose
        self.n = len(story)
        # active[h] = occupancy sensors active in the interval after h recordings (multi)
        self.active: List[frozenset] = []
        cur: Set[str] = set()
        self.active.append(frozenset(cur))
        for e in events:
            if m.is_occupancy(e.sensor):
                if e.kind == "A":
                    cur.add(e.sensor)
                else:
                    cur.discard(e.sensor)
            self.active.append(frozenset(cur))

    def free_moves(self, h: int, pos: str, k: int) -> Iterator[Tuple[str, int, Step]]:
        """Moves available in the open interval after ``h`` recordings."""
        m = self.m
        kind = m.kind(pos)
        if kind == "room":
            for r in m.regions_of[pos]:
                yield r, k, Step("move", r, h, k)
        elif kind == "region":
            for f in m.regions[pos]:
                if m.is_room(f):
                    if k < self.n and self.story[k] == f:
                        yield f, k + 1, Step("visit", f, h, k + 1)
                    if self.loose:
                        yield f, k, Step("unreported", f, h, k)
                elif self.multi and m.is_occupancy(f) and f in self.active[h]:
                    yield f, k, Step("move", f, h, k, sensor=f)
        elif kind == "occupancy":
            # single agent: x stays inside until its own deactivation.
            if self.multi and pos in self.active[h]:
                for r in m.regions_of[pos]:
                    yield r, k, Step("move", r, h, k, sensor=pos)

    def event_moves(self, h: int, pos: str, k: int) -> Iterator[Tuple[str, Optional[Step]]]:
        """Transitions across recording ``h`` (0-based).  ``None`` step = x does not move."""
        m = self.m
        e = self.events[h]
        t = h + 1
        kind = m.kind(pos)
        if m.is_beam(e.sensor):
            if self.multi:
                yield pos, None  # another agent crossed
            if kind == "region":
                for side in m.beams[e.sensor]:
                    if m.side_region(side) == pos:
                        dst = m.side_region(m.other_side(side))
                        yield dst, Step("cross", dst, t, k, sensor=side, event=h)
        elif self.multi:
            # occupancy recordings only change connectivity; x must be out by a deactivation
            if not (e.kind == "D" and pos == e.sensor):
                yield pos, None
        elif e.kind == "A":
            if kind == "region" and e.sensor in m.regions[pos]:
                yield e.sensor, Step("enter", e.sensor, t, k, sensor=e.sensor, event=h)
        else:
            if pos == e.sensor:
                for r in m.regions_of[pos]:
                    yield r, Step("exit", r, t, k, sensor=e.sensor, event=h)


_Key = Tuple[int, str, int]


def _search(p: _Problem) -> Tuple[Optional[List[Step]], str]:
    """Forward search with back-pointers.  Returns ``(path, reason)``; path ``None`` = fail."""
    story, events, n = p.story, p.events, p.n
    m_ev = len(events)
    start: _Key = (0, story[0], 1)
    parent: Dict[_Key, Tuple[Optional[_Key], Optional[Step]]] = {
        start: (None, Step("start", story[0], 0, 1))}
    frontier: List[Tuple[str, int]] = [(story[0], 1)]
    best_k = 1
    layer: Dict[Tuple[str, int], None] = {}
    for h in range(m_ev + 1):
        # closure under free moves (BFS: shortest detours, deterministic order)
        layer = dict.fromkeys(frontier)
        queue = deque(frontier)
        while queue:
            pos, k = queue.popleft()
            for npos, nk, step in p.free_moves(h, pos, k):
                if (npos, nk) not in layer:
                    layer[(npos, nk)] = None
                    parent[(h, npos, nk)] = ((h, pos, k), step)
                    queue.append((npos, nk))
        best_k = max(k for _, k in layer)
        if h == m_ev:
            break
        nxt: Dict[Tuple[str, int], None] = {}
        for pos, k in layer:
            for npos, step in p.event_moves(h, pos, k):
                if (npos, k) not in nxt:
                    nxt[(npos, k)] = None
                    parent[(h + 1, npos, k)] = ((h, pos, k), step)
        if not nxt:
            e = events[h]
            return None, ("no walk consistent with the story so far can explain recording %d "
                          "(%s %s); at most %d of %d story elements were accounted for before it"
                          % (h + 1, e.sensor, e.kind, best_k, n))
        frontier = list(nxt)
    goal = (story[-1], n)
    if goal not in layer:
        if best_k < n:
            return None, ("every recording can be explained, but at most %d of the %d story "
                          "elements can be visited in order (story element %d, %s, cannot follow)"
                          % (best_k, n, best_k + 1, story[best_k]))
        return None, ("the whole story can be visited, but x cannot be inside %s (the last "
                      "story room) after the last recording" % story[-1])
    # back-track
    steps: List[Step] = []
    key: Optional[_Key] = (m_ev, goal[0], goal[1])
    while key is not None:
        prev, step = parent[key]
        if step is not None:
            steps.append(step)
        key = prev
    steps.reverse()
    return steps, ""


def _positions(p: _Problem, starts: List[Tuple[str, int]],
               goal: Optional[Tuple[str, int]]) -> List[List[str]]:
    """Forward-backward pass over the layers of :func:`_search`.

    Forward: the states ``(pos, k)`` reachable in each layer (closure under free moves) from
    ``starts``.  Backward: those from which, through free moves of the layer, the next
    recording and the later layers, a goal state can be reached (``goal`` = the only goal
    state, or ``None``: every state of the last layer is one).  Returns, per layer, the
    positions of the surviving states in map order (rooms, regions, occupancy sensors).
    """
    m = p.m
    m_ev = len(p.events)
    layers: List[Dict[Tuple[str, int], None]] = []
    frontier = list(starts)
    for h in range(m_ev + 1):
        layer = dict.fromkeys(frontier)
        queue = deque(frontier)
        while queue:
            pos, k = queue.popleft()
            for npos, nk, _step in p.free_moves(h, pos, k):
                if (npos, nk) not in layer:
                    layer[(npos, nk)] = None
                    queue.append((npos, nk))
        layers.append(layer)
        if h == m_ev:
            break
        nxt: Dict[Tuple[str, int], None] = {}
        for pos, k in layer:
            for npos, _step in p.event_moves(h, pos, k):
                nxt.setdefault((npos, k), None)
        frontier = list(nxt)
    order = {x: i for i, x in enumerate(list(m.rooms) + list(m.regions) + list(m.occupancy))}
    out: List[List[str]] = [[] for _ in range(m_ev + 1)]
    alive: Set[Tuple[str, int]] = set()  # co-reachable states of layer h + 1
    for h in range(m_ev, -1, -1):
        layer = layers[h]
        if h == m_ev:
            seeds = [s for s in layer if goal is None or s == goal]
        else:
            seeds = [(pos, k) for pos, k in layer
                     if any((npos, k) in alive for npos, _s in p.event_moves(h, pos, k))]
        # reverse closure under the layer's free moves
        back: Dict[Tuple[str, int], List[Tuple[str, int]]] = {}
        for pos, k in layer:
            for npos, nk, _step in p.free_moves(h, pos, k):
                back.setdefault((npos, nk), []).append((pos, k))
        good = set(seeds)
        queue = deque(seeds)
        while queue:
            s = queue.popleft()
            for r in back.get(s, ()):
                if r not in good:
                    good.add(r)
                    queue.append(r)
        alive = good
        out[h] = sorted({pos for pos, _k in good}, key=order.__getitem__)
    return out


# ---------------------------------------------------------------------- public API


STARTS = ("rooms", "anywhere")


def possible_positions(m: Map, history: HistoryLike, story: Optional[StoryLike] = None,
                       agents: str = "single", unreported_visits: bool = False, *,
                       starts: str = "rooms") -> List[List[str]]:
    """Where can agent x be, given the whole history (and the story, if any)?

    Returns one list per time slot: slot ``h`` (``0..m`` for ``m`` recordings) is the open
    interval between recordings ``h`` and ``h + 1`` (slot 0 is before the first recording,
    slot ``m`` after the last).  Each list holds the positions -- rooms, free regions and
    occupancy sensors (for the inside of their region) -- that x occupies at some moment of
    that slot on *some* walk consistent with the whole history, in map order (rooms, regions
    in map order, occupancy sensors).  This is a forward-backward pass over the engine's
    states: a position counts only if it is reachable from the start *and* the rest of the
    history (and story) can still be explained from it.

    - ``story`` given: x follows the rules of :func:`validate` (starts inside ``p_1``, reports
      room entries as the story says -- or not, with ``unreported_visits`` -- and ends inside
      ``p_n`` with the whole story told); the positions are those of the witnesses.  Every
      list is empty iff the story is inconsistent.
    - ``story=None``: the pure sensor filter (the information state of STAR's combinatorial
      filter): room entries are unconstrained and x may end anywhere.  Where x starts is set
      by ``starts``: ``"rooms"`` (default) -- inside some room, as in every story;
      ``"anywhere"`` -- inside some room or in some free region, never inside an occupancy
      region (its sensor is inactive at ``t_0``); this is the filter for an interval that
      begins while x is already on its way (e.g. Problem 2's ``[t0', tf']``).
      ``unreported_visits`` has no effect.

    ``starts`` must be ``"rooms"`` or ``"anywhere"`` (else :class:`ValueError`), and
    ``"anywhere"`` needs ``story=None`` (with a story, x starts inside its first room).

    A malformed history (``check_history``) gives empty lists.  Raises :class:`InputError`
    for unknown names, an unreadable history or an empty story (``story=""``).
    """
    _check_agents(agents)
    if starts not in STARTS:
        raise ValueError("starts must be 'rooms' or 'anywhere', got %r" % (starts,))
    if starts == "anywhere" and story is not None:
        raise ValueError("starts='anywhere' needs story=None (with a story, x starts inside "
                         "its first room)")
    events = parse_history(history, m)
    story_l = None
    if story is not None:
        story_l = parse_story(story, m)
        if not story_l:
            raise InputError("the story must name at least one room")
    if check_history(m, events, agents) is not None:
        return [[] for _ in range(len(events) + 1)]
    multi = agents == "multi"
    if story_l is None:
        p = _Problem(m, [], events, multi, True)
        places = list(m.rooms) + (list(m.regions) if starts == "anywhere" else [])
        return _positions(p, [(x, 0) for x in places], None)
    p = _Problem(m, story_l, events, multi, unreported_visits)
    return _positions(p, [(story_l[0], 1)], (story_l[-1], len(story_l)))


def _check_agents(agents: str) -> None:
    if agents not in AGENTS:
        raise ValueError("agents must be 'single' or 'multi', got %r" % (agents,))


def validate(m: Map, story: StoryLike, history: HistoryLike, agents: str = "single",
             compat: Optional[str] = None, unreported_visits: bool = False) -> Result:
    """Problem 1: is ``story`` consistent with ``history`` on map ``m``?

    Parameters
    ----------
    m:
        The map (:class:`~cyber_detectives.maps.Map`).
    story:
        Room names (see :func:`~cyber_detectives.history.parse_story`); must be non-empty.
    history:
        Recordings (see :func:`~cyber_detectives.history.parse_history`).
    agents:
        ``"single"`` (every recording is made by x; default) or ``"multi"`` (an unknown
        number of other agents may be present; STAR §5).
    compat:
        ``None`` (default, corrected semantics) or ``"original"`` to run the port of the
        original Java code (bugs and crashes included; see ``docs/DESIGN.md``).
    unreported_visits:
        ``True`` lets x enter rooms without reporting them (ICRA's loose reading).  Not
        available with ``compat``.

    Returns
    -------
    Result
        ``consistent``, ``reason``, ``path`` (a witness walk) and ``path_string()``.  A history
        the agent model cannot produce (single agent: unpaired or overlapping occupancy
        intervals; multi: non-alternating activations) gives ``consistent=False`` with a
        ``reason`` starting ``"malformed history: "``.

    Raises
    ------
    InputError
        Unknown room or sensor, empty story, unreadable history.
    ValueError
        Bad ``agents`` / ``compat`` value, or ``compat`` combined with ``unreported_visits``.
    NotImplementedError
        ``compat="original"`` while the compat port is not installed.
    """
    _check_agents(agents)
    if compat is not None:
        if compat != "original":
            raise ValueError("compat must be None or 'original', got %r" % (compat,))
        if unreported_visits:
            raise ValueError("unreported_visits is not available with compat='original'")
        return _validate_original(m, story, history, agents)
    story_l = parse_story(story, m)
    if not story_l:
        raise InputError("the story must name at least one room")
    events = parse_history(history, m)
    bad = check_history(m, events, agents)
    if bad is not None:
        return Result(False, "malformed history: " + bad, None, agents)
    path, reason = _search(_Problem(m, story_l, events, agents == "multi", unreported_visits))
    if path is None:
        return Result(False, reason, None, agents)
    return Result(True, None, path, agents)


def _validate_original(m: Map, story: StoryLike, history: HistoryLike, agents: str) -> Result:
    try:
        mod = importlib.import_module("cyber_detectives.compat.original")
    except ModuleNotFoundError as e:
        if e.name in ("cyber_detectives.compat", "cyber_detectives.compat.original"):
            raise NotImplementedError(
                "compat='original' needs cyber_detectives.compat.original, which is not "
                "available") from None
        raise
    # The original takes raw input: do not reject names it would crash on.
    story_l = parse_story(story)
    events = parse_history(history, m, check_names=False)
    d = m.to_dict()
    # the original's builder reads beams as an object (to_dict may give the pair form)
    d["beams"] = {b: list(ss) for b, ss in m.beams.items()}
    consistent, path_string = mod.validate_compat(
        d, list(story_l), [[e.sensor, e.kind] for e in events], agents)
    return Result(bool(consistent),
                  None if consistent else "the original code returns false",
                  None, agents, "original", path_string if consistent else None)


# ---------------------------------------------------------------------- replay


class InvalidPath(ValueError):
    """A witness walk breaks the rules (raised by :func:`replay`)."""


def replay(m: Map, path: Sequence[Step], history: HistoryLike, agents: str = "single",
           story: Optional[StoryLike] = None, unreported_visits: bool = False) -> None:
    """Check a witness walk independently of the search; raise :class:`InvalidPath` if wrong.

    Verified: the walk starts inside the first story room at time 0; every move joins
    adjacent places, is legal at its time (occupancy regions active in multi-agent mode) and
    names the right ``sensor`` (see :class:`Step`; free moves other than region <-> occupancy
    carry ``None``);
    every recording is explained by exactly one step (single agent) or by at most one beam
    crossing with x never inside a deactivating occupancy region (multi); the reported room
    visits spell the story (``story`` if given, else the visits must at least be
    self-consistent); x ends inside the last story room after the last recording.
    """
    _check_agents(agents)
    multi = agents == "multi"
    events = parse_history(history, m)
    story_l = parse_story(story, m) if story is not None else None
    if not path:
        raise InvalidPath("empty path")

    def fail(i: int, msg: str) -> None:
        raise InvalidPath("step %d (%s): %s" % (i + 1, path[i].kind if i < len(path) else
                                                "end", msg))

    # active occupancy sensors in each interval, recomputed here on purpose
    active = [set()]
    for e in events:
        s = set(active[-1])
        if m.is_occupancy(e.sensor):
            (s.add if e.kind == "A" else s.discard)(e.sensor)
        active.append(s)

    def where(name: str) -> str:
        try:
            return m.kind(name)
        except KeyError:
            return "unknown"

    first = path[0]
    if first.kind != "start" or first.time != 0 or first.story_index != 1:
        fail(0, "a walk must begin with a start step at time 0")
    if where(first.position) != "room":
        fail(0, "x must start inside a room")
    visits = [first.position]
    pos, t, k = first.position, 0, 1

    def wait_until(i: int, until: int) -> None:
        # recordings t..until-1 happen while x stays at pos
        nonlocal t
        for h in range(t, until):
            e = events[h]
            if not multi:
                fail(i, "recording %d (%s %s) is not explained by x" % (h + 1, e.sensor, e.kind))
            if e.kind == "D" and pos == e.sensor:
                fail(i, "x is inside %s when it deactivates (recording %d)" % (pos, h + 1))
        t = until

    for i, s in enumerate(path[1:], start=1):
        if s.time < t or s.time > len(events):
            fail(i, "time %d out of order (now %d)" % (s.time, t))
        if s.kind in ("cross", "enter", "exit"):
            if s.event is None or s.time != s.event + 1:
                fail(i, "event step without matching event index")
            wait_until(i, s.event)
            e = events[s.event]
            if s.kind == "cross":
                if not m.is_beam(e.sensor) or s.sensor not in m.beams[e.sensor]:
                    fail(i, "crossing side %r does not belong to recording %s" % (s.sensor, e))
                if where(pos) != "region" or m.side_region(s.sensor) != pos:
                    fail(i, "x at %s is not next to beam side %s" % (pos, s.sensor))
                new = m.side_region(m.other_side(s.sensor))
            elif multi:
                fail(i, "in multi-agent mode occupancy recordings do not move x")
            elif s.kind == "enter":
                if e != Event(s.sensor, "A") or not m.is_occupancy(e.sensor):
                    fail(i, "enter step does not match recording %s" % (e,))
                if where(pos) != "region" or s.sensor not in m.regions[pos]:
                    fail(i, "x at %s cannot enter %s" % (pos, s.sensor))
                new = s.sensor
            else:
                if e != Event(s.sensor, "D") or pos != s.sensor:
                    fail(i, "exit step does not match recording %s / position %s" % (e, pos))
                new = s.position
                if where(new) != "region" or s.sensor not in m.regions[new]:
                    fail(i, "%s does not open into %s" % (s.sensor, new))
            if s.position != new:
                fail(i, "position %s, expected %s" % (s.position, new))
            pos, t = new, s.time
        elif s.kind in ("visit", "move", "unreported"):
            if s.event is not None:
                fail(i, "free move with an event index")
            wait_until(i, s.time)
            a, b = where(pos), where(s.position)
            # sensor: the occupancy sensor for region <-> occupancy moves, else None
            want = (pos if a == "occupancy" else s.position) if {a, b} == {
                "region", "occupancy"} else None
            if s.sensor != want:
                fail(i, "sensor %r, expected %r" % (s.sensor, want))
            if {a, b} == {"room", "region"}:
                room, reg = (pos, s.position) if a == "room" else (s.position, pos)
                if room not in m.regions[reg]:
                    fail(i, "%s does not touch %s" % (room, reg))
                if b == "room":
                    if s.kind == "visit":
                        visits.append(s.position)
                        k += 1
                    elif s.kind == "move" or not unreported_visits:
                        fail(i, "entering %s without reporting it" % s.position)
                elif s.kind != "move":
                    fail(i, "leaving a room is a plain move")
            elif {a, b} == {"region", "occupancy"}:
                occ, reg = (pos, s.position) if a == "occupancy" else (s.position, pos)
                if s.kind != "move" or not multi:
                    fail(i, "x cannot pass %s without a recording in single-agent mode" % occ)
                if occ not in m.regions[reg]:
                    fail(i, "%s does not touch %s" % (occ, reg))
                if occ not in active[t]:
                    fail(i, "%s is not active between recordings %d and %d" % (occ, t, t + 1))
            else:
                fail(i, "cannot move from %s (%s) to %s (%s)" % (pos, a, s.position, b))
            pos = s.position
        else:
            fail(i, "unknown step kind %r" % s.kind)
        if s.story_index != k:
            fail(i, "story_index %d, expected %d" % (s.story_index, k))
    wait_until(len(path), len(events))
    if story_l is not None and visits != story_l:
        raise InvalidPath("reported visits %s do not spell the story %s"
                          % ("".join(visits), "".join(story_l)))
    if pos != visits[-1] or where(pos) != "room":
        raise InvalidPath("x ends at %s, not inside the last story room %s" % (pos, visits[-1]))
