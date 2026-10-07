# Design

This document is the contract the code is built against. Update it when a decision changes.

## Goals

1. A small, dependency-free Python package, `cyber_detectives`, that answers the questions of
   our two papers:
   - **Problem 1** (STAR, ICRA): is an agent's story consistent with the observation history?
     Single agent, and an unknown number of agents (STAR §5). Return a witness path when it is.
   - **Problem 2** (ICRA): story and sensors cover different time intervals (six cases).
   - **Problem 3** (ICRA): partial story; find a shortest consistent super-sequence.
   - **Problem 4** (ICRA): story with errors; find a consistent story with fewest edits.
2. `compat="original"` reproduces the original Java code (arc-l/cyber-detective@55f57f8)
   exactly, bugs and crashes included, pinned by the golden fixtures in `tests/fixtures/golden/`.
3. The default (corrected) mode equals the original wherever the original is correct; every
   difference is attributed to a documented bug (`docs/notes/original-bugs.md`, B1–B10).
4. A static browser demo in `docs/` whose JS engine matches Python (parity fixtures), and a
   matplotlib viewer (`viewer.py`, CLI `view`).

Python ≥ 3.9, standard library only; `matplotlib` is an optional extra (`viewer`), imported
only by `viewer.py`. The JS is classic scripts with no dependencies and runs from `file://`.

## Semantics (default mode)

The supporting passages are in `docs/notes/paper-examples-star.md` §3 and
`docs/notes/paper-examples-icra.md` §1.

- **Map.** Rooms, beam detectors (two sides each), occupancy sensors, and free **regions**
  (connected components of the free workspace, the `R_k` of the papers). Each region touches a
  set of features; each beam side touches exactly one region. The paper's connectivity graph
  `G` (STAR Fig. 3(a)) is derived: two features are adjacent iff they touch a common region.
  - The default engine works on regions, not on `G`. `G` cannot express "agent x waits in a
    region that touches no room" across a deactivation (case
    `star_multi_wait_in_unlabelled_component`); STAR Fig. 3(b) is the region view.
  - A map given only as `G` edges gets one region per maximal clique of `G` (`Map.from_edges`).
    The cliques are not always the true regions: in ICRA Fig. 2, `A–B` (R1), `A–o1` (R3) and
    `B–o1` (R2) form the spurious clique `{A, B, o1}`, which replaces R1 = `{A, B}` (the other
    paper maps are recovered exactly). Verdicts never change, though, when `G` comes from a
    valid region map: a beam side's only maximal clique is its own region, and a spurious
    clique holds only rooms/occupancy sensors that pairwise share a true region, so any move
    through it can use a true region instead. The one exception is a feature with no edges,
    which gets a region of its own (in the true map it might touch none). Tested on random
    maps (`tests/test_engine.py::test_from_edges_keeps_verdicts`). Builtin maps carry explicit
    regions.
- **Rooms.** `C_p` is the map's room set (not just the rooms named in the story). Entering a
  room is a visit and must be the next story element. Repeated consecutive entries (`A, A`) are
  allowed (leave and re-enter).
- **Start/end.** The agent is inside `p_1` at `t_0` and inside `p_n` at `t_f` (after the last
  recording), the whole story used.
- **Single agent.** Every recording is made by x. A beam recording is a crossing from one side
  to the other. Occupancy activation = x enters from a touching region; x stays inside until
  the matching deactivation and then leaves into a touching region.
- **Multiple agents.** Occupancy events only toggle whether a region can be traversed; x may
  be inside an occupancy region only while it is active and must be out by its deactivation.
  x may cross a beam only at a recorded event of that beam (at most one crossing per event) and
  need not cross at any of them. All sensors are inactive at `t_0`.
- **Room visits are always reported** (STAR §2.1). ICRA drops that sentence; the opposite
  reading is available as `unreported_visits=True` for Problems 1–4 (fixtures record both).
  Under it, every room entry may also go unreported, but x must still be inside `p_n` at
  `t_f` with the whole story reported.
- **Malformed input.** A history the agent model cannot produce (single: an occupancy
  activation not immediately followed by its deactivation, a lone deactivation, a beam
  "D"; multi: non-alternating activations, a beam "D") gives `consistent=False` with a
  `reason` starting `"malformed history: "` that names the first offending recording
  (1-based). Unknown rooms/sensors, an empty story or an unreadable history are input
  errors (`InputError`, a `ValueError`), not inconsistencies.
