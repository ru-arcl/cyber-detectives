# Map geometry: the original applet map vs. the paper figures

Fixture: [`tests/fixtures/paper/star_fig2_geometry.json`](../../tests/fixtures/paper/star_fig2_geometry.json)
(provenance: original `Environment.createExampleEnvironment`, arc-l/cyber-detective@55f57f8).

## Summary

- The applet map in `ui/Environment.java` is the STAR Fig. 2 workspace. ICRA Fig. 1 is the
  same drawing with a sample path added and the region labels removed. The topology is the
  same in all three: every door, every wall and both beams line up. The differences are in
  aspect ratio and small offsets, listed below.
- We recomputed the connectivity graph from the applet geometry on our own, with a raster
  flood fill. It gives four free-space components that match STAR's R1-R4 (Fig. 3(b)). Its
  15 edges match `DetectiveGame.getBasicGame()` exactly (leaving out the virtual start edge
  `SV–A`), and they also match STAR Fig. 3(a) exactly. `getBasicGame` is therefore
  consistent with the geometry, and could have been derived from it. Its neighbour lists are
  symmetric.
- The result depends on how the drawing is read. It holds only if (a) beams use Java's
  default square stroke caps, and (b) the 5-unit strip between a beam's two drawn side
  segments counts as part of the beam. Read as pure line geometry, the map leaks (details
  below).

## Coordinates (Java screen coordinates, y down, 800×600 world)

| item | data | drawn as |
|---|---|---|
| bounding box | `[0,0,800,600]` | `drawRect`, stroke 20 (free interior is 10..790 × 10..590) |
| walls | 27 axis-parallel segments (see fixture) | stroke 8 |
| beam sides | `b1u (215,285)-(290,285)`, `b1d (215,305)-(290,305)`, `b2l (625,180)-(625,265)`, `b2r (645,180)-(645,265)` | red, stroke 15 |
| occupancy | `o1 [295,270,235,185]`, `o2 [610,270,190,330]` (x,y,w,h) | orange `fillRect` |
| rooms | `A [0,0,210,185]`, `B [610,0,190,185]`, `C [0,400,210,200]` | light-grey `fillRect` |

Paint order is beams, then occupancy, then rooms, then the bounding box (stroke 20), then
walls (stroke 8). So walls and the frame are drawn over the rooms, and room B is drawn over
the top 7.5 units of the b2 strokes. `java.awt.BasicStroke(float)` defaults to `CAP_SQUARE`,
so every wall and beam stroke extends `w/2` past both of its endpoints.

Doorways that remain open after the 8-unit wall strokes (opening = gap − 8):

