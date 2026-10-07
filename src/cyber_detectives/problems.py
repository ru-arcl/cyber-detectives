"""ICRA Problems 2-4, plus executable errata of the paper's own procedures for them.

- Problem 2 (:func:`validate_intervals`): the story covers ``[t0, tf]`` and the sensors cover
  ``[t0', tf']``, related as in one of the six interval cases of ICRA §III-B.
- Problem 3 (:func:`shortest_superstory`): partial story; a shortest consistent story ``p'``
  that contains ``p`` as a subsequence.
- Problem 4 (:func:`closest_story`): story with errors; a consistent story with the fewest
  unit-cost insertions, deletions and substitutions, with the edit operations.

All three are shortest-path / reachability questions on one product automaton (the engine's,
``engine.py``, extended by a *phase*).  A state is

    (h, phase, position, k, fresh, anchor)

- ``h``: recordings completed (``0..m``); recordings move ``h`` to ``h + 1`` exactly as in
  Problem 1 (:meth:`engine._Problem.event_moves`).
- ``phase``: how many of the interval boundaries (``t0``, ``tf``, ``t0'``, ``tf'``) have passed,
  in the order fixed by the interval case.  Problem 1 (and so Problems 3-4) has the two
  combined boundaries ``t0 = t0'`` and ``tf = tf'``.  Between two boundaries the story is
  *on* iff ``t0`` has passed and ``tf`` has not, and the sensors are *on* iff ``t0'`` has passed
  and ``tf'`` has not.
- ``position``: a room, a free region, or the inside of an occupancy region (as in the engine).
- ``k``: story elements of ``p`` accounted for (matched, substituted or deleted).
- ``fresh``, ``anchor`` (only with ``unreported_visits=True``; ``None`` otherwise): x must end
  inside the room it reported last.  ``fresh`` (rooms only) says whether x is in that room;
  ``anchor`` is ``p'_1`` until the first reported visit (x may come back to ``p'_1``
  unreported), then ``None``.  Any later room entry can be taken as the last reported visit
  (an earlier report of the same room moves there), so ``fresh`` is "entered by a reported
  visit, or re-entered ``anchor``".

Moves (``docs/notes/paper-examples-icra.md`` §1 gives the interval model):

- room -> touching region: always free.
- region -> room: while the story is on, a reported visit (which must match ``p[k]`` for a
  plain verdict; Problems 3/4 may also insert / substitute at cost 1) or, with
  ``unreported_visits``, an unreported one; while the story is off, an unreported entry.
- region <-> occupancy region, and crossing a beam: free while the sensors are off (nothing
  records them).  While they are on: single agent only through the recordings; multi-agent
  occupancy regions are passable while active (as in the engine).
- deletion (Problem 4): ``k -> k + 1`` in place, cost 1, any time before ``tf``.
- boundaries: ``t0`` needs x inside a room, which becomes the first story element (``p_1``
  for a verdict); ``tf`` needs x inside a room it reported last, with all of ``p``
  accounted for (``p_n`` for a verdict and for anchored Problem 3); ``t0'`` / ``tf'`` need x
  outside every occupancy region.
- before the first boundary x may be anywhere (all positions are start states); after the
  last one nothing is constrained, so a state with ``h = m`` and every boundary passed is a
  goal.

All costs are 0 or 1.  The search is Dijkstra on ``(cost, transitions)``, so among the
cheapest answers the witness has the fewest steps (no pointless detours), in
``O(states log states)`` with ``states = (m+1) * phases * |positions| * (n+1) * 2``.
Successors are generated in map order and remaining ties go to the state reached first, so
answers and witnesses are deterministic.

The original Java code has no counterpart, so ``compat`` other than ``None`` raises
``ValueError``.

The last section ("paper-literal") implements ICRA Algorithm 2 VALIDATEPARTIALSTORY exactly
as printed, its corrected form, and the paper's Problem 2 case-2 procedure, so that the
errata of ``docs/notes/paper-examples-icra.md`` §7 (items 1 and 2) are executable.
"""

from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass
from typing import (Any, Dict, FrozenSet, Iterator, List, NamedTuple, Optional, Sequence,
                    Tuple)

from .engine import AGENTS, Result, Step, _Problem, path_to_string, validate
from .history import (Event, HistoryLike, InputError, StoryLike, check_history,
                      parse_history, parse_story)
from .maps import Map

