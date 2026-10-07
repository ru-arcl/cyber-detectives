# Golden fixtures (recorded from the original Java code)

These files pin the behaviour of the original applet code
([arc-l/cyber-detective](https://github.com/arc-l/cyber-detective) at commit `55f57f8`) for
`compat="original"`, including its bugs and crashes. They were recorded by
`tools/reference/Harness.java` and are rebuilt end to end with
`tools/reference/regen_golden.sh`; do not edit them by hand. The values are what the
original code *does*, not what is *correct* (`verified_by` says so on every case).

| File | Cases | Content |
|---|---|---|
| `builtin.json` | 95 | the four `DetectiveGame` games (start `fixed` as in `Algorithms.test*`, and start `story` as in the applet), `Algorithms.test*`/`main`, `game_dump`, the builder check, the STAR examples from `../paper/star.json`, and reproductions of bugs B1–B7 from `docs/notes/original-inventory.md` |
| `star_random_single.json` | 960 | 320 random single-agent (story, history) pairs on STAR Fig. 2, each run through `validateAgentStory`, `getAgentStoryStatuses` and `getAgentStory` |
| `star_random_multi.json` | 320 | 320 random multi-agent pairs on STAR Fig. 2 (`validateAgentStoryMulti`) |
| `random_maps.json` | 440 | 40 random region-model maps with 3–6 rooms, 1–3 beams and 0–3 occupancy sensors: `game_dump`, single and multi validation, paths, statuses, and subgraphs |
| `subgraphs.json` | 317 | `getSubGraph`, `getSubGraphMulti` and `getReachableSubgraph` with random arguments, the `testGraphRoutines` calls, null arguments, and `updateStartingVertex` |
| `applet.json` | 481 | the applet's Run-Validation pipeline from raw text-field strings (single and multi mode, well-formed and malformed), multi-step sessions with Reset and mouse clicks, and a `__direct` companion per single-run case |
| `paper_cases.json` | 98 | every paper example the original can express (`../paper/star.json`, `../paper/icra.json`): Problem-1 verdicts, statuses and paths, and every subgraph the papers draw (STAR Figs. 4, 6(a), 6(b)/7; ICRA Fig. 4 and the `G_j`), each with a `crosscheck` block (see below and `docs/notes/phase1-crosscheck.md`) |
| `edge_cases.json` | 25 | inputs at the limits of Java `int`/`float` arithmetic: bug B7a (edge-id collisions) on three compact maps with 65,535 filler vertices (`filler_occupancy`), covering the NPEs at `Algorithms.java:207/356/430` and in `Edge.getEdgeId` via `hasEdgeBetweenVertices` (`:203/352/426`), a false-positive verdict, maps whose own edges collide, and subgraphs on them; and one applet session of clicks beyond 2^24 and the `int` range |
| `summary.json` | | statistics: crashes by exception, verdicts, order-dependent fields, applet vs. direct |
| `javahash.json` | 68 scripts | `java.util.HashSet` iteration order after each of 14,186 operations, recorded from the JDK by `tools/reference/JavaHashProbe.java`; pins `cyber_detectives.compat.javahash` (see below) |

## File layout

```json
{
"header": {"set": "...", "seed": 20101213, "set_seed": ..., "harness_version": "1.1.0",
           "original_commit": "55f57f8b...", "jdk": {...}, "jvm_flags": "...",
           "order_dependence": {"alternatives": [...]}, ...},
"maps": [ {map}, ... ],
"cases": [ {case}, ... ]
}
```

Maps follow `../README.md`, with three optional keys:

- `vertex_order` gives the creation (id) order of the non-`SV` vertices. The default is
  rooms, then beam sides, then occupancy sensors. `star_fig2` needs it because
  `getBasicGame` creates `b2l` before `b2r`.
- `regions` lists the generator's free regions, for provenance.
- `filler_occupancy` (only in `edge_cases.json`) adds N edgeless occupancy sensors
  `f1`…`fN` where `vertex_order` has `"..."` (see `../README.md`). It keeps a map with
  65,537 vertices a few hundred bytes long.

All case sets except `javahash` come from `tools/reference/gen_cases.py`. `paper_cases`
and `edge_cases` use no randomness, so their headers have `set_seed: null`.

The order of `edges` matters: it sets the order in which each vertex's neighbours are
inserted (see the harness's generic builder).

Cases follow `../README.md` and add the following:

- `op` names the harness operation. `tools/reference/README.md` lists the operations and
  their extra input fields (`game`, `start`, `s`, `goals`, `story_vertices`,
  `occupancy_active`, `vp_set`, `steps`, `vertex`, `name`).
- `start` controls where `SV` is attached:
  - `"story"`, the default for map cases, calls `updateStartingVertex(story[0])` before
    the op, as the applet does. With an empty story `SV` stays joined to the first room.
  - `"fixed"`, the default for `game` cases, leaves `SV` joined to `A`.
- `expected` holds the recorded outputs:
  - `consistent` is the boolean verdict, present for validate ops that returned;
  - `return` is the op's return value. Sets are sorted name lists; a graph is
    `{vertices, edges, ids, adjacency}`, all sorted; a status array `S[i][l]` is a list of
    sorted name lists. `null` when the op threw;
  - `stdout` is everything the original printed during the op (`dumpStatus`, `Graph.dump`,
    back-trace lines). For `applet` cases it is empty at the top level, and each step
    records its own `stdout`;
  - `exception` is `null`, or `{class, message, top_frame, origin_frame, trace}`. A crash is
    golden behaviour. Compare `class` and `origin_frame`; the message is JDK-specific;
  - `aliases`, `graph_dump`, `graph_dump_exception` and `call_exception` are op-specific
    extras (see the harness README);
  - `order_dependent` maps each recorded field to `true` when it changed under at least one
    alternative identity-hash setting. `order_dependent_paths` lists the exact leaf paths
    that changed;
  - `stdout_trim` would appear if a trace was over 60,000 characters and had to be trimmed
    (head + tail kept, with `sha256` of the full text). No current case needs it.
- `paper_cases.json` cases also carry:
  - `paper_case`, which names the paper fixture the case comes from
    (`tests/fixtures/paper/<file>.json#<case id>[/<expected key path>]`);
  - `crosscheck`, added by `tools/reference/paper_crosscheck.py`. For validate, path and
    status ops it holds `paper_states`, `fixture_consistent`, `oracle_consistent` and
    `oracle_consistent_if_story_may_end_before_tf` (from the independent region-model
    oracle `tools/reference/oracle.py`), `history_valid_for_single_agent` in single mode,
    `original`, `agrees`, and `who_is_wrong` when the original disagrees. A path op adds
    `path_valid_walk`, `path_ends_in_p_n` and `path_check`. Subgraph ops hold the paper's
    vertex and edge sets and whether the original matches them, up to the documented
    representation differences named in the block's `note`. The header gains a
    `crosscheck` description.

  `expected` is still exactly what the original does.

## `javahash.json`

`{"header": {...}, "scripts": [...]}`. Each script is
`{"id", "kind", "ops", "orders_sha1_12", "final_order"}`:

- `kind` is `obj` (distinct objects with identity `hashCode`/`equals`, like
  `projects.cyberDetective.Vertex`), `int` (`java.lang.Integer`) or `str`
  (`java.lang.String`);
- `ops` lists the operations: `"+K"` is `add(K)`, `"-K"` is `remove(K)`, and `null` is the
  null key;
- `orders_sha1_12[i]` is the first 12 hex digits of the SHA-1 of the set's iteration
  order after `ops[i]`, as the keys joined by one space.

The scripts exercise resizes, treeified bins (identity-hash objects that all share hash 1,
colliding `Integer` and `String` keys), tree removals and untreeify, tree splits and the
null key. The header records the JDK, the flags (`-XX:hashCode=2`) and the identity hash
the JVM returned (`1,1`).

## Determinism

- The fixtures were recorded with `-Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions
  -XX:hashCode=2 -XX:-OmitStackTraceInFastThrow` on Temurin JDK 8u504 (header `jvm_flags`).
  `-XX:-OmitStackTraceInFastThrow` keeps HotSpot from replacing a frequently thrown implicit
  `NullPointerException` by a preallocated one without a stack trace; adding it changed
  only the header flag fields (`jvm_flags`, `jvm_input_arguments`, the alternative
  `setting`s), every case record stayed byte-identical. Under `-XX:hashCode=2` every
  identity hash is 1, so a `HashSet<Vertex>` with at most
  10 members iterates in insertion order. At 11 or more members its single bin becomes a
  red-black tree: the order is still deterministic, but it is no longer insertion order.
  This happens in `random_maps` (`rand019`, `rand020`, `rand024`; vertices of degree 11
  and 12). `cyber_detectives.compat.javahash` emulates Java 8 `HashMap` exactly, tree bins
  included, and `javahash.json` checks it against the JDK.
- Each file runs in one JVM. `regen_golden.sh` checks that this gives the same results as
  one fresh JVM per case.
- `order_dependent: false` means no change was observed, not a proof of independence.
  Six alternative settings were tried (`hashCode=5` and `hashCode=3` with different
  `--burn-seed` offsets, listed in each header). These settings are deterministic on a
  given JDK build, so regeneration is byte-identical.
- The parts that are never order dependent are:
  - every boolean verdict;
  - every `getAgentStoryStatuses` set;
  - every subgraph's vertex and edge set;
  - every exception.

  The order-dependent parts are:
  - `dumpStatus` bracket order;
  - `Graph.dump` neighbour order and edge orientation;
  - the number of `GP.dump()` blocks before `validateAgentStoryMulti` returns true;
  - `getAgentStory` paths (B2), and with them the applet's result text;
  - the applet's "Reachable features" text.
