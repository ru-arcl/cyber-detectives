# ICRA 2011 paper: worked examples, semantics, and errata

Paper: J. Yu, S. M. LaValle, "Story Validation and Approximate Path Inference with a Sparse
Network of Heterogeneous Sensors", ICRA 2011, pp. 4980–4985 (pages cited as p. 4980–4985).
Fixture: `tests/fixtures/paper/icra.json` (2 maps, 12 cases, plus transcribed figures).

All figures were read from 500–1000 dpi renderings of the PDF, not from extracted text. The
extracted text drops primes, `≠` and subscripts. Every expectation in the fixture was recomputed
independently (see "How we verified"). We did not use the original Java code for anything here.

## 1. Semantics we pinned down

| Question | Our reading | Support in the papers |
|---|---|---|
| What "being at" a beam-side vertex means | The agent is in the free component (Fig. 2's `R_k`) that touches that side of the beam. Moving from `b_k1` in `G_j` to `b_k2` in `G_{j+1}` is a crossing. | §III-A p. 4982: "b11, b12 in Fig. 4(a) are connected to b12, b11 in Fig. 4(b)". §II-A: the agent "must pass from one side of the beam to the other side". |
| Passing through a room of C_p that is not the next story element | **Not allowed.** Every entry into a room of C_p is a story element. | STAR §2.1: "x has accounted for all its visits to p in p". ICRA never repeats this sentence (§II-A only says x "recalls in p locations on its path correctly"). But in ICRA's own NFAs (Fig. 5) every move into a room reads that room's symbol, so all visits get reported. The composite automaton built that way has exactly the language of our strict model (checked on all words of length ≤ 6). The fixture also records the result under the opposite reading (`*_if_unreported_room_visits_allowed`). The reading matters: under it, both ABDEC examples become consistent. |
| Repeated consecutive entries (`AA`) | Allowed. The agent leaves the room and comes back in. | Fig. 5 has a self-loop labelled with the room on every room state. |
| Start and end | The agent starts inside `p1` at `t0` (that counts as the first story element) and is inside `pn` at `tf`, after the last recording. | §II-A p. 4981: "we require that agent x starts from p1 and ends in pn". Alg. 1 accepts only `(pn, m+1)`. |
| Single vs multi-agent | Every ICRA problem (1–4) is single-agent, so every recording comes from x. In the simplified history an occupancy sensor appears twice: activation, then deactivation. Between those two recordings the agent is inside the region and nothing else can happen. | Problems 1–4 all say "Let there be a single agent". STAR §2.2 covers the single-agent history. The multi-agent case appears only as earlier work and as future work. |
| C_p for derived stories | The map's rooms are fixed: Fig. 2 caption says C_p = {A,…,E}. | Strictly, §II-A defines C_p as the set of distinct elements of p. Our derived stories use subsets of the rooms. We still treat the unused rooms as rooms (reportable visits), not as free space. |

**Problem 3 anchoring.** §II-A says the agent starts at `p1` and ends at `pn`. §V-A, however,
writes `p' = ω1 A ω2 B ω3 D ω4 E ω5 C ω6`, which lets `p'` start before `p1` and end after `pn`.
Alg. 2 also allows a non-empty ω1. The fixture records both readings: `problem3_*` (free) and
`problem3_anchored_*`. For every paper example the two answers coincide.

**Problem 2 model.** The story constrains room entries only
inside `[t0,tf]`, and the sensors record only inside `[t0',tf']`. Before the first boundary and
after the last one, the agent's position is free. At `t0` the agent must be in `p1`. At `tf` it
must be in `pn` with the whole story used. At `tf'` all recordings must have been explained. The
agent may not be inside an occupancy region at `t0'` or `tf'`. Because there is a single agent,
recordings that fall outside `[t0,tf]` must still be explained by x.

## 2. Maps

### Fig. 1 (p. 4980) = STAR Fig. 2

We compared the 1000-dpi renderings doorway by doorway. Every wall gap of ICRA Fig. 1 appears in
STAR Fig. 2, so the topology is the same and only the drawing differs (ICRA adds the agent's path
and drops the R labels). The free components, named as in STAR Fig. 2, are:

- R1 = {A, C, b1d, o1}
- R2 = {A, b1u, o1, b2l}
- R3 = {o1, o2}
- R4 = {b2r, B, o2}

Expanding each component into a clique gives 15 edges. This equals the edge list we transcribed
from STAR Fig. 3(a). In the fixture this map is `icra_fig1`. Its beam-side names and order are
copied from the README's `star_fig2` example, because ICRA does not name the sides.

**The path drawn in Fig. 1.** The agent's path goes A → R2 → crosses b2 → R4 → B → R4 → o2 →
R3 → o1 → exits o1 upward into R2 → enters A through its right doorway → leaves A through its
bottom doorway into R1 → C. It records `b2 o2 o2 o1 o1`, matching the caption "b2, o2, o1"
written in simplified notation.

### Fig. 2 / Fig. 3 (p. 4982)

We read the doorways off Fig. 2:

| Component | Vertices |
|---|---|
| R1 | A, B |
| R2 | b12, B, o1, b31, C |
| R3 | A, b11, o1, b21 |
| R4 | o1, b32, D, b41 |
| R5 | b42, C, b51 |
| R6 | b22, o2, D |
| R7 | o2, E, D, b52, o3 |

R3 is labelled inside the small room at the top, but it also contains the column above b1, the
corridor from b1 to b2, and the corridor between that room and o2. R7 contains the column left
of E, the room between E and o3, and the bottom corridor to the right of b5. o2 has doorways into
both R6 (bottom) and R7 (right side).

**Fig. 3 drawn in another style.** Fig. 3 does not draw beam sides as vertices. It uses
*unlabeled junction vertices* (the free components) and draws each beam as a double red bar on
an edge, with `b_k1`/`b_k2` written on either side. We mapped the junctions as follows: the top
junction is R3, the left one is R2, the middle one is R4, the lower one is R5, the upper right
one is R6, the right one is R7, and the direct edge A–B is R1. Under this mapping Fig. 3 is
exactly the table above. Expanding the cliques gives **38 edges**. The pair o2–D appears in both
R6 and R7.

## 3. Figures 4–6

**Fig. 4(a)** is G1 for first recording b1, starting at A. It has vertices {A, B, C, b11, b12} and
edges A–b11, A–B, B–b12, B–C, C–b12.

**Fig. 4(b)** is G2 between b1 and b3. It has vertices {A, B, C, b11, b12, b31} and edges A–b11,
A–B, B–b12, B–C, B–b31, C–b12, C–b31, b12–b31.

Both are reproduced exactly by this definition of `SUBG(G, start, goal)`: keep the vertices that
lie on some walk from the start set to the goal set whose intermediate vertices are rooms. Sides
of other beams (b21, b42, b51, …) are pruned even though they are physically reachable. Starting
from any room instead of A gives the same G1. The fixture also lists every G_j for both example
histories, under `figures.subgraphs_G_j`.

**Fig. 5(a)** (M1) matches the construction exactly:

- start → A, B, C on the symbol of the room entered;
- a self-loop on each room;
- A↔B and B↔C;
- A→b11, B→b12, C→b12 on ε;
- b11 and b12 are accepting.

**Fig. 5(b)** (M2) has three defects:

1. b12→B is labelled **ε** but should read **B**.
2. b12→C is labelled **ε** but should read **C**. Fig. 5(a) uses the entered room as the label,
   and so does b11→A in Fig. 5(b) itself. With ε, the agent would be in B or C without reporting
   it.
3. The transition b12→b31 (ε) is **missing**. Fig. 4(b) has the edge b12–b31: the agent crosses
   b1 downward and then crosses b3 without entering a room. The ε-labels in defects 1–2 hide
   this omission by accident.

**Fig. 6.** The links are M1.b11 → M2.b12 and M1.b12 → M2.b11 (both ε), and M2.b31 → M3 drawn
dotted. That dotted target is b32 of M3. The caption's history is b1 b3 o2 o2 b4.

**Two things the construction leaves out, and we had to add:**

1. *The interval between an occupancy activation and its deactivation.* We set M_j = {o}, because
   the agent is inside o.
2. *Consecutive recordings of the same sensor* (`o D`, `o A` or `b b`). Here the start and goal
   vertices of M_j coincide. If one NFA state serves as both, which is what vertex-as-state
   suggests, the automaton accepts extra words. Example: history `b1 b1 o1 o1 o1 o1 b2` gets
   AAADBD, AABDCD, …, because the agent "re-enters" o1 without a recording. Start and goal need
   separate copies. With that change, the composite language equals the region model's.

## 4. The worked examples (all single-agent, map `icra_fig2` unless noted)

| Case id | Story / history | P1 strict | P1 if unreported visits allowed | P3 shortest p' | P4 min edits (optimal p') |
|---|---|---|---|---|---|
| `icra_fig1_ABAC` (map `icra_fig1`) | ABAC / b2 o2 o2 o1 o1 | **true** | true | 4, ABAC | 0 |
| `icra_sec3a_ABDEC_b1b3o2o2b4` (§III-A, Fig. 6) | ABDEC / b1 b3 o2 o2 b4 | **false** | true | 6, ABDEDC only | 1: ABDDC, ABDEDC |
| `icra_sec5a_ABDEC_b1b2o2o2b4` (§V-A) | ABDEC / b1 b2 o2 o2 b4 | **false** | true | 6, ABDEDC only | 1: ABDC, ABDCC, ABDDC, ABDEDC |