__all__ = [
    "validate_intervals", "shortest_superstory", "closest_story",
    "IntervalResult", "SuperstoryResult", "ClosestResult", "EditOp",
    "INTERVAL_CASES",
    # paper-literal (errata made executable)
    "algorithm2_as_printed", "algorithm2_corrected", "case2_procedure_as_printed",
]

#: ICRA §III-B interval cases (unprimed = story interval, primed = sensors' interval).
INTERVAL_CASES: Dict[int, str] = {
    1: "t0 < tf < t0' < tf'",
    2: "t0 < t0' < tf < tf'",
    3: "t0' < t0 < tf < tf'",
    4: "t0 < t0' < tf' < tf",
    5: "t0' < t0 < tf' < tf",
    6: "t0' < tf' < t0 < tf",
}

# Boundary sequences.  Each entry is the set of boundaries passed at one instant.
_P1_MARKS: Tuple[FrozenSet[str], ...] = (frozenset(("t0", "t0'")), frozenset(("tf", "tf'")))
_CASE_MARKS: Dict[int, Tuple[FrozenSet[str], ...]] = {
    c: tuple(frozenset((x,)) for x in INTERVAL_CASES[c].split(" < ")) for c in INTERVAL_CASES}

_VERDICT, _SUPER, _EDIT = "verdict", "superstory", "edit"
_INF = float("inf")


def _no_compat(compat: Optional[str]) -> None:
    if compat is not None:
        raise ValueError("compat=%r: Problems 2-4 have no original code" % (compat,))


def _check_agents(agents: str) -> None:
    if agents not in AGENTS:
        raise ValueError("agents must be 'single' or 'multi', got %r" % (agents,))


# ---------------------------------------------------------------------- result types


@dataclass
class IntervalResult(Result):
    """Outcome of :func:`validate_intervals` (a :class:`~cyber_detectives.engine.Result`).

    ``path`` is a witness walk over the whole timeline.  Besides the engine's step kinds it
    uses ``"begin"`` (where x is before the first boundary; time 0), ``"mark"`` (a boundary
    passes; ``sensor`` = ``"t0"``, ``"tf"``, ``"t0'"`` or ``"tf'"``, comma-joined if
    simultaneous), ``"pass"`` (an unrecorded beam crossing while the sensors are off;
    ``sensor`` = the side crossed from) and ``"unseen"`` (entering or leaving an occupancy
    region while the sensors are off; ``sensor`` = the occupancy sensor).
    Room entries outside the story interval are ``"unreported"``.  :func:`replay` does not
    accept these walks.
    """

    case: int = 2

    def path_string(self) -> Optional[str]:
        """Witness as text: rooms reported in the story interval as their names, other room
        entries ``(X)``, recordings ``[b1u]`` / ``[o1]``, unrecorded passes ``<b1u>`` /
        ``<o1>``, boundaries ``|t0|``."""
        if self.path is None:
            return None
        out: List[str] = []
        for s in self.path:
            if s.kind == "mark":
                out.append("|%s|" % s.sensor)
                if "t0" in s.sensor.split(","):
                    out.append(s.position)  # x is inside p_1 at t0
            elif s.kind == "pass" or (s.kind == "unseen" and s.position == s.sensor):
                out.append("<%s>" % s.sensor)  # unrecorded crossing / occupancy entry
            elif s.kind != "begin":
                out.append(path_to_string([s]))
        return "".join(out)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["case"] = self.case
        d["interval"] = INTERVAL_CASES[self.case]
        return d


class EditOp(NamedTuple):
    """One edit turning the given story ``p`` into the answer ``p'`` (Problem 4).

    ``op`` is ``"substitute"``, ``"insert"`` or ``"delete"``; ``index`` is the 0-based
    position in ``p`` (for ``"insert"``: the new room goes before ``p[index]``, or at the end
    when ``index == len(p)``); ``old`` / ``new`` are the room removed / added (``None`` when
    not applicable).
    """

    op: str
    index: int
    old: Optional[str]
    new: Optional[str]

    def __str__(self) -> str:
        if self.op == "substitute":
            return "substitute %s at %d by %s" % (self.old, self.index, self.new)
        if self.op == "insert":
            return "insert %s before %d" % (self.new, self.index)
        return "delete %s at %d" % (self.old, self.index)


