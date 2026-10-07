# Cross-check: the original code vs. the paper examples

> This note records our initial reverse-engineering pass ("phase 1" in file names and
> cross-references): the original code run on every paper example, before the new
> implementation existed. The final bug list is `original-bugs.md`; it supersedes the bug
> sections here (§4–§6). §7 lists what that pass still lacked and how it was resolved.

We ran every paper example that the original code can express through the live original
(`tools/reference/run.sh`, canonical flags `-XX:hashCode=2`, Temurin 8u504, original commit
`55f57f8`). We then compared three answers for each one:

- what the paper says, where it says anything;
- the fixture value, which we extracted and then re-verified independently;
- a third, separate region-model oracle written for this note (Appendix B). It uses no
  graph `G`, no subgraphs and no DP.

The recorded outputs are in `tests/fixtures/golden/paper_cases.json`: 98 cases in the
golden format, plus a per-case `crosscheck` block. Sections 5 and 6 cover the inventory
bugs. We re-ran each one through the harness and appended the observed outputs to
`docs/notes/original-inventory.md` under "Confirmed by harness".

**Bottom line**

- The oracle reproduces all 26 fixture verdicts.
- The original agrees with the fixtures on 20 of 26 verdicts. It also reproduces all 26
  paper subgraphs, using 40 harness calls (STAR Figs. 4, 6(a), 6(b)/7; ICRA Fig. 4 and every `G_j`), up to two
  documented representation differences.
- All 6 disagreements are errors in the **original**. None is in the paper or the
  fixtures:
  - 5 come from a new finding, **B8**: the original accepts a story that is used up
    before the last recording. Both papers forbid this.
  - 1 is the known **B5**: no check that a single-agent history is well formed.

## 1. How to reproduce

`tools/reference/regen_golden.sh` rebuilds this set together with the other golden files:

- `tools/reference/gen_cases.py` writes the case file (`gen_paper`, the set `paper_cases`;
  Appendix A);
- the canonical run and the six alternative hash settings go through
  `tools/reference/run.sh` like every other set;
- `tools/reference/make_golden.py --paper-crosscheck` merges the results and adds the
  per-case `crosscheck` block with `tools/reference/paper_crosscheck.py`, which uses the
  independent oracle `tools/reference/oracle.py` (Appendices B and C).

The same run also writes `tests/fixtures/golden/javahash.json`
(`tools/reference/javahash_probe.py`).

Self-checks, all of which passed:

- A second canonical run is byte-identical.
- `--burn-seed 7` leaves the canonical output unchanged.
- `--force-generic` gives identical output, so the generic builder equals
  `getBasicGame()`.
- Each of the 98 cases run in its own JVM equals the batch run.

What ran:

- All 12 STAR cases on `star_fig2`. Single-agent cases go through `validateAgentStory`,
  `getAgentStoryStatuses` and `getAgentStory`; multi-agent cases through
  `validateAgentStoryMulti`.
- All 12 ICRA cases, through the single-agent ops. All ICRA cases are single-agent, and
  the strict `consistent` value is Problem 1.
- `icra_fig2` is built by the harness's generic builder. ICRA Problems 2–4, Alg. 2 and
  the automata have no original code and were skipped. So was `star_fig1`, which has no
  rooms, so the original cannot attach `SV` to a room.
- Every subgraph the papers draw. Each one calls `getSubGraph`, `getReachableSubgraph`
  or `getSubGraphMulti` with the paper's start vertex, goals and active occupancy set.

The start vertex is always `start="story"`, i.e. `updateStartingVertex(p1)`, as the
applet does. This is the original's form of the paper's `V_I = {p1}`.

## 2. Verdict table (Problem 1)

Column meanings:

- **paper**: the verdict the paper itself states (`-` = derived case, nothing stated).
- **fixture**: the independently verified value.
- **oracle**: this note's region model under the fixture semantics (strict room visits,
  x inside `p_n` at `t_f`).
