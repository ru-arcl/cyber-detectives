"""Validate the independent oracles (tests/oracles/brute.py) before trusting them.

1. Every paper-fixture expectation for Problems 1-4 (tests/fixtures/paper/*.json) is
   reproduced by the oracles, and every fixture witness walk is accepted by the region-walk
   checker.
2. The path-string checker accepts the original's valid paths and rejects its known-invalid
   ones (bugs B3, B5, B8 in docs/notes/original-inventory.md).
3. On seeded random maps: every simulated walk's (story, history) is consistent and its walk
   replays; the verdicts satisfy the implications strict => unreported, single => multi,
   Problem 1 => every Problem 2 interval case.

None of this uses the engine.  Scale with CD_TEST_SCALE (default 1).
"""

from __future__ import annotations

import json
import os
import random

import pytest

from oracles import brute as B

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(HERE, "fixtures", "paper")
SCALE = float(os.environ.get("CD_TEST_SCALE", "1") or 1)


def _load(name):
    with open(os.path.join(PAPER, name + ".json")) as f:
        return json.load(f)


STAR = _load("star")
ICRA = _load("icra")


def _paper_maps():
    """The paper maps with the free regions as transcribed in the fixtures."""
    star = {m["name"]: m for m in STAR["maps"]}
    icra = {m["name"]: m for m in ICRA["maps"]}
    fig = ICRA["figures"]
    return {
        "star_fig2": (star["star_fig2"], star["star_fig2"]["expected"]["free_components_fig2"]),
        "star_fig1": (star["star_fig1"], star["star_fig1"]["expected"]["free_components_fig1"]),
        "icra_fig1": (icra["icra_fig1"], fig["icra_fig1_free_components"]["regions"]),
        "icra_fig2": (icra["icra_fig2"], fig["fig3_free_components"]["icra_fig2"]),
    }


MAPS = _paper_maps()
RM = {k: B.RegionMap.from_dict(d, regs) for k, (d, regs) in MAPS.items()}
CASES = [c for c in STAR["cases"] + ICRA["cases"]]
EQ3 = [["b1", "A"], ["o1", "A"], ["o2", "A"], ["b2", "A"], ["o2", "D"], ["o1", "D"]]
FEAS = [["b1", "A"], ["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"], ["b2", "A"]]


# icra_fig2's G has one clique that is not a region: A-B (R1), A-o1 (R3) and B-o1 (R2) form
# the triangle {A, B, o1}, so "one region per maximal clique" turns R1 = {A, B} into
# {A, B, o1}.  The extra contact (o1 with R1) adds no walk, because every pair in the
# triangle already shares a region; test_clique_regions_give_the_same_verdicts checks that.
SPURIOUS_CLIQUES = {"icra_fig2": ({frozenset({"A", "B", "o1"})}, {frozenset({"A", "B"})})}


@pytest.mark.parametrize("name", sorted(MAPS))
def test_transcribed_regions_vs_maximal_cliques_of_G(name):
    d, regs = MAPS[name]
    rm = RM[name]
    assert rm.g_edges() == {frozenset(e) for e in d["edges"]}
    feats = list(rm.rooms) + list(rm.side_beam) + list(rm.occupancy)
    cliques = set(B.maximal_cliques(feats, d["edges"]))
    extra, missing = SPURIOUS_CLIQUES.get(name, (set(), set()))
    assert cliques - set(rm.regions) == extra
    assert set(rm.regions) - cliques == missing


@pytest.mark.parametrize("name", sorted(SPURIOUS_CLIQUES))
def test_clique_regions_give_the_same_verdicts(name):
    d, _ = MAPS[name]
    by_clique = B.RegionMap.from_dict(d)  # regions from the maximal cliques of G
    for c in CASES:
        if c["map"] != name:
            continue
        for unrep in (False, True):
            for mode in ("single", "multi"):
                args = (c["story"], c["history"], mode, unrep)
                assert B.consistent(by_clique, *args) == B.consistent(RM[name], *args), \
                    (c["id"], mode, unrep)


