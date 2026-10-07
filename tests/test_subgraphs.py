"""Paper-literal constructions (``cyber_detectives.subgraphs``) against the figure fixtures.

Every figure-level expectation of ``tests/fixtures/paper/star.json`` (Fig. 4, Fig. 5, the DP
narrative of p. 400-401, Fig. 6(a)/(b), Fig. 7) and ``icra.json`` (Fig. 4(a)/(b), Fig. 5,
Fig. 6, ``figures.subgraphs_G_j``) is reproduced, in the variant the fixture records.  The
corrected constructions are then checked against ``engine.validate`` on random inputs: the
paper's algorithm, corrected, decides the same thing as the engine.
"""

from __future__ import annotations

import random

import pytest

from conftest import load_fixture, paper_cases, sweep_scale
from cyber_detectives import Map, builtin_map, validate
from cyber_detectives.history import check_single_agent_history, parse_history
from cyber_detectives.subgraphs import (EPS, Graph, connectivity_graph, get_reachable_subgraph,
                                        get_subgraph, get_subgraph_multi, icra_composite,
                                        icra_nfas, icra_subg, icra_subgraphs,
                                        icra_validate_agent_story, star_composite,
                                        star_multi_composite, star_multi_subgraphs,
                                        star_subgraphs, star_validate, star_validate_multi)

STAR = {c["id"]: c for c in paper_cases("star")}
ICRA = {c["id"]: c for c in paper_cases("icra")}
ICRA_FIG = load_fixture("paper", "icra.json")["figures"]
EQ2 = STAR["star_eq1_eq2_single"]
EQ3 = STAR["star_eq1_eq3_multi"]


def pairs(es):
    return [tuple(e) for e in es]


@pytest.fixture(scope="module")
def fig2():
    return builtin_map("star_fig2")


@pytest.fixture(scope="module")
def icra2():
    return builtin_map("icra_fig2")


# ============================================================================ STAR Fig. 4


def test_algorithm_1_2_on_each_fig4_row(fig2):
    G = connectivity_graph(fig2)
    assert len(G.edges) == 15
    fig4 = EQ2["expected"]["subgraphs_fig4"]
    extra = EQ2["expected"]["algorithm2_literal_extra_vertices"]
    goal_sets = {"G1": ["b1u", "b1d"], "G21": ["o1"], "G22": ["o1"], "G3": ["b2r", "b2l"],
                 "G4": ["o2"], "G5": []}
    for name, row in fig4.items():
        corr = get_subgraph(G, row["start"], fig2.rooms, goal_sets[name], literal=False)
        assert corr.V() == row["V"], name
        assert corr.E() == pairs(row["E"]), name
        lit = get_subgraph(G, row["start"], fig2.rooms, goal_sets[name])
        assert lit.V() == sorted(row["V"] + extra.get(name, [])), name
        assert lit.E() == pairs(row["E"]), name


def test_star_subgraphs_reproduce_fig4(fig2):
    fig4 = EQ2["expected"]["subgraphs_fig4"]
    parts = star_subgraphs(fig2, EQ2["story"], EQ2["history"])
    assert [p.name for p in parts] == list(fig4)
    for p in parts:
        row = fig4[p.name]
        assert p.start == row["start"]
        assert sorted(p.goals) == sorted(row["goals"])
        assert p.V == row["V"]
        assert p.E == pairs(row["E"])
        assert p.interval == row["interval"]
    # no part for (t2,t3) or (t5,t6): the single agent is inside o1 / o2
    assert {p.interval for p in parts}.isdisjoint({"(t2,t3)", "(t5,t6)"})
    assert [p.terminal for p in parts] == [False] * 5 + [True]


