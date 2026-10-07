"""Render a scripted session of the live viewer to a PNG (for the README).

The live viewer itself is interactive::

    python -m cyber_detectives view --map star_fig2      # also icra_fig1, icra_fig2
    python -m cyber_detectives view --map icra_fig2 --multi --dark --story ABCDE

(click the map to make x follow the mouse; type a claimed story in the box; keys: m = single
/ multi agent, u = undo, r = reset).  This script plays the part of the mouse: it walks x
along the witness of ``--walk-story`` / ``--walk-history`` (the engine's path, drawn by
``cyber_detectives.geometry.path_polyline``), optionally stopping after ``--stop`` waypoints,
types ``--claim`` and saves the frame.  Needs matplotlib; run from the repository root::

    python tools/viewer_snapshot.py --out viewer.png
    python tools/viewer_snapshot.py --map icra_fig2 --stop 9 --claim ABD --out viewer-icra.png
"""

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))

from cyber_detectives import builtin_map, validate  # noqa: E402
from cyber_detectives.geometry import path_polyline  # noqa: E402

DEFAULTS = {  # map -> (story, history) of a consistent walk
    "star_fig2": ("ACBAC", "b1 o1 o1 o2 o2 b2"),
    "icra_fig1": ("ACBAC", "b1 o1 o1 o2 o2 b2"),
    "icra_fig2": ("ABCDE", "b1 b3 o2 o2"),
}


def snapshot(map_name, story=None, history=None, claim=None, stop=None, agents="single",
             theme="light", out="viewer.png", dpi=110):
    """Walk x along the witness of (story, history), type ``claim``, save; returns the viewer."""
    from cyber_detectives.viewer import Viewer

    m = builtin_map(map_name)
    d_story, d_history = DEFAULTS.get(map_name, (None, None))
    story = d_story if story is None else story
    history = d_history if history is None else history
    r = validate(m, story, history, agents="single")
    if not r.consistent:
        raise SystemExit("cannot walk %s / %s: %s" % (story, history, r.reason))
    pts = path_polyline(m, r.path)[:stop]
    v = Viewer(m, agents=agents, theme=theme, start=r.path[0].position,
               claim=story if claim is None else claim)
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.dist(a, b) / 5))
        for i in range(1, n + 1):
            v.move_to(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
    v.save(out, dpi=dpi)
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map", default="star_fig2", choices=sorted(DEFAULTS))
    ap.add_argument("--walk-story", help="story of the walk (default: per map)")
    ap.add_argument("--walk-history", help="history of the walk (default: per map)")
    ap.add_argument("--stop", type=int, help="stop after this many waypoints")
    ap.add_argument("--claim", help="claimed story typed in the box (default: the walk's)")
    ap.add_argument("--multi", action="store_true", help="multi-agent mode")
    ap.add_argument("--dark", action="store_true", help="dark theme")
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--out", default="viewer.png", help="PNG to write (default: viewer.png)")
    a = ap.parse_args(argv)
    v = snapshot(a.map, a.walk_story, a.walk_history, a.claim, a.stop,
                 "multi" if a.multi else "single", "dark" if a.dark else "light", a.out, a.dpi)
    print("wrote %s: story %s, history %s; %s" % (
        a.out, "".join(v.story), " ".join(s for s, _ in v.history) or "-",
        v.verdict_of_claim()[1]))


if __name__ == "__main__":
    main()