def _ids(cases):
    return [c["id"] for c in cases]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_problem1_verdicts(case):
    rm, e = RM[case["map"]], case["expected"]
    args = (rm, case["story"], case["history"], case["mode"])
    assert B.consistent(*args) is e["consistent"]
    if "consistent_if_unreported_room_visits_allowed" in e:
        assert B.consistent(*args, unreported_visits=True) is \
            e["consistent_if_unreported_room_visits_allowed"]


WITNESS_KEYS = [("witness_regions", False), ("witness_walk_regions", False),
                ("witness_walk_regions_if_unreported_visits_allowed", True)]
WITNESS_CASES = [(c, k, u) for c in CASES for k, u in WITNESS_KEYS if c["expected"].get(k)]


@pytest.mark.parametrize("case,key,unreported", WITNESS_CASES,
                         ids=["%s-%s" % (c["id"], k) for c, k, _ in WITNESS_CASES])
def test_fixture_witness_walks_replay(case, key, unreported):
    rm = RM[case["map"]]
    ok, why = B.check_region_walk(rm, case["story"], case["history"], case["expected"][key],
                                  case["mode"], unreported)
    assert ok, why
    if not unreported and not case["expected"]["consistent"]:
        pytest.fail("fixture gives a strict witness for an inconsistent case")


def test_region_walk_checker_rejects_bad_walks():
    rm = RM["star_fig2"]
    hist = FEAS
    # the fixture witness (C before the b1 crossing) and the note's walk (b1 first) both replay
    fixture = ["A", "R1", "C", "R1", "R2", "o1", "R3", "o2", "R4", "B", "R4", "R2", "A", "R1",
               "C"]
    note = ["A", "R2", "R1", "C", "R1", "o1", "R3", "o2", "R4", "B", "R4", "R2", "A", "R1", "C"]
    assert B.check_region_walk(rm, "ACBAC", hist, fixture)[0]
    assert B.check_region_walk(rm, "ACBAC", hist, note)[0]
    assert not B.check_region_walk(rm, "ACBAC", hist, note[:-2])[0]  # ends outside C
    assert not B.check_region_walk(rm, "ACBC", hist, note)[0]  # unreported visit to A
    assert B.check_region_walk(rm, "ACBC", hist, note, unreported_visits=True)[0]
    assert not B.check_region_walk(rm, "ACBAC", hist[:-1], note)[0]  # b2 crossing unrecorded
    assert not B.check_region_walk(rm, "ACBAC", EQ3, note)[0]  # o2 fires while x is in o1
    assert B.check_region_walk(rm, "ACBAC", EQ3, ["A", "R1", "C", "R1", "o1", "R3", "o2", "R4",
                                                  "B", "R4", "o2", "R3", "o1", "R1", "A", "R1",
                                                  "C"], "multi")[0]


P3_KEYS = [("problem3_shortest_superstory_length", "problem3_shortest_superstories", False),
           ("problem3_anchored_shortest_length", "problem3_anchored_shortest_superstories", True)]
P3_CASES = [(c, a, b, anch) for c in CASES for a, b, anch in P3_KEYS if a in c["expected"]]


@pytest.mark.parametrize("case,key,skey,anchored", P3_CASES,
                         ids=["%s-%s" % (c["id"], "anchored" if an else "free")
                              for c, _, _, an in P3_CASES])
def test_problem3_superstory(case, key, skey, anchored):
    rm, e = RM[case["map"]], case["expected"]
    r = B.shortest_superstories(rm, case["story"], case["history"], case["mode"],
                                anchored=anchored, max_extra=3)
    if e[key] is None:
        assert r.status == "none", r
    else:
        assert (r.status, r.value) == ("found", e[key])
        if skey in e:
            assert r.stories == sorted(e[skey])


P4_CASES = [c for c in CASES if "problem4_min_edits" in c["expected"]]


