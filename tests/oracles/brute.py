"""Brute-force oracles for Problems 1-4 and witness checkers, independent of the engine.

Everything here works on the *region model* of ``docs/DESIGN.md`` ("Semantics (default
mode)"), read straight from the fixture JSON form (``tests/fixtures/README.md``); nothing is
imported from ``cyber_detectives``.  The method is deliberately different from the engine's
dynamic program over ``(position, story index)`` states (see ``README.md``):

* **Interleaving enumeration.**  A candidate explanation fixes, for every story element
  ``p_2..p_n``, the gap between recordings ("window") in which it is entered, and in
  multi-agent mode which beam recordings x itself made.  Candidates are generated one choice
  at a time (a backtracking tree, no memo table shared between branches) and each one is
  checked by simulating the *set* of places the agent can be in.  A dead prefix prunes its
  subtree; nothing else is shared.
* **Free moves by simple-walk enumeration.**  Inside one window the places reachable without
  a recording are found by enumerating every simple walk (DFS with a visited set *per walk*)
  over rooms / regions / occupancy regions, not by a graph closure.
* Problems 3 and 4 enumerate candidate stories (super-sequences by increasing length; strings
  by increasing edit distance) and test each with the Problem 1 oracle.  Problem 2 builds the
  ICRA interval timeline explicitly (``docs/notes/paper-examples-icra.md`` section 1) and
  enumerates where the story boundaries fall among the recordings.

Places ("positions"): ``("room", name)``, ``("reg", index)`` for a free region,
``("occ", name)`` for the inside of an occupancy region.
"""

from __future__ import annotations

import itertools
import random
import re
from typing import Any, Dict, FrozenSet, Iterable, Iterator, List, Mapping, NamedTuple, \
    Optional, Sequence, Set, Tuple

ROOM, REG, OCC = "room", "reg", "occ"

Pos = Tuple[str, Any]
Rec = Tuple[str, str]  # (sensor, "A" | "D")

# Hard cap on simple-walk enumeration steps per (start, window kind); the randomized tests
# use small sparse maps far below it.  Hitting it is an oracle error, never a silent answer.
WALK_STEP_LIMIT = 2_000_000


class OracleError(RuntimeError):
    """The oracle cannot answer (input outside its supported size or form)."""


# --------------------------------------------------------------------------- maps


def maximal_cliques(vertices: Iterable[str],
                    edges: Iterable[Sequence[str]]) -> List[FrozenSet[str]]:
    """All maximal cliques of an undirected graph (Bron-Kerbosch with pivoting)."""
    nb: Dict[str, Set[str]] = {v: set() for v in vertices}
    for a, b in edges:
        nb.setdefault(a, set()).add(b)
        nb.setdefault(b, set()).add(a)
    out: List[FrozenSet[str]] = []

    def bk(r: Set[str], p: Set[str], x: Set[str]) -> None:
        if not p and not x:
            out.append(frozenset(r))
            return
        pivot = max(p | x, key=lambda u: len(nb[u] & p))
        for v in sorted(p - nb[pivot]):
            bk(r | {v}, p & nb[v], x & nb[v])
            p = p - {v}
            x = x | {v}

    bk(set(), set(nb), set())
    return out


class RegionMap:
    """A map in the region model, normalised for the oracles.

    ``regions`` is ``{name: [feature, ...]}`` (features = rooms, beam sides, occupancy
    sensors) or a list of feature lists (named ``R1``, ``R2``, ...).
    """

    def __init__(self, rooms: Sequence[str], beams: Mapping[str, Sequence[str]],
                 occupancy: Sequence[str],
                 regions: Any, name: str = "map") -> None:
        self.name = name
        self.rooms: Tuple[str, ...] = tuple(rooms)
        self.beams: Dict[str, Tuple[str, str]] = {b: (s[0], s[1]) for b, s in beams.items()}
        self.occupancy: Tuple[str, ...] = tuple(occupancy)
        if isinstance(regions, Mapping):
            items = list(regions.items())
        else:
            items = [("R%d" % (i + 1), r) for i, r in enumerate(regions)]
        self.region_names: List[str] = [k for k, _ in items]
        self.regions: List[FrozenSet[str]] = [frozenset(v) for _, v in items]

        self.side_beam: Dict[str, str] = {s: b for b, ss in self.beams.items() for s in ss}
        self.other_side: Dict[str, str] = {}
        for b, (s0, s1) in self.beams.items():
            self.other_side[s0] = s1
            self.other_side[s1] = s0
        self.side_region: Dict[str, int] = {}
        for s in self.side_beam:
            where = [i for i, r in enumerate(self.regions) if s in r]
            if len(where) != 1:
                raise ValueError("beam side %r touches %d regions" % (s, len(where)))
            self.side_region[s] = where[0]
        self.touch: Dict[str, FrozenSet[int]] = {
            f: frozenset(i for i, r in enumerate(self.regions) if f in r)
            for f in self.rooms + self.occupancy}
        self.reg_rooms = [sorted(f for f in r if f in self.rooms) for r in self.regions]
        self.reg_occ = [sorted(f for f in r if f in self.occupancy) for r in self.regions]
        self.reg_sides = [sorted(f for f in r if f in self.side_beam) for r in self.regions]
        known = set(self.rooms) | set(self.occupancy) | set(self.side_beam)
        for nm, r in zip(self.region_names, self.regions):
            if not r <= known:
                raise ValueError("region %s touches unknown features %r" % (nm, sorted(r - known)))
        self._reach_cache: Dict[Any, FrozenSet[Pos]] = {}
        self._verdict_cache: Dict[Any, bool] = {}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], regions: Any = None) -> "RegionMap":
        """Build from the fixture dict.  Regions: argument, else ``d["regions"]``, else the
        maximal cliques of ``d["edges"]`` (named ``R1..`` in sorted order)."""
        if regions is None:
            regions = d.get("regions")
        if regions is None:
            feats = list(d["rooms"]) + [s for ss in d["beams"].values() for s in ss] \
                + list(d["occupancy"])
            cl = maximal_cliques(feats, d["edges"])
            regions = sorted((sorted(c) for c in cl))
        return cls(d["rooms"], d["beams"], d["occupancy"], regions, d.get("name", "map"))

    def positions(self) -> List[Pos]:
        return ([(ROOM, r) for r in self.rooms] + [(REG, i) for i in range(len(self.regions))]
                + [(OCC, o) for o in self.occupancy])

    def g_edges(self) -> Set[FrozenSet[str]]:
        """Edges of the connectivity graph G: features touching a common region."""
        out = set()
        for r in self.regions:
            for a, b in itertools.combinations(sorted(r), 2):
                out.add(frozenset((a, b)))
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "rooms": list(self.rooms),
            "beams": {b: list(s) for b, s in self.beams.items()},
            "occupancy": list(self.occupancy),
            "regions": {n: sorted(r) for n, r in zip(self.region_names, self.regions)},
            "edges": sorted(sorted(e) for e in self.g_edges()),
        }

    def pos_name(self, p: Pos) -> str:
        return self.region_names[p[1]] if p[0] == REG else p[1]

    def name_pos(self, name: str) -> Pos:
        if name in self.rooms:
            return (ROOM, name)
        if name in self.occupancy:
            return (OCC, name)
        if name in self.region_names:
            return (REG, self.region_names.index(name))
        raise ValueError("unknown place %r" % name)


