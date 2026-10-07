"""Maps: rooms, beam detectors, occupancy sensors and the free regions that join them.

A map is the paper's workspace reduced to its topology (STAR §2, Fig. 2-3; ICRA §II):

- **rooms** (the set ``C_p``): entering one is a visit that the story must report;
- **beam detectors**, each with two **sides** (the vertices ``b1u``/``b1d`` of the papers),
  listed in the order the original Java code passes them to ``BeamDetector``;
- **occupancy sensors** (``o1``, ...), small regions whose entry/exit is recorded;
- **regions**: the connected components of the free workspace (the papers' ``R_k``). Each
  region *touches* a set of features (rooms, beam sides, occupancy sensors); each beam side
  touches exactly one region.

The connectivity graph ``G`` of the papers (STAR Fig. 3(a)) is derived: two features are
adjacent iff they touch a common region.  The engine works on regions, not on ``G``
(see ``docs/DESIGN.md``, "Semantics").

Maps are read from and written to the JSON format of ``tests/fixtures/README.md`` (plus an
explicit ``regions`` key).  Builtin maps live in ``data/*.json``.
"""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

__all__ = [
    "Map",
    "MapError",
    "builtin_map",
    "builtin_map_names",
    "load_map",
]

#: Names of rooms, sensors, beam sides and regions.  ``+``/``-`` and separators are excluded
#: because history strings use them (``o1+``, ``b1 o1``).
_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


class MapError(ValueError):
    """A map description is inconsistent (unknown names, a beam side in two regions, ...)."""


def _check_name(what: str, name: Any) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise MapError("%s name %r must be a non-empty string of letters, digits or '_'"
                       % (what, name))
    return name


