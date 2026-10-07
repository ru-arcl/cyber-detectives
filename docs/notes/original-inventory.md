# Inventory of the original Java code (non-GUI logic)

> **The final bug list is `original-bugs.md`.** It supersedes §6 (suspected bugs) and §7
> (confirmed by harness) of this note: it adds B9 and B10, gives every bug its golden counts
> and default-mode treatment, and corrects the B7a reproduction. The control used in §7
> (story `B` on a small map, "true") is itself an instance of B8, because x must re-enter
> `B`; story `BB` is the clean reproduction. The rest of this note (§1–§5) remains the
> function-by-function specification of `compat="original"`.

This note lists every non-drawing function of the original applet
(`arc-l/cyber-detective`, HEAD 55f57f8, `cyber-detective-source/`). For each one it gives
the signature, what it computes, what it prints, which arguments it mutates, its cost,
and its quirks. It is the specification for `compat="original"` in our port.

Notation: `n` = story length, `m` = number of sensor recordings, `V`/`E` = vertices/edges
of the connectivity graph `G`, `Vs` = sensor vertices. "Canonical vertex" means the
`Vertex` object stored in the game graph `g`. Subgraphs hold *copies* of it.

**How we checked this.** We read the code; we did not compile or run Java (the golden
harness in `tools/reference/` will confirm it). Every claim marked **[emu]** was checked
with a line-by-line Python emulation of `Algorithms.java`. We compared that emulation
against a brute-force search over the region model of STAR Fig. 2/3(b): free regions
R1={A,C,b1d,o1}, R2={A,b1u,b2l,o1}, R3={o1,o2}, R4={b2r,o2,B}, whose cliques give exactly
the 15 non-`SV` edges of `getBasicGame`. The search covered:

- every story over {A,B,C} of length 1–4;
- every history the applet can produce with at most 4 tokens (single mode) or 5 tokens
  (multi mode);
- every raw event sequence with at most 4 events.

Claims marked **[harness]** are hypotheses for the Java harness to confirm.

---

## 1. Data structures (`projects/cyberDetective/`)

### Vertex.java
| Function | Behaviour / quirks |
|---|---|
| `Vertex()` / `Vertex(String name, int id)` | Fields: `name`, `id`, `neighbors` (a `HashSet<Vertex>`), and `assoVertex` (the opposite beam side; `null` otherwise, and only set in `getBasicGame`). |
| `boolean isNeighbor(Vertex v)` (l.25) | `neighbors.contains(v)`. `Vertex` has no `equals` or `hashCode`, so this is an **identity** test: a copy of a neighbour is not a neighbour. |
| `void addNeighbor(Vertex)`, `addNeighbors(Collection)` (raw type), `removeNeighbor(Vertex)` | Mutate `this.neighbors` only, never the other side. Symmetry is the caller's job. |
| `Vertex getCopy()` (l.41) | New object with the same `id` and `name`. The copy has **no neighbours and no `assoVertex`**. |

**Iteration order.** Every `HashSet<Vertex>` iterates by identity hash code, which is
random per JVM run. Iteration is insertion-ordered only when buckets collide. Section 5
lists which outputs depend on this.

### Edge.java
| Function | Behaviour / quirks |
|---|---|
| `Edge(Vertex v1, Vertex v2)` | Keeps the orientation `vertices = {v1, v2}` and sets `id = getEdgeId(v1, v2)`. |
| `static int getEdgeId(Vertex,Vertex)` / `(int,int)` | `min*65536 + max`. This is injective only while every id is below 65536. Example collision: `(1,65539)` and `(2,3)` both give 131075. From `min ≥ 32768` the result overflows `int` but stays injective modulo 2^32. The ids 2..11 used by the original never collide. A self-pair `(v,v)` gets a valid-looking id, `v*65537`. |
| `Edge getCopy()` | Copies both vertices with `Vertex.getCopy` (so neighbours and `assoVertex` are dropped) and keeps the orientation. |

### Graph.java
Fields: `vertexIds` (`HashSet<Integer>`), `vertexMap` (`HashMap<Integer,Vertex>`),
`vertexNameMap` (`HashMap<String,Vertex>`), `edgeIds`, `edgeMap` (`HashMap<Integer,Edge>`).

| Function | Behaviour / quirks |
|---|---|
| `hasEdgeBetweenVertices(Vertex,Vertex)` / `(int,int)` (l.24, l.34) | O(1) lookup of `getEdgeId` in `edgeMap`. Does not check that the vertices belong to this graph. |
| `getEdgeBetweenVertices(...)` (l.29, l.39) | Returns `null` when the edge is absent. With colliding ids it can return a **different** edge. |
| `addEdgelessVertex(Vertex v)` (l.48) | Stores `v` itself (aliasing). A vertex with an existing id or name silently overwrites the map entry, and no neighbours are merged. |
| `addCopyOfEdgelessVertex(Vertex v)` (l.55) | Adds `v.getCopy()`. |
| `addEdge(Edge ed)` (l.60) | A no-op if `ed.id` is already present, even when `ed` carries different vertex objects. Otherwise, for each endpoint: if the id is new, the graph **adopts the caller's `Vertex` object**; if the id exists, it **rewrites `ed.vertices[i]`** to the graph's own object. It then updates both neighbour sets and stores `ed` itself. **Mutates its argument.** |
| `addCopyOfEdge(Edge e)` (l.89) | `addEdge(e.getCopy())`. Subgraphs therefore never share `Vertex` objects with `g`. |
| `dump()` (l.97) | **stdout.** Prints an empty line, then `vertices: `. For each vertex in `vertexMap` order it prints `name: n1 n2 ` (neighbours in identity-hash order). Then `edges: ` and `v0--v1  ` per edge in `edgeMap` order, with a newline after every 5th edge (the `i>0` test is redundant), then a final newline. |

