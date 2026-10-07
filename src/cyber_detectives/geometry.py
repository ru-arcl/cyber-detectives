"""Map geometry: regions from drawings, hit-testing, drawable paths, geometric walk simulation.

A *geometry* is the drawing of a map (``data/geometry/<map>.json``): a bounding box, walls,
rooms, occupancy rectangles and beam detectors, all axis-aligned, in screen coordinates
(origin top-left, x right, y **down**; the original applet's 800 x 600 world units).

Everything here works on an **exact cell decomposition** of the drawing.  Every obstacle
(wall or frame stroke, beam, room, occupancy rectangle) is an axis-aligned rectangle, so the
grid formed by all rectangle edges splits the plane into cells that each lie entirely inside
or entirely outside every rectangle.  Each cell gets one *owner*, by priority

    wall (incl. the frame)  >  beam side  >  beam band  >  occupancy  >  room  >  free

and the free cells are grouped into 4-connected components: the regions ``R_k`` of the
papers.  A feature *touches* a region when one of its cells shares an edge (of positive
length) with a free cell of that region.  This is exact, needs no resolution parameter, and
treats walls and beams as closed sets (so a gap of width 0 does not connect anything).

Two beam drawings are supported (see ``docs/notes/map-geometry.md``):

- **two-sided** (the original applet): each side is its own stroked segment; the band is the
  bounding box of both strokes, so the strip between them belongs to the beam (not to free
  space, which would let an agent "cross" without being seen);
- **one segment** (our ICRA Fig. 2 drawing): one stroked segment across a doorway, plus which
  half (``"-x"``, ``"+x"``, ``"-y"``, ``"+y"``; y points down) belongs to which side.

Public helpers:

- :func:`builtin_geometry`, :func:`load_geometry`, :func:`as_geometry`, :class:`Geometry`;
- :meth:`Geometry.derive_regions` (regions computed from the drawing) and
  :func:`check_geometry` (compare them with a :class:`~cyber_detectives.maps.Map`);
- hit-testing: :meth:`Geometry.location`, :meth:`Geometry.feature_at`,
  :meth:`Geometry.region_at`, :meth:`Geometry.in_free_space`;
- drawing: :meth:`Geometry.compute_portals`, :meth:`Geometry.route`, :func:`expand_path`,
  :func:`path_polyline`;
- simulation: :func:`simulate_walk`, :func:`simulate_multi` (a Monte Carlo oracle: every
  simulated story/history pair is consistent by construction).

Standard library only.
"""

from __future__ import annotations

import bisect
import heapq
import json
import math
import os
import random
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

__all__ = [
    "Geometry",
    "GeometryError",
    "SimulatedWalk",
    "MultiWalk",
    "as_geometry",
    "builtin_geometry",
    "builtin_geometry_names",
    "check_geometry",
    "expand_path",
    "load_geometry",
    "path_polyline",
    "simulate_multi",
    "simulate_walk",
    "trace_polyline",
]

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]  # internal form: (x0, y0, x1, y1), x0 < x1, y0 < y1

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "geometry")

# Owner kinds, highest priority first.  "free" cells form the regions.
_KINDS = ("wall", "side", "beam", "occupancy", "room", "free")
_PRIO = {k: i for i, k in enumerate(_KINDS)}

_NORMALS = {"-x": (-1.0, 0.0), "+x": (1.0, 0.0), "-y": (0.0, -1.0), "+y": (0.0, 1.0)}


class GeometryError(ValueError):
    """A geometry is malformed, or a request cannot be satisfied on it."""


# ---------------------------------------------------------------------- small helpers

def _xywh(r: Sequence[float]) -> Rect:
    """``[x, y, w, h]`` -> ``(x0, y0, x1, y1)``."""
    x, y, w, h = (float(v) for v in r)
    if w <= 0 or h <= 0:
        raise GeometryError("rectangle %r must have positive width and height" % (list(r),))
    return (x, y, x + w, y + h)


def _stroke_rect(seg: Sequence[float], width: float, cap: str) -> Rect:
    """The rectangle covered by an axis-aligned segment drawn with a stroke of ``width``.

    ``cap="square"`` (Java's ``BasicStroke`` default) extends the stroke ``width/2`` past
    both endpoints; ``cap="butt"`` does not.
    """
    x1, y1, x2, y2 = (float(v) for v in seg)
    if x1 != x2 and y1 != y2:
        raise GeometryError("segment %r is not axis-aligned" % (list(seg),))
    if cap not in ("square", "butt"):
        raise GeometryError("unknown stroke cap %r (use 'square' or 'butt')" % cap)
    h = width / 2.0
    x0, x1 = min(x1, x2), max(x1, x2)
    y0, y1 = min(y1, y2), max(y1, y2)
    along = h if cap == "square" else 0.0
    if y0 == y1:  # horizontal (or a point)
        return (x0 - along, y0 - h, x1 + along, y1 + h)
    return (x0 - h, y0 - along, x1 + h, y1 + along)


def _inflate(r: Rect, m: float) -> Rect:
    return (r[0] - m, r[1] - m, r[2] + m, r[3] + m)


def _rect_center(r: Rect) -> Point:
    return ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0)


def _in_rect(p: Point, r: Rect) -> bool:
    """Closed containment."""
    return r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]


def _segment_hits_rect(p: Point, q: Point, r: Rect) -> bool:
    """True if the closed segment ``pq`` meets the closed rectangle ``r`` (Liang-Barsky)."""
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for pk, qk in ((-dx, p[0] - r[0]), (dx, r[2] - p[0]), (-dy, p[1] - r[1]), (dy, r[3] - p[1])):
        if pk == 0.0:
            if qk < 0.0:
                return False
        else:
            t = qk / pk
            if pk < 0.0:
                if t > t1:
                    return False
                if t > t0:
                    t0 = t
            else:
                if t < t0:
                    return False
                if t < t1:
                    t1 = t
    return t0 <= t1


def _dist(p: Point, q: Point) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _lerp(p: Point, q: Point, t: float) -> Point:
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _pt(v: Sequence[float]) -> Point:
    return (float(v[0]), float(v[1]))


def _jsonable_point(p: Point) -> List[float]:
    return [_num(p[0]), _num(p[1])]


def _num(v: float) -> float:
    """Round for JSON output: integers stay integers."""
    r = round(v, 2)
    return int(r) if r == int(r) else r


# ---------------------------------------------------------------------- beams

class Beam:
    """Drawing of one beam detector.

    Attributes
    ----------
    name:
        Beam name (``"b1"``).
    sides:
        The two side names in the order the geometry file lists them.
    band:
        The rectangle the beam occupies (``(x0, y0, x1, y1)``).
    side_rects:
        ``{side: rect}``, the part of the band that belongs to each side.
    normals:
        ``{side: (nx, ny)}``, unit vector from the beam's centre line towards that side.
    line:
        The centre line ``((x1, y1), (x2, y2))`` (for drawing a one-segment beam).
    """

    def __init__(self, name: str, spec: Mapping[str, Any], default_stroke: float,
                 default_cap: str) -> None:
        self.name = name
        stroke = float(spec.get("stroke", default_stroke))
        cap = spec.get("cap", default_cap)
        sides = spec.get("sides")
        if not isinstance(sides, Mapping) or len(sides) != 2:
            raise GeometryError("beam %r: 'sides' must map exactly two side names" % name)
        self.sides: Tuple[str, str] = tuple(sides)  # type: ignore[assignment]
        self.side_rects: Dict[str, Rect] = {}
        self.normals: Dict[str, Point] = {}
        vals = list(sides.values())
        if all(isinstance(v, str) for v in vals):
            # One segment across the doorway; each side owns one half of the stroke.
            if "segment" not in spec:
                raise GeometryError("beam %r: one-segment form needs 'segment'" % name)
            seg = [float(v) for v in spec["segment"]]
            self.band = _stroke_rect(seg, stroke, cap)
            horizontal = seg[1] == seg[3]
            x0, y0, x1, y1 = self.band
            xm, ym = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if set(vals) != ({"-y", "+y"} if horizontal else {"-x", "+x"}):
                raise GeometryError(
                    "beam %r: a %s segment needs sides %s, got %r"
                    % (name, "horizontal" if horizontal else "vertical",
                       "'-y'/'+y'" if horizontal else "'-x'/'+x'", vals))
            for s, d in sides.items():
                self.normals[s] = _NORMALS[d]
                self.side_rects[s] = {"-y": (x0, y0, x1, ym), "+y": (x0, ym, x1, y1),
                                      "-x": (x0, y0, xm, y1), "+x": (xm, y0, x1, y1)}[d]
            self.line = ((seg[0], seg[1]), (seg[2], seg[3]))
        elif all(isinstance(v, (list, tuple)) and len(v) == 4 for v in vals):
            # Two-sided drawing (original applet): one stroke per side, band = bounding box.
            for s, seg in sides.items():
                self.side_rects[s] = _stroke_rect(seg, stroke, cap)
            ra, rb = (self.side_rects[s] for s in self.sides)
            self.band = (min(ra[0], rb[0]), min(ra[1], rb[1]), max(ra[2], rb[2]),
                         max(ra[3], rb[3]))
            ca, cb = _rect_center(ra), _rect_center(rb)
            dx, dy = cb[0] - ca[0], cb[1] - ca[1]
            if abs(dx) > abs(dy):
                na, nb = ((-1.0, 0.0), (1.0, 0.0)) if dx > 0 else ((1.0, 0.0), (-1.0, 0.0))
            elif dy != 0:
                na, nb = ((0.0, -1.0), (0.0, 1.0)) if dy > 0 else ((0.0, 1.0), (0.0, -1.0))
            else:
                raise GeometryError("beam %r: the two side segments coincide" % name)
            self.normals = {self.sides[0]: na, self.sides[1]: nb}
            sa = [float(v) for v in sides[self.sides[0]]]
            sb = [float(v) for v in sides[self.sides[1]]]
            self.line = (((sa[0] + sb[0]) / 2, (sa[1] + sb[1]) / 2),
                         ((sa[2] + sb[2]) / 2, (sa[3] + sb[3]) / 2))
        else:
            raise GeometryError(
                "beam %r: side values must all be segments [x1,y1,x2,y2] (two-sided form) or "
                "all half-plane names '-x','+x','-y','+y' (one-segment form)" % name)

    @property
    def horizontal(self) -> bool:
        """True if the beam runs along x (its sides are above/below)."""
        return self.normals[self.sides[0]][0] == 0.0

    def center_line_coord(self) -> float:
        """y of the centre line of a horizontal beam, x of a vertical one."""
        x0, y0, x1, y1 = self.band
        return (y0 + y1) / 2.0 if self.horizontal else (x0 + x1) / 2.0

    def other(self, side: str) -> str:
        return self.sides[1] if side == self.sides[0] else self.sides[0]


