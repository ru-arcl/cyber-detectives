"""The applet's non-drawing logic (cyber_detectives.compat.applet).

The full replay of the recorded applet sessions is in tests/test_compat_golden.py
(applet.json); these tests pin the parsing, the click geometry against
tests/fixtures/paper/star_fig2_geometry.json, and the stateful bugs by name.
"""

from __future__ import annotations

import json
import os

import pytest

from cyber_detectives.compat import applet as ap
from cyber_detectives.compat.original import DetectiveGame, NullPointerException

HERE = os.path.dirname(os.path.abspath(__file__))
GEOM = os.path.join(HERE, "fixtures", "paper", "star_fig2_geometry.json")


def test_java_split():
    assert ap.java_split("") == [""]
    assert ap.java_split("b1") == ["b1"]
    assert ap.java_split("b1,") == ["b1"]
    assert ap.java_split(",b1") == ["", "b1"]
    assert ap.java_split(",") == []
    assert ap.java_split("b1,,o1,,") == ["b1", "", "o1"]


def test_parse_sensor_text():
    assert ap.parse_sensor_text("b1,o1,x,o2,B1, b2,b2", "single") == [
        ["b1", "A"], ["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"], ["b2", "A"]]
    assert ap.parse_sensor_text("o1,o2,b2,o2,o1,o1", "multi") == [
        ["o1", "A"], ["o2", "A"], ["b2", "A"], ["o2", "D"], ["o1", "D"], ["o1", "A"]]


def test_story_chars():
    assert ap.story_chars("ACB") == ["A", "C", "B"]
    assert ap.story_chars("A\U0001F600") == ["A", "\ud83d", "\ude00"]


def test_click_scaling():
    assert ap.click_to_world(50, 40) == (100, 80)
    assert ap.click_to_world(-5, 10) == (-10, 20)
    assert ap.click_to_world(399, 299) == (798, 598)


def test_click_scaling_follows_java_float_arithmetic():
    """(int)(px * 800f / 400) with px an int: px is rounded to float first (|px| > 2^24),
    every step rounds to float, the cast saturates. Expected values recorded from the JVM
    (tests/fixtures/golden/edge_cases.json, case applet_click_limits)."""
    assert ap.click_to_world(16777217, 16777219) == (33554432, 33554440)
    assert ap.click_to_world(33554435, 16777215) == (67108872, 33554430)
    assert ap.click_to_world(2147483647, -2147483648) == (2147483647, -2147483648)
    assert ap.click_to_world(-1, 0) == (-2, 0)


def test_harness_number_conversion_is_number_int_value():
    """Harness.java: ((Number) x).intValue() on Long (low 32 bits) or Double (saturating)."""
    from cyber_detectives.compat.original import HarnessError

    iv = ap.java_number_int_value
    assert iv(4294967396) == 100
    assert iv(9223372036854775807) == -1 and iv(-9223372036854775808) == 0
    assert iv(1e10) == 2147483647 and iv(-1e10) == -2147483648 and iv(float("inf")) == 2147483647
    assert iv(-3.7) == -3 and iv(2.9999) == 2 and iv(float("nan")) == 0
    for bad in (True, None, "1", [1], 1 << 63):
        with pytest.raises(HarnessError):
            iv(bad)
    rec = ap.run_steps({"steps": [{"click": [4294967396, 10]}, {"click": [1e10, 10.9]}]})
    assert [(r["x"], r["y"], r["hit"]) for r in rec] == [(200, 20, "A"), (2147483647, 20, None)]
    assert rec[0]["px"] == 4294967396  # recorded as given


