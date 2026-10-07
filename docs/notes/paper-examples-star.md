# Worked examples from the STAR/WAFR paper

Paper: J. Yu, S. M. LaValle, "Cyber Detectives: Determining When Robots or People
Misbehave", WAFR 2010, *Algorithmic Foundations of Robotics IX*, STAR 68, pp. 391–407.
Page numbers below are the printed proceedings pages (391–407).

Fixture: [`tests/fixtures/paper/star.json`](../../tests/fixtures/paper/star.json)

- 2 maps: `star_fig2` (Fig. 2 / Fig. 3(a)) and `star_fig1` (Fig. 1).
- 14 cases:
  - 2 come straight from the paper: eq. (1)+(2) single-agent and eq. (1)+(3) multi-agent. These
    carry every intermediate result the paper prints (Figs. 4–7, the DP narrative).
  - 12 are derived cases, each labelled `derived`. They pin down semantics and expose bugs in
    the pseudocode.

## Contents

1. [How the expectations were checked](#1-how-the-expectations-were-checked)
2. [Maps](#2-maps)
3. [Semantics we pin down](#3-semantics-we-pin-down)
4. [Single-agent example: eq. (1) + eq. (2)](#4-single-agent-example-eq-1--eq-2)
5. [Multi-agent example: eq. (1) + eq. (3)](#5-multi-agent-example-eq-1--eq-3)
6. [Algorithms 1–4: transcription and issues](#6-algorithms-14-transcription-and-issues)
7. [Complexity claims](#7-complexity-claims)
8. [Derived cases](#8-derived-cases)
9. [Errata list](#9-errata-list)
10. [Open ambiguities for implementers](#10-open-ambiguities-for-implementers)

## 1. How the expectations were checked

We used neither the original Java code nor its outputs. We read every figure, equation and
algorithm off 200–600 dpi renders of the PDF pages. We did not rely on `pdftotext`, which
drops `≠`, primes, the `|` separators in the DP displays and all subscripts.

We wrote two models independently of each other, in private verification scripts that are not
part of this repository. Besides the two models, those scripts computed the Fig. 4/6/7
sub-graphs and a random cross-check, built `star.json` while asserting every transcription
against the computation, checked the directed arcs of Fig. 5, compared the two end-of-story
conventions, and counted disagreements with each refinement of Algorithm 3 removed. The
published tests now cover the same ground (`tests/test_subgraphs.py`,
`tests/test_oracle_self.py`).

1. **Physical brute force.** This model never uses the graph `G`. Free components R1–R4 were
   read off Fig. 2 by tracing every doorway:
   - R1 = {A, C, b1d, o1}
   - R2 = {A, b1u, b2l, o1}
   - R3 = {o1, o2}
   - R4 = {B, b2r, o2}

   The agent is a token that sits in a component, a room or an occupancy region. We run a
   time-expanded search over the history with state = (location, number of story elements
   matched, label of how the current interval was entered). The movement rules are in §3.
2. **Paper construction.**
   - `G` is a clique on the vertices that touch each free component.
   - Sub-graphs come from Algorithms 1/2 (single agent) and Algorithm 4 (multi-agent).
   - The chained graph `G_s` uses the direction rules of Fig. 5. Its DP is Algorithm 3, with the
     index bugs from §6 fixed.

Cross-check, single agent, Fig. 2 map: 6000 random cases (stories of length 1–6 over {A,B,C}
and well-formed single-agent histories of up to 7 recordings; 802 of them consistent).

- The paper construction agrees with the brute force on all 6000 cases.
- This holds only with two refinements that the paper leaves implicit (§6, items A3-v and A3-vi).
- Without the start/goal role split there are 23 disagreements; without the room self-loops, 293; without both, 317.

## 2. Maps

### Fig. 2 / Fig. 3(a): `star_fig2`

`V = {A, B, C, o1, o2, b1u, b1d, b2l, b2r}` (p. 397). `b1` is horizontal, so its sides are
`u`/`d`. `b2` is vertical, so its sides are `l`/`r`.

Edges read off Fig. 3(a), checked at 500 dpi:

```
A-C  A-b1u  A-b1d  A-b2l  A-o1  C-b1d  C-o1  b1u-b2l  b1u-o1  b1d-o1  b2l-o1  o1-o2
B-b2r  B-o2  b2r-o2                                                   (15 edges)
```

This edge set equals the cliques of R1–R4 exactly.

Fig. 3(b) region graph (13 edges), also identical to what the components predict:

- R1–{C, A, b1, o1}
- R2–{A, b1, o1, b2}
- R3–{o1, o2}
- R4–{b2, B, o2}

Comparison with the original `DetectiveGame.getBasicGame()` (we transcribed its neighbour lists
by hand, without running it):

- The neighbour lists are symmetric.
- The edge set is **identical** to Fig. 3(a), except for one extra edge `SV–A`. `SV` is a virtual
  start vertex that `updateStartingVertex` re-attaches to the first story room.
- No edge is missing and none is spurious.
- Beam side order in the original is `b1 = [b1u, b1d]` and `b2 = [b2r, b2l]`. The fixture keeps
  that order.

This agrees with the geometry-based derivation in
[`map-geometry.md`](map-geometry.md).

### Fig. 1: `star_fig1`

- One occupancy region `o` with doorways a (left), b (top) and c (right).
- One vertical beam at the top, just right of doorway b.
- Doorways a and b open into the same free component L. Doorway c opens into Rt. Rt also holds
  the space right of the beam and the small unlabelled room, which is not in `C_p`.
- No rooms, so `G` = {o–bl, o–br}.
- The vertex names `o`, `b`, `bl` and `br` are ours.
- The figure carries no consistency example. It only illustrates that an occupancy recording
  cannot tell which doorway the agent used (p. 395).

## 3. Semantics we pin down

| question | our reading | support in the paper |
|---|---|---|
| Where does the agent start? | Inside `p_1` at `t_0`. | Alg. 3 lines 1 and 17 (`V_I ← {p_1}`, `V_s ← {(p_1,1)}`); p. 400: "Since agent x starts in A, we are done with p_1". |
| What does "at a beam side vertex" mean, e.g. `b1u`? | The agent is in the free component adjacent to that side, at the beam. As a **goal** of a sub-graph, `b1u` means "about to cross from the upper side". As a **start**, `b1d` means "just crossed, now on the lower side". A crossing joins goal `b1u` of one sub-graph to start `b1d` of the next. | p. 397–398 ("right before t1 ... the agent must be at either b1u or b1d"); Fig. 5 dashed arcs `G1.b1u → G21.b1d` and `G1.b1d → G22.b1u`; p. 400 path `A_G1 b1u b1d C_G21`. |
| May the agent pass through a room of `C_p` that is not the next story element? | **No.** Every entry into a room of `C_p` is a visit and must match the next story element. Rooms outside `C_p` count as free space (Fig. 1's right room). | §2.1, p. 394: "for every p ∈ C_p, x has accounted for all its visits to p in **p**". p. 400: "The copy of C in G22 is not directly reachable from A in G1, passing only vertices from sensors". p. 401: "For the copy of C in G3, since it must pass A to reach B, this subproblem dies". |
| What is `C_p` when a derived story leaves out a map room (e.g. `C, B`)? | The map's rooms A, B, C. | The paper defines `C_p` as the distinct elements of **p** (§2.1). The Fig. 2 caption treats only "rooms in agent x's story" as obstacles. Read literally, story `C, B` makes A free space, which merges R1 and R2 and changes `G`. Every case was re-checked under both readings and no expected answer changes, but an implementation has to pick one. |
| Are repeated consecutive entries (`A, A`) allowed? | **Yes**: the agent leaves the room and re-enters it. | Fig. 5 draws a self-loop on every room vertex of every sub-graph. The text never discusses it. The original applet also appends a room again when it is clicked again (see `map-geometry.md`). |
| Must the agent be in `p_n` at `t_f`? | **Yes.** After the last recording it must be in (or reach) `p_n` and stay there. | §2.3: **p** and **r** "span the same time interval [t_0, t_f]". Alg. 3 line 21 accepts only `(p_n, m+1)`, the copy of `p_n` in the last sub-graph. Our paper-algorithm implementation matched the brute force under this convention on all 6000 random cases. Under "p_n is just the last room visited", 419 of 4000 random single-agent cases change answer; none of the multi-agent cases did. See case `star_single_end_must_be_last_room`. |
| Single agent and occupancy events | Every recording is made by x. Activation means x enters `o` from an adjacent free component, and x stays inside until the deactivation, when it leaves to an adjacent component. Activation intervals of a valid single-agent history never overlap. | §2.2 "Observation History for a Single Agent"; §4: "every sensor recording is triggered by that agent". |
| Multi-agent and occupancy events | Occupancy events only toggle whether `o` can be passed. x may be inside `o` only while `o` is active, and must be out by the deactivation. | §5, p. 402: "recordings from occupancy sensors should only be treated as events that change the connectivity ... whether agent x is the agent that triggered the activation/deactivation ... is not relevant". |
| Multi-agent and beams | x may cross a beam only at a recorded event of that beam, at most once per event, and need not cross at any of them. | §5: "it may trigger any subsequence of sensor recordings"; §2.2: all event times are distinct. |
| Initial sensor state | All occupancy sensors are inactive at `t_0`. | Implicit: the histories start with activations. |

## 4. Single-agent example: eq. (1) + eq. (2)

Story `p = (A, C, B, A, C)`. History
`r = ((b1,t1),(o1,t2),(o1,t3),(b2,t4),(o2,t5),(o2,t6))`.

**Paper and brute force agree: inconsistent.**

The paper's reason (p. 396) checks out. B lies in R4. After B, the only recordings left are
o2's activation and deactivation, which lead to R3 (a dead end, because o1 no longer fires) or
back to R4.

### Fig. 4: sub-graphs

Green = start, red = goal.

| name | interval | start | goals | V | E |
|---|---|---|---|---|---|
| G1 | [t0,t1) | A | b1u, b1d | A, C, b1u, b1d | A–C, A–b1u, A–b1d, C–b1d |
| G21 | (t1,t2) | b1d | o1 | A, C, b1d, o1 | A–C, A–b1d, A–o1, C–b1d, C–o1, b1d–o1 |
| G22 | (t1,t2) | b1u | o1 | A, C, b1u, o1 | A–C, A–b1u, A–o1, b1u–o1, C–o1 |
| G3 | (t3,t4) | o1 | b2l | A, C, b2l, o1 | A–C, A–b2l, A–o1, C–o1, b2l–o1 |
| G4 | (t4,t5) | b2r | o2 | B, b2r, o2 | B–b2r, B–o2, b2r–o2 |
| G5 | (t6,tf] | o2 | – | B, o2 | B–o2 |

Algorithms 1/2 reproduce every row exactly, with one exception: line 11 (`V' ← V' ∪ V_G`),
taken literally, adds the unreachable goal `b2r` to G3 as an isolated vertex. The figure leaves
it out. No sub-graph exists for (t2,t3) or (t5,t6), because the agent is inside o1 or o2.

### Fig. 5: directed composite graph (partial)

| sub-graph | arcs |
|---|---|
| G1 | A↻, C↻, A⇄C, A→b1u, A→b1d, C→b1d |
| G21 | A↻, C↻, A⇄C, b1d→A, b1d→C, b1d→o1, A→o1, C→o1 |
| G22 | A↻, C↻, A⇄C, b1u→A, b1u→o1, A→o1, C→o1 |
| G3 | A↻, C↻, A⇄C, o1→A, o1→C, o1→b2l, A→b2l |

Dashed crossing arcs: `G1.b1u→G21.b1d`, `G1.b1d→G22.b1u`, `G21.o1→G3.o1`, `G22.o1→G3.o1`, and
`G3.b2l→` (to G4).

The rule behind the drawing:

- Edges between rooms point both ways, and every room has a self-loop.
- A sensor start vertex has out-arcs only.
- A goal vertex has in-arcs only.

Our construction regenerates exactly these arcs from Algorithm 2's output (`star_composite` in
`subgraphs.py`, tested in `tests/test_subgraphs.py`).

### DP narrative (p. 400–401)

The `|` separators are visible in the page image; `pdftotext` drops them.

- After `p_2 = C`: `A_G1 C_G1 | BAC`, `A_G1 b1u b1d C_G21 | BAC`, `A_G1 b1d b1u o1 C_G3 | BAC`.
  The copy of C in G22 cannot be reached.
- After `p_3 = B`: `A_G1 b1u b1d C_G21 o1 b2l b2r B_G4 | AC` and
  `A_G1 b1u b1d C_G21 o1 b2l b2r o2 B_G5 | AC`. The paper prints **`b21`** (digit one) here
  and in "reach B from b21 in G3". It must be `b2l`: no vertex b21 exists.
- After `p_4 = A`: no subproblem survives, so the result is false.

The brute force records which copy (interval and entry) each visit can happen in. It gives
exactly {G1, G21, G3} for C, {G4, G5} for B and ∅ for the second A. Our DP over the
paper construction gives the same.

Minor point: the "collapse" of the first two subproblems happens when they enter G3 at `o1`.
They reach different copies of o1 (G22's and G21's), which are both chained into G3's start `o1`.

## 5. Multi-agent example: eq. (1) + eq. (3)

History `r = ((b1,t1),(o1,t2),(o2,t3),(b2,t4),(o2,t5),(o1,t6))`.

**Paper and brute force agree: consistent.**

Witness from the brute force: x crosses no beam (other agents trigger b1 and b2).

- During [t0,t3): A → R1 → C.
- During (t3,t5), with o1 and o2 both active: R1 → o1 → R3 → o2 → R4 → B → R4 → o2 → R3 → o1 →
  R1 → A → R1 → C.

### Fig. 6

**(a) G⁰₄**: start A, goals b2l and b2r.

- V = {A, B, C, o1, o2, b2l, b2r}.
- E = A–C, A–b2l, A–o1, C–o1, b2l–o1, o1–o2, B–b2r, B–o2, b2r–o2.
- Algorithm 2 with `V_C = C_p ∪ {o1, o2} ∪ {A}` reproduces it exactly.

**(b) After clique-ification**: V = {A, B, C, b2l, b2r}.

- The figure draws 9 edges: A–B, A–C, A–b2l, A–b2r, B–C, B–b2l, B–b2r, C–b2l, C–b2r.
- **Algorithm 4 taken literally gives K5**, which adds **b2l–b2r**. After o1 is removed, both
  b2l and b2r are neighbours of o2.
- The missing edge joins two goal vertices, so it can never be used. The difference is
  cosmetic, but tests that compare edge sets must choose one of the two versions. The fixture
  records both.

**Caption.** It says G⁰₄ is the sub-graph "during (t4,t5)". By the text (built for `(b2,t4)`
from start A, and named like G⁰₁, which is "built from t0 to t1"), it covers **[t0,t4)**.

### Fig. 7: sketch of `G_s`

The paper's subscript is the index of the recording that closes the sub-graph. In the
superscript, `0` means start A, and `k1`/`k2` mean the first/second side of the beam recorded
at index k.

There are **9 sub-graphs**: 1 built at b1, 3 at b2 and 5 at o2's deactivation.

| name | start | goals |
|---|---|---|
| G⁰₁ | A | b1u, b1d |
| G⁰₄ | A | b2r, b2l |
| G¹¹₄ | b1u | b2r, b2l |
| G¹²₄ | b1d | b2r, b2l |
| G⁰₅ | A | – |
| G¹¹₅ | b1u | – |
| G¹²₅ | b1d | – |
| G⁴¹₅ | b2l | – |
| G⁴²₅ | b2r | – |

Entry arrows from A lead into G⁰₁, G⁰₄ and G⁰₅.

The 10 dashed arcs:

- `G⁰₁.b1d→G¹¹₄.b1u` and `G⁰₁.b1d→G¹¹₅.b1u`
- `G⁰₁.b1u→G¹²₄.b1d` and `G⁰₁.b1u→G¹²₅.b1d`
- `{G¹¹₄, G¹²₄, G⁰₄}.b2r→G⁴¹₅.b2l`
- `{G¹¹₄, G¹²₄, G⁰₄}.b2l→G⁴²₅.b2r`

G⁰₁ equals Fig. 4's G1, as the text says. The fixture records the vertex and edge sets that
Algorithm 4 computes for all nine. The paper draws only G⁰₄.

A DP over this chained graph accepts. The whole story fits inside G⁰₅, which clique-ifies to
the triangle A–B–C.

The paper says `(o1,t6)` can be ignored because no beam recording follows it. This is
correct: everything after t5 could have been done in (t4,t5) with a superset of the
connectivity, and the agent then waits in the last room.

## 6. Algorithms 1–4: transcription and issues

Transcribed from the page images. `pdftotext` loses `≠` and the primes. Line numbers are the
paper's.

```
Algorithm 1  GETSUBGRAPH
Input: G=(V,E), the start vertex s, C_p, and goal vertices V_G
Output: G'=(V',E'), the part of G that is reachable from s
 1: V_C ← C_p ∪ {s}
 2: return GETREACHABLESUBGRAPH(G, s, V_C, V_G)

Algorithm 2  GETREACHABLESUBGRAPH
Input: G=(V,E), s, V_C, V_G          Output: G'=(V',E')
 1: for all edges (v_i,v_j) ∈ E such that v_i, v_j ∈ V_C do
 2:     add (v_i,v_j) to E'          // V' is also updated.
 3: end for
 4: G' ← CONNECTEDCOMPONENT(G', s)
 5: if V_G is not empty then
 6:     for all v_i, v_j such that v_i ∈ V', v_j ∈ V_G do
 7:         if (v_i,v_j) ∈ E then
 8:             add (v_i,v_j) to E'
 9:         end if
10:     end for
11:     V' ← V' ∪ V_G
12: end if
13: return G'

Algorithm 3  VALIDATEAGENTSTORY
Input: G, p=(p_1,...,p_n), r=(r_1,...,r_m)
Output: true if p is consistent with r, false otherwise
 1: V_I ← {p_1}
 2: for j = 1 to m+1 do
 3:     initialize V_G as an empty set
 4:     if ISDEACTIVATION(r_j) then
 5:         continue
 6:     end if
 7:     if (j ≠ m+1) then
 8:         V_G ← SENSORVERTICES(r_i)
 9:     else
10:         empty V_G
11:     end if
12:     G_j ← GETSUBGRAPH(G, V_I, C_p, V_G)
13:     V_I ← V_G
14: end for
15: G_s ← CHAIN(G_1, ..., G_{m+1})
16: initialize V_s, V_s' as empty sets of two tuples
17: V_s ← {(p_1, 1)}          // A two tuple is a vertex of G_s
18: for i = 2 to n do
19:     for j = 1 to m+1 do
20:         if (p_i, j) adjacent to (p_{i-1}, k) ∈ V_s for some k ≤ j then
21:             if i == n && j == m+1 then
22:                 return true
23:             end if
24:             add (p_i, j) to V_s'
25:         end if
26:     end for
27:     V_s ← V_s'; empty V_s'
28: end for
29: return false

Algorithm 4  GETSUBGRAPHMULTI
Input: G=(V,E), the start vertex s, C_p, the active occupancy sensors O, and goal vertices V_G.
Output: G'=(V',E'), the part of G that is reachable from s.
 1: V_C ← C_p ∪ O ∪ {s}
 2: G' ← GETREACHABLESUBGRAPH(G, s, V_C, V_G)
 3: for all o ∈ (O ∩ V') do
 4:     add to E' an edge between each pair of o's neighbors
 5:     remove o from G'
 6: end for
 7: return G'
```

**Correction to a premise we were given.** Line 7 of Algorithm 3 reads `if (j ≠ m+1)` in
the PDF, at 500 dpi. The condition is **not** inverted. `pdftotext` drops the `≠` and turns
it into `j = m+1`. The other issues in Algorithm 3 are real.

### Issues in Algorithm 3

- **A3-i, line 8.** `SENSORVERTICES(r_i)` uses `i`, which is unbound in this loop. It must be
  `r_j`.
- **A3-ii, lines 2 and 4.** The loop runs to `j = m+1`, where `r_{m+1}` does not exist, so
  `ISDEACTIVATION(r_{m+1})` is undefined. Line 4 has to be guarded with `j ≤ m`.
- **A3-iii, line 12.** `GETSUBGRAPH` takes a single start vertex `s` (Alg. 1), but it is called
  with the set `V_I`. When `|V_I| = 2` (after a beam), the result has to be one part per start
  (G21 and G22 in Fig. 4). A single `G_j` cannot hold both.
  - Line 13 (`V_I ← V_G`) gives the goal sides as the next starts. That set happens to be
    symmetric, but which goal leads to which start (the opposite side for a beam, the same
    vertex for an occupancy sensor) is left to `CHAIN`, "based on sensor crossings".
- **A3-iv, numbering.** Deactivations are skipped with `continue`, so the indices `j` that get
  a `G_j` have gaps. For eq. (2) they are 1, 2, 4, 5, 7. Fig. 4 numbers the same sub-graphs
  G1–G5, and `CHAIN(G_1, …, G_{m+1})` and the `j = 1..m+1` loop at line 19 range over
  undefined graphs.
  - Also, `(p_i, j)` does not say which part of `G_j` is meant (G21 or G22). For the
    clique-type `G` this is harmless, because a room's forward reachability is the same in
    either part, but it should be stated.
- **A3-v, line 20.** "adjacent" must mean *reachable in `G_s` along a directed path whose
  interior vertices are all sensor vertices*. The narrative paths on p. 400, e.g.
  `A_G1 b1d b1u o1 C_G3`, are not single edges.
  - With the direction rule of Fig. 5 (sensor start: out-arcs only; goal: in-arcs only),
    consider a sub-graph whose **start is also one of its goals**. This happens when the same
    beam fires twice in a row, or the same occupancy sensor is entered twice in a row. Then
    the walk start → room → same vertex as goal is lost.
  - The start and goal roles need separate copies. Without them our DP disagrees with the
    brute force on 23 of the 6000 random cases. See `star_single_same_beam_twice`
    (`A, B, A` with `b2, b2`: consistent, rejected without the split).
  - The two copies must also be joined by a direct arc from the start copy to the goal copy.
    The agent may stay in the start's free component and cross straight back, or re-enter the
    same occupancy region, without visiting any room. `G` has no self-loops, so separate
    copies alone do not give this walk. The implementation behind the 23/6000 count already
    has this arc in both variants; "without the split" removes only the room → goal
    in-arcs. Reproduction, all single-agent and all consistent by the physical model:
    `(C, A)` with `o1 A, o1 D, o1 A, o1 D`; `(A, C)` with `b1, b1`;
    `(C, A, A, A, A, C)` with `b2, b2`. Separate copies without the stay arc reject all
    three. In an independent re-check (3000 fresh random single-agent cases, explicit walk
    enumeration), separate copies without the stay arc gave 50 wrong answers, more than the
    11 from having no split at all. With the stay arc and the self-loops, 0 were wrong.
- **A3-vi, line 20.** Repeated consecutive story entries are accepted only through the room
  self-loops of Fig. 5. Pure adjacency in `G` has no self-loops. See `star_single_repeated_room`.
- **A3-vii, lines 18 and 29.** When `n = 1` the `i`-loop never runs, so the algorithm returns
  false even when the history is consistent (e.g. empty `r`, story `(A)`). See
  `star_single_story_length_one`.
- **A3-viii, no well-formedness check.** A single-agent history must not have overlapping
  occupancy intervals (§2.2). Algorithm 3 never checks this. Fed eq. (3) as a single-agent
  history, it skips the deactivations and walks from start o1 to goal o2 along the G-edge
  o1–o2, so it accepts. The correct answer is false. See `star_eq1_eq3_single`.
- **A3-ix, line 21.** The acceptance test returns from inside the loop, so the result depends
  only on reaching `(p_n, m+1)`. This fixes the end-of-story convention of §3.

### Issues in Algorithm 2

- **A2-i, line 11.** It adds every goal to `V'`, even unreachable ones. G3 for eq. (2) then
  gets an isolated `b2r`, which Fig. 4/5 omit.
- **A2-ii, lines 1–4.** `V'` is built only from edges ("V' is also updated"). If `s` has no
  neighbour in `V_C`, then `s ∉ V'` and `CONNECTEDCOMPONENT(G', s)` is undefined. The case
  where `s` reaches a goal directly is still meaningful, so `s` must be added explicitly.
- **A2-iii, specification.** The prose says "if V_G is not empty, then a path from s must also
  end at vertices of V_G". Algorithm 2 does not prune vertices that cannot reach a goal. In the
  STAR example every vertex can reach a goal, so the figures cannot tell the two versions apart.

### Issues in Algorithm 4

- **A4-i.** Clique-ification also joins goal vertices to each other (Fig. 6(b) vs the literal
  output; see §5) and can join a start to a goal on the same beam. Both are harmless if goals
  are terminal.
- **A4-ii.** The neighbour sets change as each `o` is removed, so line 4 uses the current `G'`.
  The final result does not depend on the order.
- **A4-iii.** The paper gives no pseudocode for the multi-agent `G_s` and its DP ("we omit the
  pesudocode", p. 404). See §10.

## 7. Complexity claims

Transcribed from the page images.

- `BUILDCONNECTIVITYGRAPH`: O(n_w²), where n_w is the input size of W_free (p. 399).
- Connected components take time linear in n_w [16]. `GETREACHABLESUBGRAPH` is dominated by
  lines 6–7: O(|V_G| n_w lg n_w) (p. 399).
- Naive search through the sub-graphs takes exponential time, because each beam doubles the
  chaining (G21/G22) (p. 399).
- G_s contains at most 2(m+1) copies of G, so each element of C_p ∪ C_s appears O(m) times.
  There are O(m) subproblems per p_i (p. 399–400).
- Algorithm 3 (p. 402):
  - O(m) calls to `GETSUBGRAPH`, taking O(m n_w lg n_w) in total because |V_G| ≤ 2.
  - The rest takes O(n·m lg n_w).
  - Total: **O(m(n + n_w) lg n_w)**.
  - Note: this total leaves out the O(n_w²) for `BUILDCONNECTIVITYGRAPH`. That is consistent
    only if G counts as input, as in Alg. 3's input line.
- Multi-agent (p. 404):
  - There are up to O(2^m) subsets of recordings that x might have triggered.
  - Up to O(m²) sub-graphs, because each recording may create O(m) of them. Fig. 7 has
    1 + 3 + 5 = 9.
  - Search takes **O(n m² log m_w)**.
  - `GETSUBGRAPHMULTI` takes **O(m_w³)**, dominated by lines 3–6.
  - Total: **O(m²(n log m_w + m_w³))**.
  - `m_w` is never defined. It is almost certainly `n_w`. Note also the mix of `lg` and `log`.
- The 2(m+1) bound and the 1 + 3 + 5 count of Fig. 7 both check out on the example.

We did not re-derive the bounds formally.

## 8. Derived cases

These are not in the paper. All carry `"source": "derived: ..."`, and their expectations come
only from the brute force. Histories copied from the original are used as **inputs only**.

| id | mode | consistent | why it is there |
|---|---|---|---|
| `star_eq1_eq3_single` | single | false | eq. (3) is not a valid single-agent history; Algorithm 3 has no check for this (A3-viii) |
| `star_eq1_eq2_multi` | multi | false | eq. (2) stays inconsistent with more agents |
| `star_single_feasible_original_history` | single | true | history of the original's `getSingleFeasibleGame` |
| `star_multi_infeasible_original_history` | multi | false | history of the original's `getMultiInfeasibleGame` |
| `star_multi_deactivation_order_infeasible` | multi | false | story `C,B`; o2 is active only before o1, so merging active sets across a deactivation is unsound |
| `star_multi_wait_in_unlabelled_component` | multi | true | story `C,B`; x waits in R3, which has no vertex in G, across o1's deactivation |
| `star_multi_overlap_C_B` | multi | true | control case for the two rows above |
| `star_single_same_beam_twice` | single | true | start = goal in one sub-graph (A3-v) |
| `star_single_repeated_room` | single | true | `A,A` (A3-vi) |
| `star_single_story_length_one` | single | true | n = 1 (A3-vii) |
| `star_single_end_must_be_last_room` | single | false | true under the "last room visited" convention (A3-ix) |
| `star_single_role_split_random_find` | single | true | a random-search instance of A3-v |

For the single-agent derived cases, the field `expected.paper_algorithm` records what our
implementation of Algorithms 1–3 returns with and without each refinement.

## 9. Errata list

| # | where | problem |
|---|---|---|
| E1 | p. 401, 3 places | `b_{21}` (digit one) should be `b_{2l}`: "reach B from b21 in G3" and both `p_3 = B` paths. |
| E2 | p. 401, Alg. 3 line 8 | `SENSORVERTICES(r_i)` should be `r_j`. |
| E3 | p. 401, Alg. 3 lines 2/4/15/19 | `r_{m+1}` is undefined. Skipped deactivations leave gaps in the `G_j` indices, so the indices do not match Fig. 4's G1–G5. |
| E4 | p. 401, Alg. 3 line 12 | A set `V_I` is passed where Algorithm 1 takes a single start vertex. |
| E5 | p. 401, Alg. 3 | Returns false when n = 1. |
| E6 | p. 401, Alg. 3 | No check that a single-agent history is well formed. Eq. (3) as single-agent input is accepted. |
| E7 | p. 401, Alg. 3 line 20 | "adjacent" really means reachable through sensor vertices. The start-equals-goal case is not handled. Repeated rooms depend on self-loops that only Fig. 5 shows. |
| E8 | p. 398, Alg. 2 line 11 | Adds unreachable goals (`b2r` in G3), unlike Figs. 4/5. If `s` has no `V_C` neighbour, `s ∉ V'`. |
| E9 | p. 403, Fig. 6(b) vs Alg. 4 | Algorithm 4 adds `b2l–b2r`; the figure omits it. |
| E10 | p. 403, Fig. 6 caption | "G⁰₄, during (t4,t5)" should be [t0,t4) (start A, built for `(b2,t4)`). |
| E11 | p. 404 | `m_w` is undefined (read `n_w`). `lg` and `log` are mixed. |
| E12 | p. 402 | The total O(m(n+n_w) lg n_w) leaves out the O(n_w²) of `BUILDCONNECTIVITYGRAPH`. |
| E13 | typos | "mulitple" (Fig. 6 caption); "pesudocode" (p. 403 and p. 404); "suproblems" and "the the copy" (p. 402); "an sensor" (p. 402); "This limit the time complexity" (p. 404); "partition of the search problem" (p. 400); "the later two" for "the latter two" (p. 397). |
| E14 | p. 400 narrative | "the first two subproblems collapse" at o1, but they reach different copies of o1 (G22's and G21's) and only merge at G3's start. Cosmetic. |
| E15 | §5 (p. 402–404) | No pseudocode for the multi-agent `G_s`/DP. How sub-graphs that end at a deactivation hand over to later sub-graphs is not specified (§10). |
| E16 | §3 (p. 398) | "they can be visited only once as the start vertex or the goal vertex" is ambiguous when one vertex is both the start and a goal of the same sub-graph (A3-v). |

**Not errors, despite what the text extraction suggests:**

- Alg. 3 line 7 is `j ≠ m+1`.
- The paper's claims for eq. (2) (inconsistent) and eq. (3) (consistent) are both correct.
- The Fig. 3(a) edge list matches Fig. 2 exactly.

## 10. Open ambiguities for implementers

1. **Multi-agent deactivations (most important).** §5 builds sub-graphs at beam recordings and
   at deactivations. In the example, the only deactivation that matters is the final one,
   which works as the terminal sub-graph. The paper never says how a sub-graph that ends at a
   deactivation connects to sub-graphs after it. Two cases show what a correct construction
   must handle:
   - `star_multi_deactivation_order_infeasible`: one sub-graph whose active set is the union
     over an interval containing a deactivation wrongly accepts.
   - `star_multi_wait_in_unlabelled_component`: the agent may wait across a deactivation in a
     free component that holds no vertex of G (R3 touches only o1 and o2). The hand-over has
     to be expressible in G, for example through the deactivated sensor's vertex, or the
     construction must be component-based.
2. **End convention.** We adopt "inside `p_n` at `t_f`" (Alg. 3 line 21). The alternative is
   recorded where it changes the answer.
3. **Repeated consecutive entries.** We allow them (Fig. 5 self-loops). The text is silent.
4. **Start/goal role split** (A3-v), with a direct start-copy → goal-copy arc. It is
   required for correctness, and the paper never states it.
5. **Fig. 6(b) edge set.** Choose either the drawn 9 edges or Algorithm 4's literal K5. Both
   are in the fixture.
6. **Overlap of rooms and sensors** (p. 402: "s ⊊ p", "p ⊊ s", partial overlap). The paper
   only sketches this, and it has no worked example. The STAR example assumes C_p and C_s are
   disjoint.
