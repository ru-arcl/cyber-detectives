"""Guard: malformed inputs never crash the port with a Python error.

The original Java code dereferences values that can be null on odd input (unknown names,
sensor sides missing from the game, edge-id collisions on maps with 65,536+ vertices, a
corrupted virtual start vertex, ...). ``compat.original`` must then raise the Java exception
(:class:`~cyber_detectives.compat.original.JavaException` with the Java frames), never a
Python ``AttributeError``/``TypeError``/``KeyError``/``IndexError``.

- ``test_fuzz_semantically_malformed_cases``: random cases the reference harness accepts
  (strings where it wants strings, sensors the map declares), but full of nulls, unknown and
  reserved names, sensor sides as story rooms, beam "D" recordings, a map named
  ``star_fig2`` whose sensors the built-in game lacks, collision maps, extreme clicks.
  ``run_harness_case`` must return a JSON-serialisable record (Java exceptions are recorded
  in it) and ``validate_compat`` may raise only ``JavaException``. These cases were checked
  against the live original with ``tools/reference/differential.py --cases`` when written.
- ``test_fuzz_structurally_malformed_cases``: the same cases with fields replaced by values
  of the wrong JSON type. The harness rejects those as malformed (``HarnessError``); the
  port must do the same (a ``ValueError``), or run the case if it is still well-formed.

Sizes scale with ``CD_TEST_SCALE``.
"""

from __future__ import annotations

import collections
import json
import os
import random

import pytest

from cyber_detectives.compat import original as O
from cyber_detectives.compat.original import HarnessError, JavaException, run_harness_case, validate_compat

SCALE = max(1, int(os.environ.get("CD_TEST_SCALE", "1") or 1))
SEED = 20261006

ALGO_OPS = ["validateAgentStory", "validateAgentStoryMulti", "getAgentStory", "getAgentStoryStatuses"]
OTHER_OPS = ["getSubGraph", "getSubGraphMulti", "getReachableSubgraph", "game_dump", "update_starting_vertex"]
BASIC_NAMES = ["SV", "A", "B", "C", "b1u", "b1d", "b2l", "b2r", "o1", "o2"]
BIG = [16777217, -16777219, 33554435, 2147483647, -2147483648, 4294967396, 9223372036854775807,
       -9223372036854775808, 1e10, -1e10, 1.5e300, -0.5, 99.99, 0, 60, 40]


# ---------------------------------------------------------------------------- generators


def _random_map(rng, name):
    """A small map the generic builder accepts: random edges, self-loops and duplicates
    included."""
    rooms = ["A", "B", "C", "D"][:rng.randint(1, 4)]
    beams = {b: [b + "u", b + "d"] for b in ["b1", "b2"][:rng.randint(0, 2)]}
    occ = ["o1", "o2", "o3"][:rng.randint(0, 3)]
    allv = rooms + [s for sd in beams.values() for s in sd] + occ
    edges = [[rng.choice(allv), rng.choice(allv)] for _ in range(rng.randint(0, 2 * len(allv)))]
    order = list(allv)
    rng.shuffle(order)
    m = {"name": name, "rooms": rooms, "beams": beams, "occupancy": occ, "edges": edges}
    if rng.random() < 0.5:
        m["vertex_order"] = order
    return m


def _mismatched_star(rng, name):
    """A map called ``star_fig2`` (so the harness uses ``getBasicGame()``) whose sensors are
    not the built-in game's: beam sides resolve to null or to rooms/SV, occupancy sensors to
    rooms."""
    pool = BASIC_NAMES + ["X", "b9u", "b9d"]
    beams = {}
    for b in ["b1", "b2", "b9"][:rng.randint(1, 3)]:
        beams[b] = [rng.choice(pool), rng.choice(pool)]
    occ = rng.sample(["o1", "o2", "A", "C", "b1u"], rng.randint(0, 3))
    occ = [o for o in occ if o not in beams]
    return {"name": "star_fig2", "rooms": ["A", "B", "C"], "beams": beams, "occupancy": occ,
            "edges": [], "_alias": name}


