"""Command-line interface."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import types

import pytest

from conftest import REPO_ROOT
from cyber_detectives import builtin_map
from cyber_detectives import engine as engine_mod
from cyber_detectives.cli import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_validate_inconsistent():
    code, out, _ = run("validate", "--map", "star_fig2", "--story", "ACBAC",
                       "--history", "b1 o1 o1 b2 o2 o2")
    assert code == 1
    assert out.startswith("inconsistent\nreason: ")


def test_validate_consistent_path():
    code, out, _ = run("validate", "--map", "star_fig2", "--story", "ACBAC",
                       "--history", "b1 o1 o1 o2 o2 b2")
    assert code == 0
    assert out == "consistent\npath: AC[b1d][o1][o2]B[b2r]AC\n"


def test_validate_multi_json():
    code, out, _ = run("validate", "--map", "star_fig2", "--story", "ACBAC",
                       "--history", "b1 o1 o2 b2 o2 o1", "--multi", "--json")
    assert code == 0
    d = json.loads(out)
    assert d["consistent"] and d["agents"] == "multi" and d["map"] == "star_fig2"
    assert d["path_string"] == "AC{o1}{o2}B{o2}{o1}AC"
    assert d["path"][0] == {"kind": "start", "position": "A", "time": 0, "story_index": 1,
                            "sensor": None, "event": None}


def test_validate_unreported_visits():
    args = ["validate", "--map", "icra_fig2", "--story", "ABDEC",
            "--history", "b1 b3 o2 o2 b4"]
    assert run(*args)[0] == 1
    assert run(*(args + ["--unreported-visits"]))[0] == 0


def test_validate_map_file(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(builtin_map("star_fig2").to_dict()))
    code, out, _ = run("validate", "--map", str(p), "--story", "AB", "--history", "b2")
    assert code == 0 and "A[b2l]B" in out


@pytest.mark.parametrize("argv, msg", [
    (["--map", "nope", "--story", "A", "--history", ""], "neither a builtin map"),
    (["--map", "star_fig2", "--story", "AZ", "--history", ""], "not a room"),
    (["--map", "star_fig2", "--story", "A", "--history", "b7"], "not a sensor"),
    (["--map", "star_fig2", "--story", "", "--history", ""], "at least one room"),
])
def test_validate_input_errors(argv, msg):
    code, out, err = run("validate", *argv)
    assert code == 2 and msg in err and out == ""


def test_validate_unreadable_map_file(tmp_path):
    """A --map path that exists but cannot be read is an input error (exit 2), not a
    traceback with exit 1 ("inconsistent")."""
    code, out, err = run("validate", "--map", str(tmp_path), "--story", "A", "--history", "")
    assert code == 2 and out == ""
    assert err.startswith("cyber-detectives: error: cannot read --map ") and "\n" == err[-1]
    p = tmp_path / "noperm.json"
    p.write_text(json.dumps(builtin_map("star_fig2").to_dict()))
    p.chmod(0)
    try:
        if not os.access(str(p), os.R_OK):  # root ignores file modes
            code, out, err = run("validate", "--map", str(p), "--story", "A", "--history", "")
            assert code == 2 and out == "" and "cannot read --map" in err
    finally:
        p.chmod(0o644)


def test_validate_compat_not_available(monkeypatch):
    def boom(name):
        raise ModuleNotFoundError("no", name="cyber_detectives.compat")
    monkeypatch.setattr(engine_mod.importlib, "import_module", boom)
    code, _, err = run("validate", "--map", "star_fig2", "--story", "A", "--history", "b1",
                       "--compat", "original")
    assert code == 3 and "not implemented" in err


def test_validate_compat_crash(monkeypatch):
    mod = types.ModuleType("cyber_detectives.compat.original")

    class ArrayIndexOutOfBoundsException(Exception):
        pass

    ArrayIndexOutOfBoundsException.__module__ = "cyber_detectives.compat.original"

    def validate_compat(*a):
        raise ArrayIndexOutOfBoundsException("6 (Algorithms.java:365)")

    mod.validate_compat = validate_compat
    monkeypatch.setattr(engine_mod.importlib, "import_module", lambda n: mod)
    code, out, _ = run("validate", "--map", "star_fig2", "--story", "B", "--history", "",
                       "--compat", "original")
    assert code == 4 and "ArrayIndexOutOfBoundsException" in out
    code, out, _ = run("validate", "--map", "star_fig2", "--story", "B", "--history", "",
                       "--compat", "original", "--json")
    assert code == 4 and json.loads(out)["exception"]["class"] == "ArrayIndexOutOfBoundsException"


def test_maps_list_and_show():
    code, out, _ = run("maps")
    assert code == 0
    for name in ("star_fig2", "star_fig1", "icra_fig1", "icra_fig2"):
        assert name in out
    code, out, _ = run("maps", "--json")
    rows = json.loads(out)
    assert [r["name"] for r in rows] == ["icra_fig1", "icra_fig2", "star_fig1", "star_fig2"]
    code, out, _ = run("maps", "icra_fig2")
    d = json.loads(out)
    assert d["name"] == "icra_fig2" and len(d["regions"]) == 7
    code, out, err = run("maps", "nope")
    assert code == 2 and out == ""
    assert err.startswith("cyber-detectives: error: map 'nope' is neither a builtin map")
    assert "--map" not in err  # `maps` has no --map option


@pytest.mark.parametrize("argv", [
    ["intervals", "--case", "2"],
    ["superstory"],
    ["closest"],
])
def test_problem_commands_wired(argv, monkeypatch):
    """Problems 2-4 subcommands call the problems.py functions with the parsed inputs."""
    import cyber_detectives.problems as problems
    seen = {}

    real = {"validate_intervals": problems.validate_intervals,
            "shortest_superstory": problems.shortest_superstory,
            "closest_story": problems.closest_story}

    def spy(fn):
        def wrapped(m, story, history, **kw):
            seen["call"] = (m.name, story, history, kw)
            return real[fn](m, story, history, **kw)
        return wrapped

    for fn in real:
        monkeypatch.setattr(problems, fn, spy(fn))
    code, out, _ = run(*argv, "--map", "star_fig2", "--story", "AB", "--history", "b2")
    assert code == 0 and out.startswith("consistent\n")
    assert out.endswith("path: A[b2l]B\n") or out.endswith("|tf'|\n")
    assert seen["call"][:3] == ("star_fig2", "AB", "b2")
    if argv[0] == "intervals":
        assert seen["call"][3]["case"] == 2


@pytest.mark.parametrize("argv, want", [
    (["intervals", "--case", "2"], {"case": 2, "agents": "single", "unreported_visits": False}),
    (["intervals", "--case", "5", "--multi"], {"case": 5, "agents": "multi"}),
    (["superstory"], {"anchored": True, "agents": "single", "unreported_visits": False}),
    (["superstory", "--free"], {"anchored": False, "agents": "single"}),
    (["superstory", "--free", "--multi", "--unreported-visits"],
     {"anchored": False, "agents": "multi", "unreported_visits": True}),
    (["closest", "--multi"], {"agents": "multi", "unreported_visits": False}),
])
def test_problem_command_options(argv, want, monkeypatch):
    import cyber_detectives.problems as problems
    seen = {}

    def fake(m, story, history, **kw):
        seen.update(kw)
        return None

    for fn in ("validate_intervals", "shortest_superstory", "closest_story"):
        monkeypatch.setattr(problems, fn, fake)
    assert run(*argv, "--map", "star_fig2", "--story", "AB", "--history", "b2")[0] == 1
    assert {k: seen.get(k) for k in want} == want
    if argv[0] != "superstory":
        assert "anchored" not in seen


def test_superstory_free_and_multi_end_to_end():
    args = ["superstory", "--map", "star_fig2", "--story", "ACB", "--history", "b2 b1 b2"]
    assert run(*args) == (1, "no solution\n", "")  # anchored: none
    code, out, _ = run(*(args + ["--free"]))
    assert code == 0
    assert out.splitlines() == ["consistent", "story: B A C B", "inserted: B at 0",
                                "path: B[b2r]AC[b1d][b2l]B"]
    code, out, _ = run(*(args + ["--free", "--json"]))
    d = json.loads(out)
    assert code == 0 and d["story"] == ["B", "A", "C", "B"] and d["anchored"] is False
    # the empty story is allowed only with --free
    code, out, _ = run("superstory", "--map", "star_fig2", "--story", "", "--history", "b2",
                       "--free")
    assert code == 0 and "story: A B" in out
    code, _, err = run("superstory", "--map", "star_fig2", "--story", "", "--history", "b2")
    assert code == 2 and "at least one room" in err
    # b2 then b1: a single agent that crossed b2 into R4 cannot reach b1 any more; with
    # other agents present one of them makes the b1 recording
    two = ["--map", "star_fig2", "--story", "AB", "--history", "b2 b1"]
    assert run("superstory", *two) == (1, "no solution\n", "")
    code, out, _ = run("superstory", *two, "--multi", "--json")
    d = json.loads(out)
    assert code == 0 and d["agents"] == "multi" and d["story"] == ["A", "B"]
    code, out, _ = run("closest", *two, "--json")
    assert code == 0 and json.loads(out)["edits"] == 2
    code, out, _ = run("closest", *two, "--multi", "--json")
    assert code == 0 and json.loads(out)["edits"] == 0
    assert run("intervals", "--case", "3", *two)[0] == 1
    code, out, _ = run("intervals", "--case", "3", *two, "--multi", "--json")
    assert code == 0 and json.loads(out)["agents"] == "multi"


def test_problem_command_none_result(monkeypatch):
    import cyber_detectives.problems as problems
    monkeypatch.setattr(problems, "shortest_superstory", lambda *a, **k: None)
    code, out, _ = run("superstory", "--map", "star_fig2", "--story", "AB", "--history", "b1")
    assert code == 1 and out == "no solution\n"
    code, out, _ = run("superstory", "--map", "star_fig2", "--story", "AB", "--history", "b1",
                       "--json")
    assert code == 1 and out == "null\n"


ICRA = ["--map", "icra_fig2", "--story", "ABDEC", "--history", "b1 b3 o2 o2 b4"]


@pytest.mark.parametrize("argv, expected", [
    (["intervals", "--case", "2"],
     ["consistent", "case: 2 (t0 < t0' < tf < tf')",
      "path: |t0|AB<b31>DE<o2><b22>|t0'|[b11]C|tf|[b31](D)[o2](D)[b41]|tf'|"]),
    (["superstory"],
     ["consistent", "story: A B D E D C", "inserted: D at 4",
      "path: A[b11]B[b31]D[o2]ED[b41]C"]),
    (["closest"],
     ["consistent", "story: A B D D C", "edits: 1 (substitute E at 3 by D)",
      "path: A[b11]B[b31]D[o2]D[b41]C"]),
])
def test_problem_text_output(argv, expected):
    """Text output is uniform with validate: verdict first, then only meaningful fields."""
    code, out, err = run(*argv, *ICRA)
    assert (code, err) == (0, "")
    assert out.splitlines() == expected
    assert "None" not in out and "agents:" not in out and "compat:" not in out


def test_problem_text_output_closest_operations_and_zero_edits():
    code, out, _ = run("closest", "--map", "icra_fig2", "--story", "DAD",
                       "--history", "b1 b3 o2 o2 b4")
    assert code == 0 and out.splitlines()[1:3] == [
        "story: A D D C",
        "edits: 3 (insert A before 0; substitute A at 1 by D; substitute D at 2 by C)"]
    code, out, _ = run("closest", "--map", "star_fig2", "--story", "AB", "--history", "b2")
    assert code == 0 and out == "consistent\nstory: A B\nedits: 0\npath: A[b2l]B\n"


def test_problem_text_output_malformed_history():
    two = ["--map", "icra_fig2", "--story", "AB", "--history", "o2 b1"]
    code, out, _ = run("intervals", "--case", "3", *two)
    lines = out.splitlines()
    assert code == 1 and lines[0] == "inconsistent" and len(lines) == 3
    assert lines[2].startswith("reason: malformed history: recording 2 (b1 A)")
    for cmd in ("superstory", "closest"):
        assert run(cmd, *two) == (1, "no solution\n", "")


def test_problem_text_output_intervals_inconsistent():
    code, out, _ = run("intervals", "--case", "3", "--map", "star_fig2", "--story", "AB",
                       "--history", "b2 b1")
    assert code == 1
    lines = out.splitlines()
    assert lines[:2] == ["inconsistent", "case: 3 (t0' < t0 < tf < tf')"]
    assert len(lines) == 3 and lines[2].startswith("reason: no walk tells the story")


@pytest.mark.parametrize("argv, keys", [
    (["intervals", "--case", "2"], {"consistent", "reason", "agents", "compat", "case",
                                    "interval", "path_string", "path", "map"}),
    (["superstory"], {"story", "length", "inserted", "anchored", "agents", "path_string",
                      "path", "map"}),
    (["closest"], {"story", "edits", "operations", "agents", "path_string", "path", "map"}),
])
def test_problem_json_complete(argv, keys):
    code, out, _ = run(*argv, *ICRA, "--json")
    d = json.loads(out)
    assert code == 0 and set(d) == keys and d["map"] == "icra_fig2"


def test_module_entry_point():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(REPO_ROOT, "src") + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run([sys.executable, "-m", "cyber_detectives", "validate", "--map",
                        "star_fig2", "--story", "ACBAC", "--history", "b1 o1 o1 b2 o2 o2"],
                       capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert p.returncode == 1 and p.stdout.startswith("inconsistent")
    p = subprocess.run([sys.executable, "-m", "cyber_detectives", "--version"],
                       capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert p.returncode == 0 and "1.0.0" in p.stdout
