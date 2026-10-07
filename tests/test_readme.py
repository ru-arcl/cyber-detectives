"""The README's examples are real: every code block runs and prints what the README shows.

Conventions in README.md:

- ``pycon`` (or ``python`` with ``>>>`` prompts) blocks are doctests, run in order in one
  namespace; ``python`` blocks without prompts are executed.
- ``console`` blocks: a line ``$ CMD`` is a command and the lines after it, up to the next
  ``$``, its exact stdout.  A trailing ``# exit N`` on the command line gives the expected
  exit status (default 0).  ``cyber-detectives`` and ``python -m cyber_detectives`` run this
  checkout's package.
- ``bash`` blocks hold setup commands (install, test suites, opening windows) that are not
  run here; the package commands among them are smoke-tested, and the files they name must
  exist.
- Inline code that starts with ``cyber-detectives `` is run and must exit 0.
- Every relative link or image target exists.
"""

from __future__ import annotations

import doctest
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
TEXT = README.read_text(encoding="utf-8")

_FENCE = re.compile(r"^```([\w-]*)[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)
BLOCKS: List[Tuple[str, str]] = [(m.group(1), m.group(2)) for m in _FENCE.finditer(TEXT)]
PROSE = _FENCE.sub("", TEXT)


def _blocks(*langs: str) -> List[str]:
    return [body for lang, body in BLOCKS if lang in langs]


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["MPLBACKEND"] = "agg"
    env.pop("DISPLAY", None)
    return env


def _argv(cmd: str) -> Optional[List[str]]:
    """The command as argv for this checkout's package, or None if it is not ours."""
    words = shlex.split(cmd, comments=True)
    if words[:1] == ["cyber-detectives"]:
        return [sys.executable, "-m", "cyber_detectives"] + words[1:]
    if len(words) >= 3 and words[0] in ("python", "python3") and words[1:3] == [
            "-m", "cyber_detectives"]:
        return [sys.executable] + words[1:]
    return None


def _run(argv: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(ROOT), env=_env(), capture_output=True, text=True,
                          timeout=120)


# ------------------------------------------------------------------------- python blocks

def test_python_blocks():
    blocks = _blocks("pycon", "python", "py")
    assert blocks, "README has no Python example"
    globs = {"__name__": "readme"}
    parser = doctest.DocTestParser()
    runner = doctest.DocTestRunner(optionflags=doctest.NORMALIZE_WHITESPACE)
    n_examples = 0
    sys.path.insert(0, str(ROOT / "src"))
    try:
        for i, body in enumerate(blocks):
            if ">>>" in body:
                test = parser.get_doctest(body, globs, "README block %d" % i, str(README), 0)
                n_examples += len(test.examples)
                runner.run(test, clear_globs=False)
            else:
                exec(compile(body, "README block %d" % i, "exec"), globs)
    finally:
        sys.path.remove(str(ROOT / "src"))
    res = runner.summarize(verbose=False)
    assert n_examples > 0
    assert res.failed == 0, "%d README example(s) printed something else" % res.failed


# ------------------------------------------------------------------------ console blocks

def _console_cases() -> List[Tuple[str, int, str]]:
    cases = []
    for body in _blocks("console"):
        cmd = None  # type: Optional[str]
        out = []  # type: List[str]
        for line in body.splitlines() + ["$ "]:
            if line.startswith("$ "):
                if cmd:
                    m = re.search(r"#\s*exit\s+(\d+)\s*$", cmd)
                    cases.append((cmd, int(m.group(1)) if m else 0,
                                  "".join(s + "\n" for s in out)))
                cmd, out = line[2:].strip(), []
            else:
                out.append(line)
    return cases


CONSOLE = _console_cases()


def test_console_blocks_exist():
    assert len(CONSOLE) >= 3


@pytest.mark.parametrize("cmd,status,expected", CONSOLE, ids=[c[0][:60] for c in CONSOLE])
def test_console_command(cmd, status, expected):
    argv = _argv(cmd)
    assert argv is not None, "console block command is not a cyber-detectives call: " + cmd
    p = _run(argv)
    assert p.returncode == status, p.stderr
    assert p.stdout == expected
    assert p.stderr == ""


def _inline_commands() -> List[str]:
    return sorted(set(m.group(1) for m in re.finditer(r"`(cyber-detectives [^`]+)`", PROSE)))


@pytest.mark.parametrize("cmd", _inline_commands())
def test_inline_command(cmd):
    p = _run(_argv(cmd))
    assert p.returncode == 0, p.stderr
    if cmd.split()[1] == "maps":  # the prose names the builtin maps
        for name in re.findall(r"`((?:star|icra)_fig\d)`", PROSE):
            assert name in p.stdout


# --------------------------------------------------------------------------- bash blocks

def _bash_commands() -> List[str]:
    cmds = []
    for body in _blocks("bash", "sh", "shell"):
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                cmds.append(line)
    return cmds


def test_bash_commands_name_existing_files():
    cmds = _bash_commands()
    assert cmds
    for cmd in cmds:
        for word in shlex.split(cmd, comments=True):
            if "/" in word and not word.startswith(("-", "http")) and word != "OUT":
                assert (ROOT / word).exists(), "%r in %r does not exist" % (word, cmd)


@pytest.mark.parametrize("cmd", [c for c in _bash_commands() if _argv(c)])
def test_bash_package_command(cmd, tmp_path):
    argv = _argv(cmd)
    if "view" in argv:
        # Opening a window is not testable; render the first frame instead.
        pytest.importorskip("matplotlib")
        out = tmp_path / "view.png"
        p = _run(argv + ["--save", str(out)])
        assert p.returncode == 0, p.stderr
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        p = _run(argv)
        assert p.returncode == 0, p.stderr


# --------------------------------------------------------------------------------- links

def _links() -> List[str]:
    targets = re.findall(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", TEXT)
    return [t for t in targets if not re.match(r"[a-z]+:", t) and not t.startswith("#")]


def test_links_found():
    links = _links()
    assert "LICENSE" in links
    assert "docs/media/demo.gif" in links


def test_media_images_and_scripts_listed():
    """Every image in docs/media is shown, and every script that makes one is in the README."""
    links = _links()
    media = ROOT / "docs" / "media"
    images = sorted(p.name for p in media.iterdir() if p.suffix in (".png", ".gif"))
    assert {"demo.gif", "concepts.png", "viewer.png"} <= set(images)
    for name in images:
        assert "docs/media/" + name in links, name
    cmds = _bash_commands()
    for script in sorted(p.name for p in media.glob("make_*")):
        assert any("docs/media/" + script in shlex.split(c, comments=True) for c in cmds), script


@pytest.mark.parametrize("target", sorted(set(_links())))
def test_relative_link_exists(target):
    path = target.split("#", 1)[0]
    if path.startswith("src/") and not (ROOT / "src").is_dir():
        pytest.skip("no src/ next to the tests (testing an installed package)")
    assert (ROOT / path).exists(), "README links to missing %s" % target


# ------------------------------------------------------------------------------- content

def test_bug_ids_match_notes():
    notes = (ROOT / "docs" / "notes" / "original-bugs.md").read_text(encoding="utf-8")
    listed = re.findall(r"^- (B\d+):", TEXT, re.M)
    assert listed == ["B%d" % i for i in range(1, 11)]
    for b in listed:
        assert re.search(r"^## %s$" % b, notes, re.M), b


def test_no_local_paths():
    assert not re.search(r"/(home|tmp|Users)/|[A-Z]:\\", TEXT)