Order notes: `HashMap<Integer>` iteration is deterministic for a given JDK. On Java 8+ the
small vertex ids come out in ascending order. Edge keys are large, so their bucket depends
on the JDK's hash spreading: Java 1.5–7 and Java 8+ give different `edgeMap` orders.
Neighbour order is not deterministic.

### Story.java, ObservationHistory.java, SensorRecording.java, Sensor.java, BeamDetector.java, OccupancySensor.java
| Function | Behaviour / quirks |
|---|---|
| `Story.addVertex(Vertex)` | Appends to a `Vector`. `null` is accepted, and the applet does pass it (section 4). |
| `Story.getStoryAsArray()` | The story as given, repeats kept. |
| `Story.getVertexSetAsArray()` | De-duplicates by identity. The order is identity-hash dependent but only used as a set. Called once per (phase, state) by the algorithms. |
| `Story.dump()` | **stdout** `story: A C B A C ` followed by a newline. |
| `ObservationHistory.addSensorRecording`, `getOHAsArray()` | `Vector` wrapper. |
| `ObservationHistory.dump()` | **stdout** `observation history: b1[A]o1[A]o1[D]...` (no separators) followed by a newline. |
| `SensorRecording(Sensor, int event)` | `ACTIVATION=1`, `DEACTIVATION=2`. Nothing is validated. A beam recording with `DEACTIVATION` is treated as a **crossing**, because only occupancy deactivations are skipped. |
| `Sensor.getGraphVertices()` | Returns the internal `sensorVertices` array, aliased. |
| `BeamDetector(String name, Vertex[] sv)` | Keeps `sv` as passed. `sv[0]` matters to `flip`, which compares by name. |
| `OccupancySensor(Vertex sv)` | `name = sv.name`, `sensorVertices = {sv}`. |

### DetectiveGame.java
| Function | Behaviour / quirks |
|---|---|
| `static DetectiveGame getBasicGame()` (l.38) | Hard-codes the STAR Fig. 2 graph plus a virtual start vertex `SV` joined to `A`. A fresh `IDGenerator` is used and its first id is discarded, so `SV=2, A=3, B=4, C=5, b1u=6, b1d=7, b2l=8, b2r=9, o1=10, o2=11`. Neighbour lists are written by hand (symmetric; 16 edges). Edges are then built by iterating `vertexMap` (ascending id) and each neighbour set. Every edge is therefore oriented smaller-id first, and `Graph.dump` edge strings are deterministic. Also sets `assoVertex` pairs (b1u↔b1d, b2r↔b2l), `roomIds`, `beamIds`, `occuIds`, `storyVertices=[SV,A,B,C]`, and an empty story and history. Beam vertex order: `b1 = {b1u, b1d}`, `b2 = {b2r, b2l}`. |
| `getSingleInfeasibleGame()` (l.183) | Story ACBAC; history `b1 o1A o1D b2 o2A o2D` (STAR eq. 2). Code → false; brute force → false. **[emu]** |
| `getSingleFeasibleGame()` (l.208) | Story ACBAC; history `b1 o1A o1D o2A o2D b2`. Code → true; brute force → true. **[emu]** `getAgentStory` gives `A[b1u]C[o1][o2]B[b2r]AC`, which is deterministic because every bucket has one element. **[emu]** |
| `getMultiFeasibleGame()` (l.233) | Story ACBAC; history `b1 o1A o2A b2 o2D o1D` (STAR eq. 3). Multi → true. **[emu]** |
| `getMultiInfeasibleGame()` (l.258) | Story ACBAC; history `b1 o2A o2D o1A b2 o1D`. Multi → false. **[emu]** Used by `Algorithms.main`. |
| `void updateStartingVertex(Vertex v)` (l.20) | **Mutates the game graph.** It takes the *first* neighbour of `SV` in iteration order (assumes exactly one), removes that edge from both neighbour sets and from `edgeIds`/`edgeMap`, then adds edge `SV–v`. Throws `ArrayIndexOutOfBoundsException` if `SV` has no neighbours. With `v == null` it throws a `NullPointerException` **after** `SV.addNeighbor(null)` has run: the old edge is gone and `SV.neighbors = {null}`, so every later call throws an NPE (on `svn.neighbors`). The game stays corrupt until Reset. |