@dataclass
class SuperstoryResult:
    """Outcome of :func:`shortest_superstory` (``None`` is returned when no ``p'`` exists).

    ``story`` is a shortest consistent ``p' >= p``; ``length == len(story)``; ``inserted``
    lists the 0-based positions of ``p'`` that are not matched to ``p``; ``path`` is a witness
    walk for ``p'`` (engine :class:`Step` list, checkable with
    ``replay(m, path, history, agents, story=p')``).
    """

    story: List[str]
    length: int
    inserted: List[int]
    path: List[Step]
    anchored: bool = True
    agents: str = "single"
    consistent: bool = True

    def path_string(self) -> str:
        """The witness in the engine's notation (see :func:`engine.path_to_string`)."""
        return path_to_string(self.path)

    def to_dict(self) -> Dict[str, Any]:
        return {"story": list(self.story), "length": self.length,
                "inserted": list(self.inserted), "anchored": self.anchored,
                "agents": self.agents, "path_string": self.path_string(),
                "path": [s.to_dict() for s in self.path]}


@dataclass
class ClosestResult:
    """Outcome of :func:`closest_story` (``None`` is returned when no story is consistent).

    ``story`` is a consistent ``p'`` at minimum edit distance ``edits`` from ``p``;
    ``operations`` lists exactly ``edits`` :class:`EditOp` in story order; ``path`` is a witness
    walk for ``p'`` (checkable with ``replay``).
    """

    story: List[str]
    edits: int
    operations: List[EditOp]
    path: List[Step]
    agents: str = "single"
    consistent: bool = True

    def path_string(self) -> str:
        return path_to_string(self.path)

    def to_dict(self) -> Dict[str, Any]:
        return {"story": list(self.story), "edits": self.edits,
                "operations": [str(o) for o in self.operations], "agents": self.agents,
                "path_string": self.path_string(),
                "path": [s.to_dict() for s in self.path]}


# ---------------------------------------------------------------------- the search

_State = Tuple[int, int, str, int, Optional[bool], Optional[str]]


class _Info(NamedTuple):
    """How a transition was taken (for the witness)."""

    kind: str                 # visit/unreported/move/pass/cross/enter/exit/wait/delete/mark
    position: str
    sensor: Optional[str] = None
    event: Optional[int] = None
    op: Optional[str] = None  # match/insert/substitute (reported visits, t0), delete


