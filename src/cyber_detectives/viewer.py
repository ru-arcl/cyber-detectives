"""Live matplotlib viewer: walk agent x through a map and test stories against what it records.

    python -m cyber_detectives view --map star_fig2        (also icra_fig1, icra_fig2)

Click the map to make x follow the mouse (click again to stop).  x moves along the straight
segment from where it is towards the pointer and stops just before a wall, or before any
place the region model cannot enter (it never stops on a beam).  On the way it records

- its **true story**: the rooms it enters, starting with the room it starts in;
- the **observation history**: beam crossings and occupancy activations/deactivations.

The shaded rooms, regions and occupancy rectangles are the *information state*: every
place where x could be now, judged from the recordings alone and the fact that x started
inside some room (which room, and the story, unknown).
Type a claimed story in the text box: the panel says live whether it is consistent with the
recorded history (:func:`cyber_detectives.validate`).  Keys: ``m`` single/multi agent,
``u`` undo the last recording or room entry, ``r`` reset.

Needs matplotlib, imported lazily: ``import cyber_detectives`` never loads it.
"""

from __future__ import annotations

import bisect
import math
import os
import textwrap
from typing import Any, List, Optional, Sequence, Set, Tuple, Union

from .engine import possible_positions, validate
from .geometry import GeometryError, as_geometry, trace_polyline
from .history import InputError
from .maps import Map, builtin_map

__all__ = ["NoDisplayError", "Viewer", "information_state", "run"]

Point = Tuple[float, float]
_STAND = ("room", "occupancy", "region")
_ORDER = {"room": 0, "region": 1, "occupancy": 2}
BACKOFF = 2.0  # world units kept between x and the obstacle it stopped at
GRID_TOL = 1e-6  # x never stops closer than this (world units) to a cell boundary
#: matplotlib backends that cannot open a window (``rcsetup.non_interactive_bk`` adds more).
NON_INTERACTIVE = ("agg", "cairo", "pdf", "pgf", "ps", "svg", "template")


class NoDisplayError(RuntimeError):
    """:func:`run` without ``save`` on a matplotlib backend that cannot open a window."""

THEMES = {
    "light": dict(bg="#ffffff", fg="#1f2328", muted="#6e7781", wall="#2b2f36",
                  free="#f2f3f5", room="#e3e6ea", occ="#fbe7c6", beam="#7c4dff",
                  possible="#7fb3ff", agent="#d1242f", trail="#d1242f", ok="#1a7f37",
                  bad="#cf222e", box="#f6f8fa", boxhover="#ffffff"),
    "dark": dict(bg="#16181d", fg="#e6edf3", muted="#8b949e", wall="#c9d1d9",
                 free="#22262e", room="#30363d", occ="#5a4520", beam="#b39dff",
                 possible="#2f6fd0", agent="#ff6b6b", trail="#ff6b6b", ok="#3fb950",
                 bad="#ff7b72", box="#22262e", boxhover="#2d333b"),
}


def information_state(m: Map, history: Any, agents: str = "single") -> Set[str]:
    """Positions (rooms, regions, occupancy sensors) where x can be after ``history``, judged
    from the recordings alone and the fact that x started inside some room: the last slot of
    :func:`~cyber_detectives.possible_positions` without a story, ``starts="rooms"``.  For a
    single agent whose history ends with an open activation ``o A`` (x is inside ``o``) the
    answer is ``{o}``, or empty if x cannot be next to ``o`` at that moment."""
    history = [list(e) for e in history]
    if agents == "single" and history and m.is_occupancy(history[-1][0]) and \
            history[-1][1] == "A":
        # x is inside: a single-agent history with an open activation is malformed for the
        # engine, so filter up to the activation and step inside by hand.
        o = history[-1][0]
        before = possible_positions(m, history[:-1], agents=agents, starts="rooms")[-1]
        return {o} if set(before) & set(m.regions_of[o]) else set()
    return set(possible_positions(m, history, agents=agents, starts="rooms")[-1])