def test_literal_subgraphs_keep_unreachable_goals(fig2):
    fig4 = EQ2["expected"]["subgraphs_fig4"]
    extra = EQ2["expected"]["algorithm2_literal_extra_vertices"]
    parts = {p.name: p for p in star_subgraphs(fig2, EQ2["story"], EQ2["history"],
                                               variant="literal")}
    for name in ("G1", "G21", "G22", "G3", "G5"):
        assert parts[name].V == sorted(fig4[name]["V"] + extra.get(name, []))
        assert parts[name].E == pairs(fig4[name]["E"])
    # Alg. 3 line 13 (V_I <- V_G) then also starts a part beyond the unreachable b2r
    assert parts["G41"].start == "b2r" and parts["G41"].E == pairs(fig4["G4"]["E"])
    assert parts["G42"].start == "b2l" and "B" not in parts["G42"].V


# ============================================================================ STAR Fig. 5 + DP


def test_composite_fig5_arcs(fig2):
    f5 = EQ2["expected"]["composite_fig5"]
    gs = star_composite(fig2, EQ2["story"], EQ2["history"])
    for name, arcs in f5["arcs"].items():
        assert gs.part_arcs(name) == sorted(pairs(arcs)), name
    assert f5["self_loops_on_rooms"]
    ours = gs.crossing_arcs()
    drawn = pairs(f5["crossing_arcs"])
    assert set(drawn) <= set(ours)
    # Fig. 5 is partial: it stops at G3's crossing into G4
    assert set(ours) - set(drawn) == {("G4.o2", "G5.o2")}
    # the literal variant draws the same arcs inside G1..G3
    lit = star_composite(fig2, EQ2["story"], EQ2["history"], variant="literal")
    for name, arcs in f5["arcs"].items():
        assert lit.part_arcs(name) == sorted(pairs(arcs)), name


def _walk_exists(gs, path):
    """Is the paper's path string (``"A_G1 b1u b1d C_G21 | BAC"``) a walk in ``G_s``?

    Room tokens name their part; a sensor token may stand for two consecutive nodes with the
    same vertex (a goal and the next part's start, e.g. ``o1``).
    """
    toks = path.split("|")[0].split()

    def match(tok, node):
        if "_" in tok:
            room, part = tok.split("_", 1)
            return node.role == "room" and node.vertex == room and node.part == part
        return node.role != "room" and node.vertex == tok

    first = [u for u in gs.arcs if match(toks[0], u)]
    stack = [(0, u) for u in first]
    seen = set(stack)
    while stack:
        i, u = stack.pop()
        if i == len(toks) - 1:
            return True
        for w in gs.arcs.get(u, ()):
            nxt = []
            if "_" not in toks[i] and match(toks[i], w):
                nxt.append((i, w))
            if match(toks[i + 1], w):
                nxt.append((i + 1, w))
            for st in nxt:
                if st not in seen:
                    seen.add(st)
                    stack.append(st)
    return False


def test_dp_narrative_p400(fig2):
    exp = EQ2["expected"]
    r = star_validate(fig2, EQ2["story"], EQ2["history"])
    assert r.consistent is exp["consistent"] is False
    assert r.subproblems == {int(k): v for k, v in exp["subproblems_after_story_index"].items()}
    assert ("G22" in r.subproblems[2]) is exp["copy_of_C_in_G22_reachable"]
    gs = r.composite
    paths = exp["paper_subproblem_paths"]
    for key in ("after_p2", "after_p3"):
        for p in paths[key]:
            assert _walk_exists(gs, p), p
    assert paths["after_p4"] == [] and r.subproblems[4] == []
    # the printed 'b21' (digit one) is not a vertex: the walk only exists with b2l
    assert not _walk_exists(gs, paths["after_p3"][0].replace("b2l", "b21"))
    # our own witness paths are walks too, one per surviving subproblem
    for i in (2, 3):
        assert len(r.paths[i]) == len({p.split()[-1] for p in r.paths[i]})
        for p in r.paths[i]:
            assert _walk_exists(gs, p), p
    assert r.paths[2] == ["A_G1 C_G1", "A_G1 b1u b1d C_G21", "A_G1 b1d b1u o1 C_G3"]


SINGLE = [c for c in STAR.values() if c["mode"] == "single"]


