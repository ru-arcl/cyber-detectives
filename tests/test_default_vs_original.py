"""Default mode vs the original on every golden verdict: each difference has a named cause.

For every golden case that has both an original verdict and a default verdict
(``validateAgentStory`` / ``validateAgentStoryMulti`` / ``getAgentStory`` cases that returned,
and every applet Run that returned a verdict), we compute ``validate(...)`` in default mode.
Wherever the two differ, a *bug detector* must explain the difference.  The detectors and the
bugs they stand for are documented in ``docs/notes/original-bugs.md``.

A detector is a predicate on the input (map, story, history, start), never on the original's
output, paired with a *relaxation* of the default semantics that mimics the bug:

========  ==========================================================  ======================
detector  fires when                                                  relaxation
========  ==========================================================  ======================
B5        the occupancy recordings cannot come from the agent model    the original's reading
          (single: an activation not immediately followed by its       of the history: drop
          deactivation, a lone deactivation; multi: activations and    every deactivation and
          deactivations of a sensor do not alternate)                  pass through ``o`` at
                                                                       each activation
                                                                       (single); drop
                                                                       redundant toggles
                                                                       (multi)
B7b       the history has a beam deactivation                          read it as a crossing
B7c       ``start="fixed"`` (SV joined to the map's first room, as in  verdict ``False``
          ``DetectiveGame``) and the story starts elsewhere
B8        single agent, and the relaxed end convention changes the     "x may use up the story
          verdict                                                      before the last
                                                                       recording" (then no
                                                                       more room entries)
B1        multi agent, and the original's occupancy timing changes     free moves see the
          the verdict                                                  occupancy state of the
                                                                       *next* non-deactivation
                                                                       recording; x inside a
                                                                       deactivating ``o``
                                                                       leaves it at the
                                                                       deactivation
========  ==========================================================  ======================

B8 and B1 are semantic: they fire when switching the relaxation on changes the default verdict
(on top of the structural detectors that fire).  A difference is *explained* when the
relaxations of the firing detectors, applied together, give the original's verdict; it is
attributed to the smallest such subsets.  Beyond explaining differences, the relaxations
together are a complete model of the original's verdicts on the golden data
(``test_bug_model_reproduces_every_original_verdict``).

Paths: wherever both verdicts are true in single-agent mode, the original's ``getAgentStory``
string must describe a legal walk under the default rules (independent checker
``oracles.brute.check_path_string``) unless detector B3 flags it: the path has more bracketed
crossings than the history has recordings, and the history has two consecutive recordings
of the same beam (the trigger of the back-tracking bug).
"""

from __future__ import annotations

import collections
import itertools
import json
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import pytest

from cyber_detectives import InputError, Map, builtin_map, builtin_map_names, validate
from cyber_detectives import engine as E
from cyber_detectives.compat import original as O
from cyber_detectives.history import parse_history, parse_story

from conftest import load_fixture
from oracles import brute as B

GOLDEN_SETS = ["builtin", "star_random_single", "star_random_multi", "random_maps", "applet",
               "paper_cases"]

# Counts per category (golden entries; an entry is one validate/getAgentStory case or one
# applet Run).  Keep in sync with docs/notes/original-bugs.md.
EXPECTED_CATEGORIES = {
    ("single", "B5"): 99,
    ("single", "B5+B7b"): 2,
    ("single", "B5+B7b+B8"): 4,
    ("single", "B5+B8"): 22,
    ("single", "B7b"): 1,
    ("single", "B7b+B8"): 2,
    ("single", "B7c"): 1,
    ("single", "B8"): 36,
    ("multi", "B1"): 4,
    ("multi", "B5"): 60,
    ("multi", "B5+B7b"): 10,
    ("multi", "B7b"): 2,
}
EXPECTED_B3_PATHS = 17          # both verdicts true, original path has an extra crossing
EXPECTED_NO_DEFAULT_VERDICT = 7  # empty story or unknown room: the default raises InputError

Hist = List[Tuple[str, str]]


# ---------------------------------------------------------------------- golden entries