class Map:
    """A sensor-network map in the region model.

    Parameters
    ----------
    name:
        Short identifier (``"star_fig2"``).
    rooms:
        Room names, in order.  Every room is in ``C_p``: entering it is a reported visit.
    beams:
        ``{beam: (side_0, side_1)}``, or a list of ``[beam, [side_0, side_1]]`` pairs (same
        meaning; the JSON form that keeps the order of all-digit names, see :meth:`to_dict`).
        The side order is the original's ``sensorVertices`` order; it only matters for
        ``compat="original"``.
    occupancy:
        Occupancy sensor names, in order.
    regions:
        ``{region: [feature, ...]}``, a list of ``[region, [feature, ...]]`` pairs (same
        meaning), or a list of feature lists (named ``R1``, ``R2``, ... in order, skipping
        names already taken by rooms, beams, beam sides or occupancy sensors).  A non-empty
        list whose items are all ``[string, list]`` pairs is the pair form.  Features are
        room names, beam side names and occupancy sensor names.
    geometry:
        Optional drawing/hit-testing data (see ``geometry.py``).  Passed through untouched.
    title:
        Optional one-line human description (shown by ``cyber-detectives maps``).
    source, provenance:
        Free-form provenance (strings or dicts), passed through untouched.
    edges:
        Optional edge list of ``G``.  If given it must equal the derived ``G`` (as a set); its
        order is kept for ``to_dict`` because the original Java code's results depend on the
        neighbour insertion order (``compat="original"``).
    vertex_order:
        Optional creation order of the features in the original code (see
        ``tests/fixtures/golden/README.md``); passed through for ``compat="original"``.

    Raises
    ------
    MapError
        If the description is inconsistent.  The message names the offending item.
    """

    def __init__(
        self,
        name: str,
        rooms: Sequence[str],
        beams: Mapping[str, Sequence[str]],
        occupancy: Sequence[str],
        regions: Union[Mapping[str, Sequence[str]], Sequence[Sequence[str]]],
        *,
        geometry: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        source: Optional[Any] = None,
        provenance: Optional[Any] = None,
        edges: Optional[Sequence[Sequence[str]]] = None,
        vertex_order: Optional[Sequence[str]] = None,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise MapError("map name must be a non-empty string")
        self.name = name
        self.rooms: Tuple[str, ...] = tuple(_check_name("room", r) for r in rooms)
        self.beams: Dict[str, Tuple[str, str]] = {}
        for b, sides in _beam_items(beams):
            _check_name("beam", b)
            if not isinstance(sides, (list, tuple)):
                raise MapError("beam %r must have exactly two sides, got %r" % (b, sides))
            sides = tuple(sides)
            if len(sides) != 2:
                raise MapError("beam %r must have exactly two sides, got %r" % (b, list(sides)))
            for s in sides:
                _check_name("beam side", s)
            if sides[0] == sides[1]:
                raise MapError("beam %r has two sides with the same name %r" % (b, sides[0]))
            self.beams[b] = (sides[0], sides[1])
        self.occupancy: Tuple[str, ...] = tuple(_check_name("occupancy sensor", o)
                                                for o in occupancy)

        if isinstance(regions, Mapping):
            reg_items = [(k, v) for k, v in regions.items()]
        elif not isinstance(regions, (list, tuple)):
            raise MapError("regions must be an object or a list, got %r" % (regions,))
        elif _is_pair_list(regions):
            reg_items = [(p[0], p[1]) for p in regions]
        else:
            reg_list = list(regions)
            reg_items = list(zip(_auto_region_names(len(reg_list), self.rooms, self.beams,
                                                    self.occupancy), reg_list))
        self.regions: Dict[str, Tuple[str, ...]] = {}
        for rname, feats in reg_items:
            _check_name("region", rname)
            if isinstance(feats, str):
                raise MapError("region %r: features must be a list of names, not a string"
                               % rname)
            if not isinstance(feats, (list, tuple)):
                raise MapError("region %r: features must be a list of names, got %r"
                               % (rname, feats))
            self.regions[rname] = tuple(feats)

        self.geometry = geometry
        self.title = title
        self.source = source
        self.provenance = provenance
        self.vertex_order: Optional[Tuple[str, ...]] = (
            tuple(vertex_order) if vertex_order is not None else None)
        self._edge_order: Optional[List[Tuple[str, str]]] = (
            [(str(e[0]), str(e[1])) for e in edges] if edges is not None else None)

        self._index()
        self._validate()

    # ------------------------------------------------------------------ construction

    def _index(self) -> None:
        self.side_beam: Dict[str, str] = {s: b for b, ss in self.beams.items() for s in ss}
        self._room_set = frozenset(self.rooms)
        self._occ_set = frozenset(self.occupancy)
        #: feature -> regions it touches, in region order
        self.regions_of: Dict[str, Tuple[str, ...]] = {}
        acc: Dict[str, List[str]] = {f: [] for f in self.features}
        for rname, feats in self.regions.items():
            for f in feats:
                if isinstance(f, str) and f in acc and rname not in acc[f]:
                    acc[f].append(rname)
        self.regions_of = {f: tuple(v) for f, v in acc.items()}

    def _validate(self) -> None:
        # 1. all names distinct across kinds
        seen: Dict[str, str] = {}
        for kind, names in (("room", self.rooms), ("beam", tuple(self.beams)),
                            ("beam side", tuple(self.side_beam)),
                            ("occupancy sensor", self.occupancy),
                            ("region", tuple(self.regions))):
            for n in names:
                if n in seen:
                    raise MapError("name %r is used twice (as %s and as %s)"
                                   % (n, seen[n], kind))
                seen[n] = kind
        # 2. region features are known features, no duplicates
        feats = set(self.features)
        for rname, fs in self.regions.items():
            if not fs:
                raise MapError("region %r touches no feature" % rname)
            for f in fs:
                if not isinstance(f, str) or f not in feats:
                    what = seen.get(f) if isinstance(f, str) else None
                    hint = (" (a %s; regions touch rooms, beam sides and occupancy sensors)"
                            % what) if what else ""
                    raise MapError("region %r touches unknown feature %r%s" % (rname, f, hint))
            if len(set(fs)) != len(fs):
                raise MapError("region %r lists a feature twice: %r" % (rname, list(fs)))
        # 3. each beam side touches exactly one region
        for s, b in self.side_beam.items():
            n = len(self.regions_of[s])
            if n != 1:
                raise MapError(
                    "beam side %r (of %r) touches %d regions %r; each side must touch exactly one"
                    % (s, b, n, list(self.regions_of[s])))
        # 4. optional edge list equals the derived G
        if self._edge_order is not None:
            given = set()
            for a, b in self._edge_order:
                if a not in feats or b not in feats:
                    raise MapError("edge %r-%r names an unknown feature" % (a, b))
                if a == b:
                    raise MapError("edge %r-%r is a self-loop" % (a, b))
                given.add(frozenset((a, b)))
            derived = {frozenset(e) for e in self.edges()}
            if given != derived:
                missing = sorted(tuple(sorted(e)) for e in derived - given)
                extra = sorted(tuple(sorted(e)) for e in given - derived)
                raise MapError("map %r: edge list does not match the regions: edges implied by "
                               "the regions but not listed %r; listed but not implied %r"
                               % (self.name, missing, extra))
        # 5. optional vertex order is a permutation of the features
        if self.vertex_order is not None and sorted(self.vertex_order) != sorted(feats):
            raise MapError("map %r: vertex_order %r is not a permutation of the features %r"
                           % (self.name, list(self.vertex_order), sorted(feats)))

    # ------------------------------------------------------------------ queries

    @property
    def sides(self) -> Tuple[str, ...]:
        """All beam sides, beam by beam, in ``sensorVertices`` order."""
        return tuple(s for ss in self.beams.values() for s in ss)

    @property
    def features(self) -> Tuple[str, ...]:
        """The vertices of ``G``: rooms, then beam sides, then occupancy sensors."""
        return self.rooms + self.sides + self.occupancy

    @property
    def sensors(self) -> Tuple[str, ...]:
        """All sensor names: beams, then occupancy sensors."""
        return tuple(self.beams) + self.occupancy

    def kind(self, name: str) -> str:
        """Return ``"room"``, ``"beam"``, ``"side"``, ``"occupancy"`` or ``"region"``.

        Raises ``KeyError`` for an unknown name.
        """
        if name in self._room_set:
            return "room"
        if name in self.beams:
            return "beam"
        if name in self.side_beam:
            return "side"
        if name in self._occ_set:
            return "occupancy"
        if name in self.regions:
            return "region"
        raise KeyError(name)

    def is_room(self, name: str) -> bool:
        return name in self._room_set

    def is_beam(self, name: str) -> bool:
        return name in self.beams

    def is_occupancy(self, name: str) -> bool:
        return name in self._occ_set

    def other_side(self, side: str) -> str:
        """The opposite side of the beam that ``side`` belongs to."""
        a, b = self.beams[self.side_beam[side]]
        return b if side == a else a

    def side_region(self, side: str) -> str:
        """The (unique) region a beam side touches."""
        return self.regions_of[side][0]

    def edges(self) -> List[Tuple[str, str]]:
        """Edges of the connectivity graph ``G`` (STAR Fig. 3(a)), each sorted, list sorted."""
        out = set()
        for fs in self.regions.values():
            for i in range(len(fs)):
                for j in range(i + 1, len(fs)):
                    out.add(tuple(sorted((fs[i], fs[j]))))
        return sorted(out)  # type: ignore[arg-type]

    def adjacency(self) -> Dict[str, List[str]]:
        """``G`` as ``{feature: [neighbours in feature order]}``."""
        order = {f: i for i, f in enumerate(self.features)}
        adj: Dict[str, set] = {f: set() for f in self.features}
        for a, b in self.edges():
            adj[a].add(b)
            adj[b].add(a)
        return {f: sorted(ns, key=order.__getitem__) for f, ns in adj.items()}

    def region_graph_edges(self) -> List[Tuple[str, str]]:
        """Edges of the region graph (STAR Fig. 3(b)): region -- room / beam / occupancy sensor.

        Beam sides are collapsed to their beam.  Each pair is ``sorted``; the list is sorted.
        """
        out = set()
        for rname, fs in self.regions.items():
            for f in fs:
                g = self.side_beam.get(f, f)
                out.add(tuple(sorted((rname, g))))
        return sorted(out)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ I/O

    @classmethod
    def from_edges(
        cls,
        name: str,
        rooms: Sequence[str],
        beams: Mapping[str, Sequence[str]],
        occupancy: Sequence[str],
        edges: Sequence[Sequence[str]],
        **kwargs: Any,
    ) -> "Map":
        """Build a map from the connectivity graph ``G`` alone.

        Each maximal clique of ``G`` becomes one region (named ``R1``, ``R2``, ... in the order
        of their first feature in ``rooms + sides + occupancy``, skipping names that are
        already taken, as in :class:`Map`); a feature with no edges gets a region of its own.

        **Caveat.** The cliques are the true regions only when ``G`` has no "spurious" maximal
        cliques.  Three regions that pairwise share features create a triangle that is not a
        region, and a region whose features all lie in such a clique is swallowed by it: in
        ICRA Fig. 2, ``A-B`` (R1), ``A-o1`` (R3) and ``B-o1`` (R2) give the clique
        ``{A, B, o1}`` in place of R1 = ``{A, B}``.  For a map with known regions, pass them
        explicitly.

        The verdicts of the default engine do not change, though, whenever ``G`` comes from
        some valid region map: a beam side ``s`` touches one region ``R_s`` and its neighbours
        are exactly ``R_s``, so the only maximal clique holding ``s`` is ``R_s`` itself; and a
        spurious clique holds only rooms and occupancy sensors that pairwise share a true
        region, so any move through it (or wait in it) can be made through (or in) a true
        region instead.  Region names, and so witness paths, may differ.  (One exception: a
        feature with no edges gets a region of its own, while in the true map it might touch
        no region at all; ``G`` cannot tell the two apart, and for a room they differ on
        stories like ``AA``.)  A beam side in two
        maximal cliques (impossible for such a ``G``) raises ``MapError``.
        """
        # (malformed sides are reported by the constructor)
        sides = [s for _b, ss in _beam_items(beams) if isinstance(ss, (list, tuple)) for s in ss]
        feats = list(rooms) + sides + list(occupancy)
        known = set(feats)
        adj: Dict[str, set] = {f: set() for f in feats}
        for e in edges:
            a, b = e[0], e[1]
            if a not in known or b not in known:
                raise MapError("edge %r-%r names an unknown feature" % (a, b))
            if a == b:
                raise MapError("edge %r-%r is a self-loop" % (a, b))
            adj[a].add(b)
            adj[b].add(a)
        order = {f: i for i, f in enumerate(feats)}
        cliques = _maximal_cliques(adj)
        cliques = [sorted(c, key=order.__getitem__) for c in cliques]
        cliques.sort(key=lambda c: [order[f] for f in c])
        kwargs.setdefault("edges", [tuple(e) for e in edges])
        return cls(name, rooms, beams, occupancy, cliques, **kwargs)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Map":
        """Build a map from the fixture JSON format (``tests/fixtures/README.md``).

        ``beams`` may be a ``{name: sides}`` object or a list of ``[name, sides]`` pairs;
        ``regions`` a ``{name: features}`` object, a list of ``[name, features]`` pairs or a
        list of feature lists (see :class:`Map`).  Without
        ``regions`` the regions come from ``edges`` via :meth:`from_edges` (see its caveat).
        With both, the edges must equal the derived ``G``.  Unknown keys (``expected``,
        ``verified_by``, ...) are ignored.
        """
        if not isinstance(d, Mapping):
            raise MapError("a map must be a JSON object, got %s" % type(d).__name__)
        for key in ("name", "rooms", "beams", "occupancy"):
            if key not in d:
                raise MapError("map is missing the key %r" % key)
        kw: Dict[str, Any] = {
            "geometry": copy.deepcopy(d.get("geometry")),
            "title": d.get("title"),
            "source": d.get("source"),
            "provenance": copy.deepcopy(d.get("provenance")),
            "vertex_order": d.get("vertex_order"),
        }
        if d.get("regions") is not None:
            return cls(d["name"], d["rooms"], d["beams"], d["occupancy"], d["regions"],
                       edges=d.get("edges"), **kw)
        if d.get("edges") is None:
            raise MapError("map %r has neither 'regions' nor 'edges'" % d["name"])
        return cls.from_edges(d["name"], d["rooms"], d["beams"], d["occupancy"], d["edges"],
                              **kw)

    def to_dict(self) -> Dict[str, Any]:
        """The fixture JSON form, with ``regions`` and ``edges`` (and the passthrough keys).

        ``beams`` and ``regions`` are written as JSON objects, except that each of them
        becomes a list of ``[name, value]`` pairs when one of its names is made of digits
        only (``"7"``): JavaScript objects iterate such keys first, in numeric order, so the
        object form would not keep the map order in the browser engine.  :meth:`from_dict`
        (Python and JS) reads both forms.
        """
        out: Dict[str, Any] = {"name": self.name}
        if self.title is not None:
            out["title"] = self.title
        if self.source is not None:
            out["source"] = self.source
        if self.provenance is not None:
            out["provenance"] = copy.deepcopy(self.provenance)
        out["rooms"] = list(self.rooms)
        out["beams"] = _named_json([(b, list(ss)) for b, ss in self.beams.items()])
        out["occupancy"] = list(self.occupancy)
        if self.vertex_order is not None:
            out["vertex_order"] = list(self.vertex_order)
        out["regions"] = _named_json([(r, list(fs)) for r, fs in self.regions.items()])
        out["edges"] = [list(e) for e in (self._edge_order if self._edge_order is not None
                                          else self._region_edge_order())]
        if self.geometry is not None:
            out["geometry"] = copy.deepcopy(self.geometry)
        return out

    def _region_edge_order(self) -> List[Tuple[str, str]]:
        """Deterministic edge order for maps without an explicit edge list: region by region,
        pairs in the region's feature order, first occurrence kept."""
        seen = set()
        out = []
        for fs in self.regions.values():
            for i in range(len(fs)):
                for j in range(i + 1, len(fs)):
                    key = frozenset((fs[i], fs[j]))
                    if key not in seen:
                        seen.add(key)
                        out.append((fs[i], fs[j]))
        return out

    def __repr__(self) -> str:
        return ("Map(%r, rooms=%r, beams=%r, occupancy=%r, regions=%d)"
                % (self.name, list(self.rooms), list(self.beams), list(self.occupancy),
                   len(self.regions)))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Map):
            return NotImplemented
        return (self.name == other.name and self.rooms == other.rooms
                and self.beams == other.beams and self.occupancy == other.occupancy
                and {k: frozenset(v) for k, v in self.regions.items()}
                == {k: frozenset(v) for k, v in other.regions.items()})

    __hash__ = None  # type: ignore[assignment]  # mutable passthrough fields