class _Timeline:
    """One search problem: map, story, history, boundary sequence and objective."""

    def __init__(self, m: Map, story: List[str], events: List[Event], multi: bool,
                 loose: bool, marks: Sequence[FrozenSet[str]], mode: str,
                 anchored: bool = True) -> None:
        self.m = m
        self.story = story
        self.n = len(story)
        self.events = events
        self.n_ev = len(events)
        self.multi = multi
        self.loose = loose
        self.marks = tuple(marks)
        self.mode = mode
        self.anchored = anchored
        # the engine's event transitions and per-interval active occupancy sets
        self.base = _Problem(m, story, events, multi, loose)
        self.flags: List[Tuple[bool, bool, bool]] = []  # (story_on, sensors_on, tf_passed)
        passed: set = set()
        for ph in range(len(self.marks) + 1):
            if ph:
                passed |= self.marks[ph - 1]
            self.flags.append(("t0" in passed and "tf" not in passed,
                               "t0'" in passed and "tf'" not in passed,
                               "tf" in passed))
        self.positions: List[str] = list(m.rooms) + list(m.regions) + list(m.occupancy)

    # -- reported visits ------------------------------------------------------------

    def _report(self, room: str, k: int, first: bool) -> Iterator[Tuple[int, int, str]]:
        """Ways to report entering ``room`` with ``k`` elements of ``p`` accounted for:
        ``(cost, new k, op)``.  ``first``: this is ``p'_1`` (the ``t0`` boundary)."""
        matches = k < self.n and self.story[k] == room
        if matches:
            yield 0, k + 1, "match"
        if self.mode == _VERDICT:
            return
        if self.mode == _SUPER:
            if not (first and self.anchored):
                yield 1, k, "insert"
            return
        if k < self.n and not matches:
            yield 1, k + 1, "substitute"
        yield 1, k, "insert"

    # -- transitions ----------------------------------------------------------------

    def _entered(self, room: str, reported: bool, anchor: Optional[str]
                 ) -> Tuple[Optional[bool], Optional[str]]:
        """``(fresh, anchor)`` after entering ``room`` (both ``None`` in strict mode, where
        every entry while the story is on is reported)."""
        if not self.loose:
            return None, None
        if reported:
            return True, None
        return anchor == room, anchor

    def successors(self, st: _State) -> Iterator[Tuple[int, _State, _Info]]:
        h, ph, pos, k, fresh, anchor = st
        m = self.m
        story_on, sensors_on, tf_passed = self.flags[ph]
        kind = m.kind(pos)
        # 1. the next boundary
        if ph < len(self.marks):
            yield from self._mark(st)
        # 2. free moves inside the current interval
        if kind == "room":
            for r in m.regions_of[pos]:
                yield 0, (h, ph, r, k, None, anchor), _Info("move", r)
        elif kind == "region":
            for f in m.regions[pos]:
                fk = m.kind(f)
                if fk == "room":
                    if story_on:
                        for c, nk, op in self._report(f, k, False):
                            yield (c, (h, ph, f, nk) + self._entered(f, True, anchor),
                                   _Info("visit", f, op=op))
                    if self.loose or not story_on:
                        yield (0, (h, ph, f, k) + self._entered(f, False, anchor),
                               _Info("unreported", f))
                elif fk == "occupancy":
                    if not sensors_on:
                        yield 0, (h, ph, f, k, None, anchor), _Info("unseen", f, f)
                    elif self.multi and f in self.base.active[h]:
                        yield 0, (h, ph, f, k, None, anchor), _Info("move", f, f)
                elif not sensors_on:  # beam side: an unrecorded crossing
                    dst = m.side_region(m.other_side(f))
                    yield 0, (h, ph, dst, k, None, anchor), _Info("pass", dst, f)
        elif kind == "occupancy":
            if not sensors_on or (self.multi and pos in self.base.active[h]):
                for r in m.regions_of[pos]:
                    yield (0, (h, ph, r, k, None, anchor),
                           _Info("move" if sensors_on else "unseen", r, pos))
        # 3. deletion of p[k] (Problem 4)
        if self.mode == _EDIT and k < self.n and not tf_passed:
            yield 1, (h, ph, pos, k + 1, fresh, anchor), _Info("delete", pos, op="delete")
        # 4. the next recording (only inside the sensors' interval)
        if sensors_on and h < self.n_ev:
            for npos, step in self.base.event_moves(h, pos, k):
                nf = fresh if npos == pos else None  # multi: x may stay in a room
                if step is None:
                    yield 0, (h + 1, ph, npos, k, nf, anchor), _Info("wait", npos, event=h)
                else:
                    yield (0, (h + 1, ph, npos, k, nf, anchor),
                           _Info(step.kind, npos, step.sensor, h))

    def _mark(self, st: _State) -> Iterator[Tuple[int, _State, _Info]]:
        h, ph, pos, k, fresh, anchor = st
        mk = self.marks[ph]
        label = ",".join(x for x in ("t0", "t0'", "tf", "tf'") if x in mk)
        kind = self.m.kind(pos)
        if ("t0'" in mk or "tf'" in mk) and kind == "occupancy":
            return
        if "tf" in mk:
            # x must be inside the room it reported last.  With unreported visits, WLOG the
            # last reported visit is x's final room entry (an earlier report of the same room
            # can be moved there, deletions included); the exception is a story whose only
            # reported element so far is p'_1, where x may come back to it unreported.
            if kind != "room" or k != self.n or (self.loose and not fresh):
                return
            if (self.mode == _VERDICT or (self.mode == _SUPER and self.anchored)) \
                    and pos != self.story[-1]:
                return
        if "t0" in mk:
            if kind != "room":
                return
            nf, na = (True, pos) if self.loose else (None, None)
            for c, nk, op in self._report(pos, k, True):
                yield c, (h, ph + 1, pos, nk, nf, na), _Info("mark", pos, label, op=op)
            return
        yield 0, (h, ph + 1, pos, k, fresh, anchor), _Info("mark", pos, label)

    def is_goal(self, st: _State) -> bool:
        return st[0] == self.n_ev and st[1] == len(self.marks)

    # -- search -------------------------------------------------------------------------

    def solve(self) -> Optional[Tuple[int, List[Tuple[_State, Optional[_Info]]]]]:
        """Cheapest goal; returns ``(cost, [(state, info that led to it), ...])`` or None.

        Dijkstra on ``(cost, transitions)``: among the cheapest answers the witness takes the
        fewest steps (no pointless detours); remaining ties go to the state reached first in
        successor (map) order, so the result is deterministic.
        """
        dist: Dict[_State, Tuple[int, int]] = {}
        parent: Dict[_State, Optional[Tuple[_State, _Info]]] = {}
        heap: List[Tuple[int, int, int, _State]] = []
        seq = 0
        for p in self.positions:  # before the first boundary x may be anywhere
            s0: _State = (0, 0, p, 0, None, None)
            dist[s0] = (0, 0)
            parent[s0] = None
            heap.append((0, 0, seq, s0))
            seq += 1
        heapq.heapify(heap)
        while heap:
            d, mv, _seq, st = heapq.heappop(heap)
            if (d, mv) > dist[st]:
                continue
            if self.is_goal(st):
                chain: List[Tuple[_State, Optional[_Info]]] = []
                cur: Optional[_State] = st
                while cur is not None:
                    pr = parent[cur]
                    chain.append((cur, pr[1] if pr else None))
                    cur = pr[0] if pr else None
                chain.reverse()
                return d, chain
            for c, nst, info in self.successors(st):
                key = (d + c, mv + 1)
                old = dist.get(nst)
                if old is None or key < old:
                    dist[nst] = key
                    parent[nst] = (st, info)
                    heapq.heappush(heap, (key[0], key[1], seq, nst))
                    seq += 1
        return None