- **oracle-E**: the same model, but the story may be used up before `t_f`.
- **original**: `validateAgentStory(Multi)`.

| case | mode | paper | fixture | oracle | oracle-E | original | who is wrong |
|---|---|---|---|---|---|---|---|
| star_eq1_eq2_single | S | F | F | F | F | F | – |
| star_eq1_eq3_multi | M | T | T | T | T | T | – |
| star_eq1_eq3_single | S | - | F | F | F | **T** | original (B5) |
| star_eq1_eq2_multi | M | - | F | F | F | F | – |
| star_single_feasible_original_history | S | - | T | T | T | T | – |
| star_multi_infeasible_original_history | M | - | F | F | F | F | – |
| star_multi_deactivation_order_infeasible | M | - | F | F | F | F | – |
| star_multi_wait_in_unlabelled_component | M | - | T | T | T | T | – |
| star_multi_overlap_C_B | M | - | T | T | T | T | – |
| star_single_same_beam_twice | S | - | T | T | T | T | – |
| star_single_repeated_room | S | - | T | T | T | T | – |
| star_single_story_length_one | S | - | T | T | T | T | – |
| star_single_end_must_be_last_room | S | - | F | F | T | **T** | original (B8) |
| star_single_role_split_random_find | S | - | T | T | T | T | – |
| icra_fig1_ABAC | S | T | T | T | T | T | – |
| icra_sec3a_ABDEC_b1b3o2o2b4 | S | not stated | F | F | F | F | – |
| icra_sec5a_ABDEC_b1b2o2o2b4 | S | not stated | F | F | F | F | – |
| icra_derived_problem2_case2_counterexample | S | - | F | F | T | **T** | original (B8) |
| icra_derived_alg2_literal_DAD | S | - | F | F | F | F | – |
| icra_derived_problem4_length_claim_counterexample | S | - | F | F | T | **T** | original (B8) |
| icra_derived_problem4_length_claim_counterexample_all_rooms | S | - | F | F | F | F | – |
| icra_derived_alg1_single_room_story | S | - | T | T | T | T | – |
| icra_derived_repeat_semantics_a | S | - | F | F | T | **T** | original (B8) |
| icra_derived_repeat_semantics_b | S | - | T | T | T | T | – |
| icra_derived_piecewise_acceptance_AA | S | - | F | F | T | **T** | original (B8) |
| icra_derived_fig1_swapped_occupancy | S | - | F | F | F | F | – |

Under the strict reading of room visits, the original matches the fixtures on both ICRA
ABDEC examples. Like the fixtures, it forbids entering a room out of story order: rooms
not yet due are simply not in `vpSet`. The fixture's alternative key
`consistent_if_unreported_room_visits_allowed` would be true for both, and the original
never takes that reading.

Where the literal paper algorithms fail, the original is right in four places:

- STAR Alg. 3 / ICRA Alg. 1 return false for a one-room story. The original returns true.
- The paper needs room self-loops for `AA`; the original handles it with
  `areNeighbors(v, v)` (`Algorithms.java:162`).
- The paper's start/goal role-split problem does not arise, because the original keeps
  one state per goal vertex (`star_single_same_beam_twice`,
  `star_single_role_split_random_find`).
- ICRA Alg. 2's missing `L(i-1,·)` has no counterpart in the original
  (`icra_derived_alg2_literal_DAD` gives false).

### Paths (`getAgentStory`) and statuses

We checked each returned path with the oracle's path checker (`check_path`, Appendix B).
Every path on a fixture-consistent case is a valid walk:

| case | path | paper/fixture |
|---|---|---|
| star_single_feasible_original_history | `A[b1u]C[o1][o2]B[b2r]AC` | valid, ends in p_n |
| star_single_same_beam_twice | `A[b2l]B[b2r]A` | valid |
| star_single_repeated_room | `AA` | valid |
| star_single_story_length_one | `A` | valid |
| star_single_role_split_random_find | `CA[b2l]B[b2r][o1]C` | valid |
| icra_fig1_ABAC | `A[b2l]B[o2][o1]AC` | valid; it is the Fig. 1 path |
| icra_derived_alg1_single_room_story | `A` | valid |
| icra_derived_repeat_semantics_b | `A[b11][b12]A` | valid |

The 5 B8 cases return paths that end outside `p_n`: `AA[b2l]`, `A[b11]B[b31]`,
`CCA[b2l]`, `A[b11][b12]` and `AA[b11]`. The B5 case returns
`A[b1u]C[o1][o2]B[b2r]AC`, a walk that is impossible for eq. (3) (o1 is still active
while o2 activates).

On every fixture-inconsistent input that the original also rejects (6 cases),
`getAgentStoryStatuses` and `getAgentStory` throw AIOOBE at `Algorithms.java:365` (B4).

The original's DP states for eq. (1)+(2) (`stdout`) are:

```
A[b1d,b1u]C[b1u]BAC
A[o1]C[o1]BAC
A[b2r]C[b2r]BAC
A[o2]C[o2]B[o2]AC
ACBAC
```

They follow the paper's subproblem narrative on pp. 400–401:

- C can be done in G1, G21 or G3.
- B is done in G4/G5.
- Nothing survives the next A.

## 3. Subgraph table (STAR Algs. 1, 2, 4; ICRA §III-A)

| paper object | original call | result |
|---|---|---|
| STAR Fig. 4 G1, G21, G22, G4, G5 | `getSubGraph(s, Cp=story rooms, VG=SENSORVERTICES)` | identical V and E |
| STAR Fig. 4 G3 (VG = {b2l, b2r}) | same | identical to the **drawn** G3. The original drops the unreachable goal b2r, which literal Alg. 2 l.11 would add as an isolated vertex (STAR erratum already in star.json) |
| STAR Fig. 6(a) | `getReachableSubgraph(A, {A,B,C,o1,o2}, {b2r,b2l})` | identical |
| STAR Fig. 7, all 9 subgraphs (Fig. 6(b) = G0_4) | `getSubGraphMulti(s, O, story rooms, VG)` | identical to literal Alg. 4 once the active `o` vertices and their edges are removed. The original keeps `o` (Alg. 4 l.5 removes it; the inventory explains why keeping it is needed). G0_4 includes `b2l–b2r`, which Fig. 6(b) as drawn omits (paper erratum; the original agrees with literal Alg. 4) |
| ICRA Fig. 4(a), 4(b) and `G_j`, j ∈ {1,2,3,5,6}, both ABDEC histories (24 calls) | `getSubGraph` once per possible start | the union over starts whose subgraph reaches a goal (or all starts in the last interval) equals the paper's subgraph exactly. ICRA draws one subgraph per interval; the original builds one per start state |

## 4. Disagreements: who is wrong

### D1 = B8 (new): the original accepts stories used up before the last recording (5 cases)

**Minimal reproduction.** Map `star_fig2`, single mode, story `A`, history `b1`. The
applet gives the same result: story text `A`, sensors `b1`.

- `validateAgentStory` → **true**; `getAgentStory` → `A[b1u]` (or `A[b1d]` under other
  hash orders).
- Correct: **false**. x starts in A and must cross b1, which leaves it in R2 or R1. To be
  "in A at `t_f`" it would have to re-enter A, and that unreported visit is not in the
  story. If x is instead allowed to end outside A, the answer is true; that is
  oracle-E.
- The applet shows the same bug from mouse clicks: golden
  `applet_session_click_random05` (story C, sensors `b1`) displays "Valid story. A possible
  path: C[b1d]".