@pytest.mark.parametrize("case", SINGLE, ids=[c["id"] for c in SINGLE])
def test_star_algorithm3_variants(fig2, case):
    exp = case["expected"]
    st, h = case["story"], case["history"]
    assert star_validate(fig2, st, h).consistent is exp["consistent"]
    pa = exp.get("paper_algorithm")
    if pa is not None:
        assert star_validate(fig2, st, h, check_history=False).consistent \
            is pa["with_fixes_no_history_check"]
        assert star_validate(fig2, st, h, check_history=False, split_roles=False).consistent \
            is pa["without_start_goal_role_split"]
        assert star_validate(fig2, st, h, check_history=False, self_loops=False).consistent \
            is pa["without_room_self_loops"]
        wf = check_single_agent_history(fig2, parse_history(h, fig2)) is None
        assert wf is pa["history_well_formed_for_single"]
    if "paper_algorithm_as_printed" in exp:
        assert star_validate(fig2, st, h, variant="literal").consistent \
            is exp["paper_algorithm_as_printed"]
    if "paper_algorithm_without_history_check_accepts" in exp:
        assert star_validate(fig2, st, h, check_history=False).consistent \
            is exp["paper_algorithm_without_history_check_accepts"]
        assert star_validate(fig2, st, h, variant="literal").consistent is True


@pytest.mark.parametrize("story,history", [
    ("CA", "o1 o1 o1 o1"), ("AC", "b1 b1"), ("CAAAAC", "b2 b2")])
def test_role_split_needs_the_stay_arc(fig2, story, history):
    # docs/notes/paper-examples-star.md A3-v: separate copies without the direct
    # start->goal arc reject these consistent cases
    assert validate(fig2, story, history).consistent
    assert star_validate(fig2, story, history).consistent
    assert not star_validate(fig2, story, history, stay_arcs=False).consistent


# ============================================================================ STAR Fig. 6, 7


def test_fig6a_and_fig6b(fig2):
    G = connectivity_graph(fig2)
    a = EQ3["expected"]["fig6a_G0_4_before_cliquification"]
    O = ["o1", "o2"]
    g = get_reachable_subgraph(G, a["start"], set(fig2.rooms) | set(O) | {a["start"]},
                               a["goals"])
    assert g.V() == a["V"] and g.E() == pairs(a["E"])
    b = EQ3["expected"]["fig6b_G0_4_after_cliquification"]
    lit = get_subgraph_multi(G, "A", fig2.rooms, O, a["goals"])
    assert lit.V() == b["V"] and lit.E() == pairs(b["E_algorithm4_literal"])
    drawn = get_subgraph_multi(G, "A", fig2.rooms, O, a["goals"], literal=False)
    assert drawn.V() == b["V"] and drawn.E() == pairs(b["E_drawn_in_paper"])
    # the clique-ification does not depend on the order of O (A4-ii)
    assert get_subgraph_multi(G, "A", fig2.rooms, O[::-1], a["goals"]) == lit


def test_composite_fig7(fig2):
    f7 = EQ3["expected"]["composite_fig7"]
    parts = star_multi_subgraphs(fig2, EQ3["story"], EQ3["history"])
    assert len(parts) == f7["num_subgraphs"] == 9
    assert {p.name for p in parts} == set(f7["subgraphs"])
    for p in parts:
        row = f7["subgraphs"][p.name]
        assert p.start == row["start"], p.name
        assert sorted(p.goals) == sorted(row["goals"]), p.name
        assert sorted(p.active) == sorted(row["active_occupancy"]), p.name
        assert p.interval == row["covers"], p.name
        assert p.V == row["V_algorithm4"], p.name
        assert p.E == pairs(row["E_algorithm4"]), p.name
    assert sorted(p.name for p in parts if p.entry) == sorted(f7["entry_subgraphs_from_A"])
    gs = star_multi_composite(fig2, EQ3["story"], EQ3["history"])
    assert sorted(gs.crossing_arcs()) == sorted(pairs(f7["crossing_arcs"]))
    g01 = gs.by_name["G0_1"]
    g1 = EQ2["expected"]["subgraphs_fig4"]["G1"]
    assert EQ3["expected"]["G0_1_equals_fig4_G1"]
    assert g01.V == g1["V"] and g01.E == pairs(g1["E"])