def as_region_map(m: Any) -> RegionMap:
    return m if isinstance(m, RegionMap) else RegionMap.from_dict(m)


# --------------------------------------------------------------------------- inputs


def norm_story(rm: RegionMap, story: Any) -> Tuple[str, ...]:
    """A story as a tuple of room names (string of letters, separated string, or list)."""
    if isinstance(story, str):
        toks = [t for t in re.split(r"[\s,]+", story) if t]
        if len(toks) == 1 and toks[0] not in rm.rooms:
            toks = list(toks[0])
        story = toks
    out = tuple(story)
    for r in out:
        if r not in rm.rooms:
            raise ValueError("story names unknown room %r" % (r,))
    return out


def norm_history(rm: RegionMap, history: Any) -> Tuple[Rec, ...]:
    """A history as a tuple of (sensor, "A"|"D").  Accepts [sensor, kind] pairs, objects with
    ``sensor``/``kind`` attributes, or a string (occupancy names toggle; ``o1+``/``o1-``)."""
    out: List[Rec] = []
    if isinstance(history, str):
        on: Set[str] = set()
        for tok in (t for t in re.split(r"[\s,]+", history) if t):
            if tok[-1] in "+-" and tok[:-1] in rm.occupancy:
                out.append((tok[:-1], "A" if tok[-1] == "+" else "D"))
            elif tok in rm.occupancy:
                out.append((tok, "D" if tok in on else "A"))
                on.symmetric_difference_update({tok})
            else:
                out.append((tok, "A"))
    else:
        for e in history:
            if hasattr(e, "sensor"):
                out.append((e.sensor, e.kind))
            else:
                out.append((e[0], e[1]))
    for s, k in out:
        if s not in rm.beams and s not in rm.occupancy:
            raise ValueError("history names unknown sensor %r" % (s,))
        if k not in ("A", "D"):
            raise ValueError("event kind must be A or D, got %r" % (k,))
    return tuple(out)


def malformed(rm: RegionMap, hist: Sequence[Rec]) -> Optional[str]:
    """Why the history is not well formed in either mode, or None.

    Beams only fire ("A"); each occupancy sensor alternates A, D, A, ... starting inactive.
    (The single-agent extras -- nothing else inside an activation interval -- are enforced by
    the simulation itself.)"""
    on: Set[str] = set()
    for i, (s, k) in enumerate(hist):
        if s in rm.beams:
            if k != "A":
                return "event %d: beam %s cannot deactivate" % (i + 1, s)
        elif k == "A":
            if s in on:
                return "event %d: %s activated twice" % (i + 1, s)
            on.add(s)
        else:
            if s not in on:
                return "event %d: %s deactivated while inactive" % (i + 1, s)
            on.discard(s)
    return None


# --------------------------------------------------------------------------- free moves


def _free_moves(rm: RegionMap, p: Pos, story_on: bool, sensors_on: bool,
                active: FrozenSet[str], multi: bool, unreported: bool) -> Iterator[Pos]:
    """Moves that need no recording and no story element, in a window of the given kind."""
    kind, x = p
    if kind == ROOM:  # leaving a room is always silent
        for i in rm.touch[x]:
            yield (REG, i)
    elif kind == OCC:
        # Leaving o: if sensors are off; for a single agent only after o's deactivation;
        # for multi only while o is still active (x must be out before it deactivates).
        if (not sensors_on or (not multi and x not in active)
                or (multi and x in active)):
            for i in rm.touch[x]:
                yield (REG, i)
    else:
        if not story_on or unreported:  # entering a room without reporting it
            for r in rm.reg_rooms[x]:
                yield (ROOM, r)
        for o in rm.reg_occ[x]:  # entering o unseen: sensors off, or opened by others
            if not sensors_on or (multi and o in active):
                yield (OCC, o)
        if not sensors_on:  # crossing a beam unrecorded
            for s in rm.reg_sides[x]:
                yield (REG, rm.side_region[rm.other_side[s]])


def _reach(rm: RegionMap, start: Pos, story_on: bool, sensors_on: bool,
           active: FrozenSet[str], multi: bool, unreported: bool) -> FrozenSet[Pos]:
    """Every place on some simple walk of free moves from ``start`` (start included).

    Enumerates the simple walks themselves (visited set per walk), memoised only per call
    signature -- a pure function of the map, not of the story or history."""
    key = (start, story_on, sensors_on, active if sensors_on else None, multi, unreported)
    hit = rm._reach_cache.get(key)
    if hit is not None:
        return hit
    found: Set[Pos] = set()
    steps = [0]

    def walk(node: Pos, visited: FrozenSet[Pos]) -> None:
        found.add(node)
        steps[0] += 1
        if steps[0] > WALK_STEP_LIMIT:
            raise OracleError("simple-walk enumeration too large on map %s" % rm.name)
        for nb in _free_moves(rm, node, story_on, sensors_on, active, multi, unreported):
            if nb not in visited:
                walk(nb, visited | {nb})

    walk(start, frozenset([start]))
    res = frozenset(found)
    rm._reach_cache[key] = res
    return res


# --------------------------------------------------------------------------- timeline search
#
# A *plan* is the timeline, alternating windows and instants:
#   ("win", story_on, sensors_on)         a gap between instants
#   ("ev", sensor, kind)                   a recording (only inside the sensors' interval)
#   ("mark", "t0" | "tf" | "t0'" | "tf'")  an interval boundary
# It always starts and ends with a window whose flags are both off (the agent is free before
# the first boundary and after the last one).

FREE = ("win", False, False)
STORY_ONLY = ("win", True, False)
SENSORS_ONLY = ("win", False, True)
BOTH = ("win", True, True)