**Cause.** `Algorithms.java:196-199`. In the final phase (`i == r.length`), the loop
`for (l = j; ...)` returns true at once when `l == S.length-1`, including at `l == j`. So
any state `(s, n)` left over from an earlier phase, in which the story was finished
*before* the last recording, is accepted. That `s` is a beam side or occupancy vertex,
not `p_n`.

**Why the paper and fixtures are right.**

- STAR Alg. 3 l.21 accepts only `(p_n, m+1)`, the copy of `p_n` in the last subgraph.
- STAR §2.1's example story ends "arrived room C, at which point I stopped".
- ICRA §II-A states it outright: "we require that agent x starts from p1 and ends in pn".

**Minimal fix (corrected mode only).** Change the same final-phase test in all three
single-agent routines: `validateAgentStory` (l.197), `getAgentStory` (l.256) and
`getAgentStoryStatuses` (l.346). Accept only when `l > j`, which means `p_n` itself is
entered after the last recording. With `m == 0` this holds automatically for `n >= 1`.
The multi-agent test at l.420 needs no change, because x may wait in `p_n`.
We checked this on a patched private copy of `Algorithms.java`; the original checkout was
not touched. With the patch, `validateAgentStory` agrees with the strict oracle on all 221
valid single-agent inputs (202 `star_random_single` + 19 paper cases). The only
disagreements left are on invalid histories (B5).

**Scale.**

- Of the golden `star_random_single` inputs that are valid single-agent histories, the
  original matches oracle-E on **202 of 202** and the strict oracle on 199. The 3 B8 cases
  are s0007, s0115 and s0166.
- In the paper set, 5 of the 20 single-agent verdicts are affected.
- Multi-agent verdicts are unaffected: x may always wait in `p_n`. On all 238 golden
  multi-agent cases with valid histories, the original agrees with the strict oracle.

**Consequence for the inventory.** Its claim of "0 differences in 40 920 cases" for
`validateAgentStory` holds only because its brute force used the same "may end early"
convention. Under the paper's semantics, the original is not correct on valid input.

### D2 = B5: the original accepts an impossible single-agent history (1 case)

**Reproduction.** `star_eq1_eq3_single`: story ACBAC, history eq. (3)
`b1 o1A o2A b2 o2D o1D`, single mode.

- Original: **true**, path `A[b1u]C[o1][o2]B[b2r]AC`.
- Correct: **false**. Under STAR §2.2, a single agent cannot activate o2 while o1 is
  still active.
- Paper Alg. 3 as printed would also accept, because it never checks well-formedness
  (star.json `paper_algorithm.with_fixes_no_history_check = true`). So the paper's
  *algorithm* shares the bug, and its *problem definition* does not.
- The fixture is right.
- The applet's single mode cannot produce such a history: it always pairs `o` tokens.

**Scale in golden data.**

- Single-agent: the original accepts 51 of the 118 invalid histories in
  `star_random_single`, and the strict oracle says false for all 51. For 47 of them the
  answer is false under either end convention. The other 4 end with an activation that
  is never matched, so x would end inside `o`.
- Multi-agent: the original accepts 62 invalid histories in `star_random_multi` where
  the strict oracle says false: 40 have a deactivation with no activation, 18 a double
  activation and 4 a beam `D`.

### Things that are *not* wrong

- No map edge, fixture value or paper figure was contradicted by the original.
- The only paper-vs-original differences in subgraphs are the two paper errata already in
  `star.json`: G3's extra goal, and Fig. 6(b)'s missing `b2l–b2r`. On both, the original
  sides with the drawing and with literal Alg. 4 respectively.

## 5. Confirmed bugs, ranked by severity

Every row was run live through the harness under the canonical flags. "Alt" rows also ran
under `hashCode=5` (burn seeds 1, 2, 3) and `hashCode=3` (burn seed 0). The full outputs
are in the inventory's "Confirmed by harness" section.

