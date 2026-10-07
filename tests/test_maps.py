"""Maps: builtin data vs paper fixtures, G derivation, from_edges, validation errors."""

from __future__ import annotations

import json
import os
import random
import time

import pytest

from conftest import load_fixture
from cyber_detectives import Map, MapError, builtin_map, builtin_map_names, load_map
from cyber_detectives.maps import _maximal_cliques
from oracles import brute as B

BUILTINS = ["icra_fig1", "icra_fig2", "star_fig1", "star_fig2"]


def _norm_edges(edges):
    return sorted(tuple(sorted(e)) for e in edges)


def _paper_maps():
    out = {}
    for f in ("star", "icra"):
        for m in load_fixture("paper", f + ".json")["maps"]:
            out[m["name"]] = m
    return out


def test_builtin_names():
    assert builtin_map_names() == BUILTINS


@pytest.mark.parametrize("name", BUILTINS)
def test_builtin_matches_paper_fixture(name):
    m = builtin_map(name)
    fx = _paper_maps()[name]
    assert list(m.rooms) == fx["rooms"]
    assert {b: list(s) for b, s in m.beams.items()} == fx["beams"]
    assert list(m.occupancy) == fx["occupancy"]
    assert m.edges() == _norm_edges(fx["edges"])
    assert m.provenance and "source" in m.provenance


def test_builtin_regions_match_paper_regions():
    star = _paper_maps()["star_fig2"]["expected"]
    m = builtin_map("star_fig2")
    assert {r: sorted(v) for r, v in m.regions.items()} == \
        {r: sorted(v) for r, v in star["free_components_fig2"].items()}
    for side, reg in star["beam_side_component"].items():
        assert m.side_region(side) == reg
    assert m.region_graph_edges() == _norm_edges(star["region_graph_fig3b_edges"])
    icra = load_fixture("paper", "icra.json")["figures"]
    m2 = builtin_map("icra_fig2")
    assert {r: sorted(v) for r, v in m2.regions.items()} == \
        {r: sorted(v) for r, v in icra["fig3_free_components"]["icra_fig2"].items()}
    assert len(m2.edges()) == 38
    assert len(m.edges()) == 15


def test_star_fig2_keeps_original_order():
    """Edge order and vertex order of getBasicGame, for compat="original"."""
    gold = load_fixture("golden", "star_random_single.json")["maps"][0]
    d = builtin_map("star_fig2").to_dict()
    assert d["edges"] == gold["edges"]
    assert d["vertex_order"] == gold["vertex_order"]
    assert d["beams"] == gold["beams"]


def test_adjacency_and_kinds():
    m = builtin_map("star_fig2")
    adj = m.adjacency()
    assert adj["A"] == ["C", "b1u", "b1d", "b2l", "o1"]
    assert adj["B"] == ["b2r", "o2"]
    assert m.kind("A") == "room" and m.kind("b1") == "beam" and m.kind("b1u") == "side"
    assert m.kind("o1") == "occupancy" and m.kind("R3") == "region"
    with pytest.raises(KeyError):
        m.kind("zz")
    assert m.other_side("b2r") == "b2l"
    assert m.regions_of["o1"] == ("R1", "R2", "R3")
    assert m.regions_of["A"] == ("R1", "R2")


@pytest.mark.parametrize("name", ["icra_fig1", "star_fig1", "star_fig2"])
def test_from_edges_recovers_regions(name):
    """Maximal cliques of G equal the paper regions for these builtin maps."""
    m = builtin_map(name)
    m2 = Map.from_edges(name, m.rooms, m.beams, m.occupancy, m.edges())
    assert sorted(sorted(v) for v in m2.regions.values()) == \
        sorted(sorted(v) for v in m.regions.values())


def test_from_edges_spurious_clique_icra_fig2():
    """ICRA Fig. 2: A-B (R1), A-o1 (R3), B-o1 (R2) form the spurious clique {A, B, o1}, which
    swallows R1 = {A, B}.  Every other region is recovered exactly (the documented caveat;
    verdict equivalence is tested in test_engine.py)."""
    m = builtin_map("icra_fig2")
    m2 = Map.from_edges("x", m.rooms, m.beams, m.occupancy, m.edges())
    got = sorted(sorted(v) for v in m2.regions.values())
    want = sorted(sorted(v) for r, v in m.regions.items() if r != "R1")
    assert got == sorted(want + [["A", "B", "o1"]])
    assert m2.edges() == m.edges()


def test_from_edges_isolated_feature_gets_region():
    m = Map.from_edges("t", ["A", "B"], {"b": ["bl", "br"]}, [],
                       [["A", "bl"]])
    assert sorted(sorted(v) for v in m.regions.values()) == [["A", "bl"], ["B"], ["br"]]


def test_from_edges_caveat_beam_side_in_two_cliques():
    with pytest.raises(MapError, match="touches 2 regions"):
        Map.from_edges("t", ["A", "B"], {"b": ["bl", "br"]}, [],
                       [["A", "bl"], ["B", "bl"]])