@pytest.mark.parametrize("case", P4_CASES, ids=_ids(P4_CASES))
def test_problem4_closest(case):
    rm, e = RM[case["map"]], case["expected"]
    r = B.closest_stories(rm, case["story"], case["history"], case["mode"], max_edits=2)
    assert (r.status, r.value) == ("found", e["problem4_min_edits"])
    if "problem4_optimal_stories" in e:
        assert r.stories == sorted(e["problem4_optimal_stories"])
    if "min_length_of_optimal_story" in e:
        assert min(len(w) for w in r.stories) == e["min_length_of_optimal_story"]


NP_CASES = [c for c in CASES if "shortest_accepted_length_n_prime" in c["expected"]]


@pytest.mark.parametrize("case", NP_CASES, ids=_ids(NP_CASES))
def test_shortest_accepted_story(case):
    rm, e = RM[case["map"]], case["expected"]
    r = B.shortest_accepted(rm, case["history"], case["mode"], max_len=4)
    assert (r.status, r.value) == ("found", e["shortest_accepted_length_n_prime"])
    if "shortest_accepted_stories" in e:
        assert r.stories == sorted(e["shortest_accepted_stories"])


P2_CASES = [c for c in CASES if "problem2_by_interval_case" in c["expected"]]


@pytest.mark.parametrize("case", P2_CASES, ids=_ids(P2_CASES))
def test_problem2_interval_cases(case):
    rm, e = RM[case["map"]], case["expected"]
    got = {str(k): B.consistent_intervals(rm, case["story"], case["history"], k, case["mode"])
           for k in range(1, 7)}
    assert got == e["problem2_by_interval_case"]


# ----------------------------------------------------------------------------- path strings

# (map, mode, story, history, path, valid).  Valid paths: the original's getAgentStory output
# on fixture-consistent cases (docs/notes/phase1-crosscheck.md, "Paths").  Invalid: B3 (extra
# crossing), B8 (ends outside p_n), B5 (single-agent history no agent can produce).
PATHS = [
    ("star_fig2", "single", "ACBAC", FEAS, "A[b1u]C[o1][o2]B[b2r]AC", True),
    ("star_fig2", "single", "ABA", [["b2", "A"]] * 2, "A[b2l]B[b2r]A", True),
    ("star_fig2", "single", "AA", [], "AA", True),
    ("star_fig2", "single", "A", [], "A", True),
    ("star_fig2", "single", "CABC", [["b2", "A"], ["b2", "A"], ["o1", "A"], ["o1", "D"]],
     "CA[b2l]B[b2r][o1]C", True),
    ("icra_fig1", "single", "ABAC", [["b2", "A"], ["o2", "A"], ["o2", "D"], ["o1", "A"],
                                     ["o1", "D"]], "A[b2l]B[o2][o1]AC", True),
    ("icra_fig2", "single", "AA", [["b1", "A"]] * 2, "A[b11][b12]A", True),
    ("star_fig2", "single", "AC", [["b1", "A"]] * 3, "A[b1u][b1d][b1u]C", True),
    ("star_fig2", "single", "AC", [["b1", "A"]] * 2, "A[b1d][b1u]C", True),
    ("star_fig2", "single", "AC", [["b1", "A"]] * 2, "A[b1u][b1d][b1u]C", False),  # B3
    ("star_fig2", "single", "AC", [["b1", "A"]] * 3, "A[b1d][b1u][b1d][b1u]C", False),  # B3
    ("star_fig2", "single", "AC", [["b1", "A"]] * 3, "A[b1d][b1u][b1d]C", False),
    ("star_fig2", "single", "A", [["b1", "A"]], "A[b1u]", False),  # B8
    ("star_fig2", "single", "AA", [["b2", "A"]], "AA[b2l]", False),  # B8
    ("star_fig2", "single", "ACBAC", EQ3, "A[b1u]C[o1][o2]B[b2r]AC", False),  # B5
    ("star_fig2", "single", "ACBAC", FEAS, "A[b1u]C[o2][o1]B[b2r]AC", False),
    ("star_fig2", "single", "ACBAC", FEAS, "A[b1u]CA[o1][o2]B[b2r]AC", False),
    # multi: x crosses no beam and passes o1, o2 opened by others (STAR eq. (3) witness)
    ("star_fig2", "multi", "ACBAC", EQ3, "AC{o1}{o2}B{o2}{o1}AC", True),
    ("star_fig2", "multi", "ACBAC", EQ3, "A[b1u]C{o1}{o2}B{o2}{o1}AC", True),
    ("star_fig2", "multi", "ACBAC", EQ3, "ACBAC", False),  # occupancy passes must be shown
    ("star_fig2", "multi", "CB", [["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"]],
     "C{o1}{o2}B", True),  # waits in R3 across o1's deactivation
    ("star_fig2", "multi", "CB", [["o2", "A"], ["o2", "D"], ["o1", "A"], ["o1", "D"]],
     "C{o1}{o2}B", False),
]