# ---------------------------------------------------------------------- witnesses


def _story_walk(tl: _Timeline, chain: List[Tuple[_State, Optional[_Info]]]
                ) -> Tuple[List[str], List[Step], List[EditOp], List[int]]:
    """Turn a Problem 1-timeline chain into ``(p', steps, edit ops, inserted positions)``.

    ``steps`` are engine :class:`Step` objects for the story ``p'`` (``story_index`` counts
    elements of ``p'``), so :func:`replay` can check them.  Moves before ``t0`` are dropped.
    """
    new: List[str] = []
    steps: List[Step] = []
    ops: List[EditOp] = []
    inserted: List[int] = []
    started = False

    def report(prev_k: int, room: str, op: Optional[str]) -> None:
        new.append(room)
        if op == "insert":
            inserted.append(len(new) - 1)
            ops.append(EditOp("insert", prev_k, None, room))
        elif op == "substitute":
            ops.append(EditOp("substitute", prev_k, tl.story[prev_k], room))

    prev: Optional[_State] = None
    for st, info in chain:
        if info is None:
            prev = st
            continue
        assert prev is not None
        h, k_prev = prev[0], prev[3]
        if info.kind == "delete":
            ops.append(EditOp("delete", k_prev, tl.story[k_prev], None))
        elif info.kind == "mark":
            if "t0" in info.sensor.split(","):
                started = True
                report(k_prev, info.position, info.op)
                steps.append(Step("start", info.position, 0, 1))
            elif "tf" in info.sensor.split(","):
                break
        elif not started:
            pass
        elif info.kind == "visit":
            report(k_prev, info.position, info.op)
            steps.append(Step("visit", info.position, h, len(new)))
        elif info.kind == "unreported":
            steps.append(Step("unreported", info.position, h, len(new)))
        elif info.kind == "move":
            steps.append(Step("move", info.position, h, len(new), sensor=info.sensor))
        elif info.kind in ("cross", "enter", "exit"):
            steps.append(Step(info.kind, info.position, info.event + 1, len(new),
                              sensor=info.sensor, event=info.event))
        elif info.kind == "wait":
            pass
        else:  # pragma: no cover - pass/unseen cannot happen on the Problem 1 timeline
            raise AssertionError("unexpected transition %r" % (info,))
        prev = st
    return new, steps, ops, inserted


def _interval_walk(chain: List[Tuple[_State, Optional[_Info]]]) -> List[Step]:
    """Turn a Problem 2 chain into a Step list (see :class:`IntervalResult`)."""
    steps: List[Step] = []
    reported = 0
    prev: Optional[_State] = None
    for st, info in chain:
        if info is None:
            steps.append(Step("begin", st[2], 0, 0))
            prev = st
            continue
        assert prev is not None
        h = prev[0]
        if info.kind == "mark":
            if info.op is not None:  # t0: x is inside p_1
                reported = 1
            steps.append(Step("mark", info.position, h, reported, sensor=info.sensor))
        elif info.kind == "visit":
            reported += 1
            steps.append(Step("visit", info.position, h, reported))
        elif info.kind in ("unreported", "move", "pass", "unseen"):
            steps.append(Step(info.kind, info.position, h, reported, sensor=info.sensor))
        elif info.kind in ("cross", "enter", "exit"):
            steps.append(Step(info.kind, info.position, info.event + 1, reported,
                              sensor=info.sensor, event=info.event))
        prev = st
    return steps


# ---------------------------------------------------------------------- public API