| # | bug | minimal reproduction (map star_fig2) | observed | correct | reachable from the applet? |
|---|---|---|---|---|---|
| 1 | **B8 (new)**: story may end before the last recording | single; `A`; `b1` | `true`, path `A[b1u]` | false | yes (`C`/`b1` gives "Valid story … C[b1d]") |
| 2 | **B1**: multi-agent false negative after a deactivation | multi; `BC`; `o1A b2 o1D` | `false` (without `o1D`: `true`) | true | yes (multi, `BC`, `o1,b2,o1`) |
| 3 | **B3**: invalid path, one crossing too many | single; `AC`; `b1 b1 b1` (consistent under every convention) | `A[b1d][b1u][b1d][b1u]C`, 4 crossings for 3 recordings | e.g. `A[b1u][b1d][b1u]C` | yes |
| 4 | **B5**: no well-formedness check on histories | single; `A`; `o1D` / `o1A o2A` / `o1A b1`; paper eq. (3) as single | `true` ×4 | false | single mode: no; multi mode: no (the toggle always starts with A) |
| 5 | **B4**: AIOOBE instead of `null` | `getAgentStory`, paper eq. (1)+(2) single | AIOOBE @ `Algorithms.java:365` | null / "inconsistent" | no (guarded by the verdict) |
| 6 | **B6**: sticky applet failure | Run `AD`/`b1`, then `AC`/`b1` | NPE @ `:207` twice; the second run's story is `A,null,A,C`; after Reset: valid | second run valid | yes |
| 6b | B6: `DA` corrupts SV | Run `DA`, then `A` | NPE @ `DetectiveGame.java:30`, then `:23`; after Reset OK | – | yes |
| 6c | B6: tokens silently dropped | sensors `" b1"`, `"B1,b3,b1"` | parsed `[]` / `[b1]` | error message | yes |
| 7 | **B2**: path depends on hash order | single; `A`; `b1` | `A[b1u]` canonical, `A[b1d]` alt | either (both valid) | yes |
| 8 | B7: edge-id collision | 2 rooms + 65 537 occupancy vertices; edges `A–o65537`, `B–o1` (both id 262149) | `B–o1` silently dropped; story `B`, `o1A o1D` → `false` (control map: `true`, but see below) | true | no |
| 8b | B7: beam `D` counted as a crossing | single; `AC`; `b1D` | `true` | invalid input | no |
| 8c | B7: `DetectiveGame` start fixed at A | `start=fixed`; `CA`; `[]` | `false` | true | no |
| 9 | UI: the b2 hit strip overlaps room B | click canvas (312,91), i.e. world (624,182) | hit `B` (at (312,93): `b2l`) | b2l | yes |

Also confirmed: under `MultiFeasible`, `validateAgentStoryMulti` prints 36 `GP.dump()`
blocks under the canonical setting and 37, 37 and 35 under the alternatives. This is
control flow, not just print order.

Correction (row 8): the control "story `B` → `true`" is itself B8 (x must re-enter `B`);
`original-bugs.md` uses story `BB` as the clean B7a reproduction. That note is the final bug
list and supersedes §4–§6 here.

Two latent B7 items were **not** confirmed, because no harness input reaches them:

- the NPE when neighbour sets and `edgeMap` diverge;
- the dependence of `flip`/`vertexNameMap` on unique vertex names.

## 6. Inventory claims refuted or qualified

- **B3, "under insertion order the code gives the right answer"**: wrong in general. The
  inventory's `AC`/`b1,b1` example is fine under `hashCode=2`, but `A`/`b1,b1` gives
  `A[b1d][b1u][b1d]` and `AC`/`b1×3` gives `A[b1d][b1u][b1d][b1u]C`.
- **B3 scale**: 24 invalid paths in the golden data (12 `star_random_single`, 7
  `random_maps`, 5 applet). Of the 17 on the STAR map, 13 come from valid single-agent
  histories. Every invalid path fails only on the crossing count. None is a walk with the
  right count that is physically impossible.
