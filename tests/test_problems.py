"""ICRA Problems 2-4 (``cyber_detectives.problems``) and the paper-literal procedures.

- Every Problem 2/3/4 expectation of ``tests/fixtures/paper/icra.json`` (STAR has none),
  including the counterexamples to the paper's claims: the max{n, n'} length claim, the
  printed Algorithm 2 (story DAD; Fig. 1 with swapped occupancy order) and the printed
  case-2 procedure.  The paper-literal implementations must reproduce those errors.
- The fixture's lists of all optimal stories are re-derived by enumeration with ``validate``.
- Every Problem 3/4 witness is replayed with ``engine.replay`` for its story; every Problem 2
  witness is checked by an interval-timeline checker written here.
- Randomized: Problem 3/4 against Problem 1 (zero cost iff consistent), the corrected
  Algorithm 2 against the free reading of Problem 3, and all three problems against the
  brute-force oracles of ``tests/oracles/brute.py`` (both Problem 3 readings, multi-agent too).
  Size knob: ``CD_TEST_SCALE``.
"""

from __future__ import annotations

import itertools
import random

import pytest

from conftest import paper_cases, sweep_scale
from cyber_detectives import (InputError, Map, builtin_map, closest_story, replay,
                              shortest_superstory, validate, validate_intervals)
from cyber_detectives import problems as P
from cyber_detectives.cli import main as cli_main
from oracles import brute as B

ICRA = paper_cases("icra")
P2 = [c for c in ICRA if "problem2_by_interval_case" in c["expected"]]
P3_FREE = [c for c in ICRA if "problem3_shortest_superstory_length" in c["expected"]]
P3_ANCH = [c for c in ICRA if "problem3_anchored_shortest_length" in c["expected"]]
P4 = [c for c in ICRA if "problem4_min_edits" in c["expected"]]
ALG2 = [c for c in ICRA if "algorithm2_literal_returns" in c["expected"]]
CASE2 = [c for c in ICRA if "paper_case2_procedure_returns" in c["expected"]]


def _ids(cases):
    return [c["id"] for c in cases]


def _s(story):
    return "".join(story)


def test_fixture_counts():
    assert not [c for c in paper_cases("star")
                if any(k.startswith(("problem2", "problem3", "problem4"))
                       for k in c["expected"])]
    assert (len(P2), len(P3_FREE), len(P3_ANCH), len(P4), len(ALG2), len(CASE2)) == \
        (3, 8, 5, 8, 5, 3)


# ----------------------------------------------------------------------------- helpers


def _supersequences(story, rooms, extra):
    cur = {tuple(story)}
    for _ in range(extra):
        cur = {w[:i] + (a,) + w[i:] for w in cur for i in range(len(w) + 1) for a in rooms}
    return cur


def _within_edits(story, rooms, d):
    """All non-empty strings at Levenshtein distance exactly ``d`` from ``story``."""
    seen = {tuple(story)}
    layer = {tuple(story)}
    for _ in range(d):
        nxt = set()
        for w in layer:
            for i in range(len(w) + 1):
                for a in rooms:
                    nxt.add(w[:i] + (a,) + w[i:])
                    if i < len(w):
                        nxt.add(w[:i] + (a,) + w[i + 1:])
                if i < len(w):
                    nxt.add(w[:i] + w[i + 1:])
        layer = nxt - seen
        seen |= layer
    return {w for w in layer if w}


def _is_subsequence(small, big):
    it = iter(big)
    return all(c in it for c in small)


def _apply_ops(story, ops):
    """Apply EditOps (indices refer to the original story) and return the new story."""
    out = []
    by_index = {}
    for o in ops:
        by_index.setdefault(o.index, []).append(o)
    for i in range(len(story) + 1):
        here = by_index.get(i, [])
        out += [o.new for o in here if o.op == "insert"]
        if i == len(story):
            break
        kept = [o for o in here if o.op != "insert"]
        assert len(kept) <= 1
        if not kept:
            out.append(story[i])
        elif kept[0].op == "substitute":
            assert kept[0].old == story[i]
            out.append(kept[0].new)
        else:
            assert kept[0].op == "delete" and kept[0].old == story[i]
    return out