def _inputs(m: Map, story: StoryLike, history: HistoryLike, agents: str, need_story: bool
            ) -> Tuple[List[str], List[Event], Optional[str]]:
    _check_agents(agents)
    story_l = parse_story(story, m)
    if need_story and not story_l:
        raise InputError("the story must name at least one room")
    events = parse_history(history, m)
    return story_l, events, check_history(m, events, agents)


def validate_intervals(m: Map, story: StoryLike, history: HistoryLike, case: int = 2, *,
                       agents: str = "single", unreported_visits: bool = False,
                       compat: Optional[str] = None) -> IntervalResult:
    """Problem 2: is ``story`` (told for ``[t0, tf]``) consistent with ``history`` (recorded
    over ``[t0', tf']``) when the intervals are related as in ``case`` (ICRA §III-B,
    :data:`INTERVAL_CASES`)?

    Model (``docs/notes/paper-examples-icra.md`` §1): room entries are story elements only
    inside ``[t0, tf]``; sensors record only inside ``[t0', tf']``, and outside it x passes beams
    and occupancy regions unseen; x is inside ``p_1`` at ``t0`` and inside ``p_n`` at ``tf`` with
    the whole story told; every recording is explained by ``tf'`` (single agent: by x); x is
    outside every occupancy region at ``t0'`` and ``tf'``; before the first and after the last
    boundary x is unconstrained.  Exact boundary times are free (the search places them).

    ``agents="multi"`` applies the multi-agent rules inside ``[t0', tf']``.  Returns an
    :class:`IntervalResult` (``consistent``, ``reason``, ``path``, ``case``); a malformed history
    gives ``consistent=False`` with a ``"malformed history: "`` reason.
    """
    _no_compat(compat)
    if case not in INTERVAL_CASES:
        raise ValueError("case must be 1..6, got %r" % (case,))
    story_l, events, bad = _inputs(m, story, history, agents, True)
    if bad is not None:
        return IntervalResult(False, "malformed history: " + bad, None, agents, case=case)
    tl = _Timeline(m, story_l, events, agents == "multi", unreported_visits,
                   _CASE_MARKS[case], _VERDICT)
    sol = tl.solve()
    if sol is None:
        return IntervalResult(False, "no walk tells the story over [t0, tf] and explains the "
                              "history over [t0', tf'] with %s (case %d)"
                              % (INTERVAL_CASES[case], case), None, agents, case=case)
    return IntervalResult(True, None, _interval_walk(sol[1]), agents, case=case)


def shortest_superstory(m: Map, story: StoryLike, history: HistoryLike, *,
                        anchored: bool = True, agents: str = "single",
                        unreported_visits: bool = False,
                        compat: Optional[str] = None) -> Optional[SuperstoryResult]:
    """Problem 3: a shortest story ``p' >= p`` (``p`` a subsequence of ``p'``) consistent with
    ``history``, or ``None`` if there is none.

    ``anchored=True`` (default; ICRA §II-A, ``docs/DESIGN.md`` "Start/end"): x starts in the
    given ``p_1`` and ends in the given ``p_n``, i.e. ``p'_1 = p_1`` and ``p'_last = p_n``; only
    visits in between were forgotten.  ``anchored=False`` is the reading of §V-A
    (``p' = w1 p1 w2 ... pn w(n+1)``) and of Algorithm 2: forgotten visits may also precede
    ``p_1`` and follow ``p_n``; then ``story`` may be empty, and the answer is a shortest
    consistent story (the paper's ``n'``).  ``p'`` uses any rooms of the map.

    Returns a :class:`SuperstoryResult` (``story``, ``length``, ``inserted``, ``path``).  A
    malformed history has no consistent story: ``None``.
    """
    _no_compat(compat)
    story_l, events, bad = _inputs(m, story, history, agents, anchored)
    if bad is not None:
        return None
    tl = _Timeline(m, story_l, events, agents == "multi", unreported_visits, _P1_MARKS,
                   _SUPER, anchored)
    sol = tl.solve()
    if sol is None:
        return None
    new, steps, _ops, inserted = _story_walk(tl, sol[1])
    assert len(new) == len(story_l) + sol[0]
    return SuperstoryResult(new, len(new), inserted, steps, anchored, agents)