def _segment(events: Sequence[Rec], win: Tuple) -> List[Tuple]:
    out: List[Tuple] = [win]
    for s, k in events:
        out += [("ev", s, k), win]
    return out


def _plan(*parts: Any) -> List[Tuple]:
    """Concatenate marks (strings) and segments (lists) into a plan."""
    out: List[Tuple] = [FREE]
    for part in parts:
        if isinstance(part, str):
            if out[-1][0] != "win":
                raise AssertionError("two instants in a row")
            out.append(("mark", part))
        else:
            if out[-1][0] == "win":
                raise AssertionError("two windows in a row")
            out += part
    out += [FREE] if out[-1][0] != "win" else []
    return out


def _search(rm: RegionMap, story: Tuple[str, ...], plan: List[Tuple], multi: bool,
            unreported: bool) -> bool:
    """Is there an interleaving of story entries (and, multi, of x's beam crossings) with the
    plan's instants that a walk can realise?  Backtracking over choices, no shared memo."""
    n = len(story)
    active_before: List[FrozenSet[str]] = []
    act: Set[str] = set()
    for item in plan:
        active_before.append(frozenset(act))
        if item[0] == "ev" and item[1] in rm.occupancy:
            (act.add if item[2] == "A" else act.discard)(item[1])
    everywhere = frozenset(rm.positions())

    def closure(S: FrozenSet[Pos], idx: int) -> FrozenSet[Pos]:
        _, story_on, sensors_on = plan[idx]
        if S is everywhere:
            return S
        out: Set[Pos] = set()
        for p in S:
            out |= _reach(rm, p, story_on, sensors_on, active_before[idx], multi, unreported)
        return frozenset(out)

    def instant(item: Tuple, C: FrozenSet[Pos], k: int) -> Iterator[Tuple[FrozenSet[Pos], int]]:
        if item[0] == "mark":
            if item[1] == "t0":
                if k == 0 and (ROOM, story[0]) in C:
                    yield frozenset([(ROOM, story[0])]), 1
            elif item[1] == "tf":
                if k == n and (ROOM, story[-1]) in C:
                    yield frozenset([(ROOM, story[-1])]), n
            else:  # t0', tf': not inside an occupancy region
                S2 = frozenset(p for p in C if p[0] != OCC)
                if S2:
                    yield S2, k
            return
        _, sensor, kind = item
        if sensor in rm.beams:
            crossed = frozenset((REG, rm.side_region[rm.other_side[s]])
                                for s in rm.beams[sensor] if (REG, rm.side_region[s]) in C)
            if crossed:
                yield crossed, k  # x crosses at this recording
            if multi:
                yield C, k  # someone else crossed
        elif kind == "A":
            if multi:
                yield C, k  # only connectivity changes
            elif any(p[0] == REG and p[1] in rm.touch[sensor] for p in C):
                yield frozenset([(OCC, sensor)]), k  # x enters o
        else:
            if multi:
                S2 = C - {(OCC, sensor)}  # x must already be out
                if S2:
                    yield S2, k
            elif (OCC, sensor) in C:
                yield frozenset([(OCC, sensor)]), k  # x leaves o in the next window

    def rec(idx: int, k: int, S: FrozenSet[Pos]) -> bool:
        if idx == len(plan) - 1:
            return True  # final free window: S is non-empty, the boundaries were all met
        story_on = plan[idx][1]
        cur, kk = S, k
        while True:  # choose how many further story elements are entered in this window
            C = closure(cur, idx)
            for S2, k2 in instant(plan[idx + 1], C, kk):
                if rec(idx + 2, k2, S2):
                    return True
            if not story_on or kk >= n:
                return False
            r = story[kk]
            if not any(p[0] == REG and r in rm.reg_rooms[p[1]] for p in C):
                return False
            cur, kk = frozenset([(ROOM, r)]), kk + 1

    return rec(0, 0, everywhere)


# --------------------------------------------------------------------------- Problem 1


def consistent(m: Any, story: Any, history: Any, agents: str = "single",
               unreported_visits: bool = False) -> bool:
    """Problem 1: is ``story`` consistent with ``history``?  Malformed histories -> False."""
    rm = as_region_map(m)
    p = norm_story(rm, story)
    s = norm_history(rm, history)
    if not p:
        raise ValueError("empty story")
    if agents not in ("single", "multi"):
        raise ValueError("agents must be 'single' or 'multi'")
    key = ("p1", p, s, agents, unreported_visits)
    if key in rm._verdict_cache:
        return rm._verdict_cache[key]
    if malformed(rm, s) is not None:
        ans = False
    else:
        plan = _plan("t0", _segment(s, BOTH), "tf")
        ans = _search(rm, p, plan, agents == "multi", unreported_visits)
    rm._verdict_cache[key] = ans
    return ans


# --------------------------------------------------------------------------- Problem 2


def interval_plans(case: int, s: Sequence[Rec]) -> Iterator[List[Tuple]]:
    """Every timeline for ICRA interval case 1..6 (primes = the sensors' interval), one per
    placement of the story boundaries among the recordings."""
    m = len(s)
    s = list(s)
    if case == 1:    # t0 < tf < t0' < tf'
        yield _plan("t0", _segment([], STORY_ONLY), "tf", [FREE], "t0'",
                    _segment(s, SENSORS_ONLY), "tf'")
    elif case == 2:  # t0 < t0' < tf < tf'
        for a in range(m + 1):
            yield _plan("t0", _segment([], STORY_ONLY), "t0'", _segment(s[:a], BOTH), "tf",
                        _segment(s[a:], SENSORS_ONLY), "tf'")
    elif case == 3:  # t0' < t0 < tf < tf'
        for a in range(m + 1):
            for b in range(a, m + 1):
                yield _plan("t0'", _segment(s[:a], SENSORS_ONLY), "t0", _segment(s[a:b], BOTH),
                            "tf", _segment(s[b:], SENSORS_ONLY), "tf'")
    elif case == 4:  # t0 < t0' < tf' < tf
        yield _plan("t0", _segment([], STORY_ONLY), "t0'", _segment(s, BOTH), "tf'",
                    _segment([], STORY_ONLY), "tf")
    elif case == 5:  # t0' < t0 < tf' < tf
        for a in range(m + 1):
            yield _plan("t0'", _segment(s[:a], SENSORS_ONLY), "t0", _segment(s[a:], BOTH),
                        "tf'", _segment([], STORY_ONLY), "tf")
    elif case == 6:  # t0' < tf' < t0 < tf
        yield _plan("t0'", _segment(s, SENSORS_ONLY), "tf'", [FREE], "t0",
                    _segment([], STORY_ONLY), "tf")
    else:
        raise ValueError("case must be 1..6")