### common/util
| Function | Behaviour / quirks |
|---|---|
| `IDGenerator.getNextId()` | `synchronized`, pre-increment, so the first id is 1. |
| `FileHelper.AppletInit(Applet)` | `baseUrl = protocol://host[:port]path` of the code base. |
| `FileHelper.regularInit()` | `baseUrl` = code-source location, with the jar file name stripped when it ends in `.jar`. |
| `getBaseUrl()` / `setBaseUrl(String)` | Static global state. |
| `getBufferedReaderFromStream(InputStream)` | Wraps the stream in a reader with the platform charset. |
| `getStreamFromUrl(file)` / `(base, file)` | `new URL(base + file)` (string concatenation) behind a 10 KiB buffered stream. On any exception: log4j `error`, return `null`. |
| `loadProfile(String path)` / `(InputStream)` | Returns `Properties`, which are **empty** on failure (log4j error). The `FileInputStream` is never closed. |
| `loadUrlProfile(base, file)` / `(file)` | Like `loadProfile` but from a URL. Also prints a stack trace to stderr. |
| `writeBufferToFile(name, content)` | Overwrites the file. Throws `IOException`, and the writer leaks on error. |
| `appendBufferToFile(name, content)` | Appends the content plus the platform newline. |
| `copyUrlBinaryFile(name, srcUrl, destPath)` | Returns silently if the destination exists. Streams are not closed on error; stack traces go to stderr. |
| `readPropertiesFromFile(name)` | Empty `Properties` for a directory, `null` on `IOException`. The stream is not closed. |

The only callers are `AwtApplet.init()` (`AppletInit`, `getBaseUrl`, which prints
`Using <url> as remote base URL`) and a commented-out block in `Geometry`. This file is
the only reason the code needs log4j 1.2.12. None of it affects validation.

---

## 2. Algorithms.java

### Helpers
| Function | Behaviour |
|---|---|
| `private isDeactivation(sr)` / `isActivation(sr)` (l.111/118) | True only for **occupancy** recordings with that event. |
| `private Vertex flip(Graph g, Vertex v, Sensor r)` (l.125) | For occupancy, the canonical `g` vertex with `v.id` (the agent is inside `o`). For a beam, the canonical *other* side, chosen by **name** comparison with `sensorVertices[0]`; a `v` that is neither side maps to `sensorVertices[0]`. A state therefore means "the side the agent ends on after crossing". |
| `private dumpStatus(Vertex[] p, Set<Vertex>[] status)` (l.139) | **stdout** `search status: [x,y]A[..]C...`. It prints `status[0]` in brackets if non-empty, then for each `i` `p[i].name` followed by `status[i+1]` in brackets if non-empty. **The order inside brackets is identity-hash dependent.** |
| `private areNeighbors(Graph g, int v1, int v2)` (l.162) | Edge in `g`, **or `v1 == v2`**. A story that repeats a room back to back (`AA`) therefore means "stay", which matches exact-match semantics because A can be left and re-entered silently. |

### `getReachableSubgraph(Graph g, Vertex s, Set vpSet, Vertex[] vg)` (l.48) ↔ STAR Alg. 2
1. Copies `s` into `tempGraph` and `subGraph`. Copies every `g` edge whose two endpoints are
   in `vpSet`. `vpSet.contains` is an **identity** test, so callers must pass canonical
   vertices. They do; `null` entries are harmless.
2. BFS from `s.id`. Vertices are marked visited on **dequeue**, so a vertex can be queued
   (and expanded) several times. The edge copy is guarded by `hasEdge`, which keeps the
   result correct, but the cost is O(Σ deg²) instead of O(V+E). The
   `tempGraph.hasEdgeBetweenVertices(cv, ns[i])` test is always true.
3. Goal step. For each goal `vg[i]` and each vertex in a snapshot of the subgraph's
   vertices, if the canonical `g` vertex `isNeighbor(vg[i])`, the step copies
   `g.getEdgeBetweenVertices(...)` from the **full graph `g`**. If the neighbour sets and
   `edgeMap` disagree this would be an NPE inside `getCopy`. That cannot happen with the
   original data: `getBasicGame` and `updateStartingVertex` keep them consistent. A goal
   with no neighbour in the subgraph is simply missing; the paper adds every `VG`
   (line 11).
- **Output:** a new `Graph` of copies; nothing is printed (the `dump()` calls are commented
  out). **Mutations:** none to `g`. **Cost:** O(E + Σdeg² + |vg|·V).
- `vertexNameMap` in subgraphs is filled by name and could collide on duplicate names; it
  is never read.

### `getSubGraph(Graph g, Vertex s, Vertex[] storyVertices, Vertex[] vg)` (l.9) ↔ STAR Alg. 1
`VC = set(storyVertices) ∪ {s}`. Deviation: the paper uses all of `Cp`; the code uses only
the rooms in the story. This is the stricter choice and is correct for exact matching.

**Single-agent finding [emu].** In single-agent mode the subgraph is pure overhead. The
callers only ever test adjacency `(p[l], s)` and `(goal, s)`, and in GP those pairs are
adjacent exactly when they are adjacent in `g`. Replacing GP by `g` changed none of the
40 920 test cases.

### `getSubGraphMulti(Graph g, Vertex s, Vertex[] occuSensors, Vertex[] storyVertices, Vertex[] vg)` (l.20) ↔ STAR Alg. 4
`VC = story ∪ O ∪ {s}`. After `getReachableSubgraph`, each active `o` present in `gp` gets
edges between every pair of its *current* gp neighbours. Neighbours include goal vertices
and edges added by earlier contractions, so chains of active sensors close transitively.
We believe the final edge set does not depend on `O`'s iteration order.

Deviations from Alg. 4:
- `o` is **not removed** (line 5). Keeping it is needed: on an activation the goal is `o`
  itself, and removing `o` changes 727 of 13 299 multi results **[emu]**.