def _check_superstory(m, story, history, r, agents="single", unreported=False,
                      anchored=True):
    assert r.length == len(r.story)
    assert _is_subsequence(story, r.story)
    rest = [x for i, x in enumerate(r.story) if i not in set(r.inserted)]
    assert rest == list(story)
    assert len(r.inserted) == len(r.story) - len(story)
    if anchored:
        assert r.story[0] == story[0] and r.story[-1] == story[-1]
    assert validate(m, r.story, history, agents, unreported_visits=unreported).consistent
    replay(m, r.path, history, agents, r.story, unreported_visits=unreported)


def _levenshtein(a, b):
    return B.levenshtein(list(a), list(b))


def _check_closest(m, story, history, r, agents="single", unreported=False):
    assert r.story
    assert len(r.operations) == r.edits == _levenshtein(r.story, story)
    assert _apply_ops(list(story), r.operations) == r.story
    assert validate(m, r.story, history, agents, unreported_visits=unreported).consistent
    replay(m, r.path, history, agents, r.story, unreported_visits=unreported)


def check_interval_path(m, story, history, case, path, agents="single", unreported=False):
    """Independent check of a Problem 2 witness (see ``problems.IntervalResult``)."""
    from cyber_detectives import parse_history

    events = parse_history(history, m)
    multi = agents == "multi"
    order = [x for x in P.INTERVAL_CASES[case].split(" < ")]
    assert path and path[0].kind == "begin"
    pos = path[0].position
    passed = []
    h = 0
    told = []
    active = set()

    def flags():
        story_on = "t0" in passed and "tf" not in passed
        sensors_on = "t0'" in passed and "tf'" not in passed
        return story_on, sensors_on

    def kind(x):
        return m.kind(x)

    def advance(to):
        """Recordings h..to-1 happen while x stays at pos (multi: made by others)."""
        nonlocal h
        for e in events[h:to]:
            assert multi, "recording %d not explained by x" % (h + 1)
            if m.is_occupancy(e.sensor):
                (active.add if e.kind == "A" else active.discard)(e.sensor)
                assert not (e.kind == "D" and pos == e.sensor)
        h = max(h, to)

    for st in path[1:]:
        if st.kind in ("cross", "enter", "exit"):
            advance(st.event)
        else:
            advance(st.time)
        story_on, sensors_on = flags()
        if st.kind == "mark":
            assert st.position == pos
            for lab in st.sensor.split(","):
                assert lab == order[len(passed)], (lab, order, passed)
                passed.append(lab)
                if lab == "t0":
                    assert kind(pos) == "room" and pos == story[0]
                    told = [pos]
                elif lab == "tf":
                    assert kind(pos) == "room" and told == list(story)
                    assert pos == story[-1]
                else:
                    assert kind(pos) != "occupancy"
            continue
        if st.kind in ("cross", "enter", "exit"):
            assert sensors_on and st.event == h and st.time == h + 1
            e = events[st.event]
            if st.kind == "cross":
                assert m.is_beam(e.sensor) and st.sensor in m.beams[e.sensor]
                assert m.side_region(st.sensor) == pos
                assert st.position == m.side_region(m.other_side(st.sensor))
            elif st.kind == "enter":
                assert not multi and tuple(e) == (st.sensor, "A")
                assert st.sensor in m.regions[pos] and st.position == st.sensor
            else:
                assert not multi and tuple(e) == (st.sensor, "D") and pos == st.sensor
                assert st.sensor in m.regions[st.position]
            h = st.event + 1
            pos = st.position
            continue
        # free moves
        a, b = kind(pos), kind(st.position)
        if st.kind in ("visit", "unreported"):
            assert a == "region" and b == "room" and st.position in m.regions[pos]
            if st.kind == "visit":
                assert story_on
                told.append(st.position)
            else:
                assert unreported or not story_on
        elif st.kind == "move":
            if a == "room":
                assert b == "region" and pos in m.regions[st.position]
            else:
                assert multi and sensors_on and st.sensor in active
                assert {a, b} == {"region", "occupancy"}
        elif st.kind == "unseen":
            assert not sensors_on and {a, b} == {"region", "occupancy"}
        elif st.kind == "pass":
            assert not sensors_on and m.side_region(st.sensor) == pos
            assert st.position == m.side_region(m.other_side(st.sensor))
        else:
            raise AssertionError("unknown step kind %r" % st.kind)
        pos = st.position
    advance(len(events))
    assert passed == order


