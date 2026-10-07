"""Unit tests for the port of the original Java code (cyber_detectives.compat.original).

The exhaustive check is tests/test_compat_golden.py; these tests pin individual quirks by
name (bug ids as in docs/notes/original-inventory.md) and the public helpers.
"""

from __future__ import annotations

import pytest

import cyber_detectives as cd
from cyber_detectives.compat import original as o
from cyber_detectives.compat.original import (ArrayIndexOutOfBoundsException, DetectiveGame, Edge,
                                              JavaException, NullPointerException, STAR_SPEC, build_game,
                                              capture_stdout, run_harness_case, structure, validate_compat)


def star(story, history, agents="single"):
    return validate_compat(STAR_SPEC, list(story), [h if isinstance(h, list) else [h[:-1], h[-1]]
                                                    for h in history], agents)


def H(s):
    """'b1A o1A o1D' -> pairs."""
    return [[t[:-1], t[-1]] for t in s.split()]


# --------------------------------------------------------------------------- the games


def test_basic_game_structure():
    g = DetectiveGame.get_basic_game()
    ids = {v.name: v.id for v in g.graph.vertex_map.values()}
    assert ids == {"SV": 2, "A": 3, "B": 4, "C": 5, "b1u": 6, "b1d": 7, "b2l": 8, "b2r": 9, "o1": 10, "o2": 11}
    assert len(g.graph.edge_map) == 16  # STAR Fig. 3(a)'s 15 edges + the virtual SV-A edge
    assert [v.name for v in g.graph.vertex_name_map["o1"].neighbors] == ["A", "C", "b2l", "b1u", "b1d", "o2"]
    assert g.graph.vertex_name_map["b2r"].asso_vertex.name == "b2l"


def test_generic_builder_reproduces_basic_game():
    a, b = structure(DetectiveGame.get_basic_game()), structure(build_game(STAR_SPEC))
    assert a == b  # neighbour iteration order included
    res = run_harness_case({"id": "x", "op": "builder_check", "map": "star_fig2"})
    assert res["return"]["identical"] and res["return"]["neighbor_iteration_order_equal"]


def test_builder_accepts_core_map_dict():
    m = cd.builtin_map("star_fig2")
    assert structure(build_game(m.to_dict())) == structure(DetectiveGame.get_basic_game())


def test_four_games():
    # MultiFeasible passes the single-agent check too (overlapping o1/o2 intervals, bug B5)
    expect = {"get_single_infeasible_game": (False, False), "get_single_feasible_game": (True, True),
              "get_multi_feasible_game": (True, True), "get_multi_infeasible_game": (False, False)}
    for name, (single, multi) in expect.items():
        g = getattr(DetectiveGame, name)()
        sv = g.graph.vertex_name_map["SV"]
        assert o.validate_agent_story(g.graph, sv, g.story, g.ob_his) is single, name
        g = getattr(DetectiveGame, name)()
        assert o.validate_agent_story_multi(g.graph, g.graph.vertex_name_map["SV"], g.story, g.ob_his) is multi, name


def test_single_feasible_path_and_trace():
    g = DetectiveGame.get_single_feasible_game()
    with capture_stdout() as out:
        path = o.get_agent_story(g.graph, g.graph.vertex_name_map["SV"], g.story, g.ob_his)
    assert path == "A[b1u]C[o1][o2]B[b2r]AC"
    lines = out.getvalue().splitlines()
    assert lines[-2:] == ["SV 0", "search status: [SV]ACBAC"]


# --------------------------------------------------------------------------- bugs by name


def test_B8_story_may_end_before_last_recording():
    assert star("A", ["b1A"]) == (True, "A[b1u]")


def test_B5_single_agent_history_not_checked():
    assert star("A", ["o1D"])[0] is True
    assert star("A", ["o1A", "o2A"])[0] is True


def test_B1_multi_false_negative():
    assert star("BC", ["o1A", "b2A", "o1D"], "multi") == (False, None)


def test_B3_path_with_extra_crossings():
    ok, path = star("CAC", ["o1A", "b1A", "o1D", "b1A"])
    assert ok and path == "C[o1][b1d][b1u][b1d]AC"  # four crossings for three recordings