In both ABDEC rows the anchored and free Problem-3 answers agree.

The paper states none of these answers. The ICRA Fig. 1 caption only says the path is possible,
which we confirm.

**Why both ABDEC examples are inconsistent.** E touches only R7. From R7 the agent can reach b4
(b41 in R4, b42 in R5) only by entering D, which would be a second, unreported visit to D, or by
crossing b5 or entering o3, neither of which was recorded. C must come after E and after b4, so a
second D is unavoidable.

The two histories do differ in one way:

- With **b1 b2 …**, deleting E works (ABDC). After crossing b2 the agent is in R6, next to o2, so
  it can activate and deactivate o2 before entering D.
- With **b1 b3 …**, the agent arrives in R4. It must enter D to reach o2 and enter D again to get
  back to b4, so ABDC fails but ABDDC works.

**Problem 2 on the paper examples** (our model, fixture key `problem2_by_interval_case`):

| History | Case 1 | Case 2 | Case 3 | Case 4 | Case 5 | Case 6 |
|---|---|---|---|---|---|---|
| s = b1 b3 o2 o2 b4 | T | T | F | F | T | T |
| s = b1 b2 o2 o2 b4 | T | T | F | T | T | T |

The paper's case-2 procedure returns false for both histories (see E2 below).

## 5. Problem 2: the six interval cases (p. 4982–4983, from the image)

