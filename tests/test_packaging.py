"""Packaging metadata stays consistent.

- ``cyber_detectives.__version__`` is the single source of the version: ``pyproject.toml``
  declares it dynamic (setuptools ``attr``) and ``CITATION.cff`` repeats it.
- When a recent setuptools (>= 77, the ``build-system`` requirement) is importable, the wheel
  metadata is generated from a scratch copy of the project and its ``Version`` is compared,
  and a wheel is built and must hold every module, every builtin map (``data/*.json``,
  ``data/geometry/*.json``) and the ``cyber-detectives`` console script; otherwise only the
  static ``pyproject.toml`` configuration is checked.  (The rest of the suite imports
  ``src/``, see ``[tool.pytest.ini_options]``, so only these tests see what a wheel ships.)
- Both need the source tree: run from an unpacked copy of the tests without ``src/`` (to test
  an installed package), they skip.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

import pytest

from conftest import REPO_ROOT

import cyber_detectives


def _read(name):
    with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as f:
        return f.read()


def _toml_section(text, name):
    """Body of the ``[name]`` table of a TOML file (enough for our flat pyproject)."""
    m = re.search(r"^\[%s\]\s*$(.*?)(?=^\[|\Z)" % re.escape(name), text, re.M | re.S)
    assert m is not None, "[%s] missing from pyproject.toml" % name
    return m.group(1)


def test_citation_version_matches_package():
    m = re.search(r"^version:\s*['\"]?([^'\"\s#]+)", _read("CITATION.cff"), re.M)
    assert m is not None, "CITATION.cff has no top-level 'version:'"
    assert m.group(1) == cyber_detectives.__version__


def test_pyproject_version_is_dynamic_from_package():
    text = _read("pyproject.toml")
    project = _toml_section(text, "project")
    assert re.search(r'^dynamic\s*=\s*\[[^\]]*"version"', project, re.M), \
        "pyproject.toml must declare the version dynamic"
    assert not re.search(r"^version\s*=", project, re.M), \
        "pyproject.toml must not repeat a static version"
    dynamic = _toml_section(text, "tool.setuptools.dynamic")
    assert re.search(r'^version\s*=\s*\{\s*attr\s*=\s*"cyber_detectives\.__version__"\s*\}',
                     dynamic, re.M)


def test_license_metadata_is_spdx():
    project = _toml_section(_read("pyproject.toml"), "project")
    assert re.search(r'^license\s*=\s*"BSD-3-Clause"\s*$', project, re.M)
    assert re.search(r'^license-files\s*=\s*\[\s*"LICENSE"\s*\]', project, re.M)
    assert "License ::" not in project, "license classifiers are deprecated (PEP 639)"


def test_node_engine_matches_test_runner():
    with open(os.path.join(REPO_ROOT, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    assert pkg["engines"]["node"] == ">=18.1"  # ``node --test`` (npm test) needs Node 18.1
    assert pkg["scripts"]["test"].startswith("node --test")


def _setuptools_version():
    try:
        import setuptools
    except ImportError:
        return None
    m = re.match(r"(\d+)", setuptools.__version__)
    return int(m.group(1)) if m else None


def _project_copy(tmp_path):
    """A scratch copy of the buildable project (so no egg-info or build/ lands in the source
    tree); skips without setuptools >= 77 or without the source tree."""
    major = _setuptools_version()
    if major is None or major < 77:
        pytest.skip("setuptools >= 77 is not importable: no wheel built (the static pyproject "
                    "configuration is checked by test_pyproject_version_is_dynamic_from_package)")
    if not os.path.isdir(os.path.join(REPO_ROOT, "src", "cyber_detectives")):
        pytest.skip("no src/ next to the tests (testing an installed package): nothing to build")
    proj = tmp_path / "proj"
    proj.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(os.path.join(REPO_ROOT, name), str(proj / name))
    shutil.copytree(os.path.join(REPO_ROOT, "src"), str(proj / "src"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    return proj


def test_wheel_metadata_version_matches_package(tmp_path):
    proj = _project_copy(tmp_path)
    out = tmp_path / "meta"
    out.mkdir()
    code = ("import sys, setuptools.build_meta as b; "
            "print(b.prepare_metadata_for_build_wheel(sys.argv[1]))")
    proc = subprocess.run([sys.executable, "-c", code, str(out)], cwd=str(proj),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-6000:]
    dist_info = proc.stdout.strip().splitlines()[-1]
    with open(str(out / dist_info / "METADATA"), encoding="utf-8") as f:
        metadata = f.read()
    m = re.search(r"^Version:\s*(\S+)", metadata, re.M)
    assert m is not None, metadata[:2000]
    assert m.group(1) == cyber_detectives.__version__
    assert re.search(r"^License-Expression:\s*BSD-3-Clause\s*$", metadata, re.M)


def test_wheel_contents(tmp_path):
    """The wheel ships every module and data file of src/cyber_detectives and the console
    script (a missing ``package-data`` entry would otherwise go unnoticed: the suite imports
    src/)."""
    proj = _project_copy(tmp_path)
    out = tmp_path / "dist"
    out.mkdir()
    code = ("import sys, setuptools.build_meta as b; "
            "print(b.build_wheel(sys.argv[1]))")
    proc = subprocess.run([sys.executable, "-c", code, str(out)], cwd=str(proj),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-6000:]
    wheel = out / proc.stdout.strip().splitlines()[-1]
    with zipfile.ZipFile(str(wheel)) as z:
        names = set(z.namelist())
        dist_info = {n.split("/", 1)[0] for n in names if ".dist-info/" in n}
        assert len(dist_info) == 1, dist_info
        entry_points = z.read(dist_info.pop() + "/entry_points.txt").decode("utf-8")
    src = os.path.join(REPO_ROOT, "src")
    expected = set()
    for dirpath, dirnames, filenames in os.walk(os.path.join(src, "cyber_detectives")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if f.endswith((".py", ".json")):
                expected.add(os.path.relpath(os.path.join(dirpath, f), src).replace(os.sep, "/"))
    assert {"cyber_detectives/data/star_fig2.json",
            "cyber_detectives/data/geometry/star_fig2.json",
            "cyber_detectives/compat/original.py"} <= expected
    assert sorted(expected - names) == []
    assert re.search(r"^\[console_scripts\]\s*^cyber-detectives\s*=\s*cyber_detectives\.cli:main\s*$",
                     entry_points, re.M), entry_points