# ---------------------------------------------------------------------- geometry

class Geometry:
    """The drawing of a map, with an exact cell decomposition (built lazily).

    Construct with :meth:`from_dict` / :func:`load_geometry` / :func:`builtin_geometry`.
    The JSON format (``data/geometry/<map>.json``)::

        {"name": ..., "map": ..., "provenance": ...,
         "geometry": {
           "bounding_box": {"rect": [x, y, w, h], "stroke": 20},
           "walls": {"stroke": 8, "cap": "square", "segments": [[x1, y1, x2, y2], ...]},
           "rooms": {"A": [x, y, w, h], ...},
           "occupancy": {"o1": [x, y, w, h], ...},
           "beams": {"b1": {"stroke": 15, "cap": "square",
                            "sides": {"b1u": [x1, y1, x2, y2], "b1d": [...]}},     # two-sided
                     "b3": {"segment": [x1, y1, x2, y2], "stroke": 6,
                            "sides": {"b31": "-x", "b32": "+x"}}},                 # one segment
           "regions": {"R1": {"point": [x, y]}, ...},          # names + label points
           "portals": {"R1": {"A": {"region_point": [x, y], "feature_point": [x, y]}}}}}

    ``regions`` names the computed components (each named point must lie in free space).
    ``portals`` is optional precomputed drawing data (see :meth:`compute_portals`).  The
    original applet's key names (``room_rects``, ``occupancy_rects``, ``beam_sides``) are
    also accepted, so ``tests/fixtures/paper/star_fig2_geometry.json`` loads as is.
    """

    def __init__(self, data: Mapping[str, Any], name: Optional[str] = None,
                 meta: Optional[Mapping[str, Any]] = None) -> None:
        self.raw = dict(data)
        self.meta = dict(meta or {})
        self.name = name or self.meta.get("name") or data.get("name") or "geometry"
        bb = data.get("bounding_box")
        if not isinstance(bb, Mapping) or "rect" not in bb:
            raise GeometryError("geometry %r: missing bounding_box.rect" % self.name)
        self.bbox: Rect = _xywh(bb["rect"])
        self.frame_stroke = float(bb.get("stroke", 0.0))

        walls = data.get("walls", {})
        self.wall_stroke = float(walls.get("stroke", 1.0))
        self.wall_cap = walls.get("cap", "square")
        self.wall_segments: List[List[float]] = [[float(v) for v in s]
                                                 for s in walls.get("segments", [])]
        self.wall_rects()  # validates the segments (axis-aligned, known cap)

        rooms = data.get("rooms", data.get("room_rects", {}))
        occ = data.get("occupancy", data.get("occupancy_rects", {}))
        self.rooms: Dict[str, Rect] = {k: _xywh(v) for k, v in rooms.items()}
        self.occupancy: Dict[str, Rect] = {k: _xywh(v) for k, v in occ.items()}

        self.beams: Dict[str, Beam] = {}
        if "beams" in data:
            for b, spec in data["beams"].items():
                self.beams[b] = Beam(b, spec, 1.0, "square")
        elif "beam_sides" in data:
            # Applet fixture form: {"stroke": 15, "segments": {"b1u": seg, ...}}; the beam of
            # a side is its name minus the last character (b1u -> b1).
            bs = data["beam_sides"]
            groups: Dict[str, Dict[str, Any]] = {}
            for s, seg in bs["segments"].items():
                groups.setdefault(s[:-1], {})[s] = seg
            for b, sides in groups.items():
                self.beams[b] = Beam(b, {"sides": sides}, float(bs.get("stroke", 1.0)),
                                     bs.get("cap", "square"))
        self.side_beam: Dict[str, str] = {s: b for b, bm in self.beams.items() for s in bm.sides}

        self.region_points: Dict[str, Point] = {}
        for r, spec in (data.get("regions") or {}).items():
            pt = spec.get("point") if isinstance(spec, Mapping) else spec
            self.region_points[r] = _pt(pt)
        self.stored_portals: Dict[str, Dict[str, Dict[str, Point]]] = {}
        for r, feats in (data.get("portals") or {}).items():
            self.stored_portals[r] = {
                f: {k: _pt(v) for k, v in pp.items()} for f, pp in feats.items()}

        names = list(self.rooms) + list(self.occupancy) + list(self.beams) + list(self.side_beam)
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise GeometryError("geometry %r: names used twice: %s" % (self.name, sorted(dup)))

        self._decomp: Optional[_Decomposition] = None
        self._grids: Dict[float, "_RouteGrid"] = {}
        self._portals: Optional[Dict[str, Dict[str, Dict[str, Point]]]] = None

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Geometry":
        """Build from a geometry file's object (with a ``geometry`` member) or the bare
        geometry object itself (what ``Map.geometry`` holds)."""
        if not isinstance(d, Mapping):
            raise GeometryError("a geometry must be a JSON object, got %s" % type(d).__name__)
        if "geometry" in d and isinstance(d["geometry"], Mapping):
            meta = {k: v for k, v in d.items() if k != "geometry"}
            return cls(d["geometry"], name=d.get("map") or d.get("name"), meta=meta)
        return cls(d)

    # ------------------------------------------------------------------ basic properties

    @property
    def features(self) -> Tuple[str, ...]:
        """Rooms, beam sides and occupancy sensors that appear in the drawing."""
        return tuple(self.rooms) + tuple(self.side_beam) + tuple(self.occupancy)

    @property
    def size(self) -> Tuple[float, float]:
        """Width and height of the bounding box (the canvas)."""
        return (self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])

    def wall_rects(self) -> List[Rect]:
        """Rectangles covered by the frame and the wall strokes."""
        x0, y0, x1, y1 = self.bbox
        h = self.frame_stroke / 2.0
        out: List[Rect] = []
        if h > 0:
            out += [(x0 - h, y0 - h, x1 + h, y0 + h), (x0 - h, y1 - h, x1 + h, y1 + h),
                    (x0 - h, y0 - h, x0 + h, y1 + h), (x1 - h, y0 - h, x1 + h, y1 + h)]
        out += [_stroke_rect(s, self.wall_stroke, self.wall_cap) for s in self.wall_segments]
        return out

    def obstacle_rects(self) -> List[Rect]:
        """Everything that is not free space: walls, frame, beams, rooms, occupancy."""
        return (self.wall_rects() + [b.band for b in self.beams.values()]
                + list(self.rooms.values()) + list(self.occupancy.values()))

    def feature_kind(self, name: str) -> str:
        """``"room"``, ``"occupancy"``, ``"side"`` or ``"beam"``; ``KeyError`` otherwise."""
        if name in self.rooms:
            return "room"
        if name in self.occupancy:
            return "occupancy"
        if name in self.side_beam:
            return "side"
        if name in self.beams:
            return "beam"
        raise KeyError(name)

    def feature_center(self, name: str) -> Point:
        """Centre of a room or occupancy rectangle, or of a beam's band."""
        if name in self.rooms:
            return _rect_center(self.rooms[name])
        if name in self.occupancy:
            return _rect_center(self.occupancy[name])
        if name in self.beams:
            return _rect_center(self.beams[name].band)
        if name in self.side_beam:
            return _rect_center(self.beams[self.side_beam[name]].side_rects[name])
        raise KeyError(name)

    # ------------------------------------------------------------------ decomposition

    @property
    def decomposition(self) -> "_Decomposition":
        if self._decomp is None:
            self._decomp = _Decomposition(self)
        return self._decomp

    def derive_regions(self) -> Dict[str, Tuple[str, ...]]:
        """The free regions computed from the drawing: ``{name: sorted touched features}``.

        Names come from ``regions`` (the component containing each named point); unnamed
        components that touch a feature are called ``_1``, ``_2``, ...  Components that touch
        no feature (sealed pockets) are left out.
        """
        d = self.decomposition
        return {d.comp_names[c]: tuple(sorted(d.comp_touches[c]))
                for c in range(len(d.comp_touches)) if d.comp_touches[c]}

    def location(self, x: float, y: float) -> Tuple[str, Optional[str]]:
        """What lies at a point, in the decomposition's terms.

        Returns ``(kind, name)`` with ``kind`` one of ``"wall"`` (walls and frame, name
        ``None``), ``"side"`` (a beam side), ``"beam"`` (the band between two drawn sides),
        ``"occupancy"``, ``"room"``, ``"region"`` (free space; name = region name) or
        ``"outside"``.  Points on a boundary get the highest-priority owner (obstacles are
        closed sets).
        """
        return self.decomposition.locate((x, y))

    def region_at(self, x: float, y: float) -> Optional[str]:
        """Name of the free region containing the point, or ``None``."""
        k, n = self.location(x, y)
        return n if k == "region" else None

    def in_free_space(self, x: float, y: float) -> bool:
        """True if the point is in free space (a region): not in a wall, the frame, a beam, a
        room or an occupancy rectangle."""
        return self.location(x, y)[0] == "region"

    def feature_at(self, x: float, y: float, beam_tolerance: float = 0.0) -> Optional[str]:
        """Hit-test a point (e.g. a click): the feature whose shape contains it, or ``None``.

        Tested in the original applet's order (``Environment.getClickedVertex``): occupancy
        rectangles, then rooms, then beam sides (closed rectangles; first match wins).  A beam
        side's rectangle is widened outward by ``beam_tolerance`` (useful for thin one-segment
        beams).  The band strip between two drawn sides (applet form) hits nothing.
        """
        p = (x, y)
        for o, r in self.occupancy.items():
            if _in_rect(p, r):
                return o
        for a, r in self.rooms.items():
            if _in_rect(p, r):
                return a
        for b in self.beams.values():
            for s in b.sides:
                r = b.side_rects[s]
                if beam_tolerance > 0:
                    nx, ny = b.normals[s]
                    t = beam_tolerance
                    r = (r[0] - t * (nx < 0), r[1] - t * (ny < 0),
                         r[2] + t * (nx > 0), r[3] + t * (ny > 0))
                if _in_rect(p, r):
                    return s
        return None

    def segment_runs(self, p: Point, q: Point) -> List[Tuple[float, float, str, Optional[str]]]:
        """Walk the straight segment ``p -> q`` through the decomposition.

        Returns maximal runs ``(t_start, t_end, kind, name)`` (``0 <= t <= 1``) of the owners
        met, with ``kind``/``name`` as in :meth:`location`.
        """
        return self.decomposition.runs(p, q)

    def segment_is_free(self, p: Point, q: Point, margin: float = 0.0) -> bool:
        """True if the closed segment ``pq`` meets no obstacle inflated by ``margin``."""
        for r in self.obstacle_rects():
            if _segment_hits_rect(p, q, _inflate(r, margin)):
                return False
        return True

    # ------------------------------------------------------------------ drawing helpers

    def region_point(self, region: str) -> Point:
        """A representative (label) point of a region."""
        if region in self.region_points:
            return self.region_points[region]
        d = self.decomposition
        for c, n in enumerate(d.comp_names):
            if n == region:
                return d.deep_point(c)
        raise KeyError(region)

    def portals(self) -> Dict[str, Dict[str, Dict[str, Point]]]:
        """Portal points per ``(region, feature)``: the stored ones if the file has them,
        else :meth:`compute_portals`."""
        if self.stored_portals:
            return self.stored_portals
        if self._portals is None:
            self._portals = self.compute_portals()
        return self._portals

    def compute_portals(self, depth: float = 14.0) -> Dict[str, Dict[str, Dict[str, Point]]]:
        """Compute a portal for every region and every feature it touches.

        For a room or occupancy sensor the portal is its widest doorway into the region:
        ``region_point`` lies in the region and ``feature_point`` inside the feature, both on
        the doorway's normal through its centre, up to ``depth`` units from it.  For a beam
        side, ``region_point`` lies in the region in front of that side and ``feature_point``
        is on the beam's centre line, so ``region_point(side) -> feature_point ->
        region_point(other side)`` draws a crossing.  The straight segment between the two
        points crosses nothing but the doorway (checked).

        Returns ``{region: {feature: {"region_point": (x, y), "feature_point": (x, y)}}}``.
        """
        d = self.decomposition
        out: Dict[str, Dict[str, Dict[str, Point]]] = {}
        for c, rname in enumerate(d.comp_names):
            if not d.comp_touches[c]:
                continue
            for f in sorted(d.comp_touches[c]):
                doors = d.doors(c, f)
                if not doors:  # pragma: no cover - touches implies a door
                    raise GeometryError("no doorway between %s and %s" % (rname, f))
                portal = None
                for door in doors:  # widest first
                    portal = self._portal_from_door(rname, f, door, depth)
                    if portal is not None:
                        break
                if portal is None:
                    raise GeometryError("could not place a portal between %s and %s"
                                        % (rname, f))
                out.setdefault(rname, {})[f] = portal
        return out

    def _portal_from_door(self, region: str, feature: str, door: Tuple[Point, Point, Point],
                          depth: float) -> Optional[Dict[str, Point]]:
        a, b, n = door
        c = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        kind = self.feature_kind(feature)
        if kind == "side":
            beam = self.beams[self.side_beam[feature]]
            m = beam.center_line_coord()
            fp = (c[0], m) if beam.horizontal else (m, c[1])
        else:
            fp = None
            for dd in _depths(depth):
                cand = (c[0] - n[0] * dd, c[1] - n[1] * dd)
                if self.location(*cand) == (kind, feature) and self._clear_path(
                        [cand, self.feature_center(feature)], {(kind, feature)}):
                    fp = cand
                    break
            if fp is None:
                return None
        for dd in _depths(depth):
            rp = (c[0] + n[0] * dd, c[1] + n[1] * dd)
            if self.location(*rp) != ("region", region):
                continue
            allowed = {("region", region), (kind, feature)}
            if kind == "side":
                allowed.add(("beam", self.side_beam[feature]))
            if self._clear_path([rp, fp], allowed):
                return {"region_point": rp, "feature_point": fp}
        return None

    def _clear_path(self, pts: Sequence[Point], allowed: Iterable[Tuple[str, Optional[str]]]
                    ) -> bool:
        ok = set(allowed)
        for p, q in zip(pts, pts[1:]):
            for _, _, k, n in self.segment_runs(p, q):
                if (k, n) not in ok:
                    return False
        return True

    def route(self, p: Point, q: Point, region: Optional[str] = None) -> List[Point]:
        """A polyline from ``p`` to ``q`` that stays in the free space of one region.

        Both points must lie in that region.  Planned on a 4-unit grid with clearance from
        obstacles (6, then 3, then 1.5 units), then shortcut where straight segments stay
        clear.  Raises :class:`GeometryError` if no such path exists.
        """
        rp, rq = self.region_at(*p), self.region_at(*q)
        if region is None:
            region = rp
        if rp != region or rq != region or region is None:
            raise GeometryError("route %r -> %r: both points must be in region %r (they are in "
                                "%r and %r)" % (p, q, region, rp, rq))
        if self.segment_is_free(p, q, margin=1.0):
            return [p, q]
        for clearance in (6.0, 3.0, 1.5):
            grid = self._grids.get(clearance)
            if grid is None:
                grid = self._grids[clearance] = _RouteGrid(self, clearance)
            path = grid.plan(p, q, region)
            if path is not None:
                return path
        raise GeometryError("no path from %r to %r inside region %r" % (p, q, region))