def test_dp_over_fig7(fig2):
    r = star_validate_multi(fig2, EQ3["story"], EQ3["history"])
    assert r.consistent is EQ3["expected"]["dp_over_fig7_consistent"] is True
    # "the whole story fits inside G0_5", the triangle A-B-C
    assert all("G0_5" in r.subproblems[i] for i in range(1, 6))
    assert r.composite.by_name["G0_5"].E == [("A", "B"), ("A", "C"), ("B", "C")]


MULTI = [c for c in STAR.values() if c["mode"] == "multi"]


@pytest.mark.parametrize("case", MULTI, ids=[c["id"] for c in MULTI])
def test_multi_family_verdicts(fig2, case):
    st, h, exp = case["story"], case["history"], case["expected"]["consistent"]
    assert star_validate_multi(fig2, st, h, variant="corrected").consistent is exp
    lit = star_validate_multi(fig2, st, h).consistent
    if case["id"] == "star_multi_wait_in_unlabelled_component":
        # G cannot express waiting in R3 (touches only o1, o2) across o1's deactivation
        assert lit is False and exp is True
    else:
        assert lit is exp


# ============================================================================ ICRA


def test_icra_fig4(icra2):
    G = connectivity_graph(icra2)
    assert len(G.edges) == 38
    for key, starts, goals in (("fig4a", ["A"], ["b11", "b12"]),
                               ("fig4b", ["b11", "b12"], ["b31", "b32"])):
        g = icra_subg(G, starts, goals, icra2.rooms)
        assert g.V() == ICRA_FIG[key]["vertices"], key
        assert g.E() == pairs(ICRA_FIG[key]["edges"]), key
    # starting G_1 from every room instead of A gives the same Fig. 4(a)
    assert icra_subg(G, icra2.rooms, ["b11", "b12"], icra2.rooms).E() == \
        pairs(ICRA_FIG["fig4a"]["edges"])


@pytest.mark.parametrize("history", sorted(ICRA_FIG["subgraphs_G_j"]))
def test_icra_subgraphs_G_j(icra2, history):
    exp = ICRA_FIG["subgraphs_G_j"][history]
    segs = icra_subgraphs(icra2, history, p1="A")
    assert [s.j for s in segs] == [e["j"] for e in exp]
    for s, e in zip(segs, exp):
        assert s.graph.V() == e["vertices"], s.j
        assert s.graph.E() == pairs(e["edges"]), s.j
    assert segs[3].inside == "o2"
    # literal: SUBG(G, o2, o2) lets x visit D and E while inside o2
    lit = icra_subgraphs(icra2, history, p1="A", variant="literal")
    for s, t in zip(segs, lit):
        if s.j != 4:
            assert s.graph == t.graph
    assert lit[3].graph.V() == ["D", "E", "o2"]


def _trans(ts):
    return {tuple(t) for t in ts}


def test_icra_fig5(icra2):
    nfas = icra_nfas(icra2, "b1 b3 o2 o2 b4", p1="A")
    a, b = nfas[0], nfas[1]
    fa = ICRA_FIG["fig5a_as_printed"]
    assert ICRA_FIG["fig5a_as_printed"]["matches_construction"]
    assert _trans(a.transitions) == _trans(fa["transitions"])
    assert a.starts == [fa["start"]] and sorted(a.accepting) == sorted(fa["accepting"])
    fb = ICRA_FIG["fig5b_as_printed"]
    assert not fb["matches_construction"]
    assert b.starts == fb["start"] and b.accepting == fb["accepting"]
    printed = _trans(fb["transitions"])
    assert _trans(b.transitions) != printed
    for src, wrong, label in fb["corrections"]["relabel"]:
        old = (src, EPS, wrong.split("->")[1])
        assert old in printed
        printed = (printed - {old}) | {(src, label, label)}
    printed |= _trans(fb["corrections"]["add"])
    assert _trans(b.transitions) == printed
    # M_1 of Fig. 5(a) also comes out when G_1 starts from every room (p1=None)
    assert _trans(icra_nfas(icra2, "b1 b3 o2 o2 b4")[0].transitions) == _trans(fa["transitions"])
    # the same for both variants: the example has no repeated sensor
    lit = icra_nfas(icra2, "b1 b3 o2 o2 b4", p1="A", variant="literal")
    assert _trans(lit[0].transitions) == _trans(a.transitions)
    assert _trans(lit[1].transitions) == _trans(b.transitions)