- **B4 example**: story `B` with an empty history crashes only with `start=fixed`. With
  the applet start it returns `"B"`.
- **§2 "agreement with brute force: 0 differences"**: this holds only because the
  inventory's brute force let the story end early (B8, §4).

## 7. What the initial pass still lacked, and what has been resolved

**Reproducibility and file layout**

- **M1 (resolved).** `paper_cases.json` was not produced by `regen_golden.sh`, which starts
  by deleting `tests/fixtures/golden/*.json`. Now `gen_cases.py` has the set (`gen_paper`),
  `paper_crosscheck.py` and `oracle.py` live in `tools/reference/`, `make_golden.py` adds the
  `crosscheck` block and lists the set in `summary.json`, and
  `tests/fixtures/golden/README.md` documents `paper_case` and `crosscheck`.
- **M2 (resolved).** `tests/fixtures/README.md` now documents the top-level layout of every
  fixture file (including the bare map object in `paper/star_fig2_geometry.json`), the
  harness keys and the `consistent_if_*` switches.
- **M3.** No fixture records the end-of-story convention per case. The
  `compat="original"` mode needs it (B8), as does the corrected default. We suggest adding
  `consistent_if_story_may_end_before_tf` to every single-agent paper case;
  `paper_cases.json` already carries it as `crosscheck.oracle_consistent_if_story_may_end_before_tf`.

**Gaps in examples and coverage**

- **M4 (resolved).** Paper examples with no fixture and no original counterpart:
  - STAR p. 402, rooms overlapping occupancy sensors (s ⊊ p, p ⊊ s, partial overlap):
    prose only, with no worked example; out of scope (the map model keeps rooms and
    occupancy regions disjoint, `paper-examples-star.md` §10 item 6).
  - STAR Fig. 1 (`star_fig1` has no rooms, so the original cannot run it): its free
    components are a fixture expectation, checked by `tests/test_oracle_self.py` and
    `tests/test_maps.py`.
  - ICRA Problems 2–4 (no original code): besides the paper fixtures, the independent
    brute-force oracle `tests/oracles/brute.py` checks `validate_intervals`,
    `shortest_superstory` and `closest_story` on random inputs
    (`tests/test_oracle_random.py`). The Problem 2 interval model remains a judgement call
    (`paper-examples-icra.md` §1, `docs/DESIGN.md` "Semantics").