- The contraction creates edges with `new Edge(ns[j], ns[k])` using gp's objects. Their
  orientation is iteration-order dependent, which shows up in `GP.dump()`.

### `boolean validateAgentStory(Graph g, Vertex sv, Story story, ObservationHistory obHis)` (l.167) ↔ STAR Alg. 3 / ICRA Alg. 1
DP in transposed form. The outer loop runs over recordings `i = 0..m` (`m` = the final
phase); the inner index is the story position `l`. `S[l]` is the set of canonical vertices
the agent can be at right after the previous non-deactivation recording, having consumed
`p[0..l-1]`. Initially `S[0] = {sv}`.

For each phase:
- Occupancy deactivations are skipped (`continue`). Otherwise `Vg` is the recording's
  sensor vertices, or empty for the final phase.
- For each state `(s, j)`, GP is `getSubGraph(g, s, story, Vg)` and the code runs a
  **deterministic walk along the story**: for `l = j..n`:
  - if this is the final phase and `l == n`, **return true**;
  - for each goal `v` with `edge(v,s) || v == s`, add `flip(v)` to `SP[l]`;
  - if `l < n` and `areNeighbors(p[l], s)`, set `s = p[l]`; otherwise stop.
- Then `S = SP`, and `dumpStatus` prints S (one **stdout** line per non-deactivation
  phase, the final phase excluded).
- Returns false if no state reaches `l = n` in the final phase.

Because the next room is fixed by the story, the walk is not a greedy approximation.

The paper's `V_I ← {p1}` becomes a virtual start `sv` joined only to `p1`. That only
matches the paper when the caller has attached SV to `p[0]`: the applet does, through
`updateStartingVertex`; the `DetectiveGame` tests hard-wire `A`.

- **Boolean result:** independent of iteration order (a union of sets). The `dumpStatus`
  text is not.
- **Mutations:** none. **Cost:** at most 2(n+1) states per phase, since all states come
  from one sensor. Each state costs one subgraph build plus O(n·|Vg|), so the total is
  O(m·n·(E+Σdeg²+n)). The paper's bound is O(m(n+n_w) lg n_w).
- **Agreement with brute force [emu]:** 0 differences in 40 920 applet-style
  single-agent cases.

Edge cases:
- **Empty history:** true iff `p[0]` is adjacent to SV and consecutive rooms are adjacent
  or equal. Correct.
- **Empty story:** the history must be empty to get true; any non-deactivation recording
  empties S, giving false. The paper requires `p1`, so this is ill-defined. The applet
  blocks empty stories.
- **First room not adjacent to SV** (e.g. `DetectiveGame` with SV–A and story `CA`):
  always false, although "start in C" makes `CA` valid. Only reachable outside the
  applet.
- **Inputs that violate the single-agent assumptions** are accepted: deactivations are
  ignored wholesale, and pairing is not checked. See B5.

### `Set<Vertex>[][] getAgentStoryStatuses(g, sv, story, obHis)` (l.315)
Same DP as `validateAgentStory`, but it keeps all phases in `S[i][l]`. On a deactivation it
sets **`S[i+1] = S[i]` (the same array of sets)**. This aliasing is harmless: a
deactivation phase writes nothing, and later phases write only into fresh `S[k+1]`.
**stdout:** `dumpStatus(p, S[i+1])` per non-deactivation phase.

Bug: the documented `return null` is **unreachable**. When no state reaches the end, the
final phase falls through to `dumpStatus(p, S[m+1])` and throws
**`ArrayIndexOutOfBoundsException`** (B4). Mutations: none.

### `String getAgentStory(g, sv, story, obHis)` (l.226) — path backtracking (STAR §4 / ICRA §III.A, prose only)
1. Calls `getAgentStoryStatuses`, so its prints come first.
2. Final phase `i = m`: scans `j = 0..n` and each `S[m][j]` in iteration order. The first
   state whose walk reaches `n` sets `J = j` and `last = state`. It pushes
   `(j, assoVertex ?: state)` and prints **stdout** `"<name> <j>"`. For beams the pushed
   vertex is the approach side; for occupancy, `o`.
3. Phases `i = m-1..0`, skipping deactivations: for **every** state `x` in `S[i][j]`, walk
   from `j`. When `l == J` and some goal gives `flip(v).id == last.id`, set `J = j`,
   `last = x`, push `(j, asso(x) ?: x)`, print `"<name> <j>"`, and leave only the goal
   loop.
4. Each non-deactivation phase prints `dumpStatus(p, S[i])` on **stdout**.
5. Builds the path. It starts from the story names and, for `k = 0..size-2`, does
   `insertElementAt("[" + name + "]", loc_k)`. The bound `size-1` correctly drops the last
   entry, which is SV pushed by phase 0. The entries run latest-first with non-increasing
   `loc`. Inserting at `loc` therefore never meets an already inserted item before
   `loc`, and items with the same `loc` end up in chronological order: **inserted names do
   not shift later indices incorrectly.** The pieces are concatenated with no separator,
   which is ambiguous for names longer than one letter.

Quirks:
- Calling it on an inconsistent input throws AIOOBE from step 1. (The `last.id` NPE would
  come later and is never reached.) The applet only calls it after a `true`.
- The aliased statuses are harmless here: every state has a predecessor, so each phase
  finds a match.
- **B2 (nondeterminism):** which final state and which predecessor are chosen depends on
  `HashSet` order.
