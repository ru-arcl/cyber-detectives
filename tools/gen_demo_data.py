#!/usr/bin/env python3
"""Generate ``docs/js/data.js``, the static data of the browser demo, from the Python package.

    python3 tools/gen_demo_data.py            # (re)write docs/js/data.js
    python3 tools/gen_demo_data.py --check    # exit 1 if docs/js/data.js is out of date
    python3 tools/gen_demo_data.py --stdout   # print instead of writing

``data.js`` is a classic script (no ES module, so the demo works from ``file://``) that
assigns one object to ``globalThis.CD_DATA`` (= ``window.CD_DATA`` in a browser).  It holds
data only; its header comment documents the exact shape (:data:`HEADER`).  For each builtin
map with a drawing it has:

- the map (rooms, beams with sides, occupancy, regions, the graph ``G``) and its provenance;
- the drawing: bounding box, walls (raw strokes and the filled rectangles they cover),
  room and occupancy rectangles, beams (strokes, band, which half/stroke is which side, side
  label points), region label points and portals;
- precomputed collision-free routes, so the demo never does geometry: inside every region a
  polyline between every two of its *anchors* (the region's label point and the portal of
  every feature it touches), the polyline from every room's / occupancy region's interior
  point to its portal in every region it touches, and every beam crossing.

:func:`polyline_for_path` turns an engine witness (a list of ``Step``) into a polyline using
only that data; the demo's JS port must do exactly the same (the presets carry the expected
polylines).  ``tests/test_demo_data.py`` checks that the polylines are legal motions through
the right features and that ``data.js`` is up to date.

``star_fig2`` is presented once: ``icra_fig1`` is the same workspace (ICRA Fig. 1 = STAR
Fig. 2; ``docs/notes/map-geometry.md``), and the generator checks that before leaving it out.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

import cyber_detectives as cd  # noqa: E402
from cyber_detectives import geometry as G  # noqa: E402
from cyber_detectives.history import history_to_string, parse_history  # noqa: E402
from cyber_detectives.problems import INTERVAL_CASES, case2_procedure_as_printed  # noqa: E402

OUT_PATH = os.path.join(ROOT, "docs", "js", "data.js")
SCHEMA = 1
DEMO_MAPS = ("star_fig2", "icra_fig2")
LABEL = "@label"  # anchor id of a region's label point (never a map name: '@' is not allowed)
SIDE_LABEL_GAP = 10.0  # distance of a beam-side label from the beam band

Point = List[float]


def _num(v: float) -> Any:
    """Round for JSON (2 decimals; integral values become ints)."""
    r = round(float(v), 2)
    return int(r) if r == int(r) else r


def _p(p: Sequence[float]) -> Point:
    return [_num(p[0]), _num(p[1])]


def _xywh(r: Sequence[float]) -> List[Any]:
    """Internal ``(x0, y0, x1, y1)`` -> ``[x, y, w, h]``."""
    return [_num(r[0]), _num(r[1]), _num(r[2] - r[0]), _num(r[3] - r[1])]


def _dedup(pts: Sequence[Sequence[float]]) -> List[Point]:
    out: List[Point] = []
    for p in pts:
        q = _p(p)
        if not out or out[-1] != q:
            out.append(q)
    return out


# ---------------------------------------------------------------------- maps

def _check_icra_fig1_is_star_fig2() -> None:
    """``icra_fig1`` is left out because it duplicates ``star_fig2``; make sure it does."""
    a, b = cd.builtin_map("star_fig2"), cd.builtin_map("icra_fig1")
    same = (list(a.rooms) == list(b.rooms) and list(a.occupancy) == list(b.occupancy)
            and {k: list(v) for k, v in a.beams.items()} == {k: list(v) for k, v in b.beams.items()}
            and {r: sorted(fs) for r, fs in a.regions.items()}
            == {r: sorted(fs) for r, fs in b.regions.items()}
            and a.geometry == b.geometry)
    if not same:
        raise SystemExit("icra_fig1 is no longer a copy of star_fig2: add it to DEMO_MAPS")


def _map_dict(m: "cd.Map") -> Dict[str, Any]:
    d = m.to_dict()
    d.pop("geometry", None)
    d.pop("provenance", None)
    d.pop("title", None)
    d.pop("name", None)
    d.pop("source", None)
    return d


def _drawing(m: "cd.Map", g: "G.Geometry") -> Dict[str, Any]:
    raw = g.raw
    beams: Dict[str, Any] = {}
    for b, sides in m.beams.items():
        bm = g.beams[b]
        spec = raw["beams"][b]
        one_segment = "segment" in spec
        (x1, y1), (x2, y2) = bm.line
        band = bm.band
        labels = {}
        for s in sides:
            r = bm.side_rects[s]
            nx, ny = bm.normals[s]
            cx, cy = (r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0
            if nx:  # vertical beam: label left/right of the band
                lx = (band[2] if nx > 0 else band[0]) + nx * SIDE_LABEL_GAP
                labels[s] = _p((lx, cy))
            else:
                ly = (band[3] if ny > 0 else band[1]) + ny * SIDE_LABEL_GAP
                labels[s] = _p((cx, ly))
        entry: Dict[str, Any] = {
            "sides": list(sides),
            "form": "one-segment" if one_segment else "two-sided",
            "stroke": _num(spec.get("stroke", 1)),
            "cap": spec.get("cap", "square"),
        }
        if one_segment:
            entry["segment"] = [_num(v) for v in spec["segment"]]
            entry["half"] = {s: spec["sides"][s] for s in sides}
        else:
            entry["side_segments"] = {s: [_num(v) for v in spec["sides"][s]] for s in sides}
        entry.update({
            "line": [_num(x1), _num(y1), _num(x2), _num(y2)],
            "band": _xywh(band),
            "side_rects": {s: _xywh(bm.side_rects[s]) for s in sides},
            "normals": {s: [_num(v) for v in bm.normals[s]] for s in sides},
            "side_labels": labels,
        })
        beams[b] = entry
    portals = g.portals()
    return {
        "units": raw.get("units", ""),
        "size": [_num(v) for v in g.size],
        "bounding_box": {"rect": [_num(v) for v in raw["bounding_box"]["rect"]],
                         "stroke": _num(raw["bounding_box"].get("stroke", 0))},
        "walls": {"stroke": _num(g.wall_stroke), "cap": g.wall_cap,
                  "segments": [[_num(v) for v in s] for s in g.wall_segments]},
        "wall_rects": [_xywh(r) for r in g.wall_rects()],
        "rooms": {a: _xywh(g.rooms[a]) for a in m.rooms},
        "occupancy": {o: _xywh(g.occupancy[o]) for o in m.occupancy},
        "beams": beams,
        "regions": {r: {"point": _p(g.region_point(r))} for r in m.regions},
        "portals": {r: {f: {"region_point": _p(portals[r][f]["region_point"]),
                            "feature_point": _p(portals[r][f]["feature_point"])}
                        for f in m.regions[r]}
                    for r in m.regions},
    }


CLEARANCE = 3.0  # preferred distance of a drawn route from walls, beams, rooms


def _in_region(g: "G.Geometry", p: Sequence[float], q: Sequence[float], region: str) -> bool:
    return all((k, n) == ("region", region)
               for _, _, k, n in g.segment_runs(tuple(p), tuple(q)))


def _clear(g: "G.Geometry", p: Sequence[float], q: Sequence[float]) -> bool:
    return g.segment_is_free(tuple(p), tuple(q), margin=CLEARANCE)


def _with_clearance(g: "G.Geometry", pts: List[Point], region: str) -> List[Point]:
    """Bend route segments that pass closer than ``CLEARANCE`` to an obstacle around the
    offending obstacle's corners (``Geometry.route`` lets the first and last segment graze
    corners).  Only cosmetic: every segment stays inside ``region``."""

    def fix(p: Point, q: Point, level: int) -> List[Point]:
        if level > 3 or _clear(g, p, q):
            return [q]
        best: Optional[Tuple[float, Point]] = None
        for r in g.obstacle_rects():
            x0, y0, x1, y1 = (r[0] - CLEARANCE - 0.5, r[1] - CLEARANCE - 0.5,
                              r[2] + CLEARANCE + 0.5, r[3] + CLEARANCE + 0.5)
            for c in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                c = _p(c)
                if g.region_at(*c) != region or not (_in_region(g, p, c, region)
                                                     and _in_region(g, c, q, region)):
                    continue
                score = (math.dist(p, c) + math.dist(c, q)
                         + 1000.0 * (not _clear(g, p, c)) + 1000.0 * (not _clear(g, c, q)))
                if best is None or score < best[0]:
                    best = (score, c)
        if best is None:
            return [q]
        c = best[1]
        return fix(p, c, level + 1) + fix(c, q, level + 1)

    out = [pts[0]]
    for p, q in zip(pts, pts[1:]):
        out += fix(p, q, 0)
    out = _dedup(out)
    # drop waypoints that a clear straight segment makes unnecessary
    i = 0
    while i + 2 < len(out):
        if _clear(g, out[i], out[i + 2]) and _in_region(g, out[i], out[i + 2], region):
            del out[i + 1]
        else:
            i += 1
    return out


def _routes(m: "cd.Map", g: "G.Geometry") -> Dict[str, Any]:
    portals = g.portals()
    out: Dict[str, Any] = {}
    for r, feats in m.regions.items():
        anchors: List[Tuple[str, Point]] = [(LABEL, _p(g.region_point(r)))]
        anchors += [(f, _p(portals[r][f]["region_point"])) for f in feats]
        paths: Dict[str, Dict[str, List[Point]]] = {a: {} for a, _ in anchors}
        for i, (a, pa) in enumerate(anchors):
            for b, pb in anchors[i + 1:]:
                if pa == pb:
                    raise SystemExit("%s: anchors %s and %s coincide in %s" % (m.name, a, b, r))
                route = g.route(tuple(pa), tuple(pb), r)
                pts = [pa] + [_p(q) for q in route[1:-1]] + [pb]
                pts = _with_clearance(g, _dedup(pts), r)
                paths[a][b] = pts
                paths[b][a] = pts[::-1]
        out[r] = {"anchors": {a: p for a, p in anchors}, "paths": paths}
    return out


def _interiors(m: "cd.Map", g: "G.Geometry") -> Dict[str, Any]:
    portals = g.portals()
    out: Dict[str, Any] = {}
    for f in list(m.rooms) + list(m.occupancy):
        c = _p(g.feature_center(f))
        out[f] = {"point": c,
                  "to": {r: _dedup([c, portals[r][f]["feature_point"],
                                    portals[r][f]["region_point"]])
                         for r in m.regions_of[f]}}
    return out


def _crossings(m: "cd.Map", g: "G.Geometry") -> Dict[str, Any]:
    portals = g.portals()
    out: Dict[str, Any] = {}
    for b, sides in m.beams.items():
        for s in sides:
            o = m.other_side(s)
            ra, rb = m.side_region(s), m.side_region(o)
            pa, pb = portals[ra][s], portals[rb][o]
            out[s] = {"beam": b, "to_side": o, "from_region": ra, "to_region": rb,
                      "points": _dedup([pa["region_point"], pa["feature_point"],
                                        pb["feature_point"], pb["region_point"]])}
    return out


_SUMMARY = {
    "star_fig2": "Original applet geometry (Environment.createExampleEnvironment, "
                 "arc-l/cyber-detective@55f57f8), coordinates verbatim; the workspace of "
                 "STAR Fig. 2, which is also ICRA Fig. 1.",
    "icra_fig2": "Measured from ICRA 2011 Fig. 2 (600-dpi render, every wall, doorway and "
                 "beam rounded to 5 units); beams drawn as one segment across the doorway.",
}

_TITLES = {
    "star_fig2": "STAR Fig. 2 = ICRA Fig. 1",
    "icra_fig2": "ICRA Fig. 2",
}


def build_map_entry(name: str) -> Dict[str, Any]:
    m = cd.builtin_map(name)
    g = G.as_geometry(m)
    gfile = G.builtin_geometry(name)
    entry: Dict[str, Any] = {
        "name": name,
        "title": _TITLES[name],
        "long_title": m.title,
        "also": ["icra_fig1"] if name == "star_fig2" else [],
        "provenance": {
            "summary": _SUMMARY[name],
            "geometry": gfile.meta.get("provenance", ""),
            "geometry_notes": gfile.meta.get("notes", ""),
            "map": m.provenance or {},
        },
        "map": _map_dict(m),
        "drawing": _drawing(m, g),
        "routes": _routes(m, g),
        "interiors": _interiors(m, g),
        "crossings": _crossings(m, g),
    }
    return entry


# ---------------------------------------------------------------------- polylines

def _step_fields(step: Any) -> Tuple[str, str, Optional[str]]:
    if isinstance(step, Mapping):
        return step["kind"], step["position"], step.get("sensor")
    return step.kind, step.position, step.sensor


def polyline_for_path(map_entry: Mapping[str, Any], steps: Sequence[Any]) -> List[Point]:
    """Polyline of a witness walk, built ONLY from ``map_entry`` (``CD_DATA.maps[name]``).

    ``steps`` are engine ``Step`` objects or their ``to_dict()`` forms (``kind``,
    ``position``, ``sensor``), from ``validate`` or Problems 2-4 (whose extra kinds ``begin``,
    ``mark``, ``pass``, ``unseen`` are handled too).  The demo's JS mirrors this exactly:

    - x stands at an *anchor*: a room/occupancy interior point, or inside a region at one of
      the region's anchors (``"@label"`` or a feature name = that feature's portal).
    - first step: start at ``interiors[pos].point`` (room/occupancy) or the region's label
      point (anchor ``"@label"``).
    - each next step, with ``P`` = previous position and ``Q`` = ``step.position``:
      * ``kind`` is ``"cross"`` or ``"pass"`` (``sensor`` = side ``s`` crossed from): append
        ``routes[P].paths[a][s]`` (unless ``a == s``), then ``crossings[s].points``; the
        anchor becomes ``crossings[s].to_side`` in region ``Q``;
      * else if ``Q == P``: nothing (e.g. a ``mark``);
      * else if ``P`` is a room/occupancy and ``Q`` a region: append ``interiors[P].to[Q]``
        (interior -> doorway -> portal); anchor ``P`` in region ``Q``;
      * else if ``P`` is a region and ``Q`` a room/occupancy: append
        ``routes[P].paths[a][Q]`` (unless ``a == Q``), then ``interiors[Q].to[P]`` reversed.
    - if the walk ends in a region, append the route from the anchor to ``"@label"``.
    - "append" skips a point equal to the last one (exact comparison: every point comes from
      the same rounded data).

    Raises ``ValueError`` on a transition the data has no route for.
    """
    routes = map_entry["routes"]
    interiors = map_entry["interiors"]
    crossings = map_entry["crossings"]
    pts: List[Point] = []

    def add_all(seq: Sequence[Point]) -> None:
        for p in seq:
            if not pts or pts[-1][0] != p[0] or pts[-1][1] != p[1]:
                pts.append([p[0], p[1]])

    def route(region: str, a: str, b: str) -> None:
        if a == b:
            return
        try:
            add_all(routes[region]["paths"][a][b])
        except KeyError:
            raise ValueError("no route in %s from %s to %s" % (region, a, b)) from None

    if not steps:
        return pts
    _, pos, _ = _step_fields(steps[0])
    anchor: Optional[str] = None
    if pos in interiors:
        add_all([interiors[pos]["point"]])
    elif pos in routes:
        anchor = LABEL
        add_all([routes[pos]["anchors"][LABEL]])
    else:
        raise ValueError("unknown start position %r" % (pos,))
    for step in steps[1:]:
        kind, q, sensor = _step_fields(step)
        if kind in ("cross", "pass"):
            c = crossings.get(sensor)
            if c is None or c["from_region"] != pos or c["to_region"] != q or anchor is None:
                raise ValueError("cannot cross from %r at %r into %r" % (pos, sensor, q))
            route(pos, anchor, sensor)
            add_all(c["points"])
            anchor = c["to_side"]
        elif q == pos:
            continue
        elif pos in interiors and q in routes:
            try:
                add_all(interiors[pos]["to"][q])
            except KeyError:
                raise ValueError("%s does not open into %s" % (pos, q)) from None
            anchor = pos
        elif pos in routes and q in interiors:
            if pos not in interiors[q]["to"]:
                raise ValueError("%s does not open into %s" % (q, pos))
            route(pos, anchor, q)  # type: ignore[arg-type]
            add_all(interiors[q]["to"][pos][::-1])
            anchor = None
        else:
            raise ValueError("no move from %r to %r (step kind %r)" % (pos, q, kind))
        pos = q
    if pos in routes:
        route(pos, anchor, LABEL)  # type: ignore[arg-type]
    return pts


# ---------------------------------------------------------------------- presets

# (id, title, map, problem, agents, story, history (events), options, caption, ref, bug)
_E = List[List[str]]
EQ2: _E = [["b1", "A"], ["o1", "A"], ["o1", "D"], ["b2", "A"], ["o2", "A"], ["o2", "D"]]
EQ3: _E = [["b1", "A"], ["o1", "A"], ["o2", "A"], ["b2", "A"], ["o2", "D"], ["o1", "D"]]
FIG1: _E = [["b2", "A"], ["o2", "A"], ["o2", "D"], ["o1", "A"], ["o1", "D"]]
SEC3A: _E = [["b1", "A"], ["b3", "A"], ["o2", "A"], ["o2", "D"], ["b4", "A"]]
SEC5A: _E = [["b1", "A"], ["b2", "A"], ["o2", "A"], ["o2", "D"], ["b4", "A"]]

PRESETS: List[Dict[str, Any]] = [
    dict(id="star_eq1_eq2_single", title="STAR eq. (1) + (2), single agent",
         map="star_fig2", problem=1, agents="single", story="ACBAC", events=EQ2,
         caption="Story ACBAC against history (2): no single agent can tell it. After B "
                 "(region R4) only o2 fires again, so A is never reachable.",
         ref="STAR §2.3 eq. (1)-(2), p. 395-396; Figs. 4-5"),
    dict(id="star_eq1_eq3_multi", title="STAR eq. (1) + (3), multiple agents",
         map="star_fig2", problem=1, agents="multi", story="ACBAC", events=EQ3,
         caption="Same story against history (3) with other agents around: consistent. "
                 "x crosses no beam; while o1 and o2 are both active it walks through them "
                 "to B and back.",
         ref="STAR §5, eq. (1), (3), p. 396 and 402-404; Figs. 6-7"),
    dict(id="icra_fig1_ABAC", title="ICRA Fig. 1: story A, B, A, C",
         map="star_fig2", problem=1, agents="single", story="ABAC", events=FIG1,
         caption="The example of ICRA Fig. 1 (the STAR Fig. 2 workspace): A, B, A, C "
                 "triggering b2, o2, o1 is consistent.",
         ref="ICRA Fig. 1 caption, p. 4980"),
    dict(id="icra_sec3a_p1", title="ICRA §III-A: ABDEC, Problem 1",
         map="icra_fig2", problem=1, agents="single", story="ABDEC", events=SEC3A,
         caption="Story ABDEC, history b1 b3 o2 o2 b4: inconsistent when every room visit "
                 "is reported (STAR §2.1): to cross b4 after E, x must enter D again.",
         ref="ICRA §III-A, p. 4982; Fig. 6 caption"),
    dict(id="icra_sec3a_p1_unreported", title="ICRA §III-A: ABDEC, visits may go unreported",
         map="icra_fig2", problem=1, agents="single", story="ABDEC", events=SEC3A,
         options={"unreported_visits": True},
         caption="ICRA drops STAR's rule that every room visit is reported. Under that "
                 "reading the same input is consistent: x passes A and D without telling.",
         ref="ICRA §II-A, §III-A, p. 4981-4982"),
    dict(id="icra_sec3a_p3", title="ICRA §III-A: ABDEC, Problem 3",
         map="icra_fig2", problem=3, agents="single", story="ABDEC", events=SEC3A,
         options={"anchored": True},
         caption="Problem 3 (partial story): the shortest consistent super-story of ABDEC "
                 "is ABDEDC (one inserted visit).",
         ref="ICRA §III-A, p. 4982; §V-A, p. 4983-4984"),
    dict(id="icra_sec3a_p4", title="ICRA §III-A: ABDEC, Problem 4",
         map="icra_fig2", problem=4, agents="single", story="ABDEC", events=SEC3A,
         caption="Problem 4 (story with errors): one edit (for example E -> D) makes the story "
                 "consistent.",
         ref="ICRA §III-A, p. 4982; §V-B, p. 4984"),
    dict(id="icra_sec5a_p1", title="ICRA §V-A: ABDEC, Problem 1",
         map="icra_fig2", problem=1, agents="single", story="ABDEC", events=SEC5A,
         caption="Story ABDEC, history b1 b2 o2 o2 b4: inconsistent as told; as in "
                 "§III-A, x would have to enter D again between E and C.",
         ref="ICRA §V-A, p. 4983-4984"),
    dict(id="icra_sec5a_p3", title="ICRA §V-A: ABDEC, Problem 3",
         map="icra_fig2", problem=3, agents="single", story="ABDEC", events=SEC5A,
         options={"anchored": True},
         caption="Problem 3: the shortest consistent super-story is ABDEDC; the paper "
                 "gives only its form w1 A w2 B w3 D w4 E w5 C w6.",
         ref="ICRA §V-A, p. 4983-4984"),
    dict(id="icra_sec5a_p4", title="ICRA §V-A: ABDEC, Problem 4",
         map="icra_fig2", problem=4, agents="single", story="ABDEC", events=SEC5A,
         caption="Problem 4: one edit suffices (several optimal stories exist; the engine "
                 "returns one deterministically).",
         ref="ICRA §V-A/B, p. 4983-4984"),
    dict(id="icra_p2_case2", title="ICRA Problem 2, case 2: counterexample",
         map="icra_fig2", problem=2, agents="single", story="AB",
         events=[["b1", "A"], ["b3", "A"]], options={"case": 2},
         caption="Story interval [t0, tf] starts first, sensors' [t0', tf'] ends last. "
                 "Consistent (x leaves B after tf, unseen by the story, and crosses b3), but "
                 "the paper's case-2 procedure returns false.",
         ref="ICRA §III-B case 2, p. 4983 (derived example)"),
    dict(id="bug_B1", title="Original bug B1: multi-agent false negative",
         map="star_fig2", problem=1, agents="multi", story="BC",
         events=[["o1", "A"], ["b2", "A"], ["o1", "D"]],
         caption="Another agent opens o1; x crosses b2 into R2, walks through o1 to C. The "
                 "original says false: it handles the o1 deactivation as if o1 were closed "
                 "the whole time.",
         ref="derived from STAR §5; docs/notes/original-bugs.md B1",
         bug={"id": "B1", "explanation": "a deactivation loses the agent's progress: moves "
                                         "made while o1 was open are forgotten"}),
    dict(id="bug_B3", title="Original bug B3: path with an extra crossing",
         map="star_fig2", problem=1, agents="single", story="AC",
         events=[["b1", "A"], ["b1", "A"], ["b1", "A"]],
         caption="Consistent. The original's 'possible path' crosses b1 four times for "
                 "three recordings.",
         ref="derived from STAR §3; docs/notes/original-bugs.md B3",
         bug={"id": "B3", "explanation": "getAgentStory keeps scanning a phase after a match "
                                         "and pushes a spurious crossing"}),
    dict(id="bug_B5", title="Original bug B5: impossible history accepted",
         map="star_fig2", problem=1, agents="single", story="ACBAC", events=EQ3,
         caption="History (3) read as made by one agent: o2 activates while x is still "
                 "inside o1, which no single agent can do. The original says true.",
         ref="derived from STAR eq. (3); docs/notes/original-bugs.md B5",
         bug={"id": "B5", "explanation": "malformed histories are not rejected; the "
                                         "validator accepts what no agent can produce"}),
    dict(id="bug_B8", title="Original bug B8: story may end too early",
         map="star_fig2", problem=1, agents="single", story="AA",
         events=[["b2", "A"]],
         caption="x would have to cross b2 after its last visit to A and so end outside A. "
                 "The original says true (path AA[b2l]).",
         ref="derived from STAR §2; docs/notes/original-bugs.md B8",
         bug={"id": "B8", "explanation": "the single-agent validator accepts when the story "
                                         "ends before the last recording"}),
]


def _steps(path: Optional[Sequence[Any]]) -> Optional[List[Dict[str, Any]]]:
    return None if path is None else [s.to_dict() for s in path]


def _expected(m: "cd.Map", entry: Mapping[str, Any], p: Mapping[str, Any]) -> Dict[str, Any]:
    opts = p.get("options", {})
    unrep = bool(opts.get("unreported_visits", False))
    story, events, agents = p["story"], p["events"], p["agents"]
    out: Dict[str, Any] = {}
    if p["problem"] == 1:
        r = cd.validate(m, story, events, agents=agents, unreported_visits=unrep)
        out.update(consistent=r.consistent, reason=r.reason, story=None,
                   path_string=r.path_string(), path=_steps(r.path))
    elif p["problem"] == 2:
        r = cd.validate_intervals(m, story, events, case=opts["case"], agents=agents,
                                  unreported_visits=unrep)
        out.update(consistent=r.consistent, reason=r.reason, story=None,
                   path_string=r.path_string(), path=_steps(r.path),
                   interval=INTERVAL_CASES[opts["case"]])
        if opts["case"] == 2:
            out["paper_case2_procedure"] = case2_procedure_as_printed(m, story, events)
    elif p["problem"] == 3:
        r = cd.shortest_superstory(m, story, events, agents=agents, unreported_visits=unrep,
                                   anchored=opts.get("anchored", True))
        out.update(consistent=r is not None, reason=None,
                   story=None if r is None else list(r.story),
                   inserted=None if r is None else list(r.inserted),
                   path_string=None if r is None else r.path_string(),
                   path=None if r is None else _steps(r.path))
    else:
        r = cd.closest_story(m, story, events, agents=agents, unreported_visits=unrep)
        out.update(consistent=r is not None, reason=None,
                   story=None if r is None else list(r.story),
                   edits=None if r is None else r.edits,
                   operations=None if r is None else [list(o) for o in r.operations],
                   path_string=None if r is None else r.path_string(),
                   path=None if r is None else _steps(r.path))
    out["polyline"] = (polyline_for_path(entry, out["path"]) if out["path"] is not None
                       else None)
    return out


# a caption that names a free region (R4) needs the region labels on (the page turns them on)
REGION_NAME = re.compile(r"\bR\d+\b")


def build_presets(entries: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for p in PRESETS:
        m = cd.builtin_map(p["map"])
        opts = {"unreported_visits": False}
        opts.update(p.get("options", {}))
        events = [list(e) for e in p["events"]]
        hist = history_to_string(parse_history(events, m), m)
        d: Dict[str, Any] = {
            "id": p["id"], "title": p["title"], "map": p["map"], "problem": p["problem"],
            "agents": p["agents"], "story": p["story"], "history": hist, "events": events,
            "options": opts, "caption": p["caption"], "ref": p["ref"],
            "region_labels": bool(REGION_NAME.search(p["caption"])),
            "bug": p.get("bug"),
        }
        d["expected"] = _expected(m, entries[p["map"]], dict(p, options=opts))
        if p.get("bug"):
            o = cd.validate(m, p["story"], events, agents=p["agents"], compat="original")
            d["original"] = {"consistent": o.consistent, "path_string": o.path_string()}
        out.append(d)
    return out


# ---------------------------------------------------------------------- output

HEADER = """\
/* Cyber Detectives demo data.  GENERATED by tools/gen_demo_data.py from the Python package
 * (src/cyber_detectives); do not edit by hand.  Regenerate with
 *     python3 tools/gen_demo_data.py
 * tests/test_demo_data.py fails when this file is out of date.
 *
 * Classic script (no ES module): assigns one plain object to globalThis.CD_DATA
 * (window.CD_DATA in a browser).  Data only, no functions.  Coordinates are world units,
 * origin top-left, x right, y DOWN.  Rectangles are [x, y, w, h]; points [x, y];
 * segments [x1, y1, x2, y2].
 *
 * CD_DATA = {
 *   schema: 1, generator: "tools/gen_demo_data.py", package_version: "1.0.0",
 *   map_order: ["star_fig2", "icra_fig2"],
 *   maps: { <name>: {
 *     name, title, long_title,
 *     also: ["icra_fig1"],             // builtin map names that are the same workspace
 *     provenance: { summary, geometry, geometry_notes, map: {<key>: <text>} },
 *     map: {                           // Map.to_dict() of the Python package, minus geometry
 *       rooms: ["A", ...], beams: {b1: ["b1u", "b1d"], ...}, occupancy: ["o1", ...],
 *       vertex_order?: [...], regions: {R1: [feature, ...], ...}, edges: [[u, v], ...] },
 *     drawing: {
 *       units, size: [w, h], bounding_box: {rect: [x, y, w, h], stroke},
 *       walls: {stroke, cap: "square"|"butt", segments: [seg, ...]},
 *       wall_rects: [rect, ...],       // filled area of the frame + wall strokes (caps applied)
 *       rooms: {A: rect, ...}, occupancy: {o1: rect, ...},
 *       beams: { b1: {
 *         sides: [s1, s2], form: "two-sided"|"one-segment", stroke, cap,
 *         side_segments?: {s: seg},     // two-sided: one stroke per side (original applet)
 *         segment?: seg, half?: {s: "-x"|"+x"|"-y"|"+y"},  // one-segment: which half is s
 *         line: seg,                    // centre line
 *         band: rect,                   // whole beam (obstacle except when crossing)
 *         side_rects: {s: rect},        // the part of the band that belongs to side s
 *         normals: {s: [nx, ny]},       // unit vector from the centre line towards side s
 *         side_labels: {s: point} } },  // where to write the side name
 *       regions: {R1: {point}},          // region label points (in free space)
 *       portals: {R: {feature: {region_point, feature_point}}} },
 *     routes: { R: {                   // collision-free polylines inside region R
 *       anchors: {"@label": point, <feature>: point},   // feature -> its portal region_point
 *       paths: {a: {b: [point, ...]}} } },  // every ordered pair a != b; paths[b][a] = reverse
 *     interiors: { <room or occupancy>: {point, to: {R: [interior, feature_point, region_point]}} },
 *     crossings: { <side s>: {beam, to_side, from_region, to_region, points: [...]} }
 *   } },
 *   presets: [ {
 *     id, title, map, problem: 1|2|3|4, agents: "single"|"multi",
 *     story: "ACBAC", history: "b1 o1 o1 b2 o2 o2" (toggle notation; o1+/o1- if needed),
 *     events: [[sensor, "A"|"D"], ...],
 *     options: {unreported_visits: bool, case?: 1..6 (problem 2), anchored?: bool (problem 3)},
 *     caption, ref, bug: null | {id: "B1", explanation},
 *     region_labels: bool,             // the caption names a region (R4): show region labels
 *     expected: {                      // the Python package's answer (default mode)
 *       consistent, reason, story (problems 3/4: p' as a list, else null), path_string,
 *       path: [{kind, position, time, story_index, sensor, event}, ...] | null,
 *       polyline: [point, ...] | null,  // polyline_for_path(map, path)
 *       inserted? (3), edits?, operations? ([op, index, old, new], 4),
 *       interval?, paper_case2_procedure? (2) },
 *     original?: {consistent, path_string} } ]   // compat="original" (bug presets only)
 * }
 *
 * Witness -> polyline (tools/gen_demo_data.py polyline_for_path; port it exactly):
 *   x stands at an anchor: a room/occupancy interior, or in region R at anchor a ("@label"
 *   or a feature name).  Start: interiors[p].point, or (region) routes[R].anchors["@label"]
 *   with a = "@label".  For each next step (P = previous position, Q = step.position):
 *     kind "cross"/"pass" (sensor s = side crossed from): append routes[P].paths[a][s]
 *       (skip if a == s), then crossings[s].points; a = crossings[s].to_side;
 *     else Q == P: nothing;
 *     else P room/occupancy, Q region: append interiors[P].to[Q]; a = P;
 *     else P region, Q room/occupancy: append routes[P].paths[a][Q] (skip if a == Q), then
 *       interiors[Q].to[P] reversed.
 *   If the walk ends in a region, append routes[R].paths[a]["@label"] (skip if a is it).
 *   "append" drops a point equal (===) to the last one.
 */