def test_maximal_cliques_match_oracle_on_random_graphs():
    rng = random.Random(8)
    for _ in range(300):
        n = rng.randint(1, 12)
        vs = ["v%d" % i for i in range(n)]
        p = rng.choice([0.0, 0.1, 0.3, 0.6, 0.9])
        edges = [(a, b) for i, a in enumerate(vs) for b in vs[i + 1:] if rng.random() < p]
        adj = {v: set() for v in vs}
        for a, b in edges:
            adj[a].add(b)
            adj[b].add(a)
        got = _maximal_cliques(adj)
        assert len(got) == len({frozenset(c) for c in got})  # no clique reported twice
        assert {frozenset(c) for c in got} == set(B.maximal_cliques(vs, edges)), (vs, edges)


def test_from_dict_without_regions_is_fast_on_large_sparse_maps():
    """Deriving regions from G stays near-linear on sparse maps (it was quadratic: 54 s for
    65,537 edgeless features, ~20 s for this size).  The bounds are generous: typical times
    are 10x lower."""
    n = 40000
    occ = ["o%d" % i for i in range(n)]
    d = {"name": "big", "rooms": ["A"], "beams": {}, "occupancy": occ, "edges": []}
    t = time.perf_counter()
    m = Map.from_dict(d)
    assert time.perf_counter() - t < 5.0
    assert len(m.regions) == n + 1
    # a sparse chain of triangles and edges
    edges = [[occ[i], occ[i + 1]] for i in range(n - 1)]
    edges += [[occ[i], occ[i + 2]] for i in range(0, n - 2, 3)]
    d = {"name": "big", "rooms": ["A"], "beams": {}, "occupancy": occ, "edges": edges}
    t = time.perf_counter()
    m = Map.from_dict(d)
    assert time.perf_counter() - t < 5.0
    assert sorted(m.regions["R1"]) == ["A"] and m.regions["R2"] == ("o0", "o1", "o2")
    assert len(m.edges()) == len(edges)


def test_roundtrip_dict_and_file(tmp_path):
    for name in BUILTINS:
        m = builtin_map(name)
        d = m.to_dict()
        m2 = Map.from_dict(json.loads(json.dumps(d)))
        assert m2 == m
        assert m2.to_dict() == d
        p = tmp_path / (name + ".json")
        p.write_text(json.dumps(d))
        assert load_map(str(p)) == m


def test_from_dict_without_regions_uses_cliques():
    d = dict(_paper_maps()["star_fig2"])
    m = Map.from_dict(d)
    assert sorted(sorted(v) for v in m.regions.values()) == \
        sorted(sorted(v) for v in builtin_map("star_fig2").regions.values())


def test_from_dict_golden_random_maps():
    """Golden random maps carry unnamed region lists whose cliques equal their edges."""
    for d in load_fixture("golden", "random_maps.json")["maps"]:
        m = Map.from_dict(d)
        assert m.edges() == _norm_edges(d["edges"])
        assert [list(v) for v in m.regions.values()] == d["regions"]


def _base(**over):
    d = {"name": "t", "rooms": ["A", "B"], "beams": {"b": ["bl", "br"]},
         "occupancy": ["o"], "regions": {"R1": ["A", "bl", "o"], "R2": ["B", "br", "o"]}}
    d.update(over)
    return d


@pytest.mark.parametrize("over, msg", [
    ({"regions": {"R1": ["A", "bl", "o"], "R2": ["B", "br", "o", "bl"]}}, "touches 2 regions"),
    ({"regions": {"R1": ["A", "o"], "R2": ["B", "br", "o"]}}, "touches 0 regions"),
    ({"regions": {"R1": ["A", "bl", "zz"], "R2": ["B", "br"]}}, "unknown feature 'zz'"),
    ({"regions": {"R1": ["A", "bl", "b"], "R2": ["B", "br"]}}, "a beam"),
    ({"regions": {"R1": [], "R2": ["B", "br", "bl"]}}, "touches no feature"),
    ({"regions": {"R1": ["A", "A", "bl"], "R2": ["B", "br"]}}, "twice"),
    ({"rooms": ["A", "o"]}, "used twice"),
    ({"beams": {"b": ["bl"]}}, "exactly two sides"),
    ({"beams": {"b": ["bl", "bl"]}}, "same name"),
    ({"rooms": ["A", "B C"]}, "room name"),
    ({"regions": {"A": ["A", "bl"], "R2": ["B", "br"]}}, "used twice"),
    ({"edges": [["A", "bl"]]}, "does not match"),
    ({"vertex_order": ["A"]}, "permutation"),
])
def test_validation_errors(over, msg):
    with pytest.raises(MapError, match=msg):
        Map.from_dict(_base(**over))