def test_hit_testing_matches_geometry_fixture():
    geo = json.load(open(GEOM, encoding="utf-8"))
    env = ap.Environment.create_example_environment(DetectiveGame.get_basic_game())
    rects = {r.vertex.name: [r.x, r.y, r.width, r.height] for r in env.room_a + env.occu_a}
    for name, r in list(geo["geometry"]["room_rects"].items()) + list(geo["geometry"]["occupancy_rects"].items()):
        assert rects[name] == r, name
    beams = {env.beam_a[i].vertex.name: [r.x, r.y, r.width, r.height] for i, r in enumerate(env.beam_rect)}
    assert beams == {k: [float(x) for x in v] for k, v in geo["applet"]["beam_hit_rects"].items()}
    assert [s.vertex.name for s in env.beam_a] == ["b1u", "b1d", "b2l", "b2r"]  # HashMap<Integer> order
    assert geo["applet"]["hit_test_order"] == ["occupancy", "rooms", "beams"]
    hit = lambda x, y: (lambda v: v and v.name)(env.get_clicked_vertex(x, y))
    assert hit(100, 80) == "A" and hit(400, 360) == "o1" and hit(250, 285) == "b1u"
    assert hit(624, 182) == "B"  # y in [180, 185) on the b2 strips: rooms are tested first
    assert hit(624, 185) == "b2l"
    assert hit(210, 0) is None  # x = 210 is just outside A (half-open) and on no other feature
    assert hit(209, 184) == "A" and hit(210, 184) is None


def test_run_outputs():
    a = ap.Applet()
    assert a.instructions == ap.START_INSTRUCTIONS
    a.run()
    assert a.result_text == ap.NOTHING_TO_VALIDATE
    a.story_text, a.sensor_text = "ACBAC", "b1,o1,o2,b2"
    step = {}
    a.run(step)
    assert a.result_text == "Valid story.\nA possible path: A[b1u]C[o1][o2]B[b2r]AC"
    assert step["parsed_history"] == [["b1", "A"], ["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"], ["b2", "A"]]
    a.single_selected = False
    a.sensor_text = "b1,o1,o2,b2,o2,o1"
    a.run()
    assert a.result_text == ap.VALID
    a.sensor_text = "b1,o2,o2,o1,b2,o1"
    a.run()
    assert a.result_text == ap.INCONSISTENT


def test_B6_crash_poisons_later_runs_until_reset():
    a = ap.Applet()
    a.story_text, a.sensor_text = "DA", ""
    with pytest.raises(NullPointerException) as ei:
        a.run()
    assert ei.value.top_frame.endswith("updateStartingVertex(DetectiveGame.java:30)")
    assert [v and v.name for v in a.env.game.story.visited_vertices] == [None, "A"]  # not cleared
    a.story_text, a.sensor_text = "AC", "b1"
    with pytest.raises(NullPointerException) as ei:
        a.run()
    assert ei.value.top_frame.endswith("updateStartingVertex(DetectiveGame.java:23)")
    a.reset()
    a.story_text, a.sensor_text = "AC", "b1"
    a.run()
    assert a.result_text == "Valid story.\nA possible path: A[b1u]C"


def test_clicks_and_stale_vertices_after_reset():
    a = ap.Applet()
    old_a = a.env.game.graph.vertex_name_map["A"]
    a.click(50, 40)
    assert a.story_text == "A"
    assert a.instructions.startswith("Current location: A\nReachable features: b1u, b1d, b2l, o1, C, A\n")
    a.click(125, 142)  # b1u: the agent crosses to b1d
    assert a.sensor_text == "b1" and a.instructions.startswith("Current location: b1d\n")
    a.click(50, 40)  # b1d touches A, so A is offered again: the story grows
    assert a.story_text == "AA"
    a.reset()
    assert a.env.game.graph.vertex_name_map["A"] is not old_a
    step = {}
    a.click(50, 40, step)
    assert step["hit"] == "A" and step["accepted"]
    assert a.env.get_clicked_vertex(100, 80) is old_a  # the Environment keeps the first game's vertices


def test_run_steps_matches_harness_record_shape():
    recs = ap.run_steps({"steps": [{"run": {"story": "A", "sensors": "", "mode": "single"}}, {"reset": True},
                                   {"click": [5, 5]}]})
    assert [r["action"] for r in recs] == ["run", "reset", "click"]
    assert recs[0]["result_text"] == "Valid story.\nA possible path: A" and recs[0]["exception"] is None
    assert recs[2]["hit"] == "A" and recs[2]["x"] == 10