class Entry:
    """One golden verdict: input, the original's verdict and (single) path."""

    __slots__ = ("set", "id", "map", "story", "history", "agents", "start", "original", "path")

    def __init__(self, set_: str, id_: str, map_: Dict[str, Any], story: Sequence[Any],
                 history: Sequence[Sequence[str]], agents: str, start: str, original: bool,
                 path: Optional[str] = None) -> None:
        self.set, self.id, self.map = set_, id_, map_
        self.story = list(story)
        self.history: Hist = [(s, k) for s, k in history]
        self.agents, self.start, self.original, self.path = agents, start, original, path

    def key(self) -> Tuple:
        return (self.map["name"], tuple(self.story), tuple(self.history), self.agents, self.start)

    def __repr__(self) -> str:
        return "%s:%s(%s, %s, %s)" % (self.set, self.id, "".join(map(str, self.story)),
                                      " ".join(s + k for s, k in self.history), self.agents)


_GAMES = {"SingleInfeasible": O.DetectiveGame.get_single_infeasible_game,
          "SingleFeasible": O.DetectiveGame.get_single_feasible_game,
          "MultiFeasible": O.DetectiveGame.get_multi_feasible_game,
          "MultiInfeasible": O.DetectiveGame.get_multi_infeasible_game}

_ENTRIES: List[Entry] = []


def golden_entries() -> List[Entry]:
    if _ENTRIES:
        return _ENTRIES
    star = load_fixture("golden", "builtin.json")["maps"][0]
    assert star["name"] == "star_fig2"
    for s in GOLDEN_SETS:
        data = load_fixture("golden", s + ".json")
        maps = {m["name"]: m for m in data["maps"]}
        for c in data["cases"]:
            op, ex = c["op"], c["expected"]
            if op in ("validateAgentStory", "validateAgentStoryMulti", "getAgentStory"):
                if "game" in c:  # a hard-coded DetectiveGame: read its story and history
                    g = _GAMES[c["game"]]()
                    story = [v.name for v in g.story.get_story_as_array()]
                    hist = O.enc_history(g.ob_his)
                    mp = star
                else:
                    story, hist, mp = c["story"], c["history"], maps[c["map"]]
                start = c.get("start", "fixed" if "game" in c else "story")
                agents = "multi" if op == "validateAgentStoryMulti" else "single"
                if op == "getAgentStory":
                    # a returned path means validateAgentStory is true (it only crashes
                    # otherwise, bug B4)
                    if ex["exception"] is None and isinstance(ex["return"], str):
                        _ENTRIES.append(Entry(s, c["id"], mp, story, hist, agents, start, True,
                                              ex["return"]))
                elif ex.get("consistent") is not None:
                    _ENTRIES.append(Entry(s, c["id"], mp, story, hist, agents, start,
                                          ex["consistent"]))
            elif op == "applet":
                for i, r in enumerate(ex["return"]):
                    if r.get("action") == "run" and r["exception"] is None \
                            and r.get("validate") is not None:
                        # parsed_story/parsed_history: what the game held when Run validated
                        # (including leftovers of an earlier failed Run, bug B6)
                        _ENTRIES.append(Entry(s, "%s#%d" % (c["id"], i), maps[c["map"]],
                                              r["parsed_story"], r["parsed_history"],
                                              r["mode"], "story", r["validate"], r.get("path")))
    return _ENTRIES


_MAPS: Dict[Tuple[str, str], Map] = {}


def map_of(e: Entry) -> Map:
    d = e.map
    key = (d["name"], json.dumps(d, sort_keys=True))
    if key not in _MAPS:
        if d.get("regions") is None and d["name"] in builtin_map_names():
            m = builtin_map(d["name"], geometry=False)  # explicit regions
            assert sorted(map(sorted, m.edges())) == sorted(map(sorted, d["edges"]))
        else:
            m = Map.from_dict(d)
        _MAPS[key] = m
    return _MAPS[key]


def default_verdict(m: Map, story: Sequence[Any], history: Hist, agents: str) -> Optional[bool]:
    """The default verdict, or None when the default rejects the input (InputError)."""
    if not story or any(r is None for r in story):
        return None
    try:
        return validate(m, list(story), [list(x) for x in history], agents=agents).consistent
    except InputError:
        return None