def _depths(depth: float) -> List[float]:
    out = [depth]
    d = depth
    while d > 2.0:
        d = max(2.0, d - 2.0)
        out.append(d)
    return out


# ---------------------------------------------------------------------- decomposition

class _Decomposition:
    """Exact decomposition of a geometry into rectangular cells (see module docstring)."""

    def __init__(self, g: Geometry) -> None:
        self.g = g
        h = g.frame_stroke / 2.0
        X0, Y0, X1, Y1 = g.bbox[0] - h, g.bbox[1] - h, g.bbox[2] + h, g.bbox[3] + h
        self.domain = (X0, Y0, X1, Y1)

        # (priority-ordered) painting list: lowest priority first, later entries overwrite.
        layers: List[Tuple[Rect, Tuple[str, Optional[str]]]] = []
        for a, r in g.rooms.items():
            layers.append((r, ("room", a)))
        for o, r in g.occupancy.items():
            layers.append((r, ("occupancy", o)))
        for b, bm in g.beams.items():
            layers.append((bm.band, ("beam", b)))
        for b, bm in g.beams.items():
            for s in bm.sides:
                layers.append((bm.side_rects[s], ("side", s)))
        for r in g.wall_rects():
            layers.append((r, ("wall", None)))

        xs = {X0, X1}
        ys = {Y0, Y1}
        for r, _ in layers:
            for v in (r[0], r[2]):
                if X0 < v < X1:
                    xs.add(v)
            for v in (r[1], r[3]):
                if Y0 < v < Y1:
                    ys.add(v)
        self.xs = sorted(xs)
        self.ys = sorted(ys)
        nx, ny = len(self.xs) - 1, len(self.ys) - 1
        self.nx, self.ny = nx, ny

        self.owners: List[Tuple[str, Optional[str]]] = [("free", None)]
        oid: Dict[Tuple[str, Optional[str]], int] = {("free", None): 0}
        cell = [0] * (nx * ny)
        for r, owner in layers:
            if owner not in oid:
                oid[owner] = len(self.owners)
                self.owners.append(owner)
            k = oid[owner]
            i0 = bisect.bisect_left(self.xs, max(r[0], X0))
            i1 = bisect.bisect_left(self.xs, min(r[2], X1))
            j0 = bisect.bisect_left(self.ys, max(r[1], Y0))
            j1 = bisect.bisect_left(self.ys, min(r[3], Y1))
            for i in range(i0, i1):
                base = i * ny
                for j in range(j0, j1):
                    cell[base + j] = k
        self.cell = cell

        # Free components (4-connectivity) and the features they touch.
        comp = [-1] * (nx * ny)
        touches: List[set] = []
        firsts: List[int] = []
        for start in range(nx * ny):
            if cell[start] != 0 or comp[start] != -1:
                continue
            c = len(touches)
            comp[start] = c
            firsts.append(start)
            tset: set = set()
            stack = [start]
            while stack:
                idx = stack.pop()
                i, j = divmod(idx, ny)
                for ii, jj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                    if 0 <= ii < nx and 0 <= jj < ny:
                        n2 = ii * ny + jj
                        o = cell[n2]
                        if o == 0:
                            if comp[n2] == -1:
                                comp[n2] = c
                                stack.append(n2)
                        else:
                            kind, name = self.owners[o]
                            if kind in ("room", "occupancy", "side"):
                                tset.add(name)
            touches.append(tset)
        self.comp = comp
        self.comp_touches = touches

        # Names: from the geometry's region points; then _1, _2, ... ; pockets _pocket1, ...
        names: List[Optional[str]] = [None] * len(touches)
        self.name_errors: List[str] = []
        for rname, p in g.region_points.items():
            idx = self._cell_index(p)
            if idx is None or cell[idx] != 0:
                self.name_errors.append("region point %s %r is not in free space"
                                        % (rname, p))
                continue
            c = comp[idx]
            if names[c] is not None:
                self.name_errors.append("region points %s and %s lie in the same component"
                                        % (names[c], rname))
                continue
            names[c] = rname
        k = kp = 0
        for c in range(len(names)):
            if names[c] is None:
                if touches[c]:
                    k += 1
                    names[c] = "_%d" % k
                else:
                    kp += 1
                    names[c] = "_pocket%d" % kp
        self.comp_names: List[str] = names  # type: ignore[assignment]
        self.comp_of_name = {n: c for c, n in enumerate(self.comp_names)}

    # -------------------------------------------------------------- lookup

    def _cell_index(self, p: Point) -> Optional[int]:
        x, y = p
        X0, Y0, X1, Y1 = self.domain
        if not (X0 <= x <= X1 and Y0 <= y <= Y1):
            return None
        i = min(max(bisect.bisect_right(self.xs, x) - 1, 0), self.nx - 1)
        j = min(max(bisect.bisect_right(self.ys, y) - 1, 0), self.ny - 1)
        return i * self.ny + j

    def _describe(self, idx: int) -> Tuple[str, Optional[str]]:
        o = self.cell[idx]
        if o == 0:
            return ("region", self.comp_names[self.comp[idx]])
        return self.owners[o]

    def locate(self, p: Point) -> Tuple[str, Optional[str]]:
        x, y = p
        X0, Y0, X1, Y1 = self.domain
        if not (X0 <= x <= X1 and Y0 <= y <= Y1):
            return ("outside", None)
        # All cells whose closed rectangle contains p (1, 2 or 4); highest priority wins.
        ii = {min(max(bisect.bisect_right(self.xs, x) - 1, 0), self.nx - 1)}
        jj = {min(max(bisect.bisect_right(self.ys, y) - 1, 0), self.ny - 1)}
        i0, j0 = next(iter(ii)), next(iter(jj))
        if self.xs[i0] == x and i0 > 0:
            ii.add(i0 - 1)
        if self.ys[j0] == y and j0 > 0:
            jj.add(j0 - 1)
        best = None
        for i in ii:
            for j in jj:
                d = self._describe(i * self.ny + j)
                if best is None or _PRIO[d[0] if d[0] != "region" else "free"] < \
                        _PRIO[best[0] if best[0] != "region" else "free"]:
                    best = d
        return best  # type: ignore[return-value]

    def runs(self, p: Point, q: Point) -> List[Tuple[float, float, str, Optional[str]]]:
        dx, dy = q[0] - p[0], q[1] - p[1]
        ts = [0.0, 1.0]
        if dx != 0.0:
            lo, hi = min(p[0], q[0]), max(p[0], q[0])
            a = bisect.bisect_right(self.xs, lo)
            b = bisect.bisect_left(self.xs, hi)
            ts.extend((self.xs[k] - p[0]) / dx for k in range(a, b))
        if dy != 0.0:
            lo, hi = min(p[1], q[1]), max(p[1], q[1])
            a = bisect.bisect_right(self.ys, lo)
            b = bisect.bisect_left(self.ys, hi)
            ts.extend((self.ys[k] - p[1]) / dy for k in range(a, b))
        ts.sort()
        out: List[Tuple[float, float, str, Optional[str]]] = []
        for ta, tb in zip(ts, ts[1:]):
            if tb - ta <= 1e-12:
                continue
            m = (ta + tb) / 2.0
            d = self.locate((p[0] + dx * m, p[1] + dy * m))
            if out and (out[-1][2], out[-1][3]) == d:
                out[-1] = (out[-1][0], tb, d[0], d[1])
            else:
                out.append((ta, tb, d[0], d[1]))
        if not out:  # p == q
            d = self.locate(p)
            out.append((0.0, 1.0, d[0], d[1]))
        return out

    # -------------------------------------------------------------- doors, points

    def doors(self, c: int, feature: str) -> List[Tuple[Point, Point, Point]]:
        """Maximal straight contact segments between component ``c`` and ``feature``,
        widest first: ``(end_a, end_b, normal from feature into the region)``."""
        ny = self.ny
        edges: Dict[Tuple[str, float, Point], List[Tuple[float, float]]] = {}
        for idx in range(self.nx * ny):
            if self.comp[idx] != c:
                continue
            i, j = divmod(idx, ny)
            for ii, jj, n in ((i - 1, j, (1.0, 0.0)), (i + 1, j, (-1.0, 0.0)),
                              (i, j - 1, (0.0, 1.0)), (i, j + 1, (0.0, -1.0))):
                if not (0 <= ii < self.nx and 0 <= jj < ny):
                    continue
                o = self.cell[ii * ny + jj]
                if o == 0 or self.owners[o][1] != feature:
                    continue
                if n[1] == 0.0:  # vertical contact edge
                    x = self.xs[i] if ii < i else self.xs[i + 1]
                    edges.setdefault(("v", x, n), []).append((self.ys[j], self.ys[j + 1]))
                else:
                    y = self.ys[j] if jj < j else self.ys[j + 1]
                    edges.setdefault(("h", y, n), []).append((self.xs[i], self.xs[i + 1]))
        doors: List[Tuple[float, Point, Point, Point]] = []
        for (orient, v, n), spans in edges.items():
            spans.sort()
            cur = list(spans[0])
            merged = []
            for a, b in spans[1:]:
                if a <= cur[1] + 1e-9:
                    cur[1] = max(cur[1], b)
                else:
                    merged.append(cur)
                    cur = [a, b]
            merged.append(cur)
            for a, b in merged:
                if orient == "v":
                    doors.append((b - a, (v, a), (v, b), n))
                else:
                    doors.append((b - a, (a, v), (b, v), n))
        doors.sort(key=lambda d: (-d[0], d[1], d[2]))
        return [(a, b, n) for _, a, b, n in doors]

    def deep_point(self, c: int) -> Point:
        """The centre of the component's largest cell (a crude label position)."""
        best, area = None, -1.0
        ny = self.ny
        for idx in range(self.nx * ny):
            if self.comp[idx] == c:
                i, j = divmod(idx, ny)
                a = (self.xs[i + 1] - self.xs[i]) * (self.ys[j + 1] - self.ys[j])
                if a > area:
                    area, best = a, ((self.xs[i] + self.xs[i + 1]) / 2,
                                     (self.ys[j] + self.ys[j + 1]) / 2)
        return best  # type: ignore[return-value]