@pytest.mark.parametrize("mp,mode,story,hist,path,valid", PATHS,
                         ids=["%s-%s-%s" % (p[1], p[2], p[4]) for p in PATHS])
def test_path_string_checker(mp, mode, story, hist, path, valid):
    ok, why = B.check_path_string(RM[mp], story, hist, path, mode)
    assert ok is valid, why


def test_path_string_checker_unreported_visits():
    rm = RM["icra_fig2"]
    story, hist = "ABDEC", [["b1", "A"], ["b3", "A"], ["o2", "A"], ["o2", "D"], ["b4", "A"]]
    # the fixture's loose witness: the second visit to D is unreported, written "(D)"
    path = "A[b11]B[b31]D[o2]E(D)[b41]C"
    assert B.check_path_string(rm, story, hist, path, unreported_visits=True)[0]
    assert not B.check_path_string(rm, story, hist, path)[0]  # strict: (D) not allowed
    # a silent entry must be written: without "(D)" the walk E -> R7 -> R4 is impossible
    for unrep in (False, True):
        assert not B.check_path_string(rm, story, hist, "A[b11]B[b31]D[o2]E[b41]C",
                                       unreported_visits=unrep)[0]
    assert not B.check_path_string(rm, story, hist, "A[b11]B[b31]D[o2]E(C)[b41]C",
                                   unreported_visits=True)[0]  # C does not touch R7


# ----------------------------------------------------------------------------- Step lists


def _steps(rows):
    keys = ("kind", "position", "time", "story_index", "sensor", "event")
    return [dict(zip(keys, r)) for r in rows]


# STAR feasible single-agent case: A R1 C R1 -b1-> R2 o1 R3 o2 R4 B R4 -b2-> R2 A R1 C
FEAS_STEPS = [
    ("start", "A", 0, 1, None, None), ("move", "R1", 0, 1, None, None),
    ("visit", "C", 0, 2, None, None), ("move", "R1", 0, 2, None, None),
    ("cross", "R2", 1, 2, "b1d", 0), ("enter", "o1", 2, 2, "o1", 1),
    ("exit", "R3", 3, 2, "o1", 2), ("enter", "o2", 4, 2, "o2", 3),
    ("exit", "R4", 5, 2, "o2", 4), ("visit", "B", 5, 3, None, None),
    ("move", "R4", 5, 3, None, None), ("cross", "R2", 6, 3, "b2r", 5),
    ("visit", "A", 6, 4, None, None), ("move", "R1", 6, 4, None, None),
    ("visit", "C", 6, 5, None, None)]
# STAR eq. (3), multi: x only passes o1, o2 while both are active
EQ3_STEPS = [
    ("start", "A", 0, 1, None, None), ("move", "R1", 0, 1, None, None),
    ("visit", "C", 0, 2, None, None), ("move", "R1", 0, 2, None, None),
    ("move", "o1", 3, 2, "o1", None), ("move", "R3", 3, 2, "o1", None),
    ("move", "o2", 3, 2, "o2", None), ("move", "R4", 3, 2, "o2", None),
    ("visit", "B", 3, 3, None, None), ("move", "R4", 3, 3, None, None),
    ("move", "o2", 4, 3, "o2", None), ("move", "R3", 4, 3, "o2", None),
    ("move", "o1", 5, 3, "o1", None), ("move", "R1", 5, 3, "o1", None),
    ("visit", "A", 6, 4, None, None), ("move", "R1", 6, 4, None, None),
    ("visit", "C", 6, 5, None, None)]