def consistent_intervals(m: Any, story: Any, history: Any, case: int, agents: str = "single",
                         unreported_visits: bool = False) -> bool:
    """Problem 2 under the interval model of ``docs/notes/paper-examples-icra.md`` section 1:
    the story constrains room entries only in [t0, tf], the sensors record only in
    [t0', tf'], the agent is free outside both, in p1 at t0, in pn at tf with the whole story
    used, all recordings explained by tf', and not inside an occupancy region at t0' / tf'."""
    rm = as_region_map(m)
    p = norm_story(rm, story)
    s = norm_history(rm, history)
    if not p:
        raise ValueError("empty story")
    if malformed(rm, s) is not None:
        return False
    return any(_search(rm, p, plan, agents == "multi", unreported_visits)
               for plan in interval_plans(case, s))


# --------------------------------------------------------------------------- Problems 3, 4


class Bounded(NamedTuple):
    """A bounded brute-force answer.

    ``status`` is "found" (``value`` is the optimum, ``stories`` all optimal stories),
    "none" (provably no answer exists) or "beyond" (an answer exists but its value exceeds
    ``bound``)."""
    status: str
    value: Optional[int]
    stories: List[str]
    bound: int


def is_subsequence(small: Sequence[str], big: Sequence[str]) -> bool:
    it = iter(big)
    return all(c in it for c in small)


def superstory_exists(m: Any, story: Any, history: Any, agents: str = "single",
                      unreported_visits: bool = False, anchored: bool = False) -> bool:
    """Exact existence of a consistent super-sequence (any length).

    A consistent p' >= p is a walk whose visits contain p; reading the extra visits as
    unreported ones, p' exists iff ``X + p + Y`` is consistent with unreported visits for some
    rooms X, Y (each possibly absent): X/Y stand for p'_1/p'_last when they are not matched
    to p_1/p_n.  Anchored (p'_1 = p_1, p'_last = p_n): X = Y = absent."""
    rm = as_region_map(m)
    p = list(norm_story(rm, story))
    ends: List[List[str]] = [[]] + ([] if anchored else [[r] for r in rm.rooms])
    return any(consistent(rm, x + p + y, history, agents, True)
               for x in ends for y in ends)


def _supersequences(story: Sequence[str], alphabet: Sequence[str], extra: int) -> Set[str]:
    """All strings obtained by inserting exactly ``extra`` letters into ``story``."""
    cur = {"".join(story)}
    for _ in range(extra):
        cur = {w[:i] + a + w[i:] for w in cur for i in range(len(w) + 1) for a in alphabet}
    return cur


def shortest_superstories(m: Any, story: Any, history: Any, agents: str = "single",
                          unreported_visits: bool = False, anchored: bool = False,
                          max_extra: int = 3) -> Bounded:
    """Problem 3 by enumeration: super-sequences of p by increasing length (up to
    ``len(p) + max_extra``), each tested with the Problem 1 oracle.  Room names must be
    single letters."""
    rm = as_region_map(m)
    p = norm_story(rm, story)
    _single_letters(rm)
    n = len(p)
    for extra in range(max_extra + 1):
        good = sorted(w for w in _supersequences(p, rm.rooms, extra)
                      if (not anchored or (w[0] == p[0] and w[-1] == p[-1]))
                      and consistent(rm, w, history, agents, unreported_visits))
        if good:
            return Bounded("found", n + extra, good, n + max_extra)
    if not superstory_exists(rm, p, history, agents, unreported_visits, anchored):
        return Bounded("none", None, [], n + max_extra)
    return Bounded("beyond", None, [], n + max_extra)


def levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    """Unit-cost insertion/deletion/substitution distance."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def any_story_exists(m: Any, history: Any, agents: str = "single",
                     unreported_visits: bool = False) -> bool:
    """Is some non-empty story consistent with the history?  (A walk exists iff ``X`` or
    ``X Y`` is consistent with unreported visits for some rooms X, Y.)"""
    rm = as_region_map(m)
    return any(consistent(rm, [x], history, agents, True) for x in rm.rooms) or any(
        consistent(rm, [x, y], history, agents, True) for x in rm.rooms for y in rm.rooms)


def closest_stories(m: Any, story: Any, history: Any, agents: str = "single",
                    unreported_visits: bool = False, max_edits: int = 2) -> Bounded:
    """Problem 4 by enumeration: non-empty strings by increasing number of single edits from
    p (up to ``max_edits``), each tested with the Problem 1 oracle; the distance of every
    optimal string is re-computed with :func:`levenshtein`.  Single-letter room names."""
    rm = as_region_map(m)
    p = "".join(norm_story(rm, story))
    _single_letters(rm)
    seen = {p}
    layer = {p}
    for d in range(max_edits + 1):
        good = sorted(w for w in layer if w and consistent(rm, w, history, agents,
                                                           unreported_visits))
        if good:
            dist = {levenshtein(w, p) for w in good}
            if dist != {d}:
                raise AssertionError("edit layering broken: %r" % dist)
            return Bounded("found", d, good, max_edits)
        nxt: Set[str] = set()
        for w in layer:
            for i in range(len(w) + 1):
                for a in rm.rooms:
                    nxt.add(w[:i] + a + w[i:])
                    if i < len(w):
                        nxt.add(w[:i] + a + w[i + 1:])
                if i < len(w):
                    nxt.add(w[:i] + w[i + 1:])
        layer = nxt - seen
        seen |= layer
    if not any_story_exists(rm, history, agents, unreported_visits):
        return Bounded("none", None, [], max_edits)
    return Bounded("beyond", None, [], max_edits)


def shortest_accepted(m: Any, history: Any, agents: str = "single",
                      unreported_visits: bool = False, max_len: int = 4) -> Bounded:
    """Shortest non-empty consistent stories (the ICRA n'), by enumerating all strings."""
    rm = as_region_map(m)
    _single_letters(rm)
    for n in range(1, max_len + 1):
        good = sorted("".join(w) for w in itertools.product(rm.rooms, repeat=n)
                      if consistent(rm, w, history, agents, unreported_visits))
        if good:
            return Bounded("found", n, good, max_len)
    if not any_story_exists(rm, history, agents, unreported_visits):
        return Bounded("none", None, [], max_len)
    return Bounded("beyond", None, [], max_len)


def _single_letters(rm: RegionMap) -> None:
    if any(len(r) != 1 for r in rm.rooms):
        raise OracleError("string enumeration needs single-letter room names")


# --------------------------------------------------------------------------- witness checkers


def parse_path_string(rm: RegionMap, text: str) -> List[Tuple[str, str]]:
    """Tokens of a ``Result.path_string()``: ("room", R), ("rec", v) for ``[v]``,
    ("pass", o) for ``{o}`` and ("unrep", R) for ``(R)`` (unreported room entry).  Room names
    outside brackets are matched greedily (longest first): the notation has no separators."""
    toks: List[Tuple[str, str]] = []
    rooms = sorted(rm.rooms, key=len, reverse=True)
    i = 0
    while i < len(text):
        c = text[i]
        if c in "[{(":
            close, kind = {"[": ("]", "rec"), "{": ("}", "pass"), "(": (")", "unrep")}[c]
            j = text.find(close, i)
            if j < 0:
                raise ValueError("unclosed %r in path %r" % (c, text))
            toks.append((kind, text[i + 1:j]))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            for r in rooms:
                if text.startswith(r, i):
                    toks.append(("room", r))
                    i += len(r)
                    break
            else:
                raise ValueError("cannot parse path %r at %r" % (text, text[i:]))
    return toks


def check_path_string(m: Any, story: Any, history: Any, path: str, agents: str = "single",
                      unreported_visits: bool = False) -> Tuple[bool, str]:
    """Does ``path`` (DESIGN.md path notation) describe a legal walk that explains every
    recording and matches the story?  Returns (ok, reason).

    Single: one ``[v]`` per beam recording (``v`` = side before crossing) and one ``[o]`` per
    activation (x inside o until its deactivation).  Multi: ``[v]`` only for beam recordings
    x made (which recording of that beam is searched for); every traversal of an occupancy
    region must appear as ``{o}`` (``[o]`` accepted too), entered and left while o is active.
    Rooms outside brackets must be exactly the story; an unreported room entry must appear
    as ``(R)`` and is allowed only with ``unreported_visits`` (DESIGN.md "Path notation").
    Timing (which window each token falls in) is searched for."""
    rm = as_region_map(m)
    p = norm_story(rm, story)
    s = norm_history(rm, history)
    multi = agents == "multi"
    try:
        toks = parse_path_string(rm, path)
    except ValueError as e:
        return False, str(e)
    rooms = tuple(v for t, v in toks if t == "room")
    if rooms != p:
        return False, "rooms %s differ from story %s" % ("".join(rooms), "".join(p))
    if not toks or toks[0] != ("room", p[0]):
        return False, "path must start with the story's first room"
    why = malformed(rm, s)
    if why:
        return False, "history malformed (%s), no path can explain it" % why
    for t, v in toks:
        if t == "rec" and v not in rm.side_beam and v not in rm.occupancy:
            return False, "[%s] is neither a beam side nor an occupancy sensor" % v
        if t == "pass" and v not in rm.occupancy:
            return False, "{%s} is not an occupancy sensor" % v
        if t == "pass" and not multi:
            return False, "{%s} in single-agent mode" % v
        if t == "unrep" and (v not in rm.rooms or not unreported_visits):
            return False, "(%s): unreported room entry not allowed here" % v
    if not multi:
        recs = [e for e in s if not (e[0] in rm.occupancy and e[1] == "D")]
        brs = [v for t, v in toks if t == "rec"]
        if len(brs) != len(recs):
            return False, "%d bracketed recordings for %d recordings" % (len(brs), len(recs))
    m_ = len(s)
    active_before: List[FrozenSet[str]] = []
    act: Set[str] = set()
    for sen, k in s:
        active_before.append(frozenset(act))
        if sen in rm.occupancy:
            (act.add if k == "A" else act.discard)(sen)
    active_before.append(frozenset(act))

    def succ(state: Tuple[int, int, Pos]) -> Iterator[Tuple[int, int, Pos]]:
        ti, h, pos = state
        active = active_before[h]
        tok = toks[ti] if ti < len(toks) else None
        kind, x = pos
        # silent moves inside window h
        if kind == ROOM:
            for i in rm.touch[x]:
                yield ti, h, (REG, i)
        elif kind == OCC:
            if (multi and x in active) or (not multi and x not in active):
                for i in rm.touch[x]:
                    yield ti, h, (REG, i)
        else:
            if tok and tok[0] in ("room", "unrep") and tok[1] in rm.reg_rooms[x]:
                yield ti + 1, h, (ROOM, tok[1])
            if multi and tok and tok[0] in ("pass", "rec") and tok[1] in rm.occupancy \
                    and tok[1] in active and tok[1] in rm.reg_occ[x]:
                yield ti + 1, h, (OCC, tok[1])
        # the recording that ends window h
        if h < m_:
            sen, k = s[h]
            if sen in rm.beams:
                if tok and tok[0] == "rec" and rm.side_beam.get(tok[1]) == sen \
                        and pos == (REG, rm.side_region[tok[1]]):
                    yield ti + 1, h + 1, (REG, rm.side_region[rm.other_side[tok[1]]])
                if multi:
                    yield ti, h + 1, pos
            elif k == "A":
                if multi:
                    yield ti, h + 1, pos
                elif tok == ("rec", sen) and kind == REG and x in rm.touch[sen]:
                    yield ti + 1, h + 1, (OCC, sen)
            else:
                if multi and pos != (OCC, sen):
                    yield ti, h + 1, pos
                if not multi and pos == (OCC, sen):
                    yield ti, h + 1, pos

    start = (1, 0, (ROOM, p[0]))
    goal = (len(toks), m_, (ROOM, p[-1]))
    seen = {start}
    todo = [start]
    best = (1, 0)
    while todo:
        st = todo.pop()
        if st == goal:
            return True, "ok"
        best = max(best, (st[0], st[1]))
        for nx in succ(st):
            if nx not in seen:
                seen.add(nx)
                todo.append(nx)
    return False, ("no legal walk realises the path; got as far as token %d of %d with %d of "
                   "%d recordings explained" % (best[0], len(toks), best[1], m_))


def _field(step: Any, name: str) -> Any:
    return step[name] if isinstance(step, Mapping) else getattr(step, name)


def check_steps(m: Any, story: Any, history: Any, steps: Sequence[Any],
                agents: str = "single", unreported_visits: bool = False) -> Tuple[bool, str]:
    """Replay a ``Result.path`` (list of DESIGN.md ``Step``: kind, position, time,
    story_index, sensor, event; objects or dicts) on the region model.  Returns (ok, reason).

    Checked: adjacency of every step; free steps (start/visit/move/unreported) happen in
    window ``time`` and recording steps (cross/enter/exit) at recording ``event`` with
    ``time == event + 1``; time never decreases; ``story_index`` counts reported visits and
    spells the story; single mode: every recording is explained exactly once, in order;
    multi mode: at most one crossing per beam recording, occupancy entered/left only while
    active, never inside a sensor when it deactivates; the walk ends inside p_n."""
    rm = as_region_map(m)
    p = norm_story(rm, story)
    s = norm_history(rm, history)
    multi = agents == "multi"
    why = malformed(rm, s)
    if why:
        return False, "history malformed (%s)" % why
    act: Set[str] = set()
    active_before: List[FrozenSet[str]] = []
    for sen, k in s:
        active_before.append(frozenset(act))
        if sen in rm.occupancy:
            (act.add if k == "A" else act.discard)(sen)
    active_before.append(frozenset(act))
    m_ = len(s)
    if not steps:
        return False, "empty path"

    def fail(i: int, msg: str) -> Tuple[bool, str]:
        return False, "step %d %r: %s" % (i, steps[i], msg)

    try:
        st0 = steps[0]
        if (_field(st0, "kind"), _field(st0, "position"), _field(st0, "time"),
                _field(st0, "story_index")) != ("start", p[0], 0, 1):
            return fail(0, "must be ('start', %r, time 0, story_index 1)" % p[0])
        cur = (ROOM, p[0])
        t, k = 0, 1  # recordings completed, story elements reported

        def skip_to(i: int, upto: int) -> Optional[str]:
            """Recordings t..upto-1 happen while x stays at ``cur`` without explaining them."""
            for e in range(t, upto):
                sen, kind = s[e]
                if not multi:
                    return "recording %d %r is not explained by any step" % (e, s[e])
                if kind == "D" and cur == (OCC, sen):
                    return "x is inside %s when it deactivates (recording %d)" % (sen, e)
            return None

        for i in range(1, len(steps)):
            st = steps[i]
            kind, name = _field(st, "kind"), _field(st, "position")
            time, sidx = _field(st, "time"), _field(st, "story_index")
            sensor, event = _field(st, "sensor"), _field(st, "event")
            try:
                nxt = rm.name_pos(name)
            except ValueError:
                return fail(i, "unknown position")
            if not isinstance(time, int) or time < t or time > m_:
                return fail(i, "time %r out of order (now %d)" % (time, t))
            if kind in ("visit", "move", "unreported"):
                err = skip_to(i, time)
                if err:
                    return fail(i, err)
                t = time
                active = active_before[t]
                if event is not None:
                    return fail(i, "a free step explains no recording")
                if kind in ("visit", "unreported"):
                    if nxt[0] != ROOM or cur[0] != REG or cur[1] not in rm.touch[nxt[1]]:
                        return fail(i, "room entry from %r" % (rm.pos_name(cur),))
                    if kind == "visit":
                        if k >= len(p) or p[k] != nxt[1]:
                            return fail(i, "visit to %s but next story element is %s"
                                        % (nxt[1], p[k] if k < len(p) else "none"))
                        k += 1
                    elif not unreported_visits:
                        return fail(i, "unreported visit without unreported_visits")
                else:
                    if cur[0] == ROOM and nxt[0] == REG and nxt[1] in rm.touch[cur[1]]:
                        pass
                    elif multi and cur[0] == REG and nxt[0] == OCC \
                            and cur[1] in rm.touch[nxt[1]]:
                        if nxt[1] not in active:
                            return fail(i, "enters %s while inactive" % nxt[1])
                        if sensor is not None and sensor != nxt[1]:
                            return fail(i, "sensor field %r" % (sensor,))
                    elif multi and cur[0] == OCC and nxt[0] == REG \
                            and nxt[1] in rm.touch[cur[1]]:
                        if cur[1] not in active:
                            return fail(i, "leaves %s while inactive" % cur[1])
                        if sensor is not None and sensor != cur[1]:
                            return fail(i, "sensor field %r" % (sensor,))
                    else:
                        return fail(i, "illegal free move %s -> %s"
                                    % (rm.pos_name(cur), name))
            elif kind in ("cross", "enter", "exit"):
                if not isinstance(event, int) or not t <= event < m_ or time != event + 1:
                    return fail(i, "recording index %r / time %r invalid (now %d)"
                                % (event, time, t))
                err = skip_to(i, event)
                if err:
                    return fail(i, err)
                sen, ek = s[event]
                if kind == "cross":
                    if sensor not in rm.side_beam or rm.side_beam[sensor] != sen:
                        return fail(i, "crosses %r at recording %r" % (sensor, s[event]))
                    if cur != (REG, rm.side_region[sensor]) or \
                            nxt != (REG, rm.side_region[rm.other_side[sensor]]):
                        return fail(i, "crossing %s from %s to %s"
                                    % (sensor, rm.pos_name(cur), name))
                elif multi:
                    return fail(i, "%s steps exist only in single-agent mode" % kind)
                elif kind == "enter":
                    if (sen, ek) != (sensor, "A") or nxt != (OCC, sen) or cur[0] != REG \
                            or cur[1] not in rm.touch[sen]:
                        return fail(i, "enter at recording %r from %s" % (s[event],
                                                                          rm.pos_name(cur)))
                else:
                    if (sen, ek) != (sensor, "D") or cur != (OCC, sen) or nxt[0] != REG \
                            or nxt[1] not in rm.touch[sen]:
                        return fail(i, "exit at recording %r to %s" % (s[event], name))
                t = event + 1
            else:
                return fail(i, "unknown kind %r" % (kind,))
            if sidx != k:
                return fail(i, "story_index %r, expected %d" % (sidx, k))
            cur = nxt
        err = skip_to(len(steps), m_)
        if err:
            return False, "after the last step: " + err
    except (KeyError, AttributeError, TypeError) as e:
        return False, "malformed step: %r" % (e,)
    if k != len(p):
        return False, "only %d of %d story elements reported" % (k, len(p))
    if cur != (ROOM, p[-1]):
        return False, "ends in %s, not inside %s" % (rm.pos_name(cur), p[-1])
    return True, "ok"


def check_region_walk(m: Any, story: Any, history: Any, walk: Sequence[str],
                      agents: str = "single", unreported_visits: bool = False
                      ) -> Tuple[bool, str]:
    """Is ``walk`` (place names: rooms, occupancy sensors, region names as in the map's
    ``regions``; a region-to-region step is a beam crossing) a legal walk explaining the
    history and matching the story?  Searches only the timing of each step."""
    rm = as_region_map(m)
    p = norm_story(rm, story)
    s = norm_history(rm, history)
    multi = agents == "multi"
    if malformed(rm, s):
        return False, "history malformed"
    try:
        w = [rm.name_pos(x) for x in walk]
    except ValueError as e:
        return False, str(e)
    if not w or w[0] != (ROOM, p[0]):
        return False, "walk must start in the story's first room"
    act: Set[str] = set()
    active_before: List[FrozenSet[str]] = []
    for sen, k in s:
        active_before.append(frozenset(act))
        if sen in rm.occupancy:
            (act.add if k == "A" else act.discard)(sen)
    active_before.append(frozenset(act))
    n, m_, L = len(p), len(s), len(w) - 1

    def beams_between(a: Pos, b: Pos) -> Set[str]:
        if a[0] != REG or b[0] != REG:
            return set()
        return {rm.side_beam[sd] for sd in rm.reg_sides[a[1]]
                if rm.side_region[rm.other_side[sd]] == b[1]}

    def succ(st: Tuple[int, int, int]) -> Iterator[Tuple[int, int, int]]:
        i, h, k = st
        active = active_before[h]
        here = w[i]
        if i < L:  # take step i -> i+1 silently in window h
            a, b = here, w[i + 1]
            if a[0] == ROOM and b[0] == REG and b[1] in rm.touch[a[1]]:
                yield i + 1, h, k
            if a[0] == REG and b[0] == ROOM and a[1] in rm.touch[b[1]]:
                if k < n and p[k] == b[1]:
                    yield i + 1, h, k + 1
                if unreported_visits:
                    yield i + 1, h, k
            if a[0] == REG and b[0] == OCC and a[1] in rm.touch[b[1]] \
                    and multi and b[1] in active:
                yield i + 1, h, k
            if a[0] == OCC and b[0] == REG and b[1] in rm.touch[a[1]] \
                    and ((multi and a[1] in active) or (not multi and a[1] not in active)):
                yield i + 1, h, k
        if h < m_:
            sen, kind = s[h]
            nxt = w[i + 1] if i < L else None
            # step i -> i+1 caused by recording h
            if nxt is not None and sen in beams_between(here, nxt):
                yield i + 1, h + 1, k
            if (nxt is not None and not multi and kind == "A" and nxt == (OCC, sen)
                    and here[0] == REG and here[1] in rm.touch[sen]):
                yield i + 1, h + 1, k
            # recording h while x stays put
            if multi and not (kind == "D" and here == (OCC, sen)):
                yield i, h + 1, k
            if not multi and kind == "D" and here == (OCC, sen):
                yield i, h + 1, k

    start, goal = (0, 0, 1), (L, m_, n)
    if w[-1] != (ROOM, p[-1]):
        return False, "walk must end in the story's last room"
    seen, todo = {start}, [start]
    while todo:
        st = todo.pop()
        if st == goal:
            return True, "ok"
        for nx in succ(st):
            if nx not in seen:
                seen.add(nx)
                todo.append(nx)
    return False, "no timing of the walk explains story and history"


# --------------------------------------------------------------------------- random instances


def random_region_map(rng: random.Random, rooms: Tuple[int, int] = (2, 5),
                      beams: Tuple[int, int] = (0, 3), occupancy: Tuple[int, int] = (0, 3),
                      regions: Tuple[int, int] = (1, 4), name: str = "rand") -> Dict[str, Any]:
    """A random small region-model map in fixture-dict form (with ``regions`` and ``edges``).

    Every beam's sides lie in two different regions, every occupancy sensor touches 1-3
    regions, every room 1-2; every region touches >= 2 features, and the regions are exactly
    the maximal cliques of the derived G (so a G-only reader recovers the same map).  With
    probability 1/3 an extra sensor-only region joins two occupancy sensors (STAR's R3)."""
    for _ in range(10_000):
        nr, nb, no = rng.randint(*rooms), rng.randint(*beams), rng.randint(*occupancy)
        k = rng.randint(*regions)
        if nb and k < 2:
            k = 2
        regs: List[Set[str]] = [set() for _ in range(k)]
        room_names = [chr(ord("A") + i) for i in range(nr)]
        beam_d = {"b%d" % (i + 1): ["b%du" % (i + 1), "b%dd" % (i + 1)] for i in range(nb)}
        occ = ["o%d" % (i + 1) for i in range(no)]
        for b, (s0, s1) in beam_d.items():
            r0, r1 = rng.sample(range(k), 2)
            regs[r0].add(s0)
            regs[r1].add(s1)
        for o in occ:
            for r in rng.sample(range(k), rng.randint(1, min(3, k))):
                regs[r].add(o)
        for a in room_names:
            for r in rng.sample(range(k), rng.randint(1, min(2, k))):
                regs[r].add(a)
        if no >= 2 and rng.random() < 1 / 3:
            regs.append(set(rng.sample(occ, 2)))
        if any(len(r) < 2 for r in regs):
            continue
        if len({frozenset(r) for r in regs}) != len(regs):
            continue
        rm = RegionMap(room_names, beam_d, occ, [sorted(r) for r in regs], name)
        feats = room_names + [s for ss in beam_d.values() for s in ss] + occ
        cl = maximal_cliques(feats, [tuple(e) for e in rm.g_edges()])
        if set(cl) != set(rm.regions):
            continue
        return rm.to_dict()
    raise OracleError("could not generate a map")


def random_general_region_map(rng: random.Random, rooms: Tuple[int, int] = (1, 4),
                              beams: Tuple[int, int] = (0, 3),
                              occupancy: Tuple[int, int] = (0, 3),
                              regions: Tuple[int, int] = (1, 4),
                              name: str = "general") -> Dict[str, Any]:
    """A random region-model map *without* the clique condition of
    :func:`random_region_map` (fixture-dict form, ``regions`` only, no ``edges``).

    Allowed here and excluded there: regions with a single feature (a dead end behind a beam
    side or an occupancy region), both sides of a beam in the same region, a region whose
    features are a subset of another's (so the regions are not the cliques of ``G``, as in
    ICRA Fig. 2), duplicate regions, and rooms / occupancy sensors touching no region.  Every
    beam side still touches exactly one region (DESIGN.md "Map")."""
    nr, nb, no = rng.randint(*rooms), rng.randint(*beams), rng.randint(*occupancy)
    k = rng.randint(*regions)
    regs: List[Set[str]] = [set() for _ in range(k)]
    room_names = [chr(ord("A") + i) for i in range(nr)]
    beam_d = {"b%d" % (i + 1): ["b%du" % (i + 1), "b%dd" % (i + 1)] for i in range(nb)}
    occ = ["o%d" % (i + 1) for i in range(no)]
    for s0, s1 in beam_d.values():
        regs[rng.randrange(k)].add(s0)
        regs[rng.randrange(k)].add(s1)
    for o in occ:
        for r in rng.sample(range(k), rng.randint(0, min(3, k))):
            regs[r].add(o)
    for a in room_names:
        lo = 0 if rng.random() < 0.1 else 1
        for r in rng.sample(range(k), rng.randint(lo, min(3, k))):
            regs[r].add(a)
    named = {"R%d" % (i + 1): sorted(r) for i, r in enumerate(x for x in regs if x)}
    if not named:  # every feature ended up in no region: give the first room one
        named = {"R1": [room_names[0]]}
    return {"name": name, "rooms": room_names, "beams": beam_d, "occupancy": occ,
            "regions": named}


def simulate_walk(rm: RegionMap, rng: random.Random, agents: str = "single",
                  max_story: int = 5, max_events: int = 6, noise: float = 0.3,
                  max_steps: int = 40) -> Optional[Tuple[List[str], List[List[str]], List[str]]]:
    """Simulate agent x (plus, in multi mode, other agents' recordings) on the region model.

    Returns (story, history, walk as place names) or None if x did not end in a room.  The
    result is consistent by construction (strict visits)."""
    multi = agents == "multi"
    starts = [r for r in rm.rooms if rm.touch[r]]
    if not starts:
        return None
    pos: Pos = (ROOM, rng.choice(starts))
    story, hist, walk = [pos[1]], [], [pos[1]]
    active: Set[str] = set()

    def go(p: Pos) -> None:
        nonlocal pos
        pos = p
        walk.append(rm.pos_name(p))

    for step in range(max_steps):
        if multi and rng.random() < noise and len(hist) < max_events:
            sen = rng.choice(list(rm.beams) + list(rm.occupancy) or [None])
            if sen in rm.beams:
                hist.append([sen, "A"])
            elif sen is not None:
                if sen not in active:
                    hist.append([sen, "A"])
                    active.add(sen)
                elif pos != (OCC, sen):
                    hist.append([sen, "D"])
                    active.discard(sen)
            continue
        kind, x = pos
        opts: List[Tuple[str, Any]] = []
        if kind == ROOM:
            if rng.random() < 0.25 and step > 0:
                break
            opts += [("exit", (REG, i)) for i in rm.touch[x]]
        elif kind == OCC:
            if multi:
                opts += [("exit", (REG, i)) for i in rm.touch[x]]
            else:
                hist.append([x, "D"])
                active.discard(x)
                go((REG, rng.choice(sorted(rm.touch[x]))))
                continue
        else:
            if len(story) < max_story:
                opts += [("room", (ROOM, r)) for r in rm.reg_rooms[x]]
            if len(hist) < max_events:
                opts += [("cross", sd) for sd in rm.reg_sides[x]]
            for o in rm.reg_occ[x]:
                if multi and o in active:
                    opts.append(("pass", (OCC, o)))
                if not multi and len(hist) + 2 <= max_events:
                    opts.append(("occ", (OCC, o)))
        if not opts:
            break
        what, arg = rng.choice(opts)
        if what == "cross":
            hist.append([rm.side_beam[arg], "A"])
            go((REG, rm.side_region[rm.other_side[arg]]))
        elif what == "occ":
            hist.append([arg[1], "A"])
            active.add(arg[1])
            go(arg)
        else:
            if what == "room":
                story.append(arg[1])
            go(arg)
    # finish inside a room (multi: leave an occupancy region first, while it is active)
    for _ in range(3):
        if pos[0] == ROOM:
            break
        if pos[0] == OCC:
            if not multi:
                hist.append([pos[1], "D"])
                active.discard(pos[1])
            go((REG, rng.choice(sorted(rm.touch[pos[1]]))))
        if pos[0] == REG and rm.reg_rooms[pos[1]]:
            r = rng.choice(rm.reg_rooms[pos[1]])
            story.append(r)
            go((ROOM, r))
    if pos[0] != ROOM:
        return None
    return story, hist, walk


def random_story(rng: random.Random, rm: RegionMap, lo: int = 1, hi: int = 4) -> List[str]:
    return [rng.choice(rm.rooms) for _ in range(rng.randint(lo, hi))]


def random_history(rng: random.Random, rm: RegionMap, agents: str = "single", lo: int = 0,
                   hi: int = 6, malformed_prob: float = 0.0) -> List[List[str]]:
    """An arbitrary history.  Occupancy events alternate per sensor (single: as adjacent A/D
    pairs) unless a deliberate malformation is drawn."""
    sensors = list(rm.beams) + list(rm.occupancy)
    out: List[List[str]] = []
    if not sensors:
        return out
    target = rng.randint(lo, hi)
    active: Set[str] = set()
    while len(out) < target:
        sen = rng.choice(sensors)
        if sen in rm.beams:
            out.append([sen, "A"])
        elif agents == "single":
            out += [[sen, "A"], [sen, "D"]]
        else:
            out.append([sen, "D" if sen in active else "A"])
            active.symmetric_difference_update({sen})
    out = out[:max(target, 0)] if agents == "multi" else out
    if rm.occupancy and rng.random() < malformed_prob:
        o = rng.choice(rm.occupancy)
        out.insert(rng.randint(0, len(out)), [o, rng.choice("AD")])
    return out


def perturb(rng: random.Random, rm: RegionMap, story: List[str],
            hist: List[List[str]]) -> Tuple[List[str], List[List[str]]]:
    """One random local change to a (story, history) pair (keeps the story non-empty)."""
    story, hist = list(story), [list(e) for e in hist]
    choice = rng.randrange(5)
    if choice == 0 and len(hist) >= 2:
        i = rng.randrange(len(hist) - 1)
        hist[i], hist[i + 1] = hist[i + 1], hist[i]
    elif choice == 1 and hist:
        hist.pop(rng.randrange(len(hist)))
    elif choice == 2:
        story[rng.randrange(len(story))] = rng.choice(rm.rooms)
    elif choice == 3 and len(story) >= 2:
        story.pop(rng.randrange(len(story)))
    else:
        sensors = list(rm.beams)
        if sensors:
            hist.insert(rng.randint(0, len(hist)), [rng.choice(sensors), "A"])
        else:
            story.insert(rng.randint(0, len(story)), rng.choice(rm.rooms))
    return story, hist
