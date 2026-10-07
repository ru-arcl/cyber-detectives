"""compat="original" reproduces every golden fixture exactly.

For every case in tests/fixtures/golden/<set>.json, the harness emulation
(:func:`cyber_detectives.compat.original.run_harness_case`) must reproduce the recorded
``consistent``, ``return``, ``stdout``, ``exception``, ``aliases``, ``graph_dump``,
``graph_dump_exception`` and ``call_exception`` -- byte for byte, including the fields that
are ``order_dependent`` (their canonical, ``-XX:hashCode=2`` value is what is recorded).
Exceptions are compared on class and every stack frame; their message too when the golden
file was recorded on JDK 8 (messages are JDK-specific, see tests/fixtures/golden/README.md).
"""

from __future__ import annotations

import collections
import glob
import json
import os

import pytest

from cyber_detectives.compat.original import run_harness_case, validate_compat, JavaException

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "fixtures", "golden")
FIELDS = ["consistent", "return", "stdout", "exception", "aliases", "graph_dump", "graph_dump_exception",
          "call_exception"]
EXC_KEYS = {"class", "message", "top_frame", "origin_frame", "trace"}

_cache = {}


def _sets():
    out = []
    for p in sorted(glob.glob(os.path.join(GOLDEN, "*.json"))):
        name = os.path.basename(p)[:-5]
        if name not in ("summary", "javahash"):
            out.append(name)
    return out


def _load(name):
    if name not in _cache:
        with open(os.path.join(GOLDEN, name + ".json"), encoding="ascii") as f:
            _cache[name] = json.load(f)
    return _cache[name]


def _strip_messages(x):
    """Drop 'message' from every exception record (for golden files not from JDK 8)."""
    if isinstance(x, dict):
        if EXC_KEYS <= set(x):
            return {k: v for k, v in x.items() if k != "message"}
        return {k: _strip_messages(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_strip_messages(v) for v in x]
    return x


def _actual(res):
    got = {k: res[k] for k in FIELDS if k in res}
    if res["op"] in ("validateAgentStory", "validateAgentStoryMulti") and res["exception"] is None:
        got["consistent"] = res["return"]
    return got


def _compare(name, force_generic=False, only_star=False):
    d = _load(name)
    jdk8 = str(d["header"]["jdk"]["java.version"]).startswith("1.8")
    maps = {m["name"]: m for m in d["maps"]}
    stats = collections.Counter()
    bad = []
    for c in d["cases"]:
        if only_star and not (c.get("map") == "star_fig2" and c["op"] not in ("builder_check", "applet")):
            continue
        exp = c["expected"]
        got = _actual(run_harness_case(c, maps, force_generic=force_generic))
        for k in FIELDS:
            if k not in exp and k not in got:
                continue
            e, g = exp.get(k, "<absent>"), got.get(k, "<absent>")
            if not jdk8:
                e, g = _strip_messages(e), _strip_messages(g)
            stats[k] += 1
            if e != g:
                bad.append((c["id"], k, e, g))
    return stats, bad


def _fail_message(bad):
    lines = ["%d mismatching fields" % len(bad)]
    for cid, k, e, g in bad[:10]:
        lines.append("%s.%s\n  expected: %s\n  got:      %s" % (cid, k, json.dumps(e)[:600], json.dumps(g)[:600]))
    return "\n".join(lines)


@pytest.mark.parametrize("name", _sets())
def test_golden_set(name):
    stats, bad = _compare(name)
    assert not bad, _fail_message(bad)
    assert stats["return"] == len(_load(name)["cases"])


@pytest.mark.parametrize("name", [s for s in _sets() if s != "applet"])
def test_golden_set_with_generic_builder(name):
    """Every star_fig2 case rebuilt with the generic builder (as regen_golden.sh's
    --force-generic check) gives the recorded output too."""
    _, bad = _compare(name, force_generic=True, only_star=True)
    assert not bad, _fail_message(bad)


def test_every_golden_set_is_tested():
    names = _sets()
    for s in ("builtin", "star_random_single", "star_random_multi", "random_maps", "subgraphs", "applet",
              "paper_cases"):
        assert s in names
    summary = json.load(open(os.path.join(GOLDEN, "summary.json")))
    assert sorted(summary["sets"]) == sorted(names)
    assert summary["totals"]["cases"] == sum(len(_load(n)["cases"]) for n in names)


def _validate_cases():
    """(case, expected verdict/exception, expected path) for every map case that runs the
    applet pipeline (start "story"): validate ops, joined with the getAgentStory case of the
    same input when there is one."""
    out = []
    for name in _sets():
        d = _load(name)
        maps = {m["name"]: m for m in d["maps"]}
        by_input = {}
        for c in d["cases"]:
            if c["op"] == "getAgentStory" and "map" in c and c.get("start", "story") == "story":
                by_input[(c["map"], json.dumps(c.get("story")), json.dumps(c.get("history")))] = c
        for c in d["cases"]:
            if c["op"] not in ("validateAgentStory", "validateAgentStoryMulti") or "map" not in c:
                continue
            if c.get("start", "story") != "story" or isinstance(c["map"], dict):
                continue
            path_case = by_input.get((c["map"], json.dumps(c.get("story")), json.dumps(c.get("history"))))
            out.append((name, maps, c, path_case))
    return out


def test_validate_compat_reproduces_golden_verdicts_and_paths():
    """validate_compat (the pipeline behind validate(..., compat="original")) agrees with the
    recorded validate / getAgentStory cases."""
    n = n_paths = 0
    for name, maps, c, path_case in _validate_cases():
        spec = maps.get(c["map"])
        if spec is None:
            from cyber_detectives.compat.original import STAR_SPEC
            spec = STAR_SPEC
        agents = "multi" if c["op"] == "validateAgentStoryMulti" else "single"
        exp = c["expected"]
        try:
            ok, path = validate_compat(spec, c.get("story") or [], c.get("history") or [], agents)
        except JavaException as e:
            got_exc = e.describe()
            if path_case is not None and exp["exception"] is None and exp["return"] is True:
                # validate returned true, so the crash must come from getAgentStory
                assert path_case["expected"]["exception"]["origin_frame"] == got_exc["origin_frame"], c["id"]
            else:
                assert exp["exception"] is not None, c["id"]
                assert (got_exc["class"], got_exc["origin_frame"]) == (
                    exp["exception"]["class"], exp["exception"]["origin_frame"]), c["id"]
            n += 1
            continue
        assert exp["exception"] is None and ok == exp["return"], c["id"]
        if agents == "single" and ok and path_case is not None:
            assert path_case["expected"]["exception"] is None
            assert path == path_case["expected"]["return"], c["id"]
            n_paths += 1
        n += 1
    assert n > 1000 and n_paths > 100, (n, n_paths)