def _beam_items(beams: Any) -> List[Tuple[Any, Any]]:
    """``beams`` as ``(name, sides)`` items: a mapping, or a list of ``[name, sides]`` pairs."""
    if isinstance(beams, Mapping):
        return list(beams.items())
    if not isinstance(beams, (list, tuple)):
        raise MapError("beams must be an object or a list of [name, sides] pairs, got %r"
                       % (beams,))
    for i, p in enumerate(beams):
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise MapError("beams item %d (%r) is not a [name, sides] pair" % (i + 1, p))
    return [(p[0], p[1]) for p in beams]


def _is_pair_list(items: Sequence[Any]) -> bool:
    """A non-empty list whose items are all ``[string, list]`` pairs (named regions)."""
    return bool(items) and all(
        isinstance(p, (list, tuple)) and len(p) == 2 and isinstance(p[0], str)
        and isinstance(p[1], (list, tuple)) for p in items)


def _all_digits(name: str) -> bool:
    return name.isascii() and name.isdigit()


def _named_json(items: List[Tuple[str, Any]]) -> Any:
    """``{name: value}``, or ``[[name, value], ...]`` if a name is made of digits only."""
    if any(_all_digits(n) for n, _v in items):
        return [[n, v] for n, v in items]
    return dict(items)