def _history_text(history: Sequence[Sequence[str]], m: Map) -> str:
    return " ".join(s if m.is_beam(s) else s + ("+" if k == "A" else "-") for s, k in history)


class Viewer:
    """The viewer's state and figure.  Drive it with :meth:`move_to`, :meth:`set_claim`,
    :meth:`toggle_agents`, :meth:`undo`, :meth:`reset`, or with real/synthetic mouse and key
    events.  ``fig=None`` draws on an off-screen Agg figure (tests, snapshots)."""

    def __init__(self, m: Union[Map, str], agents: str = "single", theme: str = "light",
                 start: Optional[str] = None, fig: Any = None, claim: str = "") -> None:
        from matplotlib.collections import PatchCollection
        from matplotlib.patches import Rectangle
        from matplotlib.widgets import TextBox

        self.m = builtin_map(m) if isinstance(m, str) else m
        try:
            self.g = as_geometry(self.m)
        except GeometryError as e:
            raise ValueError("map %r has no drawing to walk in" % self.m.name) from e
        if agents not in ("single", "multi"):
            raise ValueError("agents must be 'single' or 'multi', got %r" % (agents,))
        self.agents, self.c = agents, THEMES[theme]
        self.start = start or next(r for r in self.m.rooms if r in self.g.rooms)
        if fig is None:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure
            fig = Figure(figsize=(12, 6.6))
            FigureCanvasAgg(fig)
        self.fig, c = fig, self.c
        fig.set_facecolor(c["bg"])
        ax = self.ax = fig.add_axes((0.01, 0.02, 0.6, 0.96))
        x0, y0, x1, y1 = self.g.decomposition.domain
        ax.set_xlim(x0, x1)
        ax.set_ylim(y1, y0)  # y points down, as in the drawings
        ax.set_aspect("equal")
        ax.set_axis_off()
        # Free regions as merged columns of free cells, one collection per region.
        d = self.g.decomposition
        self._grid = (list(d.xs), list(d.ys))  # sorted cell boundaries
        cols: dict = {}
        for i in range(d.nx):
            j = 0
            while j < d.ny:
                idx = i * d.ny + j
                if d.cell[idx] != 0:
                    j += 1
                    continue
                k = j
                while k + 1 < d.ny and d.cell[i * d.ny + k + 1] == 0 and \
                        d.comp[i * d.ny + k + 1] == d.comp[idx]:
                    k += 1
                cols.setdefault(d.comp_names[d.comp[idx]], []).append(
                    Rectangle((d.xs[i], d.ys[j]), d.xs[i + 1] - d.xs[i], d.ys[k + 1] - d.ys[j]))
                j = k + 1
        self.patches: dict = {}
        for name, rects in cols.items():
            pc = PatchCollection(rects, linewidth=0, antialiased=False)
            self.patches[name] = ax.add_collection(pc)
        for kind, rects in (("room", self.g.rooms), ("occ", self.g.occupancy)):
            for name, r in rects.items():
                self.patches[name] = ax.add_patch(Rectangle(
                    (r[0], r[1]), r[2] - r[0], r[3] - r[1], linewidth=1.3, linestyle="--",
                    edgecolor=c["muted"]))
                if kind == "room":  # in a corner: x walks through room centres
                    pad = self.g.frame_stroke / 2 + 8
                    ax.text(max(r[0], self.g.bbox[0]) + pad, max(r[1], self.g.bbox[1]) + pad,
                            name, ha="left", va="top", color=c["fg"], fontsize=13,
                            fontweight="bold")
                else:
                    ax.text((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, name, ha="center",
                            va="center", color=c["fg"], fontsize=10)
        for r in self.g.wall_rects():
            ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], linewidth=0,
                                   facecolor=c["wall"]))
        for b, bm in self.g.beams.items():
            for s, r in bm.side_rects.items():
                ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], linewidth=0,
                                       facecolor=c["beam"], alpha=0.9 if s == bm.sides[0] else 0.6))
            x0b, y0b, x1b, y1b = bm.band
            at = (x1b + 4, (y0b + y1b) / 2)
            ax.text(at[0], at[1], b, color=c["beam"], fontsize=9, ha="left", va="center",
                    fontweight="bold")
        (self.trail_line,) = ax.plot([], [], color=c["trail"], linewidth=1.2, alpha=0.6)
        (self.dot,) = ax.plot([], [], "o", color=c["agent"], markersize=9, zorder=5)
        self.label = ax.text(0, 0, "x", color=c["agent"], fontsize=11, fontweight="bold",
                             zorder=5)
        tax = self.tax = fig.add_axes((0.63, 0.13, 0.36, 0.85))
        tax.set_axis_off()
        self.panel = tax.text(0, 1, "", va="top", ha="left", family="monospace", fontsize=9.5,
                              color=c["fg"], linespacing=1.45, transform=tax.transAxes)
        self.verdict = tax.annotate("", xy=(0, 0), xycoords=self.panel, xytext=(0, -4),
                                    textcoords="offset points", va="top", ha="left",
                                    family="monospace", fontsize=9.5, fontweight="bold")
        tax.text(0, 0.0, "keys: click = follow on/off   u = undo\n      r = reset   "
                 "m = single/multi agent", va="bottom", ha="left", family="monospace",
                 fontsize=9, color=c["muted"], transform=tax.transAxes)
        bax = fig.add_axes((0.72, 0.04, 0.26, 0.06))
        self.textbox = TextBox(bax, "claimed story ", initial=claim, color=c["box"],
                               hovercolor=c["boxhover"])
        self.textbox.label.set_color(c["fg"])
        self.textbox.text_disp.set_color(c["fg"])
        self.textbox.on_text_change(self.set_claim)
        self.claim = claim
        canvas = fig.canvas
        self._cids = [canvas.mpl_connect("motion_notify_event", self.on_motion),
                      canvas.mpl_connect("button_press_event", self.on_click),
                      canvas.mpl_connect("key_press_event", self.on_key)]
        self.reset()

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        """x back inside the start room; nothing recorded; following off."""
        self.pos: Point = self.g.feature_center(self.start)
        self.story: List[str] = [self.start]
        self.history: List[List[str]] = []
        self.trail: List[Point] = [self.pos]
        self.stack: List[Tuple[Point, int, int, int]] = []
        self.following = False
        self.refresh()

    def where(self) -> Tuple[str, Optional[str]]:
        """``(kind, name)`` of x's location (``"room"``, ``"region"``, ``"occupancy"``)."""
        return self.g.location(*self.pos)

    def move_to(self, x: float, y: float) -> bool:
        """Move x straight towards ``(x, y)``, as far as it can legally go; record what
        happens on the way.  Returns True if x moved."""
        p, q = self.pos, (float(x), float(y))
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        if length < 1e-9:
            return False
        runs = self.g.segment_runs(p, q)
        # Try the farthest run x may stop in first: the pointer itself, else just before the
        # end of an earlier room/region/occupancy run.  The stop point must lie strictly in
        # that run's place, at least GRID_TOL off every cell boundary: from a point on (or a
        # few ulps off) a boundary the next move would not see the room or occupancy
        # rectangle it starts in, and drop a room entry or an occupancy activation.
        walk = None
        for k in range(len(runs) - 1, -1, -1):
            t0, t1, kind, name = runs[k]
            if kind not in _STAND:
                continue
            mid = (t0 + t1) / 2
            back = max(mid, t1 - BACKOFF / length)
            for t in ((1.0, back, mid) if k == len(runs) - 1 else (back, mid)):
                end = (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
                if self._near_grid(end) or self.g.location(*end) != (kind, name):
                    continue
                try:
                    walk = trace_polyline(self.g, [p, end])
                    break
                except GeometryError:
                    continue
            if walk is not None:
                break
        if walk is None:
            return False
        if math.hypot(end[0] - p[0], end[1] - p[1]) < 1e-9:
            return False
        events = walk.events
        if events and events[0][0] == 0.0 and events[0][1] == "room" and self.where()[0] == "room":
            events = events[1:]  # trace_polyline reports the room it starts in
        if events:
            self.stack.append((p, len(self.trail), len(self.story), len(self.history)))
        for _, kind, name, _ in events:
            if kind == "room":
                self.story.append(name)
            else:
                self.history.append([name, "D" if kind == "leave" else "A"])
        self.pos = end
        self.trail.append(end)
        self.refresh()
        return True

    def _near_grid(self, p: Point) -> bool:
        """Is ``p`` within GRID_TOL of a cell boundary (a grid line of the decomposition)?"""
        for v, lines in zip(p, self._grid):
            k = bisect.bisect_left(lines, v)
            if (k < len(lines) and lines[k] - v < GRID_TOL) or \
                    (k > 0 and v - lines[k - 1] < GRID_TOL):
                return True
        return False

    def undo(self) -> bool:
        """Take back the last move that recorded something (room entry or recording)."""
        if not self.stack:
            return False
        self.pos, nt, ns, nh = self.stack.pop()
        del self.trail[nt:], self.story[ns:], self.history[nh:]
        self.following = False
        self.refresh()
        return True

    def toggle_agents(self) -> None:
        self.agents = "multi" if self.agents == "single" else "single"
        self.refresh()

    def set_claim(self, text: str) -> None:
        self.claim = text
        self.refresh()

    def information_state(self) -> Set[str]:
        return information_state(self.m, self.history, self.agents)

    def verdict_of_claim(self) -> Tuple[Optional[bool], str]:
        """``(consistent, message)`` for the claimed story; ``None`` if there is none or it
        cannot be read."""
        if not self.claim.strip():
            return None, "type a claimed story, e.g. %s" % "".join(self.story)
        try:
            r = validate(self.m, self.claim.strip(), self.history, agents=self.agents)
        except InputError as e:
            return None, "cannot read the story: %s" % e
        if r.consistent:
            return True, "CONSISTENT  witness: %s" % r.path_string()
        kind, name = self.where()
        if kind == "occupancy" and self.agents == "single":
            return False, ("INCONSISTENT: x is inside %s, so the history ends with an open "
                           "activation (%s+) until x leaves" % (name, name))
        return False, "INCONSISTENT: %s" % r.reason

    # ------------------------------------------------------------------ events

    def on_motion(self, event: Any) -> None:
        if self.following and event.inaxes is self.ax and event.xdata is not None:
            self.move_to(event.xdata, event.ydata)

    def on_click(self, event: Any) -> None:
        if event.inaxes is self.ax:
            self.following = not self.following
            self.refresh()

    def on_key(self, event: Any) -> None:
        if getattr(self.textbox, "capturekeystrokes", False):
            return  # typing into the text box
        if event.key == "m":
            self.toggle_agents()
        elif event.key == "u":
            self.undo()
        elif event.key == "r":
            self.reset()

    # ------------------------------------------------------------------ drawing

    def refresh(self) -> None:
        """Redraw.  The information state and the verdict are recomputed only when the
        history, the mode or the claim changed."""
        c, key = self.c, (tuple(map(tuple, self.history)), self.agents, self.claim,
                          len(self.story))
        if key != getattr(self, "_key", None):
            self._key, self._possible = key, self.information_state()
            self._verdict = self.verdict_of_claim()
            for name, patch in self.patches.items():
                base = c["occ"] if name in self.g.occupancy else (
                    c["room"] if name in self.g.rooms else c["free"])
                on = name in self._possible
                patch.set_facecolor(c["possible"] if on else base)
                patch.set_alpha(0.55 if on else 1.0)
        possible, (ok, msg) = self._possible, self._verdict
        xs, ys = zip(*self.trail)
        self.trail_line.set_data(xs, ys)
        self.dot.set_data([self.pos[0]], [self.pos[1]])
        self.label.set_position((self.pos[0] + 8, self.pos[1] - 8))
        kind, name = self.where()
        wrap = lambda s, ind="  ": textwrap.fill(s, 46, initial_indent=ind,  # noqa: E731
                                                 subsequent_indent=ind) or ind
        lines = [
            "map %s   %s agent%s" % (self.m.name, self.agents,
                                    "s" if self.agents == "multi" else ""),
            "x is in %s %s   following: %s" % (kind, name, "on" if self.following else
                                               "off (click the map)"),
            "", "true story (rooms entered):", wrap(" ".join(self.story)),
            "observation history (%d recording%s):" % (len(self.history),
                                                       "" if len(self.history) == 1 else "s"),
            wrap(_history_text(self.history, self.m) or "(none)"),
            "", "shaded = where x could be now, from the", "history alone and x starting in a room",
            "(information state):",
            wrap(", ".join(sorted(possible, key=lambda n: (_ORDER[self.m.kind(n)], n)))),
            "", "claimed story vs recorded history:",
        ]
        self.panel.set_text("\n".join(lines))
        self.verdict.set_text(textwrap.fill(msg, 46))
        self.verdict.set_color(c["muted"] if ok is None else c["ok"] if ok else c["bad"])
        if getattr(self.fig.canvas, "manager", None) is not None:  # off-screen: draw on save
            self.fig.canvas.draw_idle()

    def save(self, path: str, dpi: int = 110) -> str:
        """Write the current frame to ``path`` and return ``path``.  The format comes from the
        extension (``png``, ``svg``, ``pdf``, ...); a name without one gets a PNG under
        exactly that name (matplotlib would otherwise append ``.png``)."""
        fmt = None if os.path.splitext(path)[1] else "png"
        self.fig.savefig(path, dpi=dpi, facecolor=self.fig.get_facecolor(), format=fmt)
        return path


def interactive_backend() -> Optional[str]:
    """The name of matplotlib's current backend if it can open a window, else ``None``
    (``Agg``, ``pdf``, ``svg``, ... -- e.g. no display, or ``MPLBACKEND=agg``)."""
    import matplotlib

    name = matplotlib.get_backend()
    low = name.lower()
    try:
        from matplotlib.rcsetup import non_interactive_bk
        others = {b.lower() for b in non_interactive_bk}
    except ImportError:  # pragma: no cover - very old matplotlib
        others = set()
    if low in NON_INTERACTIVE or low in others:
        return None
    return name


def run(m: Union[Map, str], agents: str = "single", theme: str = "light",
        claim: str = "", save: Optional[str] = None) -> Viewer:
    """Open the viewer in a window (``save=None``) or write a still PNG of its first frame
    (:meth:`Viewer.save`).  Raises :class:`NoDisplayError` if ``save`` is ``None`` and
    matplotlib has no backend that can open a window."""
    if save is not None:
        v = Viewer(m, agents=agents, theme=theme, claim=claim)
        v.save(save)
        return v
    import matplotlib.pyplot as plt

    if interactive_backend() is None:
        raise NoDisplayError("no interactive matplotlib backend (current: %s); use --save PNG"
                             % plt.get_backend())

    plt.rcParams["keymap.home"] = [k for k in plt.rcParams["keymap.home"] if k != "r"]
    fig = plt.figure(figsize=(12, 6.6))
    try:
        fig.canvas.manager.set_window_title("Cyber Detectives viewer")
    except AttributeError:  # pragma: no cover - backends without a window manager
        pass
    v = Viewer(m, agents=agents, theme=theme, fig=fig, claim=claim)
    plt.show()
    return v
