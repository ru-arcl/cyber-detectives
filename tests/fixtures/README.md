# Test fixtures

All fixtures are JSON. Three kinds live here:

- `paper/`: worked examples extracted from the papers (figures, equations, text), each
  independently re-verified before use. They pin the default (corrected) mode.
- `golden/`: outputs recorded from the original Java code by the reference harness in
  `tools/reference/`. They pin `compat="original"`, bugs included. `golden/README.md`
  describes them in detail.
- `parity/`: answers of the Python package, recorded for the JavaScript engine of the demo
  (see "Parity files" below).

Every file is a JSON object. The table lists the top-level keys.

| File | Top-level keys |
|---|---|
| `paper/star.json` | `source`, `notes`, `semantics`, `maps`, `cases` |
| `paper/icra.json` | `source`, `notes`, `semantics`, `problem2_interval_cases`, `maps`, `cases`, `figures` |
| `paper/star_fig2_geometry.json` | a single map object (see "Maps"): `name`, `source`, `provenance`, `rooms`, `beams`, `occupancy`, `edges`, `geometry`, `applet`, `expected`, `verified_by` |
| `golden/<set>.json` | `header`, `maps`, `cases` |
| `golden/javahash.json` | `header`, `scripts` |
| `golden/summary.json` | statistics only (`note`, `sets`, `totals`, `files`) |
| `parity/<set>.json` | `description`, `functions`, `generator`, `maps`, `cases` |

What the paper-file keys mean:

- `source` names the paper.
- `notes` points to the note in `docs/notes/` that explains each case.
- `semantics` states the reading of the problem that the expectations assume (start, room
  visits, `C_p`, repeated entries, end, single vs. multi, initial sensor state).
- `problem2_interval_cases` holds the six ICRA §III-B interval orderings.
- `figures` holds figure-level expectations that belong to no single case: free components,
  ICRA Fig. 4–6 and the subgraphs `G_j`.

The same STAR Fig. 2 graph appears under four names: `star_fig2` in `paper/star.json` and
in the golden files, `icra_fig1` in `paper/icra.json`, and `star_fig2_geometry`. The four
copies must stay equal as edge sets.

## Maps

A map is the connectivity graph `G` of the papers. Its vertices are the rooms, the
occupancy sensors and the two sides of each beam detector. An edge joins two vertices that
touch a common connected component of the free workspace.

```json
{
  "name": "star_fig2",
  "source": "STAR Fig. 2 / Fig. 3(a); original DetectiveGame.getBasicGame()",
  "rooms": ["A", "B", "C"],
  "beams": {"b1": ["b1u", "b1d"], "b2": ["b2r", "b2l"]},
  "occupancy": ["o1", "o2"],
  "edges": [["A", "b1u"], ["A", "C"]]
}
```

Beam sides are listed in the order the original passes them to `BeamDetector`
(`sensorVertices[0]`, `sensorVertices[1]`).

`beams` (and `regions`, below) may also be a list of `[name, value]` pairs, e.g.
`"beams": [["b1", ["b1u", "b1d"]], ["7", ["7a", "7b"]]]`. `Map.to_dict` writes this form
when a name consists of digits only, because JavaScript iterates such object keys first
whatever the JSON text says, and the map order fixes search order, witnesses and messages
(`docs/DESIGN.md`, "Name order in JSON"). Both forms are read by Python and JS.

Optional map keys:

- `regions` holds the free components (the papers' `R_k`). Paper maps give them under
  `expected` (`free_components_fig2`) or `figures`. Random golden maps list them as
  `regions` for provenance.
- `vertex_order` and the **order** of `edges` matter only for `compat="original"`. They
  set the original's vertex ids and neighbour insertion order (see "Harness keys" below and
  `golden/README.md`).