# ---------------------------------------------------------------------- routing grid

class _RouteGrid:
    """Uniform grid of points at least ``clearance`` away from every obstacle."""

    STEP = 4.0

    def __init__(self, g: Geometry, clearance: float) -> None:
        self.g = g
        self.clearance = clearance
        h = self.STEP
        x0, y0, x1, y1 = g.bbox
        self.x0, self.y0 = x0, y0
        self.nx = int((x1 - x0) / h)
        self.ny = int((y1 - y0) / h)
        blocked = bytearray(self.nx * self.ny)
        for r in g.obstacle_rects():
            a, b, c, d = _inflate(r, clearance)
            i0 = max(0, int(math.floor((a - x0) / h - 0.5)))
            i1 = min(self.nx - 1, int(math.ceil((c - x0) / h - 0.5)))
            j0 = max(0, int(math.floor((b - y0) / h - 0.5)))
            j1 = min(self.ny - 1, int(math.ceil((d - y0) / h - 0.5)))
            for i in range(i0, i1 + 1):
                px = x0 + (i + 0.5) * h
                if not (a <= px <= c):
                    continue
                for j in range(j0, j1 + 1):
                    py = y0 + (j + 0.5) * h
                    if b <= py <= d:
                        blocked[i * self.ny + j] = 1
        self.region: List[Optional[str]] = [None] * (self.nx * self.ny)
        for idx in range(self.nx * self.ny):
            if not blocked[idx]:
                self.region[idx] = g.region_at(*self.point(idx))

    def point(self, idx: int) -> Point:
        i, j = divmod(idx, self.ny)
        return (self.x0 + (i + 0.5) * self.STEP, self.y0 + (j + 0.5) * self.STEP)

    def _attach(self, p: Point, region: str) -> Optional[int]:
        """Nearest grid node of ``region`` visible from ``p`` by a free straight segment."""
        h = self.STEP
        ci = int((p[0] - self.x0) / h)
        cj = int((p[1] - self.y0) / h)
        for rad in range(1, 12):
            cands = []
            for i in range(max(0, ci - rad), min(self.nx, ci + rad + 1)):
                for j in range(max(0, cj - rad), min(self.ny, cj + rad + 1)):
                    idx = i * self.ny + j
                    if self.region[idx] == region:
                        cands.append((_dist(p, self.point(idx)), idx))
            cands.sort()
            for _, idx in cands:
                if self.g.segment_is_free(p, self.point(idx)):
                    return idx
        return None

    def plan(self, p: Point, q: Point, region: str) -> Optional[List[Point]]:
        s = self._attach(p, region)
        t = self._attach(q, region)
        if s is None or t is None:
            return None
        ny = self.ny
        goal = self.point(t)
        dist = {s: 0.0}
        prev: Dict[int, int] = {}
        heap = [(_dist(self.point(s), goal), 0.0, s)]
        while heap:
            _, dcur, u = heapq.heappop(heap)
            if u == t:
                break
            if dcur > dist.get(u, math.inf):
                continue
            i, j = divmod(u, ny)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di == 0 and dj == 0:
                        continue
                    ii, jj = i + di, j + dj
                    if not (0 <= ii < self.nx and 0 <= jj < ny):
                        continue
                    v = ii * ny + jj
                    if self.region[v] != region:
                        continue
                    if di and dj and (self.region[ii * ny + j] != region
                                      or self.region[i * ny + jj] != region):
                        continue
                    nd = dcur + (1.4142135623730951 if di and dj else 1.0)
                    if nd < dist.get(v, math.inf):
                        dist[v] = nd
                        prev[v] = u
                        heapq.heappush(heap, (nd + _dist(self.point(v), goal) / self.STEP,
                                              nd, v))
        if t not in dist:
            return None
        nodes = [t]
        while nodes[-1] != s:
            nodes.append(prev[nodes[-1]])
        pts = [p] + [self.point(n) for n in reversed(nodes)] + [q]
        # Shortcut: from each kept point jump to the farthest point visible with a margin.
        margin = min(2.0, self.clearance / 2.0)
        out = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1:
                m = 0.0 if (i == 0 or j == len(pts) - 1) else margin
                if self.g.segment_is_free(pts[i], pts[j], margin=m):
                    break
                j -= 1
            out.append(pts[j])
            i = j
        return out


