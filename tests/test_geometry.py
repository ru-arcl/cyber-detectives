"""Map geometry: regions derived from the drawings, hit-testing, portals, drawable paths."""

from __future__ import annotations

import copy
import json
import os
import random

import pytest

from conftest import load_fixture, paper_cases

from cyber_detectives import builtin_map, builtin_map_names, validate
from cyber_detectives import geometry as G
from cyber_detectives.geometry import Geometry, GeometryError

GEO_NAMES = G.builtin_geometry_names()


def _maps_with_geometry():
    return [n for n in builtin_map_names() if n in GEO_NAMES]


# ---------------------------------------------------------------------- data files

def test_every_geometry_has_a_builtin_map():
    assert set(GEO_NAMES) >= {"star_fig2", "icra_fig2"}
    for n in GEO_NAMES:
        assert n in builtin_map_names(), n
        assert G.builtin_geometry(n).name == n


@pytest.mark.parametrize("name", _maps_with_geometry())
def test_derived_regions_equal_map_regions(name):
    """The drawing realises the map exactly: same features, same regions (names and the
    features each touches)."""
    m = builtin_map(name)
    assert m.geometry is not None, "builtin_map should attach data/geometry/%s.json" % name
    assert G.check_geometry(m) == []
    g = G.as_geometry(m)
    assert {k: set(v) for k, v in g.derive_regions().items()} == \
        {k: set(v) for k, v in m.regions.items()}
    # no sealed pockets or unnamed components
    assert all(not n.startswith("_") for n in g.decomposition.comp_names), \
        g.decomposition.comp_names


def test_star_fig2_is_the_applet_geometry_verbatim():
    fx = load_fixture("paper", "star_fig2_geometry.json")["geometry"]
    g = G.builtin_geometry("star_fig2")
    raw = g.raw
    assert raw["bounding_box"] == fx["bounding_box"]
    assert raw["walls"]["segments"] == fx["walls"]["segments"]
    assert raw["walls"]["stroke"] == fx["walls"]["stroke"]
    assert raw["rooms"] == fx["room_rects"]
    assert raw["occupancy"] == fx["occupancy_rects"]
    for b in ("b1", "b2"):
        assert raw["beams"][b]["stroke"] == fx["beam_sides"]["stroke"]
        for s, seg in raw["beams"][b]["sides"].items():
            assert seg == fx["beam_sides"]["segments"][s]
    assert "Environment.createExampleEnvironment" in g.meta["provenance"]


def test_icra_fig2_conventions():
    g = G.builtin_geometry("icra_fig2")
    m = builtin_map("icra_fig2", geometry=False)
    assert g.meta["provenance"] == "measured from ICRA 2011 Fig. 2"
    assert g.size == (800, 510)
    for b, sides in m.beams.items():
        spec = g.raw["beams"][b]
        assert len(spec["segment"]) == 4, "each beam is ONE segment"
        assert set(spec["sides"]) == set(sides)
        # b<k>1 is the upper/left side, as labelled in ICRA Fig. 3
        assert spec["sides"][b + "1"] in ("-x", "-y")
    for seg in g.raw["walls"]["segments"]:
        assert seg[0] == seg[2] or seg[1] == seg[3], "walls are axis-aligned"


def test_paper_geometry_fixture_loads_and_matches_its_expectations():
    """The paper geometry fixture (tests/fixtures/paper/star_fig2_geometry.json, applet key
    names) loads as is; regions, G and areas agree with the raster flood fill recorded there."""
    fx = load_fixture("paper", "star_fig2_geometry.json")
    g = Geometry.from_dict(fx["geometry"])
    exp = fx["expected"]
    d = g.decomposition
    found = {}
    for reg in exp["regions"]:
        name = g.region_at(*reg["interior_point"])
        assert name is not None
        c = d.comp_of_name[name]
        assert d.comp_touches[c] == set(reg["touches"])
        area = sum((d.xs[i + 1] - d.xs[i]) * (d.ys[j + 1] - d.ys[j])
                   for i in range(d.nx) for j in range(d.ny) if d.comp[i * d.ny + j] == c)
        assert area == pytest.approx(reg["approx_area"], rel=0.01)
        found[reg["name"]] = name
    assert len(set(found.values())) == 4
    edges = set()
    for fs in g.derive_regions().values():
        for a in fs:
            for b in fs:
                if a < b:
                    edges.add((a, b))
    assert edges == {tuple(sorted(e)) for e in exp["edges_from_geometry"]}


