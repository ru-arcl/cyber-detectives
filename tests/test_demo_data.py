"""The demo's static data (``docs/js/data.js``, from ``tools/gen_demo_data.py``).

- ``data.js`` is up to date (regenerated in memory and compared byte for byte), small, pure
  ASCII, free of local paths, and its body is the JSON of ``build_data()``;
- every precomputed route, interior polyline and beam crossing is a legal motion in the
  drawing (checked with the geometry's exact decomposition), and routes stay inside their
  region;
- ``polyline_for_path`` (which the demo's JS mirrors) turns random engine witnesses on every
  demo map -- Problem 1 single/multi/unreported visits, Problems 2, 3, 4 -- into polylines
  that stay in free space and pass through the witness's places, beam sides and room visits
  in order (``geometry.trace_polyline`` replays them independently);
- the presets agree with the paper fixtures and the original's bugs they illustrate.

Sizes scale with ``CD_TEST_SCALE``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import shutil
import subprocess

import pytest

from conftest import REPO_ROOT, paper_cases, sweep_scale

from cyber_detectives import (builtin_map, closest_story, shortest_superstory, validate,
                              validate_intervals)
from cyber_detectives import geometry as G

SCALE = sweep_scale()
DATA_JS = os.path.join(REPO_ROOT, "docs", "js", "data.js")


def _load_generator():
    path = os.path.join(REPO_ROOT, "tools", "gen_demo_data.py")
    spec = importlib.util.spec_from_file_location("gen_demo_data", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


GEN = _load_generator()


@pytest.fixture(scope="module")
def data():
    return GEN.build_data()


@pytest.fixture(scope="module")
def text():
    with open(DATA_JS, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------- the file

def test_data_js_is_up_to_date(data, text):
    assert text == GEN.render(data), \
        "docs/js/data.js is out of date: run python3 tools/gen_demo_data.py"


def test_data_js_is_small_ascii_and_has_no_local_paths(text):
    raw = text.encode("utf-8")
    assert len(raw) < 1_000_000
    assert raw.decode("ascii")  # pure ASCII: safe from file:// whatever the page charset
    for bad in ("/home/", "/tmp/", "C:\\", "http://", "https://"):
        assert bad not in text


def test_data_js_body_is_the_json(data, text):
    start = text.index("root.CD_DATA = ") + len("root.CD_DATA = ")
    end = text.rindex(";\n})(")
    assert json.loads(text[start:end]) == json.loads(json.dumps(data))
    assert "function" not in text[start:end]  # data only


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_data_js_runs_as_classic_script(data):
    script = (
        "const vm=require('vm'),fs=require('fs');const ctx={};vm.createContext(ctx);"
        "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
        "process.stdout.write(JSON.stringify(ctx.CD_DATA));")
    out = subprocess.run(["node", "-e", script, DATA_JS], check=True, capture_output=True,
                         text=True, timeout=60).stdout
    assert json.loads(out) == json.loads(json.dumps(data))


def test_maps_presented(data):
    assert data["map_order"] == ["star_fig2", "icra_fig2"]
    assert data["maps"]["star_fig2"]["also"] == ["icra_fig1"]
    assert "applet" in data["maps"]["star_fig2"]["provenance"]["summary"]
    assert "measured from ICRA 2011 Fig. 2" in data["maps"]["icra_fig2"]["provenance"]["geometry"]
    for name, e in data["maps"].items():
        m = builtin_map(name)
        assert e["map"]["regions"] == {r: list(fs) for r, fs in m.regions.items()}
        assert e["map"]["edges"] == [list(x) for x in m.to_dict()["edges"]]


# ---------------------------------------------------------------------- precomputed geometry

def _runs(g, pts):
    out = []
    for p, q in zip(pts, pts[1:]):
        for _, _, kind, name in g.segment_runs(tuple(p), tuple(q)):
            if not out or out[-1] != (kind, name):
                out.append((kind, name))
    return out


@pytest.mark.parametrize("name", GEN.DEMO_MAPS)
def test_routes_stay_in_their_region(data, name):
    e = data["maps"][name]
    g = G.as_geometry(builtin_map(name))
    m = builtin_map(name)
    for r, rt in e["routes"].items():
        assert set(rt["anchors"]) == {GEN.LABEL} | set(m.regions[r])
        for a, pa in rt["anchors"].items():
            assert g.location(*pa) == ("region", r), (name, r, a)
            assert set(rt["paths"][a]) == set(rt["anchors"]) - {a}
            for b, pts in rt["paths"][a].items():
                assert pts[0] == pa and pts[-1] == rt["anchors"][b]
                assert rt["paths"][b][a] == pts[::-1]
                assert _runs(g, pts) == [("region", r)], (name, r, a, b, pts)
                # and with clearance from every obstacle (cosmetic, but promised)
                for p, q in zip(pts, pts[1:]):
                    assert g.segment_is_free(tuple(p), tuple(q), margin=GEN.CLEARANCE), (r, a, b, p, q)


@pytest.mark.parametrize("name", GEN.DEMO_MAPS)
def test_interiors_and_crossings_are_legal(data, name):
    e = data["maps"][name]
    m = builtin_map(name)
    g = G.as_geometry(m)
    for f, it in e["interiors"].items():
        kind = m.kind(f)
        assert g.location(*it["point"]) == (kind, f)
        assert set(it["to"]) == set(m.regions_of[f])
        for r, pts in it["to"].items():
            assert pts[0] == it["point"] and pts[-1] == e["routes"][r]["anchors"][f]
            assert _runs(g, pts) == [(kind, f), ("region", r)], (f, r, pts)
    assert set(e["crossings"]) == set(m.sides)
    for s, c in e["crossings"].items():
        o = m.other_side(s)
        assert (c["to_side"], c["from_region"], c["to_region"]) == (
            o, m.side_region(s), m.side_region(o))
        assert c["points"][0] == e["routes"][c["from_region"]]["anchors"][s]
        assert c["points"][-1] == e["routes"][c["to_region"]]["anchors"][o]
        tr = G.trace_polyline(g, c["points"])
        assert tr.walk == [c["from_region"], c["to_region"]]
        assert [ev[3] for ev in tr.events if ev[1] == "beam"] == [(s, o)]


@pytest.mark.parametrize("name", GEN.DEMO_MAPS)
def test_drawing_matches_geometry(data, name):
    d = data["maps"][name]["drawing"]
    m = builtin_map(name)
    g = G.as_geometry(m)
    assert set(d["rooms"]) == set(m.rooms) and set(d["occupancy"]) == set(m.occupancy)
    assert set(d["regions"]) == set(m.regions)
    for r, spec in d["regions"].items():
        assert g.region_at(*spec["point"]) == r
    for b, spec in d["beams"].items():
        assert spec["sides"] == list(m.beams[b])
        for s in spec["sides"]:
            # the side label is outside the band, on side s, and the side rect is side s
            x, y, w, h = spec["side_rects"][s]
            assert g.feature_at(x + w / 2, y + h / 2) == s
            lx, ly = spec["side_labels"][s]
            cx = spec["band"][0] + spec["band"][2] / 2
            cy = spec["band"][1] + spec["band"][3] / 2
            nx, ny = spec["normals"][s]
            assert (lx - cx) * nx + (ly - cy) * ny > 0


# ---------------------------------------------------------------------- witness polylines

def _expected_walk(steps):
    out = []
    for s in steps:
        p = s.position if not isinstance(s, dict) else s["position"]
        if not out or out[-1] != p:
            out.append(p)
    return out


def _check_polyline(name, entry, g, m, steps, story=None, history=None):
    """The polyline of ``steps`` is legal, passes through the witness's places in order,
    crosses beams exactly as the witness does, and reports ``story`` / ``history``."""
    pts = GEN.polyline_for_path(entry, steps)
    tr = G.trace_polyline(g, pts)  # raises if it touches a wall or makes an illegal move
    assert tr.walk == _expected_walk(steps), (name, [s.to_dict() for s in steps], pts)
    want_cross = [(s.sensor, m.other_side(s.sensor)) for s in steps
                  if s.kind in ("cross", "pass")]
    assert [ev[3] for ev in tr.events if ev[1] == "beam"] == want_cross
    if story is not None:
        assert tr.story == list(story), (name, story, tr.story)
    if history is not None:
        assert tr.history == [list(e) for e in history]
    for p in pts:
        assert g.location(*p)[0] in ("region", "room", "occupancy", "side", "beam")
    return pts


def _random_story(rng, m, n):
    return [rng.choice(m.rooms) for _ in range(n)]


@pytest.mark.parametrize("name", GEN.DEMO_MAPS)
def test_polylines_of_single_agent_witnesses(data, name):
    entry = data["maps"][name]
    m = builtin_map(name)
    g = G.as_geometry(m)
    for i in range(25 * SCALE):
        rng = random.Random("demo/%s/single/%d" % (name, i))
        w = G.simulate_walk(g, rng, steps=rng.randint(3, 40))
        r = validate(m, w.story, w.history)
        assert r.consistent
        _check_polyline(name, entry, g, m, r.path, story=w.story, history=w.history)
        ru = validate(m, w.story, w.history, unreported_visits=True)
        pts = _check_polyline(name, entry, g, m, ru.path, history=w.history)
        assert pts[0] == entry["interiors"][w.story[0]]["point"]
        assert pts[-1] == entry["interiors"][w.story[-1]]["point"]


@pytest.mark.parametrize("name", GEN.DEMO_MAPS)
def test_polylines_of_multi_agent_witnesses(data, name):
    entry = data["maps"][name]
    m = builtin_map(name)
    g = G.as_geometry(m)
    for i in range(20 * SCALE):
        rng = random.Random("demo/%s/multi/%d" % (name, i))
        mw = G.simulate_multi(g, rng, agents=1 + i % 4, steps=rng.randint(4, 30))
        r = validate(m, mw.story, mw.history, agents="multi")
        assert r.consistent
        _check_polyline(name, entry, g, m, r.path, story=mw.story)


@pytest.mark.parametrize("name", GEN.DEMO_MAPS)
def test_polylines_of_problem_2_3_4_witnesses(data, name):
    entry = data["maps"][name]
    m = builtin_map(name)
    g = G.as_geometry(m)
    seen = {2: 0, 3: 0, 4: 0}
    for i in range(12 * SCALE):
        rng = random.Random("demo/%s/p234/%d" % (name, i))
        w = G.simulate_walk(g, rng, steps=rng.randint(3, 25))
        story = list(w.story)
        if len(story) > 1 and rng.random() < 0.7:
            del story[rng.randrange(1, len(story))]  # a partial story
        agents = rng.choice(("single", "multi"))
        r3 = shortest_superstory(m, story, w.history, agents=agents,
                                 anchored=rng.random() < 0.5)
        if r3 is not None:
            seen[3] += 1
            _check_polyline(name, entry, g, m, r3.path, story=r3.story)
        bad = _random_story(rng, m, rng.randint(1, 5))
        r4 = closest_story(m, bad, w.history, agents=agents)
        if r4 is not None:
            seen[4] += 1
            _check_polyline(name, entry, g, m, r4.path, story=r4.story)
        case = rng.randint(1, 6)
        r2 = validate_intervals(m, story, w.history, case=case, agents=agents)
        if r2.consistent:
            seen[2] += 1
            _check_polyline(name, entry, g, m, r2.path)
    assert min(seen.values()) >= 3, seen


def test_polyline_rejects_impossible_steps(data):
    from cyber_detectives import Step
    entry = data["maps"]["star_fig2"]
    with pytest.raises(ValueError):
        GEN.polyline_for_path(entry, [Step("start", "A", 0, 1), Step("visit", "B", 0, 2)])
    with pytest.raises(ValueError):  # b2r does not touch R1
        GEN.polyline_for_path(entry, [Step("start", "A", 0, 1), Step("move", "R1", 0, 1),
                                      Step("cross", "R2", 1, 1, sensor="b2r", event=0)])
    assert GEN.polyline_for_path(entry, []) == []


# ---------------------------------------------------------------------- presets

def _paper(name, cid):
    return next(c for c in paper_cases(name) if c["id"] == cid)


def test_presets_shape_and_paper_agreement(data):
    presets = {p["id"]: p for p in data["presets"]}
    assert len(presets) == len(data["presets"])
    for p in data["presets"]:
        assert p["map"] in data["maps"] and p["problem"] in (1, 2, 3, 4)
        assert p["agents"] in ("single", "multi")
        assert p["caption"] and p["ref"] and p["title"]
        # a caption that names a free region turns the region labels on
        assert p["region_labels"] is bool(re.search(r"\bR\d+\b", p["caption"])), p["id"]
        ex = p["expected"]
        assert (ex["path"] is None) == (not ex["consistent"]) == (ex["polyline"] is None)
        if ex["path"] is not None:
            assert ex["polyline"] == GEN.polyline_for_path(data["maps"][p["map"]], ex["path"])
    # verdicts / answers recorded in the paper fixtures
    assert presets["star_eq1_eq2_single"]["expected"]["consistent"] is \
        _paper("star", "star_eq1_eq2_single")["expected"]["consistent"] is False
    assert presets["star_eq1_eq3_multi"]["expected"]["consistent"] is \
        _paper("star", "star_eq1_eq3_multi")["expected"]["consistent"] is True
    assert presets["icra_fig1_ABAC"]["expected"]["consistent"] is True
    for sec, cid in (("sec3a", "icra_sec3a_ABDEC_b1b3o2o2b4"),
                     ("sec5a", "icra_sec5a_ABDEC_b1b2o2o2b4")):
        c = _paper("icra", cid)["expected"]
        assert presets["icra_%s_p1" % sec]["expected"]["consistent"] is c["consistent"]
        p3 = presets["icra_%s_p3" % sec]["expected"]
        assert "".join(p3["story"]) in c["problem3_anchored_shortest_superstories"]
        p4 = presets["icra_%s_p4" % sec]["expected"]
        assert p4["edits"] == c["problem4_min_edits"]
        assert "".join(p4["story"]) in c["problem4_optimal_stories"]
    assert presets["icra_sec3a_p1_unreported"]["expected"]["consistent"] is \
        _paper("icra", "icra_sec3a_ABDEC_b1b3o2o2b4")["expected"][
            "consistent_if_unreported_room_visits_allowed"]
    # several optimal stories exist: the caption must not claim the engine's is the only one
    assert "for example" in presets["icra_sec3a_p4"]["caption"]
    assert any(p["region_labels"] for p in data["presets"])
    p2 = presets["icra_p2_case2"]["expected"]
    c2 = _paper("icra", "icra_derived_problem2_case2_counterexample")["expected"]
    assert p2["consistent"] is c2["problem2_by_interval_case"]["2"] is True
    assert p2["paper_case2_procedure"] is c2["paper_case2_procedure_returns"] is False


def test_bug_presets_show_the_bug(data):
    bugs = {p["bug"]["id"]: p for p in data["presets"] if p["bug"]}
    assert set(bugs) == {"B1", "B3", "B5", "B8"}
    for bid, p in bugs.items():
        assert p["bug"]["explanation"]
        assert p["original"]["consistent"] != p["expected"]["consistent"] or bid == "B3"
    # B3: both say consistent, but the original's path has more crossings than recordings
    b3 = bugs["B3"]
    n_rec = len(b3["events"])
    assert b3["original"]["path_string"].count("[") > n_rec
    assert b3["expected"]["path_string"].count("[") == n_rec
    assert bugs["B5"]["expected"]["reason"].startswith("malformed history")
    assert bugs["B1"]["original"]["consistent"] is False
    assert bugs["B8"]["original"]["consistent"] is True