- **Not supported:** the reading "`p_n` is merely the last room visited" (fixture keys
  `consistent_if_story_need_not_end_in_last_room`); the original's behaviour under it is bug
  B8 and is available only through `compat="original"`.

**Problems 2–4.** All take `agents` and `unreported_visits` as above.

- **Problem 2** (`docs/notes/paper-examples-icra.md` §1): room entries are story elements only
  in `[t0, tf]`; recordings happen only in `[t0', tf']`, and outside it x crosses beams and
  enters occupancy regions unseen; x is inside `p_1` at `t0`, inside `p_n` at `tf` (whole story
  told), outside every occupancy region at `t0'` and `tf'`; free before the first and after
  the last boundary. Boundary placement among the recordings is searched (the paper's "start
  at `(p1, j)` for all `j`"). Multi-agent: the multi rules inside `[t0', tf']`, with the same
  "outside occupancy at `t0'`/`tf'`" rule.
- **Problem 3.** `p'` may use every room of the map. Default `anchored=True`: `p'_1 = p_1` and
  `p'_last = p_n` (§II-A, "Start/end" above). `anchored=False` is the §V-A / Algorithm 2
  reading (`p' = ω1 p1 ω2 … pn ω(n+1)`); only then may `story` be empty (answer: a shortest
  consistent story, the paper's `n'`). The fixtures record both; they differ, e.g. on
  `ACB` / `b2 b1 b2` (anchored: none; free: `BACB`). Cost = number of inserted visits.
- **Problem 4.** Unit-cost insertion, deletion, substitution; `p'` non-empty, any rooms;
  empty `story` allowed.

## Algorithm (default mode)

**Problem 1** (`engine.py`) is the composite automaton of ICRA §IV, built implicitly: a state
is `(position, story index)` after processing a prefix of the history, where `position` is a
room, a region, or the inside of an occupancy region (positions are map names, which are
unique across kinds). Between consecutive recordings the agent closes over free moves (region
↔ touching room = consume the next story element if it matches; region ↔ active occupancy
region in multi-agent mode). A recording advances every state across the event (single:
mandatory; multi: optional for beams, connectivity change for occupancy). This is the paper's
dynamic program, `O((n+1)(m+1)·|map|)` states, with back-pointers for the witness.

Each layer is closed by breadth-first search in map order (rooms, beam sides, occupancy;
regions in file order), so the witness is deterministic and takes the fewest free moves
between recordings for the states it reaches. It is generally not the original's
`getAgentStory` path: on `ACBAC` / `b1 o1 o1 o2 o2 b2` the engine gives
`AC[b1d][o1][o2]B[b2r]AC`, the original `A[b1u]C[o1][o2]B[b2r]AC`. A failure reason says
which recording no walk can explain, how many story elements can be matched, or that x cannot
end inside `p_n`.

**Problems 2–4** (`problems.py`) use one search: the engine's product automaton extended by a
*phase* (which interval boundaries `t0`, `tf`, `t0'`, `tf'` have passed, in the order of the
case; Problems 1/3/4 have the two combined boundaries `t0=t0'`, `tf=tf'`), over states
`(h, phase, position, k, fresh, anchor)` with insertion/deletion/substitution costs. `k`
counts story elements accounted for; `fresh` and `anchor` exist only under
`unreported_visits` (x must end in the room it reported last; WLOG that is its final room
entry, except that x may come back unreported to `p'_1` while nothing else is reported).
It is solved by Dijkstra on (cost, number of transitions) with a FIFO tie-break and
successors in map order, so answers and witnesses are deterministic and take the fewest
steps among optimal ones. Engine reuse: `engine._Problem.event_moves` and `.active`.

**Paper-literal constructions**, for the figures and for teaching, not used by the above:

- `subgraphs.py`: STAR Algorithms 1, 2, 4; sensing-induced subgraphs `G_j`; composite graph
  `G_s` and the DP of Algorithm 3; the multi-agent family of Fig. 7; ICRA `SUBG`, NFAs `M_j`,
  composite automaton `M`, Algorithm 1. Each builder takes `variant="literal"` (as printed,
  reproducing the figures' own artefacts: unreachable goals in `V'`, goal-goal edges after
  clique-ification, one vertex for a start that is also a goal, false for `n = 1`, no history
  check) or `"corrected"` (the fixes listed in `docs/notes/paper-examples-{star,icra}.md`;
  module docstring). The corrected single-agent `G_s` DP and the corrected ICRA `M` decide
  exactly what `validate` decides (randomized tests). The multi-agent family cannot be made
  exact on `G` (`star_multi_wait_in_unlabelled_component`), so its corrected variant is built
  on the region graph (Fig. 3(b)), with parts ending at every beam recording and every
  deactivation and rooms, regions and active occupancy regions handed over at deactivations;
  it also agrees with `validate(..., agents="multi")`. Fig. 7's side numbering (`G41_5` starts
  at `b2l`) follows the map's `vertex_order` when present, else the beam side order.
- ICRA's final state `F`: p. 4983 first makes the room states of `M_{m+1}` its acceptance
  states ("when i = m, we let all vertices in C_p be acceptance states") and then says "we
  connect all of M_{m+1}'s states to a single acceptance state F". Both variants follow the
  first sentence (x ends inside `p_n`). Reading the second sentence alone, which contradicts
  the first and accepts stories that end before the last recording, is an ambiguity of the
  text rather than a figure artefact; it is available as `icra_composite(...,
  end_anywhere=True)` (`docs/notes/paper-examples-icra.md` §7 item 20).
- `problems.py`: `algorithm2_as_printed` (ICRA Alg. 2 verbatim: returns `True` on `DAD` /
  `b1 b3 o2 o2 b4` where no `p'` exists), `algorithm2_corrected` (the fix of the notes, =
  free Problem 3 length; tested on random inputs), `case2_procedure_as_printed` (false on
  `AB` / `b1 b3`, which case 2 accepts). The automaton for them has separate entry copies of
  each `M_j`'s start states.

## Package layout

```
src/cyber_detectives/
  __init__.py      public API (below)
  maps.py          Map: rooms, beams (sides), occupancy, regions, geometry; G; JSON I/O;
                   builtin maps (data/*.json)
  history.py       Event, parse_history, parse_story, well-formedness checks
  engine.py        default Problem 1 engine (single + multi), witness paths
  problems.py      Problems 2, 3, 4 (+ paper-literal Alg. 2 and case-2 procedure)
  subgraphs.py     paper-literal Algorithms 1, 2, 4, G_s, NFAs
  geometry.py      exact regions from drawings, hit-testing, polylines, walk simulation
  viewer.py        matplotlib live viewer (the only module that imports matplotlib)
  cli.py           `python -m cyber_detectives ...`
  compat/
    javahash.py    Java 8 HashMap/HashSet iteration-order emulation (canonical JVM setting)
    original.py    line-by-line port of the original non-GUI Java code
    applet.py      the applet's non-drawing logic (parsing, Run/Reset, click handling)
  data/            builtin maps (JSON, with provenance; geometry/ for drawings)
tests/             pytest suite; js/ (node --test: engine, original port, demo UI logic);
                   fixtures/ (paper/, golden/, parity/; see fixtures/README.md); oracles/
docs/              browser demo (index.html, js/, css/); DESIGN.md; notes/;
                   media/ (README images and the scripts that make them)
tools/             gen_parity.py (parity fixtures), gen_demo_data.py (demo routes and
                   presets), viewer_snapshot.py, demo/check.js (headless Chrome check of
                   the demo), reference/ (Java harness: golden fixtures, live differential)
```

## Public API

```python
from cyber_detectives import (
    Map, builtin_map, load_map, builtin_map_names,   # maps
    parse_story, parse_history, Event,               # inputs
    validate,                                        # Problem 1
    validate_intervals,                              # Problem 2
    shortest_superstory,                             # Problem 3
    closest_story,                                   # Problem 4
    replay, Result, Step, path_to_string,            # witness checking
    possible_positions,                              # where x can be, per time slot
    MapError, InputError, InvalidPath,               # errors (all ValueError subclasses)
)

m = builtin_map("star_fig2")               # also "star_fig1", "icra_fig1", "icra_fig2"
story, history = "ACBAC", "b1 o1 o1 o2 o2 b2"
r = validate(m, story, history)            # agents="single" (default)
r.consistent      # True
r.reason          # None, or why not (or why the history is malformed)
r.path            # list[Step] | None: witness walk
r.path_string()   # "AC[b1d][o1][o2]B[b2r]AC" (original's notation, see below)
validate(m, story, history, agents="multi")         # path_string() "AC{o1}{o2}B[b2r]AC"
validate(m, story, history, unreported_visits=True)
validate(m, story, history, compat="original")      # original's verdict and path, bugs included:
                                                    # to_dict()["path_string"] "A[b1u]C[o1][o2]B[b2r]AC"
replay(m, r.path, history, agents="single", story=story)  # raises InvalidPath if wrong
possible_positions(m, history, story=None, agents="single", unreported_visits=False,
                   *, starts="rooms")      # [["A", "C", "R1", "R2"], ...]: one list per slot
```

- **Maps.** `Map(name, rooms, beams, occupancy, regions, *, geometry, title, source,
  provenance, edges, vertex_order)`; `beams` is `{name: [side0, side1]}` or a list of
  `[name, [side0, side1]]` pairs; `regions` is `{name: [features]}`, a list of
  `[name, [features]]` pairs, or a list of feature lists (named `R1`, `R2`, ..., skipping
  names taken by rooms, beams, sides or sensors). A non-empty list whose items are all
  `[string, list]` pairs is the pair form (a feature list never has a list item). Any other
  container raises `MapError`. `Map.from_dict` / `to_dict` use the fixture format plus
  `regions` (and `title`, `provenance`, `vertex_order`, `geometry` when present).
  **Name order in JSON:** `to_dict` writes `beams` as an object, and `regions` as an object,
  except that each of the two becomes a list of `[name, value]` pairs when one of its names
  consists of digits only (`"7"`, `"007"`). JavaScript objects iterate integer-like keys first
  in numeric order whatever the JSON text says, so only the pair form keeps the map order
  (which fixes search order, witnesses and messages) in the JS engine; `from_dict` reads both
  forms in Python and JS. A hand-written JSON map with all-digit names in object form is read
  in JSON-text order by Python and in JS object order by the demo. Names are otherwise
  arbitrary `[A-Za-z0-9_]+` strings: `constructor`, `__proto__`, `toString` work like any
  other name in both engines (the JS engine keys every table by a native `Map`). Without
  `regions` it falls back to `Map.from_edges`; with both, the edges must equal the derived
  `G`. The given edge order and `vertex_order` are kept and emitted by `to_dict` because the
  original's output depends on them (`data/star_fig2.json` carries `getBasicGame`'s). Queries:
  `features`, `sides`, `sensors`, `kind(name)`, `regions_of[feature]`, `side_region(side)`,
  `other_side(side)`, `edges()` (G), `adjacency()`, `region_graph_edges()` (Fig. 3(b)).
  Inconsistent maps raise `MapError` naming the offending item. `load_map(path)` reads a JSON
  file. `builtin_map(name, *, geometry=True)` loads `data/<name>.json` and, if `data/geometry/<name>.json`
  exists and the map has no inline geometry, attaches it as `Map.geometry` (the file's
  `"geometry"` member if present, else the whole object).
- **Stories** are a string of one-letter room names (`"ACBAC"`), a space/comma-separated
  string, or a list of names (Python: list or tuple). A string without separators is one
  room per character; use separators or a list for multi-character room names.
- **Histories** are a list (Python: list or tuple) whose items are `Event(sensor, kind)`
  (JS: `CyberDetectives.Event`), `[sensor, kind]` pairs (fixture format) or
  `{"sensor": ..., "kind": ...}` mappings with exactly those two keys (JS: objects with
  exactly those two own keys, or a `Map`), with `kind` in `{"A","D"}`, items of these forms
  mixed freely; or a string of sensor names in which an occupancy
  name toggles activation/deactivation per sensor (ICRA's `b1 b3 o2 o2 b4` notation).
  Explicit `o1+` / `o1-` tokens are also accepted (and keep the toggle in step). Separators
  are spaces and/or commas; a list of such tokens works too. Telling a bare beam token from an
  occupancy token needs the map.
- **Rejected input forms.** Anything else as the whole story or history -- a mapping (it is
  not read as its keys), a set, a generator or other iterator, a number, `None` -- raises
  `InputError` with the same text in both engines: `story must be a string or a list of room
  names, got <repr>` / `history must be a string or a list of events, got <repr>`.
- **Result.** `Result(consistent, reason, path, agents, compat)`; `to_dict()` adds
  `path_string`. `bool(result)` is the verdict.
- **Step** (`engine.Step`, frozen dataclass): `kind`, `position` (where x is after the step),
  `time` (recordings completed when the step is done: a free move happens between recording
  `time` and `time+1`), `story_index` (story elements accounted for after the step), `sensor`,
  `event` (0-based recording index explained by the step). Kinds: `start` (inside `p_1`,
  time 0), `visit` (reported room entry), `move` (room → region; region ↔ occupancy region in
  multi-agent mode, with `sensor` set), `unreported` (room entry with
  `unreported_visits=True`), `cross` (`sensor` = side crossed from), `enter` / `exit`
  (single-agent occupancy activation / deactivation).
- **Path notation.** `path_string()` (and `path_to_string(steps)`) follows the original's
  `getAgentStory` output: rooms in order, and for each recording attributed to x the sensor
  vertex the agent was on *before* the event in brackets (`[b1u]` = crossed b1 from its u
  side; `[o1]` = passed through o1). In multi-agent mode, traversals of an occupancy region
  opened by other agents appear as `{o1}` (written when x enters it), and beam crossings by x
  as `[b1u]`; recordings x does not make leave no trace. An unreported room entry
  (`unreported_visits=True`) appears as `(D)`.
- **replay** re-derives the active occupancy sensors and checks every step (adjacency,
  timing, every recording explained once in single mode, at most one crossing per beam
  recording and never inside a deactivating sensor in multi mode, story spelled, ends in
  `p_n`); the tests replay every witness they get.
- **possible_positions** (`engine.py`) answers "where could x be?": one list per time slot
  (slot `h` = between recordings `h` and `h+1`; `m+1` slots), holding the positions (rooms,
  regions, occupancy sensors = inside their region) that x occupies during that slot on some
  walk consistent with the *whole* history, in map order (rooms, regions, occupancy). It is
  a forward-backward pass over the engine's `(position, k)` layers (reachable from the start
  and able to explain the rest). With `story`, the walks are those of `validate` (all lists
  empty iff inconsistent); with `story=None` it is the pure sensor filter (STAR's
  combinatorial filter): room entries are free, x may end anywhere, and x starts per the
  keyword-only `starts`: `"rooms"` (default) inside some room, as in every story (Problem 1);
  `"anywhere"` inside some room or in some free region, never inside an occupancy region (all
  sensors are inactive at the first slot): the filter for an interval that begins while x is
  already on its way, e.g. the recordings of Problem 2 (`[t0', tf']`). `starts` other than
  these two raises `ValueError`, and so does `starts="anywhere"` with a story (x then starts
  inside `p_1`). JS: `possiblePositions(map, history, {story, agents, unreportedVisits,
  starts})`, same defaults and messages. Malformed history: empty lists. Tested against an
  explicit time-expanded graph for story, `"rooms"` and `"anywhere"`
  (`tests/test_engine_states.py`).
- **Shading** ("where x could be"). The demo shades the slot of the current step. For a
  consistent Problem 1 story, or a Problem 3/4 answer `p'`, that is
  `possible_positions(..., story=...)`; otherwise the sensor filter with `starts="rooms"`.
  Problem 2 never uses its story: it shades the sensor filter with `starts="anywhere"` (the
  sensors' interval may begin while x is on its way), and only inside `[t0', tf']`; outside
  it the recordings say nothing about x, and nothing is shaded. The
  viewer shades `viewer.information_state(m, history, agents)`, the last slot of the sensor
  filter with `starts="rooms"`.
- **Submodules.** Not re-exported, loaded on first attribute access (PEP 562 `__getattr__`
  in `__init__.py`, so `import cyber_detectives` stays cheap): `cyber_detectives.compat`
  (the port behind `compat="original"`), `cyber_detectives.subgraphs` (paper-literal
  constructions), `cyber_detectives.geometry`, and `cyber_detectives.viewer` (`Viewer`,
  `run(m, agents, theme, claim, save)`, `information_state`, `NoDisplayError`; imports
  matplotlib lazily).

**Problems 2–4** (`problems.py`; all take `agents="single"|"multi"` and `unreported_visits`;
`compat` other than `None` raises `ValueError`):

```python
validate_intervals(m, story, history, case=2)          # IntervalResult(Result): adds .case
shortest_superstory(m, story, history, anchored=True)  # SuperstoryResult .story .length .inserted .path, or None
closest_story(m, story, history)                       # ClosestResult .story .edits .operations .path, or None
```

- `closest_story`'s `operations` lists exactly `edits` `EditOp(op, index, old, new)` (`index`
  into the original story; an insert goes before `story[index]`).
- Witnesses of Problems 3/4 are ordinary engine `Step` lists for `p'` (`replay(m, path,
  history, agents, story=p')` passes). Problem 2's witness uses extra step kinds `begin`,
  `mark`, `pass` (unrecorded beam crossing) and `unseen` (unrecorded occupancy entry/exit);
  `replay` does not accept it; `path_string()` writes `|t0|`, `<b1u>`.
- A malformed history: Problem 2 gives `consistent=False` with a `"malformed history: "`
  reason; Problems 3/4 return `None`.

## `compat="original"`

Accepted by `validate` only (Problems 2–4 have no original code). It runs
`validateAgentStory` + `getAgentStory` (single) or `validateAgentStoryMulti` (multi, no path)
the way the applet does, and raises the original's crashes as `compat.original.JavaException`
subclasses (`ArrayIndexOutOfBoundsException`, `NullPointerException`) carrying the Java
source location.

- `validate` calls `compat.original.validate_compat(map_dict, story_list, event_pairs,
  agents)` with `map_dict = m.to_dict()`, the story as a list and `[[sensor, kind], ...]`; it
  returns `(consistent, path_string_or_None)`. Unknown rooms/sensors are passed through
  unchecked so the port can reproduce the original's own failures; the module is imported
  lazily and its absence raises `NotImplementedError`. `validate_compat` always builds the
  game with `build_game(map_dict)`, the reference harness's generic builder (for STAR Fig. 2,
  with its `vertex_order` and edge order, it equals `getBasicGame()`; a golden test checks
  this). Unknown story names become null, as in the applet; unknown sensor names or events
  raise `ValueError`, because the original cannot express them; an empty story skips
  `updateStartingVertex`, as the harness does, instead of returning the applet's "Nothing to
  validate.".
