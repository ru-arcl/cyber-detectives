"""Live differential test of compat="original" against the real Java original.

Runs a small batch of fresh random cases (tools/reference/differential.py) through the
reference harness and through the port and requires every field to match. The live test
needs a reference build:

- ``$CD_CACHE/env.sh`` (default cache: ``tools/reference/.cache``) from a previous
  ``tools/reference/build.sh`` run -- used as is (``CD_SKIP_BUILD=1``); or
- ``CD_JAVA_HOME`` set to a JDK 8 -- then ``run.sh`` builds first (it needs the original
  checkout via ``CD_ORIGINAL_DIR`` or network access, see tools/reference/README.md).

Otherwise it is skipped with the reason. Batch size: 200 cases times ``CD_TEST_SCALE``.
The generator self-checks below need no JDK.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REF = os.path.join(REPO, "tools", "reference")
sys.path.insert(0, REF)
_dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in tools/reference
import differential as D  # noqa: E402
sys.dont_write_bytecode = _dwb

SEED = 7357  # not the script's default seed: the test batch differs from a default run


def _scale() -> int:
    try:
        return max(1, int(os.environ.get("CD_TEST_SCALE", "1")))
    except ValueError:
        return 1


def _env_sh_java(path):
    """CD_JAVA from an env.sh written by build.sh (None if absent)."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("CD_JAVA="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


def _live_setup():
    """(extra environment, None) when a JDK is available, else (None, skip reason)."""
    cache = os.environ.get("CD_CACHE") or os.path.join(REF, ".cache")
    env_sh = os.path.join(cache, "env.sh")
    if os.path.isfile(env_sh):
        java = _env_sh_java(env_sh)
        if java and os.access(java, os.X_OK):
            return {"CD_CACHE": cache, "CD_SKIP_BUILD": os.environ.get("CD_SKIP_BUILD", "1")}, None
        if not os.environ.get("CD_JAVA_HOME"):
            return None, "%s points to a missing java (%s); rerun tools/reference/build.sh" % (env_sh, java)
    if os.environ.get("CD_JAVA_HOME"):
        return {"CD_CACHE": cache, "CD_SKIP_BUILD": "0"}, None  # run.sh builds into the cache
    return None, ("no JDK for the live differential test: set CD_JAVA_HOME to a JDK 8, or build the "
                  "reference once with tools/reference/build.sh (cache %s; see tools/reference/README.md)" % cache)


def test_differential_live(tmp_path, monkeypatch):
    extra, reason = _live_setup()
    if extra is None:
        pytest.skip(reason)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    try:
        s = D.run_differential(n=200 * _scale(), seed=SEED, jobs=2, out=str(tmp_path), max_repros=3,
                               log=open(os.devnull, "w"))
    except D.SetupError as e:
        pytest.fail("reference harness could not run: %s" % e)
    import io
    buf = io.StringIO()
    D.report(s, buf)
    assert not s["harness_errors"], buf.getvalue()
    assert not s["mismatches"], buf.getvalue()
    assert s["cases"] >= 200 * _scale()
    # the batch must actually reach the interesting code paths
    assert s["validate_compat_checked"] > 0
    assert s["cases_with_tree_bins"] > 0, "no case built a treeified HashMap bin"
    for op in ("validateAgentStory", "validateAgentStoryMulti", "getAgentStory", "getAgentStoryStatuses",
               "applet"):
        assert s["by_op"].get(op, 0) > 0, (op, s["by_op"])


# ---------------------------------------------------------------- no JDK needed


def test_generator_is_deterministic_across_hash_seeds():
    """The generated batch depends only on (seed, n), not on PYTHONHASHSEED."""
    code = ("import sys, json, hashlib; sys.path.insert(0, %r); import differential as D; "
            "print(hashlib.sha1(json.dumps(D.generate(400, %d)).encode()).hexdigest())" % (REF, SEED))
    outs = set()
    for hs in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hs, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=os.path.join(REPO, "src"))
        outs.add(subprocess.run([sys.executable, "-c", code], env=env, check=True,
                                stdout=subprocess.PIPE, universal_newlines=True).stdout.strip())
    assert len(outs) == 1, outs


def test_generated_cases_are_well_formed_for_the_port():
    """Every generated case runs through the port without a harness error or a crash of
    the port itself, and units are reproducible one at a time (--only)."""
    data = D.generate(300, SEED)
    cov = {}
    res = D.run_python(data, cov)
    bad = [r for r in res if "harness_error" in r or "python_crash" in r]
    assert not bad, json.dumps(bad[:3], indent=1)[:3000]
    assert cov.get("treeified", 0) > 0
    big = [m for m in data["maps"] if "big" in m.get("source", "")]
    assert big, "no big map generated"
    for m in big:  # every big map has a vertex with >= 11 neighbours
        deg = {}
        for u, w in m["edges"]:
            deg[u] = deg.get(u, 0) + 1
            deg[w] = deg.get(w, 0) + 1
        assert max(deg.values()) >= 11, m["name"]
    unit = data["cases"][-1]["unit"]
    again = D.generate(0, SEED, only=[unit])
    assert again["cases"] == [c for c in data["cases"] if c["unit"] == unit]


def test_comparison_reports_first_difference():
    a = {"return": [{"path": "A[b1u]C", "x": 1}], "stdout": "abc"}
    b = {"return": [{"path": "A[b1d]C", "x": 1}], "stdout": "abc"}
    assert D.first_diff(a, b) == ".return[0].path (strings differ at char 4)"
    diffs = D.compare_result({}, dict(a, id="c", op="applet"), dict(b, id="c", op="applet"))
    assert [d[0] for d in diffs] == ["return"]
    assert D.compare_result({}, {"id": "c", "stdout": "x"}, {"id": "c", "stdout": "x"}) == []
    exc_j = {"exception": {"class": "E", "message": "m1", "top_frame": "f", "origin_frame": "f", "trace": []}}
    exc_p = {"exception": {"class": "E", "message": "m2", "top_frame": "f", "origin_frame": "f", "trace": []}}
    assert D.compare_result({}, exc_j, exc_p, jdk8=True)
    assert not D.compare_result({}, exc_j, exc_p, jdk8=False)  # messages are JDK-specific