# ----------------------------------------------------------------------------- Problem 2


@pytest.mark.parametrize("case", P2, ids=_ids(P2))
def test_problem2_fixture(case):
    m = builtin_map(case["map"])
    want = case["expected"]["problem2_by_interval_case"]
    for k in range(1, 7):
        r = validate_intervals(m, case["story"], case["history"], k)
        assert r.consistent == want[str(k)], (k, r.reason)
        assert r.case == k
        if r.consistent:
            check_interval_path(m, case["story"], case["history"], k, r.path)
            assert r.path_string()
            assert r.to_dict()["interval"] == P.INTERVAL_CASES[k]
        else:
            assert r.reason and r.path is None and r.path_string() is None


@pytest.mark.parametrize("case", CASE2, ids=_ids(CASE2))
def test_case2_procedure_as_printed_reproduces_erratum(case):
    m = builtin_map(case["map"])
    got = P.case2_procedure_as_printed(m, case["story"], case["history"])
    assert got == case["expected"]["paper_case2_procedure_returns"]
    # ... while the interval model accepts all three in case 2 (erratum 2)
    assert validate_intervals(m, case["story"], case["history"], 2).consistent
    assert not got


def test_case2_counterexample_witness():
    """Story AB, history b1 b3: x tells the story by tf and makes a recorded crossing after
    it, so x is not inside p_n = B at the last recording (which the printed procedure
    demands).  The witness is the one with the fewest steps."""
    m = builtin_map("icra_fig2")
    r = validate_intervals(m, "AB", "b1 b3", 2)
    assert r.consistent
    ps = r.path_string()
    assert ps == "|t0|A|t0'|[b11]B|tf|[b31]|tf'|"
    assert "[" in ps[ps.index("|tf|"):]  # a recording comes after the story ends
    assert not validate(m, "AB", "b1 b3").consistent


def test_problem2_reduces_to_problem1_on_paper_example():
    m = builtin_map("icra_fig1")
    assert validate(m, "ABAC", "b2 o2 o2 o1 o1").consistent
    for k in range(1, 7):
        assert validate_intervals(m, "ABAC", "b2 o2 o2 o1 o1", k).consistent


def test_problem2_inputs():
    m = builtin_map("icra_fig2")
    with pytest.raises(ValueError):
        validate_intervals(m, "AB", "b1", 7)
    with pytest.raises(ValueError):
        validate_intervals(m, "AB", "b1", 2, compat="original")
    with pytest.raises(InputError):
        validate_intervals(m, "", "b1", 2)
    with pytest.raises(ValueError):
        validate_intervals(m, "AB", "b1", 2, agents="many")
    r = validate_intervals(m, "AB", [["o1", "D"]], 3)
    assert not r.consistent and r.reason.startswith("malformed history: ")


# ----------------------------------------------------------------------------- Problem 3


