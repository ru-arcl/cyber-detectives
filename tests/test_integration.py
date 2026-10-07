"""Integration: the public API, ``Map.to_dict()`` -> compat builder, and
``validate(..., compat="original")`` end to end on the golden fixtures.

``tests/test_compat_golden.py`` pins ``compat.original.validate_compat`` against the golden map
dicts directly.  Here every case goes through the user-facing path instead: a
:class:`~cyber_detectives.maps.Map` object (builtin, or ``Map.from_dict`` of the golden map),
``validate(..., compat="original")``, ``Map.to_dict()``, and the CLI.  This checks that the
default package's own map representation keeps everything the original's output depends on
(vertex creation order, edge order, hence ids, neighbour-set and edge-map iteration order).
"""

from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional

import pytest

import cyber_detectives as cd
from cyber_detectives import Map, builtin_map, builtin_map_names, validate
from cyber_detectives.cli import main
from cyber_detectives.compat import original as O

from conftest import load_fixture

GOLDEN_SETS = ["builtin", "star_random_single", "star_random_multi", "random_maps", "applet",
               "paper_cases"]


# ---------------------------------------------------------------------- public API


DESIGN_API = [
    "Map", "builtin_map", "load_map", "parse_story", "parse_history", "Event", "validate",
    "validate_intervals", "shortest_superstory", "closest_story", "replay", "Result", "Step",
    "MapError", "InputError", "InvalidPath", "builtin_map_names",
]


def test_public_api_matches_design():
    for name in DESIGN_API:
        assert name in cd.__all__, name
        assert getattr(cd, name) is not None
    for name in ("MapError", "InputError", "InvalidPath"):
        assert issubclass(getattr(cd, name), ValueError)
    ns: Dict[str, Any] = {}
    exec("from cyber_detectives import *", ns)
    assert set(cd.__all__) <= set(ns)


def test_submodules_are_reachable_lazily():
    assert cd.subgraphs.__name__ == "cyber_detectives.subgraphs"
    assert cd.compat.__name__ == "cyber_detectives.compat"
    assert cd.compat.original.validate_compat is O.validate_compat
    assert {"compat", "subgraphs", "geometry"} <= set(dir(cd))
    with pytest.raises(AttributeError):
        cd.no_such_module  # noqa: B018


# ---------------------------------------------------------------------- Map.to_dict -> builder


def _raw(game: O.DetectiveGame) -> Dict[str, Any]:
    """Everything of a built game the original's output can depend on, in iteration order
    (not sorted, unlike the harness's ``enc_game``)."""
    g = game.graph
    return {
        "vertex_map": [(v.name, v.id, [n.name for n in v.neighbors.to_array()],
                        v.asso_vertex.name if v.asso_vertex is not None else None)
                       for v in g.vertex_map.values()],
        "edge_map": [(e.id, e.vertices[0].name, e.vertices[1].name) for e in g.edge_map.values()],
        "vertex_name_map": [(k, v.id) for k, v in g.vertex_name_map.items()],
        "vertex_ids": sorted(g.vertex_ids),
        "edge_ids": sorted(g.edge_ids),
        "room_ids": game.room_ids.to_array(),
        "beam_ids": game.beam_ids.to_array(),
        "occu_ids": game.occu_ids.to_array(),
        "story_vertices": [(v.name, v.id) for v in game.story_vertices],
        "sensor_vertices": [(v.name, v.id) for v in game.sensor_vertices],
    }


def test_builtin_star_fig2_reproduces_get_basic_game():
    """``builtin_map("star_fig2").to_dict()`` through the compat builder is exactly
    ``DetectiveGame.getBasicGame()``: ids, neighbour order, edge orientation and order."""
    built = _raw(O.build_game(builtin_map("star_fig2").to_dict()))
    basic = _raw(O.DetectiveGame.get_basic_game())
    assert built == basic
    # spot checks against DetectiveGame.java (ids 2..11, SV joined to A)
    ids = dict(basic["vertex_name_map"])
    assert ids == {"SV": 2, "A": 3, "B": 4, "C": 5, "b1u": 6, "b1d": 7, "b2l": 8, "b2r": 9,
                   "o1": 10, "o2": 11}
    assert len(basic["edge_map"]) == 16  # STAR Fig. 3(a)'s 15 edges + SV-A


def _golden_maps() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for s in GOLDEN_SETS:
        for m in load_fixture("golden", s + ".json")["maps"]:
            out.setdefault(m["name"], m)
    return out


@pytest.mark.parametrize("name", sorted(_golden_maps()))
def test_to_dict_round_trip_feeds_the_builder_unchanged(name):
    """``Map.from_dict(d).to_dict()`` builds the same game as the golden map dict ``d``, and so
    does the builtin map of the same name."""
    d = _golden_maps()[name]
    want = _raw(O.build_game(d))
    assert _raw(O.build_game(Map.from_dict(d).to_dict())) == want
    if name in builtin_map_names():
        assert _raw(O.build_game(builtin_map(name).to_dict())) == want


def test_every_builtin_map_with_rooms_builds():
    for name in builtin_map_names():
        m = builtin_map(name, geometry=False)
        if not m.rooms:  # star_fig1 is a workspace figure without rooms
            with pytest.raises(ValueError):
                O.build_game(m.to_dict())
            continue
        g = O.build_game(m.to_dict()).graph
        assert sorted(g.vertex_name_map) == sorted(["SV"] + list(m.features))


# ---------------------------------------------------------------------- validate(compat=...)