- **B3 (wrong path):** after a match, the scan continues with the *updated* `J` and `last`.
  A second state in the same bucket can then match against the new `last`, which belongs
  to phase `i` itself, and push a spurious crossing.

### `boolean validateAgentStoryMulti(g, sv, story, obHis)` (l.370) ↔ STAR §5 (prose; "we omit the pseudocode")
Keeps an active set `O` (identity `HashSet` of canonical occupancy vertices).
- **Deactivation:** `O.remove(o)` and `continue`. The phase is not processed and nothing
  is printed.
- **Activation:** `O.add(o)` *before* this phase's walk, so the walk already treats `o` as
  open. This is harmless: the only state it can create is `o`, and the activation can be
  re-attributed to x [emu: no false positives].
- `Vg` gets three assignments. The first two are **dead**: `O.toArray()`, then
  `sensorVertices`, both overwritten by `getGraphVertices()` (the same array). So `Vg` is
  always the recording's sensor vertices.
- On an activation, `Vgp = Vg + [s]`, which adds the start as an extra goal. It only feeds
  the goal-edge step. Every edge it could add already exists, so it is a **no-op**
  (0 / 13 299 differences [emu]). The `SP` loop uses `Vg`, not `Vgp`.
- Per state: GP is `getSubGraphMulti(g, s, O, story, Vgp)` and **`GP.dump()` prints the
  whole subgraph on stdout for every (phase, state)**. The walk is the same as in the
  single-agent version.
- After each phase, `S[j].addAll(SP[j])`: states **accumulate** and are never removed.
  This is how "x triggered any subsequence of the beam events" is encoded, since a state
  may sit out any recording. Then `dumpStatus(p, S)`.
- States equal to an occupancy vertex `o` survive `o`'s deactivation. That is
  semantically fine: it reads as "x left `o` into one of its regions before the
  deactivation, choice deferred". There were 0 false positives in 163 800 applet-style
  multi cases [emu].
- **Boolean result:** order-independent. **Mutations:** none. **Cost:** buckets hold up to
  |Vs| states, so roughly O(m·n·|Vs|·(E+Σdeg²+|O|·deg²)), plus the dump output.
- **B1 (false negatives):** a skipped deactivation merges its interval with the next one,
  which is evaluated with `o` already closed. Progress x made through `o` while it was
  open is lost, because states are stored only at goal crossings. The paper says the
  opposite: "additional processing is needed only when deactivation of an occupancy
  sensor happens".
- Malformed input is not checked. A deactivation with no prior activation is ignored.
  The code thus assumes every sensor is inactive at t0, which the paper never states.

### Test drivers (stdout only)
- `testGraphRoutines()` dumps G and six subgraphs. It uses `storyVertices=[SV,A,B,C]`, not
  the story.
- `testStoryHistory()` dumps the single feasible game.
- `testSingleAgent()` runs the single feasible game and prints `Valid story.`.
- `testMultiAgent()` runs the multi infeasible game and prints `Story inconsistent.`.
- `main` calls `testMultiAgent()`.

---

## 3. Mapping to the papers

| Paper | Original | Deviations |
|---|---|---|
| STAR Alg. 1 GETSUBGRAPH | `getSubGraph` | VC = story rooms ∪ {s}, not all of Cp. |
| STAR Alg. 2 GETREACHABLESUBGRAPH | `getReachableSubgraph` | BFS with repeated queue entries. Goals are only added when adjacent (paper line 11 adds all). |
| STAR Alg. 3 / ICRA Alg. 1 VALIDATEAGENTSTORY | `validateAgentStory` | No explicit G_s or CHAIN. The DP is transposed (recordings outer, story positions inner), with one subgraph per (phase, start state) instead of per phase, and the virtual SV in place of `V_I={p1}`. Paper typos are not reproduced: line 8 reads `SENSORVERTICES(r_i)` for `r_j`, and line 12 passes the set `V_I` to a routine that takes one vertex. |
| "a path can be retrieved via backtracking" (STAR §4, ICRA §III.A) | `getAgentStoryStatuses`, `getAgentStory` | Has bugs B2–B4. A path is shown as the story plus `[approach side]` per crossing. |
| STAR Alg. 4 GETSUBGRAPHMULTI | `getSubGraphMulti` | `o` not removed (needed, see above). |
| STAR §5 multi-agent search (no pseudocode) | `validateAgentStoryMulti` | Deactivations skipped (B1). States accumulate. Dead `Vg` assignments, the no-op `Vgp`, and `GP.dump()` spam. |
| BUILDCONNECTIVITYGRAPH | none (`getBasicGame` hard-codes G; `Environment` geometry is drawing only) | |
| ICRA Problems 2–4, ICRA Alg. 2 VALIDATEPARTIALSTORY, composite automaton | none | No original code. |

---

## 4. GUI classes: non-drawing logic

### CyberDetectiveDemoApplet
- **`appInit()`** builds the Swing UI. The environment is
  `createExampleEnvironment(getBasicGame())`.
- **Reset** clears the three text fields, replaces `env.game` with a fresh
  `getBasicGame()`, and calls `startSimulation()`. The `Environment`'s `Rect` and
  `LineSegment` objects still hold the **old** game's vertices, so clicks return stale
  objects. This has no visible effect: lookups are by id, and the only difference in the
  old neighbour sets (the SV edge) is skipped by name.