# ---------------------------------------------------------------------- loading

def load_geometry(path: Union[str, "os.PathLike[str]"]) -> Geometry:
    """Load a geometry from a JSON file (see :class:`Geometry` for the format)."""
    with open(path, "r", encoding="utf-8") as f:
        return Geometry.from_dict(json.load(f))


def builtin_geometry_names() -> List[str]:
    """Names of the builtin geometries (``data/geometry/*.json``), sorted."""
    try:
        names = os.listdir(_DATA_DIR)
    except FileNotFoundError:  # pragma: no cover
        return []
    return sorted(n[:-5] for n in names if n.endswith(".json"))


def builtin_geometry(name: str) -> Geometry:
    """The builtin geometry of a builtin map (``"star_fig2"``, ``"icra_fig2"``, ...)."""
    if name not in builtin_geometry_names():
        raise KeyError("no builtin geometry %r; available: %s"
                       % (name, ", ".join(builtin_geometry_names())))
    return load_geometry(os.path.join(_DATA_DIR, name + ".json"))


def as_geometry(obj: Any) -> Geometry:
    """Coerce to a :class:`Geometry`: a Geometry, a ``Map`` with ``.geometry``, a geometry
    dict, or the name of a builtin geometry."""
    if isinstance(obj, Geometry):
        return obj
    if isinstance(obj, str):
        return builtin_geometry(obj)
    if isinstance(obj, Mapping):
        return Geometry.from_dict(obj)
    g = getattr(obj, "geometry", None)
    if g is None:
        raise GeometryError("%r has no geometry" % (obj,))
    if isinstance(g, Geometry):
        return g
    cache = getattr(obj, "_geometry_cache", None)
    if cache is not None and cache[0] is g:
        return cache[1]
    geo = Geometry.from_dict(g)
    if not geo.meta.get("name") and getattr(obj, "name", None):
        geo.name = obj.name
    try:
        obj._geometry_cache = (g, geo)
    except AttributeError:  # pragma: no cover - slotted objects
        pass
    return geo


def check_geometry(geometry: Any, map_: Any = None) -> List[str]:
    """Compare the regions derived from a drawing with a map's regions.

    ``map_`` defaults to ``geometry`` itself when that is a ``Map``.  Returns a list of
    human-readable problems (empty = the drawing realises the map exactly: same features,
    same region names, same touched features per region).
    """
    if map_ is None:
        map_ = geometry
    g = as_geometry(geometry)
    problems = list(g.decomposition.name_errors)
    derived = {k: frozenset(v) for k, v in g.derive_regions().items()}
    expected = {k: frozenset(v) for k, v in map_.regions.items()}
    feats_map = set(map_.rooms) | set(map_.occupancy) | {s for ss in map_.beams.values()
                                                         for s in ss}
    feats_geo = set(g.features)
    if feats_map != feats_geo:
        problems.append("features differ: only in map %s, only in drawing %s"
                        % (sorted(feats_map - feats_geo), sorted(feats_geo - feats_map)))
    for b, ss in map_.beams.items():
        if b in g.beams and set(ss) != set(g.beams[b].sides):
            problems.append("beam %s: sides %s in map, %s in drawing"
                            % (b, sorted(ss), sorted(g.beams[b].sides)))
    for r in sorted(set(derived) | set(expected)):
        if derived.get(r) != expected.get(r):
            problems.append("region %s: map %s, drawing %s"
                            % (r, sorted(expected[r]) if r in expected else None,
                               sorted(derived[r]) if r in derived else None))
    return problems


# ---------------------------------------------------------------------- paths

_TOKEN_RE = re.compile(
    r"\[([^\]]+)\]|\{([^}]+)\}|\(([^)]+)\)|([A-Za-z0-9_]+)|([,\s]+)|(.)")


class _Topo:
    """The topology used by path expansion: from a Map if given, else from the drawing."""

    def __init__(self, obj: Any, g: Geometry) -> None:
        if hasattr(obj, "regions") and hasattr(obj, "beams") and hasattr(obj, "rooms"):
            self.rooms = list(obj.rooms)
            self.occupancy = list(obj.occupancy)
            self.beams = {b: tuple(ss) for b, ss in obj.beams.items()}
            self.regions = {r: tuple(fs) for r, fs in obj.regions.items()}
        else:
            self.rooms = list(g.rooms)
            self.occupancy = list(g.occupancy)
            self.beams = {b: bm.sides for b, bm in g.beams.items()}
            self.regions = g.derive_regions()
        self.side_beam = {s: b for b, ss in self.beams.items() for s in ss}
        self.regions_of: Dict[str, List[str]] = {}
        for r, fs in self.regions.items():
            for f in fs:
                self.regions_of.setdefault(f, []).append(r)

    def other(self, side: str) -> str:
        a, b = self.beams[self.side_beam[side]]
        return b if side == a else a

    def names(self) -> List[str]:
        return (self.rooms + self.occupancy + list(self.side_beam) + list(self.beams)
                + list(self.regions))


def _tokens_from_string(s: str, topo: _Topo) -> List[Tuple[str, ...]]:
    """Parse ``path_string`` notation (``"A[b1u]C[o1]{o2}(D)B"``) or a separated list."""
    known = sorted(topo.names(), key=len, reverse=True)
    out: List[Tuple[str, ...]] = []
    for m in _TOKEN_RE.finditer(s):
        br, cu, par, word, sep, bad = m.groups()
        if bad is not None:
            raise GeometryError("cannot parse path %r at %r" % (s, bad))
        if sep is not None:
            continue
        if br is not None:
            out.append(("bracket", br.strip()))
        elif cu is not None:
            out.append(("curly", cu.strip()))
        elif par is not None:  # "(D)": an unreported room entry (unreported_visits=True)
            out.append(("name", par.strip()))
        else:
            # Room names are concatenated in path_string ("AC"): split greedily.
            w = word
            while w:
                for n in known:
                    if w.startswith(n):
                        out.append(("name", n))
                        w = w[len(n):]
                        break
                else:
                    raise GeometryError("unknown name %r in path %r" % (w, s))
    return out


def _step_token(step: Any) -> List[Tuple[str, ...]]:
    """Tokens of one engine :class:`~cyber_detectives.engine.Step` (``kind``, ``position``,
    ``sensor``): a crossing contributes the side crossed from and the region reached; every
    other step contributes its position (room, region or occupancy sensor)."""
    kind = getattr(step, "kind", None)
    pos = getattr(step, "position", None)
    if isinstance(pos, str):
        if kind == "cross" and isinstance(getattr(step, "sensor", None), str):
            return [("bracket", step.sensor), ("name", pos)]
        return [("name", pos)]
    if isinstance(step, Mapping) and "position" in step:  # Step.to_dict() form
        if step.get("kind") == "cross" and step.get("sensor"):
            return [("bracket", step["sensor"]), ("name", step["position"])]
        return [("name", step["position"])]
    raise GeometryError("cannot read path step %r" % (step,))