@pytest.mark.parametrize("case", P3_FREE, ids=_ids(P3_FREE))
def test_problem3_free_fixture(case):
    m = builtin_map(case["map"])
    e = case["expected"]
    r = shortest_superstory(m, case["story"], case["history"], anchored=False)
    want = e["problem3_shortest_superstory_length"]
    if want is None:
        assert r is None
        return
    assert r is not None and r.length == want and not r.anchored
    _check_superstory(m, case["story"], case["history"], r, anchored=False)
    if "problem3_shortest_superstories" in e:
        assert _s(r.story) in e["problem3_shortest_superstories"]
        every = sorted(_s(w) for w in _supersequences(case["story"], m.rooms,
                                                      want - len(case["story"]))
                       if validate(m, w, case["history"]).consistent)
        assert every == sorted(e["problem3_shortest_superstories"])


@pytest.mark.parametrize("case", P3_ANCH, ids=_ids(P3_ANCH))
def test_problem3_anchored_fixture(case):
    m = builtin_map(case["map"])
    e = case["expected"]
    r = shortest_superstory(m, case["story"], case["history"])  # anchored is the default
    want = e["problem3_anchored_shortest_length"]
    if want is None:
        assert r is None and e["problem3_anchored_shortest_superstories"] == []
        return
    assert r is not None and r.length == want and r.anchored
    _check_superstory(m, case["story"], case["history"], r)
    assert _s(r.story) in e["problem3_anchored_shortest_superstories"]


def test_problem3_readings_differ_on_all_rooms_counterexample():
    """ACB / b2 b1 b2: every consistent story starts with B, so only the free reading has an
    answer (BACB)."""
    m = builtin_map("icra_fig1")
    assert shortest_superstory(m, "ACB", "b2 b1 b2") is None
    r = shortest_superstory(m, "ACB", "b2 b1 b2", anchored=False)
    assert _s(r.story) == "BACB" and r.inserted == [0]


def test_problem3_inputs():
    m = builtin_map("icra_fig2")
    with pytest.raises(InputError):
        shortest_superstory(m, "", "b1")             # anchored needs p_1 and p_n
    with pytest.raises(ValueError):
        shortest_superstory(m, "A", "b1", compat="original")
    assert shortest_superstory(m, "A", [["o1", "D"]]) is None  # malformed
    assert shortest_superstory(m, "", "", anchored=False).length == 1
    r = shortest_superstory(m, "", "b1 b1", anchored=False)  # e.g. A, b1 there and back, A
    assert r.length == 2 and validate(m, r.story, "b1 b1").consistent


# ----------------------------------------------------------------------------- Problem 4


@pytest.mark.parametrize("case", P4, ids=_ids(P4))
def test_problem4_fixture(case):
    m = builtin_map(case["map"])
    e = case["expected"]
    r = closest_story(m, case["story"], case["history"])
    assert r is not None and r.edits == e["problem4_min_edits"]
    _check_closest(m, case["story"], case["history"], r)
    if "problem4_optimal_stories" in e:
        assert _s(r.story) in e["problem4_optimal_stories"]
        every = sorted(_s(w) for w in _within_edits(case["story"], m.rooms, r.edits)
                       if validate(m, w, case["history"]).consistent)
        assert every == sorted(e["problem4_optimal_stories"])
    if "min_length_of_optimal_story" in e:
        assert min(len(w) for w in e["problem4_optimal_stories"]) == \
            e["min_length_of_optimal_story"] == len(r.story)


LENGTH_CLAIM = [c for c in ICRA if "max_n_nprime" in c["expected"]]


@pytest.mark.parametrize("case", LENGTH_CLAIM, ids=_ids(LENGTH_CLAIM))
def test_problem4_length_claim_counterexamples(case):
    """§V-B: "p' ... closest to p cannot have length more than max{n, n'}" is false; the
    bound on the number of edits, max{n, n'}, holds."""
    m = builtin_map(case["map"])
    e = case["expected"]
    n = len(case["story"])
    shortest = shortest_superstory(m, "", case["history"], anchored=False)  # n'
    assert shortest.length == e["shortest_accepted_length_n_prime"]
    if "shortest_accepted_stories" in e:
        assert _s(shortest.story) in e["shortest_accepted_stories"]
        every = sorted(_s(w) for w in itertools.product(m.rooms, repeat=shortest.length)
                       if validate(m, w, case["history"]).consistent)
        assert every == sorted(e["shortest_accepted_stories"])
    assert max(n, shortest.length) == e["max_n_nprime"]
    r = closest_story(m, case["story"], case["history"])
    assert len(r.story) > max(n, shortest.length)          # the claim fails
    assert r.edits <= max(n, shortest.length)              # the edit bound holds