- x=210: y 79..126 (A ↔ R2), 324..371 (inside R1), 479..521 (C ↔ R1)
- x=295: y 79..126 (inside R2), 189..266 (corridor, R2), 349..396 (o1 ↔ R1), 504..541 (inside R1)
- x=530: y 79..126 and 189..266 (inside R2), 349..396 (o1 ↔ R3)
- x=610: y 189..266 (corridor up to b2l, R2), 474..521 (o2 ↔ R3)
- y=185: x 89..136 (A ↔ R1), 214..291 and 534..606 (corridors, R2), 684..726 (B ↔ R4)
- y=270: x 384..426 (o1 ↔ R2), 684..726 (o2 ↔ R4)
- y=455: x 384..426 (o1 ↔ R1)
- y=400: no opening (C's top wall is solid)

## Rendered comparison (described in words)

We drew the geometry with matplotlib, using the applet's colours and stroke widths. We then
overlaid it on 500-dpi renders of STAR Fig. 2 (p. 396) and ICRA Fig. 1 (p. 4980). For the
overlay, each figure's inner frame was rescaled to 800×600 separately on each axis.

- **Aspect ratio.** The inner frame of the paper figure is about 1.51:1 (STAR) or 1.53:1
  (ICRA). The applet uses 4:3. So the paper drawing is about 13% wider relative to its
  height.
- **Walls.** After the per-axis rescale, every wall in the paper falls within about 10 units
  of an applet wall. The vertical lines read at ≈206/291/536/620, against the applet's
  210/295/530/610. The horizontal lines read at ≈178/270/410/467, against 185/270/400/455.
- **Doors.** Every applet doorway exists in the paper and the reverse is also true; no door is
  missing on either side. Door positions along a wall differ by up to roughly 15-30 units. For
  example, the two top doors on x=295 and x=530 start a little higher in the paper.
- **Beams.** The paper draws each beam as one red segment: b1 is horizontal at y≈292 across the
  left corridor, and b2 is vertical at x≈645 from B's bottom wall down to o2's top wall. The
  applet draws two parallel red segments 20 units apart, one per side vertex. The b1 pair is
  centred at y=295, matching the paper. The b2 pair is centred at x=635, against the paper's
  ≈645.
- **Decoration.** The paper uses a hatched frame and the applet a solid black 20-unit stroke.
  STAR labels R1-R4; the applet and ICRA Fig. 1 do not. ICRA Fig. 1 adds a start point x_I in
  A, a goal point x_G in C and a curve for the story A,B,A,C. That curve runs
  A→R2→(b2)→R4→B→R4→o2→R3→o1→R2→A→R1→C. Traced by eye on the overlay, it crosses doorways consistent with our geometry and triggers exactly b2, o2, o1, which
  matches the caption.
- **README screenshot** (arc-l/cyber-detective README). It shows the applet drawing this map at
  400×300, which agrees with our render. The b1u/b1d labels overlap each other in it. Its
  story string `ABACCCCCCCCBBBB` shows that clicking the current room again appends that room
  again.

The paper figure and the applet map are the same workspace. Neither one is a pixel-exact copy
of the other.

## Independent connectivity derivation

**Method.** We rasterised at 4 cells per unit and treated these as obstacles:

- the frame stroke (20)
- the walls (stroke 8, square caps)
- the room rectangles and the occupancy rectangles
- each beam side stroke (15, square caps)
- the bounding box of each beam's two side strokes, which fills the 5-unit strip between them

We labelled the free cells with 4-connectivity. A feature vertex touches a component if some
free cell 4-adjacent to that feature belongs to the component. Two feature vertices are joined
by an edge if they touch a common component (the convention in `tests/fixtures/README.md`).

**Which side is which.** The y axis points down, so `b1u` (y=285) is the upper side on screen.
Free cells next to the b1u stroke lie above the beam, and free cells next to the b1d stroke lie
below it. Likewise `b2l` (x=625) faces the corridor to the left and `b2r` (x=645) faces R4 to
the right. This matches STAR p. 397: "b1u, b1d are the upper and lower sides of b1 … b2l, b2r
are the left and right sides of b2". So each drawn segment is that side's half of a single
beam, offset 10 units from the beam's centre line.

**Result (4 components, paper names):**

| region | interior point | touches |
|---|---|---|
| R1 (left room + lower corridor + area below o1) | (100,300) | A, C, b1d, o1 |
| R2 (upper corridor, top-middle room, horizontal corridor) | (400,100) | A, b1u, b2l, o1 |
| R3 (between o1 and o2) | (570,500) | o1, o2 |
| R4 (below B, right of b2) | (720,230) | B, b2r, o2 |

Edges (15): A–C, A–b1d, A–b1u, A–b2l, A–o1, B–b2r, B–o2, C–b1d, C–o1, b1d–o1, b1u–b2l,
b1u–o1, b2l–o1, b2r–o2, o1–o2.

- **getBasicGame.** Its edge set is identical to ours, plus `SV–A`. `SV` is a virtual start
  vertex: `updateStartingVertex` moves its single edge to the first room of the story, so it is
  not part of the geometry. All of its neighbour lists are symmetric.
- **STAR Fig. 3(a).** Identical. We checked this on a 500-dpi render. The edges from A go to
  b2l, b1u, o1, b1d and C.
- **STAR Fig. 3(b).** The region adjacency is identical: R1–{C, A, b1, o1}, R2–{A, b1, o1, b2},
  R3–{o1, o2}, R4–{b2, B, o2}.

**How sensitive the result is to reading the drawing** (each variant was rerun):

| variant | components | difference |
|---|---|---|
| strip between the two side strokes left free ("literally as drawn") | 6 | adds spurious **b1u–b1d** and **b2l–b2r** edges. Each strip is an enclosed sliver touching both sides. Such an edge would let an agent cross a beam without triggering it, which is wrong. |
| butt caps on beams | 2 | b1 stops 1 unit short of both walls (215 vs wall edge 214; 290 vs 291). b2 stops 1 unit short of the y=270 wall (265 vs 266). R1, R2 and R4 merge. |
| butt caps on walls only | 4 | no change |
| zero-width lines | 2 | same three beam ends, now 5-unit gaps (215 vs 210, 290 vs 295, 265 vs 270) |

The applet never computes connectivity from geometry; its graph is hard-coded. The geometry
is therefore only "correct" under Java's default square caps with each beam treated as one
band. Any code that recomputes connectivity from these coordinates must model the beam as one
band. That band spans x 207.5..297.5 × y 277.5..312.5 for b1, and x 617.5..652.5 × y
172.5..272.5 for b2. Alternatively, such code can extend the beam centre lines to the walls.

## Hit-testing and click scaling (for the demo)

- **Canvas.** The panel is `CANVAS_WIDTH × CANVAS_HEIGHT` = 400×300 px. `predraw` scales by
  `canvasWidth / SCALING_FACTOR` = 400/800 = 0.5 on both axes, so the 800×600 world fills the
  canvas.
- **Click to world** (`CyberDetectiveDemoApplet.mouseClicked`):
  `x = (int)(px * 800f / 400)` and `y = (int)(py * 800f / 400)`. Both axes divide by
  `canvasWidth`, which is correct because the scale is uniform. Only even world coordinates
  can be produced.
- **`Environment.getClickedVertex(x, y)`.** First match wins, using `Rectangle2D.contains`,
  which is half-open (`x0 ≤ x < x0+w`, `y0 ≤ y < y0+h`). The tests run in this order:
  1. occupancy rectangles (o1, o2)
  2. room rectangles (A, B, C)
  3. beam hit rectangles

  Each beam hit rectangle is the segment widened by ±8 perpendicular to it and **not**
  extended along it:

  | beam side | hit rectangle (x, y, w, h) |
  |---|---|
  | b1u | `[215,277,75,16]` |
  | b1d | `[215,297,75,16]` |
  | b2l | `[617,180,16,85]` |
  | b2r | `[637,180,16,85]` |

  Consequences:
  - The hit rectangles of the two sides of a beam do not overlap. A 4-unit dead band lies
    between them (b1: y 293..296; b2: x 633..636).
  - Clicks in y 180..184 on b2 return room **B**, because rooms are tested first.
  - The drawn strokes extend 7.5 units past the segment ends because of the square caps, but
    those parts are not clickable.
  - Walls and corridors return `null`.
- **Iteration order.** Within each type the order comes from `HashMap<Integer,…>.values()`.
  The vertex ids are small and deterministic (`getBasicGame`: SV=2, A=3, B=4, C=5, b1u=6,
  b1d=7, b2l=8, b2r=9, o1=10, o2=11), so the order is ascending id. It never matters, because
  shapes of the same type do not overlap.
- **Click semantics** (to reproduce in the demo):
  - A click is accepted only if the vertex is in `vertexIdMap`. At the start that is {A, B, C}.
    After that it is the current vertex's neighbours (except SV) plus the current vertex.
  - Clicking a room appends its letter to the story string. Repeats are allowed.
  - Clicking a sensor appends `name.substring(0,2)` (o1, o2, b1, b2) to the sensor string.
  - Clicking a beam side `v` moves the current location to `v.assoVertex`, the opposite side.
    So the user clicks the side they are on in order to cross. Clicking it again crosses back.
  - In single-agent mode each `o_i` token later expands to an activation followed by a
    deactivation. In multi-agent mode successive `o_i` tokens alternate between activation and
    deactivation.
  - On Reset, `env.game` is replaced by a new `getBasicGame()`. The `Environment` shapes keep
    the Vertex objects of the first game, which works only because the ids are identical.

## Open points

- The applet coordinates are a hand redraw of the paper figure (or the reverse). No source
  gives the paper figure's own coordinates. We use the applet coordinates as the canonical
  geometry for the demo and keep this provenance noted.
