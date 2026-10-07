"""Regenerates docs/media/concepts.png (and concepts.svg): the key ideas in one figure.

    python3 docs/media/make_concepts.py            # from the repository root; needs matplotlib

Everything drawn comes from the package: the STAR Fig. 2 map and its geometry
(``builtin_map("star_fig2")``, ``as_geometry``), the free regions R1-R4 (the geometry's cell
decomposition), the verdicts (``validate``) and the "where x could be" sets
(``possible_positions``).  Panels:

(a) the workspace: rooms A, B, C; beams b1, b2 (sides b1u/b1d, b2l/b2r); occupancy sensors
    o1, o2; free regions R1-R4 (the connected parts of the free space outside rooms and sensors);
(b) two observation histories as timelines, for the story ACBAC: the original applet's
    feasible example (consistent) and STAR eq. (2) (inconsistent);
(c) for the consistent case, where x could be in each slot between recordings
    (``possible_positions(m, history, story)``); beams and the occupancy regions that are not
    on act as walls until a recording opens one of them for an instant;
(d) the same for STAR eq. (2): with the story every set is empty, so the panel shows the
    recordings alone (``possible_positions(m, history)``); after B, x never gets back to A.

Fonts are matplotlib's bundled DejaVu Sans, and the PNG/SVG carry no dates, so two runs with the
same matplotlib give byte-identical files.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PatchCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyArrowPatch, Patch, Rectangle  # noqa: E402

from cyber_detectives import builtin_map, parse_history, possible_positions, validate  # noqa: E402
from cyber_detectives.geometry import as_geometry  # noqa: E402

STORY = "ACBAC"
CASES = [  # (label, history, source)
    ("consistent", "b1 o1 o1 o2 o2 b2", "the original applet's feasible example"),
    ("inconsistent", "b1 o1 o1 b2 o2 o2", "STAR eq. (2)"),
]

INK, MUTED, FAINT = "#1f2328", "#57606a", "#8c959f"
FLOOR, WALL, ROOM, ROOM_EDGE = "#ffffff", "#2b2f36", "#e4e7ec", "#9aa3ae"
OCC, OCC_EDGE, OCC_ON = "#f6ead7", "#b08d57", "#f2c14e"
BEAM = "#cf222e"
POSS, POSS_EDGE = "#a8dcc8", "#137a55"
REGION_TINT = {"R1": "#dde9f9", "R2": "#ebe2f7", "R3": "#ddf1e4", "R4": "#f8dfe7"}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "svg.hashsalt": "cyber-detectives",
    "svg.fonttype": "path", "axes.unicode_minus": False,
})


def region_rects(g):
    """Free regions as merged columns of free cells: {region: [Rectangle args]}."""
    d, out = g.decomposition, {}
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
            out.setdefault(d.comp_names[d.comp[idx]], []).append(
                ((d.xs[i], d.ys[j]), d.xs[i + 1] - d.xs[i], d.ys[k + 1] - d.ys[j]))
            j = k + 1
    return out


def rect(ax, r, **kw):
    ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], **kw))


def draw_map(ax, m, g, regions, possible=(), active=(), fired=None, labels="full"):
    """One drawing of the map.  ``possible``: positions to shade; ``active``: occupancy sensors
    that are on; ``fired``: the sensor of the recording that just happened (outlined)."""
    possible = set(possible)
    x0, y0, x1, y1 = g.bbox
    pad = g.frame_stroke / 2
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y1 + pad, y0 - pad)  # y points down, as in the drawings
    ax.set_aspect("equal")
    ax.set_axis_off()
    rect(ax, g.bbox, facecolor=FLOOR, linewidth=0)
    for name, rs in regions.items():
        face = POSS if name in possible else (REGION_TINT[name] if labels == "full" else FLOOR)
        ax.add_collection(PatchCollection([Rectangle(*a) for a in rs], facecolor=face,
                                          linewidth=0, antialiased=False))
    for name, r in g.rooms.items():
        rect(ax, r, facecolor=POSS if name in possible else ROOM, edgecolor=ROOM_EDGE,
             linewidth=0.8)
    for name, r in g.occupancy.items():
        on = name in active
        face = OCC_ON if on else OCC
        rect(ax, r, facecolor=face, edgecolor=OCC_EDGE, linewidth=0.8,
             linestyle="-" if on else (0, (4, 3)))
        if name in possible:  # x is inside the region of an active sensor
            rect(ax, r, facecolor="none", edgecolor=POSS_EDGE, linewidth=2.2)
        elif not on and labels != "full":  # an inactive occupancy region is a wall for now
            rect(ax, r, facecolor="none", edgecolor=BEAM, linewidth=0, hatch="////", alpha=0.35)
    for r in g.wall_rects():
        rect(ax, r, facecolor=WALL, linewidth=0)
    for b, bm in g.beams.items():
        (ax_, ay), (bx, by) = bm.line
        ax.plot([ax_, bx], [ay, by], color=BEAM, linewidth=2.6 if labels == "full" else 2.0,
                solid_capstyle="butt", zorder=4)
    if fired:
        if fired in g.occupancy:
            rect(ax, g.occupancy[fired], facecolor="none", edgecolor=INK, linewidth=1.6, zorder=5)
        else:
            (ax_, ay), (bx, by) = g.beams[fired].line
            ax.plot([ax_, bx], [ay, by], color=BEAM, linewidth=7, alpha=0.35, zorder=3,
                    solid_capstyle="butt")
    for name, r in g.rooms.items():
        fs = 15 if labels == "full" else 9
        ax.text(r[0] + pad + 10, r[1] + pad + 8, name, ha="left", va="top", fontsize=fs,
                fontweight="bold", color=INK, zorder=6)
    if labels != "full":
        return
    for name, r in g.occupancy.items():
        ax.text(r[0] + 10, r[3] - 10, name, ha="left", va="bottom", fontsize=11,
                fontweight="bold", color="#6b4f1d", zorder=6)
    for name in m.regions:
        x, y = g.region_point(name)
        if name == "R3":
            y = 360
        ax.text(x, y, name, ha="center", va="center", fontsize=11, style="italic", color=MUTED,
                zorder=6)
    for b, bm in g.beams.items():
        (ax_, ay), (bx, by) = bm.line
        if bm.horizontal:  # b1: sides up / down
            ax.text((ax_ + bx) / 2, ay - 9, bm.sides[0], ha="center", va="bottom", fontsize=8.5,
                    color=BEAM, zorder=6)
            ax.text((ax_ + bx) / 2, ay + 9, bm.sides[1], ha="center", va="top", fontsize=8.5,
                    color=BEAM, zorder=6)
            ax.text(ax_ - 2, ay, b + " ", ha="right", va="center", fontsize=10,
                    fontweight="bold", color=BEAM, zorder=6)
        else:  # b2: sides left / right, the name above them
            ym = (ay + by) / 2 + 12
            ax.text(ax_ - 8, ym, bm.sides[0], ha="right", va="center", fontsize=8.5,
                    color=BEAM, zorder=6)
            ax.text(ax_ + 8, ym, bm.sides[1], ha="left", va="center", fontsize=8.5,
                    color=BEAM, zorder=6)
            ax.text(ax_ - 8, ym - 24, b, ha="right", va="center", fontsize=10,
                    fontweight="bold", color=BEAM, zorder=6)

def active_after(m, events, h):
    """Occupancy sensors that are on after the first h recordings."""
    on = set()
    for e in events[:h]:
        if m.is_occupancy(e.sensor):
            (on.add if e.kind == "A" else on.discard)(e.sensor)
    return on


def rec_label(m, e):
    return e.sensor if m.is_beam(e.sensor) else e.sensor + (" on" if e.kind == "A" else " off")


def draw_timeline(ax, m, events, y, title, verdict, ok):
    """One history as lanes (b1, b2, o1, o2) over time: recording k sits at x = k, slot h is
    the interval [h, h + 1] between recordings h and h + 1."""
    n = len(events)
    lanes = list(m.beams) + list(m.occupancy)
    lane_y = {s: y + (len(lanes) - 1 - i) * 0.42 for i, s in enumerate(lanes)}
    top, bottom = y + (len(lanes) - 1) * 0.42 + 0.24, y - 0.24
    for h in range(n + 1):
        ax.add_patch(Rectangle((h, bottom), 1.0, top - bottom, linewidth=0, zorder=0,
                               facecolor="#f3f5f8" if h % 2 == 0 else "#fbfcfd"))
        ax.text(h + 0.5, top + 0.06, "slot %d" % h, ha="center", va="bottom", fontsize=8,
                color=FAINT)
    for s_, ly in lane_y.items():
        ax.text(-0.12, ly, s_, ha="right", va="center", fontsize=9, color=INK)
        ax.plot([0, n + 1], [ly, ly], color="#d0d7de", linewidth=0.6, zorder=1)
    on_since = {}
    for k, e in enumerate(events, start=1):
        ly = lane_y[e.sensor]
        if m.is_beam(e.sensor):
            ax.plot([k, k], [ly - 0.15, ly + 0.15], color=BEAM, linewidth=2.4, zorder=3)
        elif e.kind == "A":
            on_since[e.sensor] = k
        else:
            k0 = on_since.pop(e.sensor)
            ax.add_patch(Rectangle((k0, ly - 0.11), k - k0, 0.22, facecolor=OCC_ON,
                                   edgecolor=OCC_EDGE, linewidth=0.8, zorder=2))
        ax.text(k, bottom - 0.06, rec_label(m, e), ha="center", va="top", fontsize=8.8,
                color=MUTED)
    ax.text(-0.75, top + 0.42, title, ha="left", va="bottom", fontsize=10, color=INK)
    ax.text(n + 1, top + 0.42, verdict, ha="right", va="bottom", fontsize=10,
            fontweight="bold", color=POSS_EDGE if ok else BEAM)


class Layout:
    """Axes placed in inches from the top-left corner of the figure."""

    def __init__(self, fig, w, h):
        self.fig, self.w, self.h = fig, w, h

    def rect(self, x, y, w, h):
        return (x / self.w, 1 - (y + h) / self.h, w / self.w, h / self.h)

    def axes(self, x, y, w, h):
        return self.fig.add_axes(self.rect(x, y, w, h))

    def text(self, x, y, s, **kw):
        kw.setdefault("va", "top")
        kw.setdefault("ha", "left")
        return self.fig.text(x / self.w, 1 - y / self.h, s, **kw)


def map_aspect(g):
    """Height / width of a map drawing (the frame included)."""
    x0, y0, x1, y1 = g.bbox
    return (y1 - y0 + g.frame_stroke) / (x1 - x0 + g.frame_stroke)


def strip(L, x0, y0, pw, gap, m, g, regions, events, sets, title, note):
    """Small maps, one per slot, with the recording that separates consecutive slots; returns
    the height used (inches)."""
    ph = pw * map_aspect(g)
    L.text(x0, y0, title, fontsize=10.5, color=INK)
    L.text(x0, y0 + 0.25, note, fontsize=9.3, color=MUTED, linespacing=1.35)
    ytop = y0 + 0.25 + 0.18 * (note.count("\n") + 1) + 0.3
    for h, ps in enumerate(sets):
        x = x0 + h * (pw + gap)
        ax = L.axes(x, ytop, pw, ph)
        fired = events[h - 1].sensor if h else None
        draw_map(ax, m, g, regions, ps, active_after(m, events, h), fired, labels="small")
        L.text(x + pw / 2, ytop - 0.03, "slot %d" % h, ha="center", va="bottom", fontsize=8.5,
               color=FAINT)
        L.text(x + pw / 2, ytop + ph + 0.05, "{" + ", ".join(ps) + "}" if ps else "{ }",
               ha="center", fontsize=9.5, color=INK)
        if h:
            xa, xb, ym = x - gap + 0.04, x - 0.04, ytop + ph / 2 + 0.08
            L.fig.add_artist(FancyArrowPatch(L.rect(xa, ym, 0, 0)[:2], L.rect(xb, ym, 0, 0)[:2],
                                             transform=L.fig.transFigure, arrowstyle="-|>",
                                             mutation_scale=8, color=MUTED, linewidth=0.9))
            L.text(x - gap / 2, ym - 0.05, rec_label(m, events[h - 1]), ha="center",
                   va="bottom", fontsize=8.3, color=INK, fontweight="bold")
    return ytop + ph + 0.45 - y0


def main(out_png=os.path.join(HERE, "concepts.png"), out_svg=os.path.join(HERE, "concepts.svg")):
    m = builtin_map("star_fig2")
    g = as_geometry(m)
    regions = region_rects(g)
    aspect = map_aspect(g)
    hist = {label: parse_history(h, m) for label, h, _ in CASES}
    res = {label: validate(m, STORY, h) for label, h, _ in CASES}
    assert res["consistent"].consistent and not res["inconsistent"].consistent
    with_story = {label: possible_positions(m, h, STORY) for label, h, _ in CASES}
    alone = possible_positions(m, CASES[1][1])
    assert not any(with_story["inconsistent"])  # inconsistent: every set is empty

    W, H, M = 11.0, 9.95, 0.3
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor("white")
    L = Layout(fig, W, H)

    # (a) the map
    mw = 5.0
    L.text(M, 0.18, "(a) The workspace of STAR Fig. 2 (= ICRA Fig. 1)", fontsize=11, color=INK)
    ax = L.axes(M, 0.5, mw, mw * aspect)
    draw_map(ax, m, g, regions)
    ycap = 0.5 + mw * aspect + 0.1
    L.text(M, ycap, "Rooms A, B, C; beam detectors b1, b2 (each side named);\n"
           "occupancy sensors o1, o2 (dashed). The free space outside the\n"
           "rooms and sensors falls into four connected regions R1-R4.",
           fontsize=9.3, color=MUTED, linespacing=1.35)

    # (b) the histories as timelines
    bx = M + mw + 0.75
    L.text(bx - 0.35, 0.18, "(b) Story %s against two observation histories" % STORY,
           fontsize=11, color=INK)
    tl = L.axes(bx, 0.5, W - M - bx, mw * aspect)
    tl.set_axis_off()
    n = len(hist["consistent"])
    tl.set_xlim(-0.75, n + 1.05)
    tl.set_ylim(-0.85, 5.15)
    for k, (label, h, src) in enumerate(CASES):
        r = res[label]
        draw_timeline(tl, m, hist[label], 3.05 - k * 3.0, src[0].upper() + src[1:],
                      "consistent" if r.consistent else "inconsistent", r.consistent)
    L.text(bx - 0.35, ycap, "A beam records a crossing (tick); an occupancy sensor is on\n"
           "(bar) while someone is inside its region. The time between two\n"
           "recordings is a slot; validate() gives the verdicts.", fontsize=9.3, color=MUTED,
           linespacing=1.35)

    # (c), (d) where x could be, slot by slot
    pw = 1.12
    gap = (W - 2 * M - (n + 1) * pw) / n
    y = ycap + 0.8
    y += strip(L, M, y, pw, gap, m, g, regions, hist["consistent"], with_story["consistent"],
               "(c) Where x could be in each slot: story %s, history %s (consistent)"
               % (STORY, CASES[0][1]),
               "possible_positions(m, history, story): the positions of all consistent walks. "
               "Between recordings every beam and every\noccupancy region that is off "
               "(hatched) acts as a wall; a recording opens one of them for an instant "
               "(outlined), then it closes again.")
    y += strip(L, M, y, pw, gap, m, g, regions, hist["inconsistent"], alone,
          "(d) STAR eq. (2): story %s, history %s (inconsistent)" % (STORY, CASES[1][1]),
          "With the story every set is empty, so these are the recordings alone, "
          "possible_positions(m, history). After b2, x is in B or R4,\nand no later set "
          "reaches A: the story cannot go on B, A, C.\nvalidate(): \u201c%s.\u201d"
          % res["inconsistent"].reason.replace("every recording can be explained, but ", ""))
    handles = [Patch(facecolor=POSS, edgecolor=POSS_EDGE, label="x could be here"),
               Patch(facecolor=ROOM, edgecolor=ROOM_EDGE, label="room"),
               Patch(facecolor=OCC_ON, edgecolor=OCC_EDGE, label="occupancy sensor on"),
               Patch(facecolor=OCC, edgecolor=BEAM, hatch="////", linewidth=0,
                     label="occupancy sensor off (a wall for now)"),
               Line2D([], [], color=BEAM, linewidth=2.6,
                      label="beam detector (a wall between recordings)")]
    fig.legend(handles=handles, loc="upper center", ncol=5, frameon=False, fontsize=9.3,
               bbox_to_anchor=(0.5, 1 - (y - 0.2) / H), handlelength=1.6, columnspacing=1.6)
    fig.savefig(out_png, dpi=150, facecolor="white", metadata={"Software": None})
    if out_svg:
        fig.savefig(out_svg, facecolor="white", metadata={"Date": None, "Creator": None})
    plt.close(fig)
    for p in (out_png, out_svg):
        if p:
            print("wrote %s (%d KB)" % (os.path.relpath(p), (os.path.getsize(p) + 1023) // 1024))


if __name__ == "__main__":
    main()