Unprimed times are the story's interval `[t0, tf]`. Primed times are the sensors' interval
`[t0', tf']`.

1. t0 < tf < t0' < tf'
2. t0 < t0' < tf < tf'
3. t0' < t0 < tf < tf'
4. t0 < t0' < tf' < tf
5. t0' < t0 < tf' < tf
6. t0' < tf' < t0 < tf

The paper's handling:

- **Cases 1 and 6:** "check whether an empty story is consistent with s".
- **Case 2:** "run VALIDATEAGENTSTORY n times … story (p_i..p_n) and history s".
- **Case 3:** "allow the search to start at (p1, j) for all applicable j's; if pn is ever reached,
  report consistent".
- **Cases 4 and 5:** "along the same lines".

## 6. Algorithms, transcribed from the page images

**Algorithm 1 VALIDATEAGENTSTORY** (p. 4982)
```
Input: W_free, p = (p1..pn), s = (s1..sm)     Output: true iff p consistent with s
 1: G <- BUILDCONNGRAPH(W_free)
 2: for j = 1 to m+1
 3:   G_j <- SUBG(G, j == 1 ? p1 : s_{j-1}, j = m+1 ? nil : s_j)      [sic: "=" not "=="]
 4: G_s <- CHAIN(G_1, ..., G_{m+1})
 5: initialize V_s, V_s' as empty sets of two tuples
 6: V_s <- {(p1, 1)}            // a two tuple is a vertex of G_s
 7: for i = 2 to n
 8:   for j = 1 to m+1
 9:     if (p_i, j) adjacent to (p_{i-1}, k) in V_s for some k <= j
10:       if i == n && j == m+1
11:         return true
12:       add (p_i, j) to V_s'
13:   V_s <- V_s'; empty V_s'
14: return false
```

