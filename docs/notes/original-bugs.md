# Bugs in the original code

This is the final list of bugs we found in the original Java code
([arc-l/cyber-detective](https://github.com/arc-l/cyber-detective) at commit `55f57f8`).
It supersedes the bug sections of `original-inventory.md` (§6 and §7) and
`phase1-crosscheck.md`. The ids B1–B8 are the ones used in those notes. B9 and B10 are new.

- `compat="original"` reproduces every bug, crashes included. It is a line-by-line port in
  `cyber_detectives.compat`, pinned by the golden fixtures in `tests/fixtures/golden/`.
- The default mode fixes them. On the golden data, every verdict where the two modes differ
  is attributed to a bug below by `tests/test_default_vs_original.py`.

File and line references are to `cyber-detective-source/projects/cyberDetective/` in the
original repository.

## Summary

Severity scale:

- **high**: a wrong verdict reachable from the applet, the original's only user interface;
- **medium**: a wrong verdict or path reachable through the Java API, or a broken applet
  session;
- **low**: a crash on input the caller should not pass, nondeterminism between valid
  answers, or noise;
- **latent**: not reachable with the original's map and applet.

| id | severity | what goes wrong | golden entries (distinct inputs) |
|---|---|---|---|
| [B1](#b1) | high | multi-agent false negative: a deactivation loses the agent's progress | 4 (1) verdicts |
| [B2](#b2) | low | `getAgentStory`'s path depends on `HashSet` iteration order | 89 order-dependent paths |
| [B3](#b3) | medium | `getAgentStory` returns a path with extra crossings, even under a fixed order | 24 paths (17 on inputs the default accepts) |
| [B4](#b4) | low | `getAgentStoryStatuses` / `getAgentStory` throw instead of returning `null` | 237 crashes |
| [B5](#b5) | medium | histories no agent can produce are accepted (single and multi) | 197 (133) verdicts |
| [B6](#b6) | medium | applet: an unknown story letter crashes and poisons later runs; bad sensor tokens are dropped | 25 crashed Runs, 4 poisoned Runs, 14 Runs with dropped tokens |
| [B7](#b7) | latent | (a) edge-id collision; (b) beam deactivation counted as a crossing; (c) hard-wired start in A; (d), (e) unchecked invariants | (b) 21 (17) and (c) 1 verdicts |
| [B8](#b8) | high | single-agent false positive: the story may end before the last recording | 64 (33) verdicts |
| [B9](#b9) | low | `validateAgentStoryMulti` prints a full subgraph per state, plus dead code | all 539 multi-agent cases' stdout |
| [B10](#b10) | low | applet clicks: a beam strip is hit as room B; stale vertices after Reset | 2 sessions |

### How the counts were made

A *golden entry* is one recorded verdict: a `validateAgentStory`,
`validateAgentStoryMulti` or `getAgentStory` case that returned, or one applet Run that
returned a verdict. The golden files have 1,631 such entries. On 1,624 of them the default
mode has a verdict too. The other 7 have an empty story, which the default rejects as an
input error, so they are not compared.

The two modes differ on 243 of the 1,624. Each difference is explained by one detector
or a combination:

| mode | detectors | entries | distinct inputs |
|---|---|---|---|
| single | B5 | 99 | 48 |
| single | B5 + B8 | 22 | 12 |
| single | B5 + B7b | 2 | 1 |
| single | B5 + B7b + B8 | 4 | 2 |
| single | B7b | 1 | 1 |
| single | B7b + B8 | 2 | 1 |
| single | B7c | 1 | 1 |
| single | B8 | 36 | 18 |
| multi | B1 | 4 | 1 |
| multi | B5 | 60 | 60 |
| multi | B5 + B7b | 10 | 10 |
| multi | B7b | 2 | 2 |

Every difference goes the same way: the original says true and the default says false.
The two exceptions are B1 (original false) and B7c (original false).

**How a difference is attributed.** Each detector is a predicate on the input (map, story,
history, start), never on the original's output. Each one comes with a *relaxation* of the
default semantics that mimics the bug; the test's module docstring defines them all. A
difference is explained when the relaxations of the detectors that fire, applied together,
give the original's verdict. It is attributed to the smallest subset that does so.

**The relaxations as a model of the original.** Applied together, the relaxations reproduce
the original's verdict on all 1,624 entries, including the 1,381 where the two modes agree.
So on the golden data, the original's verdicts are exactly:

- default semantics,
- plus B5, B7b and B8 in single-agent mode,
- plus B5, B7b and B1 in multi-agent mode,
- plus B7c for the fixed-start cases.

**Paths.** There are 1,624 − 243 = 1,381 entries where the two modes agree. Some of them
are single-agent entries where both modes say true and the original printed a path. Each
such path is replayed by an independent path checker (`tests/oracles/brute.py`). It
passes, except for 17 paths that detector B3 flags.

## B1

**Multi-agent false negative: a deactivation loses the agent's progress.** Severity:
**high**.

**Reproduction**

- Map `star_fig2`, multi-agent.
- Story `BC`, history `o1A b2 o1D`. In applet multi mode, the sensor text is `o1,b2,o1`.
- Original: **false**.
- Correct: **true**. Another agent activates o1. x starts in B and crosses b2 from `b2r`
  to `b2l` into R2. It passes through the open o1 into R1 and enters C. Then the other
  agent leaves o1.
- Without the final `o1D` the original says true.

**Root cause**

- `Algorithms.java:385-388`: `validateAgentStoryMulti` handles a deactivation by
  `O.remove(...)` and `continue`, so the deactivation is not a phase of its own.
- States are stored only at goal crossings (l.423-429). The walk from the last stored state
  (`b2l`) to the next phase therefore runs with o1 already closed (l.416, `O` passed to
  `getSubGraphMulti`).
- The moves x made through o1 while it was open are lost.
- The paper says the opposite: "additional processing is needed only when deactivation of
  an occupancy sensor happens" (STAR §5).

**compat="original"**

- `compat.original.validate_agent_story_multi` ports the loop as written.
- `validate(m, "BC", "o1 b2 o1", agents="multi", compat="original")` returns false.

**Default mode**

- An occupancy recording changes connectivity at its own time (`engine.py`). x may move
  through an active region in any interval in which it is active.
- Detector B1 replays the default search with the original's timing:
  - free moves see the occupancy set of the *next* non-deactivation recording;
  - x inside a deactivating `o` leaves it at the deactivation.

**Golden data**

- 4 entries, 1 distinct input: `builtin.json` `repro_B1_multi_false_negative`, and in
  `applet.json` `applet_malformed30_multi` (Run and `__direct`) and
  `applet_session_mode_switch`.
- No random case triggers it.
- Our initial reverse-engineering pass found, by emulation, 560 differences in 163,800
  applet-style multi-agent inputs, all false negatives (`original-inventory.md` §6).

## B2

**`getAgentStory`'s path depends on `HashSet` iteration order.** Severity: **low**. Both
answers are valid explanations.

**Reproduction**

- Map `star_fig2`, single agent, story `A`, history `b1`.
- Under the canonical JVM setting `-XX:hashCode=2`, the original prints `A[b1u]`. Under
  `hashCode=5` it prints `A[b1d]`.
- The input itself is inconsistent; see B8.

**Root cause**

- `Algorithms.java:248-266`: `getAgentStory` takes the first state in `S[i][j]` whose walk
  reaches the end. It then takes, phase by phase, the first matching predecessor
  (l.268-283).
- The states sit in a `HashSet<Vertex>`, and `Vertex.java` defines neither `hashCode` nor
  `equals`. The iteration order therefore follows identity hash codes, which vary between
  JVM runs and settings.

**compat="original"**

- The golden fixtures pin `-XX:+UnlockExperimentalVMOptions -XX:hashCode=2`, under which
  every identity hash is 1.
- `compat.javahash` emulates Java 8 `HashMap` exactly under that setting, including tree
  bins at 11 or more members. It is checked against the JDK by
  `tests/fixtures/golden/javahash.json`.
- Golden fields that changed under other settings are marked `order_dependent`.

**Default mode**

- The witness comes from a breadth-first search in map order. It is deterministic and
  always replayable (`replay`).
- It is generally not the original's path: for example `AC[b1d][o1][o2]B[b2r]AC` where the
  original prints `A[b1u]C[o1][o2]B[b2r]AC`.

**Golden data:** 89 `getAgentStory` paths are order dependent (`summary.json`,
`getAgentStory_cases_whose_path_is_order_dependent_B2`).

## B3

**`getAgentStory` returns a path with extra crossings.** Severity: **medium**. The path is
printed to the applet user as "A possible path". This happens even under the canonical
order.

**Reproduction**

- Map `star_fig2`, single agent, story `AC`, history `b1 b1 b1`. The input is consistent.
- Original: true with path `A[b1d][b1u][b1d][b1u]C`, which has 4 crossings for 3
  recordings.
- Correct: a path with 3 crossings, for example the default's.
- With story `A` and history `b1 b1`, the original prints `A[b1d][b1u][b1d]`. That input is
  inconsistent (B8).

**Root cause**

- `Algorithms.java:268-283`: after a match in phase `i`, `J` and `last` are updated to a
  state of phase `i` itself, and the `break` at l.282 leaves only the goal loop.
- The scan over the remaining states of `S[i][·]` (l.248-250) continues. The early exit at
  l.293/295 applies only to the final phase.
- A second state of the same phase can now "match" the new `last`. This happens for the
  other side of the same beam: `flip(b1d) == b1u == last`. The second state pushes a
  spurious crossing.
- So B3 needs two consecutive recordings of the same beam.

**compat="original"**

- Ported as is (`compat.original.get_agent_story`).
- `validate(m, "AC", "b1 b1 b1", compat="original").path_string()` returns
  `"A[b1d][b1u][b1d][b1u]C"`.

**Default mode**

- Every witness is replayed by `replay` in the tests.
- Detector B3 flags a path that has more bracketed crossings than the history has
  recordings, on a history with two consecutive recordings of the same beam.

**Golden data**

- 24 recorded paths have the wrong number of crossings (`summary.json`): 12 in
  `star_random_single`, 7 in `random_maps` and 5 in `applet`.
- 17 of them are on inputs that both modes accept; B3 flags all 17. The other 7 are on
  inputs that only the original accepts (B5/B8).
- Every other path on an input both modes accept replays.

## B4

**`getAgentStoryStatuses` and `getAgentStory` throw instead of returning `null`.** Severity:
**low**. The applet calls them only after a true verdict.

**Reproduction**

- Map `star_fig2`, story `B`, empty history.
- `SV` is left joined to `A`, as in `DetectiveGame` and `Algorithms.test*`.
- Original: `ArrayIndexOutOfBoundsException` at `Algorithms.java:365`.
- Correct: `null` ("no path"). With the applet start, the same input returns `"B"`.

**Root cause**

- `Algorithms.java:315-368`: `S` has `r.length + 1` rows (l.318).
- When no state reaches the end in the final phase `i == r.length`, control falls through
  to `dumpStatus(p, S[i + 1])` at l.365, which indexes row `r.length + 1`.
- The documented `return null` at l.367 is unreachable.
- `getAgentStory` calls `getAgentStoryStatuses` first (l.229), so it throws too.

**compat="original"**

- `compat.original.get_agent_story_statuses` / `get_agent_story` raise
  `compat.original.ArrayIndexOutOfBoundsException`. Its `origin_frame` is
  `projects.cyberDetective.Algorithms.getAgentStoryStatuses(Algorithms.java:365)`.
- The CLI maps an original crash to exit status 4.

**Default mode:** `validate` returns `consistent=False` with a reason. It never crashes on
inconsistent input.

**Golden data:** 237 recorded crashes at `Algorithms.java:365`, from `getAgentStory` and
`getAgentStoryStatuses` cases.

## B5

**Histories that no agent can produce are accepted.** Severity: **medium**. The applet
cannot produce such histories: single mode always pairs `o1` into an activation and a
deactivation, and multi mode toggles. The Java API accepts them.

**Reproductions** (map `star_fig2`; the correct answer is always false, `malformed history`)

| mode | story | history | original |
|---|---|---|---|
| single | `A` | `o1D` | true, path `A` |
| single | `A` | `o1A o2A` | true, path `A[o1][o2]` (also B8) |
| single | `A` | `o1A b1` | true (also B8) |
| single | `ACBAC` | STAR eq. (3) `b1 o1A o2A b2 o2D o1D` | true, path `A[b1u]C[o1][o2]B[b2r]AC` |
| multi | `A` | `o1D` | true |
| multi | `CB` | `o1A o1A o2A` | true |

**Root cause**

- Single agent:
  - `Algorithms.java:180-182` (`validateAgentStory`) skips every occupancy deactivation
    with `continue`. So do l.239-241 (`getAgentStory`) and l.328-331
    (`getAgentStoryStatuses`).
  - An activation is a phase whose goal is `o` itself (l.184-186, 200-205). The next phase
    walks on from `o`, so x is read as passing through `o` at the activation.
  - Nothing checks that the activation is followed by its own deactivation and by nothing
    else.
- Multi agent:
  - `Algorithms.java:380-393` keeps the active sensors in a `HashSet`. A deactivation of
    an inactive sensor, or a repeated activation, is silently absorbed.
  - Every sensor is assumed inactive at `t_0` (l.380), which the paper never states.
  - Nothing checks that activations and deactivations alternate.

**compat="original":** ported as is.

**Default mode**

- `consistent=False` with a reason starting `"malformed history: "` that names the first
  offending recording (`docs/DESIGN.md`, "Malformed input").
- Detector B5 is an independent well-formedness check. A test checks that it agrees with
  `history.check_history`.
- Its relaxation is the original's reading:
  - single agent: drop every deactivation, and pass through `o` at each activation;
  - multi agent: drop redundant toggles.

**Golden data**

- 197 entries (133 distinct inputs): 127 single-agent, 70 multi-agent.
- Some of these also need B7b or B8; see the table in the summary.
- Paper example: STAR eq. (3) read as a single-agent history (`paper_cases.json`).

## B6

**Applet: an unknown story letter crashes and poisons later runs; unknown sensor tokens are
dropped.** Severity: **medium**. It breaks an applet session until Reset.

**Reproductions** (applet, `star_fig2`)

1. Run story `AD`, sensors `b1`.
   - `D` resolves to null and `validateAgentStory` throws an NPE at
     `Algorithms.java:207`.
   - The story and history are not cleared.
   - Run again with story `AC`: the game now holds the story `A, null, A, C` and the
     history `b1, b1`, and the run throws again.
   - Only Reset recovers: then `Valid story. A possible path: A[b1u]C`.
   - Golden case `applet_session_sticky_AD_then_AC`.
2. Run story `DA`.
   - `updateStartingVertex(null)` adds null to `SV`'s neighbours and then throws an NPE at
     `DetectiveGame.java:30`.
   - Every later Run throws an NPE at `DetectiveGame.java:23` until Reset.
   - Golden case `applet_session_sticky_DA_corrupts_SV`.
3. Sensor text `" b1"` parses to an empty history, and `"B1,b3,b1"` parses to `b1`.
   - Tokens must match `o1`, `o2`, `b1` or `b2` exactly. Anything else is dropped without
     a message.
   - The correct behaviour is to report the bad token.

**Root cause**

- `CyberDetectiveDemoApplet.java:204-206` resolves each story character with
  `vertexNameMap.get` and keeps the nulls.
- The story and history are cleared only at the end of the handler (l.270-271), which an
  exception skips.
- `DetectiveGame.java:29-30` mutates `SV` before dereferencing the null vertex.
- l.213-262 match sensor tokens with `equals` and ignore the rest.

**compat="original"**

- `compat.applet.Applet` ports the Run and Reset handlers, including the stored state.
- Pinned by the `applet.json` sessions.

**Default mode and CLI**

- Unknown room or sensor names are an `InputError` (CLI exit status 2) naming the bad
  token.
- Calls are stateless.

**Golden data**

- 25 applet Runs crash with an NPE.
- 4 Runs in 3 sessions validate a story poisoned by an earlier Run:
  `applet_session_sticky_AD_then_AC`, `applet_session_sticky_DA_corrupts_SV` and
  `applet_session_nothing_to_validate_keeps_state`.
- 14 Runs have dropped sensor tokens.

## B7

**Latent issues: unreachable with the original's map and applet.**

### B7a: edge-id collision

Severity: **latent**. It needs vertex ids of 65536 or more.

**Reproduction**

- Rooms `A`, `B`; occupancy sensors `o1`…`o65537`; regions `{A, o65537}` and `{B, o1}`;
  every other sensor in a region of its own.
- Story `BB`, history `o1A o1D`.
- Original: false.
- Correct: true. The original also says true on the same map with only `o1`, `o2`, with
  path `B[o1]B`.

**Root cause**

- `Edge.java:20-27` computes `id = min*65536 + max` in `int`.
- `A–o65537` (3·65536 + 65541) and `B–o1` (4·65536 + 5) get the same id.
- The builder adds an edge only if its id is absent (`DetectiveGame.java:165-176`), so
  `B–o1` is silently dropped.

**compat and default**

- `compat.original.Edge.get_edge_id` uses the same 32-bit arithmetic, so compat reproduces
  it.
- The default mode has no edge ids.
- Test: `tests/test_default_vs_original.py::test_minimal_reproduction_b7a`.

**Correction to `original-inventory.md`.** Its §7 used story `B` as the control. Its
"true" on the small map is itself B8: x must re-enter B. Story `BB` is the clean
reproduction.

### B7b: a beam deactivation counts as a crossing

Severity: **latent**. Only the Java API can create a beam deactivation; the applet always
records beams as activations.

**Reproduction**

- `star_fig2`, single agent, story `AC`, history `b1D`.
- Original: true, path `A[b1u]C`.
- Correct: false, `malformed history` (beams only fire).

**Root cause:** `isDeactivation` (`Algorithms.java:111-116`) is true only for occupancy
sensors. A beam recording with event `DEACTIVATION` goes through the normal phase as a
crossing (l.184-186, 204).

**Default mode:** malformed history. Detector B7b reads the deactivation as a crossing.

**Golden data:** 21 entries (17 distinct inputs), alone or with B5/B8.

### B7c: the hard-coded games start in A

Severity: **latent**. It affects the `DetectiveGame` games and `Algorithms.test*`, not the
applet.

**Reproduction**

- `star_fig2` with `SV` joined to `A`, as built by `getBasicGame`.
- Story `CA`, empty history.
- Original: false.
- Correct: true (start in C, enter A).

**Root cause**

- `DetectiveGame.java:122-124` joins `SV` to `A`.
- The search starts at `SV` (`Algorithms.java:176`), and its walk needs
  `areNeighbors(p[0], SV)` (l.207).
- Only the applet re-attaches `SV` to the first story room (`updateStartingVertex`,
  `CyberDetectiveDemoApplet.java:235/265`).

**compat and default**

- The golden cases with `start: "fixed"` reproduce it. `validate_compat` always uses the
  applet start.
- The default always starts in `p_1`.

**Golden data:** 1 entry (`repro_first_room_not_adjacent_to_SV_fixed`). It is also why the
B4 reproduction needs the fixed start.

### B7d, B7e: unchecked invariants

**B7d.** In the goal step (`Algorithms.java:98-101`), `isNeighbor` consults neighbour sets
but the edge comes from `edgeMap`. If the two diverge, `addCopyOfEdge(null)` throws an NPE
(`Graph.java:90`). No map spec reaches this.

**B7e.** `flip` compares beam sides by name (l.130), and `vertexNameMap` is keyed by name.
Duplicate names would alias.

**Default mode:** `Map` rejects duplicate names (`MapError`).

## B8

**Single-agent false positive: the story may end before the last recording.** Severity:
**high**.

**Reproductions** (`star_fig2`, single agent)

| story | history | original | correct |
|---|---|---|---|
| `A` | `b1` | true, path `A[b1u]` | false: x would end outside A |
| `AA` | `b2` | true, path `AA[b2l]` | false |
| `BC` | `b2 b1 b1` | true | false (golden `s0007`) |

**Root cause**

- `Algorithms.java:196-199`: in the final phase, `if (i == r.length && l == S.length - 1)
  return true`. This also fires for a state carried over from an earlier phase with the
  story already used up (`l == j == n`). That state is a sensor vertex, not `p_n`.
- The same test appears at l.255-256 (`getAgentStory`) and l.345-347
  (`getAgentStoryStatuses`). The printed path then ends with a bracket after the last
  room.
- The papers forbid this. STAR Alg. 3 l.21 requires `(p_n, m+1)`, and ICRA §II-A says "we
  require that agent x starts from p1 and ends in pn".
- Multi-agent verdicts are unaffected: x need not make the later recordings, so it can
  stay in `p_n`.
- The fix is to accept in the final phase only when `l > j`. On a patched copy, the
  original then agrees with the strict oracle on every valid single-agent input
  (`original-inventory.md` §7).

**compat="original":** ported as is.

**Default mode**

- x must be inside `p_n` at `t_f` (`docs/DESIGN.md`, "Start/end").
- The reading "`p_n` is merely the last room visited" is not supported; fixture keys
  `consistent_if_story_need_not_end_in_last_room` record it.
- Detector B8 appends a fresh room that touches every region to the story. After using up
  the story, x must then make the remaining recordings without entering a room, which is
  the original's rule.

**Golden data**

- 64 entries (33 distinct inputs), alone or with B5/B7b.
- 5 paper cases are affected: `star_single_end_must_be_last_room`,
  `icra_derived_problem2_case2_counterexample`,
  `icra_derived_problem4_length_claim_counterexample`, `icra_derived_repeat_semantics_a`
  and `icra_derived_piecewise_acceptance_AA`.
- 7 applet Runs (plus 4 of their `__direct` companions), for example
  `applet_session_click_random05`: "Valid story. A possible path: C[b1d]".

## B9

**`validateAgentStoryMulti` prints a full subgraph per state, and carries dead code.**
Severity: **low**. It does not change verdicts.

**Reproduction**

- `DetectiveGame.getMultiFeasibleGame()`, which is STAR eq. (3).
- `validateAgentStoryMulti` prints 36 `Graph.dump()` blocks before returning true. The
  count depends on the hash order: 35–37 under other settings.

**Root cause**

- `Algorithms.java:418` calls `GP.dump()` for every (phase, state). This is debugging
  output left enabled; the single-agent versions have it commented out (l.195, 254, 344).
- In the same method:
  - `Vg` is assigned three times (l.390-401), and only the last assignment is used;
  - `Vgp` (l.407-414) adds the start as an extra goal, but every edge it could add
    already exists, so it is a no-op (0 of 13,299 emulated cases differ).
- The output grows with phases × states × subgraph size. In the applet it goes to the
  Java console.

**compat="original":** reproduced, and captured with `compat.original.capture_stdout()`.
The golden `stdout` of every multi-agent case pins it.

**Default mode:** prints nothing.

**Golden data:** all 539 `validateAgentStoryMulti` cases record the dumps.

## B10

**Applet clicks: a beam strip is hit as room B; stale vertices after Reset.** Severity:
**low**. This affects the interactive story builder only.

**Stale vertices.** Reset (`CyberDetectiveDemoApplet.java:188`) replaces `env.game`, but
the `Environment` keeps the `Vertex` objects of the game it was created with
(`Environment.java:30-47`). Clicks after Reset return the old game's vertices and list
their neighbours. The ids coincide, so no visible difference was observed (golden
`applet_session_click_after_reset_stale_vertices`).

**Hit-testing.** `Environment.getClickedVertex` (`Environment.java:94-111`) tests
occupancy rectangles, then rooms, then beam strips widened by 8 px (l.83-91). Room B's
rectangle overlaps the top of the b2 strips. A click at applet pixel (312, 91), world
(624, 182), selects room B instead of `b2l`; one pixel lower (312, 93) selects `b2l` (golden
`applet_session_click_b2_overlap_strip`).

**compat="original":** `compat.applet` ports both behaviours.

**Default mode:** none. The browser demo has its own hit-testing (`geometry.feature_at`).

## Not counted as bugs

**Empty story.**

- `validateAgentStory` with an empty story returns true for an empty history and false
  otherwise.
- The papers require `p_1`, and the applet answers "Nothing to validate.".
- The default raises `InputError`. These are the 7 golden entries without a default verdict.

**Repeated rooms.**

- `areNeighbors` treats a vertex as its own neighbour (`Algorithms.java:162-165`), so the
  original satisfies a repeated room (`AA`) without leaving the room.
- The default makes x leave into a region and re-enter.
- The verdicts agree whenever the room touches a region. No golden entry differs because
  of it: the relaxation model above has no term for it.

**Path notation.**

- `getAgentStory` concatenates room names and bracketed sensor names with no separator
  (l.308-311). This is ambiguous for multi-letter room names; the original's map has none.
- The default keeps the notation and documents it (`docs/DESIGN.md`, "Path notation").

**Aliased status rows.** `getAgentStoryStatuses` aliases `S[i+1] = S[i]` on a deactivation
(l.329). This is harmless, because a deactivation phase writes nothing
(`original-inventory.md` §2).