def closest_story(m: Map, story: StoryLike, history: HistoryLike, *, agents: str = "single",
                  unreported_visits: bool = False,
                  compat: Optional[str] = None) -> Optional[ClosestResult]:
    """Problem 4: a non-empty story consistent with ``history`` at the fewest unit-cost
    insertions, deletions and substitutions from ``story`` (any rooms of the map may be used),
    or ``None`` if no story is consistent (e.g. a malformed history).

    Returns a :class:`ClosestResult` (``story``, ``edits``, ``operations``, ``path``).  An
    empty ``story`` is allowed (the answer is then a shortest consistent story).
    """
    _no_compat(compat)
    story_l, events, bad = _inputs(m, story, history, agents, False)
    if bad is not None:
        return None
    tl = _Timeline(m, story_l, events, agents == "multi", unreported_visits, _P1_MARKS, _EDIT)
    sol = tl.solve()
    if sol is None:
        return None
    new, steps, ops, _ins = _story_walk(tl, sol[1])
    assert len(ops) == sol[0]
    return ClosestResult(new, sol[0], ops, steps, agents)


# ======================================================================================
# Paper-literal procedures (ICRA 2011), kept to make the errata executable.
#
# They are NOT used by the functions above.  Tests check that they reproduce the paper's
# errors on the counterexamples of docs/notes/paper-examples-icra.md §7 (items 1 and 2).
# ======================================================================================


class _PaperAutomaton:
    """The composite automaton ``M`` of ICRA §IV on the region model (single agent, strict
    room visits), as a weighted graph whose edge weight is the number of symbols read.

    Nodes: ``"S"`` (start state of ``M``, the start of ``M_1``); ``("L", h, pos)``, the state
    "at ``pos`` in ``M_{h+1}``" (so the paper's ``(p_i, j)`` is ``("L", j-1, p_i)``);
    ``("E", h, pos)``, the start (entry) states of ``M_{h+1}``, reached only across recording
    ``h`` (separate copies, see the notes §3); ``"F"``, the accepting state.  Entering a room
    reads its symbol (weight 1); everything else is ``epsilon``.
    """

    def __init__(self, m: Map, events: List[Event]) -> None:
        self.m = m
        self.n_ev = len(events)
        base = _Problem(m, [], events, False, False)
        self.out: Dict[Any, List[Tuple[Any, int]]] = {"S": [], "F": []}
        positions = list(m.rooms) + list(m.regions) + list(m.occupancy)
        for r in m.rooms:
            self.out["S"].append((("L", 0, r), 1))
        self.entries: Dict[int, List[Any]] = {}
        for h in range(self.n_ev + 1):
            for pos in positions:
                node = ("L", h, pos)
                edges = self.out.setdefault(node, [])
                kind = m.kind(pos)
                if kind == "room":
                    edges += [(("L", h, r), 0) for r in m.regions_of[pos]]
                    if h == self.n_ev:
                        edges.append(("F", 0))
                elif kind == "region":
                    edges += [(("L", h, f), 1) for f in m.regions[pos] if m.is_room(f)]
                if h < self.n_ev:
                    for npos, _step in base.event_moves(h, pos, 0):
                        e = ("E", h + 1, npos)
                        if e not in self.out:
                            self.out[e] = [(("L", h + 1, npos), 0)]
                            self.entries.setdefault(h + 1, []).append(e)
                        edges.append((e, 0))
        self._cache: Dict[Any, Dict[Any, int]] = {}

    def start_states(self, j: int) -> List[Any]:
        """Start states ``S_k`` of ``M_j`` (1-based ``j``)."""
        return ["S"] if j == 1 else list(self.entries.get(j - 1, []))

    def _from(self, a: Any) -> Dict[Any, int]:
        if a not in self._cache:
            dist = {a: 0}
            dq = deque([(0, a)])
            while dq:
                d, u = dq.popleft()
                if d > dist[u]:
                    continue
                for v, w in self.out.get(u, ()):
                    if d + w < dist.get(v, _INF):
                        dist[v] = d + w
                        (dq.append if w else dq.appendleft)((d + w, v))
            self._cache[a] = dist
        return self._cache[a]

    def shortest_len(self, a: Any, b: Any, nonempty: bool = False) -> float:
        """SHORTESTLEN(a, b): fewest symbols read from ``a`` to ``b`` (``inf`` if unreachable;
        ``None`` nodes are unreachable).  ``nonempty``: when ``a == b`` a room state must be
        left and re-entered (one visit cannot account for two story elements)."""
        if a is None or b is None:
            return _INF
        if a == b and nonempty:
            return min((w + self._from(v).get(b, _INF) for v, w in self.out.get(a, ())),
                       default=_INF)
        return self._from(a).get(b, _INF)