**Algorithm 2 VALIDATEPARTIALSTORY** (p. 4984)
```
Input: p = (p1..pn, F), M, M_1..M_{m+1}     Output: true if M accepts some p' >= p, false otherwise
 1: initialize 2D array L as array of inf's
 2: L(0,1) <- 0
 3: for i = 1 to n+1
 4:   for j = 1 to m+1
 5:     l <- inf
 6:     for each start state S_k of M_j
 7:       t <- { min_{j'} SHORTESTLEN((p_{i-1}, j'), S_k) }
 8:       l <- min{ l, t + SHORTESTLEN(S_k, (p_i, j)) }
 9:     L(i,j) <- min{ l, SHORTESTLEN((p_{i-1}, j), (p_i, j)) }
10: if L(n+1, m+1) != inf
11:   return true
12: return false
```
**Line 10 really is `≠ ∞` in the PDF.** The extracted text loses the `≠`, which makes the line
look inverted. It is not inverted.

**§V-B recursion** (p. 4984):

`D(p_i, T) = min_S { D(p_{i-1}, S) + D(p_i, S, T) }`

Here `D(p_i, S, T)` is the shortest edit distance from state S to state T on symbol `p_i`. The
staged form is

`min_S { D(p_{i-1},S) + min{ min_{S_j}{ D(p_i,S,S_j) + D(ε,S_j,T) }, min_{S_j}{ D(ε,S,S_j) + D(p_i,S_j,T) } } }`

and the paper regroups it as

`min{ min_{S_j}{ min_S{ D(p_{i-1},S) + D(p_i,S,S_j) } + D(ε,S_j,T) }, min_{S_j}{ min_S{ D(p_{i-1},S) + D(ε,S,S_j) } + D(p_i,S_j,T) } }`.

Notation: T is an internal state of M_j, {S_j} are the start states of M_j (at most two), and
S ∈ M_{j−1}.

**Complexity claims as printed:**

| Claim | Location |
|---|---|
| Algorithm 1 search O(nm lg n_w); preprocessing O(m n_w lg n_w + n_w²) | §III-A |
| Cases 1/6 search O(m lg n_w); cases 2–5 O(nm lg n_w) | §III-B |
| Problem 3 (Alg. 2) O(n m n_w²), using Dijkstra inside each M_k in O(n_w²) | §V-A |
| General D(p_i,S,T): O(Q³) = O(m³ m_w³); staged: O(m m_w³) per p_i | §V-B |
| Transducer U: O(m · m_w · m_w lg m_w) per i, O(n m m_w² lg m_w) total | §V-B |
| Versus O(m³ n_w⁴) preprocessing in [30] and O(n m m_w² lg(m m_w)) in [1] | §V-B |
| "time linear in both the length of the story and the length of the observation history" | Abstract, §VI |

## 7. Errors, typos, ambiguities and inconsistencies in the paper