def test_icra_fig6_links(icra2):
    M = icra_composite(icra2, "b1 b3 o2 o2 b4", p1="A")
    links = M.link_names()
    for src, lab, dst in ICRA_FIG["fig6"]["links"]:
        assert lab == EPS and (src, dst) in links
    assert links[:3] == [(s, d) for s, _, d in ICRA_FIG["fig6"]["links"]]


CASES_ICRA = [c for c in ICRA.values() if "consistent" in c["expected"]]


@pytest.mark.parametrize("case", CASES_ICRA, ids=[c["id"] for c in CASES_ICRA])
def test_icra_algorithm1(case):
    m = builtin_map(case["map"])
    exp = case["expected"]
    assert icra_validate_agent_story(m, case["story"], case["history"]) is exp["consistent"]
    if "algorithm1_literal_returns" in exp:
        assert icra_validate_agent_story(m, case["story"], case["history"],
                                         variant="literal") is exp["algorithm1_literal_returns"]


def test_icra_piecewise_acceptance_is_not_enough(icra2):
    case = ICRA["icra_derived_piecewise_acceptance_AA"]
    nfas = icra_nfas(icra2, case["history"], p1=case["story"][0])
    pieces = case["expected"]["piecewise_split"]
    assert all(A.accepts(p) for A, p in zip(nfas, pieces)) is \
        case["expected"]["each_piece_accepted_by_its_M_j"]
    assert not icra_composite(icra2, case["history"]).accepts(case["story"])


def test_icra_literal_artefacts(icra2):
    # one state for start and goal: x re-enters o1 without a recording
    # (docs/notes/paper-examples-icra.md section 3, "Two things the construction leaves out")
    h = "b1 b1 o1 o1 o1 o1 b2"
    for w in ("AAADBD", "AABDCD"):
        assert not validate(icra2, w, h).consistent
        assert not icra_composite(icra2, h).accepts(w)
        assert icra_composite(icra2, h, split_roles=False).accepts(w)
    # "connect all of M_{m+1}'s states to F" read alone (end_anywhere=True): the story may end
    # before the last recording, the reading the STAR fixture records as
    # consistent_if_story_need_not_end_in_last_room.  By default both variants take the
    # preceding sentence ("when i = m, we let all vertices in C_p be acceptance states").
    case = STAR["star_single_end_must_be_last_room"]
    m1 = builtin_map("icra_fig1")
    for variant in ("literal", "corrected"):
        assert icra_composite(m1, case["history"], variant=variant,
                              end_anywhere=True).accepts(case["story"]) is \
            case["expected"]["consistent_if_story_need_not_end_in_last_room"]
        assert icra_composite(m1, case["history"], variant=variant).accepts(case["story"]) is \
            case["expected"]["consistent"]
    # SUBG(G, o, o) inside an occupancy interval: x passes through o unrecorded
    m = Map("inside", ["S", "X", "Y"], {}, ["o"], {"Ra": ["S", "o", "X"], "Rb": ["o", "Y"]})
    assert not validate(m, "SYX", "o o").consistent
    assert icra_composite(m, "o o", variant="literal").accepts("SYX")
    assert not icra_composite(m, "o o").accepts("SYX")


# ============================================================================ API odds and ends


