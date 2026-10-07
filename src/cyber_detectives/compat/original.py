"""Line-by-line port of the original Cyber Detectives Java code (non-GUI part).

Source: https://github.com/arc-l/cyber-detective at commit
``55f57f8b307047df615acdd2024140ad4030bbd4``, files
``cyber-detective-source/projects/cyberDetective/{Vertex,Edge,Graph,Story,
ObservationHistory,SensorRecording,Sensor,BeamDetector,OccupancySensor,DetectiveGame,
Algorithms}.java`` and ``common/util/IDGenerator.java``.

This module reproduces what that code *does*, bugs and crashes included (see
``docs/notes/original-inventory.md`` and ``docs/notes/phase1-crosscheck.md`` for the bug
list B1-B8). It is pinned by the golden fixtures in ``tests/fixtures/golden/``. Comments
cite the Java file and line of every quirk (``Algorithms.java:207`` etc.).

Conventions:

- Java names become snake_case (``validateAgentStory`` -> :func:`validate_agent_story`,
  ``vertexNameMap`` -> ``vertex_name_map``); Java ``null`` is ``None``; arrays and
  ``Vector`` are Python lists.
- Every ``HashSet``/``HashMap`` whose iteration order can be observed is a
  :class:`~cyber_detectives.compat.javahash.JavaHashSet` /
  :class:`~cyber_detectives.compat.javahash.JavaHashMap`, which iterate exactly like Java 8
  under the canonical golden JVM setting (``-XX:hashCode=2``: every identity hash is 1).
  Sets that the original only probes with ``contains`` (``vertexIds``, ``edgeIds``,
  ``vertexNameMap``, the local ``vpSet`` of ``getSubGraph``) are plain Python containers:
  their order is never observable, not even by the reference harness.
- Crashes raise :class:`JavaException` subclasses carrying the Java stack frames
  (``top_frame``, ``origin_frame``, ``trace`` in the format of
  ``tools/reference/Harness.java``) and the JDK 8 message.
- ``System.out`` output is discarded unless captured with :func:`capture_stdout`, which
  reproduces it byte for byte (``Graph.dump``, ``dumpStatus``, the ``getAgentStory`` debug
  lines, the ``Algorithms.test*`` drivers).

The module also contains :func:`build_game` (the reference harness's generic map builder),
:func:`validate_compat` (the applet pipeline used by ``validate(..., compat="original")``) and
:func:`run_harness_case`, an emulation of the reference harness used by the golden tests.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .javahash import JavaHashMap, JavaHashSet

__all__ = [
    # exceptions / stdout
    "JavaException", "NullPointerException", "ArrayIndexOutOfBoundsException",
    "capture_stdout",
    # the original classes
    "IDGenerator", "Vertex", "Edge", "Graph", "Story", "ObservationHistory", "SensorRecording",
    "Sensor", "BeamDetector", "OccupancySensor", "DetectiveGame",
    # Algorithms
    "get_sub_graph", "get_sub_graph_multi", "get_reachable_subgraph", "is_deactivation",
    "is_activation", "flip", "dump_status", "are_neighbors", "validate_agent_story",
    "get_agent_story", "get_agent_story_statuses", "validate_agent_story_multi",
    "test_graph_routines", "test_story_history", "test_single_agent", "test_multi_agent", "main",
    # builders / pipelines
    "build_game", "expand_map", "game_for_map", "make_history", "validate_compat", "run_harness_case",
    "STAR_SPEC", "FILLER_MARK",
]

_PKG = "projects.cyberDetective."


def _frame(cls: str, method: str, line: int) -> str:
    """A stack frame in the format of Harness.frame(): ``pkg.Class.method(File.java:line)``."""
    return "%s%s.%s(%s.java:%d)" % (_PKG, cls, method, cls, line)


# ============================================================================ exceptions


class JavaException(Exception):
    """A Java exception thrown by the original code.

    ``frames`` is the Java stack from the throwing frame outwards, as far as the ported
    code goes (the frames of the caller, e.g. the reference harness, are not included).
    """

    java_class = "java.lang.RuntimeException"

    def __init__(self, frame: str, message: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.frames: List[str] = [frame]

    def add_frame(self, frame: str) -> None:
        """Append a caller frame while the exception propagates."""
        self.frames.append(frame)

    @property
    def top_frame(self) -> str:
        return self.frames[0]

    @property
    def origin_frame(self) -> Optional[str]:
        """The first frame inside the original code (``projects.``/``common.``)."""
        for f in self.frames:
            if f.startswith("projects.") or f.startswith("common."):
                return f
        return None

    def describe(self) -> Dict[str, Any]:
        """``{class, message, top_frame, origin_frame, trace}`` as Harness.describe()."""
        return {"class": self.java_class, "message": self.message, "top_frame": self.top_frame,
                "origin_frame": self.origin_frame, "trace": list(self.frames)}

    def __str__(self) -> str:
        return "%s%s at %s" % (self.java_class, "" if self.message is None else ": " + self.message,
                               self.top_frame)


class NullPointerException(JavaException):
    java_class = "java.lang.NullPointerException"


class ArrayIndexOutOfBoundsException(JavaException):
    java_class = "java.lang.ArrayIndexOutOfBoundsException"


# ============================================================================ System.out

_sink: Optional[List[str]] = None


def _print(s: str) -> None:
    if _sink is not None:
        _sink.append(s)


def _println(s: str = "") -> None:
    if _sink is not None:
        _sink.append(s + "\n")


def _js(x: Any) -> str:
    """Java string conversion of a (possibly null) String."""
    return "null" if x is None else x


class _Capture:
    """Result of :func:`capture_stdout`; ``getvalue()`` is everything printed so far."""

    def __init__(self) -> None:
        self.parts: List[str] = []

    def getvalue(self) -> str:
        return "".join(self.parts)


@contextlib.contextmanager
def capture_stdout() -> Iterator[_Capture]:
    """Capture what the original prints on ``System.out`` (not thread-safe)::

        with capture_stdout() as out:
            validate_agent_story(g, sv, story, ob_his)
        text = out.getvalue()

    Like ``System.setOut``, a nested capture redirects: its text does not reach the outer
    capture. Without a capture the output is discarded (and mostly not even formatted).
    """
    global _sink
    cap = _Capture()
    prev = _sink
    _sink = cap.parts
    try:
        yield cap
    finally:
        _sink = prev


def _int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


# ============================================================================ the classes


class IDGenerator:
    """``common/util/IDGenerator.java``: ids 1, 2, 3, ..."""

    def __init__(self) -> None:
        self.id = 0

    def get_next_id(self) -> int:
        self.id += 1
        return self.id


class Vertex:
    """``Vertex.java``. No ``equals``/``hashCode`` (identity semantics, as in Java); the
    neighbour set is a ``HashSet<Vertex>`` (``Vertex.java:16``)."""

    __slots__ = ("name", "id", "neighbors", "asso_vertex")
    JAVA_CLASS = _PKG + "Vertex"

    def __init__(self, name: Optional[str] = None, id: int = 0):
        self.name = name
        self.id = id
        self.neighbors = JavaHashSet()
        self.asso_vertex: Optional[Vertex] = None

    def is_neighbor(self, v: Optional["Vertex"]) -> bool:
        return v in self.neighbors

    def add_neighbor(self, v: Optional["Vertex"]) -> None:
        self.neighbors.add(v)

    def add_neighbors(self, c) -> None:
        self.neighbors.add_all(c)

    def remove_neighbor(self, v: Optional["Vertex"]) -> None:
        # one-sided: the other vertex keeps this one (Vertex.java:37-39)
        self.neighbors.remove(v)

    def get_copy(self) -> "Vertex":
        # copies id and name only: no neighbours, no assoVertex (Vertex.java:41-46)
        v = Vertex()
        v.id = self.id
        v.name = self.name
        return v

    def __repr__(self) -> str:
        return "Vertex(%r, %d)" % (self.name, self.id)


class Edge:
    """``Edge.java``."""

    __slots__ = ("vertices", "id")

    def __init__(self, v1: Optional[Vertex], v2: Optional[Vertex]):
        self.vertices = [v1, v2]
        try:
            self.id = Edge.get_edge_id_v(v1, v2)
        except JavaException as e:
            e.add_frame(_frame("Edge", "<init>", 13))
            raise

    @staticmethod
    def get_edge_id_v(v1: Optional[Vertex], v2: Optional[Vertex]) -> int:
        """``getEdgeId(Vertex, Vertex)`` (Edge.java:16-18)."""
        if v1 is None or v2 is None:
            raise NullPointerException(_frame("Edge", "getEdgeId", 17))
        return Edge.get_edge_id(v1.id, v2.id)

    @staticmethod
    def get_edge_id(v1: int, v2: int) -> int:
        """``getEdgeId(int, int)`` (Edge.java:20-27): smaller*65536 + larger in Java int
        arithmetic, so ids collide once vertex ids reach 65536 (bug B7)."""
        if v1 < v2:
            return _int32(v1 * 65536 + v2)
        return _int32(v2 * 65536 + v1)

    def get_copy(self) -> "Edge":
        # duplicates the vertices as well (Edge.java:33-35)
        return Edge(self.vertices[0].get_copy(), self.vertices[1].get_copy())

    def __repr__(self) -> str:
        return "Edge(%s--%s)" % (self.vertices[0] and self.vertices[0].name,
                                 self.vertices[1] and self.vertices[1].name)


class Graph:
    """``Graph.java``. ``vertex_map`` and ``edge_map`` are ``HashMap<Integer, ...>`` whose
    ``values()`` order the original iterates; ``vertex_ids``/``edge_ids`` (``Set<Integer>``)
    and ``vertex_name_map`` (``Map<String, Vertex>``) are only probed, so plain Python
    containers stand in for them."""

    __slots__ = ("vertex_ids", "vertex_map", "vertex_name_map", "edge_ids", "edge_map")

    def __init__(self) -> None:
        self.vertex_ids: set = set()
        self.vertex_map = JavaHashMap()
        self.vertex_name_map: Dict[Optional[str], Vertex] = {}
        self.edge_ids: set = set()
        self.edge_map = JavaHashMap()

    def has_edge_between_vertices(self, v1: Optional[Vertex], v2: Optional[Vertex]) -> bool:
        """``hasEdgeBetweenVertices(Vertex, Vertex)`` (Graph.java:24-27)."""
        try:
            return Edge.get_edge_id_v(v1, v2) in self.edge_map
        except JavaException as e:
            e.add_frame(_frame("Graph", "hasEdgeBetweenVertices", 26))
            raise

    def get_edge_between_vertices(self, v1: Optional[Vertex], v2: Optional[Vertex]) -> Optional[Edge]:
        """``getEdgeBetweenVertices(Vertex, Vertex)`` (Graph.java:29-32); null if absent."""
        try:
            return self.edge_map.get(Edge.get_edge_id_v(v1, v2))
        except JavaException as e:
            e.add_frame(_frame("Graph", "getEdgeBetweenVertices", 31))
            raise

    def has_edge_between_ids(self, v1: int, v2: int) -> bool:
        """``hasEdgeBetweenVertices(int, int)`` (Graph.java:34-37)."""
        return Edge.get_edge_id(v1, v2) in self.edge_map

    def get_edge_between_ids(self, v1: int, v2: int) -> Optional[Edge]:
        """``getEdgeBetweenVertices(int, int)`` (Graph.java:39-42)."""
        return self.edge_map.get(Edge.get_edge_id(v1, v2))

    def add_edgeless_vertex(self, v: Vertex) -> None:
        """Graph.java:48-53; overwrites an existing vertex with the same id/name."""
        self.vertex_ids.add(v.id)
        self.vertex_map.put(v.id, v)
        self.vertex_name_map[v.name] = v

    def add_copy_of_edgeless_vertex(self, v: Optional[Vertex]) -> None:
        """Graph.java:55-58."""
        if v is None:
            raise NullPointerException(_frame("Graph", "addCopyOfEdgelessVertex", 56))
        self.add_edgeless_vertex(v.get_copy())

    def add_edge(self, ed: Edge) -> None:
        """Graph.java:60-83: endpoints already present (by id) are replaced by the graph's
        own vertex objects; neighbour sets are updated on both sides."""
        if ed.id not in self.edge_ids:
            vs = ed.vertices
            for i in range(2):
                vi = vs[i]
                if vi.id not in self.vertex_ids:
                    self.vertex_ids.add(vi.id)
                    self.vertex_map.put(vi.id, vi)
                    self.vertex_name_map[vi.name] = vi
                else:
                    vs[i] = self.vertex_map.get(vi.id)
            vs[0].neighbors.add(vs[1])
            vs[1].neighbors.add(vs[0])
            self.edge_ids.add(ed.id)
            self.edge_map.put(ed.id, ed)

    def add_copy_of_edge(self, e: Optional[Edge]) -> None:
        """Graph.java:89-92."""
        if e is None:
            raise NullPointerException(_frame("Graph", "addCopyOfEdge", 90))
        self.add_edge(e.get_copy())

    def dump(self) -> None:
        """``Graph.dump()`` (Graph.java:97-120): vertices with their neighbours in iteration
        order, then the edges (oriented as stored) five per line; a final newline follows
        even after a full line, so a multiple of five edges ends with an empty line."""
        vs = self.vertex_map.values()
        if _sink is None:
            # nothing is printed; only the NPE on a null neighbour remains observable
            for v in vs:
                if None in v.neighbors:
                    raise NullPointerException(_frame("Graph", "dump", 105))
            for e in self.edge_map.values():
                if e.vertices[0] is None or e.vertices[1] is None:
                    raise NullPointerException(_frame("Graph", "dump", 113))
            return
        _println()
        _println("vertices: ")
        for v in vs:
            _print(_js(v.name) + ": ")
            for n in v.neighbors.to_array():
                if n is None:
                    raise NullPointerException(_frame("Graph", "dump", 105))
                _print(_js(n.name) + " ")
            _println()
        _println("edges: ")
        for i, e in enumerate(self.edge_map.values()):
            a, b = e.vertices
            if a is None or b is None:
                raise NullPointerException(_frame("Graph", "dump", 113))
            _print(_js(a.name) + "--" + _js(b.name))
            _print("  ")
            if i > 0 and (i + 1) % 5 == 0:
                _println()
        _println()


class Story:
    """``Story.java``: the claimed room sequence (a ``Vector<Vertex>``; entries may be null
    when a name is unknown, as in the applet)."""

    def __init__(self) -> None:
        self.visited_vertices: List[Optional[Vertex]] = []

    def add_vertex(self, v: Optional[Vertex]) -> None:
        self.visited_vertices.append(v)

    def get_story_as_array(self) -> List[Optional[Vertex]]:
        return list(self.visited_vertices)

    def get_vertex_set_as_array(self) -> List[Optional[Vertex]]:
        """Story.java:22-26: the distinct story vertices in ``HashSet`` order."""
        v_set = JavaHashSet()
        v_set.add_all(self.visited_vertices)
        return v_set.to_array()

    def dump(self) -> None:
        """Story.java:28-35."""
        _print("story: ")
        for v in self.visited_vertices:
            if v is None:
                raise NullPointerException(_frame("Story", "dump", 32))
            _print(_js(v.name) + " ")
        _println()


class SensorRecording:
    """``SensorRecording.java``."""

    ACTIVATION = 1
    DEACTIVATION = 2

    __slots__ = ("sensor", "event")

    def __init__(self, sensor: "Sensor", event: int):
        self.sensor = sensor
        self.event = event


class ObservationHistory:
    """``ObservationHistory.java`` (a ``Vector<SensorRecording>``)."""

    def __init__(self) -> None:
        self.sensor_recordings: List[SensorRecording] = []

    def add_sensor_recording(self, v: SensorRecording) -> None:
        self.sensor_recordings.append(v)

    def get_oh_as_array(self) -> List[SensorRecording]:
        return list(self.sensor_recordings)

    def dump(self) -> None:
        """ObservationHistory.java:21-28 (anything but ACTIVATION prints ``[D]``)."""
        _print("observation history: ")
        for r in self.sensor_recordings:
            _print(_js(r.sensor.name) + ("[A]" if r.event == SensorRecording.ACTIVATION else "[D]"))
        _println()


class Sensor:
    """``Sensor.java`` (abstract)."""

    SENSOR_TYPE_OCCUPANCY = 1
    SENSOR_TYPE_BEAM = 2

    def __init__(self) -> None:
        self.type = 0
        self.name: Optional[str] = None
        self.sensor_vertices: Optional[List[Optional[Vertex]]] = None

    def get_graph_vertices(self) -> Optional[List[Optional[Vertex]]]:
        return self.sensor_vertices

    def get_type(self) -> int:
        return self.type


class BeamDetector(Sensor):
    """``BeamDetector.java``: ``sensor_vertices`` = the two side vertices, in the order given
    (``flip`` returns the other one)."""

    def __init__(self, name: str, sv: List[Optional[Vertex]]):
        super().__init__()
        self.type = Sensor.SENSOR_TYPE_BEAM
        self.name = name
        self.sensor_vertices = sv


class OccupancySensor(Sensor):
    """``OccupancySensor.java``: named after its vertex."""

    def __init__(self, sv: Optional[Vertex]):
        super().__init__()
        self.type = Sensor.SENSOR_TYPE_OCCUPANCY
        if sv is None:
            raise NullPointerException(_frame("OccupancySensor", "<init>", 7))
        self.name = sv.name
        self.sensor_vertices = [sv]


class DetectiveGame:
    """``DetectiveGame.java``: a graph plus a story and an observation history."""

    def __init__(self) -> None:
        self.story_vertices: Optional[List[Vertex]] = None
        self.sensor_vertices: Optional[List[Vertex]] = None
        self.graph: Optional[Graph] = None
        self.story: Optional[Story] = None
        self.ob_his: Optional[ObservationHistory] = None
        self.room_ids = JavaHashSet()
        self.beam_ids = JavaHashSet()
        self.occu_ids = JavaHashSet()

    def update_starting_vertex(self, v: Optional[Vertex]) -> None:
        """DetectiveGame.java:20-35: move the virtual start vertex ``SV`` next to ``v``.

        Quirks: SV's current neighbour is whatever ``neighbors.toArray()[0]`` is (l.22, an
        AIOOBE if SV has none); a null ``v`` adds null to SV's neighbours (l.29) and then
        throws an NPE (l.30), leaving SV with the neighbour set ``{null}`` so that every
        later call throws an NPE at l.23 (bug B6).
        """
        g = self.graph
        sv = g.vertex_name_map.get("SV")
        if sv is None:
            raise NullPointerException(_frame("DetectiveGame", "updateStartingVertex", 22))
        arr = sv.neighbors.to_array()
        if not arr:
            raise ArrayIndexOutOfBoundsException(_frame("DetectiveGame", "updateStartingVertex", 22), "0")
        svn = arr[0]
        if svn is None:  # l.23: svn.neighbors on the null left by an earlier call (B6)
            raise NullPointerException(_frame("DetectiveGame", "updateStartingVertex", 23))
        svn.neighbors.remove(sv)
        sv.neighbors.remove(svn)
        eid = Edge.get_edge_id(sv.id, svn.id)
        g.edge_ids.discard(eid)
        g.edge_map.remove(eid)

        sv.add_neighbor(v)  # l.29: adds null when v is null
        if v is None:  # l.30
            raise NullPointerException(_frame("DetectiveGame", "updateStartingVertex", 30))
        v.add_neighbor(sv)
        e = Edge(sv, v)
        g.edge_ids.add(e.id)
        g.edge_map.put(e.id, e)

    # -- the hard-coded games -------------------------------------------------------------

    @staticmethod
    def get_basic_game() -> "DetectiveGame":
        """DetectiveGame.java:38-181: the STAR Fig. 2 map, empty story and history.

        Ids: the first generated id is discarded (l.40), so SV=2, A=3, B=4, C=5, b1u=6,
        b1d=7, b2l=8, b2r=9, o1=10, o2=11. SV is joined to A (l.122/124). The maps are
        filled in the order SV, A, B, C, b1u, b1d, b2r, b2l, o1, o2 (b2r before b2l,
        l.94-95), the neighbour lists in the order of l.122-162, and the edges by iterating
        ``vertexMap.values()`` and each neighbour set (l.165-176).
        """
        id_gen = IDGenerator()
        id_gen.get_next_id()
        game = DetectiveGame()
        g = Graph()
        sv = Vertex("SV", id_gen.get_next_id())
        a = Vertex("A", id_gen.get_next_id())
        b = Vertex("B", id_gen.get_next_id())
        c = Vertex("C", id_gen.get_next_id())
        b1u = Vertex("b1u", id_gen.get_next_id())
        b1d = Vertex("b1d", id_gen.get_next_id())
        b2l = Vertex("b2l", id_gen.get_next_id())
        b2r = Vertex("b2r", id_gen.get_next_id())
        o1 = Vertex("o1", id_gen.get_next_id())
        o2 = Vertex("o2", id_gen.get_next_id())

        b1u.asso_vertex = b1d
        b1d.asso_vertex = b1u
        b2r.asso_vertex = b2l
        b2l.asso_vertex = b2r

        for v in (b1u, b1d, b2r, b2l):
            game.beam_ids.add(v.id)
        for v in (a, b, c):
            game.room_ids.add(v.id)
        for v in (o1, o2):
            game.occu_ids.add(v.id)

        game.graph = g
        game.story_vertices = [sv, a, b, c]
        game.sensor_vertices = [b1u, b1d, b2l, b2r, o1, o2]

        order = (sv, a, b, c, b1u, b1d, b2r, b2l, o1, o2)  # l.88-119
        for v in order:
            g.vertex_ids.add(v.id)
        for v in order:
            g.vertex_map.put(v.id, v)
        for v in order:
            g.vertex_name_map[v.name] = v

        # neighbour lists, l.122-162
        for v, ns in ((sv, (a,)),
                      (a, (sv, b1u, b1d, b2l, o1, c)),
                      (b, (b2r, o2)),
                      (c, (a, b1d, o1)),
                      (b1u, (a, b2l, o1)),
                      (b1d, (a, c, o1)),
                      (b2l, (a, b1u, o1)),
                      (b2r, (b, o2)),
                      (o1, (a, c, b2l, b1u, b1d, o2)),
                      (o2, (o1, b2r, b))):
            for n in ns:
                v.add_neighbor(n)

        _add_edges_from_neighbors(g)  # l.165-176
        game.story = Story()
        game.ob_his = ObservationHistory()
        return game

    @staticmethod
    def _star_game(events: Sequence[Tuple[str, int]]) -> "DetectiveGame":
        """The common part of the four games (DetectiveGame.java:183-281): story ACBAC,
        sensors O1, O2, B1=(b1u,b1d), B2=(b2r,b2l), and the given recordings."""
        game = DetectiveGame.get_basic_game()
        g = game.graph
        for n in "ACBAC":
            game.story.add_vertex(g.vertex_name_map.get(n))
        s = {
            "O1": OccupancySensor(g.vertex_name_map.get("o1")),
            "O2": OccupancySensor(g.vertex_name_map.get("o2")),
            "B1": BeamDetector("b1", [g.vertex_name_map.get("b1u"), g.vertex_name_map.get("b1d")]),
            "B2": BeamDetector("b2", [g.vertex_name_map.get("b2r"), g.vertex_name_map.get("b2l")]),
        }
        for name, ev in events:
            game.ob_his.add_sensor_recording(SensorRecording(s[name], ev))
        return game

    @staticmethod
    def get_single_infeasible_game() -> "DetectiveGame":
        """DetectiveGame.java:183-206: b1 o1A o1D b2 o2A o2D (STAR eq. (2))."""
        A, D = SensorRecording.ACTIVATION, SensorRecording.DEACTIVATION
        return DetectiveGame._star_game([("B1", A), ("O1", A), ("O1", D), ("B2", A), ("O2", A), ("O2", D)])

    @staticmethod
    def get_single_feasible_game() -> "DetectiveGame":
        """DetectiveGame.java:208-231: b1 o1A o1D o2A o2D b2."""
        A, D = SensorRecording.ACTIVATION, SensorRecording.DEACTIVATION
        return DetectiveGame._star_game([("B1", A), ("O1", A), ("O1", D), ("O2", A), ("O2", D), ("B2", A)])

    @staticmethod
    def get_multi_feasible_game() -> "DetectiveGame":
        """DetectiveGame.java:233-256: b1 o1A o2A b2 o2D o1D (STAR eq. (3))."""
        A, D = SensorRecording.ACTIVATION, SensorRecording.DEACTIVATION
        return DetectiveGame._star_game([("B1", A), ("O1", A), ("O2", A), ("B2", A), ("O2", D), ("O1", D)])

    @staticmethod
    def get_multi_infeasible_game() -> "DetectiveGame":
        """DetectiveGame.java:258-281: b1 o2A o2D o1A b2 o1D."""
        A, D = SensorRecording.ACTIVATION, SensorRecording.DEACTIVATION
        return DetectiveGame._star_game([("B1", A), ("O2", A), ("O2", D), ("O1", A), ("B2", A), ("O1", D)])


def _add_edges_from_neighbors(g: Graph) -> None:
    """DetectiveGame.java:165-176: one Edge per neighbour pair, oriented (this vertex,
    neighbour) for the first vertex of ``vertexMap.values()`` that has it."""
    for v in g.vertex_map.values():
        for n in v.neighbors.to_array():
            if not g.has_edge_between_vertices(v, n):
                e = Edge(v, n)
                g.edge_ids.add(e.id)
                g.edge_map.put(e.id, e)


# ============================================================================ Algorithms


def _fa(method: str, line: int) -> str:
    return _frame("Algorithms", method, line)


def _npe_at(method: str, line: int) -> NullPointerException:
    """An NPE thrown directly in ``Algorithms.<method>`` at ``line``."""
    return NullPointerException(_fa(method, line))


def _npe_has_edge(method: str, line: int) -> NullPointerException:
    """``GP.hasEdgeBetweenVertices(v, s)`` with ``s == null``: the NPE is thrown by
    ``Edge.getEdgeId(Vertex, Vertex)`` reading ``v2.id`` (Edge.java:17)."""
    e = NullPointerException(_frame("Edge", "getEdgeId", 17))
    e.add_frame(_frame("Graph", "hasEdgeBetweenVertices", 26))
    e.add_frame(_fa(method, line))
    return e


def _flip_at(g: "Graph", v: "Vertex", r: "Sensor", method: str, line: int) -> Optional["Vertex"]:
    try:
        return flip(g, v, r)
    except JavaException as e:
        e.add_frame(_fa(method, line))
        raise


def get_sub_graph(g: Graph, s: Optional[Vertex], story_vertices: Sequence[Optional[Vertex]],
                  vg: Sequence[Optional[Vertex]]) -> Graph:
    """``Algorithms.getSubGraph`` (Algorithms.java:9-18): the reachable subgraph over the
    story vertices plus ``s``, then the goals ``vg`` attached (STAR Alg. 2)."""
    vp_set = set(story_vertices)  # HashSet used only for contains(): order irrelevant
    vp_set.add(s)
    try:
        return get_reachable_subgraph(g, s, vp_set, vg)
    except JavaException as e:
        e.add_frame(_fa("getSubGraph", 17))
        raise


def get_sub_graph_multi(g: Graph, s: Optional[Vertex], occu_sensors: Sequence[Optional[Vertex]],
                        story_vertices: Sequence[Optional[Vertex]], vg: Sequence[Optional[Vertex]]) -> Graph:
    """``Algorithms.getSubGraphMulti`` (Algorithms.java:20-46): as getSubGraph with the
    active occupancy vertices added, then the neighbours of every active occupancy vertex
    joined pairwise (clique-ification; the occupancy vertex itself is kept, unlike STAR
    Alg. 4 l.5)."""
    vp_set = set(story_vertices)
    vp_set.update(occu_sensors)
    vp_set.add(s)
    try:
        gp = get_reachable_subgraph(g, s, vp_set, vg)
    except JavaException as e:
        e.add_frame(_fa("getSubGraphMulti", 31))
        raise
    for o in occu_sensors:
        if o is None:
            raise NullPointerException(_fa("getSubGraphMulti", 34))
        if o.id in gp.vertex_map:
            ns = gp.vertex_map.get(o.id).neighbors.to_array()
            for j in range(len(ns)):
                for k in range(j + 1, len(ns)):
                    if not gp.has_edge_between_ids(ns[j].id, ns[k].id):
                        try:
                            ed = Edge(ns[j], ns[k])
                        except JavaException as e:
                            e.add_frame(_fa("getSubGraphMulti", 39))
                            raise
                        gp.add_edge(ed)
    return gp


def get_reachable_subgraph(g: Graph, s: Optional[Vertex], vp_set, vg: Sequence[Optional[Vertex]]) -> Graph:
    """``Algorithms.getReachableSubgraph`` (Algorithms.java:48-109).

    1. ``tempGraph`` = the edges of ``g`` with both ends in ``vp_set`` (in ``edgeMap``
       order), plus a copy of ``s``;
    2. a BFS from ``s`` over ``tempGraph`` copies every edge it scans into ``subGraph``
       (a vertex may be queued several times: it is marked visited when dequeued, l.76);
    3. every edge of ``g`` between a vertex of ``subGraph`` and a goal in ``vg`` is added
       (goals unreachable from ``s`` are therefore never added).
    ``vp_set`` is anything supporting ``in`` (only ``contains`` is used, l.61).
    """
    temp_graph = Graph()
    sub_graph = Graph()
    es = g.edge_map.values()

    try:
        temp_graph.add_copy_of_edgeless_vertex(s)
    except JavaException as e:
        e.add_frame(_fa("getReachableSubgraph", 56))
        raise
    sub_graph.add_copy_of_edgeless_vertex(s)
    for e in es:
        if e.vertices[0] in vp_set and e.vertices[1] in vp_set:
            temp_graph.add_edge(e.get_copy())  # addCopyOfEdge, l.63 (e is never null)

    # BFS from s (l.71-89)
    vvid = set()  # Set<Integer>, contains only
    queue = [s.id]
    t_vmap = temp_graph.vertex_map
    t_edges = temp_graph.edge_map
    s_edges = sub_graph.edge_map
    while queue:
        cv = t_vmap.get(queue[0])
        vvid.add(cv.id)
        del queue[0]
        cid = cv.id
        for n in cv.neighbors.to_array():
            nid = n.id
            eid = (cid << 16) + nid if cid < nid else (nid << 16) + cid
            if eid >= 0x80000000 or eid < -0x80000000:
                eid = _int32(eid)
            if eid in t_edges:
                if eid not in s_edges:
                    sub_graph.add_edge(t_edges.get(eid).get_copy())  # l.82
                if nid not in vvid:
                    queue.append(nid)

    # goal vertices (l.94-104)
    sgvs = sub_graph.vertex_map.values()
    for v2 in vg:
        for sv_ in sgvs:
            v1 = g.vertex_map.get(sv_.id)
            if v1 is None:
                raise NullPointerException(_fa("getReachableSubgraph", 100))
            if v2 in v1.neighbors:
                try:
                    ed = g.get_edge_between_vertices(v1, v2)
                    if ed is None:  # a neighbour without an edge in g.edgeMap
                        raise NullPointerException(_frame("Graph", "addCopyOfEdge", 90))
                except JavaException as e:
                    e.add_frame(_fa("getReachableSubgraph", 101))
                    raise
                sub_graph.add_edge(ed.get_copy())
    return sub_graph


def is_deactivation(sr: SensorRecording) -> bool:
    """Algorithms.java:111-116: an occupancy DEACTIVATION (a beam "D" is not one, B7)."""
    return sr.sensor.type == Sensor.SENSOR_TYPE_OCCUPANCY and sr.event == SensorRecording.DEACTIVATION


def is_activation(sr: SensorRecording) -> bool:
    """Algorithms.java:118-123."""
    return sr.sensor.type == Sensor.SENSOR_TYPE_OCCUPANCY and sr.event == SensorRecording.ACTIVATION


def flip(g: Graph, v: Vertex, r: Sensor) -> Optional[Vertex]:
    """Algorithms.java:125-137: the vertex of ``g`` reached by triggering sensor ``r`` from
    ``v``: the occupancy vertex itself, or the other side of the beam (by name comparison
    with side 0, so any vertex not named like side 0 maps to side 0)."""
    if r.type == Sensor.SENSOR_TYPE_OCCUPANCY:
        return g.vertex_map.get(v.id)
    s0, s1 = r.sensor_vertices[0], r.sensor_vertices[1]
    if s0 is None or v.name is None:  # v.name.equals(r.sensorVertices[0].name)
        raise NullPointerException(_fa("flip", 130))
    if v.name == s0.name:
        if s1 is None:
            raise NullPointerException(_fa("flip", 131))
        return g.vertex_map.get(s1.id)
    return g.vertex_map.get(s0.id)


def dump_status(p: Sequence[Optional[Vertex]], status: Sequence[JavaHashSet]) -> None:
    """Algorithms.java:139-160: ``search status: [S0]p1[S1]p2[S2]...`` (members of each set
    in iteration order, empty sets print nothing)."""
    if _sink is None:
        # nothing is printed; only the NPEs remain observable, in the order Java hits them
        if None in status[0]:
            raise NullPointerException(_fa("dumpStatus", 144))
        for i, pi in enumerate(p):
            if pi is None:
                raise NullPointerException(_fa("dumpStatus", 150))
            if None in status[i + 1]:
                raise NullPointerException(_fa("dumpStatus", 154))
        return
    _print("search status: ")
    _print_status_set(status[0], 144)
    for i, pi in enumerate(p):
        if pi is None:
            raise NullPointerException(_fa("dumpStatus", 150))
        _print(_js(pi.name))
        _print_status_set(status[i + 1], 154)
    _println()


def _print_status_set(st: JavaHashSet, line: int) -> None:
    """One ``[a,b,...]`` group of dumpStatus (l.141-147 / l.151-157), printed up to a null
    member, which throws at ``line``."""
    vs = st.to_array()
    if not vs:
        return
    _print("[")
    for j, v in enumerate(vs):
        if v is None:
            raise NullPointerException(_fa("dumpStatus", line))
        _print(_js(v.name))
        if j < len(vs) - 1:
            _print(",")
    _print("]")


def are_neighbors(g: Graph, v1: int, v2: int) -> bool:
    """Algorithms.java:162-165 (by id; equal ids count as neighbours)."""
    return g.has_edge_between_ids(v1, v2) or v1 == v2


def _sub_graph_at(g, s, story_vs, vg, caller: str, line: int) -> Graph:
    try:
        return get_sub_graph(g, s, story_vs, vg)
    except JavaException as e:
        e.add_frame(_fa(caller, line))
        raise


def _dump_status_at(p, status, caller: str, line: int) -> None:
    try:
        dump_status(p, status)
    except JavaException as e:
        e.add_frame(_fa(caller, line))
        raise


def validate_agent_story(g: Graph, sv: Vertex, story: Story, ob_his: ObservationHistory) -> bool:
    """``Algorithms.validateAgentStory`` (Algorithms.java:167-224), STAR Alg. 3.

    ``S[l]`` holds the sensor vertices x may stand on after the recordings so far with the
    first ``l`` story rooms used. Quirks (see docs/notes/original-inventory.md):

    - occupancy deactivations are skipped (l.180), so a single-agent history is never
      checked for well-formedness (B5);
    - the story may be used up before the last recording: the final test only asks for
      ``l == n`` at ``i == m`` (l.197), a property already reached earlier (B8);
    - an unknown (null) story room throws an NPE at l.207;
    - after an edge-id collision (B7a: vertex ids above 65535) ``areNeighbors`` can accept
      a story room that is not in ``GP``, so ``s`` becomes null: the next story test throws
      at l.207, a pending recording's ``hasEdgeBetweenVertices(v, s)`` in
      ``Edge.getEdgeId`` (l.203), and if the story ends there the verdict is true;
    - ``dumpStatus`` prints the sets after every processed recording.

    Every Java dereference of a value that can be null raises :class:`NullPointerException`
    with the Java frames (``tests/test_compat_fuzz.py`` guards this for the whole module).
    """
    p = story.get_story_as_array()
    r = ob_his.get_oh_as_array()
    n1 = len(p) + 1
    S = [JavaHashSet() for _ in range(n1)]
    SP = [JavaHashSet() for _ in range(n1)]
    S[0].add(sv)
    story_vs = story.get_vertex_set_as_array()  # recomputed per (j, k) at l.193; same contents
    m = len(r)
    for i in range(m + 1):
        if i < m and is_deactivation(r[i]):
            continue
        vg = r[i].sensor.get_graph_vertices() if i < m else []
        for j in range(n1):
            for s in S[j].to_array():
                gp = _sub_graph_at(g, s, story_vs, vg, "validateAgentStory", 193)
                gvm = gp.vertex_map
                s = gvm.get(s.id)
                for l in range(j, n1):
                    if i == m and l == n1 - 1:
                        return True  # l.197-199
                    for vgi in vg:  # l.200-206
                        if vgi is None:
                            raise _npe_at("validateAgentStory", 201)
                        v = gvm.get(vgi.id)
                        if v is None:
                            continue
                        if s is None:  # s = GP.vertexMap.get(p[l].id) was null (B7a)
                            raise _npe_has_edge("validateAgentStory", 203)
                        if v is s or gp.has_edge_between_ids(v.id, s.id):
                            SP[l].add(_flip_at(g, v, r[i].sensor, "validateAgentStory", 204))
                    if l < n1 - 1:  # l.207: p[l].id, then s.id
                        pl = p[l]
                        if pl is None or s is None:
                            raise _npe_at("validateAgentStory", 207)
                        if are_neighbors(gp, pl.id, s.id):
                            # null when the match came from an edge-id collision (B7a)
                            s = gvm.get(pl.id)
                            continue
                    break
        S = SP
        SP = [JavaHashSet() for _ in range(n1)]
        _dump_status_at(p, S, "validateAgentStory", 221)
    return False


def get_agent_story(g: Graph, sv: Vertex, story: Story, ob_his: ObservationHistory) -> str:
    """``Algorithms.getAgentStory`` (Algorithms.java:226-313): back-traces the statuses of
    :func:`get_agent_story_statuses` into a path string such as ``A[b1u]C[o1]...``.

    Quirks: it throws whatever ``getAgentStoryStatuses`` throws (an AIOOBE at l.365 on any
    inconsistent input, B4); the path depends on hash order (B2) and can contain more
    crossings than recordings (B3); every attributed status prints ``"<vertex> <j>"`` and
    every processed phase a ``dumpStatus`` line.
    """
    p = story.get_story_as_array()
    r = ob_his.get_oh_as_array()
    try:
        S = get_agent_story_statuses(g, sv, story, ob_his)
    except JavaException as e:
        e.add_frame(_fa("getAgentStory", 229))
        raise
    sr_loc_vec: List[int] = []
    sr_ver_vec: List[Vertex] = []
    story_vs = story.get_vertex_set_as_array()
    keep_breaking = False
    J = 0
    last: Optional[Vertex] = None
    m = len(r)
    for i in range(m, -1, -1):
        keep_breaking = False
        if i < m and is_deactivation(r[i]):
            continue
        vg = r[i].sensor.get_graph_vertices() if i < m else []
        Si = S[i]
        n1 = len(Si)
        for j in range(n1):
            Sj = Si[j].to_array()
            for k in range(len(Sj)):
                s = Sj[k]
                gp = _sub_graph_at(g, s, story_vs, vg, "getAgentStory", 252)
                gvm = gp.vertex_map
                s = gvm.get(s.id)
                for l in range(j, n1):
                    if i == m and l == n1 - 1:  # l.256-267
                        J = j
                        sr_loc_vec.append(j)
                        gv = g.vertex_map.get(Sj[k].id)
                        if gv is None:
                            raise _npe_at("getAgentStory", 259)
                        sr_ver_vec.append(gv.asso_vertex if gv.asso_vertex is not None else Sj[k])
                        _println(_js(Sj[k].name) + " " + str(j))
                        last = Sj[k]
                        keep_breaking = True
                        break
                    for vgi in vg:  # l.268-285
                        if vgi is None:
                            raise _npe_at("getAgentStory", 269)
                        v = gvm.get(vgi.id)
                        if v is None:
                            continue
                        if s is None:  # B7a, see validate_agent_story
                            raise _npe_has_edge("getAgentStory", 271)
                        if v is s or gp.has_edge_between_ids(v.id, s.id):
                            vp = _flip_at(g, v, r[i].sensor, "getAgentStory", 272)
                            if l == J:  # l.273: vp.id, then last.id
                                if vp is None or last is None:
                                    raise _npe_at("getAgentStory", 273)
                                if vp.id == last.id:
                                    J = j
                                    last = Sj[k]
                                    sr_loc_vec.append(j)
                                    gv = g.vertex_map.get(Sj[k].id)
                                    if gv is None:
                                        raise _npe_at("getAgentStory", 277)
                                    sr_ver_vec.append(gv.asso_vertex if gv.asso_vertex is not None else Sj[k])
                                    _println(_js(Sj[k].name) + " " + str(j))
                                    break
                    if l < n1 - 1:  # l.286-291
                        pl = p[l]
                        if pl is None or s is None:
                            raise _npe_at("getAgentStory", 286)
                        if are_neighbors(gp, pl.id, s.id):
                            s = gvm.get(pl.id)
                            continue
                    break
                if i == m and keep_breaking:
                    break
            if i == m and keep_breaking:
                break
        _dump_status_at(p, S[i], "getAgentStory", 297)

    path: List[str] = []
    for i, pi in enumerate(p):  # l.301-303
        if pi is None:
            raise NullPointerException(_fa("getAgentStory", 302))
        path.append(_js(pi.name))
    for i in range(len(sr_loc_vec) - 1):  # l.304-307: the last entry (the final phase) is dropped
        loc = sr_loc_vec[i]
        if sr_ver_vec[i] is None:  # the argument is evaluated before insertElementAt checks loc
            raise _npe_at("getAgentStory", 306)
        if loc > len(path):  # Vector.insertElementAt bound check
            exc = ArrayIndexOutOfBoundsException("java.util.Vector.insertElementAt(Vector.java:603)",
                                                 "%d > %d" % (loc, len(path)))
            exc.add_frame(_fa("getAgentStory", 306))
            raise exc
        path.insert(loc, "[" + _js(sr_ver_vec[i].name) + "]")
    return "".join(path)


def get_agent_story_statuses(g: Graph, sv: Vertex, story: Story,
                             ob_his: ObservationHistory) -> Optional[List[List[JavaHashSet]]]:
    """``Algorithms.getAgentStoryStatuses`` (Algorithms.java:315-368): the statuses
    ``S[i][l]`` of :func:`validate_agent_story` for every phase ``i``.

    Quirks: on a deactivation the whole row is aliased, ``S[i+1] = S[i]`` (l.329); when the
    final test of l.346 never fires, the ``dumpStatus(p, S[i + 1])`` after the last phase
    reads ``S[m + 1]`` and throws ``ArrayIndexOutOfBoundsException: m+1`` at l.365 (B4), so
    the ``return null`` of l.367 is unreachable.
    """
    p = story.get_story_as_array()
    r = ob_his.get_oh_as_array()
    m = len(r)
    n1 = len(p) + 1
    S = [[JavaHashSet() for _ in range(n1)] for _ in range(m + 1)]
    S[0][0].add(sv)
    story_vs = story.get_vertex_set_as_array()
    for i in range(m + 1):
        if i < m and is_deactivation(r[i]):
            S[i + 1] = S[i]  # l.329: row aliasing
            continue
        vg = r[i].sensor.get_graph_vertices() if i < m else []
        Si = S[i]
        for j in range(len(Si)):
            for s in Si[j].to_array():
                gp = _sub_graph_at(g, s, story_vs, vg, "getAgentStoryStatuses", 342)
                gvm = gp.vertex_map
                s = gvm.get(s.id)
                for l in range(j, len(Si)):
                    if i == m and l == len(Si) - 1:
                        return S  # l.346-348
                    for vgi in vg:  # l.349-355
                        if vgi is None:
                            raise _npe_at("getAgentStoryStatuses", 350)
                        v = gvm.get(vgi.id)
                        if v is None:
                            continue
                        if s is None:  # B7a, see validate_agent_story
                            raise _npe_has_edge("getAgentStoryStatuses", 352)
                        if v is s or gp.has_edge_between_ids(v.id, s.id):
                            S[i + 1][l].add(_flip_at(g, v, r[i].sensor, "getAgentStoryStatuses", 353))
                    if l < len(Si) - 1:  # l.356
                        pl = p[l]
                        if pl is None or s is None:
                            raise _npe_at("getAgentStoryStatuses", 356)
                        if are_neighbors(gp, pl.id, s.id):
                            s = gvm.get(pl.id)
                            continue
                    break
        if i + 1 >= len(S):  # l.365: S[i + 1] with i == r.length
            raise ArrayIndexOutOfBoundsException(_fa("getAgentStoryStatuses", 365), str(i + 1))
        _dump_status_at(p, S[i + 1], "getAgentStoryStatuses", 365)
    return None


def validate_agent_story_multi(g: Graph, sv: Vertex, story: Story, ob_his: ObservationHistory) -> bool:
    """``Algorithms.validateAgentStoryMulti`` (Algorithms.java:370-449), STAR Alg. 4.

    ``O`` is the set of active occupancy vertices. Quirks:

    - deactivations only update ``O`` and are otherwise skipped (l.385-388), so progress
      that needs x to wait through a deactivation is lost (B1);
    - ``Vg`` is assigned three times (l.392, 396, 400); only the last one counts;
    - on an occupancy activation the start vertex itself is added as a goal (l.408-414);
    - the statuses accumulate (``S[j].addAll(SP[j])``, l.439-441): x may ignore any beam
      recording;
    - ``GP.dump()`` is printed for every subgraph (l.418), so the number of dumps before an
      early ``return true`` depends on hash order.
    """
    p = story.get_story_as_array()
    r = ob_his.get_oh_as_array()
    n1 = len(p) + 1
    S = [JavaHashSet() for _ in range(n1)]
    SP = [JavaHashSet() for _ in range(n1)]
    S[0].add(sv)
    O = JavaHashSet()
    story_vs = story.get_vertex_set_as_array()
    m = len(r)
    for i in range(m + 1):
        vg: List[Optional[Vertex]] = []
        if i < m and is_deactivation(r[i]):
            O.remove(r[i].sensor.sensor_vertices[0])  # l.386
            continue
        act = i < m and is_activation(r[i])
        if act:
            O.add(r[i].sensor.sensor_vertices[0])  # l.391 (l.392's Vg is overwritten)
        if i < m:
            vg = r[i].sensor.get_graph_vertices()  # l.396 / l.400
        for j in range(n1):
            for s in S[j].to_array():
                vgp = vg
                if act:  # l.408-414: the current vertex becomes a goal too
                    vgp = list(vg) + [s]
                try:
                    gp = get_sub_graph_multi(g, s, O.to_array(), story_vs, vgp)
                except JavaException as e:
                    e.add_frame(_fa("validateAgentStoryMulti", 416))
                    raise
                gvm = gp.vertex_map
                s = gvm.get(s.id)
                try:
                    gp.dump()  # l.418
                except JavaException as e:
                    e.add_frame(_fa("validateAgentStoryMulti", 418))
                    raise
                for l in range(j, n1):
                    if i == m and l == n1 - 1:
                        return True  # l.420-422
                    for vgi in vg:  # l.423-429
                        if vgi is None:
                            raise _npe_at("validateAgentStoryMulti", 424)
                        v = gvm.get(vgi.id)
                        if v is None:
                            continue
                        if s is None:  # B7a, see validate_agent_story
                            raise _npe_has_edge("validateAgentStoryMulti", 426)
                        if v is s or gp.has_edge_between_ids(v.id, s.id):
                            SP[l].add(_flip_at(g, v, r[i].sensor, "validateAgentStoryMulti", 427))
                    if l < n1 - 1:  # l.430
                        pl = p[l]
                        if pl is None or s is None:
                            raise _npe_at("validateAgentStoryMulti", 430)
                        if are_neighbors(gp, pl.id, s.id):
                            s = gvm.get(pl.id)
                            continue
                    break
        for j in range(n1):
            S[j].add_all(SP[j])  # l.439-441
        SP = [JavaHashSet() for _ in range(n1)]
        _dump_status_at(p, S, "validateAgentStoryMulti", 446)
    return False


# -- the test drivers (Algorithms.java:453-496) -------------------------------------------


def _call_at(fn, frame: str, *args):
    try:
        return fn(*args)
    except JavaException as e:
        e.add_frame(frame)
        raise


def test_graph_routines() -> None:
    """Algorithms.testGraphRoutines (l.453-463): dumps G and six subgraphs."""
    gm = DetectiveGame.get_basic_game()
    gm.graph.dump()
    nm = gm.graph.vertex_name_map
    calls = [(457, "A", ["b1d", "b1u"]), (458, "b1u", ["o1"]), (459, "b1d", ["o1"]),
             (460, "o1", ["b2r", "b2l"]), (461, "b2r", ["b2r", "o2"]), (462, "o2", ["o2", "B"])]
    for line, s, goals in calls:
        sg = _call_at(get_sub_graph, _fa("testGraphRoutines", line), gm.graph, nm.get(s), gm.story_vertices,
                      [nm.get(x) for x in goals])
        _call_at(sg.dump, _fa("testGraphRoutines", line))


def test_story_history() -> None:
    """Algorithms.testStoryHistory (l.465-470)."""
    gm = DetectiveGame.get_single_feasible_game()
    gm.graph.dump()
    gm.story.dump()
    gm.ob_his.dump()


def test_single_agent() -> None:
    """Algorithms.testSingleAgent (l.472-481)."""
    gm = DetectiveGame.get_single_feasible_game()
    gm.graph.dump()
    gm.story.dump()
    gm.ob_his.dump()
    good = _call_at(validate_agent_story, _fa("testSingleAgent", 478), gm.graph,
                    gm.graph.vertex_name_map.get("SV"), gm.story, gm.ob_his)
    _println()
    _println("Valid story." if good else "Story inconsistent.")


def test_multi_agent() -> None:
    """Algorithms.testMultiAgent (l.483-492)."""
    gm = DetectiveGame.get_multi_infeasible_game()
    gm.graph.dump()
    gm.story.dump()
    gm.ob_his.dump()
    good = _call_at(validate_agent_story_multi, _fa("testMultiAgent", 489), gm.graph,
                    gm.graph.vertex_name_map.get("SV"), gm.story, gm.ob_his)
    _println()
    _println("Valid story." if good else "Story inconsistent.")


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Algorithms.main (l.494-496): runs testMultiAgent."""
    _call_at(test_multi_agent, _fa("main", 495))