- The full ported API lives in `cyber_detectives.compat.original` under the Java names
  (`validate_agent_story`, `get_agent_story`, `get_agent_story_statuses`,
  `validate_agent_story_multi`, `get_sub_graph`, `get_sub_graph_multi`,
  `get_reachable_subgraph`, the `DetectiveGame` builders, ...) and can capture the original's
  stdout traces (`capture_stdout()`, which redirects like `System.setOut`).
  `compat.original.run_harness_case(case, maps)` emulates `tools/reference/Harness.java` op
  by op (`tests/test_compat_golden.py`). `compat.applet.Applet` ports the applet's Run, Reset
  and click handlers (text parsing, result texts, hit-testing); `compat.applet.run_steps`
  emulates the harness's `applet` op.
- **Iteration order.** The original's results depend on `HashSet<Vertex>` iteration order
  (identity hash codes). Golden fixtures pin the JVM setting
  `-XX:+UnlockExperimentalVMOptions -XX:hashCode=2` (every identity hash equal); the port
  emulates Java 8 `HashMap` exactly under that setting, including bins that treeify at 11+
  members, pinned against the real JDK by `tests/fixtures/golden/javahash.json`
  (`tools/reference/javahash_probe.py`). Outputs that changed under alternative settings are
  marked `order_dependent` in the golden files. Live differential runs add
  `-XX:-OmitStackTraceInFastThrow`, so that HotSpot never replaces a repeated implicit NPE by
  a preallocated one with an empty stack trace (`tools/reference/README.md`).