def _regions_with_variant(mutate):
    d = copy.deepcopy(G.builtin_geometry("star_fig2").raw)
    d.pop("regions", None)
    mutate(d)
    return Geometry.from_dict(d).derive_regions()


def test_reading_sensitivity_matches_map_geometry_notes():
    """docs/notes/map-geometry.md: butt caps on the beams merge R1, R2, R4 (2 components);
    butt caps on the walls alone change nothing."""
    def beams_butt(d):
        for b in d["beams"].values():
            b["cap"] = "butt"

    def walls_butt(d):
        d["walls"]["cap"] = "butt"

    assert len(_regions_with_variant(beams_butt)) == 2
    base = sorted(sorted(v) for v in _regions_with_variant(lambda d: None).values())
    assert sorted(sorted(v) for v in _regions_with_variant(walls_butt).values()) == base
    assert len(base) == 4


# ---------------------------------------------------------------------- hit-testing

@pytest.mark.parametrize("name", GEO_NAMES)
def test_hit_testing(name):
    g = G.builtin_geometry(name)
    for f in list(g.rooms) + list(g.occupancy):
        c = g.feature_center(f)
        assert g.feature_at(*c) == f
        assert g.location(*c) == (g.feature_kind(f), f)
        assert not g.in_free_space(*c)
    for r, p in g.region_points.items():
        assert g.in_free_space(*p)
        assert g.region_at(*p) == r
        assert g.feature_at(*p) is None
    for b in g.beams.values():
        for s in b.sides:
            r = b.side_rects[s]
            c = ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
            assert g.feature_at(*c) == s
            # just outside the side, in front of it: hit only with a tolerance
            nx, ny = b.normals[s]
            half = ((r[2] - r[0]) * abs(nx) + (r[3] - r[1]) * abs(ny)) / 2
            out = (c[0] + nx * (half + 2), c[1] + ny * (half + 2))
            assert g.feature_at(*out) is None
            assert g.feature_at(*out, beam_tolerance=4) == s
    # walls and the frame
    w = g.wall_segments[0]
    assert g.location((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) == ("wall", None)
    assert g.location(g.bbox[0], g.bbox[1]) == ("wall", None)
    assert g.location(-1000, -1000) == ("outside", None)


def test_boundaries_belong_to_obstacles():
    g = G.builtin_geometry("star_fig2")
    # wall x=210 has stroke 8: its edge x=214 is wall, just right of it is free (R2)
    assert g.location(214, 200) == ("wall", None)
    assert g.location(214.01, 200) == ("region", "R2")
    # room A's doorway at x=210 (y 79..126): the room rectangle ends at x=210
    assert g.location(209.99, 100) == ("room", "A")
    assert g.location(210, 100) == ("room", "A")
    assert g.location(210.01, 100) == ("region", "R2")


def test_segment_runs_and_free_segments():
    g = G.builtin_geometry("star_fig2")
    runs = g.segment_runs((100, 100), (250, 100))  # A -> doorway -> R2
    assert [(k, n) for _, _, k, n in runs] == [("room", "A"), ("region", "R2")]
    assert runs[0][0] == 0.0 and runs[-1][1] == 1.0
    runs = g.segment_runs((250, 250), (250, 350))  # R2 -> b1u -> band -> b1d -> R1
    assert [(k, n) for _, _, k, n in runs] == [
        ("region", "R2"), ("side", "b1u"), ("beam", "b1"), ("side", "b1d"), ("region", "R1")]
    assert g.segment_is_free((250, 200), (250, 260))
    assert not g.segment_is_free((250, 200), (250, 300))


# ---------------------------------------------------------------------- portals

@pytest.mark.parametrize("name", GEO_NAMES)
def test_stored_portals_are_reproducible_and_valid(name):
    g = G.builtin_geometry(name)
    computed = g.compute_portals()
    stored = g.stored_portals
    assert set(stored) == set(computed)
    for r in computed:
        assert set(stored[r]) == set(computed[r]) == set(g.derive_regions()[r])
        for f, pp in computed[r].items():
            for k in ("region_point", "feature_point"):
                assert stored[r][f][k] == pytest.approx(pp[k], abs=0.01), (r, f, k)
            assert g.region_at(*pp["region_point"]) == r
            kind = g.feature_kind(f)
            allowed = {("region", r), (kind, f)}
            if kind == "side":
                beam = g.beams[g.side_beam[f]]
                allowed.add(("beam", beam.name))
                assert g.location(*pp["feature_point"])[0] in ("side", "beam")
            else:
                assert g.location(*pp["feature_point"]) == (kind, f)
            for _, _, k, n in g.segment_runs(pp["region_point"], pp["feature_point"]):
                assert (k, n) in allowed, (r, f, k, n)


@pytest.mark.parametrize("name", GEO_NAMES)
def test_routes_stay_in_free_space(name):
    g = G.builtin_geometry(name)
    rng = random.Random(7)
    x0, y0, x1, y1 = g.bbox
    pts = {}
    while sum(len(v) for v in pts.values()) < 60:
        p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        r = g.region_at(*p)
        if r is not None:
            pts.setdefault(r, []).append(p)
    for r, ps in pts.items():
        for p, q in zip(ps, ps[1:]):
            route = g.route(p, q)
            assert route[0] == p and route[-1] == q
            for a, b in zip(route, route[1:]):
                assert g.segment_is_free(a, b), (r, a, b)
    with pytest.raises(GeometryError):
        g.route(g.region_point(sorted(g.region_points)[0]),
                g.region_point(sorted(g.region_points)[1]))


# ---------------------------------------------------------------------- paths

def _assert_drawable(g, pts, n_cross):
    walls = g.wall_rects()
    bands = [b.band for b in g.beams.values()]
    crossings = 0
    for a, b in zip(pts, pts[1:]):
        for w in walls:
            assert not G._segment_hits_rect(a, b, w), (a, b, w)
        if any(G._segment_hits_rect(a, b, band) for band in bands):
            crossings += 1
    # a crossing is drawn as region -> centre line -> region: two segments touch the band
    # (three if the drawing slides along the centre line); nothing else touches a band
    assert 2 * n_cross <= crossings <= 3 * n_cross


def _witness_cases():
    out = []
    for c in paper_cases("icra") + paper_cases("star"):
        walk = c["expected"].get("witness_walk_regions")
        if walk and c["map"] in GEO_NAMES:
            out.append((c["id"], c["map"], walk, c))
    return out


@pytest.mark.parametrize("cid,mapname,walk,case", _witness_cases(),
                         ids=[c[0] for c in _witness_cases()])
def test_paper_witness_walks_draw_and_replay(cid, mapname, walk, case):
    m = builtin_map(mapname)
    g = G.as_geometry(m)
    nodes = G.expand_path(m, walk)
    assert [n[1] for n in nodes if n[0] != "cross"] == walk
    pts = G.path_polyline(m, walk)
    _assert_drawable(g, pts, sum(1 for n in nodes if n[0] == "cross"))
    tr = G.trace_polyline(m, pts)
    assert tr.walk == walk
    if case["expected"]["consistent"] and case["mode"] == "single":
        assert tr.story == case["story"]
        assert tr.history == case["history"]


def test_path_string_fallback():
    m = builtin_map("star_fig2")
    nodes = G.expand_path(m, "A[b1u]C[o1][o2]B[b2r]AC")
    assert nodes == [
        ("room", "A"), ("region", "R2"), ("cross", "b1u", "b1d"), ("region", "R1"),
        ("room", "C"), ("region", "R1"), ("occupancy", "o1"), ("region", "R3"),
        ("occupancy", "o2"), ("region", "R4"), ("room", "B"), ("region", "R4"),
        ("cross", "b2r", "b2l"), ("region", "R2"), ("room", "A"), ("region", "R1"),
        ("room", "C")]
    tr = G.trace_polyline(m, G.path_polyline(m, "A[b1u]C[o1][o2]B[b2r]AC"))
    assert "".join(tr.story) == "ACBAC"
    assert tr.history == [["b1", "A"], ["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"],
                          ["b2", "A"]]
    # multi-agent notation and explicit sides
    assert ("occupancy", "o1") in G.expand_path(m, "A{o1}C")
    assert ("room", "C") in G.expand_path(m, "A(C)A")  # unreported visit notation
    assert G.expand_path(m, ["A", "b1u", "b1d", "C"])[2] == ("cross", "b1u", "b1d")
    with pytest.raises(GeometryError):
        G.expand_path(m, "AB")  # no region joins A and B
    with pytest.raises(GeometryError):
        G.expand_path(m, "A[x9]C")


def test_engine_witness_paths_draw_and_replay():
    m = builtin_map("star_fig2")
    r = validate(m, "ACBAC", "b1 o1 o1 o2 o2 b2")
    assert r.consistent
    pts = G.path_polyline(m, r)            # a Result
    assert pts == G.path_polyline(m, r.path)  # a list of Step
    assert pts == G.path_polyline(m, [s.to_dict() for s in r.path])
    tr = G.trace_polyline(m, pts)
    assert "".join(tr.story) == "ACBAC"
    assert tr.history == [["b1", "A"], ["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"],
                          ["b2", "A"]]


# ---------------------------------------------------------------------- errors, misc

def test_malformed_geometry_is_rejected():
    base = G.builtin_geometry("icra_fig2").raw
    d = copy.deepcopy(base)
    d["walls"]["segments"].append([0, 0, 10, 10])
    with pytest.raises(GeometryError):
        Geometry.from_dict(d)
    d = copy.deepcopy(base)
    d["beams"]["b1"]["sides"] = {"b11": "-x", "b12": "+x"}  # b1 is horizontal
    with pytest.raises(GeometryError):
        Geometry.from_dict(d)
    d = copy.deepcopy(base)
    d["rooms"]["o1"] = [1, 1, 5, 5]  # name clash with the occupancy sensor
    with pytest.raises(GeometryError):
        Geometry.from_dict(d)
    with pytest.raises(GeometryError):
        G.as_geometry(builtin_map("star_fig1"))  # no drawing for STAR Fig. 1
    with pytest.raises(KeyError):
        G.builtin_geometry("nope")


def test_check_geometry_reports_differences():
    m = builtin_map("star_fig2", geometry=False)
    d = copy.deepcopy(G.builtin_geometry("star_fig2").raw)
    d["walls"]["segments"].remove([210, 130, 210, 320])  # R1 | R2 wall gone: they merge
    problems = G.check_geometry(Geometry.from_dict(d), m)
    assert any("region points R1 and R2 lie in the same component" in p for p in problems)
    assert any(p.startswith("region R") for p in problems)


def test_json_files_are_valid_and_portable():
    for n in GEO_NAMES:
        path = os.path.join(G._DATA_DIR, n + ".json")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        json.loads(text)
        assert "/home/" not in text and "/tmp/" not in text
