"""Regenerates docs/media/viewer.png: a frame of the live matplotlib viewer.

    python3 docs/media/make_viewer_png.py          # from the repository root; needs matplotlib

A thin wrapper around ``tools/viewer_snapshot.py``, which drives the real viewer
(``cyber_detectives.viewer.Viewer``) off-screen.  Here it walks x on the STAR Fig. 2 map along
the witness of story ACBAC / history b1 o1 o1 o2 o2 b2 and stops after 25 waypoints, inside
room B: x has recorded b1 o1+ o1- o2+ o2- and entered A, C, B.  The claimed story typed in
the box is ACB (consistent so far).  The shading is the viewer's information state from the
recordings alone, {B, R3, R4}: after o2 switched off, x may have left o2 into R3 as well.

Same as ``python3 tools/viewer_snapshot.py --stop 25 --claim ACB --out docs/media/viewer.png``.
Two runs with the same matplotlib give a byte-identical PNG.
"""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, os.pardir, os.pardir, "tools", "viewer_snapshot.py")


def main(out=os.path.join(HERE, "viewer.png")):
    import matplotlib

    matplotlib.use("Agg")
    spec = importlib.util.spec_from_file_location("viewer_snapshot", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    v = tool.snapshot("star_fig2", stop=25, claim="ACB", out=out)
    print("wrote %s (%d KB): story %s, history %s; %s" % (
        os.path.relpath(out), (os.path.getsize(out) + 1023) // 1024, "".join(v.story),
        " ".join(s for s, _ in v.history), v.verdict_of_claim()[1]))


if __name__ == "__main__":
    main()