## CLI

`python -m cyber_detectives` or `cyber-detectives`:

- `validate --map M --story S --history H [--multi] [--compat original]
  [--unreported-visits] [--json]`;
- `intervals --case N`, `superstory [--free]` (`anchored=False`) and `closest` for
  Problems 2–4, with the same input options, `--multi`, `--unreported-visits` and `--json`;
- `maps [NAME] [--json]`;
- `view --map M [--story S] [--multi] [--dark] [--save FILE]`: the viewer (a map with
  geometry); `--save` writes the first frame to FILE (format from the extension, PNG if
  none) instead of opening a window.

`--map` is a builtin name or a JSON file. Text output: the first line is `consistent` or
`inconsistent` (Problems 3/4: `consistent` with the story found, or just `no solution`), then
only the fields that apply (`case`, `story`, `inserted`, `edits`, `path` or `reason`); `--json` prints the
complete result (`to_dict()` plus `map`). Exit status: 0 consistent / success, 1
inconsistent / no solution, 2 input error, 3 not implemented (also `view` without
matplotlib, or without `--save` and no interactive backend), 4 the original crashed
(`--compat original`).

## Geometry (`geometry.py`, `data/geometry/*.json`)

- **Files.** `data/geometry/<map>.json` = `{name, map, title, provenance, notes, geometry}`;
  `geometry` holds `bounding_box {rect, stroke}`, `walls {stroke, cap, segments}`, `rooms`,
  `occupancy` (`[x, y, w, h]`), `beams`, `regions {R: {point}}` (names + label points) and
  `portals` (precomputed, see below). Screen coordinates, y down; everything axis-aligned;
  caps `square` (Java default) or `butt`. A beam is either two-sided (applet:
  `{stroke, sides: {b1u: seg, b1d: seg}}`, the strip between the strokes belongs to the beam)
  or one segment (`{segment, stroke, sides: {b31: "-x", b32: "+x"}}`, each side owns one
  half). Files: `star_fig2` (applet coordinates verbatim), `icra_fig1` (copy of it: same
  workspace), `icra_fig2` (measured from ICRA Fig. 2). `star_fig1` has none (no rooms).