def test_problem4_inputs_and_operations():
    m = builtin_map("icra_fig2")
    with pytest.raises(ValueError):
        closest_story(m, "A", "b1", compat="original")
    assert closest_story(m, "A", [["o1", "A"]]) is None    # malformed
    r = closest_story(m, "", "b1")                          # empty story: all insertions
    assert r.edits == len(r.story) and all(o.op == "insert" for o in r.operations)
    r = closest_story(m, "ABDEC", "b1 b2 o2 o2 b4")
    assert [str(o) for o in r.operations] and r.to_dict()["edits"] == 1


def test_problem4_repeat_and_deletion():
    m = builtin_map("icra_fig2")
    assert closest_story(m, "AAB", "").edits == 0      # leave A and re-enter it
    # E is out of reach without recordings: the closest stories drop or replace it
    r = closest_story(m, "AEB", "")
    assert r.edits == 1 and r.operations[0].index == 1
    every = sorted(_s(w) for w in _within_edits("AEB", m.rooms, 1)
                   if validate(m, w, "").consistent)
    assert _s(r.story) in every and "AB" in every


# ----------------------------------------------------------------------------- paper-literal


@pytest.mark.parametrize("case", ALG2, ids=_ids(ALG2))
def test_algorithm2_fixture(case):
    m = builtin_map(case["map"])
    e = case["expected"]
    assert P.algorithm2_as_printed(m, case["story"], case["history"]) == \
        e["algorithm2_literal_returns"]
    assert P.algorithm2_corrected(m, case["story"], case["history"]) == \
        e["algorithm2_corrected_shortest_length"]


@pytest.mark.parametrize("cid", ["icra_derived_alg2_literal_DAD",
                                 "icra_derived_fig1_swapped_occupancy"])
def test_algorithm2_as_printed_reproduces_erratum(cid):
    case = next(c for c in ICRA if c["id"] == cid)
    m = builtin_map(case["map"])
    assert P.algorithm2_as_printed(m, case["story"], case["history"]) is True
    assert P.algorithm2_corrected(m, case["story"], case["history"]) is None
    assert shortest_superstory(m, case["story"], case["history"], anchored=False) is None


# ----------------------------------------------------------------------------- witnesses


def test_superstory_witness_notation():
    m = builtin_map("icra_fig2")
    r = shortest_superstory(m, "ABDEC", "b1 b3 o2 o2 b4")
    assert _s(r.story) == "ABDEDC" and r.inserted == [4]
    assert r.path_string() == "A[b11]B[b31]D[o2]ED[b41]C"
    d = r.to_dict()
    assert d["story"] == list("ABDEDC") and d["length"] == 6 and d["anchored"]


def test_cli_problem_commands(capsys):
    import io
    out = io.StringIO()
    assert cli_main(["superstory", "--map", "icra_fig2", "--story", "ABDEC", "--history",
                     "b1 b3 o2 o2 b4"], out=out) == 0
    assert "story: A B D E D C" in out.getvalue()
    out = io.StringIO()
    assert cli_main(["closest", "--map", "icra_fig2", "--story", "DAD", "--history",
                     "b1 b3 o2 o2 b4", "--json"], out=out) == 0
    out = io.StringIO()
    assert cli_main(["intervals", "--case", "3", "--map", "icra_fig2", "--story", "ABDEC",
                     "--history", "b1 b3 o2 o2 b4"], out=out) == 1
    out = io.StringIO()
    assert cli_main(["superstory", "--map", "icra_fig2", "--story", "DAD", "--history",
                     "b1 b3 o2 o2 b4"], out=out) == 1
    assert out.getvalue() == "no solution\n"