def test_api_details(fig2):
    with pytest.raises(ValueError):
        star_subgraphs(fig2, "A", "", variant="printed")
    g = get_subgraph([("A", "b"), ("b", "c")], "A", ["A"], ["b"])
    assert isinstance(g, Graph) and g.V() == ["A", "b"] and g.E() == [("A", "b")]
    # A2-ii: a start without a neighbour in V_C is still in the sub-graph
    assert get_subgraph([("x", "y")], "s", [], []).V() == ["s"]
    r = star_validate(fig2, "AB", "o1 b2")
    assert not r and r.reason.startswith("malformed history: ")
    r = star_validate(fig2, "ACBAC", "b1 o1 o1 b2 o2 o2")
    assert "story element 4" in r.reason


# ============================================================================ random cross-checks

ROOMS = "ABCDE"


def random_map(rng):
    """A small random region map; some features touch no region, some regions one feature."""
    nreg = rng.randint(1, 5)
    rooms = list(ROOMS[:rng.randint(1, 4)])
    beams = {"b%d" % (i + 1): ["b%dx" % (i + 1), "b%dy" % (i + 1)]
             for i in range(rng.randint(0, 3))}
    occ = ["o%d" % (i + 1) for i in range(rng.randint(0, 3))]
    regs = [[] for _ in range(nreg)]
    for ss in beams.values():
        for s in ss:
            regs[rng.randrange(nreg)].append(s)
    for f, ks in [(r, [0, 1, 1, 2, 2, 3]) for r in rooms] + [(o, [0, 1, 1, 2, 3]) for o in occ]:
        for i in rng.sample(range(nreg), min(rng.choice(ks), nreg)):
            regs[i].append(f)
    return Map("rnd", rooms, beams, occ, [r for r in regs if r])


def random_inputs(rng, m, agents):
    story = [rng.choice(m.rooms) for _ in range(rng.randint(1, 5))]
    sensors = list(m.beams) + list(m.occupancy)
    hist, active = [], set()
    for _ in range(rng.randint(0, 6) if sensors else 0):
        s = rng.choice(sensors)
        if m.is_beam(s):
            hist.append([s, "A"])
        elif agents == "single" and rng.random() < 0.85:
            hist += [[s, "A"], [s, "D"]]
        elif rng.random() < 0.9:
            hist.append([s, "D" if s in active else "A"])
            (active.discard if s in active else active.add)(s)
        else:                                   # occasionally malformed
            hist.append([s, rng.choice("AD")])
    return story, hist


def _maps(rng):
    if rng.random() < 0.6:
        return random_map(rng)
    return builtin_map(rng.choice(["star_fig2", "icra_fig2", "star_fig1"])) \
        if rng.random() < 0.9 else builtin_map("icra_fig1")


def _sweep(seed, agents, check):
    rng = random.Random(seed)
    n = 300 * sweep_scale()
    stats = {"consistent": 0, "literal_differs": 0}
    for _ in range(n):
        m = _maps(rng)
        if not m.rooms:
            continue
        st, h = random_inputs(rng, m, agents)
        want = validate(m, st, h, agents).consistent
        stats["consistent"] += want
        stats["literal_differs"] += check(m, st, h, want)
    return n, stats


def test_star_dp_agrees_with_engine_single():
    def check(m, st, h, want):
        r = star_validate(m, st, h)
        assert r.consistent is want, (m.to_dict(), st, h, r.reason)
        return star_validate(m, st, h, variant="literal").consistent is not want
    n, stats = _sweep(11, "single", check)
    assert 0 < stats["consistent"] < n
    assert stats["literal_differs"] > 0


def test_icra_composite_agrees_with_engine():
    def check(m, st, h, want):
        assert icra_validate_agent_story(m, st, h) is want, (m.to_dict(), st, h)
        return icra_validate_agent_story(m, st, h, variant="literal") is not want
    n, stats = _sweep(12, "single", check)
    assert 0 < stats["consistent"] < n
    assert stats["literal_differs"] > 0


def test_corrected_multi_family_agrees_with_engine():
    def check(m, st, h, want):
        r = star_validate_multi(m, st, h, variant="corrected")
        assert r.consistent is want, (m.to_dict(), st, h, r.reason)
        return star_validate_multi(m, st, h).consistent is not want
    n, stats = _sweep(13, "multi", check)
    assert 0 < stats["consistent"] < n
    assert stats["literal_differs"] > 0