- **Run** (l.192):
  1. Empty story text: prints `Nothing to validate.` and stops.
  2. Each **character** of the story becomes `g.vertexNameMap.get(ch)` and is appended to
     `game.story`. Multi-letter names are impossible. An unknown character (`a`, `D`, a
     space) appends **`null`**.
  3. Builds `O1, O2, B1={b1u,b1d}, B2={b2r,b2l}` and splits the sensor text on `,`. The
     tokens are exact, case-sensitive matches: `" b1"`, `"B1"`, `"b3"` and `""` are
     **silently ignored**.
  4. **Single mode:** `o1`/`o2` become an activation plus a deactivation pair; `b1`/`b2`
     become one recording. It calls `updateStartingVertex(first story char)`, then
     `validateAgentStory(…, SV, …)`. On true it also calls `getAgentStory` and shows
     `Valid story.\nA possible path: <s>`; otherwise `Inconsistent story.`.
  5. **Multi mode:** each `o1`/`o2` token *toggles* (the first is an activation, the next a
     deactivation, and so on). Then `updateStartingVertex` and `validateAgentStoryMulti`.
     It shows only valid or inconsistent: **no path is ever computed** in multi mode.
  6. Finally `game.story` and `game.obHis` are cleared. **If anything above throws, the
     clear is skipped.** The stale story and history are then prepended to the next Run,
     so one bad character makes every later Run fail until Reset. `updateStartingVertex`
     changes persist between runs; this is harmless because each Run re-attaches SV.
- **Click flow vs. multi parsing.** The mouse flow writes one `o1` per visit, which is
  single-agent thinking. In multi mode that single token means "o1 activated and never
  deactivated".
- **`startSimulation()`** shows the instruction text and resets the clickable set
  `vertexIdMap` to {A, B, C}.
- **`mouseClicked(e)`** (l.291) maps the click to world coordinates (`×800/400`; y is also
  divided by `canvasWidth`, which is consistent because the scale is uniform) and calls
  `getClickedVertex`. It ignores vertices that are not in `vertexIdMap`.
  - Room: append the name to the story text.
  - Sensor: append `name.substring(0,2)` to the sensor text, comma-separated. For a beam
    the current location becomes `assoVertex`, i.e. the agent crossed to the far side.
  - The new clickable set is the location's neighbours (SV skipped by name) **plus the
    location itself**. Re-clicking the same room appends `AA`; re-clicking a beam side
    crosses back; re-clicking `o1` adds a second `o1`.
  - Instructions show `Current location: …\nReachable features: n1, n2, …, self`.
  - Adjacency is never checked on Run, so typed text bypasses it.

### Environment
- **`getClickedVertex(int x, int y)`** (l.94) tests occupancy rectangles first, then
  rooms, then the beam strips. Strips are 16 px wide; `Rectangle2D.contains` is
  half-open. Order inside each class is `HashMap` order by vertex id.
  - Overlap: the b2 strips span y∈[180,265) and room B spans y∈[0,185). A click at
    y∈[180,185) on b2l or b2r returns **B**.
  - `initialize()` (l.76) builds the strips assuming every beam is vertical or
    horizontal; any other line would be treated as horizontal.
- **`createExampleEnvironment(game)`** (l.113) holds the drawing geometry (see
  `map-geometry.md`). It is not used to compute G.
- **`createBeamDetector`, `createOccupancySensor`, `createRoom`** look up vertices by name
  in the game. They throw an NPE if a name is missing.

### Other UI
- `AwtApplet.init()`: `FileHelper.AppletInit`, prints the base URL, then calls `appInit`.
- `EnvPanel.drawPoly(..., printPoints)` would print coordinates on stdout; it is never
  called.
- `BasePanel.listeningEvents()` sleeps 100 ms and then adds the mouse listener; it is
  never called.
- `DrawingContext(DrawingContext)` does not copy the origin.
- `LineSegment.drawText` reads `name.charAt(2)`, so a two-character beam name would throw.
- Everything else is drawing.

---

## 5. Output determinism summary

| Output | Depends on identity-hash order? |
|---|---|
| `validateAgentStory`, `validateAgentStoryMulti` return values | No |
| `getAgentStoryStatuses` set contents | No |
| `getAgentStory` return string and its `"<name> <j>"` lines | **Yes** (B2, B3) |
| `dumpStatus` lines | Yes (bracket order when a bucket has 2+ elements) |
| `Graph.dump` | Vertex and edge order: deterministic per JDK. Neighbour order: no. Contraction-edge orientation in `GP.dump`: no |

**Hint for the harness:** run the JVM with `-XX:+UnlockExperimentalVMOptions
-XX:hashCode=2`. Every identity hash is then 1 and small `HashSet`s iterate in insertion
order, which pins one reproducible order. Record several orders if we want to cover B2
and B3.

---

## 6. Suspected bugs, ranked

(Superseded by `original-bugs.md`; kept as the record of what we suspected before running the
harness.)

**B1. Multi-agent false negatives: deactivations are skipped.** **[emu]**
- Reproduction: map star_fig2; mode multi; story `BC`; history `o1A, b2, o1D` (applet
  multi text `o1,b2,o1`).
- Code: **false**. Expected: **true**. Another agent activates o1. x, starting in B,
  crosses b2 from b2r to b2l into R2, passes through the open o1 into R1, and enters C.
  The other agent then leaves o1.