@pytest.mark.parametrize("over, msg", [
    ({"beams": "b"}, "beams must be an object or a list of .name, sides. pairs, got 'b'"),
    ({"beams": 5}, "beams must be an object or a list"),
    ({"beams": [["b", ["bl", "br"], 1]]}, r"beams item 1 \(\['b', \['bl', 'br'\], 1\]\) is not a"),
    ({"beams": [["b"]]}, "beams item 1"),
    ({"beams": [[1, ["bl", "br"]]]}, "beam name 1 must be"),
    ({"beams": {"b": "lr"}}, "beam 'b' must have exactly two sides, got 'lr'"),
    ({"beams": [["b", 3]]}, "beam 'b' must have exactly two sides, got 3"),
    ({"regions": 7}, "regions must be an object or a list, got 7"),
    ({"regions": "AB"}, "regions must be an object or a list, got 'AB'"),
    ({"regions": {"R1": ["A", "bl", "o"], "R2": 7}}, "region 'R2': features must be a list"),
    ({"regions": {"R1": ["A", "bl", ["o"]], "R2": ["B", "br"]}}, r"unknown feature \['o'\]"),
    # not all items are [name, list] pairs: feature lists, so 'R1' is an unknown feature
    ({"regions": [["R1", ["A", "bl", "o"]], ["B", "br", "o"]]}, "unknown feature 'R1'"),
])
def test_container_errors(over, msg):
    with pytest.raises(MapError, match=msg):
        Map.from_dict(_base(**over))


def test_pair_forms_and_digit_names():
    pairs = _base(beams=[["b", ["bl", "br"]]],
                  regions=[["R2", ["B", "br", "o"]], ["R1", ["A", "bl", "o"]]])
    m = Map.from_dict(pairs)
    assert m.beams == {"b": ("bl", "br")} and list(m.regions) == ["R2", "R1"]
    # no all-digit names: the object form, in map order
    d = m.to_dict()
    assert d["beams"] == {"b": ["bl", "br"]}
    assert list(d["regions"].items()) == [("R2", ["B", "br", "o"]), ("R1", ["A", "bl", "o"])]
    assert Map.from_dict(d) == m and Map.from_dict(d).to_dict() == d
    # all-digit names (JS objects iterate them first): that member as [name, value] pairs
    g = Map("g", ["A", "B"], [["9", ["bu", "bd"]], ["b", ["cu", "cd"]], ["2", ["eu", "ed"]]],
            [], {"R": ["A", "bu", "cu", "eu"], "S": ["B", "bd", "cd", "ed"]})
    gd = g.to_dict()
    assert gd["beams"] == [["9", ["bu", "bd"]], ["b", ["cu", "cd"]], ["2", ["eu", "ed"]]]
    assert gd["regions"] == {"R": ["A", "bu", "cu", "eu"], "S": ["B", "bd", "cd", "ed"]}
    assert g.sensors == ("9", "b", "2")
    back = Map.from_dict(json.loads(json.dumps(gd)))
    assert back.to_dict() == gd and back.sensors == g.sensors
    r = Map("r", ["A", "B"], {"b": ["bu", "bd"]}, [], {"10": ["B", "bd"], "x": ["A", "bu"],
                                                       "007": ["A", "B"]})
    assert r.to_dict()["regions"] == [["10", ["B", "bd"]], ["x", ["A", "bu"]],
                                      ["007", ["A", "B"]]]
    assert r.to_dict()["beams"] == {"b": ["bu", "bd"]}
    # from_edges reads the pair form too
    e = Map.from_edges("e", ["A", "B"], [["7", ["bu", "bd"]]], [], [["A", "bu"], ["B", "bd"]])
    assert e.sensors == ("7",) and e.to_dict()["beams"] == [["7", ["bu", "bd"]]]
    # feature lists whose items happen to be pairs of names stay feature lists
    f = Map("f", ["A", "B"], {}, [], [["A", "B"]])
    assert f.regions == {"R1": ("A", "B")}


def test_js_special_names_are_ordinary():
    names = ["constructor", "__proto__", "toString", "hasOwnProperty", "valueOf", "length"]
    m = Map("p", names[:2], {names[2]: [names[3], names[4]]}, [names[5]],
            {"__lookupGetter__": [names[0], names[3], names[5]],
             "prototype": [names[1], names[4], names[5]]})
    d = m.to_dict()
    assert list(d["regions"]) == ["__lookupGetter__", "prototype"]
    assert Map.from_dict(json.loads(json.dumps(d))) == m


def test_missing_keys():
    with pytest.raises(MapError, match="missing the key 'rooms'"):
        Map.from_dict({"name": "t"})
    d = _base()
    del d["regions"]
    with pytest.raises(MapError, match="neither"):
        Map.from_dict(d)


def test_unknown_builtin():
    with pytest.raises(KeyError, match="unknown builtin map"):
        builtin_map("nope")


def test_load_map_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{")
    with pytest.raises(MapError, match="not valid JSON"):
        load_map(str(p))


def test_builtin_data_has_no_local_paths():
    import cyber_detectives
    data = os.path.join(os.path.dirname(cyber_detectives.__file__), "data")
    for n in os.listdir(data):
        if n.endswith(".json"):
            with open(os.path.join(data, n), encoding="utf-8") as f:
                text = f.read()
            assert "/home/" not in text and "/tmp/" not in text