def _map_object(d: Dict[str, Any], cache: Dict[str, Map]) -> Map:
    """The user-facing Map for a golden map dict: the builtin map when it has that name, else
    ``Map.from_dict``."""
    if d["name"] not in cache:
        if d["name"] in builtin_map_names():
            cache[d["name"]] = builtin_map(d["name"], geometry=False)
        else:
            cache[d["name"]] = Map.from_dict(d)
    return cache[d["name"]]


def _golden_validate_cases():
    """(set, case, path_case) for every map-based validate case with the applet start;
    ``path_case`` is the matching getAgentStory case (same set, same input), if any."""
    for s in GOLDEN_SETS:
        data = load_fixture("golden", s + ".json")
        maps = {m["name"]: m for m in data["maps"]}
        paths = {}
        for c in data["cases"]:
            if c["op"] == "getAgentStory" and "map" in c and c.get("start", "story") == "story":
                paths[(c["map"], json.dumps(c["story"]), json.dumps(c["history"]))] = c
        for c in data["cases"]:
            if c["op"] not in ("validateAgentStory", "validateAgentStoryMulti"):
                continue
            if "map" not in c or c.get("start", "story") != "story":
                continue
            key = (c["map"], json.dumps(c["story"]), json.dumps(c["history"]))
            yield s, maps[c["map"]], c, paths.get(key)


def test_validate_compat_original_end_to_end_on_golden():
    """``validate(Map, ..., compat="original")`` reproduces every recorded verdict, path and
    crash of the map-based validate cases (applet start)."""
    cache: Dict[str, Map] = {}
    n = n_paths = n_crash = 0
    for s, d, c, pc in _golden_validate_cases():
        m = _map_object(d, cache)
        agents = "multi" if c["op"] == "validateAgentStoryMulti" else "single"
        exp = c["expected"]
        where = "%s:%s" % (s, c["id"])
        try:
            r = validate(m, list(c["story"]), c["history"], agents=agents, compat="original")
        except O.JavaException as e:
            got = e.describe()
            if exp["exception"] is None:
                # validate returned true; the crash must be getAgentStory's own
                assert pc is not None and pc["expected"]["exception"] is not None, where
                want = pc["expected"]["exception"]
            else:
                want = exp["exception"]
            assert (got["class"], got["origin_frame"]) == (want["class"], want["origin_frame"]), where
            n_crash += 1
            continue
        assert exp["exception"] is None and r.consistent == exp["consistent"], where
        assert r.compat == "original" and r.path is None, where
        if agents == "single" and r.consistent and pc is not None:
            assert r.path_string() == pc["expected"]["return"], where
            n_paths += 1
        elif agents == "multi" or not r.consistent:
            assert r.path_string() is None, where
        n += 1
    assert n > 900 and n_paths > 300 and n_crash >= 5, (n, n_paths, n_crash)


def test_validate_compat_original_on_paper_examples():
    """STAR eq. (1) with the original's feasible history, and eq. (2) (infeasible)."""
    m = builtin_map("star_fig2")
    r = validate(m, "ACBAC", "b1 o1 o1 o2 o2 b2", compat="original")
    assert r.consistent and r.path_string() == "A[b1u]C[o1][o2]B[b2r]AC"
    assert r.to_dict()["path_string"] == "A[b1u]C[o1][o2]B[b2r]AC"
    assert not validate(m, "ACBAC", "b1 o1 o1 b2 o2 o2", compat="original")
    # STAR eq. (3), multi-agent: true in both modes
    assert validate(m, "ACBAC", "b1 o1+ o2+ b2 o2- o1-", agents="multi", compat="original")
    assert validate(m, "ACBAC", "b1 o1+ o2+ b2 o2- o1-", agents="multi")


def test_validate_compat_original_crash_propagates():
    """An unknown room reaches the original as null and crashes there (Algorithms.java:207)."""
    m = builtin_map("star_fig2")
    with pytest.raises(O.NullPointerException) as ei:
        validate(m, ["A", "X"], "b1", compat="original")
    assert ei.value.origin_frame == "projects.cyberDetective.Algorithms.validateAgentStory(Algorithms.java:207)"


def _run(*argv: str):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_compat_original_real_port():
    code, out, _ = _run("validate", "--map", "star_fig2", "--story", "ACBAC", "--history",
                        "b1 o1 o1 o2 o2 b2", "--compat", "original")
    assert code == 0 and out == "consistent\npath: A[b1u]C[o1][o2]B[b2r]AC\n"
    # B8: the original accepts a story used up before the last recording
    code, out, _ = _run("validate", "--map", "star_fig2", "--story", "A", "--history", "b1",
                        "--compat", "original", "--json")
    d = json.loads(out)
    assert code == 0 and d["consistent"] is True and d["path_string"] == "A[b1u]"
    code, out, _ = _run("validate", "--map", "star_fig2", "--story", "A", "--history", "b1")
    assert code == 1 and out.startswith("inconsistent")
    # B1: the original's multi-agent false negative
    code, out, _ = _run("validate", "--map", "star_fig2", "--story", "BC", "--history",
                        "o1 b2 o1", "--multi", "--compat", "original")
    assert code == 1
    code, out, _ = _run("validate", "--map", "star_fig2", "--story", "BC", "--history",
                        "o1 b2 o1", "--multi")
    assert code == 0
    # a crash of the original is exit status 4
    code, out, _ = _run("validate", "--map", "star_fig2", "--story", "A X", "--history", "b1",
                        "--compat", "original", "--json")
    assert code == 4
    assert json.loads(out)["exception"]["class"] == "NullPointerException"