def _alg2_table(m: Map, story: StoryLike, history: HistoryLike, corrected: bool) -> float:
    p = parse_story(story, m)
    events = parse_history(history, m)
    M = _PaperAutomaton(m, events)
    n, mm = len(p), len(events)

    def state(i: int, j: int) -> Any:
        """The paper's ``(p_i, j)``; ``p_0`` is the start state, ``p_{n+1} = F``."""
        if i == 0:
            return "S" if j == 1 else None
        if i == n + 1:
            return "F" if j == mm + 1 else None
        return ("L", j - 1, p[i - 1])

    L: Dict[Tuple[int, int], float] = {}
    get = lambda i, j: L.get((i, j), _INF)  # noqa: E731 - line 1: "array of inf's"
    L[(0, 1)] = 0                                                          # line 2
    for i in range(1, n + 2):                                              # line 3
        for j in range(1, mm + 2):                                         # line 4
            l: float = _INF                                                # line 5
            for sk in M.start_states(j):                                   # line 6
                if corrected:  # fix: add L(i-1, j') and restrict to j' < j
                    t = min((get(i - 1, jp) + M.shortest_len(state(i - 1, jp), sk)
                             for jp in range(1, j)), default=_INF)
                else:          # line 7 as printed: no L(i-1, .), j' unrestricted
                    t = min(M.shortest_len(state(i - 1, jp), sk) for jp in range(1, mm + 2))
                l = min(l, t + M.shortest_len(sk, state(i, j)))            # line 8
            if corrected:      # fix: add L(i-1, j); a repeated room needs a new visit
                L[(i, j)] = min(l, get(i - 1, j) + M.shortest_len(
                    state(i - 1, j), state(i, j), nonempty=True))
            else:              # line 9 as printed
                L[(i, j)] = min(l, M.shortest_len(state(i - 1, j), state(i, j)))
    return get(n + 1, mm + 1)


def algorithm2_as_printed(m: Map, story: StoryLike, history: HistoryLike) -> bool:
    """ICRA Algorithm 2 VALIDATEPARTIALSTORY exactly as printed (p. 4984).

    Lines 7 and 9 never add ``L(i-1, .)``, so ``L(i, j)`` holds only the length of the last
    segment and the result is ``True`` as soon as some ``(p_n, j')`` reaches ``F``, even when no
    ``p' >= p`` exists (erratum 1 of ``docs/notes/paper-examples-icra.md`` §7; counterexample:
    map ``icra_fig2``, story ``DAD``, history ``b1 b3 o2 o2 b4``).  Unspecified details are
    filled in literally: ``j'`` ranges over ``1..m+1``, ``(p_0, 1)`` is the start state of
    ``M`` and ``(p_{n+1}, m+1)`` its accepting state, and ``SHORTESTLEN(a, a) = 0``.
    Single agent, strict room visits, no history well-formedness check.
    """
    return _alg2_table(m, story, history, corrected=False) != _INF


def algorithm2_corrected(m: Map, story: StoryLike, history: HistoryLike) -> Optional[int]:
    """Algorithm 2 with the fixes of erratum 1: ``t <- min_{j'<j} {L(i-1,j') +
    SHORTESTLEN((p_{i-1},j'), S_k)}``, ``L(i,j) <- min{l, L(i-1,j) + SHORTESTLEN((p_{i-1},j),
    (p_i,j))}`` with a non-empty string when ``p_{i-1} = p_i``.  Returns ``L(n+1, m+1)``, the
    length of a shortest ``p' >= p`` accepted by ``M`` (the *free* reading of Problem 3), or
    ``None`` when there is none.  Single agent, strict room visits."""
    v = _alg2_table(m, story, history, corrected=True)
    return None if v == _INF else int(v)


def case2_procedure_as_printed(m: Map, story: StoryLike, history: HistoryLike) -> bool:
    """ICRA §III-B, Problem 2 case 2 (``t0 < t0' < tf < tf'``) as printed: "run
    VALIDATEAGENTSTORY n times ... story ``(p_i .. p_n)`` and history ``s``", i.e. ``True`` iff
    some suffix of the story is consistent (Problem 1) with the whole history.

    This forces x into ``p_i`` at ``t0'`` and into ``p_n`` at the *last* recording, although the
    story ends at ``tf < tf'``; it is wrong (erratum 2; counterexample: map ``icra_fig2``, story
    ``AB``, history ``b1 b3``, which :func:`validate_intervals` with ``case=2`` accepts).
    """
    p = parse_story(story, m)
    if not p:
        raise InputError("the story must name at least one room")
    return any(validate(m, p[i:], history).consistent for i in range(len(p)))