def _auto_region_names(count: int, rooms: Iterable[str], beams: Mapping[str, Sequence[str]],
                       occupancy: Iterable[str]) -> List[str]:
    """Names for ``count`` unnamed regions: ``R1``, ``R2``, ... in order, skipping every name
    already used by a room, beam, beam side or occupancy sensor (a room called ``R1`` makes
    the regions ``R2``, ``R3``, ...)."""
    taken = set(rooms) | set(beams) | {s for ss in beams.values() for s in ss} | set(occupancy)
    out: List[str] = []
    i = 0
    while len(out) < count:
        i += 1
        name = "R%d" % i
        if name not in taken:
            out.append(name)
    return out


def _degeneracy_order(nb: Mapping[str, set]) -> List[str]:
    """Vertices in a degeneracy order (repeatedly remove a vertex of minimum remaining
    degree), ``O(V + E)`` with degree buckets (insertion-ordered, so deterministic)."""
    deg = {v: len(ns) for v, ns in nb.items()}
    buckets: Dict[int, Dict[str, None]] = {}
    for v, d in deg.items():
        buckets.setdefault(d, {})[v] = None
    order: List[str] = []
    done: set = set()
    d = 0
    while len(order) < len(deg):
        d = max(d - 1, 0)  # removing a vertex lowers its neighbours' degree by at most 1
        while not buckets.get(d):
            d += 1
        v = next(iter(buckets[d]))
        del buckets[d][v]
        order.append(v)
        done.add(v)
        for u in nb[v]:
            if u not in done:
                del buckets[deg[u]][u]
                deg[u] -= 1
                buckets.setdefault(deg[u], {})[u] = None
    return order