- **Regions from the drawing** use an exact cell decomposition on all rectangle edges; cell
  owner priority wall > beam side > beam band > occupancy > room > free; regions = 4-connected
  free cells; a feature touches a region iff they share an edge of positive length. Obstacles
  are closed sets. `check_geometry(map)` must return `[]` for every builtin map that has a
  drawing (`star_fig2`, `icra_fig1`, `icra_fig2`; tested); on a map without one (`star_fig1`)
  it raises `GeometryError`.
- **Hit-testing** (`Geometry` methods; `as_geometry(map)`). `location(x, y)` → `(kind, name)`
  in the decomposition's terms (the model the simulator uses); `feature_at(x, y,
  beam_tolerance=0)` → click target in the applet's order (occupancy, rooms, beam sides);
  `region_at`, `in_free_space`.
- **Drawing.** Portal per (region, feature): `region_point` in the region, `feature_point`
  inside the room/occupancy rectangle or on the beam's centre line, joined by a straight
  segment through the widest doorway. `path_polyline(map, path)` accepts what
  `expand_path` accepts: a `Result`, a list of `Step` (or `to_dict()`s), a `path_string`
  (regions guessed: first common region) or a list of place names (as in the fixtures'
  `witness_walk_regions`); `trace_polyline(geometry, points)` replays a polyline and returns
  the story/history it would produce (tests: drawn witnesses replay exactly).
- **Simulation** (Monte Carlo oracle). `simulate_walk(geom, rng, steps)`: x starts inside a
  random room, makes random straight moves and routed excursions through random doorways;
  every segment is checked against the decomposition and rejected if it touches a wall, stops
  on a beam, enters and leaves a beam on the same side, or makes a transition the region
  model lacks (e.g. through a beam stroke lying inside a room). Events come only from that
  check; the walk is cut at its last room entry. `simulate_multi(geom, rng, agents, steps)`:
  agent 0 is x (as above, cut at its final time `t_f`); others start in a room or region
  (never inside an occupancy region) at random speeds and are cut at `t_f`; beams record every
  crossing, an occupancy sensor activates when its first occupant enters and deactivates when
  its last leaves (it may still be active at `t_f`); other agents' room entries are unobserved.

## What is ported from the original, and what is not

Ported (non-GUI): everything in `projects/cyberDetective/*.java` except drawing, including
the graph primitives (`Edge.getEdgeId`, `Graph.addEdgelessVertex`,
`getEdgeBetweenVertices`, `Vertex.removeNeighbor`, ...); the applet's input parsing,
Run/Reset pipeline and click → "reachable features" logic; `Environment` hit-testing and
click scaling. Not ported: AWT/Swing drawing, `common/util/FileHelper` (applet
resource/URL loading, no algorithmic content), `AwtApplet`. `IDGenerator` is folded into the
builders.

## Testing

- `tests/fixtures/paper/`: every paper example is a regression test (default mode).
- `tests/fixtures/golden/`: `compat="original"` must reproduce every recorded return value,
  exception and (canonical-order) stdout trace exactly. `tools/reference/regen_golden.sh`
  rebuilds all of them, including `paper_cases.json` (with its `crosscheck` block, from
  `gen_cases.py` and `paper_crosscheck.py`) and `javahash.json`.
- Independent oracles on randomized inputs: brute-force enumeration on the region model
  (`tests/oracles/`, method in its README; validated against every paper expectation by
  `tests/test_oracle_self.py`, compared with `validate`, `validate_intervals`,
  `shortest_superstory` and `closest_story` by `tests/test_oracle_random.py`, which also
  replays every witness through independent `path_string()` and `Step` checkers), and
  geometric Monte Carlo walks on maps with geometry (every simulated walk's story/history
  must validate, its path must replay). Randomized sweeps scale with `CD_TEST_SCALE`
  (default 1).
- Differential: `tools/reference/differential.py` runs fresh random inputs through the live
  original (when a JDK is available) and through `compat="original"`; outputs must match.
- Default vs original (`tests/test_default_vs_original.py`): every golden verdict (validate
  and getAgentStory cases that returned, applet Runs) is recomputed in default mode; wherever
  the verdicts differ, named bug detectors must explain the difference. A detector is a
  predicate on the input paired with a relaxation of the default semantics that mimics the
  bug (B5 the original's reading of malformed occupancy recordings, B7b beam "D" read as a
  crossing, B7c start hard-wired to the first room, B8 the story may end before the last
  recording, B1 the original's multi-agent occupancy timing); a difference is explained when
  the firing detectors' relaxations together give the original's verdict. Those relaxations
  together reproduce *every* original verdict on the golden data, and where both modes say
  true (single agent) the original's path must replay under the independent path checker
  unless detector B3 flags it. Counts per category are pinned in the test and reported in
  `docs/notes/original-bugs.md`.
- Integration (`tests/test_integration.py`): the public API matches this document;
  `Map.from_dict(d).to_dict()` and the builtin maps feed `compat.original.build_game`
  unchanged (`builtin_map("star_fig2")` builds exactly `getBasicGame()`: ids, neighbour and
  edge iteration order); `validate(Map, ..., compat="original")` reproduces every golden
  map-based verdict, path and crash; the CLI with the real port.
- Paper-literal constructions (`tests/test_subgraphs.py`): every figure-level expectation of
  the paper fixtures, both variants, and randomized agreement of the corrected variants with
  `validate`.
- JS parity: `tests/fixtures/parity/*.json` generated from Python by `tools/gen_parity.py`
  (`tests/test_parity.py` fails when they are stale and runs `npm test` when Node.js >= 18.1
  is on PATH); `npm test` checks the demo engine against them (`names.json`: names that are `Object.prototype` members, `__proto__`, or all
  digits). The generator keeps every input string to characters assigned in Unicode 3.2 with
  an unchanged category (checked with `unicodedata.ucd_3_2_0`), because `repr` escapes
  non-printable characters per the Unicode version of the Python build (3.9: 13.0, 3.13:
  15.1) or of the browser; the remaining known JS differences are listed at the top of
  `docs/js/engine.js`.
- Packaging (`tests/test_packaging.py`): version and license metadata; with setuptools >= 77,
  a wheel built from a copy of the project must hold every module, every builtin map and the
  `cyber-detectives` script. The rest of the suite imports `src/` (pytest `pythonpath`); to
  test an installed package instead, run it from a copy of the tree without `src/` with
  `python -m pytest -o pythonpath=` (the tests that need `src/` then skip).
- Demo and viewer: `tests/js/ui.test.js` (demo logic, e.g. every witness position lies in
  the shading of its step), `tests/test_demo_data.py` (precomputed demo data is current),
  `tools/demo/check.js` (the page in headless Chrome), `tests/test_viewer.py` (walks, keys,
  `view --save`), `tests/test_readme.py` (every README command and snippet runs as shown).
