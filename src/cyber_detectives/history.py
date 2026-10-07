"""Stories and observation histories: parsing and well-formedness checks.

- A **story** is the agent's account of the rooms it visited, in order (``p`` in the papers).
- An **observation history** is the time-ordered list of sensor recordings (``r`` in STAR,
  ``s`` in ICRA).  Each recording is an :class:`Event` ``(sensor, kind)`` with ``kind`` ``"A"``
  (activation) or ``"D"`` (deactivation).  Beam detectors only ever produce ``"A"``.

Accepted input forms are listed in ``docs/DESIGN.md`` ("Public API").
"""

from __future__ import annotations

import re
from typing import (TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, NamedTuple, Optional,
                    Sequence, Union)

if TYPE_CHECKING:  # pragma: no cover
    from .maps import Map

__all__ = [
    "Event",
    "InputError",
    "parse_story",
    "parse_history",
    "history_to_string",
    "check_history",
    "check_single_agent_history",
    "check_multi_agent_history",
]

_SEP_RE = re.compile(r"[\s,]+")


class InputError(ValueError):
    """A story or history cannot be read (unknown room or sensor, bad token, ...).

    This is an error in the input, not an inconsistency: a malformed but readable history
    (e.g. two activations of ``o1`` in a row) is reported by :func:`check_history` and makes
    ``validate`` return an inconsistent result instead.
    """


class Event(NamedTuple):
    """One sensor recording: ``sensor`` name and ``kind`` (``"A"`` or ``"D"``)."""

    sensor: str
    kind: str

    def __str__(self) -> str:
        return "%s %s" % (self.sensor, self.kind)


StoryLike = Union[str, Sequence[str]]
HistoryLike = Union[str, Sequence[Any]]  # list or tuple


def parse_story(story: StoryLike, map: Optional["Map"] = None) -> List[str]:
    """Return the story as a list of room names.

    ``story`` is a string of one-letter room names (``"ACBAC"``), a string separated by spaces
    and/or commas (``"A C B"``, ``"R1,R2"``), or a list (or tuple) of names.  Anything else
    (a mapping, a set, a generator, ...) raises :class:`InputError`.  With ``map``, every name
    must be one of the map's rooms (else :class:`InputError`).  An empty story is returned as
    ``[]``; ``validate`` rejects it.
    """
    if isinstance(story, str):
        s = story.strip()
        if not s:
            names: List[str] = []
        elif _SEP_RE.search(s):
            names = [t for t in _SEP_RE.split(s) if t]
        else:
            names = list(s)
    else:
        if not isinstance(story, (list, tuple)):
            raise InputError("story must be a string or a list of room names, got %r"
                             % (story,))
        names = list(story)
        for x in names:
            if not isinstance(x, str) or not x:
                raise InputError("story element %r is not a room name" % (x,))
    if map is not None:
        for i, x in enumerate(names):
            if not map.is_room(x):
                raise InputError("story element %d (%r) is not a room of map %r (rooms: %s)"
                                 % (i + 1, x, map.name, " ".join(map.rooms)))
    return names


def parse_history(history: HistoryLike, map: Optional["Map"] = None, *,
                  check_names: bool = True) -> List[Event]:
    """Return the history as a list of :class:`Event`.

    Accepted forms:

    - a list (or tuple) whose items are :class:`Event` objects, ``[sensor, kind]`` pairs (the
      fixture format) or ``{"sensor": ..., "kind": ...}`` mappings (exactly those two keys);
    - a string (or a list of string tokens) of sensor names separated by spaces/commas.
      A beam name is a crossing (``"A"``).  An occupancy name toggles that sensor, starting
      from inactive: ``"o2 o2"`` is activation then deactivation (ICRA's ``b1 b3 o2 o2 b4``
      notation).  The explicit forms ``o1+`` / ``o1-`` (activation / deactivation) are also
      accepted and keep the toggle state in step.  Telling beams from occupancy sensors needs
      the ``map``; without one, only explicit ``+``/``-`` tokens are accepted and a bare token
      raises :class:`InputError`.

    With ``map`` and ``check_names`` (default), every sensor must exist in the map and every
    kind must be ``"A"`` or ``"D"``.  ``check_names=False`` passes unknown names through
    (used by ``compat="original"``, which reproduces the original's handling of bad input);
    an unknown bare token in a string is then read as an activation.

    Any other value (a mapping as the whole history, a set, a generator, ...) raises
    :class:`InputError`, with the same message as the JS engine.

    Whether the sequence could have been produced (alternation, single-agent pairing) is not
    checked here: see :func:`check_history`.
    """
    if isinstance(history, str):
        tokens: List[Any] = [t for t in _SEP_RE.split(history.strip()) if t]
        return _parse_tokens(tokens, map, check_names)
    if not isinstance(history, (list, tuple)):
        raise InputError("history must be a string or a list of events, got %r"
                         % (history,))
    items = list(history)
    if items and all(isinstance(x, str) for x in items):
        return _parse_tokens(items, map, check_names)
    out: List[Event] = []
    for i, x in enumerate(items):
        if isinstance(x, (list, tuple)) and len(x) == 2:
            sensor, kind = x[0], x[1]
        elif isinstance(x, Mapping) and len(x) == 2 and "sensor" in x and "kind" in x:
            sensor, kind = x["sensor"], x["kind"]
        else:
            raise InputError("history item %d (%r) is not a (sensor, kind) pair" % (i + 1, x))
        if not isinstance(sensor, str) or not isinstance(kind, str):
            raise InputError("history item %d (%r) must hold two strings" % (i + 1, x))
        if check_names:
            if kind not in ("A", "D"):
                raise InputError("history item %d (%r): kind must be 'A' or 'D'" % (i + 1, x))
            if map is not None and not (map.is_beam(sensor) or map.is_occupancy(sensor)):
                raise InputError("history item %d: %r is not a sensor of map %r (sensors: %s)"
                                 % (i + 1, sensor, map.name, " ".join(map.sensors)))
        out.append(Event(sensor, kind))
    return out