def expand_path(map_or_geometry: Any, path: Any) -> List[Tuple[str, ...]]:
    """Expand a path into an explicit walk over the region model.

    ``path`` may be a list of engine ``Step`` objects (or their ``to_dict()`` forms), a
    ``Result`` of :func:`~cyber_detectives.validate` (its ``path`` is used), a
    ``path_string`` (``"A[b1u]C[o1][o2]B[b2r]AC"``; multi-agent ``{o1}`` and unreported
    ``(D)`` allowed; the regions in between are then guessed), or a sequence of names (rooms,
    occupancy sensors, regions, beam sides) such as a fixture's ``witness_walk_regions``
    (``["A", "R2", "R4", "B", ...]``; consecutive regions imply a beam crossing between them,
    consecutive sides of one beam are a crossing).

    Returns nodes ``("room", A)``, ``("occupancy", o)``, ``("cross", from_side, to_side)``
    and ``("region", R)``, alternating so that a region separates every two non-region nodes.
    Where several regions would do, the first in map order is taken.
    """
    topo = _Topo(map_or_geometry, as_geometry(map_or_geometry))
    toks: List[Tuple[str, ...]] = []
    if hasattr(path, "consistent") and hasattr(path, "path"):  # a validate() Result
        path = path.path if path.path is not None else path.path_string()
    if path is None:
        raise GeometryError("no path")
    if isinstance(path, str):
        toks = _tokens_from_string(path, topo)
    else:
        for item in path:
            if isinstance(item, str):
                toks.extend(_tokens_from_string(item, topo) if re.search(r"[\[\{]", item)
                            else [("name", item)])
            else:
                toks.extend(_step_token(item))

    # Tokens -> raw nodes.
    raw: List[Tuple[str, ...]] = []
    i = 0
    while i < len(toks):
        kind, v = toks[i][0], toks[i][1]
        if kind == "bracket" or kind == "curly":
            if v in topo.side_beam:
                if not raw or raw[-1][0] != "region":
                    raw.append(("region", topo.regions_of[v][0]))
                raw.append(("cross", v, topo.other(v)))
            elif v in topo.occupancy:
                raw.append(("occupancy", v))
            else:
                raise GeometryError("unknown sensor vertex %r in path" % v)
        elif v in topo.rooms:
            raw.append(("room", v))
        elif v in topo.occupancy:
            raw.append(("occupancy", v))
        elif v in topo.regions:
            raw.append(("region", v))
        elif v in topo.side_beam:
            nxt = toks[i + 1][1] if i + 1 < len(toks) else None
            if nxt == topo.other(v):
                raw.append(("cross", v, nxt))
                i += 1
            else:
                raw.append(("region", topo.regions_of[v][0]))
        else:
            raise GeometryError("unknown name %r in path" % v)
        i += 1

    def ins(node: Tuple[str, ...]) -> str:
        return node[1]

    def outs(node: Tuple[str, ...]) -> str:
        return node[2] if node[0] == "cross" else node[1]

    out: List[Tuple[str, ...]] = []
    for node in raw:
        if not out:
            if node[0] == "cross":
                out.append(("region", topo.regions_of[node[1]][0]))
            out.append(node)
            continue
        last = out[-1]
        if node[0] == "region":
            if last[0] == "region":
                if last[1] == node[1]:
                    continue
                cross = _find_crossing(topo, last[1], node[1])
                out.append(cross)
            elif node[1] not in topo.regions_of.get(outs(last), []):
                raise GeometryError("%s does not touch region %s" % (outs(last), node[1]))
            out.append(node)
            continue
        if last[0] == "region":
            if last[1] not in topo.regions_of.get(ins(node), []):
                raise GeometryError("region %s does not touch %s" % (last[1], ins(node)))
            out.append(node)
            continue
        common = [r for r in topo.regions_of.get(outs(last), [])
                  if r in topo.regions_of.get(ins(node), [])]
        if not common:
            raise GeometryError("no region joins %s and %s" % (outs(last), ins(node)))
        out.append(("region", common[0]))
        out.append(node)
    if out and out[-1][0] == "cross":
        out.append(("region", topo.regions_of[out[-1][2]][0]))
    return out


def _find_crossing(topo: _Topo, ra: str, rb: str) -> Tuple[str, str, str]:
    for b, (s1, s2) in topo.beams.items():
        for x, y in ((s1, s2), (s2, s1)):
            if ra in topo.regions_of.get(x, []) and rb in topo.regions_of.get(y, []):
                return ("cross", x, y)
    raise GeometryError("no beam joins regions %s and %s" % (ra, rb))


def path_polyline(map_or_geometry: Any, path: Any) -> List[Point]:
    """Turn an engine witness path into drawable waypoints that stay in free space.

    ``path`` is anything :func:`expand_path` accepts.  The polyline starts at the centre of
    the first room (or the first region's point), enters each room/occupancy rectangle
    through a doorway and touches its centre, crosses each beam through its middle, and
    moves inside regions along collision-free routes (:meth:`Geometry.route`).  Segments
    never cross a wall; they cross a beam band only where the path crosses that beam.
    """
    g = as_geometry(map_or_geometry)
    nodes = expand_path(map_or_geometry, path)
    portals = g.portals()

    def portal(region: str, feature: str) -> Dict[str, Point]:
        try:
            return portals[region][feature]
        except KeyError:
            raise GeometryError("drawing has no doorway between %s and %s"
                                % (region, feature)) from None

    pts: List[Point] = []

    def add(p: Point) -> None:
        if not pts or _dist(pts[-1], p) > 1e-9:
            pts.append(p)

    def go(p: Point, region: str) -> None:
        """Move inside ``region`` from the last point to ``p``."""
        if not pts:
            add(p)
            return
        for q in g.route(pts[-1], p, region)[1:]:
            add(q)

    for k, node in enumerate(nodes):
        prev_r = nodes[k - 1][1] if k > 0 and nodes[k - 1][0] == "region" else None
        next_r = nodes[k + 1][1] if k + 1 < len(nodes) and nodes[k + 1][0] == "region" else None
        kind = node[0]
        if kind == "region":
            if k == 0:
                add(g.region_point(node[1]))
            elif k == len(nodes) - 1:
                go(g.region_point(node[1]), node[1])
            continue
        if kind in ("room", "occupancy"):
            f = node[1]
            if prev_r is not None:
                pp = portal(prev_r, f)
                go(pp["region_point"], prev_r)
                add(pp["feature_point"])
            add(g.feature_center(f))
            if next_r is not None:
                pp = portal(next_r, f)
                add(pp["feature_point"])
                add(pp["region_point"])
        elif kind == "cross":
            # expand_path guarantees a region on both sides of a crossing.
            s1, s2 = node[1], node[2]
            pa = portal(prev_r, s1)  # type: ignore[arg-type]
            pb = portal(next_r, s2)  # type: ignore[arg-type]
            go(pa["region_point"], prev_r)  # type: ignore[arg-type]
            fp = pa["feature_point"]
            add(fp)
            # Leave straight through the beam (perpendicular to it) if that lands in the next
            # region with a clear line; otherwise slide along the centre line to the other
            # side's portal.
            beam = g.beams[g.side_beam[s2]]
            n = beam.normals[s2]
            depth = _dist(pb["region_point"], pb["feature_point"])
            q = (fp[0] + n[0] * depth, fp[1] + n[1] * depth)
            if g.region_at(*q) == next_r and g._clear_path(
                    [fp, q], {("region", next_r), ("side", s2), ("side", s1),
                              ("beam", beam.name)}):
                add(q)
            else:
                add(pb["feature_point"])
                add(pb["region_point"])
    return pts


# ---------------------------------------------------------------------- simulation

class SimulatedWalk:
    """Ground truth of one simulated agent.

    Attributes
    ----------
    story:
        Rooms entered, starting with the start room (list of names).
    history:
        ``[[sensor, "A"|"D"], ...]`` recorded by this agent alone, in time order (single-agent
        semantics: every beam crossing is ``[b, "A"]``; entering/leaving an occupancy region
        is ``[o, "A"]`` / ``[o, "D"]``).
    events:
        ``[(time, kind, name, detail)]``: ``("room", A, None)``, ``("beam", b, (from, to))``,
        ``("enter", o, None)``, ``("leave", o, None)``.  Time = distance / speed.
    points, times:
        The trajectory (straight segments between consecutive points) and the time at each
        point.
    walk:
        The sequence of positions in the region model (rooms, regions, occupancy sensors).
    """

    def __init__(self) -> None:
        self.story: List[str] = []
        self.history: List[List[str]] = []
        self.events: List[Tuple[float, str, str, Any]] = []
        self.points: List[Point] = []
        self.times: List[float] = []
        self.walk: List[str] = []
        self.speed = 1.0

    def __repr__(self) -> str:
        return "SimulatedWalk(story=%r, history=%r)" % ("".join(self.story)
                                                        if all(len(s) == 1 for s in self.story)
                                                        else self.story, self.history)