# ---------------------------------------------------------------------- detectors


def b5_occupancy_malformed(m: Map, history: Hist, agents: str) -> bool:
    """B5: the occupancy recordings cannot come from the agent model.  Written independently
    of ``history.check_history``."""
    if agents == "single":
        i = 0
        while i < len(history):
            s, k = history[i]
            if m.is_occupancy(s):
                if k != "A" or i + 1 == len(history) or history[i + 1] != (s, "D"):
                    return True
                i += 2
            else:
                i += 1
        return False
    active: Set[str] = set()
    for s, k in history:
        if m.is_occupancy(s):
            if (k == "A") == (s in active):
                return True
            (active.add if k == "A" else active.discard)(s)
    return False


def b7b_beam_deactivation(m: Map, history: Hist) -> bool:
    """B7b: a beam recording with event D (the original counts it as a crossing)."""
    return any(m.is_beam(s) and k == "D" for s, k in history)


def b7c_fixed_start_elsewhere(m: Map, story: Sequence[str], start: str) -> bool:
    """B7c: SV hard-wired to the map's first room while the story starts elsewhere."""
    return start == "fixed" and bool(story) and story[0] != m.rooms[0]


def b3_extra_crossing(path: str, history: Hist, m: Map) -> bool:
    """B3: the path has more bracketed crossings than there are (non-deactivation) recordings,
    and the history has two consecutive such recordings of the same beam."""
    recs = [s for s, k in history if not (m.is_occupancy(s) and k == "D")]
    brackets = path.count("[")
    same_beam_twice = any(a == b and m.is_beam(a) for a, b in zip(recs, recs[1:]))
    return brackets > len(recs) and same_beam_twice


# ---------------------------------------------------------------------- relaxations


def _original_reading(m: Map, history: Hist, agents: str) -> Hist:
    """How the original reads the occupancy recordings (B5)."""
    out: Hist = []
    if agents == "single":
        for s, k in history:  # deactivations ignored; an activation = pass through o
            if m.is_occupancy(s):
                if k == "A":
                    out += [(s, "A"), (s, "D")]
            else:
                out.append((s, k))
        return out
    active: Set[str] = set()
    for s, k in history:  # the original's O is a set: redundant toggles do nothing
        if m.is_occupancy(s):
            if (k == "A") == (s in active):
                continue
            (active.add if k == "A" else active.discard)(s)
        out.append((s, k))
    return out


def _with_end_room(m: Map, story: Sequence[str]) -> Tuple[Map, List[str]]:
    """B8: a fresh room touching every region, appended to the story.  x must then make the
    remaining recordings without entering a room once the story is used up, which is the
    original's acceptance rule ("l == n in the final phase")."""
    end = "END"
    while end in m.features:
        end += "_"
    regions = {r: list(fs) + [end] for r, fs in m.regions.items()}
    return (Map(m.name + "_end", list(m.rooms) + [end], dict(m.beams), list(m.occupancy),
                regions), list(story) + [end])


class _B1Problem(E._Problem):
    """B1: the original's multi-agent timing (Algorithms.java:385-388).  A deactivation is not
    a phase of its own: x's walk to the next non-deactivation recording happens with the
    occupancy set as it is at that recording (deactivations applied, that recording's own
    activation already added, l.391), and a state inside a deactivating ``o`` survives it
    (x leaves ``o`` at the deactivation).  The occupancy set is the original's ``HashSet``:
    redundant toggles are no-ops."""

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        m, ev = self.m, self.events
        after: List[FrozenSet[str]] = [frozenset()]
        cur: Set[str] = set()
        for e in ev:
            if m.is_occupancy(e.sensor):
                (cur.add if e.kind == "A" else cur.discard)(e.sensor)
            after.append(frozenset(cur))
        active = []
        for h in range(len(ev) + 1):
            j = h
            while j < len(ev) and m.is_occupancy(ev[j].sensor) and ev[j].kind == "D":
                j += 1
            active.append(after[j + 1] if j < len(ev) else after[len(ev)])
        self.active = active

    def event_moves(self, h: int, pos: str, k: int):
        e = self.events[h]
        if self.m.is_occupancy(e.sensor) and e.kind == "D" and pos == e.sensor:
            for r in self.m.regions_of[pos]:
                yield r, E.Step("move", r, h + 1, k, sensor=pos)
            return
        yield from super().event_moves(h, pos, k)