def _parse_tokens(tokens: Sequence[str], map: Optional["Map"],
                  check_names: bool) -> List[Event]:
    active: Dict[str, bool] = {}
    out: List[Event] = []
    for i, tok in enumerate(tokens):
        explicit = None
        name = tok
        if len(tok) > 1 and tok[-1] in "+-":
            explicit = "A" if tok[-1] == "+" else "D"
            name = tok[:-1]
        if map is not None:
            if map.is_beam(name):
                if explicit == "D":
                    raise InputError("history token %d (%r): beam detectors only activate"
                                     % (i + 1, tok))
                out.append(Event(name, "A"))
                continue
            if map.is_occupancy(name):
                kind = explicit or ("D" if active.get(name) else "A")
                active[name] = kind == "A"
                out.append(Event(name, kind))
                continue
            if check_names:
                raise InputError("history token %d: %r is not a sensor of map %r (sensors: %s)"
                                 % (i + 1, name, map.name, " ".join(map.sensors)))
            out.append(Event(name, explicit or "A"))
            continue
        # no map: only explicit tokens are unambiguous
        if explicit is None:
            if check_names:
                raise InputError("history token %d (%r): a map is needed to tell beams from "
                                 "occupancy sensors (or write %s+ / %s-)"
                                 % (i + 1, tok, tok, tok))
            out.append(Event(name, "A"))
        else:
            out.append(Event(name, explicit))
    return out


def history_to_string(events: Iterable[Event], map: Optional["Map"] = None) -> str:
    """Render a history as tokens: beams by name, occupancy events as ``o1+`` / ``o1-``.

    With ``map``, occupancy events use the bare toggling name when that reads back to the
    same history (i.e. alternation starting from inactive holds for that sensor).
    """
    events = list(events)
    toggling_ok: Dict[str, bool] = {}
    if map is not None:
        state: Dict[str, bool] = {}
        for e in events:
            if map.is_occupancy(e.sensor):
                ok = (e.kind == "A") != state.get(e.sensor, False)
                toggling_ok[e.sensor] = toggling_ok.get(e.sensor, True) and ok
                state[e.sensor] = e.kind == "A"
    toks = []
    for e in events:
        if map is not None and map.is_beam(e.sensor) and e.kind == "A":
            toks.append(e.sensor)
        elif map is not None and toggling_ok.get(e.sensor):
            toks.append(e.sensor)
        else:
            toks.append(e.sensor + ("+" if e.kind == "A" else "-"))
    return " ".join(toks)


def _fmt(i: int, e: Event) -> str:
    return "recording %d (%s %s)" % (i + 1, e.sensor, e.kind)


def _check_kinds(map: "Map", events: Sequence[Event]) -> Optional[str]:
    for i, e in enumerate(events):
        if map.is_beam(e.sensor):
            if e.kind != "A":
                return "%s: a beam detector cannot deactivate" % _fmt(i, e)
        elif not map.is_occupancy(e.sensor):
            return "%s: unknown sensor" % _fmt(i, e)
        elif e.kind not in ("A", "D"):
            return "%s: kind must be A or D" % _fmt(i, e)
    return None


def check_single_agent_history(map: "Map", events: Sequence[Event]) -> Optional[str]:
    """Return ``None`` if one agent alone can produce ``events``, else the reason.

    A single agent (STAR §2.2) is inside an occupancy region for the whole interval between
    its activation and deactivation, so every activation must be immediately followed by the
    deactivation of the same sensor, and every deactivation immediately preceded by it.
    """
    bad = _check_kinds(map, events)
    if bad:
        return bad
    i = 0
    n = len(events)
    while i < n:
        e = events[i]
        if map.is_occupancy(e.sensor):
            if e.kind == "D":
                return ("%s: deactivation without the agent having entered %s just before"
                        % (_fmt(i, e), e.sensor))
            if i + 1 >= n:
                return ("%s: %s is never deactivated, so the agent would still be inside it "
                        "after the last recording" % (_fmt(i, e), e.sensor))
            nxt = events[i + 1]
            if nxt != Event(e.sensor, "D"):
                return ("%s follows %s: a single agent inside %s cannot cause another "
                        "recording before leaving it" % (_fmt(i + 1, nxt), _fmt(i, e), e.sensor))
            i += 2
        else:
            i += 1
    return None


def check_multi_agent_history(map: "Map", events: Sequence[Event]) -> Optional[str]:
    """Return ``None`` if ``events`` is a well-formed multi-agent history, else the reason.

    Every occupancy sensor starts inactive and its activations and deactivations alternate
    (a sensor may still be active after the last recording).
    """
    bad = _check_kinds(map, events)
    if bad:
        return bad
    active: Dict[str, bool] = {}
    for i, e in enumerate(events):
        if map.is_occupancy(e.sensor):
            on = active.get(e.sensor, False)
            if e.kind == "A" and on:
                return "%s: %s is already active" % (_fmt(i, e), e.sensor)
            if e.kind == "D" and not on:
                return "%s: %s is not active" % (_fmt(i, e), e.sensor)
            active[e.sensor] = e.kind == "A"
    return None


def check_history(map: "Map", events: Sequence[Event], agents: str = "single") -> Optional[str]:
    """Dispatch to :func:`check_single_agent_history` / :func:`check_multi_agent_history`."""
    if agents == "single":
        return check_single_agent_history(map, events)
    if agents == "multi":
        return check_multi_agent_history(map, events)
    raise ValueError("agents must be 'single' or 'multi', got %r" % (agents,))