class MultiWalk:
    """Ground truth of a multi-agent simulation (agent 0 is ``x``).

    ``story`` is x's story; ``history`` the merged observation history of all agents up to
    x's final time (occupancy sensors report the first entry and the last exit of a group);
    ``agents`` the individual walks (others are cut at x's final time as well).
    """

    def __init__(self, story: List[str], history: List[List[str]],
                 agents: List[SimulatedWalk]) -> None:
        self.story = story
        self.history = history
        self.agents = agents

    def __repr__(self) -> str:
        return "MultiWalk(story=%r, history=%r, agents=%d)" % (self.story, self.history,
                                                               len(self.agents))


def _random_point(g: Geometry, rng: random.Random, here: Tuple[str, Optional[str]],
                  anchors: Mapping[Tuple[str, Optional[str]], List[Point]]) -> Point:
    """A random waypoint: usually near a doorway, label or centre point of the current place
    (so that walks get through doors often), sometimes uniform in the canvas."""
    local = anchors.get(here)
    u = rng.random()
    if local and u < 0.7:
        a = local[rng.randrange(len(local))]
        return (a[0] + rng.gauss(0.0, 4.0), a[1] + rng.gauss(0.0, 4.0))
    x0, y0, x1, y1 = g.bbox
    if here[0] in ("room", "occupancy") and u < 0.85:
        r = (g.rooms if here[0] == "room" else g.occupancy)[here[1]]  # type: ignore[index]
        return (rng.uniform(r[0], r[2]), rng.uniform(r[1], r[3]))
    return (rng.uniform(x0, x1), rng.uniform(y0, y1))


def _anchors(g: Geometry) -> Dict[Tuple[str, Optional[str]], List[Point]]:
    """Waypoints per place: for a region, its label point, its portals' region points and
    the feature points just inside its rooms/occupancy rectangles and beyond its beams; for
    a room or occupancy rectangle, its centre and its portals' points on both sides."""
    out: Dict[Tuple[str, Optional[str]], List[Point]] = {}
    for r, feats in g.portals().items():
        reg = out.setdefault(("region", r), [g.region_point(r)])
        for f, pp in feats.items():
            kind = g.feature_kind(f)
            reg.append(pp["region_point"])
            if kind == "side":
                # a point beyond the beam, in the other side's region
                other = g.beams[g.side_beam[f]].other(f)
                for pp2 in (v for feats2 in g.portals().values() for k2, v in feats2.items()
                            if k2 == other):
                    reg.append(pp2["region_point"])
            else:
                reg.append(pp["feature_point"])
                inside = out.setdefault((kind, f), [g.feature_center(f)])
                inside.append(pp["feature_point"])
                inside.append(pp["region_point"])
    return out


_STAND = ("room", "occupancy", "region")  # where an agent may stop


def _runs_events(g: Geometry, runs: Sequence[Tuple[float, float, str, Optional[str]]]
                 ) -> Tuple[Optional[List[Tuple[float, str, str, Any]]], str]:
    """Events of a continuous motion given as owner runs ``(t0, t1, kind, name)``.

    Returns ``(events, "")`` with events ``(t, kind, name, detail)`` -- ``("room", A,
    None)`` on entering a room, ``("enter"|"leave", o, None)`` for an occupancy rectangle,
    ``("beam", b, (from_side, to_side))`` for a beam passage (timed at its middle) -- or
    ``(None, why)`` if the motion touches a wall, stops on a beam, or passes between two
    places the region model does not connect (room -> room, room -> beam, beam entered and
    left on the same side, ...).
    """
    ev: List[Tuple[float, str, str, Any]] = []
    if not runs or runs[0][2] not in _STAND or runs[-1][2] not in _STAND:
        return None, "the motion must start and end in a room, region or occupancy region"
    k = 0
    cur = (runs[0][2], runs[0][3])
    while k + 1 < len(runs):
        nxt = runs[k + 1]
        nk, nn = nxt[2], nxt[3]
        if nk in ("wall", "outside"):
            return None, "touches a wall at t=%.4g" % nxt[0]
        if nk == "region" and nn.startswith("_pocket"):  # type: ignore[union-attr]
            return None, "enters a sealed pocket at t=%.4g" % nxt[0]
        if cur[0] == "region":
            if nk == "room":
                ev.append((nxt[0], "room", nn, None))  # type: ignore[arg-type]
            elif nk == "occupancy":
                ev.append((nxt[0], "enter", nn, None))  # type: ignore[arg-type]
            elif nk == "side":
                # A beam passage: side s1 cells, (band), side s2 cells, then a region.
                b = g.side_beam[nn]  # type: ignore[index]
                m = k + 1
                seen = []
                while m < len(runs) and runs[m][2] in ("side", "beam") and \
                        (runs[m][3] == b or g.side_beam.get(runs[m][3] or "") == b):
                    if runs[m][2] == "side":
                        seen.append(runs[m][3])
                    m += 1
                if m >= len(runs) or runs[m][2] != "region":
                    return None, "beam %s entered at t=%.4g but not left into a region" % (
                        b, nxt[0])
                s1, s2 = seen[0], seen[-1]
                if s1 == s2:
                    # Touched one side's stroke and came back without reaching the centre
                    # line: no recording, but ambiguous enough that we never generate it.
                    return None, "beam %s entered and left on side %s" % (b, s1)
                ev.append(((nxt[0] + runs[m][0]) / 2.0, "beam", b, (s1, s2)))
                cur = (runs[m][2], runs[m][3])
                k = m
                continue
            else:
                return None, "region %s -> %s %s at t=%.4g" % (cur[1], nk, nn, nxt[0])
        elif cur[0] in ("room", "occupancy"):
            if nk != "region":
                return None, "%s %s -> %s %s at t=%.4g" % (cur[0], cur[1], nk, nn, nxt[0])
            if cur[0] == "occupancy":
                ev.append((nxt[0], "leave", cur[1], None))  # type: ignore[arg-type]
        else:  # pragma: no cover - runs only start in a _STAND place
            return None, "starts on %s" % (cur,)
        cur = (nk, nn)
        k += 1
    return ev, ""


def _try_move(g: Geometry, here: Tuple[str, Optional[str]], p: Point, q: Point
              ) -> Optional[List[Tuple[float, str, str, Any]]]:
    """Events of the straight move ``p -> q`` (``t`` in ``[0, 1]``), or ``None`` if the
    move is not allowed (see :func:`_runs_events`)."""
    runs = g.segment_runs(p, q)
    if (runs[0][2], runs[0][3]) != here:
        return None
    return _runs_events(g, runs)[0]


def trace_polyline(geometry: Any, points: Sequence[Point]) -> SimulatedWalk:
    """Replay a polyline geometrically: what would an agent walking it report and trigger?

    Returns a :class:`SimulatedWalk` whose ``story`` is the rooms entered (starting with the
    room containing the first point, if any), ``history`` the single-agent recordings and
    ``walk`` the places visited in the region model.  Raises :class:`GeometryError` if the
    polyline touches a wall, stops on a beam or makes a move the region model has no
    transition for.  Used to check :func:`path_polyline` and the simulator.
    """
    g = as_geometry(geometry)
    pts = [_pt(p) for p in points]
    if not pts:
        raise GeometryError("empty polyline")
    runs: List[Tuple[float, float, str, Optional[str]]] = []
    t = 0.0
    times = [0.0]
    for p, q in zip(pts, pts[1:]):
        length = _dist(p, q)
        if length == 0.0:
            times.append(t)
            continue
        for ta, tb, kind, name in g.segment_runs(p, q):
            a, b = t + ta * length, t + tb * length
            if runs and (runs[-1][2], runs[-1][3]) == (kind, name):
                runs[-1] = (runs[-1][0], b, kind, name)
            else:
                runs.append((a, b, kind, name))
        t += length
        times.append(t)
    if len(pts) == 1:
        k, n = g.location(*pts[0])
        runs = [(0.0, 0.0, k, n)]
    ev, why = _runs_events(g, runs)
    if ev is None:
        raise GeometryError("polyline is not a legal motion: " + why)
    w = SimulatedWalk()
    w.points, w.times = pts, times
    if runs[0][2] == "room":
        w.events.append((0.0, "room", runs[0][3], None))  # type: ignore[arg-type]
    w.events += ev
    _fill_story_history(g, w)
    return w


def _random_start(g: Geometry, rng: random.Random, kinds: Sequence[str]
                  ) -> Tuple[Point, Tuple[str, Optional[str]]]:
    x0, y0, x1, y1 = g.bbox
    for _ in range(100000):
        if "room" in kinds and (len(kinds) == 1 or rng.random() < 0.5) and g.rooms:
            r = g.rooms[rng.choice(sorted(g.rooms))]
            p = (rng.uniform(r[0], r[2]), rng.uniform(r[1], r[3]))
        else:
            p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        loc = g.location(*p)
        if loc[0] in kinds and not (loc[0] == "region" and loc[1].startswith("_pocket")):
            return p, loc
    raise GeometryError("could not place an agent")  # pragma: no cover


def _interior_point(g: Geometry, rng: random.Random, kind: str, name: str) -> Point:
    r = (g.rooms if kind == "room" else g.occupancy)[name]
    m = min(8.0, (r[2] - r[0]) / 4.0, (r[3] - r[1]) / 4.0)
    return (rng.uniform(r[0] + m, r[2] - m), rng.uniform(r[1] + m, r[3] - m))


