"""The matplotlib live viewer, driven headlessly (Agg canvas, synthetic events)."""

import io
import math
import os
import random
import subprocess
import sys

import pytest

from cyber_detectives import builtin_map, replay, validate
from cyber_detectives.cli import main
from cyber_detectives.geometry import as_geometry, path_polyline, simulate_walk, trace_polyline

try:
    import matplotlib  # noqa: F401
    HAVE_MPL = True
except ImportError:  # pragma: no cover
    HAVE_MPL = False

needs_mpl = pytest.mark.skipif(not HAVE_MPL, reason="matplotlib is not installed "
                               "(pip install 'cyber-detectives[viewer]')")
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
MAPS = ("star_fig2", "icra_fig1", "icra_fig2")


def walk(v, points, step=5.0):
    """Feed a polyline to the viewer as many small mouse targets."""
    for a, b in zip(points, points[1:]):
        n = max(1, int(math.dist(a, b) / step))
        for i in range(1, n + 1):
            v.move_to(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)


def mouse(v, name, x, y):
    from matplotlib.backend_bases import MouseEvent

    px, py = v.ax.transData.transform((x, y))
    ev = MouseEvent(name, v.fig.canvas, px, py, button=1 if name == "button_press_event"
                    else None)
    v.fig.canvas.callbacks.process(name, ev)


def key(v, k):
    from matplotlib.backend_bases import KeyEvent

    v.fig.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event",
                                                               v.fig.canvas, k))


@pytest.fixture
def Viewer():
    if not HAVE_MPL:
        pytest.skip("matplotlib is not installed (pip install 'cyber-detectives[viewer]')")
    from cyber_detectives.viewer import Viewer
    return Viewer