Errors we confirmed with a computed counterexample are marked **[E]**. Ambiguities are marked
**[A]** and typos **[T]**.

1. **[E] Alg. 2 lines 7 and 9 never add `L(i−1,·)`.** L(0,1) is written and never read, so
   L(i,j) holds only the length of the last segment. The algorithm then returns true whenever
   some (p_n, j') reaches F, even if (p_n, j') is unreachable after p_1..p_{n−1}.
   - *Counterexample* `icra_derived_alg2_literal_DAD`: story DAD with the §III-A history. No
     super-story exists, yet the printed algorithm returns true. The Fig. 1 map with swapped
     occupancy order (`icra_derived_fig1_swapped_occupancy`) also fails.
   - *Fix:* `t ← min_{j'<j} { L(i−1,j') + SHORTESTLEN((p_{i−1},j'), S_k) }` and
     `L(i,j) ← min{ l, L(i−1,j) + SHORTESTLEN((p_{i−1},j),(p_i,j)) }`. This corrected form
     matched ground truth on 1500 random instances. The printed form failed on 359.
   - *Further gaps:*
     - The range of `j'` is not given (it should be `j' < j`).
     - `(p_0, j')` is undefined (it should be the start state of M with j' = 1).
     - When p_{i−1} = p_i, SHORTESTLEN must require a non-empty string; otherwise one visit
       would cover two story elements.
     - The algorithm returns only a boolean, although Problem 3 asks for a shortest p'. The
       answer would be L(n+1, m+1) plus backtracking.
2. **[E] Problem 2, case 2 procedure (p. 4983).** Running VALIDATEAGENTSTORY on (p_i..p_n)
   against the whole of s has two flaws: it forces the agent to be inside p_n when the *last*
   recording is done, and it forces the agent to be inside p_i at t0'. But the story ends at
   tf < tf', and the agent may be anywhere at t0'.
   - *Counterexample* `icra_derived_problem2_case2_counterexample`: story AB, history b1 b3,
     Fig. 2 map. The story is consistent in case 2 (e.g. the agent crosses b1 into B before tf,
     and b3 is triggered after tf; witness `|t0|A|t0'|[b11]B|tf|[b31]|tf'|`), but the procedure returns false.
   - The procedure also returns false for both ABDEC examples, where our case-2 model says true.
3. **[A] Problem 2, cases 1/6 and 3.**
   - "Empty story consistent with s" is not defined for VALIDATEAGENTSTORY, which needs p1.
   - In case 3, "applicable j's" is not defined. Because there is a single agent, the prefix
     s_1..s_{j−1} must be realizable by a walk that ends in p1.
   - In case 3, "if pn is ever reached" leaves out that the rest of s must be realizable from pn.
   - Cases 4 and 5 are only sketched ("along the same lines").
   - The Problem 2 statement says the intervals "overlap", yet cases 1 and 6 are disjoint.
4. **[E] §V-B length claim.** The paper says "a p', accepted by M and closest to p, cannot have
   length more than max{n, n'}". This is false.
   - *Counterexample* `icra_derived_problem4_length_claim_counterexample`: Fig. 1 map, history b2,
     story CCA. Here L(M) = (A|C)\*A B+ ∪ B+ A (A|C)\*, so n' = 2. The unique closest story is
     CCAB (1 edit, length 4 > 3). In 3000 random instances the claim failed 40 times.
     This counterexample relies on our C_p reading (the map's rooms): CCAB uses B, which is not
     a letter of CCA, so under the literal §II-A definition (C_p = letters of p) CCAB is not an
     admissible p' and Problem 4 has no answer at all for this input.
   - *Counterexample independent of the C_p reading*
     `icra_derived_problem4_length_claim_counterexample_all_rooms`: Fig. 1 map, history
     b2 b1 b2, story ACB (C_p = {A, B, C} under both readings). Every accepted word starts with B
     and the shortest is BAB, so n' = 3. The unique closest story is BACB (1 edit, length
     4 > max{3, 3}). Found by our exhaustive search: Fig. 1 stories of length 3–5 that
     use all three rooms, against every history of up to 3 sensor events.
   - The bound "at most max{n, n'} edits" is correct (substitute, then insert or delete). It
     held in all 3000 instances.
   - The existence claim (an answer exists whenever L(M) ≠ ∅) is trivially true when p' may use
     every room of the map. Under the literal C_p (letters of p) it can fail, as the CCA input
     shows.
5. **[E] §IV, "consistent iff p can be partitioned into P_1..P_{m+1} such that P_i is accepted by
   M_j".** The index is a typo (P_j). Even with that fixed, the statement is only necessary, not
   sufficient: the state where M_j accepts must chain to the state where M_{j+1} starts (the
   crossing flips the beam side).
   - *Counterexample* `icra_derived_piecewise_acceptance_AA`: history b1, story AA. M1 accepts "A"
     only ending at b11, and M2 accepts "A" only starting at b11, but the crossing leads to b12.
     So AA is inconsistent.
   - The composite automaton M, also in §IV, is the correct criterion.
6. **[E] Fig. 5(b) defects** (§3 above): b12→B and b12→C are labelled ε instead of B and C, and
   the ε transition b12→b31 is missing.
7. **[A] What the NFA construction leaves out:** occupancy activation→deactivation intervals, and
   consecutive recordings of the same sensor, which need separate start and goal state copies
   (§3 above).
8. **[E] Alg. 1 with n = 1.** The loop `for i = 2 to n` never runs, so line 14 returns false even
   when the story is trivially consistent. Fixture case `icra_derived_alg1_single_room_story`:
   story A, empty history.
9. **[A] Alg. 1 line 9.** "(p_i, j) adjacent to (p_{i−1}, k)" must mean *reachable in G_s through
   sensor (beam-side or occupancy) vertices and the chaining edges, without entering another
   room*. Read literally as one edge, it is wrong, because consecutive story rooms are usually
   separated by sensor crossings. The paper also does not say how `(p_i, j)` with `p_i = p_{i−1}`
   is adjacent to itself.
10. **[T] Alg. 1 line 3:** `j = m + 1 ? nil : s_j` uses `=` where `==` is meant (line 3 uses `==`
    in its first ternary).
11. **[A] Room visits.** ICRA §II-A omits STAR's "accounts for all its visits". The paper never
    says whether a room in C_p may be passed through without being reported, and the verdicts
    for both ABDEC examples depend on this. We follow STAR and the Fig. 5 construction.
12. **[A] Problem 3 anchoring.** §II-A (start at p1, end at pn) conflicts with the ω1/ω6 form in
    §V-A and with Alg. 2's free start. For the paper's examples both readings give the same
    answer.
13. **[A] The ABDEC examples change history mid-paper.** §III-A and the Fig. 6 caption use
    b1 b3 o2 o2 b4, while §V-A uses b1 b2 o2 o2 b4, and the paper states no answer for either.
    §III-A introduces the example after "It turns out that this is indeed the case for
    Problem 1", which suggests it is meant as a positive example. Under the strict semantics it
    is inconsistent; it is consistent only if unreported visits are allowed (it needs a second
    visit to D).
14. **[A] Fig. 3 drawing.** The text says each beam has "two vertices … marked differently from
    other vertices", but Fig. 3 draws beams as double bars on edges between unlabeled junctions,
    which is closer to the style of STAR Fig. 3(b). Fig. 3 also has a stray label **"G"** to the
    right of the R7 junction. It is not a vertex: there is no room G. It is probably the graph's
    name.
15. **[A] Fig. 4(a)** is built "since agent x starts at A" (G_1 = SUBG(G, p1, s1)), yet the
    M1 of §IV and Fig. 5(a) has transitions from its start state to every room in C_p (A, B,
    C). This is a difference of presentation only: M reads p1 first, so a run on p can only
    continue from the room state p1, and the part of M1 not reachable from p1 can never reach
    an accepting state on such a run. M and Alg. 1 therefore decide identically (and
    `icra_nfas` builds the same M1 either way for the example).
16. **[T] §V-B, "For Problem 3, finding p' with smallest number of edits"** should say Problem 4.
    §V-B also says the transducer blocks are "each of the form M_1 from Fig. 5(a)"; they should
    be copies of M_j (M_j^i).
17. **[T] Undefined symbol.** `m_w` (§V-B) is never defined. The rest of the paper uses `n_w`
    (the size of W_free).
18. **[A] "Linear in both" lengths** (Abstract, §VI) means the bilinear O(nm). It does not mean
    O(n+m). The O(nm lg n_w) bound for Alg. 1 also needs a prefix-minimum over k ≤ j at line 9;
    a direct implementation is O(n m²).
19. **[T] Spelling and grammar:**
    - "spare network" → sparse (Abstract)
    - "deliberate tempering" → tampering (p. 4980)
    - "purpuse" → purpose (p. 4983)
    - "Exisitng" → existing (p. 4983)
    - "pesudocode" → pseudocode (p. 4984)
    - "An additional structures" → structure (p. 4984)
    - "with with" (p. 4984)
    - "preprocessing along" → alone (p. 4985)
    - In §V-A, "|ω1| + 1 = |σ(1,0,j) and |ω2| + 1 = σ(2,j,k)|" has misplaced bars; it should be
      |σ(1,0,j)| and |σ(2,j,k)|.
20. **[A] Final state F (p. 4983).** The text first says "when i = m, we let all vertices in
    C_p be acceptance states" and in the next paragraph "we connect all of M_{m+1}'s states to a
    single acceptance state F via an ε transition". Read alone, the second sentence connects
    sensor vertices too, so M would accept stories that end before the last recording, against
    §II-A ("ends in pn"). We read "all of M_{m+1}'s states" as its acceptance states (the room
    states); the other reading is `icra_composite(..., end_anywhere=True)`.
21. **[T] Fig. 1 caption.** "triggers the sensor recordings b2, o2, o1" leaves out the
    deactivations. The full history is b2 o2 o2 o1 o1.

## 8. How we verified

We used private verification scripts, which are not part of this repository:

- a region-level ground truth: a strict and a loose BFS over (position, story index, history
  index), the history's language as an NFA over room symbols, Dijkstra for Problem 3 (free and
  anchored) and Problem 4, and a Problem 2 checker with explicit interval boundaries;
- an implementation of the paper's SUBG and M_j constructions;
- checks that compare the maps with the Fig. 3 and STAR Fig. 3(a) transcriptions and the
  subgraphs and NFAs with Figs. 4 and 5, that the composite-automaton language equals the
  region-model language for all words of length ≤ 6 on 5 histories (including repeated-sensor
  ones), and that run the examples with brute-force enumeration cross-checks;
- Alg. 2 (printed and corrected), randomized tests of Alg. 2 and of the max{n, n'} claims, and
  searches for the targeted counterexamples;
- the generator of `icra.json`, which recomputes every expected value.

The published code and tests now cover the same ground (`subgraphs.py`, `problems.py`,
`tests/test_subgraphs.py`, `tests/test_problems.py`, `tests/oracles/`).

**Independent re-check.** In a second pass we re-derived both maps from fresh 1200–1500 dpi
renderings of Figs. 1–3 (wall gap by wall gap), and recomputed every expected value with separate
code written in a different style: the history's language enumerated by depth-first enumeration of
walks, strict consistency by enumerating all story/history interleavings, edit distances by
brute force over the enumerated words, and an exact reachability check for the "no p' exists"
claims. That code also recomputed the Problem 2 values under the model above, all G_j, Figs. 4(a)/(b),
and replayed every witness walk. All values matched. The only change was to add the
C_p-independent counterexample to erratum 4.