- Without the final `o1D` the code says true. In the phase after the skipped deactivation
  the walk from state b2l runs with o1 closed.
- Scale: 560 of 163 800 applet-style cases differ, all false negatives. Paper example
  eq. 3 is unaffected.

**B2. `getAgentStory` is nondeterministic.** **[emu]**
- Reproduction: single mode; story `A`; history `b1`. The output is `A[b1u]` or `A[b1d]`,
  depending on whether b1d or b1u comes first in `S[1][1]`.
- Both answers are valid explanations. 147 of 904 consistent single cases have more than
  one possible output.

**B3. `getAgentStory` can return an invalid path with an extra crossing.** **[emu]**
- Reproduction: single mode; story `AC`; history `b1, b1` (text `b1,b1`).
- If b1u iterates before b1d in `S[1][1]`, the code returns `A[b1u][b1d][b1u]C`: three
  crossings for two recordings. The only valid path in this notation is `A[b1d][b1u]C`.
  (`AC[b1d][b1u]` is also valid, but the code always picks the smallest final `j`.)
- Under insertion order (`hashCode=2`) the code gives the right answer.
- Cause: after the first match at phase 1, `last` becomes b1u. The next state, b1d, then
  "matches" `flip(b1d) = b1u == last` and pushes a bogus entry. This needs two states in
  the same bucket that are the two sides of the same beam, crossed twice in a row.
- Scale: 46 of 904 consistent cases can produce an invalid path; 24 do so even under a
  consistent global order.

**B4. `getAgentStoryStatuses` never returns `null`.** On inconsistent input it throws
`ArrayIndexOutOfBoundsException` from `dumpStatus(p, S[m+1])`, so `getAgentStory` throws
too. Example: story `B`, empty history, SV–A. The applet never reaches this. **[emu]**

**B5. The single-agent search accepts histories that violate its own assumptions.** It
ignores every deactivation and never checks the pairing. **[emu]**
- Story `A`, history `o1D` → true (expected false; the agent starts in A).
- Story `A`, history `o1A, o2A` → true (expected false: moving o1→o2 must record o1D).
- Story `A`, history `o1A, b1` → true.
- 13 674 malformed and 2 094 well-formed-but-unpaired raw histories differ. The applet's
  single mode cannot produce these.
- Multi mode: deactivation-first histories are also accepted (30 870 cases); the
  "inactive at t0" assumption is undocumented.

**B6. The applet's sticky failure.**
- Story text `AD`: `null` is appended, then an NPE in `validateAgentStory` (on
  `p[1].id`). The clear is skipped, so after correcting the text to `AC` the stored story
  is `A,null,A,C` and the Run fails again.
- Story text `DA`: an NPE inside `updateStartingVertex` corrupts SV (neighbours = {null},
  no edge). Every later Run throws an NPE until Reset.
- Unknown sensor tokens and tokens with spaces are dropped silently. **[harness]**

**B7. Latent issues (unreachable with the original data):**
- `Edge` id collisions for ids ≥ 65536.
- An NPE in the goal step if neighbour sets and `edgeMap` diverge.
- `flip` and `vertexNameMap` depend on unique names.
- A beam `DEACTIVATION` would count as a crossing.
- `DetectiveGame` stories that do not start in A are always false.

---

## 7. Confirmed by harness

(Superseded by `original-bugs.md`; see the note at the top for the B7a control.)

Each claim was run live with `tools/reference/run.sh`. The canonical flags are
`-Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions -XX:hashCode=2` (Temurin 8u504,
original `55f57f8`). "Alt" means the run was repeated under `hashCode=5` (burn seeds 1, 2,
3) and `hashCode=3` (burn seed 0) with `-XX:+UseSerialGC -XX:ActiveProcessorCount=1`.

Map `star_fig2` is used unless stated otherwise, with the applet start
(`updateStartingVertex(p1)`). Histories are written `o1A` = activation and `o1D` =
deactivation; beams always activate. Cross-references: `tests/fixtures/golden/builtin.json`
(`repro_*`), `applet.json`, `paper_cases.json`, and `docs/notes/phase1-crosscheck.md`.