def _mut(rows, i, **kw):
    out = _steps(rows)
    out[i].update(kw)
    return out


STEP_CASES = [
    ("single", FEAS, _steps(FEAS_STEPS), True),
    ("single", FEAS, _steps(FEAS_STEPS)[:-2], False),  # ends in A, story not spelled
    ("single", FEAS, _mut(FEAS_STEPS, 4, time=0), False),  # crossing time != event + 1
    ("single", FEAS, _mut(FEAS_STEPS, 4, sensor="b1u"), False),  # wrong side
    ("single", FEAS, _mut(FEAS_STEPS, 2, story_index=1), False),
    ("single", FEAS, _steps(FEAS_STEPS[:5] + FEAS_STEPS[7:]), False),  # o1 never entered
    ("single", FEAS, _mut(FEAS_STEPS, 1, kind="unreported"), False),
    ("multi", FEAS, _steps(FEAS_STEPS), False),  # enter/exit are single-agent steps
    ("multi", EQ3, _steps(EQ3_STEPS), True),
    ("multi", EQ3, _mut(EQ3_STEPS, 4, time=1), False),  # o1 not yet active
    ("multi", EQ3, _mut(EQ3_STEPS, 12, time=6), False),  # inside o1 when it deactivates
    ("single", EQ3, _steps(EQ3_STEPS), False),  # single: recordings unexplained
]


@pytest.mark.parametrize("mode,hist,steps,valid", STEP_CASES,
                         ids=["%d-%s" % (i, c[0]) for i, c in enumerate(STEP_CASES)])
def test_step_checker(mode, hist, steps, valid):
    ok, why = B.check_steps(RM["star_fig2"], "ACBAC", hist, steps, mode)
    assert ok is valid, why


# ----------------------------------------------------------------------------- random


def _n(base):
    return max(1, int(base * SCALE))


@pytest.mark.parametrize("agents", ["single", "multi"])
def test_simulated_walks_are_consistent_and_replay(agents):
    rng = random.Random(20101213 + (agents == "multi"))
    done = 0
    while done < _n(150):
        d = B.random_region_map(rng)
        rm = B.RegionMap.from_dict(d)
        sim = B.simulate_walk(rm, rng, agents)
        if sim is None:
            continue
        story, hist, walk = sim
        done += 1
        rep = json.dumps({"map": d, "story": story, "history": hist, "walk": walk})
        ok, why = B.check_region_walk(rm, story, hist, walk, agents)
        assert ok, why + "\n" + rep
        assert B.consistent(rm, story, hist, agents), rep
        assert B.consistent(rm, story, hist, agents, unreported_visits=True), rep
        if agents == "single":
            assert B.consistent(rm, story, hist, "multi"), rep
        for c in range(1, 7):
            assert B.consistent_intervals(rm, story, hist, c, agents), (c, rep)


def test_verdict_implications_on_arbitrary_inputs():
    rng = random.Random(4980)
    counts = {True: 0, False: 0}
    for i in range(_n(200)):
        d = B.random_region_map(rng)
        rm = B.RegionMap.from_dict(d)
        story = B.random_story(rng, rm)
        hist = B.random_history(rng, rm, "single", hi=5)
        if rng.random() < 0.5:
            sim = B.simulate_walk(rm, rng, "single", max_story=4, max_events=5)
            if sim:
                story, hist = B.perturb(rng, rm, sim[0], sim[1])
        rep = json.dumps({"map": d, "story": story, "history": hist})
        s = B.consistent(rm, story, hist)
        counts[s] += 1
        if s:
            assert B.consistent(rm, story, hist, unreported_visits=True), rep
            assert B.consistent(rm, story, hist, "multi"), rep
            assert all(B.consistent_intervals(rm, story, hist, c) for c in range(1, 7)), rep
        if B.consistent(rm, story, hist, "multi"):
            assert B.consistent(rm, story, hist, "multi", unreported_visits=True), rep
    assert counts[True] and counts[False], counts  # the mix exercises both answers