def test_import_does_not_load_matplotlib():
    code = ("import sys, cyber_detectives, cyber_detectives.cli; "
            "print('matplotlib' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=SRC), check=True).stdout
    assert out.strip() == "False"


@needs_mpl
def test_paper_witness_walk_star_fig2(Viewer):
    v = Viewer("star_fig2", claim="ACBAC")
    assert (v.story, v.history, v.where()) == (["A"], [], ("room", "A"))
    walk(v, path_polyline(v.m, validate(v.m, "ACBAC", "b1 o1 o1 o2 o2 b2").path))
    assert v.story == list("ACBAC")
    assert v.history == [["b1", "A"], ["o1", "A"], ["o1", "D"], ["o2", "A"], ["o2", "D"],
                         ["b2", "A"]]
    assert v.where() == ("room", "C")
    ok, msg = v.verdict_of_claim()
    assert ok and "AC[b1d][o1][o2]B[b2r]AC" in msg and msg in v.verdict.get_text().replace(
        "\n", " ")
    v.set_claim("ACBC")
    assert v.verdict_of_claim()[0] is False
    v.set_claim("AXC")
    assert v.verdict_of_claim()[0] is None  # unknown room: an input error, shown as such
    v.set_claim("")
    assert v.verdict_of_claim()[0] is None
    # The engine agrees with the information state shown: x's own place is shaded.
    assert set(v.information_state()) == {"A", "C", "R1", "R2"}


@needs_mpl
@pytest.mark.parametrize("name,seed", [(n, s) for n in MAPS for s in (1, 2)])
def test_replays_simulated_walks(Viewer, name, seed):
    w = simulate_walk(name, rng=seed, steps=15)
    v = Viewer(name, start=w.story[0], claim="".join(w.story))
    walk(v, w.points)
    assert (v.story, v.history) == (w.story, w.history)
    assert v.verdict_of_claim()[0] is True


@needs_mpl
@pytest.mark.parametrize("name", MAPS)
def test_random_mouse_stays_legal(Viewer, name):
    """Random pointer jumps: x never ends in a wall or on a beam, what it records is what a
    geometric replay of its trail records, x is always in the information state, and once
    it is in a room its true story is consistent (single and multi agent)."""
    rng = random.Random(name)
    v = Viewer(name)
    g = v.g
    x0, y0, x1, y1 = g.bbox
    for i in range(120):
        v.move_to(rng.uniform(x0, x1), rng.uniform(y0, y1))
        kind, place = v.where()
        assert kind in ("room", "region", "occupancy")
        assert place in v.information_state()
        if i % 20 == 19:
            v.toggle_agents()
            assert place in v.information_state()
            v.toggle_agents()
    tr = trace_polyline(g, v.trail)
    assert (tr.story, tr.history) == (v.story, v.history)
    assert len(v.history) > 3
    walk(v, [v.pos, g.feature_center(v.start)])  # may stop at a wall; try a witness path
    if v.where()[0] == "room":
        for agents in ("single", "multi"):
            r = validate(v.m, v.story, v.history, agents=agents)
            assert r.consistent, r.reason
            replay(v.m, r.path, v.history, agents=agents, story=v.story)


@needs_mpl
def test_walls_stop_x(Viewer):
    v = Viewer("star_fig2")
    a = v.pos
    assert v.move_to(705.0, 92.5)  # centre of B, through walls
    assert v.pos != (705.0, 92.5)
    assert v.where() in (("room", "A"), ("region", "R1"), ("region", "R2"))
    assert math.dist(a, v.pos) > 1
    assert not v.move_to(*v.pos)
    assert v.story == ["A"] and v.history == []


@needs_mpl
def test_pointer_on_a_boundary(Viewer):
    """x never stops on a cell boundary: from there the next move would not see the room it
    stands on, and a room entry or an occupancy exit would go unrecorded."""
    v = Viewer("star_fig2", start="C")  # C = [0, 210] x [400, 600], door to R1 at y = 500
    assert v.move_to(224.0, 500.0) and v.where() == ("region", "R1")
    assert v.move_to(210.0, 500.0)  # exactly on C's edge
    assert v.pos[0] != 210.0
    v.move_to(150.0, 500.0)
    assert v.story == ["C", "C"] and v.where() == ("room", "C")
    w = Viewer("star_fig2")  # o1 = [295, 530] x [270, 455], door to R1 at y = 372.5
    walk(w, path_polyline(w.m, validate(w.m, "AC", "b1 o1 o1").path)[:10])
    assert w.where() == ("occupancy", "o1")
    w.move_to(309.0, 372.5)
    w.move_to(295.0, 372.5)  # exactly on o1's edge
    w.move_to(281.0, 372.5)
    assert w.history[-2:] == [["o1", "A"], ["o1", "D"]] and w.where() == ("region", "R1")


def pixel_mouse(v, name, px, py):
    """A mouse event at integer pixel coordinates, as a real window sends them."""
    from matplotlib.backend_bases import MouseEvent

    ev = MouseEvent(name, v.fig.canvas, px, py, button=1 if name == "button_press_event"
                    else None)
    v.fig.canvas.callbacks.process(name, ev)


def to_px(v, x, y):
    px, py = v.ax.transData.transform((x, y))
    return int(round(px)), int(round(py))


def assert_trail_matches(v):
    tr = trace_polyline(v.g, v.trail)
    assert (tr.story, tr.history) == (v.story, v.history)


@needs_mpl
def test_integer_pixel_stop_next_to_occupancy_edge(Viewer):
    """Regression: in a 1025 x 563 px window pixel column 239 maps to x = 294.99999999999994,
    one ulp left of o1's edge (x = 295).  x must not stop there: the next move into o1 would
    start "inside" o1 and drop the activation, and the move out would record o1 D alone."""
    from cyber_detectives.viewer import GRID_TOL

    v = Viewer("star_fig2")
    v.fig.set_size_inches(10.25, 10.25 * 0.55)
    pixel_mouse(v, "button_press_event", *to_px(v, *v.pos))
    pts = path_polyline(v.m, validate(v.m, "AC", "o1 o1").path)
    k = next(i for i, p in enumerate(pts) if v.g.location(*p)[0] == "occupancy")
    for a, b in zip(pts, pts[1:k]):
        for i in range(1, 21):
            pixel_mouse(v, "motion_notify_event", *to_px(v, a[0] + (b[0] - a[0]) * i / 20,
                                                          a[1] + (b[1] - a[1]) * i / 20))
    py = to_px(v, 0, 372.5)[1]
    for col in (230, 235, 239):
        pixel_mouse(v, "motion_notify_event", col, py)
        assert min(abs(v.pos[0] - g) for g in v.g.decomposition.xs) >= GRID_TOL
    assert v.where() == ("region", "R1") and v.history == []
    pixel_mouse(v, "motion_notify_event", *to_px(v, 330, 372.5))
    assert v.where() == ("occupancy", "o1") and v.history == [["o1", "A"]]
    pixel_mouse(v, "motion_notify_event", *to_px(v, 270, 372.5))
    assert v.where() == ("region", "R1") and v.history == [["o1", "A"], ["o1", "D"]]
    assert_trail_matches(v)
    assert "R1" in v.information_state()


@needs_mpl
@pytest.mark.parametrize("name,seed", [(n, s) for n in MAPS for s in (1, 2)])
def test_pointer_ulps_off_cell_boundaries(Viewer, name, seed):
    """Pointer targets along a simulated walk, snapped onto (or a few ulps off) nearby cell
    boundaries -- doorway edges included: x never stops within GRID_TOL of a boundary and
    what it records always equals a geometric replay of its whole trail."""
    from cyber_detectives.viewer import GRID_TOL

    rng = random.Random("ulps-%s-%d" % (name, seed))
    w = next(w for w in (simulate_walk(name, rng=seed * 1000 + k, steps=20) for k in range(50))
             if len(w.history) >= 4)
    v = Viewer(name, start=w.story[0])
    d = v.g.decomposition

    def snap(c, lines):
        g = min(lines, key=lambda g: abs(g - c))
        if abs(g - c) > 6 or rng.random() < 0.3:
            return c
        for _ in range(rng.randint(0, 3)):
            g = math.nextafter(g, math.inf if rng.random() < 0.5 else -math.inf)
        return g

    moves = 0
    for a, b in zip(w.points, w.points[1:]):
        n = max(1, int(math.dist(a, b) / 4))
        for i in range(1, n + 1):
            moves += v.move_to(snap(a[0] + (b[0] - a[0]) * i / n, d.xs),
                               snap(a[1] + (b[1] - a[1]) * i / n, d.ys))
            assert min(abs(v.pos[0] - g) for g in d.xs) >= GRID_TOL
            assert min(abs(v.pos[1] - g) for g in d.ys) >= GRID_TOL
        assert v.where()[1] in v.information_state()
    assert_trail_matches(v)
    assert moves > 50 and len(v.history) >= 2


@needs_mpl
def test_information_state_starts_in_a_room(Viewer):
    """x started inside a room: before any recording it cannot be in R3 (star_fig2), which
    no room reaches without crossing a sensor."""
    from cyber_detectives import possible_positions

    v = Viewer("star_fig2")
    assert v.information_state() == {"A", "B", "C", "R1", "R2", "R4"}
    assert "R3" in possible_positions(v.m, [], starts="anywhere")[-1]
    assert "starting in a room" in v.panel.get_text()
    assert "R3" not in v.panel.get_text().split("information state")[1].split("claimed")[0]


@needs_mpl
def test_mouse_and_keys(Viewer):
    v = Viewer("star_fig2", claim="AC")
    pts = path_polyline(v.m, validate(v.m, "AC", "").path)
    mouse(v, "motion_notify_event", *pts[1])
    assert len(v.trail) == 1  # not following yet
    mouse(v, "button_press_event", *v.pos)
    assert v.following
    for a, b in zip(pts, pts[1:]):
        for t in (0.25, 0.5, 0.75, 1.0):
            mouse(v, "motion_notify_event", a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    assert v.story == ["A", "C"] and v.verdict_of_claim()[0] is True
    key(v, "m")
    assert v.agents == "multi" and v.verdict_of_claim()[0] is True
    assert "multi agents" in v.panel.get_text()
    key(v, "u")
    assert v.story == ["A"] and not v.following and v.where()[0] == "region"
    assert v.verdict_of_claim()[0] is True  # the verdict depends on the history only
    key(v, "u")  # nothing left to undo
    assert v.story == ["A"]
    v.textbox.begin_typing()
    key(v, "r")  # typed into the box, not a command
    assert v.agents == "multi" and len(v.trail) > 1
    v.textbox.stop_typing()
    key(v, "r")
    assert (v.story, v.history, len(v.trail), v.where()) == (["A"], [], 1, ("room", "A"))
    v.textbox.set_val("AB")  # B is behind beam b2 / occupancy o2: no recording, no visit
    assert v.claim == "AB" and v.verdict_of_claim()[0] is False
    v.textbox.set_val("ACA")
    assert v.verdict_of_claim()[0] is True


@needs_mpl
def test_undo_restores_history_and_open_occupancy(Viewer):
    v = Viewer("star_fig2")
    m = v.m
    path = validate(m, "AC", "b1 o1 o1").path
    pts = path_polyline(m, path)
    walk(v, pts[:pts.index(as_geometry(m).feature_center("o1")) + 1])
    assert v.where() == ("occupancy", "o1")
    assert v.history == [["b1", "A"], ["o1", "A"]]
    assert v.information_state() == {"o1"}
    v.set_claim("A")
    ok, msg = v.verdict_of_claim()
    assert ok is False and "inside o1" in msg
    v.toggle_agents()  # multi agent: an active sensor at the end is fine
    assert "o1" in v.information_state()
    v.toggle_agents()
    v.undo()
    assert v.history == [["b1", "A"]] and v.where()[0] == "region"


@needs_mpl
def test_dark_theme_and_errors(Viewer, tmp_path):
    v = Viewer("icra_fig2", theme="dark", agents="multi")
    out = tmp_path / "v.png"
    v.save(str(out))
    assert out.stat().st_size > 10000
    with pytest.raises(ValueError, match="no drawing"):
        Viewer("star_fig1")
    with pytest.raises(ValueError):
        Viewer("star_fig2", agents="many")


@needs_mpl
def test_cli_view_save(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    png = tmp_path / "view.png"
    assert main(["view", "--map", "icra_fig2", "--story", "AB", "--multi",
                 "--save", str(png)], out=out, err=err) == 0
    assert png.stat().st_size > 10000 and "wrote" in out.getvalue()
    assert main(["view", "--map", "star_fig1", "--save", str(png)], out=out, err=err) == 2
    assert "no drawing" in err.getvalue()


@needs_mpl
def test_cli_view_save_names(tmp_path):
    """--save writes exactly the file it reports: PNG without an extension, else the
    extension's format."""
    for name, magic in (("noext", b"\x89PNG"), ("v.svg", b"<?xml"), ("v.png", b"\x89PNG")):
        out, err = io.StringIO(), io.StringIO()
        path = tmp_path / name
        assert main(["view", "--map", "star_fig2", "--save", str(path)], out=out,
                    err=err) == 0
        assert out.getvalue() == "wrote %s\n" % path
        assert path.read_bytes()[:len(magic)] == magic
    assert sorted(p.name for p in tmp_path.iterdir()) == ["noext", "v.png", "v.svg"]


@needs_mpl
def test_cli_view_without_a_display():
    """No window possible (non-interactive backend) and no --save: a hint and exit 3."""
    r = subprocess.run([sys.executable, "-m", "cyber_detectives", "view", "--map",
                        "star_fig2"], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=SRC, MPLBACKEND="agg"))
    assert r.returncode == 3 and r.stdout == ""
    assert "no interactive matplotlib backend" in r.stderr and "--save" in r.stderr


@needs_mpl
@pytest.mark.parametrize("flags, want", [
    ([], {"agents": "single", "theme": "light", "claim": "", "save": None}),
    (["--multi"], {"agents": "multi", "theme": "light", "claim": "", "save": None}),
    (["--dark"], {"agents": "single", "theme": "dark", "claim": "", "save": None}),
    (["--story", "ACB"], {"agents": "single", "theme": "light", "claim": "ACB",
                          "save": None}),
    (["--save", "x.png", "--dark", "--multi", "--story", "AB"],
     {"agents": "multi", "theme": "dark", "claim": "AB", "save": "x.png"}),
])
def test_cli_view_options_reach_the_viewer(flags, want, monkeypatch):
    """Every `view` option is handed to viewer.run (DESIGN.md, CLI)."""
    from cyber_detectives import viewer

    calls = []
    monkeypatch.setattr(viewer, "run", lambda m, **kw: calls.append((m.name, kw)))
    out, err = io.StringIO(), io.StringIO()
    assert main(["view", "--map", "icra_fig2", *flags], out=out, err=err) == 0
    assert calls == [("icra_fig2", want)] and err.getvalue() == ""
    assert out.getvalue() == ("wrote x.png\n" if want["save"] else "")


def test_cli_view_without_matplotlib(monkeypatch):
    """`view` without matplotlib: the pip hint on stderr and exit 3."""
    monkeypatch.setitem(sys.modules, "matplotlib", None)  # makes `import matplotlib` fail
    out, err = io.StringIO(), io.StringIO()
    assert main(["view", "--map", "star_fig2", "--save", "never.png"], out=out,
                err=err) == 3
    assert out.getvalue() == ""
    assert err.getvalue() == ("cyber-detectives: the viewer needs matplotlib "
                              "(pip install 'cyber-detectives[viewer]')\n")