def _collision_map(rng, name):
    """65,535 fillers between a core and later vertices with ids above 65535 (bug B7a)."""
    core = ["A", "B", "C", "b1u", "b1d"]
    tail = ["Z"] + (["o1"] if rng.random() < 0.5 else [])
    allv = core + tail
    edges = [["A", "B"], ["B", "b1d"], ["A", "b1u"]]
    edges += [[rng.choice(allv), rng.choice(allv)] for _ in range(rng.randint(0, 4))]
    return {"name": name, "rooms": ["A", "B", "C", "Z"], "beams": {"b1": ["b1u", "b1d"]},
            "occupancy": [t for t in tail if t.startswith("o")], "filler_occupancy": 65535,
            "vertex_order": core + ["..."] + tail, "edges": edges}


def _vertex_names(m):
    return (list(m["rooms"]) + [s for sd in m["beams"].values() for s in sd] + list(m["occupancy"])
            + ["SV", "X", ""])


def _names(rng, pool, lo, hi, p_null=0.0):
    out = [rng.choice(pool) for _ in range(rng.randint(lo, hi))]
    return [None if rng.random() < p_null else n for n in out]


def _history(rng, m, hi=6):
    sensors = list(m["beams"]) + list(m["occupancy"])
    if not sensors:
        return []
    return [[rng.choice(sensors), rng.choice("AAD")] for _ in range(rng.randint(0, hi))]


def _applet_steps(rng):
    alphabet = "ABCSVXabo12,; é\U0001f600"
    steps = []
    for _ in range(rng.randint(1, 6)):
        r = rng.random()
        if r < 0.45:
            run = {}
            if rng.random() < 0.8:
                run["story"] = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6)))
            if rng.random() < 0.8:
                run["sensors"] = ",".join(rng.choice(["o1", "o2", "b1", "b2", "x", "", " b1"])
                                          for _ in range(rng.randint(0, 6)))
            if rng.random() < 0.5:
                run["mode"] = rng.choice(["single", "multi"])
            steps.append({"run": run})
        elif r < 0.6:
            steps.append({"reset": True})
        else:
            steps.append({"click": [rng.choice(BIG + [rng.randint(-20, 420)]),
                                    rng.choice(BIG + [rng.randint(-20, 320)])]})
    return steps