# ============================================================================ map builders

#: The STAR Fig. 2 map in the fixture format, with the edge order and ``vertex_order`` that
#: make :func:`build_game` reproduce ``DetectiveGame.getBasicGame()`` exactly (same as
#: ``tools/reference/gen_cases.py``'s ``STAR`` and ``Harness.starSpec()``).
STAR_SPEC: Dict[str, Any] = {
    "name": "star_fig2",
    "rooms": ["A", "B", "C"],
    "beams": {"b1": ["b1u", "b1d"], "b2": ["b2r", "b2l"]},
    "occupancy": ["o1", "o2"],
    "vertex_order": ["A", "B", "C", "b1u", "b1d", "b2l", "b2r", "o1", "o2"],
    "edges": [["A", "b1u"], ["A", "b1d"], ["A", "b2l"], ["A", "o1"], ["A", "C"],
              ["B", "b2r"], ["C", "b1d"], ["C", "o1"], ["b1u", "b2l"], ["b2l", "o1"],
              ["b1u", "o1"], ["b1d", "o1"], ["o1", "o2"], ["b2r", "o2"], ["B", "o2"]],
}


#: Placeholder in ``vertex_order`` for the vertices added by ``filler_occupancy``.
FILLER_MARK = "..."


def expand_map(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Expand the compact harness key ``"filler_occupancy": N`` (see
    ``tests/fixtures/README.md``): N edgeless occupancy sensors ``f1`` ... ``fN`` are appended to
    ``occupancy``; in ``vertex_order`` they replace the single entry ``"..."`` (or are appended
    when there is none). Returns ``spec`` itself when the key is absent. Maps with 65,535
    fillers push later vertex ids past 65535, where edge ids collide (bug B7a)."""
    if not isinstance(spec, dict):
        raise ValueError("a map must be a JSON object")
    if "filler_occupancy" not in spec:
        return spec
    n = spec["filler_occupancy"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 0 or n > 0x7FFFFFFF:
        raise ValueError("filler_occupancy must be a non-negative integer")
    fillers = ["f%d" % i for i in range(1, n + 1)]
    out = {k: v for k, v in spec.items() if k != "filler_occupancy"}
    out["occupancy"] = _str_list(spec.get("occupancy"), "occupancy") + fillers
    if spec.get("vertex_order") is not None:
        vo = _str_list(spec["vertex_order"], "vertex_order")
        k = vo.count(FILLER_MARK)
        if k > 1:
            raise ValueError("vertex_order has more than one %r" % FILLER_MARK)
        if k == 1:
            i = vo.index(FILLER_MARK)
            vo[i:i + 1] = fillers
        else:
            vo += fillers
        out["vertex_order"] = vo
    return out


def _str_list(o: Any, what: str) -> List[str]:
    """Harness.strings(): null -> [], else a JSON array of strings."""
    if o is None:
        return []
    if not isinstance(o, (list, tuple)) or not all(isinstance(x, str) for x in o):
        raise ValueError("%s must be a list of strings" % what)
    return list(o)


def _beam_items(spec: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    beams = spec.get("beams") or {}
    if not isinstance(beams, dict):
        raise ValueError("beams must be an object")
    return [(b, _str_list(sides, "beam sides")) for b, sides in beams.items()]


def build_game(spec: Dict[str, Any]) -> DetectiveGame:
    """Build a game for a fixture map dict the way ``Harness.buildGame`` does, i.e.
    ``DetectiveGame.getBasicGame()`` replayed step by step (DetectiveGame.java:38-181):

    - a fresh IDGenerator whose first id is discarded; ``SV`` gets the next id, then the
      vertices in ``vertex_order`` (default: rooms, beam sides in listed order, occupancy);
    - ``assoVertex`` for both sides of every beam; roomIds/beamIds/occuIds;
    - ``vertexIds``, ``vertexMap``, ``vertexNameMap`` filled in the order SV, rooms, beam
      sides, occupancy;
    - neighbour lists vertex by vertex in id order, each in the order of ``edges``;
      ``SV`` is joined to the first room and is that room's first neighbour;
    - edges built by iterating ``vertexMap.values()`` and each neighbour set.

    Only ``rooms``, ``beams``, ``occupancy``, ``edges``, ``vertex_order`` and
    ``filler_occupancy`` (:func:`expand_map`) are read. A malformed map raises ``ValueError``.
    """
    spec = expand_map(spec)
    rooms = _str_list(spec.get("rooms"), "rooms")
    beams = _beam_items(spec)
    occ = _str_list(spec.get("occupancy"), "occupancy")
    beam_sides = [s for _, sides in beams for s in sides]
    if spec.get("vertex_order") is not None:
        order = _str_list(spec["vertex_order"], "vertex_order")
    else:
        order = rooms + beam_sides + occ
    allv = set(rooms) | set(beam_sides) | set(occ)
    if len(allv) != len(rooms) + len(beam_sides) + len(occ) or "SV" in allv:
        raise ValueError("duplicate or reserved vertex names in map")
    if len(order) != len(allv) or set(order) != allv:
        raise ValueError("vertex_order is not a permutation of the map's vertices")
    if not rooms:
        raise ValueError("map has no rooms")
    for _, sides in beams:
        if len(sides) != 2:
            raise ValueError("a beam needs exactly two sides")

    id_gen = IDGenerator()
    id_gen.get_next_id()
    game = DetectiveGame()
    g = Graph()
    sv = Vertex("SV", id_gen.get_next_id())
    by_name: Dict[str, Vertex] = {}
    created: List[Vertex] = []
    for n in order:
        v = Vertex(n, id_gen.get_next_id())
        by_name[n] = v
        created.append(v)
    for _, (s0, s1) in beams:
        by_name[s0].asso_vertex = by_name[s1]
        by_name[s1].asso_vertex = by_name[s0]
    for n in beam_sides:
        game.beam_ids.add(by_name[n].id)
    for n in rooms:
        game.room_ids.add(by_name[n].id)
    for n in occ:
        game.occu_ids.add(by_name[n].id)
    game.graph = g
    game.story_vertices = [sv] + [by_name[n] for n in rooms]
    sensor_names = set(beam_sides) | set(occ)
    game.sensor_vertices = [v for v in created if v.name in sensor_names]

    map_order = [sv] + [by_name[n] for n in rooms + beam_sides + occ]
    for v in map_order:
        g.vertex_ids.add(v.id)
    for v in map_order:
        g.vertex_map.put(v.id, v)
    for v in map_order:
        g.vertex_name_map[v.name] = v

    adj: Dict[Vertex, List[Vertex]] = {v: [] for v in [sv] + created}
    room0 = by_name[rooms[0]]
    adj[sv].append(room0)
    adj[room0].append(sv)
    edges = spec.get("edges") or []
    if not isinstance(edges, (list, tuple)):
        raise ValueError("edges must be a list")
    for e in edges:
        if not isinstance(e, (list, tuple)) or len(e) < 2 or not all(isinstance(x, str) for x in e):
            raise ValueError("an edge must be a list of two vertex names, got %r" % (e,))
        u, w = by_name.get(e[0]), by_name.get(e[1])
        if u is None or w is None:
            raise ValueError("edge with unknown vertex %r" % (e,))
        adj[u].append(w)
        adj[w].append(u)
    for v in [sv] + created:
        for n in adj[v]:
            v.add_neighbor(n)
    _add_edges_from_neighbors(g)
    game.story = Story()
    game.ob_his = ObservationHistory()
    return game


def game_for_map(spec: Dict[str, Any], force_generic: bool = False) -> DetectiveGame:
    """``DetectiveGame.getBasicGame()`` for the map named ``star_fig2`` (as the harness does),
    :func:`build_game` otherwise. For ``star_fig2`` both give identical games
    (``builder_check``; ``regen_golden.sh`` reruns every case with the generic builder)."""
    if spec.get("name") == "star_fig2" and not force_generic:
        return DetectiveGame.get_basic_game()
    return build_game(spec)


def make_history(game: DetectiveGame, spec: Dict[str, Any], pairs: Sequence[Sequence[str]]) -> None:
    """Append ``[sensor, "A"|"D"]`` pairs to ``game.ob_his`` as ``Harness.addHistory`` does:
    one Sensor object per name, ``BeamDetector(name, [side0, side1])`` for beams,
    ``OccupancySensor(vertex)`` for occupancy sensors. Unknown sensors or events raise
    ``ValueError`` (the original has no way to express them)."""
    g = game.graph
    spec = expand_map(spec)
    beams = dict(_beam_items(spec))
    occ = _str_list(spec.get("occupancy"), "occupancy")
    sensors: Dict[str, Sensor] = {}
    if not isinstance(pairs, (list, tuple)):
        raise ValueError("a history must be a list of [sensor, event] pairs")
    for pr in pairs:
        if not isinstance(pr, (list, tuple)) or len(pr) < 2 or not (isinstance(pr[0], str) and isinstance(pr[1], str)):
            raise ValueError("a recording must be a [sensor, event] pair of strings, got %r" % (pr,))
        name, ev = pr[0], pr[1]
        sn = sensors.get(name)
        if sn is None:
            if name in beams:
                sides = beams[name]
                if len(sides) < 2:
                    raise ValueError("beam %r needs two sides" % (name,))
                sn = BeamDetector(name, [g.vertex_name_map.get(sides[0]), g.vertex_name_map.get(sides[1])])
            elif name in occ:
                v = g.vertex_name_map.get(name)
                if v is None:
                    raise ValueError("occupancy vertex missing: %s" % name)
                sn = OccupancySensor(v)
            else:
                raise ValueError("unknown sensor %r" % (name,))
            sensors[name] = sn
        if ev == "A":
            e = SensorRecording.ACTIVATION
        elif ev == "D":
            e = SensorRecording.DEACTIVATION
        else:
            raise ValueError("unknown event %r" % (ev,))
        game.ob_his.add_sensor_recording(SensorRecording(sn, e))


def validate_compat(map_dict: Dict[str, Any], story_list: Sequence[str],
                    events_as_pairs: Sequence[Sequence[str]], agents: str = "single"
                    ) -> Tuple[bool, Optional[str]]:
    """The original's verdict (and path) the way the applet computes it.

    Builds the game with :func:`build_game` (for STAR Fig. 2 this equals
    ``getBasicGame()``), resolves the story with ``vertexNameMap.get`` (unknown names become
    null, as in the applet), calls ``updateStartingVertex(first story room)``
    (CyberDetectiveDemoApplet.java:235/265; skipped for an empty story) and then

    - ``agents="single"``: ``validateAgentStory``, and ``getAgentStory`` if that returned
      true (l.236-241) -> ``(verdict, path or None)``;
    - ``agents="multi"``: ``validateAgentStoryMulti`` (l.266) -> ``(verdict, None)``.

    The original's crashes propagate as :class:`JavaException` subclasses.
    """
    if agents not in ("single", "multi"):
        raise ValueError("agents must be 'single' or 'multi', got %r" % (agents,))
    game = build_game(map_dict)
    g = game.graph
    for n in story_list:
        if not isinstance(n, str):
            raise ValueError("story entries must be strings, got %r" % (n,))
        game.story.add_vertex(g.vertex_name_map.get(n))
    make_history(game, map_dict, events_as_pairs)
    p = game.story.get_story_as_array()
    if p:
        game.update_starting_vertex(p[0])
    sv = g.vertex_name_map.get("SV")
    if agents == "single":
        ok = validate_agent_story(g, sv, game.story, game.ob_his)
        path = get_agent_story(g, sv, game.story, game.ob_his) if ok else None
        return ok, path
    return validate_agent_story_multi(g, sv, game.story, game.ob_his), None


# ============================================================================ harness emulation


class HarnessError(ValueError):
    """A malformed harness case (not original behaviour)."""


def _name(v: Optional[Vertex]) -> Optional[str]:
    return None if v is None else v.name


def _sort_key(s: Optional[str]):
    # Harness.cmp: null first, then String.compareTo (UTF-16 code units)
    return (0, b"") if s is None else (1, s.encode("utf-16-be", "surrogatepass"))


def _sorted_names(vs) -> List[Optional[str]]:
    return sorted((_name(v) for v in vs), key=_sort_key)


def _names_of(vs) -> List[Optional[str]]:
    return [_name(v) for v in vs]


def enc_graph(G: Graph) -> Dict[str, Any]:
    """Harness.encGraph: ``{vertices, edges, ids, adjacency}``, all sorted."""
    edges = []
    for e in G.edge_map.values():
        a, b = _name(e.vertices[0]), _name(e.vertices[1])
        edges.append([a, b] if _sort_key(a) <= _sort_key(b) else [b, a])
    edges.sort(key=lambda x: (_sort_key(x[0]), _sort_key(x[1])))
    vs = sorted(G.vertex_map.values(), key=lambda v: _sort_key(_name(v)))
    ids: Dict[Any, Any] = {}
    adj: Dict[Any, Any] = {}
    for v in vs:
        ids[v.name] = v.id
        adj[v.name] = _sorted_names(v.neighbors)
    return {"vertices": _sorted_names(G.vertex_map.values()), "edges": edges, "ids": ids, "adjacency": adj}


def enc_history(oh: ObservationHistory) -> List[List[str]]:
    out = []
    for r in oh.sensor_recordings:
        ev = "A" if r.event == SensorRecording.ACTIVATION else (
            "D" if r.event == SensorRecording.DEACTIVATION else str(r.event))
        out.append([r.sensor.name, ev])
    return out


def structure(game: DetectiveGame) -> Dict[str, Any]:
    """Harness.structure: the game's construction details."""
    g = game.graph
    vals = g.vertex_map.values()
    m: Dict[str, Any] = {}
    m["vertexMap_order"] = _names_of(vals)
    m["vertexMap_ids"] = [v.id for v in vals]
    m["vertexIds"] = sorted(g.vertex_ids)
    m["vertexNameMap_keys"] = sorted(g.vertex_name_map.keys(), key=_sort_key)
    m["edgeMap_order_oriented"] = ["%d:%s--%s" % (e.id, _js(_name(e.vertices[0])), _js(_name(e.vertices[1])))
                                   for e in g.edge_map.values()]
    m["edgeIds"] = sorted(g.edge_ids)
    nb: Dict[Any, Any] = {}
    asso: Dict[Any, Any] = {}
    for v in vals:
        nb[v.name] = _sorted_names(v.neighbors)
        asso[v.name] = None if v.asso_vertex is None else v.asso_vertex.name
    m["neighbor_sets"] = nb
    m["assoVertex"] = asso
    m["roomIds"] = sorted(game.room_ids)
    m["beamIds"] = sorted(game.beam_ids)
    m["occuIds"] = sorted(game.occu_ids)
    m["storyVertices"] = _names_of(game.story_vertices)
    m["sensorVertices"] = _names_of(game.sensor_vertices)
    nbo: Dict[Any, Any] = {}
    for v in vals:
        nbo[v.name] = _names_of(v.neighbors)
    m["neighbor_iteration_order"] = nbo
    return m


def enc_game(game: DetectiveGame) -> Dict[str, Any]:
    return {"graph": enc_graph(game.graph), "story": _names_of(game.story.visited_vertices),
            "history": enc_history(game.ob_his), "structure": structure(game)}


def _put_dump(res: Dict[str, Any], G: Graph) -> None:
    """Harness.putDump: ``graph_dump`` (and ``graph_dump_exception``) for G.dump()."""
    err = None
    with capture_stdout() as out:
        try:
            G.dump()
        except JavaException as e:
            err = e
    res["graph_dump"] = out.getvalue()
    if err is not None:
        res["graph_dump_exception"] = err.describe()


def _str(o: Any) -> str:
    if not isinstance(o, str):
        raise HarnessError("expected a JSON string, got %r" % (o,))
    return o


def _list(o: Any) -> list:
    """Harness.asList."""
    if not isinstance(o, list):
        raise HarnessError("expected a JSON array, got %r" % (o,))
    return o


def _vertices(g: Graph, names: Any) -> List[Optional[Vertex]]:
    if names is None:
        return []
    names = _list(names)
    return [None if n is None else g.vertex_name_map.get(_str(n)) for n in names]


def _setup(c: Dict[str, Any], maps: Dict[str, Dict[str, Any]], force_generic: bool) -> DetectiveGame:
    """Harness.setup: a fresh game for the case, with ``start`` applied."""
    if c.get("game") is not None:
        gn = c["game"]
        builders = {"SingleInfeasible": DetectiveGame.get_single_infeasible_game,
                    "SingleFeasible": DetectiveGame.get_single_feasible_game,
                    "MultiFeasible": DetectiveGame.get_multi_feasible_game,
                    "MultiInfeasible": DetectiveGame.get_multi_infeasible_game,
                    "Basic": DetectiveGame.get_basic_game}
        if not isinstance(gn, str) or gn not in builders:
            raise HarnessError("unknown game %s" % gn)
        game = builders[gn]()
        start = _str(c["start"]) if c.get("start") is not None else "fixed"
    else:
        mo = c.get("map")
        if mo is None:
            raise HarnessError("case has neither map nor game")
        if isinstance(mo, dict):
            spec = mo
        else:
            spec = maps.get(_str(mo))
            if spec is None and mo == "star_fig2":
                spec = STAR_SPEC
            if spec is None:
                raise HarnessError("unknown map %s" % mo)
        if not isinstance(spec, dict):
            raise HarnessError("expected a JSON object for map %r" % (mo,))
        use_basic = spec.get("name") == "star_fig2" and not force_generic and c.get("builder") != "generic"
        try:
            spec = expand_map(spec)
            game = DetectiveGame.get_basic_game() if use_basic else build_game(spec)
        except HarnessError:
            raise
        except ValueError as e:
            raise HarnessError(str(e))
        g = game.graph
        for n in _list(c["story"]) if c.get("story") is not None else []:
            game.story.add_vertex(g.vertex_name_map.get(_str(n)))
        if c.get("history") is not None:
            try:
                make_history(game, spec, _list(c["history"]))
            except HarnessError:
                raise
            except ValueError as e:
                raise HarnessError(str(e))
        start = _str(c["start"]) if c.get("start") is not None else "story"
    if start == "story":
        p = game.story.get_story_as_array()
        if p:
            game.update_starting_vertex(p[0])
    elif start != "fixed":
        raise HarnessError("unknown start %s" % start)
    return game


def _builder_check(c: Dict[str, Any], maps: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    mo = c.get("map")
    spec = mo if isinstance(mo, dict) else (maps.get(_str(mo)) or STAR_SPEC)
    ra = structure(DetectiveGame.get_basic_game())
    try:
        rb = structure(build_game(spec))
    except HarnessError:
        raise
    except ValueError as e:
        raise HarnessError(str(e))
    nio_a = ra.pop("neighbor_iteration_order")
    nio_b = rb.pop("neighbor_iteration_order")
    diffs = [k for k in ra if json.dumps(ra[k]) != json.dumps(rb.get(k))]
    return {"identical": not diffs, "differing_fields": diffs, "compared_fields": list(ra.keys()),
            "neighbor_iteration_order_equal": json.dumps(nio_a) == json.dumps(nio_b),
            "basic_game": ra, "basic_game_neighbor_iteration_order": nio_a}


def _dispatch(op: Optional[str], c: Dict[str, Any], maps: Dict[str, Dict[str, Any]],
              res: Dict[str, Any], force_generic: bool) -> Any:
    if op is None:
        raise HarnessError("case has no op")
    if op == "algorithms_test":
        fn = {"testGraphRoutines": test_graph_routines, "testStoryHistory": test_story_history,
              "testSingleAgent": test_single_agent, "testMultiAgent": test_multi_agent,
              "main": main}.get(_str(c.get("name")))
        if fn is None:
            raise HarnessError("unknown algorithms_test %s" % c.get("name"))
        fn()
        return None
    if op == "applet":
        from . import applet  # lazy: the applet emulation needs this module
        return applet.run_steps(c, res)
    if op == "builder_check":
        return _builder_check(c, maps)

    game = _setup(c, maps, force_generic)
    g = game.graph
    sv = g.vertex_name_map.get("SV")
    if op == "validateAgentStory":
        return validate_agent_story(g, sv, game.story, game.ob_his)
    if op == "validateAgentStoryMulti":
        return validate_agent_story_multi(g, sv, game.story, game.ob_his)
    if op == "getAgentStory":
        return get_agent_story(g, sv, game.story, game.ob_his)
    if op == "getAgentStoryStatuses":
        S = get_agent_story_statuses(g, sv, game.story, game.ob_his)
        if S is None:
            return None
        aliases = []
        for i in range(len(S)):
            for j in range(i):
                if S[i] is S[j]:
                    aliases.append([j, i])
                    break
        res["aliases"] = aliases
        return [[_sorted_names(st) for st in row] for row in S]
    if op in ("getSubGraph", "getSubGraphMulti"):
        v = g.vertex_name_map.get(_str(c.get("s")))
        occ = _vertices(g, c.get("occupancy_active")) if op == "getSubGraphMulti" else None
        stv = _vertices(g, c["story_vertices"]) if "story_vertices" in c else game.story.get_vertex_set_as_array()
        vg = _vertices(g, c.get("goals"))
        if op == "getSubGraph":
            gp = get_sub_graph(g, v, stv, vg)
        else:
            gp = get_sub_graph_multi(g, v, occ, stv, vg)
        _put_dump(res, gp)
        return enc_graph(gp)
    if op == "getReachableSubgraph":
        v = g.vertex_name_map.get(_str(c.get("s")))
        vp = JavaHashSet(_vertices(g, c.get("vp_set")))
        vg = _vertices(g, c.get("goals"))
        gp = get_reachable_subgraph(g, v, vp, vg)
        _put_dump(res, gp)
        return enc_graph(gp)
    if op == "game_dump":
        g.dump()
        game.story.dump()
        game.ob_his.dump()
        return enc_game(game)
    if op == "update_starting_vertex":
        arg = c.get("vertex")
        v = None if arg is None else g.vertex_name_map.get(_str(arg))
        try:
            game.update_starting_vertex(v)
        except JavaException as e:
            res["call_exception"] = e.describe()
        _put_dump(res, g)
        return enc_game(game)
    raise HarnessError("unknown op %s" % op)


def run_harness_case(c: Dict[str, Any], maps: Optional[Dict[str, Dict[str, Any]]] = None,
                     force_generic: bool = False) -> Dict[str, Any]:
    """Emulate ``tools/reference/Harness.java`` on one case.

    Returns ``{id, op, return, stdout, exception, ...extras}`` exactly as the harness
    records it (``aliases``, ``graph_dump``, ``graph_dump_exception``, ``call_exception``;
    applet steps for ``op == "applet"``). ``maps`` maps names to fixture map dicts.
    Malformed cases raise :class:`HarnessError`.
    """
    maps = maps or {}
    if not isinstance(c, dict):
        raise HarnessError("expected a JSON object, got %r" % (c,))
    for k in ("id", "op"):
        if c.get(k) is not None:
            _str(c[k])
    res: Dict[str, Any] = {"id": c.get("id"), "op": c.get("op")}
    ret = None
    exc = None
    with capture_stdout() as out:
        try:
            ret = _dispatch(c.get("op"), c, maps, res, force_generic)
        except JavaException as e:
            exc = e.describe()
    res["return"] = ret
    res["stdout"] = out.getvalue()
    res["exception"] = exc
    return res