- **M5 (resolved).** Non-GUI functions with no direct harness coverage, as of the initial
  pass. All of them except `FileHelper` and the drawing call `LineSegment.drawText` are
  ported in `cyber_detectives.compat` (`docs/DESIGN.md`, "What is ported from the original,
  and what is not", which excludes `FileHelper` as applet plumbing and all drawing). The
  list:
  - All 13 `common/util/FileHelper` methods. They need a decision: port them, or exclude
    them as applet plumbing.
  - Primitives that are only exercised indirectly:
    - `Edge.getEdgeId`, beyond our one-off collision repro, which is not in the golden data;
    - `Graph.addEdgelessVertex` overwrite and aliasing;
    - `Graph.getEdgeBetweenVertices` returning null;
    - the one-sided `Vertex.removeNeighbor` and the raw `Vertex.addNeighbors`;
    - `Sensor.getType`.
  - GUI-side logic, never run headless:
    - `Environment.initialize` treating non-axis beams as horizontal;
    - `LineSegment.drawText` and `charAt(2)`;
    - `Environment.create*` throwing an NPE on missing names.

**Determinism**

- **M6 (resolved).** Under `hashCode=2`, a `HashSet` with 11 or more members becomes a tree
  bin, whose order is deterministic but not insertion order (`rand019`, `rand020` and
  `rand024` record such neighbour sets). `compat.javahash` emulates Java 8 `HashMap`
  including tree bins, pinned against the real JDK by `tests/fixtures/golden/javahash.json`.
  Caveats that remain: `order_dependent: false` only means no change was seen under six
  settings, and exception messages differ between JDK 8 and JDK 11.

**Size and paths**

- **M7.** File sizes and paths:
  - No file exceeds 1.5 MB; the largest is `star_random_multi.json` at 1.27 MB.
    `paper_cases.json` is 148 KB.
  - No absolute local paths appear anywhere under the repository.
  - `regen_golden.sh` sets `PYTHONDONTWRITEBYTECODE`, so no `__pycache__` appears next to
    the tools.

## Appendix A: `gen_paper` (case generator for `paper_cases`)

Now `gen_paper` in `tools/reference/gen_cases.py`. It reads only the two paper fixtures and
uses no randomness. It emits:

1. the Problem 1 ops for every paper case on a map the original can express (`star_fig2`,
   `icra_fig1`, `icra_fig2`): `validateAgentStory`, `getAgentStoryStatuses` and
   `getAgentStory` in single mode, `validateAgentStoryMulti` in multi mode;
2. STAR Fig. 4 (`getSubGraph` for G1, G21, G22, G3, G4, G5 on eq. (1)+(2));
3. STAR Fig. 6(a) (`getReachableSubgraph`, Alg. 4 line 2 before clique-ification);
4. STAR Fig. 6(b) / Fig. 7 (`getSubGraphMulti` for the nine Alg. 4 subgraphs);
5. ICRA Fig. 4(a)/(b) and every `G_j` of both ABDEC histories, one `getSubGraph` call per
   possible start vertex.

Each case names its source in `paper_case`.

## Appendix B: independent region-model oracle

Now `tools/reference/oracle.py`. It uses no connectivity graph and no DP. It runs a
breadth-first search over (place, story index, history index), where a place is a room, a free
component `R*` or an occupancy region. The free components are the ones the fixtures read off
the figures; the script asserts that their cliques equal each map's edges.

- **Single mode:** each recording must be made by x. A beam is a crossing between the
  components of its two sides. `oA`/`oD` enter or leave `o` from or to an adjacent component.
- **Multi mode:** x may cross a beam only at one of that beam's recordings, and may pass
  through `o` only while `o` is active. x may not be inside `o` when `o` deactivates.
- **Options:** `end` is strict (x is inside `p_n` at `t_f`) or anywhere. `visits` is strict
  (every room entry is the next story element) or loose.

`check_path` replays an original `getAgentStory` string at component level and reports whether
it is a valid walk and whether it ends in `p_n`.

## Appendix C: the `crosscheck` block

Now `tools/reference/paper_crosscheck.py`, called by `make_golden.py --paper-crosscheck`. For
every case of `paper_cases.json` it adds a `crosscheck` block; `expected` stays exactly what
the original does.

- **Validate, status and path ops:** `paper_states` (the paper's own verdict, if any),
  `fixture_consistent`, `oracle_consistent` (strict end, strict visits),
  `oracle_consistent_if_story_may_end_before_tf`, `history_valid_for_single_agent` (single
  mode), `original`, `agrees`, and `who_is_wrong` when the original disagrees (the B8 or B5
  explanation). A `getAgentStory` result adds `path_valid_walk`, `path_ends_in_p_n` and
  `path_check`; a crash is reported with its exception and frame (B4). The script asserts
  that the oracle equals the fixture on every case.
- **STAR subgraphs:** the paper's vertex and edge sets and `match`. Fig. 7 cases compare after
  removing the active occupancy vertices the original keeps (Alg. 4 l.5 removes them), and
  `G0_4` records whether it matches Fig. 6(b) as drawn (it does not: the drawing lacks
  `b2l–b2r`).
- **ICRA `G_j`:** the union of the original's per-start subgraphs that reach a goal (all
  starts in the last interval), compared with the paper's single subgraph per interval, and
  with Fig. 4(a)/(b) where drawn.