def gen_cases(rng, n, n_collision):
    """Harness-valid cases (and their maps); ``n_collision`` of them on collision maps."""
    maps, cases = {}, []
    for i in range(n):
        cid = "f%04d" % i
        r = rng.random()
        if i < n_collision:
            m = _collision_map(rng, "col%d" % i)
        elif r < 0.12:
            op = rng.choice(["applet", "applet", "builder_check", "algorithms_test"])
            c = {"id": cid, "op": op}
            if op == "applet":
                c["steps"] = _applet_steps(rng)
            elif op == "builder_check":
                c["map"] = _random_map(rng, "bc%d" % i)
            else:
                c["name"] = rng.choice(["testGraphRoutines", "testStoryHistory", "testSingleAgent",
                                        "testMultiAgent", "main"])
            cases.append(c)
            continue
        elif r < 0.2:
            c = {"id": cid, "op": rng.choice(ALGO_OPS + ["game_dump"]),
                 "game": rng.choice(["SingleInfeasible", "SingleFeasible", "MultiFeasible", "MultiInfeasible",
                                     "Basic"])}
            if rng.random() < 0.5:
                c["start"] = rng.choice(["story", "fixed"])
            cases.append(c)
            continue
        elif r < 0.45:
            m = _mismatched_star(rng, "star%d" % i)
        else:
            m = _random_map(rng, "m%d" % i)
        key = m.pop("_alias", m["name"])
        maps[key] = m
        pool = _vertex_names(m)
        op = rng.choice(ALGO_OPS * 3 + OTHER_OPS) if i >= n_collision else rng.choice(ALGO_OPS)
        c = {"id": cid, "op": op, "map": key if m["name"] != "star_fig2" else m,
             "story": _names(rng, pool, 0, 6), "history": _history(rng, m)}
        if i < n_collision:  # A->Z is an edge by collision; then a pending b1 recording or story test
            c["story"] = [["A", "B", "A", "Z", "B"], ["A", "Z", "A", "B", "C"]][i % 2]
            c["history"] = [["b1", "A"]] if i % 2 == 0 else _history(rng, m, 2)
        if rng.random() < 0.3:
            c["start"] = rng.choice(["story", "fixed"])
        if op in ("getSubGraph", "getSubGraphMulti", "getReachableSubgraph"):
            c["s"] = rng.choice(pool)
            c["goals"] = _names(rng, pool, 0, 4, 0.15)
            if op == "getReachableSubgraph":
                c["vp_set"] = _names(rng, pool, 0, 6, 0.1)
            else:
                if rng.random() < 0.5:
                    c["story_vertices"] = _names(rng, pool, 0, 5, 0.1)
                if op == "getSubGraphMulti":
                    c["occupancy_active"] = _names(rng, pool, 0, 3, 0.2)
        elif op == "update_starting_vertex":
            c["vertex"] = rng.choice(pool + [None])
        cases.append(c)
    return maps, cases


def _batch():
    rng = random.Random(SEED)
    return gen_cases(rng, 400 * SCALE, 2 * SCALE)


# ---------------------------------------------------------------------------- tests


def test_fuzz_semantically_malformed_cases():
    maps, cases = _batch()
    tops = collections.Counter()
    for c in cases:
        try:
            res = run_harness_case(c, maps)
        except Exception as e:  # noqa: BLE001 - any escape is a port bug
            pytest.fail("%s escaped run_harness_case for %s" % (type(e).__name__, json.dumps(c)[:2000]))
        json.dumps(res)
        excs = [res.get("exception"), res.get("call_exception"), res.get("graph_dump_exception")]
        if c.get("op") == "applet":
            excs += [st.get("exception") for st in res["return"] or []]
        for e in excs:
            if e:
                tops[e["class"].rsplit(".", 1)[1] + " " + e["top_frame"].split("(")[1].rstrip(")")] += 1
        # the applet pipeline used by validate(..., compat="original")
        if c.get("op") in ALGO_OPS[:2] and isinstance(c.get("map"), str):
            m = maps[c["map"]]
            try:
                validate_compat(m, c["story"], c["history"], "single" if c["op"] == ALGO_OPS[0] else "multi")
            except JavaException:
                pass
    # the batch reaches the null dereferences the audit added (see the module docstring)
    for site in ("NullPointerException Algorithms.java:201", "NullPointerException Algorithms.java:207",
                 "NullPointerException Algorithms.java:131", "NullPointerException Edge.java:17",
                 "NullPointerException Algorithms.java:424", "NullPointerException Algorithms.java:350",
                 "ArrayIndexOutOfBoundsException Algorithms.java:365",
                 "NullPointerException DetectiveGame.java:30"):
        assert tops[site] > 0, (site, sorted(tops.items()))


_JUNK = [None, 0, 1.5, True, "", "zz", [], [None], [1, 2], {}, {"a": 1}, [[1]], [["b1"]], "A"]