def test_B4_crash_on_inconsistent_input():
    g = DetectiveGame.get_single_infeasible_game()
    with pytest.raises(ArrayIndexOutOfBoundsException) as ei:
        o.get_agent_story(g.graph, g.graph.vertex_name_map["SV"], g.story, g.ob_his)
    e = ei.value
    assert e.describe() == {
        "class": "java.lang.ArrayIndexOutOfBoundsException", "message": "7",
        "top_frame": "projects.cyberDetective.Algorithms.getAgentStoryStatuses(Algorithms.java:365)",
        "origin_frame": "projects.cyberDetective.Algorithms.getAgentStoryStatuses(Algorithms.java:365)",
        "trace": ["projects.cyberDetective.Algorithms.getAgentStoryStatuses(Algorithms.java:365)",
                  "projects.cyberDetective.Algorithms.getAgentStory(Algorithms.java:229)"]}
    assert isinstance(e, JavaException) and "Algorithms.java:365" in str(e)


def test_unknown_rooms_crash_like_java():
    with pytest.raises(NullPointerException) as ei:
        star("AX", ["b1A"])
    assert ei.value.top_frame.endswith("validateAgentStory(Algorithms.java:207)")
    with pytest.raises(NullPointerException) as ei:
        star("XA", [])
    assert ei.value.top_frame.endswith("updateStartingVertex(DetectiveGame.java:30)")
    with pytest.raises(NullPointerException) as ei:
        star("AX", ["b1A"], "multi")
    assert ei.value.top_frame.endswith("validateAgentStoryMulti(Algorithms.java:430)")


def test_B6_null_start_corrupts_sv():
    g = DetectiveGame.get_basic_game()
    with pytest.raises(NullPointerException):
        g.update_starting_vertex(None)
    sv = g.graph.vertex_name_map["SV"]
    assert sv.neighbors.to_array() == [None]
    with pytest.raises(NullPointerException) as ei:
        g.update_starting_vertex(g.graph.vertex_name_map["A"])
    assert ei.value.top_frame.endswith("(DetectiveGame.java:23)")


def test_B7_edge_id_collisions():
    assert Edge.get_edge_id(5, 3) == Edge.get_edge_id(3, 5) == 3 * 65536 + 5
    assert Edge.get_edge_id(1, 65536) == Edge.get_edge_id(0, 131072)  # ids >= 65536 collide
    assert Edge.get_edge_id(40000, 40001) == 40000 * 65536 + 40001 - 2 ** 32  # Java int overflow
    assert star("AC", ["b1D"])[0] is True  # a beam "D" counts as a crossing


def test_multi_start_vertex_is_a_goal_on_activation():
    # Algorithms.java:408-414: x may stay put across an occupancy activation
    assert star("A", ["o2A"], "multi") == (True, None)


# --------------------------------------------------------------------------- output


def test_capture_stdout_is_a_redirect():
    with capture_stdout() as outer:
        o._println("outer")
        with capture_stdout() as inner:
            o._println("inner")
    assert outer.getvalue() == "outer\n" and inner.getvalue() == "inner\n"
    o._println("discarded")  # no capture: nothing happens


def test_algorithms_main_output():
    with capture_stdout() as out:
        o.main([])
    text = out.getvalue()
    assert text.startswith("\nvertices: \nSV: A \nA: SV b1u b1d b2l o1 C \n")
    assert text.endswith("\nStory inconsistent.\n")
    assert "story: A C B A C \nobservation history: b1[A]o2[A]o2[D]o1[A]b2[A]o1[D]\n" in text


def test_graph_dump_format():
    g = DetectiveGame.get_basic_game()
    with capture_stdout() as out:
        g.graph.dump()
    lines = out.getvalue().split("\n")
    assert lines[:3] == ["", "vertices: ", "SV: A "]
    edge_lines = lines[lines.index("edges: ") + 1:]
    assert [len(x.split("  ")) - 1 for x in edge_lines[:4]] == [5, 5, 5, 1]  # 16 edges, 5 per line
    assert edge_lines[4:] == [""]


def test_validate_compat_rejects_bad_input():
    with pytest.raises(ValueError):
        validate_compat(STAR_SPEC, ["A"], [["b9", "A"]], "single")
    with pytest.raises(ValueError):
        validate_compat(STAR_SPEC, ["A"], [], "both")


def test_core_validate_compat_original():
    m = cd.builtin_map("star_fig2")
    r = cd.validate(m, "ACBAC", "b1 o1 o1 o2 o2 b2", compat="original")
    assert r.consistent and r.path_string() == "A[b1u]C[o1][o2]B[b2r]AC"
    assert cd.validate(m, "ACBAC", "b1 o1 o1 b2 o2 o2", compat="original").consistent is False