- `filler_occupancy` (harness and `compat.original` only) is a compact way to write a map
  with very many vertices: `"filler_occupancy": N` adds N edgeless occupancy sensors named
  `f1`, …, `fN` after the listed `occupancy`. In `vertex_order` they take the place of the
  single entry `"..."` (they are appended when there is no `"..."`). N = 65,535 pushes the
  ids of the vertices after `"..."` above 65535, where the original's edge ids collide (bug
  B7a); `golden/edge_cases.json` uses it. Example: `{"rooms": ["A", "B", "C", "Z"],
  "filler_occupancy": 65535, "vertex_order": ["A", "B", "C", "...", "Z"], ...}`.
- `expected` and `verified_by` on a map carry map-level checks, such as the free
  components, the region graph of STAR Fig. 3(b), and the edges the original adds.
- `geometry` and `applet` (only in `star_fig2_geometry.json`) give the applet's world
  coordinates, its hit rectangles and its click scaling.

## Observation histories

A history is a list of `[sensor, event]` pairs in time order. `event` is `"A"`
(activation) or `"D"` (deactivation). Beam detectors produce only `"A"`; the golden files
also feed the original a beam `"D"` on purpose (bug B7). Example STAR eq. (2):

```json
[["b1","A"], ["o1","A"], ["o1","D"], ["b2","A"], ["o2","A"], ["o2","D"]]
```

## Cases

```json
{
  "id": "star_eq1_eq2_single",
  "source": "STAR §2.3, eq. (1)-(2), p. 395-396",
  "map": "star_fig2",
  "mode": "single",
  "story": ["A", "C", "B", "A", "C"],
  "history": [["b1","A"], ["o1","A"], ["o1","D"], ["b2","A"], ["o2","A"], ["o2","D"]],
  "expected": {"consistent": false},
  "verified_by": "how this expectation was independently checked"
}
```

- `map` is a map name from the same file, or an inline map object (golden only).
- `mode` is `"single"` or `"multi"`. In `"single"`, the agent triggers every recording.
  In `"multi"`, an unknown number of other agents may also be present.
- `note` (optional) is free text.
- `expected.consistent` is the Problem 1 verdict under the file's `semantics`.
- Alternative readings use keys of the form `consistent_if_<condition>`. Each one gives
  the verdict under the opposite reading of one semantic choice:
  - `consistent_if_unreported_room_visits_allowed` (ICRA): entering a room need not be a
    story element. This is the default engine's `unreported_visits=True`.
  - `consistent_if_story_need_not_end_in_last_room` (STAR): the agent may leave `p_n`
    before `t_f`. The original behaves this way (bug B8).
- `paper_claims` records what the paper itself states, when that differs from or adds to
  the verified value.
- Other problem-specific expectations use self-describing keys under `expected`. Examples
  are `subgraphs_fig4`, `composite_fig7`, `problem3_shortest_superstory_length`,
  `problem4_min_edits`, `problem2_by_interval_case` and `witness_regions`. The note named
  in the file's `notes` explains each one.

## Harness keys (golden cases)

Golden cases are the input of `tools/reference/Harness.java`, plus what it recorded. They
add the keys below to the case format above.

| Key | Meaning |
|---|---|
| `op` | harness operation: `validateAgentStory`, `validateAgentStoryMulti`, `getAgentStory`, `getAgentStoryStatuses`, `getSubGraph`, `getSubGraphMulti`, `getReachableSubgraph`, `game_dump`, `update_starting_vertex`, `builder_check`, `algorithms_test`, `applet` |
| `game` | one of the built-in games instead of `map`: `SingleInfeasible`, `SingleFeasible`, `MultiFeasible`, `MultiInfeasible`, `Basic` (story and history come from the game) |
| `game_mode` | provenance only: the mode the built-in game was written for |
| `start` | `"story"` calls `updateStartingVertex(story[0])` before the op, as the applet does; this is the default for `map` cases. `"fixed"` keeps `SV` joined to the first room; this is the default for `game` cases. |
| `builder` | `"generic"` forces the generic map builder for `star_fig2` (the default uses `getBasicGame()`) |
| `s` | start vertex name for the subgraph ops (unknown names become `null`) |
| `goals` | goal vertex names `vg` for the subgraph ops |
| `story_vertices` | the `storyVertices` argument; the default is `story.getVertexSetAsArray()` |
| `occupancy_active` | the active occupancy vertices for `getSubGraphMulti` |
| `vp_set` | the vertex set for `getReachableSubgraph`, inserted in order into a `HashSet` |
| `vertex` | the argument of `update_starting_vertex` (`null` allowed) |
| `name` | the `Algorithms` entry point for `algorithms_test`: `testGraphRoutines`, `testStoryHistory`, `testSingleAgent`, `testMultiAgent`, `main` |
| `steps` | for `applet`: a list of `{"run": {story?, sensors?, mode?}}`, `{"reset": true}` and `{"click": [px, py]}`, replayed against one applet instance. `story_text`, `sensor_text` and `applet_mode` describe a single run without `steps`. |
| `generator_kind` | provenance: `walk`, `perturbed_walk`, `random`, `malformed`, `session` |
| `paper_case` | `paper_cases.json` only: the paper fixture the case comes from, as `tests/fixtures/paper/<file>.json#<case id>[/<expected key path>]` |
| `crosscheck` | `paper_cases.json` only: the paper's claim, the fixture value, an independent oracle's verdict, and whether the original agrees (see `golden/README.md`) |