def relaxed_verdict(m: Map, story: Sequence[str], history: Hist, agents: str, start: str,
                    rel: Iterable[str]) -> bool:
    """The default verdict with the relaxations ``rel`` applied."""
    rel = set(rel)
    if "B7c" in rel:
        return False  # the walk starts at SV, whose only neighbour is the map's first room
    if "B7b" in rel:
        history = [(s, "A") if m.is_beam(s) else (s, k) for s, k in history]
    elif b7b_beam_deactivation(m, history):
        return False  # malformed in default mode
    if agents == "multi" and "B1" in rel:
        if "B5" not in rel and b5_occupancy_malformed(m, history, agents):
            return False
        sl = parse_story(list(story), m)
        ev = parse_history([list(x) for x in history], m)
        path, _ = E._search(_B1Problem(m, sl, ev, True, False))
        return path is not None
    if "B5" in rel:
        history = _original_reading(m, history, agents)
    if "B8" in rel:
        m, story = _with_end_room(m, story)
    return validate(m, list(story), [list(x) for x in history], agents=agents).consistent


SEMANTIC = {"single": ("B8",), "multi": ("B1",)}


def firing_detectors(e: Entry, m: Map) -> FrozenSet[str]:
    """The detectors that fire on the entry's input (never looks at the original's output)."""
    base: Set[str] = set()
    if b5_occupancy_malformed(m, e.history, e.agents):
        base.add("B5")
    if b7b_beam_deactivation(m, e.history):
        base.add("B7b")
    if b7c_fixed_start_elsewhere(m, e.story, e.start):
        base.add("B7c")
    fired = set(base)
    for d in SEMANTIC[e.agents]:
        if relaxed_verdict(m, e.story, e.history, e.agents, e.start, base | {d}) != \
                relaxed_verdict(m, e.story, e.history, e.agents, e.start, base):
            fired.add(d)
    return frozenset(fired)


def explanations(e: Entry, m: Map, fired: FrozenSet[str]) -> List[Tuple[str, ...]]:
    """Smallest subsets of the firing detectors whose relaxations give the original verdict."""
    for size in range(1, len(fired) + 1):
        subs = [s for s in itertools.combinations(sorted(fired), size)
                if relaxed_verdict(m, e.story, e.history, e.agents, e.start, s) == e.original]
        if subs:
            return subs
    return []


# ---------------------------------------------------------------------- the tests


_ANALYSIS: Dict[str, Any] = {}


def analysis() -> Dict[str, Any]:
    """Classify every golden entry once (shared by the tests below)."""
    if _ANALYSIS:
        return _ANALYSIS
    cats: collections.Counter = collections.Counter()
    distinct: Dict[Tuple[str, str], Set[Tuple]] = collections.defaultdict(set)
    unexplained: List[str] = []
    model_mismatch: List[str] = []
    no_default: List[Entry] = []
    compared: List[Tuple[Entry, Map, bool]] = []
    for e in golden_entries():
        m = map_of(e)
        dv = default_verdict(m, e.story, e.history, e.agents)
        if dv is None:
            no_default.append(e)
            continue
        compared.append((e, m, dv))
        fired = firing_detectors(e, m)
        if relaxed_verdict(m, e.story, e.history, e.agents, e.start, fired) != e.original:
            model_mismatch.append("%r fired=%s" % (e, sorted(fired)))
        if dv == e.original:
            continue
        subs = explanations(e, m, fired)
        if not subs:
            unexplained.append("%r: original %s, default %s, detectors %s"
                               % (e, e.original, dv, sorted(fired)))
            continue
        label = "|".join("+".join(s) for s in subs)
        cats[(e.agents, label)] += 1
        distinct[(e.agents, label)].add(e.key())
    _ANALYSIS.update(cats=cats, distinct=distinct, unexplained=unexplained,
                     model_mismatch=model_mismatch, no_default=no_default, compared=compared)
    return _ANALYSIS