# ----------------------------------------------------------------------------- randomized

SCALE = sweep_scale()


def _random_instances(rng, count, agents="single", max_story=4, max_events=5, rooms=(2, 4)):
    made = 0
    while made < count:
        d = B.random_region_map(rng, rooms=rooms)
        rm = B.RegionMap.from_dict(d)
        r = rng.random()
        if r < 0.6:
            sim = B.simulate_walk(rm, rng, agents, max_story=max_story, max_events=max_events)
            if sim is None:
                continue
            story, hist = sim[0], sim[1]
            if r >= 0.3:
                story, hist = B.perturb(rng, rm, story, hist)
        else:
            story = B.random_story(rng, rm, 1, max_story)
            hist = B.random_history(rng, rm, agents, 0, max_events)
        made += 1
        yield Map.from_dict(d), rm, list(story), hist


def _free_steps(path):
    """Number of free moves (room entries and exits, multi-agent occupancy passages)."""
    return sum(s.kind in ("visit", "move", "unreported", "pass", "unseen") for s in path)


def test_case1_witness_has_no_excursion():
    """With no recordings and a one-room story, x never needs to leave A."""
    star = builtin_map("star_fig2")
    for k in range(1, 7):
        r = validate_intervals(star, "A", "", k)
        assert [s.kind for s in r.path] == ["begin"] + ["mark"] * 4, (k, r.path_string())
        assert {s.position for s in r.path} == {"A"}
    assert validate_intervals(star, "A", "", 1).path_string() == "|t0|A|tf||t0'||tf'|"


@pytest.mark.parametrize("fn, args, kw, path_string, n_steps", [
    # start + leave A + cross + visit B, between the four boundaries
    (validate_intervals, ("AB", "b2", 2), {}, "|t0|A|t0'|[b2l]B|tf||tf'|", 8),
    (validate_intervals, ("AB", "b2", 2), {"agents": "multi"}, "|t0|A|t0'|[b2l]B|tf||tf'|", 8),
    # start, leave A, four recordings, visit B
    (shortest_superstory, ("AB", "b1 o1 o1 b2"), {}, "A[b1d][o1][b2l]B", 7),
    # four story rooms (each entry after leaving a room: 3 exits), three recordings
    (shortest_superstory, ("ACB", "b2 b1 b2"), {"anchored": False}, "B[b2r]AC[b1d][b2l]B", 10),
    # one substitution: start, leave A, cross, visit
    (closest_story, ("AB", "b1"), {}, "A[b1d]A", 4),
    (closest_story, ("", "b2"), {}, "A[b2l]B", 4),
], ids=["p2", "p2-multi", "p3-anchored", "p3-free", "p4-substitute", "p4-empty-story"])
def test_witnesses_take_fewest_steps(fn, args, kw, path_string, n_steps):
    """Among optimal answers the witness has the fewest steps (counts derived by hand)."""
    star = builtin_map("star_fig2")
    r = fn(star, *args, **kw)
    assert r.path_string() == path_string
    assert len(r.path) == n_steps