Map keys used only by the harness are `vertex_order`, `filler_occupancy` and `regions`, described under
"Maps". `tools/reference/README.md` gives the exact semantics of every op.

## Golden files

Each file has a `header`, the `maps` its cases use, and `cases`. Each case carries
`expected` (the recorded outputs) and `verified_by`. `golden/README.md` documents the
header fields and the `expected` keys:

- header fields: `kind`, `set`, `description`, `generator`, `generator_version`, `seed`,
  `set_seed`, `harness`, `harness_version`, `original_repo`, `original_commit`, `jdk`,
  `jvm_flags`, `jvm_input_arguments`, `isolation`, `order_dependence`,
  `stdout_trim_chars`, `num_cases`, plus `part` when a set is split and `crosscheck` in
  `paper_cases.json`;
- `expected` keys: `consistent`, `return`, `stdout`, `exception`, `aliases`,
  `graph_dump`, `graph_dump_exception`, `call_exception`, `order_dependent`,
  `order_dependent_paths`, plus `stdout_trim` when a trace had to be trimmed.

`golden/javahash.json` is the odd one out. Instead of cases it holds operation scripts on
`java.util.HashSet`, with the JDK's iteration order after every operation (see
`golden/README.md`).

## Parity files

`parity/*.json` pin the browser demo's engine (`docs/js/engine.js`, `docs/js/original.js`)
to the Python package. `tools/gen_parity.py` writes them (deterministic seeds, one case per
line); `tests/test_parity.py` regenerates them in memory and fails when a committed file is
stale, and `npm test` (`tests/js/*.test.js`, also run by pytest when Node.js >= 18 is on
PATH) replays every case through the JS engine.

Each case is `{"id", "fn", "args", "expect"}`: `fn` names a Python function (the list is in
`functions` and in `FUNCTIONS` in the generator), `args.map` names an entry of `maps`
(`Map.to_dict()` without geometry, so possibly in the pair form above), and `expect` is
`{"return": ...}` or `{"error": {"type", "message"}}`.

| File | Contents |
|---|---|
| `paper.json` | every paper case: Problem 1 (strict and unreported visits), `possible_positions`, Problems 2-4 |
| `random_single.json`, `random_multi.json` | seeded random cases per agent model: `validate`, `possible_positions`, `replay` of every witness, malformed histories, input errors |
| `problems.json` | seeded random Problems 2-4: all six interval cases, anchored and free super-stories, closest stories |
| `replay.json` | `replay` on mutated witnesses: `InvalidPath` messages |
| `inputs.json` | `parse_story`, `parse_history`, `history_to_string`, `check_history` |
| `maps.json` | `Map.from_dict`: canonical `to_dict`, `G`, adjacency, region graph, kinds; `MapError` messages |
| `names.json` | names that are special as JS object keys (`constructor`, `__proto__`, all digits) |
| `compat.json` | `compat="original"`: verdicts, `getAgentStory` paths, Java crashes; `javahash` scripts |