def _mutations(rng, c):
    keys = [k for k in c if k not in ("id",)] + ["story", "history", "map", "s", "goals", "steps"]
    for _ in range(3):
        d = json.loads(json.dumps(c))
        k = rng.choice(keys)
        d[k] = rng.choice(_JUNK)
        yield d
    d = json.loads(json.dumps(c))  # one level deeper: a list element or a map field
    for k in ("story", "history", "goals", "steps"):
        if isinstance(d.get(k), list) and d[k]:
            d[k][rng.randrange(len(d[k]))] = rng.choice(_JUNK)
            yield d
            break
    if isinstance(d.get("map"), dict):
        d = json.loads(json.dumps(c))
        d["map"][rng.choice(["rooms", "beams", "occupancy", "edges", "vertex_order", "filler_occupancy"])] = \
            rng.choice(_JUNK)
        yield d


def test_fuzz_structurally_malformed_cases():
    maps, cases = _batch()
    rng = random.Random(SEED + 1)
    small = {k: v for k, v in maps.items() if "filler_occupancy" not in v}
    n = 0
    for c in cases:
        if isinstance(c.get("map"), str) and c["map"] not in small:
            continue
        for d in _mutations(rng, c):
            n += 1
            try:
                res = run_harness_case(d, small)
            except HarnessError:
                continue
            except Exception as e:  # noqa: BLE001
                pytest.fail("%s escaped run_harness_case for %s" % (type(e).__name__, json.dumps(d)[:2000]))
            json.dumps(res)
    assert n > 1000 * SCALE


@pytest.mark.parametrize("junk", _JUNK + [{"rooms": ["A"], "edges": [["A"]]},
                                          {"rooms": ["A"], "filler_occupancy": -1},
                                          {"rooms": ["A"], "beams": {"b": ["x"]}}])
def test_validate_compat_raises_only_value_error_or_java_exceptions(junk):
    """Malformed maps, stories or histories give ``ValueError`` (or the original's crash)."""
    base = {"name": "m", "rooms": ["A", "B"], "beams": {"b1": ["u", "d"]}, "edges": [["A", "u"], ["d", "B"]]}
    calls = [(junk, ["A"], []), (dict(base, edges=junk), ["A"], []), (dict(base, beams=junk), ["A"], []),
             (dict(base, rooms=junk), ["A"], []), (base, ["A", junk], [["b1", "A"]]),
             (base, ["A", "B"], [[junk, "A"]]), (base, ["A", "B"], [["b1", junk]]), (base, ["A"], junk),
             (base, ["A"], [junk])]
    for m, story, hist in calls:
        for agents in ("single", "multi"):
            try:
                validate_compat(m, story, hist, agents)
            except (ValueError, JavaException):
                pass
            except Exception as e:  # noqa: BLE001
                pytest.fail("%s from validate_compat(%r, %r, %r)" % (type(e).__name__, m, story, hist))


def test_expand_map_filler_placement():
    m = {"name": "x", "rooms": ["A", "Z"], "occupancy": ["o1"], "filler_occupancy": 3,
         "vertex_order": ["A", "o1", "...", "Z"], "edges": [["A", "Z"]]}
    e = O.expand_map(m)
    assert e["occupancy"] == ["o1", "f1", "f2", "f3"]
    assert e["vertex_order"] == ["A", "o1", "f1", "f2", "f3", "Z"]
    assert "filler_occupancy" not in e and O.expand_map(e) is e
    g = O.build_game(m).graph
    assert g.vertex_name_map["Z"].id == 2 + 6  # SV=2, A, o1, f1..f3, Z
    no_order = O.expand_map({"rooms": ["A"], "filler_occupancy": 2})
    assert no_order["occupancy"] == ["f1", "f2"] and "vertex_order" not in no_order
    assert O.expand_map({"rooms": ["A"], "vertex_order": ["A", "..."], "filler_occupancy": 1})["vertex_order"] \
        == ["A", "f1"]
    for bad in (-1, 1.0, True, None, "3"):
        with pytest.raises(ValueError):
            O.expand_map({"rooms": ["A"], "filler_occupancy": bad})
    with pytest.raises(ValueError):
        O.expand_map({"rooms": ["A"], "vertex_order": ["...", "A", "..."], "filler_occupancy": 1})
