"""JS parity fixtures (``tests/fixtures/parity/*.json``) are up to date, and the JS engine
passes them.

- The records are regenerated in memory with ``tools/gen_parity.py`` and compared byte for
  byte with the committed files: any change of Python behaviour (verdicts, witnesses,
  messages, ...) fails here until ``python3 tools/gen_parity.py`` is rerun, which in turn makes
  ``npm test`` check the browser demo's engine (``docs/js/engine.js``, ``docs/js/original.js``)
  against the new behaviour.
- If ``node`` is on PATH, ``npm test`` (or ``node --test tests/js/*.test.js`` without npm) is
  run; otherwise that test is skipped with the reason.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess

import pytest

from conftest import REPO_ROOT

GEN = os.path.join(REPO_ROOT, "tools", "gen_parity.py")
PARITY_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "parity")


def _gen_module():
    spec = importlib.util.spec_from_file_location("gen_parity", GEN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def generated():
    return _gen_module().build_all()


def test_parity_fixtures_are_up_to_date(generated):
    on_disk = sorted(os.path.basename(p) for p in glob.glob(os.path.join(PARITY_DIR, "*.json")))
    assert on_disk == sorted(generated), (
        "tests/fixtures/parity/ holds %s, the generator writes %s; run python3 tools/gen_parity.py"
        % (on_disk, sorted(generated)))
    stale = []
    for name, text in generated.items():
        with open(os.path.join(PARITY_DIR, name), encoding="utf-8") as f:
            if f.read() != text:
                stale.append(name)
    assert not stale, ("parity fixtures out of date: %s; run python3 tools/gen_parity.py "
                       "and then npm test" % ", ".join(stale))


def test_parity_fixtures_are_well_formed(generated):
    gen = _gen_module()
    total = 0
    for name, text in generated.items():
        assert len(text.encode("utf-8")) <= gen.MAX_BYTES, name
        data = json.loads(text)
        ids = [c["id"] for c in data["cases"]]
        assert len(ids) == len(set(ids)), "%s: duplicate case ids" % name
        for c in data["cases"]:
            assert c["fn"] in gen.FUNCTIONS, (name, c["id"])
            assert set(c["expect"]) in ({"return"}, {"error"}), (name, c["id"])
            m = c["args"].get("map")
            if isinstance(m, str):
                assert m in data["maps"], (name, c["id"], m)
        total += len(ids)
    assert total > 5000
    # no local paths leak into the published fixtures
    for text in generated.values():
        assert "/home/" not in text and "/tmp/" not in text


def test_npm_test_passes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH: the JS parity tests (npm test) were not run")
    # ``node --test`` arrived in Node 18.1 (package.json "engines"); older nodes reject it.
    ver = subprocess.run([node, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True, timeout=60).stdout.strip()
    m = re.match(r"v?(\d+)\.(\d+)\.", ver)
    if m is None or (int(m.group(1)), int(m.group(2))) < (18, 1):
        pytest.skip("node %s is older than 18.1 (node --test unavailable): the JS parity tests "
                    "(npm test) were not run" % (ver or "of unknown version"))
    npm = shutil.which("npm")
    cmd = [npm, "test", "--silent"] if npm else [node, "--test"] + sorted(
        glob.glob(os.path.join(REPO_ROOT, "tests", "js", "*.test.js")))
    proc = subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True, timeout=300)
    assert proc.returncode == 0, "%s failed:\n%s" % (" ".join(cmd), proc.stdout[-6000:])