def test_golden_entries_cover_every_verdict():
    a = analysis()
    n = len(a["compared"])
    by_set = collections.Counter(e.set for e, _, _ in a["compared"])
    assert set(by_set) == set(GOLDEN_SETS)
    assert n > 1600, (n, by_set)
    assert len(a["no_default"]) == EXPECTED_NO_DEFAULT_VERDICT, a["no_default"]
    for e in a["no_default"]:  # only input errors are skipped
        assert not e.story or any(r is None or r not in map_of(e).rooms for r in e.story), e


def test_every_difference_is_explained_by_a_detector():
    a = analysis()
    assert not a["unexplained"], "\n".join(["unexplained differences:"] + a["unexplained"])
    got = dict(a["cats"])
    assert got == EXPECTED_CATEGORIES, (
        "category counts changed; update EXPECTED_CATEGORIES and docs/notes/original-bugs.md:\n"
        + "\n".join("%s %s: %d entries, %d distinct inputs"
                    % (k[0], k[1], v, len(a["distinct"][k])) for k, v in sorted(got.items())))


def test_bug_model_reproduces_every_original_verdict():
    """The firing detectors' relaxations, applied together, give the original's verdict on
    every golden entry, also where it agrees with the default."""
    a = analysis()
    assert not a["model_mismatch"], "\n".join(a["model_mismatch"])


def test_original_paths_replay_unless_b3():
    n_checked = 0
    flagged: List[str] = []
    bad: List[str] = []
    for e, m, dv in analysis()["compared"]:
        if e.agents != "single" or not (e.original and dv) or not e.path:
            continue
        n_checked += 1
        ok, why = B.check_path_string(m.to_dict(), e.story, e.history, e.path, "single")
        if ok:
            assert not b3_extra_crossing(e.path, e.history, m), e
            continue
        if b3_extra_crossing(e.path, e.history, m):
            flagged.append(repr(e))
        else:
            bad.append("%r path %s: %s" % (e, e.path, why))
    assert not bad, "\n".join(bad)
    assert n_checked > 300, n_checked
    assert len(flagged) == EXPECTED_B3_PATHS, flagged


def test_detector_b5_agrees_with_the_engine_on_occupancy():
    """B5's independent predicate equals the engine's own check, restricted to histories
    without beam deactivations (those are B7b)."""
    from cyber_detectives.history import check_history
    for e, m, _ in analysis()["compared"]:
        if b7b_beam_deactivation(m, e.history):
            continue
        ev = parse_history([list(x) for x in e.history], m)
        assert b5_occupancy_malformed(m, e.history, e.agents) == \
            (check_history(m, ev, e.agents) is not None), e


# ---------------------------------------------------------------------- minimal reproductions


STAR = builtin_map("star_fig2", geometry=False)

# (bug, agents, story, history, original verdict, original path, default verdict, category)
# -- the reproductions quoted in docs/notes/original-bugs.md
MINIMAL = [
    ("B1", "multi", "BC", [("o1", "A"), ("b2", "A"), ("o1", "D")], False, None, True, "B1"),
    ("B5", "single", "A", [("o1", "D")], True, "A", False, "B5"),
    ("B5", "single", "A", [("o1", "A"), ("o2", "A")], True, "A[o1][o2]", False, "B5+B8"),
    ("B5", "multi", "A", [("o1", "D")], True, None, False, "B5"),
    ("B7b", "single", "AC", [("b1", "D")], True, "A[b1u]C", False, "B7b"),
    ("B8", "single", "A", [("b1", "A")], True, "A[b1u]", False, "B8"),
    ("B8", "single", "AA", [("b2", "A")], True, "AA[b2l]", False, "B8"),
]


@pytest.mark.parametrize("bug,agents,story,hist,orig,path,dflt,cat", MINIMAL,
                         ids=[r[0] + "-" + r[2] for r in MINIMAL])