def _maximal_cliques(adj: Mapping[str, Iterable[str]]) -> List[List[str]]:
    """All maximal cliques; isolated vertices are 1-cliques.

    Bron-Kerbosch with pivoting, started from each vertex in a degeneracy order
    (Eppstein, Loeffler and Strash 2010), so the work is near-linear on sparse graphs: each
    top-level call only sees one vertex's later and earlier neighbours.
    """
    nb = {v: set(ns) for v, ns in adj.items()}
    out: List[List[str]] = []

    def bk(r: List[str], p: set, x: set) -> None:
        if not p and not x:
            out.append(sorted(r))
            return
        pivot = max(p | x, key=lambda u: (len(nb[u] & p), u))
        for v in sorted(p - nb[pivot]):
            bk(r + [v], p & nb[v], x & nb[v])
            p.discard(v)
            x.add(v)

    order = _degeneracy_order(nb)
    pos = {v: i for i, v in enumerate(order)}
    for v in order:
        if not nb[v]:
            out.append([v])
            continue
        later = {u for u in nb[v] if pos[u] > pos[v]}
        earlier = nb[v] - later
        bk([v], later, earlier)
    return out


# ---------------------------------------------------------------------- loading

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load_map(path: Union[str, "os.PathLike[str]"]) -> Map:
    """Load a map from a JSON file in the fixture format (see :meth:`Map.from_dict`)."""
    with open(path, "r", encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            raise MapError("%s: not valid JSON: %s" % (os.fspath(path), e)) from None
    return Map.from_dict(d)


def builtin_map_names() -> List[str]:
    """Names of the builtin maps (``data/*.json``), sorted."""
    try:
        names = os.listdir(_DATA_DIR)
    except FileNotFoundError:  # pragma: no cover - broken installation
        return []
    return sorted(n[:-5] for n in names if n.endswith(".json"))


def builtin_map(name: str, *, geometry: bool = True) -> Map:
    """Return a builtin map by name (``"star_fig2"``, ``"star_fig1"``, ``"icra_fig1"``,
    ``"icra_fig2"``).

    With ``geometry=True`` (default), drawing data from ``data/geometry/<name>.json`` is
    attached as ``Map.geometry`` when that file exists and the map has none inline (the file's
    ``"geometry"`` member if it has one, else the whole object).
    """
    names = builtin_map_names()
    if name not in names:
        raise KeyError("unknown builtin map %r; available: %s" % (name, ", ".join(names)))
    m = load_map(os.path.join(_DATA_DIR, name + ".json"))
    if geometry and m.geometry is None:
        gpath = os.path.join(_DATA_DIR, "geometry", name + ".json")
        if os.path.exists(gpath):
            with open(gpath, "r", encoding="utf-8") as f:
                g = json.load(f)
            m.geometry = g.get("geometry", g) if isinstance(g, dict) else g
    return m