@pytest.mark.parametrize("unreported", [False, True], ids=["strict", "unreported"])
@pytest.mark.parametrize("agents", ["single", "multi"])
def test_zero_cost_iff_consistent(agents, unreported):
    rng = random.Random(77 + 2 * (agents == "multi") + unreported)
    ops_seen = set()
    for m, _rm, story, hist in _random_instances(rng, 60 * SCALE, agents, 5, 6, (2, 5)):
        ok = validate(m, story, hist, agents, unreported_visits=unreported).consistent
        kw = dict(agents=agents, unreported_visits=unreported)
        r3 = shortest_superstory(m, story, hist, **kw)
        r3f = shortest_superstory(m, story, hist, anchored=False, **kw)
        r4 = closest_story(m, story, hist, **kw)
        assert ok == (r3 is not None and r3.length == len(story))
        assert ok == (r3f is not None and r3f.length == len(story))
        assert ok == (r4 is not None and r4.edits == 0)
        if r3 is not None:
            _check_superstory(m, story, hist, r3, agents, unreported)
            assert r3f is not None and r3f.length <= r3.length
        if r3f is not None:
            _check_superstory(m, story, hist, r3f, agents, unreported, anchored=False)
        if r4 is not None:
            _check_closest(m, story, hist, r4, agents, unreported)
            ops_seen |= {o.op for o in r4.operations}
            if r3f is not None:
                assert r4.edits <= r3f.length - len(story)
        for r in (r3, r3f, r4):
            if r is not None:  # fewest steps among optimal answers: never more free moves
                r1 = validate(m, r.story, hist, agents, unreported_visits=unreported)
                assert _free_steps(r.path) <= _free_steps(r1.path), (r.path_string(),
                                                                     r1.path_string())
        if ok:  # a consistent story is consistent in every interval case
            for k in range(1, 7):
                assert validate_intervals(m, story, hist, k, **kw).consistent, k
    if agents == "single" and not unreported:  # the other modes rarely need insertions
        assert ops_seen == {"insert", "substitute", "delete"}


def test_algorithm2_corrected_equals_free_problem3():
    rng = random.Random(91)
    printed_wrong = 0
    for m, _rm, story, hist in _random_instances(rng, 80 * SCALE, "single", 4, 5, (2, 5)):
        r = shortest_superstory(m, story, hist, anchored=False)
        want = None if r is None else r.length
        if B.malformed(_rm, B.norm_history(_rm, hist)) is not None:
            continue  # the paper's automaton is not defined for impossible histories
        assert P.algorithm2_corrected(m, story, hist) == want
        printed = P.algorithm2_as_printed(m, story, hist)
        if want is not None:
            assert printed  # the printed form only errs towards "true"
        printed_wrong += printed and want is None
    assert printed_wrong  # the erratum shows up on random inputs too


@pytest.mark.parametrize("agents", ["single", "multi"])
def test_against_brute_force_oracle(agents):
    """Problems 2-4 against the enumeration oracles (both Problem 3 readings)."""
    rng = random.Random(123 + (agents == "multi"))
    seen = {"p2": set(), "p3": set(), "p4": set()}
    for m, rm, story, hist in _random_instances(rng, 25 * SCALE, agents, 3, 4, (2, 4)):
        for k in range(1, 7):
            want = B.consistent_intervals(rm, story, hist, k, agents)
            r = validate_intervals(m, story, hist, k, agents=agents)
            assert r.consistent == want, (k, story, hist, m.to_dict())
            seen["p2"].add(want)
            if r.consistent:
                check_interval_path(m, story, hist, k, r.path, agents)
        for anchored in (True, False):
            want = B.shortest_superstories(rm, story, hist, agents, anchored=anchored,
                                           max_extra=2)
            r = shortest_superstory(m, story, hist, anchored=anchored, agents=agents)
            seen["p3"].add(want.status)
            if want.status == "none":
                assert r is None
            elif want.status == "found":
                assert r is not None and r.length == want.value
                assert _s(r.story) in want.stories
            else:
                assert r is not None and r.length > want.bound
        want = B.closest_stories(rm, story, hist, agents, max_edits=2)
        r = closest_story(m, story, hist, agents=agents)
        seen["p4"].add(want.status)
        if want.status == "none":
            assert r is None
        elif want.status == "found":
            assert r is not None and r.edits == want.value and _s(r.story) in want.stories
        else:
            assert r is not None and r.edits > want.bound
    assert "found" in seen["p3"] and "found" in seen["p4"]
    assert True in seen["p2"]
    if agents == "single":  # multi-agent Problem 2 is rarely false on such small inputs
        assert False in seen["p2"]
