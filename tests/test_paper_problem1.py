"""Problem 1 on every paper case (tests/fixtures/paper/{star,icra}.json), default semantics.

- ``expected.consistent``: strict semantics (every room entry reported, x inside ``p_n`` at
  ``t_f``), ``agents`` from the case's ``mode``.
- ``expected.consistent_if_unreported_room_visits_allowed``: ``unreported_visits=True``.
- ``expected.consistent_if_story_need_not_end_in_last_room``: the engine has no switch for
  this reading (see docs/DESIGN.md), so it is not checked.

Every returned witness is replayed, and every fixture witness (a list of places) is checked
for adjacency on the builtin map, which pins the builtin region names to the paper's.
"""

from __future__ import annotations

import pytest

from conftest import paper_cases
from cyber_detectives import builtin_map, replay, validate

CASES = [c for f in ("star", "icra") for c in paper_cases(f)
         if "consistent" in c.get("expected", {})]
LOOSE = [c for c in CASES if "consistent_if_unreported_room_visits_allowed" in c["expected"]]


def _agents(case):
    return {"single": "single", "multi": "multi"}[case["mode"]]


def test_case_counts():
    # 14 STAR + 12 ICRA cases have a Problem-1 verdict; 7 ICRA cases have the loose key
    assert len(CASES) == 26
    assert len(LOOSE) == 7
    ids = {c["id"] for c in CASES}
    for must in ("star_multi_wait_in_unlabelled_component",
                 "star_multi_deactivation_order_infeasible", "star_multi_overlap_C_B",
                 "star_eq1_eq3_multi", "star_eq1_eq2_multi",
                 "star_multi_infeasible_original_history"):
        assert must in ids


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_problem1_strict(case):
    m = builtin_map(case["map"])
    agents = _agents(case)
    r = validate(m, case["story"], case["history"], agents)
    assert r.consistent == case["expected"]["consistent"], r.reason
    if r.consistent:
        replay(m, r.path, case["history"], agents, case["story"])
    else:
        assert r.reason


@pytest.mark.parametrize("case", LOOSE, ids=[c["id"] for c in LOOSE])
def test_problem1_unreported_visits(case):
    m = builtin_map(case["map"])
    agents = _agents(case)
    want = case["expected"]["consistent_if_unreported_room_visits_allowed"]
    r = validate(m, case["story"], case["history"], agents, unreported_visits=True)
    assert r.consistent == want, r.reason
    if r.consistent:
        replay(m, r.path, case["history"], agents, case["story"], unreported_visits=True)


def _check_place_walk(m, walk, history, multi):
    """Consecutive places must be joined: room/occupancy <-> touching region, or region ->
    region across a beam that appears in the history."""
    beams_used = {s for s, _ in history if m.is_beam(s)}
    for a, b in zip(walk, walk[1:]):
        ka, kb = m.kind(a), m.kind(b)
        if ka == "region" and kb == "region":
            assert any(m.side_region(s) == a and m.side_region(m.other_side(s)) == b
                       for bm in beams_used for s in m.beams[bm]), (a, b)
        elif ka == "region":
            assert a in m.regions_of[b], (a, b)
        elif kb == "region":
            assert b in m.regions_of[a], (a, b)
        else:
            raise AssertionError("%s -> %s joins no region" % (a, b))


WITNESS_KEYS = ("witness_regions", "witness_walk_regions",
                "witness_walk_regions_if_unreported_visits_allowed")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fixture_witness_fits_builtin_map(case):
    m = builtin_map(case["map"])
    for key in WITNESS_KEYS:
        walk = case["expected"].get(key)
        if walk:
            _check_place_walk(m, walk, case["history"], case["mode"] == "multi")
            assert walk[0] == case["story"][0] and walk[-1] == case["story"][-1]


def test_wait_in_unlabelled_component_witness():
    """The witness waits in R3 (which touches no room) across o1's deactivation."""
    m = builtin_map("star_fig2")
    hist = [["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"]]
    r = validate(m, "CB", hist, "multi")
    assert r.consistent
    places = [s.position for s in r.path]
    assert places == ["C", "R1", "o1", "R3", "o2", "R4", "B"]
    times = {s.position: s.time for s in r.path}
    assert times["o1"] == 1 and times["o2"] == 3  # o1 passed before t2, o2 after t3
