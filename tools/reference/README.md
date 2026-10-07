# Reference build of the original Java code

`build.sh` rebuilds the original Cyber Detectives applet code
([arc-l/cyber-detective](https://github.com/arc-l/cyber-detective), pinned at commit
`55f57f8b307047df615acdd2024140ad4030bbd4`) headlessly and reproducibly. We use it to
record the golden fixtures in `tests/fixtures/golden/`, which pin the behaviour of
`compat="original"`.

```bash
tools/reference/build.sh                  # cache in tools/reference/.cache (git-ignored)
CD_CACHE=/some/dir tools/reference/build.sh
source "${CD_CACHE:-tools/reference/.cache}/env.sh"   # CD_JAVA, CD_CLASSES_CORE, CD_JVM_FLAGS, ...
```

Requirements: bash, git, curl, tar, `sha256sum`/`sha1sum` (or `shasum`), and Linux x86-64
for the pinned JDK. On other platforms, set `CD_JAVA_HOME` to a local JDK 8.

## What it does

1. Clones the original repository at the pinned commit into `$CD_CACHE/original`. If you
   set `CD_ORIGINAL_DIR` to an existing checkout, the script uses that checkout instead. It
   must be at the pinned commit and have no local changes.
2. Downloads Eclipse Temurin JDK 8 and checks its SHA-256 against a value pinned in the
   script. The value matches the published `.sha256.txt`.
   - URL: <https://github.com/adoptium/temurin8-binaries/releases/download/jdk8u504-b01/OpenJDK8U-jdk_x64_linux_hotspot_8u504b01.tar.gz>
   - SHA-256: `9c70e102f527ac674ac2fe9c7d47b9a04e2d19842ba5ab8e9b33f368bbadfaea`
3. Downloads log4j 1.2.12, which only `common/util/FileHelper.java` (GUI/applet) uses. The
   script checks it against the pinned SHA-1 and SHA-256 and against Maven Central's
   published `.sha1`.
   - URL: <https://repo1.maven.org/maven2/log4j/log4j/1.2.12/log4j-1.2.12.jar>
   - SHA-1: `057b8740427ee6d7b0b60792751356cad17dc0d9`
   - SHA-256: `dc67378cf428c06408e7959e83bdc1518dd22ccd313e7c28a986612d65c276c7`
4. Compiles three times:
   - `classes/full`: all 25 source files, GUI included, with `-source 1.5 -target 1.5` and
     log4j on the classpath.
   - `classes/core`: the 11 files in `projects/cyberDetective/*.java` plus
     `common/util/IDGenerator.java`, with an empty classpath (no log4j).
   - `classes/core-compact1`: the same core files compiled against the `compact1` profile.
     That profile has no `java.awt`, `java.applet` or `javax.swing`, so this step proves the
     core never touches AWT.
5. Runs a headless smoke test. `Algorithms.main` (`testMultiAgent`) must print
   `Story inconsistent.` as its last line.
6. Writes `$CD_CACHE/env.sh`.

Every step is idempotent: the script re-downloads only files that are missing or fail
their checksum. It always recompiles, and javac output is byte-identical between runs.

### Notes on the compile level

- `-source 1.5` is the lowest level that works. `-source 1.4` rejects the generics.
- javac 8 compiles against JDK 8's `rt.jar`, not a Java 5 bootclasspath. It also accepts
  `@Override` on interface method implementations, which `CyberDetectiveDemoApplet` uses
  (anonymous `ActionListener`, `MouseListener`). A strict Java 5 javac rejects that, so
  the applet was most likely built with Java 6 or later.
- With `-Xlint:all`, javac reports only rawtypes/unchecked warnings, in `Algorithms.java`
  (generic array creation `new HashSet[...]`) and `Vertex.java` (raw `Collection`).

## Determinism: always run with `-XX:hashCode=2`

`Vertex` defines neither `equals` nor `hashCode`. The iteration order of every
`HashSet<Vertex>` (neighbour sets, search-status sets, story vertex sets) therefore
follows the JVM's identity hash codes. We tested 20 scenarios: the four `Algorithms.test*`
entry points, plus `validateAgentStory`, `validateAgentStoryMulti`, `getAgentStory` and
the applet's single-agent pipeline on each of the four `DetectiveGame` games. Each
scenario ran 25 times in a fresh JVM under 19 flag settings on three JVMs: Temurin 8u504,
Ubuntu OpenJDK 8u504 and Ubuntu OpenJDK 11.0.32. The results:

- **Order-independent output.** These parts were identical under every setting and every
  JVM, including the chaotic ones. They are safe to pin without a canonical setting:
  - every boolean verdict;
  - every `getAgentStory` path string and its `"<vertex> <j>"` back-trace lines;
  - the contents of every `search status` set, after sorting the members inside each
    `[...]`;
  - the `ArrayIndexOutOfBoundsException` that `getAgentStory` throws on infeasible
    histories (`Algorithms.java:365`). Pin the exception type and the line, not the
    message, because JDK 8 and JDK 11 word the message differently.
- **Order-dependent output.** These parts change with the hash setting:
  - the order of members inside `search status` brackets;
  - the neighbour order and edge orientation (`A--C` vs `C--A`) in `Graph.dump()`;
  - in `validateAgentStoryMulti` on feasible histories, the *number* of `GP.dump()`
    blocks printed before the early `return true` (35, 36 or 37 on `MultiFeasible`).
    This one is a control-flow effect, not just print order.
- **Defaults are stable but not portable.** With default flags (`hashCode=5`, a
  thread-local xor-shift), each JVM build gave byte-identical output across runs. The
  bytes still differed between Temurin 8 and Ubuntu 8 (same version), and on JDK 11
  between G1 (the default) and `-XX:+UseSerialGC`. `hashCode=3` (a global counter) also
  varies by JVM build and GC.
- **Unstable settings.** `hashCode=0` varied on every run. `hashCode=1`, `hashCode=4` and,
  on JDK 11, `-XX:-UseCompressedOops` varied from run to run when the machine was loaded.
- **`-XX:+UnlockExperimentalVMOptions -XX:hashCode=2` is portable.** It makes every
  identity hash code equal to 1. Output was byte-identical across all runs on all three
  JVMs, with or without `-Xint`, `-XX:+UseSerialGC` and `-XX:ActiveProcessorCount=2`. The
  only difference was the exception message text noted above.
  - With every hash equal, each `HashSet` holds all its members in one bucket, so it
    iterates in **insertion order**. That holds while a set has at most 10 members; at 11
    or more, the bin becomes a tree and the order is still deterministic but no longer
    insertion order. Every set in the STAR Fig. 2 map has at most 10 members; some random
    maps in `random_maps` have larger neighbour sets.
  - A port must therefore emulate Java 8 `HashMap`, tree bins included, as
    `cyber_detectives.compat.javahash` does. Insertion-ordered sets are enough only for
    sets with at most 10 members.

Golden fixtures must:

- record the order-dependent output under
  `-Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions -XX:hashCode=2
  -XX:-OmitStackTraceInFastThrow` (this is `CD_JVM_FLAGS` in `env.sh`; the last flag keeps
  exception traces complete, see "JVM flags" below);
- say so in the fixture;
- start each scenario in its own fresh JVM.

## Harness and golden fixtures

| File | Purpose |
|---|---|
| `Harness.java` | Drives the original classes headlessly and records return values, stdout and exceptions as JSON. It has Java 1.5 source level and a built-in minimal JSON reader/writer. |
| `run.sh` | `run.sh cases.json > results.json`. Builds via `build.sh`, compiles the harness into `$CD_CACHE/classes/harness`, then runs it with `CD_JVM_FLAGS`. Extra arguments go to the harness (`--meta`, `--burn-seed N`, `--force-generic`). `CD_SKIP_BUILD=1` reuses an existing build; `CD_HARNESS_JVM_FLAGS` overrides the flags. |
| `gen_cases.py` | Deterministic case generator (`--out DIR [--seed N]`). Writes `DIR/<set>.cases.json` for the eight sets in `tests/fixtures/golden/README.md` (`paper_cases` is built from `tests/fixtures/paper/`; it and `edge_cases` use no randomness). |
| `make_golden.py` | Merges the canonical and alternative runs into `tests/fixtures/golden/*.json` plus `summary.json`. With `--paper-crosscheck REPO` it also adds the `crosscheck` block to `paper_cases.json`. |
| `paper_crosscheck.py` | Adds the per-case `crosscheck` block to `paper_cases.json`: paper claim vs. fixture vs. oracle vs. original (`docs/notes/phase1-crosscheck.md`, Appendix C). |
| `oracle.py` | The independent region-model oracle the cross-check uses: a BFS over (place, story index, history index), with no graph `G` and no subgraphs (Appendix B). |
| `JavaHashProbe.java`, `javahash_probe.py` | Record `java.util.HashSet` iteration orders from the JDK into `tests/fixtures/golden/javahash.json` (`--out FILE`). `--check` compares them live with `cyber_detectives.compat.javahash`. |
| `regen_golden.sh` | Runs everything end to end, including the self-checks below. Takes about 25 s on a warm cache. |

### Harness input

Input is a JSON array of cases, or `{"maps": [...], "cases": [...]}`. Each case follows
`tests/fixtures/README.md` and adds an `op`. Graph setup mirrors the original:

- **Map cases** (`"map": name or inline map`):
  - `star_fig2` uses `DetectiveGame.getBasicGame()` itself.
  - Any other map goes through `Harness.buildGame`, which replays `getBasicGame` step by
    step (`DetectiveGame.java:38-181`):
    - a fresh `IDGenerator` whose first id is discarded;
    - `SV` first, then the vertices in `vertex_order`;
    - `assoVertex` and the id sets;
    - `vertexIds`, then `vertexMap`, then `vertexNameMap`, each in the order SV, rooms, beam
      sides, occupancy;
    - `addNeighbor` calls vertex by vertex in id order. `SV` is joined to the first room and
      is that room's first neighbour. All other neighbours follow the order of `edges`;
    - edges built by iterating `vertexMap.values()`.

    The builder keeps its neighbour lists in a `HashMap` keyed by vertex *name*: keyed by
    `Vertex`, every vertex would share one bin under `hashCode=2`, which is quadratic on the
    65,537-vertex maps of `edge_cases`. It still requests every identity hash in id order,
    as a `HashMap<Vertex, ...>` did, so the alternative hash settings see the same sequence
    (the regenerated fixtures were byte-identical).

    The compact key `"filler_occupancy": N` (see `tests/fixtures/README.md`) is expanded
    before anything else (`Harness.expandMap`, `compat.original.expand_map`): N edgeless
    occupancy sensors `f1`…`fN`, placed where `vertex_order` has `"..."`. With N = 65,535,
    the vertices after the fillers get ids above 65535, where `Edge.getEdgeId` collides
    (bug B7a).

    The STAR map in `gen_cases.py`'s `STAR` has an edge order and a `vertex_order` that
    make this builder reproduce `getBasicGame()` exactly. The `builder_check` op checks the
    structure. `regen_golden.sh` reruns every case with `--force-generic` and requires
    identical output.
  - The story is resolved with `vertexNameMap.get` (unknown names give `null`, as in the
    applet). The history uses one `Sensor` object per sensor name. Beams are
    `BeamDetector(name, {side0, side1})` and occupancy sensors are `OccupancySensor(v)`.
  - `start` defaults to `"story"`, which calls `updateStartingVertex(story[0])` as
    `CyberDetectiveDemoApplet.java:235/265` does. Set it to `"fixed"` to keep SV–(first room).
- **Game cases** (`"game": SingleInfeasible|SingleFeasible|MultiFeasible|MultiInfeasible|Basic`)
  use `DetectiveGame.get*Game()`. Their `start` defaults to `"fixed"`, as `Algorithms.test*`.

| op | extra inputs | `return` (plus extras) |
|---|---|---|
| `validateAgentStory`, `validateAgentStoryMulti` | | boolean |
| `getAgentStory` | | path string |
| `getAgentStoryStatuses` | | `S[i][l]` as lists of sorted names. `aliases` lists the `[j, i]` pairs where `S[i]` *is* `S[j]` (the deactivation aliasing). |
| `getSubGraph` | `s`, `goals`, `story_vertices` (default `story.getVertexSetAsArray()`) | graph `{vertices, edges, ids, adjacency}`. `graph_dump` holds the `Graph.dump()` text. |
| `getSubGraphMulti` | as above plus `occupancy_active` | graph and `graph_dump` |
| `getReachableSubgraph` | `s`, `vp_set` (inserted in order into a `HashSet`), `goals` | graph and `graph_dump` |
| `game_dump` | | prints `Graph.dump()`, `Story.dump()` and `ObservationHistory.dump()`. Returns the graph, story, history and construction `structure` (vertexMap/edgeMap order, ids, assoVertex, id sets, neighbour iteration order). |
| `update_starting_vertex` | `vertex` (name or `null`) | the game after the call. `call_exception` records a throw (a `null` vertex corrupts SV). |
| `builder_check` | `map` | whether the generic builder's structure equals `getBasicGame()`'s |
| `algorithms_test` | `name`: `testGraphRoutines`, `testStoryHistory`, `testSingleAgent`, `testMultiAgent` or `main` | `null`; the output is in stdout |
| `applet` | `steps`: `{"run": {story?, sensors?, mode?}}`, `{"reset": true}` or `{"click": [px, py]}` | one record per step |

The `applet` op re-implements the non-drawing logic of `CyberDetectiveDemoApplet`,
because that class needs a display. It follows the code line by line, with line numbers
cited in the harness:

- the Run button (l.192-272): per-character story lookup, `split(",")`, exact token
  matching, the single-mode o-pair or multi-mode toggle, `updateStartingVertex`, the
  verdict, the path, then the clears, which a throw skips;
- Reset (l.182-190), `startSimulation` (l.281-288) and `mouseClicked` (l.291-332).

Hit testing uses the original `Environment` class, created once from the initial game as
`appInit` does, so clicks after Reset return the old vertices. Steps run against one
applet instance, so stale state carries over. Each step records `parsed_story`,
`parsed_history`, `validate`, `path`, `result_text`, `game_story_after`,
`game_history_after`, its own `stdout` and its own `exception`. Click steps record `x`,
`y`, `hit`, `accepted`, both text fields and the `instructions` text. The click
coordinates are read with `((Number) v).intValue()`: the JSON reader makes integers `Long`
(low 32 bits kept) and numbers with `.`/`e` `Double` (saturating cast). `mouseClicked` then
computes `(int)(px * 800f / 400)` in `float` arithmetic, so pixels beyond 2^24 are rounded
(`edge_cases`, case `applet_click_limits`).

### Harness output

Every result carries `id`, `op`, `return`, `stdout` (the op's captured `System.out`),
`exception` and any op extras. `exception` is `{class, message, top_frame, origin_frame,
trace}`, where `trace` stops at the first harness frame. Cases are isolated: each builds a
fresh game, every `Throwable` is caught, and `System.out`/`System.err` are redirected per
case. A malformed case yields `harness_error`, and `make_golden.py` refuses to write
output in that case.

### Self-checks in `regen_golden.sh`

1. **Fresh JVM per case.** Every `builtin` case and the first 40 cases of each other set
   run once more, each in its own JVM, and must match the batch run byte for byte. This
   holds because identity hashes do not depend on history under `hashCode=2`.
2. **`--burn-seed`.** A burn seed must leave the canonical output unchanged.
3. **Generic builder.** Rebuilding every map case with the generic builder must reproduce
   the `getBasicGame()` results.
4. **Order dependence.** Six alternative identity-hash runs (`hashCode=5` and `hashCode=3`
   with `-XX:+UseSerialGC -XX:ActiveProcessorCount=1` and different burn seeds) set
   `order_dependent`. On one JDK build these runs are reproducible from run to run, so two
   regenerations are byte-identical.

### Observations from the golden run (to be read with `docs/notes/original-inventory.md`)

- **B3 shows up under the canonical setting.** `-XX:hashCode=2` (insertion order) does not
  avoid it. 24 recorded paths have more `[..]` crossings than non-deactivation
  recordings: 19 direct `getAgentStory` cases and 5 applet runs. The list is in
  `summary.json` (`paths_with_wrong_number_of_crossings_B3`). Examples:
  - `star_random_single` case `s0047`: story `CAC`, history `o1A,b1A,o1D,b1A` gives
    `C[o1][b1d][b1u][b1d]AC`, four crossings for three recordings;
  - applet story `A`, sensors `b1` twelve times, gives 13 crossings.

  The inventory's `AC` + `b1,b1` example happens to be correct under insertion order.
- **B2 is confirmed.** The path returned by `getAgentStory` changed under an alternative
  hash setting in 89 cases, `repro_B2_path_A_b1` among them. The applet's path and result
  text change with it.
- **Order-independent results.** Verdicts, status sets, subgraph contents and exceptions
  never changed under any alternative setting.
- **Crashes.** Every `getAgentStory`/`getAgentStoryStatuses` call on an inconsistent input
  throws AIOOBE at `Algorithms.java:365` (B4). A `null` story room throws an NPE at
  `Algorithms.java:207` (single) or `:430` (multi), or at `DetectiveGame.java:30` when it
  is the first room. After `DA`, the corrupted SV makes later runs throw at
  `DetectiveGame.java:23` (B6). After an edge-id collision (B7a, `edge_cases`) the current
  vertex `s = GP.vertexMap.get(p[l].id)` can be null: the next story test throws at
  `Algorithms.java:207/356/430`, a pending recording's `hasEdgeBetweenVertices(v, s)` at
  `Edge.getEdgeId(Edge.java:17)` (via `Graph.java:26`, `Algorithms.java:203/352/426`), and
  when the story ends there the verdict is `true`.

## Live differential test (`differential.py`)

`differential.py` checks `compat="original"` against the real Java original on fresh,
seeded random inputs rather than on the golden ones. It runs each case through `run.sh` and
through `cyber_detectives.compat.original.run_harness_case`, then compares every field:

- `return` and `stdout`;
- `exception`: class, message, top and origin frame, and the full trace;
- `aliases`, `graph_dump`, `graph_dump_exception` and `call_exception`;
- for applet cases, every step record.

Validate cases that use the applet start are also replayed through `validate_compat`, which
checks the verdict, the path, any crash and the stdout.

```bash
# reuse an existing JDK 8 and original checkout instead of downloading them
export CD_CACHE=/some/cache CD_JAVA_HOME=/path/to/jdk8 CD_ORIGINAL_DIR=/path/to/original
python3 tools/reference/differential.py                       # >= 5000 cases, 4 JVMs
python3 tools/reference/differential.py --n 30000 --seed 2 --jobs 8 --out DIR
python3 tools/reference/differential.py --only 594 1013       # regenerate single units
python3 tools/reference/differential.py --cases DIR/repro_00.json   # replay a repro
```

### Cases

Every unit `i` draws its inputs from `random.Random(seed * 10**7 + i)`. The default seed is
20261006, a range the golden fixtures do not use. A batch depends only on `(seed, n)`, not
on `PYTHONHASHSEED`. The mix:

- STAR Fig. 2 single-agent and multi-agent pairs, built from walks, perturbed walks and
  noise. These include unknown story rooms and `start: "fixed"`.
- Random small maps.
- Big maps, where some vertex has 11 or more neighbours. Under `hashCode=2` such a
  neighbour set becomes a treeified bin. The run counts cases that reach the tree-bin code
  (`cases with tree bins`).
- The three subgraph ops, `game_dump` and `update_starting_vertex`, including `null` and
  unknown names.
- Applet runs on walk-derived text and on malformed text.
- Multi-step applet sessions that mix Run (partial field updates, mode switches), Reset and
  clicks, a few of them at coordinates beyond 2^24, the `int` range, or as JSON doubles.
- Edge-id collision maps (bug B7a, about 1% of the units): 65,535 filler vertices
  (`filler_occupancy`) between a small core and a few vertices with ids above 65535,
  random edges, stories and histories, run through the four validation ops.

### Results and exit status

The work directory (`--out`, by default a temporary directory) holds `cases.json`,
`java.json` and `repro_*.json`. The script prints one minimised repro for each distinct
mismatch kind, up to `--max-repros`. It shrinks a case by deleting history events, story
rooms, set members, applet steps, characters, tokens and map edges for as long as the
mismatch persists.

The exit status is 0 when everything matches, 1 on any mismatch, and 2 on a setup or
harness error.

### JVM flags

The script runs the harness with the canonical flags `CD_JVM_FLAGS`, which include
`-XX:-OmitStackTraceInFastThrow` (override with `CD_HARNESS_JVM_FLAGS`; `run.sh` and
`regen_golden.sh` add the flag to an `env.sh` written by an older `build.sh`). Without that
flag, HotSpot changes what the trace
records once the JIT has compiled a method that keeps throwing an implicit NPE at the same
site. That happens after a few hundred null-room cases in one JVM. From then on, HotSpot
throws a preallocated `NullPointerException` with an **empty** stack trace. `top_frame`,
`origin_frame` and `trace` then depend on which cases ran earlier in the JVM and on JIT
timing.

On a 6,000-case batch, every one of the 37–49 mismatches seen without the flag was such an
empty-trace NPE. The golden files never contained empty traces, because their batches are
too small to trigger it: regenerating them with the flag (also added to the alternative
settings) changed only the header flag fields, and every case record stayed byte-identical.
The flag leaves identity hashes unchanged (all 1).

`tests/test_differential_live.py` runs a 200-case batch (times `CD_TEST_SCALE`) when a
reference build exists in `$CD_CACHE` or `CD_JAVA_HOME` is set, and skips otherwise. It also
checks the generator without a JDK.