"""


def _dump(v: Any, indent: int = 0, depth: int = 0) -> str:
    """Compact JSON: objects indented to depth 4, lists of scalars/points kept on one line."""
    def flat(x: Any) -> str:
        return json.dumps(x, ensure_ascii=True, separators=(",", ":"))

    def simple(x: Any) -> bool:
        if isinstance(x, list):
            return all(not isinstance(e, (dict, list)) or
                       (isinstance(e, list) and all(not isinstance(f, (dict, list)) for f in e))
                       for e in x)
        return not isinstance(x, dict)

    pad = " " * (indent + 1)
    if isinstance(v, dict):
        if not v:
            return "{}"
        if depth >= 5:
            return flat(v)
        items = ["%s%s: %s" % (pad, json.dumps(k, ensure_ascii=True), _dump(x, indent + 1, depth + 1))
                 for k, x in v.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    if isinstance(v, list) and not simple(v):
        items = ["%s%s" % (pad, _dump(x, indent + 1, depth + 1)) for x in v]
        return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"
    return flat(v)


def build_data() -> Dict[str, Any]:
    _check_icra_fig1_is_star_fig2()
    maps = {n: build_map_entry(n) for n in DEMO_MAPS}
    return {
        "schema": SCHEMA,
        "generator": "tools/gen_demo_data.py",
        "package_version": cd.__version__,
        "map_order": list(DEMO_MAPS),
        "maps": maps,
        "presets": build_presets(maps),
    }


def render(data: Optional[Dict[str, Any]] = None) -> str:
    if data is None:
        data = build_data()
    body = _dump(data)
    return (HEADER + "(function (root) {\n  \"use strict\";\n  root.CD_DATA = " + body
            + ";\n})(typeof globalThis !== \"undefined\" ? globalThis : this);\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if data.js is out of date")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing")
    ap.add_argument("--out", default=OUT_PATH, help="output file (default docs/js/data.js)")
    args = ap.parse_args(argv)
    text = render()
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                ok = f.read() == text
        except OSError:
            ok = False
        if not ok:
            print("%s is out of date; run python3 tools/gen_demo_data.py" % args.out,
                  file=sys.stderr)
        return 0 if ok else 1
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote %s (%d bytes)" % (os.path.relpath(args.out), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