| claim | input | observed output | status |
|---|---|---|---|
| B1 multi false negative | multi, `BC`, `o1A b2 o1D` | `false` (also `false` with an extra `o2A`); without `o1D`: `true`. The independent oracle gives `true` for all three | **confirmed** |
| B2 nondeterministic path | single, `A`, `b1` | canonical `A[b1u]`; alt `A[b1d]` | **confirmed** (89 order-dependent golden paths) |
| B3 invalid path | single, `AC`, `b1 b1` | canonical `A[b1d][b1u]C` (valid); alt `A[b1u][b1d][b1u]C` | confirmed under alt only |
| B3 under the canonical order | single, `A`, `b1 b1` | `A[b1d][b1u][b1d]` (3 crossings, 2 recordings) | **confirmed; refutes "under insertion order the code gives the right answer"** |
| B3 on a consistent input | single, `AC`, `b1 b1 b1` | `A[b1d][b1u][b1d][b1u]C` (4 for 3); alt `A[b1u][b1d][b1u][b1d][b1u]C` | **confirmed** |
| B3 scale | golden data | 24 paths with the wrong crossing count (12 `star_random_single`, 7 `random_maps`, 5 applet). Of the 17 on `star_fig2`, 13 come from valid single-agent histories. The checker finds no wrongly placed crossings, only wrong counts | confirmed |
| B4 `getAgentStoryStatuses` throws | `B`, `[]`, `start=fixed`; paper eq. (1)+(2) single | `ArrayIndexOutOfBoundsException` @ `Algorithms.java:365` (from both `getAgentStoryStatuses` and `getAgentStory`); with the applet start, `B`/`[]` returns `"B"` | **confirmed** (the example needs `start=fixed`) |
| B5 single accepts invalid histories | `A` + `o1D` / `o1A o2A` / `o1A b1`; `ACBAC` + eq. (3) | `true`, `true`, `true`, `true` (eq. (3) path `A[b1u]C[o1][o2]B[b2r]AC`) | **confirmed** |
| B5 multi accepts invalid histories | multi, `A`, `o1D`; multi, `CB`, `o1A o1A o2A` | `true`, `true` | **confirmed** |
| B6 sticky failure | applet Run `AD`/`b1`, Run `AC`/`b1`, Reset, Run `AC`/`b1` | NPE @ `Algorithms.java:207`; NPE again with stored story `[A,null,A,C]` and history `[b1,b1]`; after Reset `Valid story. A possible path: A[b1u]C` | **confirmed** |
| B6 `DA` corrupts SV | Run `DA`, Run `A`, Reset, Run `A` | NPE @ `DetectiveGame.java:30`, then NPE @ `DetectiveGame.java:23`, then `Valid story.` | **confirmed** |
| B6 tokens dropped | sensors `" b1"`; `"B1,b3,b1"` | parsed history `[]` (verdict valid, path `AC`); `[b1]` | **confirmed** |
| B7 edge-id collision | generic map: rooms A, B, occupancy `o1..o65537`, edges `A–o65537`, `B–o1` (ids 3·65536+65541 = 4·65536+5) | `game_dump` edges `[A–o65537, B–SV]`: `B–o1` silently dropped. Story `B`, `o1A o1D` gives `false`; the same story on a small control map gives `true`; story `A`, `o65537A o65537D` gives `true` | **confirmed** (latent: needs ids ≥ 65540) |
| B7 beam `D` counts as a crossing | single, `AC`, `b1D` | `true` | **confirmed** |
| B7 `DetectiveGame` stories not starting in A | `start=fixed`, `CA`, `[]` | `false` | **confirmed** |
| B7 NPE when neighbour sets and `edgeMap` diverge | – | not reachable through any map spec | not confirmed (latent) |
| B7 `flip`/`vertexNameMap` need unique names | – | the harness map format cannot express duplicate names | not confirmed (latent) |
| `GP.dump()` count is order dependent | `MultiFeasible`, `validateAgentStoryMulti` | 36 blocks canonical; alt 37, 37, 35 | **confirmed** |
| b2 hit strip overlaps room B | applet click (312,91), world (624,182); click (312,93) | hit `B`; hit `b2l` | **confirmed** |
| Goal step drops unreachable goals | `getSubGraph(o1, {A,B,C}, {b2r,b2l})` (STAR Fig. 4 G3) | V = {A,C,b2l,o1}, no `b2r` | **confirmed**; equals the drawn figure |
| `getSubGraphMulti` keeps `o` | the 9 STAR Fig. 7 subgraphs | each equals literal Alg. 4 once the active `o` and its edges are removed | **confirmed** |
| §2 "0 differences vs brute force" for `validateAgentStory` | single, `A`, `b1` | `true`, path `A[b1u]`: the story ends before the last recording | **qualified**: this is the new bug B8 below. The inventory's brute force shared the same convention |

**New: B8. The single-agent search accepts a story that is used up before the last
recording.**

- **Cause.** `Algorithms.java:196-199` returns true in the final phase as soon as
  `l == n`, even when `l == j`, i.e. for a state carried over from an earlier phase. That
  state is a sensor vertex, not `p_n`.
- **Why it contradicts the papers.**
  - STAR Alg. 3 l.21 requires `(p_n, m+1)`.
  - ICRA §II-A: "we require that agent x starts from p1 and ends in pn".
- **Reproductions.**
  - Single, `A`, `b1` gives `true` (correct: false).
  - In `paper_cases.json` it affects:
    - `star_single_end_must_be_last_room` (`AA`/`b2` gives `AA[b2l]`);
    - `icra_derived_problem2_case2_counterexample`;
    - `icra_derived_problem4_length_claim_counterexample`;
    - `icra_derived_repeat_semantics_a`;
    - `icra_derived_piecewise_acceptance_AA`.
  - Applet: `applet_session_click_random05` shows "Valid story. A possible path: C[b1d]".
- **Scale and scope.**
  - On the 202 golden single-agent inputs with valid histories, the original equals a
    "may end early" oracle on 202/202, and the paper's semantics on 199/202.
  - Multi-agent verdicts are unaffected: on all 238 golden multi-agent cases with valid
    histories, the original agrees with the strict oracle.
- **Corrected mode.** Make the same change at l.197, l.256 and l.346: accept in the final phase only when `l > j`. The multi-agent test at l.420 is unaffected. On a patched private copy, the original then agrees with the strict oracle on all 221 valid single-agent inputs (202 random + 19 paper); only invalid histories (B5) still differ.
