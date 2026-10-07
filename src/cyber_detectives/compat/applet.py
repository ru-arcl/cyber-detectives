"""The non-drawing logic of the original applet, ported from
``projects/cyberDetective/ui/CyberDetectiveDemoApplet.java`` and
``projects/cyberDetective/ui/Environment.java`` (arc-l/cyber-detective@55f57f8).

What is reproduced (line numbers refer to ``CyberDetectiveDemoApplet.java`` unless noted):

- the **Run Validation** button (l.192-272): the story text field is read one *character*
  per room (l.204-206; an unknown character resolves to null), the sensor text field is
  ``split(",")`` (l.213) and only the exact tokens ``o1 o2 b1 b2`` count (anything else is
  dropped silently). Single-agent mode turns ``o1`` into an activation *and* a
  deactivation (l.217-221); multi-agent mode toggles (l.244-255). Then
  ``updateStartingVertex(first character)`` (l.235/265), the verdict, and in single mode
  the path (l.238-241); the result text is exactly what the applet shows. The story and
  history are cleared only at the very end (l.270-271), so a crash leaves them in the game
  and they poison every later run until Reset (bug B6);
- **Reset** (l.182-190) and ``startSimulation`` (l.281-288);
- **mouse clicks** (``mouseClicked``, l.291-332): click scaling (both axes divide by
  ``canvasWidth``, l.292-293), ``Environment.getClickedVertex`` hit-testing (occupancy
  rectangles first, then rooms, then the widened beam strips; ``Rectangle2D.contains`` is
  half-open), the "Reachable features" text, and the stale-vertex quirk: the
  ``Environment`` keeps the vertices of the game it was created with, so after Reset clicks
  still return (and list the neighbours of) the *old* game's vertices.

Drawing is not ported. :func:`run_steps` emulates the reference harness's ``applet`` op.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .javahash import JavaHashMap
from .original import (BeamDetector, DetectiveGame, JavaException, NullPointerException,
                       OccupancySensor, SensorRecording, Vertex, enc_history, get_agent_story,
                       validate_agent_story, validate_agent_story_multi, capture_stdout)

__all__ = [
    "Applet", "Environment", "Rect", "SCALING_FACTOR", "CANVAS_WIDTH", "CANVAS_HEIGHT",
    "java_split", "story_chars", "parse_sensor_text", "click_to_world", "run_steps",
    "NOTHING_TO_VALIDATE", "VALID", "INCONSISTENT", "START_INSTRUCTIONS",
]

# ui/Geometry.java:4-8
SCALING_FACTOR = 800.0  # a float in Java
CANVAS_WIDTH = 400
CANVAS_HEIGHT = 300

# result / instruction texts (l.199, 237, 240, 267, 282, 317-329)
NOTHING_TO_VALIDATE = "Nothing to validate."
VALID = "Valid story."
INCONSISTENT = "Inconsistent story."
PATH_PREFIX = "Valid story.\nA possible path: "
START_INSTRUCTIONS = "Please pick a starting room from A, B, C to begin"


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _java_f2i(x: float) -> int:
    """Java ``(int)`` of a float: truncation toward zero, NaN -> 0, saturating."""
    if x != x:
        return 0
    if x >= 2147483647:
        return 2147483647
    if x <= -2147483648:
        return -2147483648
    return int(x)


def _scale(p: int) -> int:
    # int * float: the int is converted to float first (rounded to 24 bits when |p| > 2^24),
    # then float multiply and float divide (canvasWidth promoted to float), each rounded to
    # float; a double holds each exact intermediate, and 53 >= 2*24 + 2 bits make the
    # double-then-float rounding of the quotient exact. Finally the saturating (int) cast.
    return _java_f2i(_f32(_f32(_f32(float(_int32(p))) * SCALING_FACTOR) / CANVAS_WIDTH))


def click_to_world(px: int, py: int) -> Tuple[int, int]:
    """l.292-293: ``(int)(getX() * scalingFactor / canvasWidth)`` in float arithmetic
    (``px`` and ``py`` are Java ints); the y coordinate is also divided by ``canvasWidth``
    (harmless: the scale is uniform)."""
    return _scale(px), _scale(py)


def _int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def java_number_int_value(o: Any) -> int:
    """``((Number) o).intValue()`` for a value from the harness's JSON reader, which makes
    integers ``Long`` (``intValue`` keeps the low 32 bits) and numbers with ``.``/``e``
    ``Double`` (``intValue`` is the saturating ``(int)`` cast, NaN -> 0). Anything else
    (including integers outside the ``long`` range, which the reader rejects) raises
    :class:`~cyber_detectives.compat.original.HarnessError`."""
    from .original import HarnessError

    if isinstance(o, bool) or not isinstance(o, (int, float)):
        raise HarnessError("expected a JSON number, got %r" % (o,))
    if isinstance(o, int):
        if not -(1 << 63) <= o < (1 << 63):
            raise HarnessError("integer out of the long range: %r" % (o,))
        return _int32(o)
    return _java_f2i(o)


def java_split(s: str, sep: str = ",") -> List[str]:
    """``String.split`` with a one-character literal separator and limit 0: trailing empty
    strings are removed; a string without the separator yields ``[s]`` (also for ``""``)."""
    if sep not in s:
        return [s]
    parts = s.split(sep)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def story_chars(text: str) -> List[str]:
    """l.204-206: one ``substring(i, i + 1)`` per UTF-16 code unit (a character outside the
    BMP gives two lone surrogates, which resolve to null like any unknown name)."""
    out: List[str] = []
    for ch in text:
        if ord(ch) > 0xFFFF:
            u = ord(ch) - 0x10000
            out.append(chr(0xD800 + (u >> 10)))
            out.append(chr(0xDC00 + (u & 0x3FF)))
        else:
            out.append(ch)
    return out


def parse_sensor_text(text: str, mode: str = "single") -> List[List[str]]:
    """The history the applet builds from the sensor field, as ``[sensor, "A"|"D"]`` pairs
    (l.213-263). ``mode`` is ``"single"`` or ``"multi"``."""
    hist: List[List[str]] = []
    active = {"o1": False, "o2": False}
    for t in java_split(text, ","):
        if t in ("o1", "o2"):
            if mode == "single":
                hist += [[t, "A"], [t, "D"]]
            else:
                active[t] = not active[t]
                hist.append([t, "A" if active[t] else "D"])
        elif t in ("b1", "b2"):
            hist.append([t, "A"])
    return hist


# ============================================================================ Environment


def _env_frame(method: str, line: int) -> str:
    return "projects.cyberDetective.ui.Environment.%s(Environment.java:%d)" % (method, line)


class Rect:
    """A ``Rectangle2D.Double`` with its ``contains`` test (ui/Rect.java keeps the vertex)."""

    __slots__ = ("x", "y", "width", "height", "vertex")

    def __init__(self, x: float, y: float, width: float, height: float, vertex: Optional[Vertex] = None):
        self.x, self.y, self.width, self.height = float(x), float(y), float(width), float(height)
        self.vertex = vertex

    def contains(self, x: float, y: float) -> bool:
        """``Rectangle2D.contains(double, double)``: half-open on the far edges."""
        return x >= self.x and y >= self.y and x < self.x + self.width and y < self.y + self.height

    def __repr__(self) -> str:
        return "Rect(%g, %g, %g, %g, %s)" % (self.x, self.y, self.width, self.height,
                                             self.vertex and self.vertex.name)


class _Segment:
    """A beam side (ui/LineSegment.java): ``Line2D.Double`` plus its vertex."""

    __slots__ = ("x1", "y1", "x2", "y2", "vertex")

    def __init__(self, x1, y1, x2, y2, vertex):
        self.x1, self.y1, self.x2, self.y2 = float(x1), float(y1), float(x2), float(y2)
        self.vertex = vertex


class Environment:
    """``ui/Environment.java`` without drawing: the example map's rooms, occupancy
    rectangles and beam segments keyed by vertex id (``HashMap<Integer, ...>``, iterated in
    Java order by ``initialize``), and ``getClickedVertex``."""

    def __init__(self) -> None:
        self.game: Optional[DetectiveGame] = None
        self.bounding_rect: Optional[Rect] = None
        self.walls: List[Tuple[int, int, int, int]] = []
        self.vertex_id_room_map = JavaHashMap()
        self.vertex_id_bd_map = JavaHashMap()
        self.vertex_id_oc_map = JavaHashMap()
        self.beam_a: List[_Segment] = []
        self.room_a: List[Rect] = []
        self.occu_a: List[Rect] = []
        self.beam_rect: List[Rect] = []

    def _vertex(self, name: str, game: DetectiveGame, method: str, line: int) -> Vertex:
        v = game.graph.vertex_name_map.get(name)
        if v is None:  # v.id on null when the map lacks the name
            raise NullPointerException(_env_frame(method, line))
        return v

    def create_beam_detector(self, name, x, y, ex, ey, game) -> None:
        """Environment.java:30-35."""
        v = self._vertex(name, game, "createBeamDetector", 34)
        self.vertex_id_bd_map.put(v.id, _Segment(x, y, ex, ey, v))

    def create_occupancy_sensor(self, name, x, y, width, height, game) -> None:
        """Environment.java:37-41."""
        v = self._vertex(name, game, "createOccupancySensor", 40)
        self.vertex_id_oc_map.put(v.id, Rect(x, y, width, height, v))

    def create_room(self, name, x, y, width, height, game) -> None:
        """Environment.java:43-47."""
        v = self._vertex(name, game, "createRoom", 46)
        self.vertex_id_room_map.put(v.id, Rect(x, y, width, height, v))

    def initialize(self) -> None:
        """Environment.java:76-92: arrays in ``values()`` order; each beam gets a hit
        rectangle widened by 8 on both sides *perpendicular* to it (any non-vertical beam is
        treated as horizontal), not extended along it."""
        self.beam_a = self.vertex_id_bd_map.values()
        self.occu_a = self.vertex_id_oc_map.values()
        self.room_a = self.vertex_id_room_map.values()
        self.beam_rect = []
        for b in self.beam_a:
            if b.x1 == b.x2:
                self.beam_rect.append(Rect(b.x1 - 8, b.y1, 16, b.y2 - b.y1))
            else:
                self.beam_rect.append(Rect(b.x1, b.y1 - 8, b.x2 - b.x1, 16))

    def get_clicked_vertex(self, x: int, y: int) -> Optional[Vertex]:
        """Environment.java:94-111: first hit among occupancy rectangles, rooms, beam strips."""
        for r in self.occu_a:
            if r.contains(x, y):
                return r.vertex
        for r in self.room_a:
            if r.contains(x, y):
                return r.vertex
        for i, r in enumerate(self.beam_rect):
            if r.contains(x, y):
                return self.beam_a[i].vertex
        return None

    @staticmethod
    def create_example_environment(game: DetectiveGame) -> "Environment":
        """Environment.java:113-177 (STAR Fig. 2 in world units 800x600, y down)."""
        e = Environment()
        e.game = game
        e.bounding_rect = Rect(0, 0, 800, 600)
        e.walls = [(210, 0, 210, 75), (210, 130, 210, 320), (210, 375, 210, 475), (210, 525, 210, 600),
                   (295, 0, 295, 75), (295, 130, 295, 185), (295, 270, 295, 345), (295, 400, 295, 500),
                   (295, 545, 295, 600), (530, 0, 530, 75), (530, 130, 530, 185), (530, 270, 530, 345),
                   (530, 400, 530, 600), (610, 0, 610, 185), (610, 270, 610, 470), (610, 525, 610, 600),
                   (0, 185, 85, 185), (140, 185, 210, 185), (295, 185, 530, 185), (610, 185, 680, 185),
                   (730, 185, 800, 185), (295, 270, 380, 270), (430, 270, 680, 270), (730, 270, 800, 270),
                   (0, 400, 210, 400), (295, 455, 380, 455), (430, 455, 530, 455)]
        e.create_beam_detector("b1u", 215, 285, 290, 285, game)
        e.create_beam_detector("b1d", 215, 305, 290, 305, game)
        e.create_beam_detector("b2l", 625, 180, 625, 265, game)
        e.create_beam_detector("b2r", 645, 180, 645, 265, game)
        e.create_occupancy_sensor("o1", 295, 270, 235, 185, game)
        e.create_occupancy_sensor("o2", 610, 270, 190, 330, game)
        e.create_room("A", 0, 0, 210, 185, game)
        e.create_room("B", 610, 0, 190, 185, game)
        e.create_room("C", 0, 400, 210, 200, game)
        e.initialize()
        return e


# ============================================================================ the applet


class Applet:
    """``CyberDetectiveDemoApplet`` state and event handlers (text fields as strings).

    ``story_text``, ``sensor_text``, ``result_text`` and ``instructions`` are the contents of
    the two input fields, the output area and the instructions area; ``single_selected`` is
    the radio button. The game is ``env.game``; it persists across runs (bug B6).
    """

    def __init__(self) -> None:
        # appInit, l.64: the environment is created once, from a fresh basic game
        self.env = Environment.create_example_environment(DetectiveGame.get_basic_game())
        self.story_text = ""
        self.sensor_text = ""
        self.result_text = ""
        self.instructions = ""
        self.single_selected = True  # l.121
        self.vertex_id_map = JavaHashMap()  # l.48 (only get/put/clear)
        self.start_simulation()  # l.277

    def start_simulation(self) -> None:
        """l.281-288: rooms A, B, C are the clickable starts."""
        self.instructions = START_INSTRUCTIONS
        g = self.env.game.graph
        self.vertex_id_map.clear()
        for n in ("A", "B", "C"):
            v = g.vertex_name_map.get(n)
            self.vertex_id_map.put(v.id, v)

    def reset(self) -> None:
        """Reset button, l.182-190: clears the fields and replaces ``env.game`` (but not the
        Environment's vertices)."""
        self.story_text = ""
        self.sensor_text = ""
        self.result_text = ""
        self.env.game = DetectiveGame.get_basic_game()
        self.start_simulation()

    def run(self, step: Optional[Dict[str, Any]] = None) -> None:
        """Run Validation button, l.192-272. ``step`` (optional dict) receives
        ``parsed_story``, ``parsed_history``, ``validate`` and ``path`` as the harness records
        them. Raises the original's crashes (:class:`JavaException`); the fields keep the
        values set before the crash, as in Java."""
        if step is None:
            step = {}
        story = self.story_text
        sensor_string = self.sensor_text
        if len(story) == 0:  # l.198-201
            self.result_text = NOTHING_TO_VALIDATE
            return
        game = self.env.game
        g = game.graph
        chars = story_chars(story)
        for ch in chars:  # l.204-206
            game.story.add_vertex(g.vertex_name_map.get(ch))
        o1 = OccupancySensor(g.vertex_name_map.get("o1"))  # l.208-211
        o2 = OccupancySensor(g.vertex_name_map.get("o2"))
        b1 = BeamDetector("b1", [g.vertex_name_map.get("b1u"), g.vertex_name_map.get("b1d")])
        b2 = BeamDetector("b2", [g.vertex_name_map.get("b2r"), g.vertex_name_map.get("b2l")])
        sensors = java_split(sensor_string, ",")  # l.213
        A, D = SensorRecording.ACTIVATION, SensorRecording.DEACTIVATION
        add = game.ob_his.add_sensor_recording
        if self.single_selected:  # l.215
            for t in sensors:  # l.216-233
                if t == "o1":
                    add(SensorRecording(o1, A))
                    add(SensorRecording(o1, D))
                elif t == "o2":
                    add(SensorRecording(o2, A))
                    add(SensorRecording(o2, D))
                elif t == "b1":
                    add(SensorRecording(b1, A))
                elif t == "b2":
                    add(SensorRecording(b2, A))
            step["parsed_story"] = [None if v is None else v.name for v in game.story.visited_vertices]
            step["parsed_history"] = enc_history(game.ob_his)
            game.update_starting_vertex(g.vertex_name_map.get(chars[0]))  # l.235
            result = validate_agent_story(game.graph, g.vertex_name_map.get("SV"), game.story, game.ob_his)
            step["validate"] = result
            self.result_text = VALID if result else INCONSISTENT  # l.237
            if result:  # l.238-241
                s = get_agent_story(game.graph, g.vertex_name_map.get("SV"), game.story, game.ob_his)
                step["path"] = s
                self.result_text = PATH_PREFIX + s
        else:
            o1active = False  # l.244-263
            o2active = False
            for t in sensors:
                if t == "o1":
                    o1active = not o1active
                    add(SensorRecording(o1, A if o1active else D))
                elif t == "o2":
                    o2active = not o2active
                    add(SensorRecording(o2, A if o2active else D))
                elif t == "b1":
                    add(SensorRecording(b1, A))
                elif t == "b2":
                    add(SensorRecording(b2, A))
            step["parsed_story"] = [None if v is None else v.name for v in game.story.visited_vertices]
            step["parsed_history"] = enc_history(game.ob_his)
            game.update_starting_vertex(g.vertex_name_map.get(chars[0]))  # l.265
            result = validate_agent_story_multi(game.graph, g.vertex_name_map.get("SV"), game.story, game.ob_his)
            step["validate"] = result
            self.result_text = VALID if result else INCONSISTENT  # l.267
        game.story.visited_vertices.clear()  # l.270-271 (skipped by any exception above)
        game.ob_his.sensor_recordings.clear()

    def click(self, px: int, py: int, step: Optional[Dict[str, Any]] = None) -> None:
        """``mouseClicked`` (l.291-332) at canvas pixel (px, py). ``step`` receives ``x``,
        ``y``, ``hit`` and ``accepted``."""
        if step is None:
            step = {}
        x, y = click_to_world(px, py)
        step["x"] = x
        step["y"] = y
        v = self.env.get_clicked_vertex(x, y)  # l.294: a vertex of the Environment's game
        step["hit"] = None if v is None else v.name
        accepted = v is not None and self.vertex_id_map.get(v.id) is not None  # l.295
        step["accepted"] = accepted
        if not accepted:
            return
        game = self.env.game
        if v.id in game.room_ids:  # l.298-300
            self.story_text = self.story_text + v.name
        else:  # l.301-315
            text = self.sensor_text
            if len(text) > 0:
                text = text + "," + v.name[0:2]
            else:
                text = v.name[0:2]
            if v.id not in game.occu_ids:
                v = v.asso_vertex  # a beam side: x is now on the other side
            self.sensor_text = text
        if v is None:
            raise NullPointerException(
                "projects.cyberDetective.ui.CyberDetectiveDemoApplet.mouseClicked(CyberDetectiveDemoApplet.java:318)")
        parts = ["Current location: ", v.name, "\nReachable features: "]  # l.317-330
        self.vertex_id_map.clear()
        for n in v.neighbors.to_array():
            if n.name == "SV":
                continue
            self.vertex_id_map.put(n.id, n)
            parts.append(n.name + ", ")
        self.vertex_id_map.put(v.id, v)
        parts.append(v.name)
        parts.append("\nPlease click on one of the above features or run validation.")
        self.instructions = "".join(parts)


# ============================================================================ harness op


def run_steps(c: Dict[str, Any], res: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Emulate the reference harness's ``applet`` op (``Harness.runApplet``): replay the
    case's ``steps`` (``{"run": {story?, sensors?, mode?}}``, ``{"reset": true}``,
    ``{"click": [px, py]}``) against one :class:`Applet` and return one record per step."""
    from .original import HarnessError

    def _s(o: Any) -> str:  # Harness.str
        if not isinstance(o, str):
            raise HarnessError("expected a JSON string, got %r" % (o,))
        return o

    if c.get("steps") is not None:
        steps = c["steps"]
        if not isinstance(steps, list) or not all(isinstance(x, dict) for x in steps):
            raise HarnessError("steps must be a list of objects")
    else:
        steps = [{"run": {"story": c.get("story_text"), "sensors": c.get("sensor_text"),
                          "mode": c.get("applet_mode")}}]
    sim = Applet()
    out: List[Dict[str, Any]] = []
    for s in steps:
        rec: Dict[str, Any] = {}
        with capture_stdout() as buf:
            if "run" in s:
                r = s["run"]
                if not isinstance(r, dict):
                    raise HarnessError("run must be an object")
                rec["action"] = "run"
                if r.get("story") is not None:
                    sim.story_text = _s(r["story"])
                if r.get("sensors") is not None:
                    sim.sensor_text = _s(r["sensors"])
                if r.get("mode") is not None:
                    if _s(r["mode"]) == "single":
                        sim.single_selected = True
                    elif r["mode"] == "multi":
                        sim.single_selected = False
                    else:
                        raise HarnessError("unknown applet mode %s" % r["mode"])
                rec["mode"] = "single" if sim.single_selected else "multi"
                rec["story_text"] = sim.story_text
                rec["sensor_text"] = sim.sensor_text
                rec["validate"] = None
                rec["path"] = None
                try:
                    sim.run(rec)
                    rec["exception"] = None
                except JavaException as e:
                    rec["exception"] = e.describe()
                rec["result_text"] = sim.result_text
                rec["game_story_after"] = [None if v is None else v.name for v in sim.env.game.story.visited_vertices]
                rec["game_history_after"] = enc_history(sim.env.game.ob_his)
            elif "reset" in s:
                rec["action"] = "reset"
                try:
                    sim.reset()
                    rec["exception"] = None
                except JavaException as e:
                    rec["exception"] = e.describe()
            elif "click" in s:
                xy = s["click"]
                if not isinstance(xy, list) or len(xy) < 2:
                    raise HarnessError("a click needs [px, py], got %r" % (xy,))
                rec["action"] = "click"
                rec["px"] = xy[0]
                rec["py"] = xy[1]
                px, py = java_number_int_value(xy[0]), java_number_int_value(xy[1])  # Harness l.825
                try:
                    sim.click(px, py, rec)
                    rec["exception"] = None
                except JavaException as e:
                    rec["exception"] = e.describe()
                rec["story_text"] = sim.story_text
                rec["sensor_text"] = sim.sensor_text
                rec["instructions"] = sim.instructions
            else:
                raise HarnessError("unknown applet step %r" % (s,))
        rec["stdout"] = buf.getvalue()
        out.append(rec)
    return out