def _excursion(g: Geometry, rng: random.Random, p: Point, here: Tuple[str, Optional[str]]
               ) -> List[Point]:
    """Waypoints that take the agent from ``p`` into a random neighbouring place: from a
    region through a random doorway into a room/occupancy rectangle or across a random beam;
    from a room/occupancy rectangle out through a random doorway and on to a random point of
    that region.  Only a plan: every segment is still checked when it is walked."""
    portals = g.portals()

    def jitter(q: Point, region: str) -> Point:
        c = (q[0] + rng.gauss(0.0, 2.0), q[1] + rng.gauss(0.0, 2.0))
        return c if g.region_at(*c) == region else q

    try:
        if here[0] == "region":
            region = here[1]
            f = rng.choice(sorted(portals[region]))  # type: ignore[index]
            pp = portals[region][f]  # type: ignore[index]
            rp = jitter(pp["region_point"], region)  # type: ignore[arg-type]
            pts = g.route(p, rp, region)[1:]
            kind = g.feature_kind(f)
            if kind == "side":
                beam = g.beams[g.side_beam[f]]
                other = beam.other(f)
                n = beam.normals[other]
                fp = pp["feature_point"]
                depth = _dist(pp["region_point"], fp)
                pts.append((fp[0] + n[0] * depth, fp[1] + n[1] * depth))
            else:
                pts.append(pp["feature_point"])
                pts.append(_interior_point(g, rng, kind, f))
            return pts
        f = here[1]
        regions = sorted(r for r in portals if f in portals[r])
        region = rng.choice(regions)
        pp = portals[region][f]  # type: ignore[index]
        pts = [pp["feature_point"], pp["region_point"]]
        x0, y0, x1, y1 = g.bbox
        for _ in range(50):
            c = (rng.uniform(x0, x1), rng.uniform(y0, y1))
            if g.region_at(*c) == region:
                pts += g.route(pp["region_point"], c, region)[1:]
                break
        return pts
    except GeometryError:
        return []


def _simulate(g: Geometry, rng: random.Random, steps: int, start_kinds: Sequence[str],
              speed: float) -> SimulatedWalk:
    """``steps`` moves of one agent: random straight moves (30%) and excursions to a random
    neighbouring place (70%, see :func:`_excursion`).  Every segment is checked against the
    decomposition (:func:`_try_move`) and the events come from that check alone."""
    anchors = _anchors(g)
    w = SimulatedWalk()
    w.speed = speed
    p, here = _random_start(g, rng, start_kinds)
    w.points.append(p)
    w.times.append(0.0)
    if here[0] == "room":
        w.events.append((0.0, "room", here[1], None))  # type: ignore[arg-type]
    t = 0.0
    accepted = attempts = 0
    while accepted < steps and attempts < steps * 60:
        attempts += 1
        if rng.random() < 0.3:
            plan = [_random_point(g, rng, here, anchors)]
        else:
            plan = _excursion(g, rng, p, here)
        moved = False
        for q in plan:
            if g.location(*q)[0] not in _STAND:
                break
            ev = _try_move(g, here, p, q)
            if ev is None:
                break
            length = _dist(p, q)
            for tt, kind, name, detail in ev:
                w.events.append((t + tt * length / speed, kind, name, detail))
            t += length / speed
            p = q
            here = g.location(*q)  # type: ignore[assignment]
            w.points.append(p)
            w.times.append(t)
            moved = True
        accepted += moved
    return w


def _finish_single(g: Geometry, w: SimulatedWalk) -> SimulatedWalk:
    """Cut the walk at its last room entry so that it ends inside the last story room."""
    last = max(i for i, e in enumerate(w.events) if e[1] == "room")
    t_end = w.events[last][0]
    # Cut the trajectory just inside the room: the midpoint of the room run of that move.
    k = bisect.bisect_left(w.times, t_end)
    k = max(1, min(k, len(w.times) - 1))
    p, q = w.points[k - 1], w.points[k]
    runs = g.segment_runs(p, q)
    room = w.events[last][2]
    seg_t0, seg_t1 = w.times[k - 1], w.times[k]
    cut, tcut, best = q, seg_t1, math.inf
    for ta, tb, kind, name in runs:
        if kind == "room" and name == room:
            ts = seg_t0 + ta * (seg_t1 - seg_t0)
            if abs(ts - t_end) < best:
                tm = (ta + tb) / 2.0
                best = abs(ts - t_end)
                cut = _lerp(p, q, tm)
                tcut = seg_t0 + tm * (seg_t1 - seg_t0)
    if last == 0:
        cut, tcut, k = w.points[0], 0.0, 0
        w.points = [cut]
        w.times = [0.0]
    else:
        w.points = w.points[:k] + [cut]
        w.times = w.times[:k] + [tcut]
    w.events = w.events[:last + 1]
    _fill_story_history(g, w)
    return w


def _fill_story_history(g: Geometry, w: SimulatedWalk) -> None:
    w.story = [e[2] for e in w.events if e[1] == "room"]
    w.history = []
    w.walk = []
    for tm, kind, name, detail in w.events:
        if kind == "beam":
            w.history.append([name, "A"])
        elif kind == "enter":
            w.history.append([name, "A"])
        elif kind == "leave":
            w.history.append([name, "D"])
    # Region-model walk (rooms, regions, occupancy), for debugging and drawing.
    pos: List[str] = []
    for i in range(len(w.points) - 1):
        for _, _, kind, name in g.segment_runs(w.points[i], w.points[i + 1]):
            if kind in _STAND and (not pos or pos[-1] != name):
                pos.append(name)  # type: ignore[arg-type]
    if not pos and w.points:
        pos.append(g.location(*w.points[0])[1])  # type: ignore[arg-type]
    w.walk = pos


def _cut(g: Geometry, w: SimulatedWalk, t_f: float) -> None:
    """Truncate a walk at time ``t_f`` (events and trajectory)."""
    w.events = [e for e in w.events if e[0] <= t_f]
    k = bisect.bisect_right(w.times, t_f)
    if k < len(w.times):
        t0, t1 = w.times[k - 1], w.times[k]
        q = _lerp(w.points[k - 1], w.points[k], (t_f - t0) / (t1 - t0) if t1 > t0 else 0.0)
        w.points = w.points[:k] + [q]
        w.times = w.times[:k] + [t_f]
    _fill_story_history(g, w)


def _coerce_rng(rng: Union[random.Random, int, None]) -> random.Random:
    if isinstance(rng, random.Random):
        return rng
    return random.Random(rng)


def simulate_walk(geometry: Any, rng: Union[random.Random, int, None] = None,
                  steps: int = 30) -> SimulatedWalk:
    """Simulate one agent moving through the drawing; return its story and history.

    The agent starts at a random point of a random room and makes ``steps`` straight moves
    to random targets (uniform in the canvas, or near a doorway/room/region point), each
    move rejected if it would touch a wall, stop on a beam, or pass between two places the
    region model does not connect (e.g. through a beam stroke that lies inside a room).
    It records every room entry (the story), every beam crossing and every occupancy
    entry/exit (the history).  The walk is then cut at its last room entry, so that it ends
    inside the last story room after the last recording.  The resulting ``(story,
    history)`` is consistent by construction (single agent; also consistent in multi-agent
    mode).

    ``rng`` is a :class:`random.Random` or a seed.
    """
    g = as_geometry(geometry)
    if not g.rooms:
        raise GeometryError("geometry %r has no rooms to start in" % g.name)
    r = _coerce_rng(rng)
    w = _simulate(g, r, steps, ("room",), 1.0)
    return _finish_single(g, w)


def simulate_multi(geometry: Any, rng: Union[random.Random, int, None] = None,
                   agents: int = 3, steps: int = 30) -> MultiWalk:
    """Simulate ``agents`` agents at random speeds; agent 0 is ``x``.

    ``x`` starts in a room and its walk is cut at its last room entry (time ``t_f``), as in
    :func:`simulate_walk`.  The others start anywhere outside the occupancy regions (all
    sensors are inactive at ``t_0``) and are cut at ``t_f``.  The merged history lists every
    beam crossing by anyone, and for each occupancy sensor an activation when it becomes
    occupied and a deactivation when it becomes empty.  Room entries of other agents are
    not observed.  ``(story, history)`` is consistent in multi-agent mode by construction.
    """
    g = as_geometry(geometry)
    r = _coerce_rng(rng)
    x = _finish_single(g, _simulate(g, r, steps, ("room",), r.uniform(0.5, 2.0)))
    t_f = x.times[-1] if x.times else 0.0
    walks = [x]
    for _ in range(agents - 1):
        w = _simulate(g, r, steps, ("room", "region"), r.uniform(0.5, 2.0))
        _cut(g, w, t_f)
        walks.append(w)
    merged: List[Tuple[float, int, str, str, Any]] = []
    for i, w in enumerate(walks):
        for tm, kind, name, detail in w.events:
            if kind != "room":
                merged.append((tm, i, kind, name, detail))
    merged.sort(key=lambda e: (e[0], e[1]))
    count: Dict[str, int] = {}
    history: List[List[str]] = []
    for tm, i, kind, name, detail in merged:
        if kind == "beam":
            history.append([name, "A"])
        elif kind == "enter":
            count[name] = count.get(name, 0) + 1
            if count[name] == 1:
                history.append([name, "A"])
        elif kind == "leave":
            count[name] -= 1
            if count[name] == 0:
                history.append([name, "D"])
    return MultiWalk(list(x.story), history, walks)