def test_minimal_reproduction(bug, agents, story, hist, orig, path, dflt, cat):
    pairs = [list(x) for x in hist]
    r = validate(STAR, story, pairs, agents=agents, compat="original")
    assert r.consistent is orig and r.path_string() == path
    assert validate(STAR, story, pairs, agents=agents).consistent is dflt
    e = Entry("minimal", bug, STAR.to_dict(), list(story), pairs, agents, "story", orig)
    fired = firing_detectors(e, STAR)
    assert bug in fired
    assert "|".join("+".join(s) for s in explanations(e, STAR, fired)) == cat


def test_minimal_reproduction_b3():
    """B3 under the canonical hash order: 3 crossings for 2 recordings (inconsistent input) and
    4 for 3 (consistent input, so the default accepts it with a valid witness)."""
    r = validate(STAR, "A", "b1 b1", compat="original")
    assert r.consistent and r.path_string() == "A[b1d][b1u][b1d]"
    r = validate(STAR, "AC", "b1 b1 b1", compat="original")
    assert r.consistent and r.path_string() == "A[b1d][b1u][b1d][b1u]C"
    d = validate(STAR, "AC", "b1 b1 b1")
    assert d.consistent
    ok, why = B.check_path_string(STAR.to_dict(), "AC", "b1 b1 b1", "A[b1d][b1u][b1d][b1u]C")
    assert not ok and "4 bracketed recordings for 3 recordings" in why
    assert b3_extra_crossing("A[b1d][b1u][b1d][b1u]C", [("b1", "A")] * 3, STAR)
    ok, _ = B.check_path_string(STAR.to_dict(), "AC", "b1 b1 b1", d.path_string())
    assert ok


def test_minimal_reproduction_b4():
    """B4: getAgentStory on an inconsistent input throws instead of returning null."""
    game = O.build_game(STAR.to_dict())  # SV stays joined to A (start "fixed")
    g = game.graph
    game.story.add_vertex(g.vertex_name_map["B"])
    sv = g.vertex_name_map["SV"]
    assert O.validate_agent_story(g, sv, game.story, game.ob_his) is False
    with O.capture_stdout():
        with pytest.raises(O.ArrayIndexOutOfBoundsException) as ei:
            O.get_agent_story(g, sv, game.story, game.ob_his)
    assert ei.value.origin_frame == \
        "projects.cyberDetective.Algorithms.getAgentStoryStatuses(Algorithms.java:365)"
    # B7c on the same input: the default (which starts in p_1) accepts it
    assert validate(STAR, "B", "").consistent
    e = Entry("minimal", "B7c", STAR.to_dict(), ["B"], [], "single", "fixed", False)
    assert explanations(e, STAR, firing_detectors(e, STAR)) == [("B7c",)]


def test_minimal_reproduction_b7a():
    """B7a: edge ids ``v1*65536 + v2`` collide once ids reach 65536 (Edge.java:20-27).  With
    rooms A, B and occupancy sensors o1..o65537, the edges A-o65537 (id 3*65536 + 65541) and
    B-o1 (id 4*65536 + 5) share an id, so B-o1 is dropped and x can no longer pass through o1
    from B."""
    def big_map(n: int) -> Map:
        regions = {"R1": ["A", "o%d" % n], "R2": ["B", "o1"]}
        regions.update({"R%d" % (i + 1): ["o%d" % i] for i in range(2, n)})
        return Map("ids%d" % n, ["A", "B"], {}, ["o%d" % i for i in range(1, n + 1)], regions)

    big, small = big_map(65537), big_map(2)
    assert big.to_dict()["edges"] == [["A", "o65537"], ["B", "o1"]]
    assert validate(big, "BB", "o1 o1").consistent
    assert not validate(big, "BB", "o1 o1", compat="original").consistent
    r = validate(small, "BB", "o1 o1", compat="original")  # control: same map, small ids
    assert r.consistent and r.path_string() == "B[o1]B"
    r = validate(big, "AA", "o65537 o65537", compat="original")  # the surviving edge
    assert r.consistent and r.path_string() == "A[o65537]A"
