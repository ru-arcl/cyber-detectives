"""Paper-literal constructions: sub-graphs, the composite graph ``G_s`` and the NFAs ``M_j``.

This module implements, on the connectivity graph ``G`` derived from a :class:`Map`, the
constructions the two papers use to explain their algorithms.  It is for the figures and for
teaching; :func:`cyber_detectives.engine.validate` does not use it (``docs/DESIGN.md``).

STAR (WAFR 2010), single agent
    :func:`get_reachable_subgraph` (Algorithm 2), :func:`get_subgraph` (Algorithm 1), the
    sensing-induced sub-graphs of Fig. 4 (:func:`star_subgraphs`, one part per start vertex:
    ``G21``/``G22``), the directed composite graph ``G_s`` of Fig. 5 (:func:`star_composite`)
    and the dynamic program of Algorithm 3 over it (:func:`star_validate`).
STAR, multiple agents
    :func:`get_subgraph_multi` (Algorithm 4), the sub-graph family of Fig. 7
    (:func:`star_multi_subgraphs`: ``G0_1``, ``G0_4``, ``G11_4``, ...), its composite graph and
    DP (:func:`star_multi_composite`, :func:`star_validate_multi`).
ICRA 2011
    ``SUBG`` (:func:`icra_subg`), the per-recording sub-graphs ``G_j`` (Fig. 4,
    :func:`icra_subgraphs`), the NFAs ``M_j`` (Fig. 5, :func:`icra_nfas`), the composite
    automaton ``M`` (Fig. 6, :func:`icra_composite`) and Algorithm 1
    (:func:`icra_validate_agent_story`).

Variants
--------
Every builder takes ``variant="literal"`` or ``"corrected"``.  The literal variant follows the
pseudocode / construction as printed, with only the minimum needed to make it run; the
corrected variant applies the fixes of ``docs/notes/paper-examples-star.md`` section 6 and
``docs/notes/paper-examples-icra.md`` section 3.  Where they differ:

- **Alg. 2 line 11**: literal adds every goal to ``V'``, even unreachable ones (A2-i);
  corrected adds only goals adjacent to ``V'`` (Fig. 4 has no ``b2r`` in G3).
- **Alg. 3 line 13**: literal starts the next slot from every goal; corrected only from goals
  the part reaches.
- **Alg. 4 clique-ification**: literal also joins goals to each other (``b2l-b2r``, the
  ``E_algorithm4`` of Fig. 7); corrected drops goal-goal edges (Fig. 6(b) as drawn).
- **A vertex that is both start and goal of a part** (same beam or occupancy sensor twice in a
  row; A3-v, ICRA notes section 3): literal keeps one vertex, and Fig. 5's start rule
  (out-arcs only) wins; corrected splits it into a start copy and a goal copy joined by a
  direct start->goal arc (x crosses straight back / re-enters at once).
- **Room self-loops** (repeated entries ``A, A``): literal on every room (Fig. 5); corrected
  only on rooms that touch a free region.
- **Alg. 3 with n = 1**: literal returns false (A3-vii; ICRA erratum 8); corrected accepts.
- **Single-agent history**: literal does not check it (A3-viii); corrected rejects a history
  no single agent can produce.
- **ICRA M_j inside an occupancy interval**: literal ``SUBG(G, o, o)``, which lets x visit
  rooms and pass through ``o`` unrecorded; corrected the single vertex ``o``.
- **Multi-agent family**: literal is the §5 procedure (parts built at beam recordings and at
  the final deactivation); corrected is component-based (below).

ICRA's final state ``F`` is the same in both variants: only the acceptance states of
``M_{m+1}``, its room states, are joined to ``F`` (x ends inside ``p_n``).  ICRA p. 4983 first
says "when i = m, we let all vertices in C_p be acceptance states" and in the next paragraph
"we connect all of M_{m+1}'s states to a single acceptance state F".  Reading the second
sentence on its own (every state, sensor vertices included) contradicts the first and accepts
stories that end before the last recording; that over-literal reading is an ambiguity of the
text, not a figure artefact, and is available as ``icra_composite(..., end_anywhere=True)``.

Both variants read "adjacent" in Alg. 3 line 20 as "reachable along a directed path whose
interior vertices are sensor vertices" (the p. 400 narrative; a single edge cannot reproduce
it) and use Fig. 5's direction rule: room-room edges both ways, a sensor start vertex has
out-arcs only, a goal vertex has in-arcs only.

The corrected single-agent constructions decide exactly what :func:`validate` decides
(tested on random maps), because a single agent's position between recordings is always a
room, a start vertex or a goal vertex, all of which ``G`` represents.  The multi-agent case
is different: ``G`` cannot express "x waits in a free region that touches no room across a
deactivation" (case ``star_multi_wait_in_unlabelled_component``), so the corrected
multi-agent family is built on the region graph of STAR Fig. 3(b) instead of ``G``: parts
end at every beam recording *and* at every deactivation, and at a deactivation every room,
free region and still-active occupancy region is handed over to the next parts.  With that
change it, too, agrees with :func:`validate` (``agents="multi"``).

Naming follows the figures: STAR Fig. 4 numbers sub-graphs ``G1, G2, ...`` by slot (the
gaps between recordings that are not inside an occupancy interval), with a second digit
when a slot has several starts (``G21``, ``G22``, in the order of the goals they come from);
Fig. 7 writes ``G<start>_<k>`` with ``<start>`` = ``0`` for ``p_1`` or ``<k'><i>`` for side
``i`` of the beam recorded at ``k'``.  Side numbers follow the map's ``vertex_order`` when it
has one (for ``star_fig2`` the paper's ``V = {..., b1u, b1d, b2l, b2r}``), else the beam's
side order.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import (Callable, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Sequence,
                    Set, Tuple, Union)

from .history import Event, HistoryLike, InputError, StoryLike, parse_history, parse_story
from .history import check_history as _check_history
from .maps import Map

__all__ = [
    "EPS",
    "Graph",
    "Part",
    "Node",
    "Composite",
    "PaperResult",
    "NFA",
    "CompositeAutomaton",
    "connectivity_graph",
    "get_reachable_subgraph",
    "get_subgraph",
    "get_subgraph_multi",
    "star_subgraphs",
    "star_composite",
    "star_validate",
    "star_multi_subgraphs",
    "star_multi_composite",
    "star_validate_multi",
    "icra_subg",
    "icra_subgraphs",
    "icra_nfas",
    "icra_composite",
    "icra_validate_agent_story",
]

#: Label of an empty (epsilon) NFA transition, as in the fixtures.
EPS = "eps"

VARIANTS = ("literal", "corrected")

Edge = Tuple[str, str]


def _e(a: str, b: str) -> Edge:
    return (a, b) if a <= b else (b, a)


def _check_variant(variant: str) -> bool:
    """Return True for the literal variant."""
    if variant not in VARIANTS:
        raise ValueError("variant must be 'literal' or 'corrected', got %r" % (variant,))
    return variant == "literal"


def _ordered_unique(xs: Iterable) -> list:
    seen: set = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ============================================================================ graphs


class Graph(NamedTuple):
    """An undirected graph: a vertex set and a set of edges (each a sorted pair)."""

    vertices: FrozenSet[str]
    edges: FrozenSet[Edge]

    def V(self) -> List[str]:
        """Vertices, sorted."""
        return sorted(self.vertices)

    def E(self) -> List[Edge]:
        """Edges, sorted (each edge is a sorted pair)."""
        return sorted(self.edges)

    def adjacency(self) -> Dict[str, Set[str]]:
        """``{vertex: set of neighbours}``."""
        adj: Dict[str, Set[str]] = {v: set() for v in self.vertices}
        for a, b in self.edges:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        return adj


def connectivity_graph(m: Map) -> Graph:
    """The connectivity graph ``G`` of STAR Fig. 3(a) / ICRA Fig. 3, derived from ``m``."""
    return Graph(frozenset(m.features), frozenset(_e(a, b) for a, b in m.edges()))


GraphLike = Union[Map, Graph, Iterable[Sequence[str]]]


def _as_graph(G: GraphLike) -> Graph:
    if isinstance(G, Map):
        return connectivity_graph(G)
    if isinstance(G, Graph):
        return G
    edges = frozenset(_e(str(a), str(b)) for a, b in G)
    return Graph(frozenset(v for e in edges for v in e), edges)


def _component(edges: Iterable[Edge], s: str) -> Set[str]:
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen = {s}
    dq = deque([s])
    while dq:
        u = dq.popleft()
        for w in adj.get(u, ()):
            if w not in seen:
                seen.add(w)
                dq.append(w)
    return seen


# ============================================================================ STAR Alg. 1, 2, 4


def get_reachable_subgraph(G: GraphLike, s: str, V_C: Iterable[str], V_G: Sequence[str], *,
                           literal: bool = True) -> Graph:
    """STAR Algorithm 2, GETREACHABLESUBGRAPH.

    Keep the edges of ``G`` inside ``V_C``, take the connected component of ``s``, then add
    the edges from that component to the goal vertices ``V_G``.  With ``literal=True`` line 11
    (``V' <- V' U V_G``) adds every goal, even one no edge reaches (erratum A2-i: G3 of Fig. 4
    would get an isolated ``b2r``); with ``literal=False`` only goals adjacent to the component
    are added.  ``s`` is always in the result, even without a neighbour in ``V_C`` (erratum
    A2-ii; the pseudocode builds ``V'`` from edges only).
    """
    g = _as_graph(G)
    vc = set(V_C) | {s}
    e1 = {e for e in g.edges if e[0] in vc and e[1] in vc}
    comp = _component(e1, s)
    verts = set(comp)
    edges = {e for e in e1 if e[0] in comp and e[1] in comp}
    goals = list(V_G)
    if goals:
        # lines 6-10: v_i ranges over V' as it was before the loop
        for vi in sorted(comp):
            for vj in goals:
                if vi != vj and _e(vi, vj) in g.edges:
                    edges.add(_e(vi, vj))
        if literal:
            verts |= set(goals)
        else:
            verts |= {x for e in edges for x in e if x in goals}
    return Graph(frozenset(verts), frozenset(edges))


def get_subgraph(G: GraphLike, s: str, rooms: Iterable[str], V_G: Sequence[str], *,
                 literal: bool = True) -> Graph:
    """STAR Algorithm 1, GETSUBGRAPH: Algorithm 2 with ``V_C = C_p U {s}``."""
    return get_reachable_subgraph(G, s, set(rooms) | {s}, V_G, literal=literal)


def get_subgraph_multi(G: GraphLike, s: str, rooms: Iterable[str], O: Sequence[str],
                       V_G: Sequence[str], *, literal: bool = True) -> Graph:
    """STAR Algorithm 4, GETSUBGRAPHMULTI.

    Algorithm 2 with ``V_C = C_p U O U {s}``, then each active occupancy vertex in the result
    is replaced by a clique on its current neighbours (in the order of ``O``; the result does
    not depend on it, erratum A4-ii).  ``literal=True`` reproduces the pseudocode, which also
    joins goal vertices to each other (``b2l-b2r`` in Fig. 6(b), K5); ``literal=False`` drops
    edges between two goals that are not the start (Fig. 6(b) as drawn; such an edge is never
    usable because goals are terminal) and uses the corrected Algorithm 2.
    """
    sub = get_reachable_subgraph(G, s, set(rooms) | set(O) | {s}, V_G, literal=literal)
    verts = set(sub.vertices)
    edges = set(sub.edges)
    for o in O:
        if o not in verts or o == s or o in V_G:
            continue
        nb = sorted({x for e in edges if o in e for x in e if x != o})
        for i in range(len(nb)):
            for j in range(i + 1, len(nb)):
                edges.add(_e(nb[i], nb[j]))
        edges = {e for e in edges if o not in e}
        verts.discard(o)
    if not literal:
        goals = set(V_G) - {s}
        edges = {e for e in edges if not (e[0] in goals and e[1] in goals)}
    return Graph(frozenset(verts), frozenset(edges))


# ============================================================================ parts / G_s


@dataclass(frozen=True)
class Part:
    """One sub-graph of a composite graph (a block of ``G_s``).

    ``goals`` are the goal vertices present in ``graph`` (literal Algorithm 2: all of
    ``goal_set``); ``interval`` is the time span in the paper's notation (``"[t0,t1)"``);
    ``record`` is the 1-based index of the recording that closes the part (``None`` if it runs
    to ``t_f``).  ``slot`` is the Fig. 4 slot (single agent) or the build point (multi).
    """

    name: str
    start: str
    goals: Tuple[str, ...]
    graph: Graph
    interval: str
    slot: int
    record: Optional[int]
    entry: bool
    terminal: bool
    goal_set: Tuple[str, ...] = ()
    active: Tuple[str, ...] = ()
    start_key: str = ""

    @property
    def V(self) -> List[str]:
        return self.graph.V()

    @property
    def E(self) -> List[Edge]:
        return self.graph.E()


class Node(NamedTuple):
    """A vertex of ``G_s``: ``vertex`` of part ``part`` in a ``role``.

    Roles: ``"room"`` (a copy of a room; reaching it is a visit), ``"start"`` (the part's start
    vertex, out-arcs only), ``"goal"`` (a goal vertex, in-arcs only), ``"free"`` (a free
    region or active occupancy region; corrected multi-agent family only).
    """

    part: str
    vertex: str
    role: str

    def __str__(self) -> str:
        return "%s.%s" % (self.part, self.vertex)


@dataclass
class PaperResult:
    """Outcome of a paper algorithm.

    ``subproblems[i]`` lists (sorted, unique) the parts holding a copy of ``p_i`` reached after
    the first ``i`` story elements; ``paths[i]`` gives one path per surviving subproblem in the
    paper's notation (``"A_G1 b1u b1d C_G21"``).
    """

    consistent: bool
    reason: Optional[str] = None
    subproblems: Dict[int, List[str]] = field(default_factory=dict)
    paths: Dict[int, List[str]] = field(default_factory=dict)
    composite: Optional["Composite"] = None

    def __bool__(self) -> bool:
        return self.consistent


class Composite:
    """A composite graph ``G_s``: parts, directed arcs inside them, and crossing arcs.

    Build it with :func:`star_composite` or :func:`star_multi_composite`.  ``kinds(v)`` says
    whether vertex ``v`` is a ``"room"``, a ``"free"`` position (region / occupancy region,
    corrected multi-agent family) or neither (a sensor vertex: only start or goal).  Each link
    ``(part, vertex, role, target part)`` is an arc from that node to the target's start.
    ``arcs`` maps every node to its successors.
    """

    def __init__(self, parts: Sequence[Part], *, kinds: Callable[[str], Optional[str]],
                 links: Sequence[Tuple[str, str, str, str]], split_roles: bool,
                 stay_arcs: bool, loop_rooms: Callable[[str], bool]) -> None:
        self.parts: List[Part] = list(parts)
        self.by_name: Dict[str, Part] = {p.name: p for p in self.parts}
        self.arcs: Dict[Node, List[Node]] = {}
        self.crossing: List[Tuple[Node, Node]] = []
        #: arcs along which x stays inside the same room (hand-over at a deactivation)
        self.holds: Dict[Node, List[Node]] = {}
        self._start: Dict[str, Node] = {}
        self._goal: Dict[Tuple[str, str], Node] = {}
        self._normal: Dict[Tuple[str, str], Node] = {}
        for p in self.parts:
            self._add_part(p, kinds, split_roles, stay_arcs, loop_rooms)
        for src_part, src_vertex, src_role, dst_part in links:
            src = self.node(src_part, src_vertex, src_role)
            dst = self._start[dst_part]
            if src is None:
                continue
            self._arc(src, dst)
            self.crossing.append((src, dst))
            if src_role in ("room", "start") and dst.vertex == src.vertex \
                    and kinds(src.vertex) == "room":
                self.holds.setdefault(src, []).append(dst)

    # ------------------------------------------------------------------ construction

    def _arc(self, u: Node, v: Node) -> None:
        lst = self.arcs.setdefault(u, [])
        if v not in lst:
            lst.append(v)

    def _add_part(self, p: Part, kinds: Callable[[str], Optional[str]], split: bool,
                  stay: bool, loop_rooms: Callable[[str], bool]) -> None:
        s = p.start
        goals = set(p.goals)
        if p.entry:
            start = Node(p.name, s, "room")
        else:
            start = Node(p.name, s, "start")
        self._start[p.name] = start
        for v in p.graph.vertices:
            k = kinds(v)
            if k is not None and v not in goals and not (v == s and p.entry):
                self._normal[(p.name, v)] = Node(p.name, v, k)
        if p.entry:
            self._normal[(p.name, s)] = start
        for g in p.goals:
            if g == s and not split:
                self._goal[(p.name, g)] = start      # one vertex: the start rule wins
            else:
                self._goal[(p.name, g)] = Node(p.name, g, "goal")

        def sources(x: str) -> List[Node]:
            out = []
            if x == s:
                out.append(start)
            elif x in goals:
                return out
            n = self._normal.get((p.name, x))
            if n is not None and n not in out:
                out.append(n)
            return out

        def target(y: str) -> Optional[Node]:
            if y in goals:
                n = self._goal[(p.name, y)]
                return None if n == start else n
            return self._normal.get((p.name, y))

        self.arcs.setdefault(start, [])
        for a, b in sorted(p.graph.edges):
            for x, y in ((a, b), (b, a)):
                t = target(y)
                if t is None:
                    continue
                for u in sources(x):
                    if u != t:
                        self._arc(u, t)
        if split and stay and s in goals:
            self._arc(start, self._goal[(p.name, s)])
        for v in sorted(p.graph.vertices):
            n = self._normal.get((p.name, v))
            if n is not None and n.role == "room" and loop_rooms(v):
                self._arc(n, n)

    # ------------------------------------------------------------------ queries

    def node(self, part: str, vertex: str, role: str) -> Optional[Node]:
        """The node of ``vertex`` in ``part`` with ``role`` (``None`` if absent)."""
        if role == "start":
            n = self._start.get(part)
            return n if n is not None and n.vertex == vertex else None
        if role == "goal":
            return self._goal.get((part, vertex))
        return self._normal.get((part, vertex))

    def start_node(self, part: str) -> Node:
        return self._start[part]

    def part_arcs(self, part: str) -> List[Tuple[str, str]]:
        """Arcs inside ``part`` as sorted unique ``(from vertex, to vertex)`` pairs."""
        out = set()
        for u, vs in self.arcs.items():
            if u.part != part:
                continue
            for v in vs:
                if v.part == part:
                    out.add((u.vertex, v.vertex))
        return sorted(out)

    def crossing_arcs(self) -> List[Tuple[str, str]]:
        """Arcs between parts as ``("G1.b1u", "G21.b1d")`` pairs, in construction order."""
        return [(str(u), str(v)) for u, v in self.crossing]

    def entry_nodes(self, room: str) -> List[Node]:
        """Copies of ``room`` where x is at ``t_0`` (the start of an entry part)."""
        return [self._start[p.name] for p in self.parts
                if p.entry and p.start == room]

    def _reach_rooms(self, u: Node, target: str) -> List[Tuple[Node, List[Node]]]:
        """Copies of room ``target`` reachable from ``u`` through non-room nodes."""
        found: List[Tuple[Node, List[Node]]] = []
        got: Set[Node] = set()
        parent: Dict[Node, Optional[Node]] = {u: None}
        dq = deque([u])

        def trail(w: Node, via: Node) -> List[Node]:
            path = [w]
            x: Optional[Node] = via
            while x is not None:
                path.append(x)
                x = parent[x]
            return path[::-1]

        while dq:
            x = dq.popleft()
            for w in self.arcs.get(x, ()):
                if w.role == "room":
                    if w.vertex == target and w not in got:
                        got.add(w)
                        found.append((w, trail(w, x)))
                    continue
                if w not in parent:
                    parent[w] = x
                    dq.append(w)
        return found

    def _finishes(self, u: Node) -> bool:
        """Can x, inside the room copy ``u``, stay there until ``t_f``?"""
        seen = {u}
        dq = deque([u])
        while dq:
            x = dq.popleft()
            if self.by_name[x.part].terminal:
                return True
            for w in self.holds.get(x, ()):
                if w not in seen:
                    seen.add(w)
                    dq.append(w)
        return False

    def search(self, story: Sequence[str]) -> PaperResult:
        """The dynamic program of STAR Algorithm 3 (lines 16-29) over this ``G_s``.

        A subproblem is a copy of ``p_i`` (a room node) reachable from a copy of ``p_{i-1}``
        through sensor vertices only.  The story is accepted when a copy of ``p_n`` lies in a
        part that runs to ``t_f`` (or is handed over to one while x stays in the room).  This
        accepts ``n = 1``; Algorithm 3 as printed does not (see :func:`star_validate`).
        """
        story = list(story)
        front = self.entry_nodes(story[0])
        paths: Dict[Node, List[Node]] = {u: [u] for u in front}
        res = PaperResult(False, composite=self)
        res.subproblems[1] = sorted({u.part for u in front})
        res.paths[1] = [_fmt_path(paths[u]) for u in front]
        for i in range(1, len(story)):
            nf: List[Node] = []
            npaths: Dict[Node, List[Node]] = {}
            for u in front:
                for w, tr in self._reach_rooms(u, story[i]):
                    if w not in npaths:
                        nf.append(w)
                        npaths[w] = paths[u] + tr[1:]
            front, paths = nf, npaths
            res.subproblems[i + 1] = sorted({u.part for u in front})
            res.paths[i + 1] = [_fmt_path(paths[u]) for u in front]
        res.consistent = any(self._finishes(u) for u in front)
        if not res.consistent:
            done = max((i for i, v in res.subproblems.items() if v), default=0)
            if done < len(story):
                res.reason = ("no copy of story element %d (%s) is reachable in G_s"
                              % (done + 1, story[done]))
            else:
                res.reason = "no copy of %s in a part that lasts until t_f" % story[-1]
        return res


def _fmt_path(nodes: Sequence[Node]) -> str:
    toks: List[str] = []
    for n in nodes:
        if n.role == "room":
            toks.append("%s_%s" % (n.vertex, n.part))
        elif not toks or toks[-1] != n.vertex:
            toks.append(n.vertex)
    return " ".join(toks)


# ============================================================================ helpers


def _side_rank(m: Map, side_order: Optional[Sequence[str]]) -> Dict[str, int]:
    order = list(side_order) if side_order is not None else list(m.vertex_order or m.sides)
    return {v: i for i, v in enumerate(order)}


def _sensor_vertices(m: Map, sensor: str, rank: Dict[str, int]) -> List[str]:
    """SENSORVERTICES: the two sides of a beam (in side-number order), or ``[o]``."""
    if m.is_beam(sensor):
        a, b = m.beams[sensor]
        return sorted((a, b), key=lambda v: rank.get(v, 0))
    return [sensor]


def _rooms(m: Map, rooms: Optional[Iterable[str]]) -> List[str]:
    return list(rooms) if rooms is not None else list(m.rooms)


def _inputs(m: Map, story: StoryLike, history: HistoryLike) -> Tuple[List[str], List[Event]]:
    story_l = parse_story(story, m)
    if not story_l:
        raise InputError("the story must name at least one room")
    return story_l, parse_history(history, m)


def _t(k: int) -> str:
    return "t%d" % k


# ============================================================================ STAR single agent


def star_subgraphs(m: Map, story: StoryLike, history: HistoryLike, *,
                   variant: str = "corrected", rooms: Optional[Iterable[str]] = None,
                   side_order: Optional[Sequence[str]] = None) -> List[Part]:
    """The sensing-induced sub-graphs of STAR Algorithm 3 lines 1-14 (Fig. 4), single agent.

    One part per start vertex (erratum A3-iii): after a beam recording the next slot has a
    part for each side the agent can be on (``G21`` from ``b1d``, ``G22`` from ``b1u``).
    Deactivations are skipped (the agent is inside the occupancy region), so slots are
    numbered without gaps as in Fig. 4 (A3-iv).  ``rooms`` is ``C_p`` (default: the map's
    rooms, ``docs/DESIGN.md``).  The corrected variant reproduces Fig. 4 exactly; the literal
    one keeps unreachable goals (G3 gets ``b2r``) and so also builds parts from them.
    """
    literal = _check_variant(variant)
    story_l, events = _inputs(m, story, history)
    cp = _rooms(m, rooms)
    rank = _side_rank(m, side_order)
    G = connectivity_graph(m)
    M = len(events)
    parts: List[Part] = []
    starts: List[str] = [story_l[0]]
    slot = 0
    for j in range(1, M + 2):
        if j <= M and events[j - 1].kind == "D":       # lines 4-6, guarded for j = m+1
            continue
        last = j == M + 1
        VG = [] if last else _sensor_vertices(m, events[j - 1].sensor, rank)
        slot += 1
        lo = "[t0" if j == 1 else "(" + _t(j - 1)
        hi = "tf]" if last else _t(j) + ")"
        new: List[Part] = []
        for i, s in enumerate(starts):
            g = get_subgraph(G, s, cp, VG, literal=literal)
            name = ("G%d" % slot if len(starts) == 1 else
                    "G%d%d" % (slot, i + 1) if slot < 10 and i < 9 else "G%d_%d" % (slot, i + 1))
            new.append(Part(name=name, start=s, goals=tuple(v for v in VG if v in g.vertices),
                            graph=g, interval=lo + "," + hi, slot=slot,
                            record=None if last else j, entry=(j == 1), terminal=last,
                            goal_set=tuple(VG)))
        parts += new
        if not last:
            beam = m.is_beam(events[j - 1].sensor)
            starts = _ordered_unique(m.other_side(g) if beam else g
                                     for p in new for g in p.goals)
    return parts


def _star_links(m: Map, parts: Sequence[Part]) -> List[Tuple[str, str, str, str]]:
    links = []
    for p in parts:
        if p.terminal:
            continue
        for g in p.goals:
            tgt = m.other_side(g) if g in m.side_beam else g
            for q in parts:
                if q.slot == p.slot + 1 and q.start == tgt:
                    links.append((p.name, g, "goal", q.name))
    return links


def _loop_rule(m: Map, cp: Iterable[str], literal: bool, enabled: bool) -> Callable[[str], bool]:
    rooms = set(cp)
    if not enabled:
        return lambda v: False
    if literal:
        return lambda v: v in rooms
    # x can leave and re-enter a room only through a free region it touches
    return lambda v: v in rooms and bool(m.regions_of.get(v))


def star_composite(m: Map, story: StoryLike, history: HistoryLike, *,
                   variant: str = "corrected", split_roles: Optional[bool] = None,
                   stay_arcs: Optional[bool] = None, self_loops: Optional[bool] = None,
                   rooms: Optional[Iterable[str]] = None,
                   side_order: Optional[Sequence[str]] = None) -> Composite:
    """The directed composite graph ``G_s`` of STAR Fig. 5 (``CHAIN`` of Algorithm 3).

    Arcs inside a part follow Fig. 5: room-room edges both ways, a self-loop on each room, a
    sensor start vertex has out-arcs only, a goal vertex has in-arcs only.  A crossing arc
    joins goal ``b`` of slot ``k`` to the part of slot ``k+1`` that starts on the other side
    of the beam (or at the same occupancy vertex).

    ``split_roles`` (default: corrected variant) gives a vertex that is both the start and a
    goal of a part two copies, plus a direct start->goal arc if ``stay_arcs`` (default True):
    x may cross straight back or re-enter the occupancy region at once (A3-v).  Without the
    split the vertex is one node that keeps only its out-arcs.  ``self_loops`` (default True)
    puts the Fig. 5 self-loops on rooms; the corrected variant puts them only on rooms that
    touch a free region (a room with no doorway cannot be left and re-entered).
    """
    literal = _check_variant(variant)
    cp = _rooms(m, rooms)
    parts = star_subgraphs(m, story, history, variant=variant, rooms=cp, side_order=side_order)
    room_set = set(cp)
    return Composite(parts, kinds=lambda v: "room" if v in room_set else None,
                     links=_star_links(m, parts),
                     split_roles=(not literal) if split_roles is None else split_roles,
                     stay_arcs=True if stay_arcs is None else stay_arcs,
                     loop_rooms=_loop_rule(m, cp, literal,
                                           True if self_loops is None else self_loops))


def star_validate(m: Map, story: StoryLike, history: HistoryLike, *,
                  variant: str = "corrected", check_history: Optional[bool] = None,
                  split_roles: Optional[bool] = None, stay_arcs: Optional[bool] = None,
                  self_loops: Optional[bool] = None,
                  rooms: Optional[Iterable[str]] = None,
                  side_order: Optional[Sequence[str]] = None) -> PaperResult:
    """STAR Algorithm 3, VALIDATEAGENTSTORY (single agent), over :func:`star_composite`.

    ``variant="literal"``: no history check (A3-viii), literal sub-graphs, no role split, and
    ``n = 1`` returns false (line 18 loop never runs, A3-vii).  ``"corrected"``: a history no
    single agent can produce is rejected first, the role split and stay arcs are used, and
    ``n = 1`` is handled; this decides exactly what ``validate(..., agents="single")`` decides.
    The keyword switches override single refinements (the fixtures' ``paper_algorithm``
    ablations).  The result carries the subproblems after each story element (p. 400-401).
    """
    literal = _check_variant(variant)
    story_l, events = _inputs(m, story, history)
    check = (not literal) if check_history is None else check_history
    if check:
        bad = _check_history(m, events, "single")
        if bad is not None:
            return PaperResult(False, "malformed history: " + bad)
    gs = star_composite(m, story_l, events, variant=variant, split_roles=split_roles,
                        stay_arcs=stay_arcs, self_loops=self_loops, rooms=rooms,
                        side_order=side_order)
    res = gs.search(story_l)
    if literal and len(story_l) == 1:
        res.consistent = False
        res.reason = "Algorithm 3 as printed returns false when n = 1 (loop 'for i = 2 to n')"
    return res


# ============================================================================ STAR multi agent


def _hist_active(m: Map, events: Sequence[Event]) -> List[List[str]]:
    """``act[k]``: occupancy sensors active just before recording ``k`` (1-based), in
    activation order; ``act[m+1]`` after the last one."""
    act: List[List[str]] = [[]]
    cur: List[str] = []
    for e in events:
        act.append(list(cur))
        if m.is_occupancy(e.sensor):
            if e.kind == "A":
                if e.sensor not in cur:
                    cur.append(e.sensor)
            elif e.sensor in cur:
                cur.remove(e.sensor)
    act.append(list(cur))
    return act


def star_multi_subgraphs(m: Map, story: StoryLike, history: HistoryLike, *,
                         variant: str = "literal", rooms: Optional[Iterable[str]] = None,
                         side_order: Optional[Sequence[str]] = None) -> List[Part]:
    """The multi-agent sub-graph family of STAR §5 (Fig. 6, Fig. 7).

    **Literal** (as illustrated on p. 403-404; the paper gives no pseudocode): walk the
    history keeping an active list ``O``.  At each beam recording ``k`` build
    ``G<start>_k = GETSUBGRAPHMULTI(G, start, C_p, O, sides)`` for every start so far
    (``p_1``, then both sides of every earlier beam recording), then add the beam's two sides
    as starts.  At a deactivation after which no beam and no activation follows, build the
    terminal parts (no goals, ``O`` still holding the sensor) and ignore the rest ("no new
    locations of G become reachable").  Any other deactivation just leaves ``O`` (the paper
    leaves this case open, E15); without such a deactivation the terminal parts are built
    after the last recording.  For eq. (3) this gives the nine parts of Fig. 7.  This family
    is not exact: see ``star_multi_wait_in_unlabelled_component`` (rejected) and
    ``docs/notes/paper-examples-star.md`` section 10.

    **Corrected** (component-based): the parts are sub-graphs of the region graph (STAR
    Fig. 3(b); vertices are rooms, beam sides, occupancy sensors and free regions).  A part
    starts at ``p_1``, at a side of a beam recording, or at a hand-over vertex of a
    deactivation (a room, a free region or a still-active occupancy region), and ends at
    each later beam recording up to and including the first deactivation (or ``t_f``); its
    active set is the one just before its last recording.  Between two such recordings only
    activations happen, so x can postpone every move to the end of the interval and the
    union of the active sets is exact.
    """
    literal = _check_variant(variant)
    story_l, events = _inputs(m, story, history)
    cp = _rooms(m, rooms)
    rank = _side_rank(m, side_order)
    if literal:
        return _multi_literal(m, story_l, events, cp, rank)
    return _multi_corrected(m, story_l, events, cp, rank)


def _multi_name(key: str, k: int) -> str:
    return "G%s_%d" % (key, k)


def _multi_literal(m: Map, story: List[str], events: List[Event], cp: List[str],
                   rank: Dict[str, int]) -> List[Part]:
    G = connectivity_graph(m)
    M = len(events)
    starts: List[Tuple[str, str, int]] = [(story[0], "0", 0)]   # (vertex, key, time)
    O: List[str] = []
    parts: List[Part] = []

    def build(k: int, VG: List[str], terminal: bool) -> None:
        for s, key, t0 in starts:
            g = get_subgraph_multi(G, s, cp, O, VG, literal=True)
            lo = "[t0" if t0 == 0 else "(" + _t(t0)
            if terminal:
                cov = lo + ",tf]" if k > M else lo + "," + _t(k) + ") (then to tf)"
            else:
                cov = lo + "," + _t(k) + ")"
            parts.append(Part(name=_multi_name(key, k), start=s,
                              goals=tuple(v for v in VG if v in g.vertices), graph=g,
                              interval=cov, slot=k, record=None if terminal else k,
                              entry=(t0 == 0), terminal=terminal, goal_set=tuple(VG),
                              active=tuple(O), start_key=key))

    for k in range(1, M + 1):
        e = events[k - 1]
        if m.is_beam(e.sensor):
            VG = _sensor_vertices(m, e.sensor, rank)
            build(k, VG, False)
            starts += [(v, "%d%d" % (k, i + 1), k) for i, v in enumerate(VG)]
        elif e.kind == "A":
            if e.sensor not in O:
                O.append(e.sensor)
        else:
            later = events[k:]
            if not any(m.is_beam(x.sensor) or x.kind == "A" for x in later):
                build(k, [], True)
                return parts
            if e.sensor in O:
                O.remove(e.sensor)
    build(M + 1, [], True)
    return parts


def _region_graph(m: Map) -> Graph:
    edges = frozenset(_e(f, r) for r, fs in m.regions.items() for f in fs)
    return Graph(frozenset(m.features) | frozenset(m.regions), edges)


def _multi_corrected(m: Map, story: List[str], events: List[Event], cp: List[str],
                     rank: Dict[str, int]) -> List[Part]:
    H = _region_graph(m)
    adj = H.adjacency()
    M = len(events)
    act = _hist_active(m, events)
    room_set = set(cp)
    # build points: beam recordings, deactivations, and t_f (= M+1)
    points: List[int] = [k for k in range(1, M + 1)
                         if m.is_beam(events[k - 1].sensor) or events[k - 1].kind == "D"]
    points.append(M + 1)
    starts: List[Tuple[str, str, int]] = [(story[0], "0", 0)]
    for k in points[:-1]:
        e = events[k - 1]
        if m.is_beam(e.sensor):
            starts += [(v, "%d%d" % (k, i + 1), k)
                       for i, v in enumerate(_sensor_vertices(m, e.sensor, rank))]
        else:
            hand = list(m.rooms) + list(m.regions) + [o for o in act[k] if o != e.sensor]
            starts += [(v, "%d:%s" % (k, v), k) for v in hand]
    parts: List[Part] = []
    for s, key, t0 in starts:
        for k in points:
            if k <= t0:
                continue
            terminal = k == M + 1
            beam = not terminal and m.is_beam(events[k - 1].sensor)
            VG = _sensor_vertices(m, events[k - 1].sensor, rank) if beam else []
            normal = room_set | set(m.rooms) | set(m.regions) | set(act[k])
            # reachability from the start: goals are terminal; other beam sides are walls
            seen = {s}
            dq = deque([s])
            while dq:
                u = dq.popleft()
                if u != s and u in VG:
                    continue
                for w in sorted(adj.get(u, ())):
                    if w not in seen and (w in normal or w in VG):
                        seen.add(w)
                        dq.append(w)
            edges = frozenset(e for e in H.edges if e[0] in seen and e[1] in seen)
            lo = "[t0" if t0 == 0 else "(" + _t(t0)
            parts.append(Part(name=_multi_name(key, k), start=s,
                              goals=tuple(v for v in VG if v in seen),
                              graph=Graph(frozenset(seen), edges),
                              interval=lo + ("," + _t(k) + ")" if not terminal else ",tf]"),
                              slot=k, record=None if terminal else k, entry=(t0 == 0),
                              terminal=terminal, goal_set=tuple(VG),
                              active=tuple(act[k]), start_key=key))
            if terminal or not beam:
                break          # a deactivation hands everything over
    return parts


def star_multi_composite(m: Map, story: StoryLike, history: HistoryLike, *,
                         variant: str = "literal", rooms: Optional[Iterable[str]] = None,
                         side_order: Optional[Sequence[str]] = None) -> Composite:
    """The composite graph of STAR Fig. 7 over :func:`star_multi_subgraphs`.

    A crossing arc joins goal ``g`` of a part built at beam recording ``k`` to every part
    that starts on the other side of that recording (``G0_1.b1d -> G11_4.b1u`` and
    ``-> G11_5.b1u``).  In the corrected variant a part that ends at a deactivation also
    hands each room, free region and still-active occupancy region it reaches over to the
    parts starting there.  Direction rules as in :func:`star_composite`; the literal variant
    has no role split and puts self-loops on all rooms, the corrected one needs neither
    (free regions are explicit).
    """
    literal = _check_variant(variant)
    story_l, events = _inputs(m, story, history)
    cp = _rooms(m, rooms)
    parts = star_multi_subgraphs(m, story_l, events, variant=variant, rooms=cp,
                                 side_order=side_order)
    room_set = set(cp)
    by_key: Dict[str, List[Part]] = {}
    for p in parts:
        by_key.setdefault(p.start_key, []).append(p)
    rank = _side_rank(m, side_order)
    links: List[Tuple[str, str, str, str]] = []
    for p in parts:
        if p.terminal:
            continue
        k = p.record
        assert k is not None
        e = events[k - 1]
        if m.is_beam(e.sensor):
            sides = _sensor_vertices(m, e.sensor, rank)
            for g in p.goals:
                key = "%d%d" % (k, sides.index(m.other_side(g)) + 1)
                links += [(p.name, g, "goal", q.name) for q in by_key.get(key, ())]
        else:
            # corrected family only: hand-over at the deactivation of e.sensor
            for v in sorted(p.graph.vertices):
                if v == e.sensor or v in m.side_beam:
                    continue
                key = "%d:%s" % (k, v)
                for q in by_key.get(key, ()):
                    role = "room" if v in room_set else "free"
                    links.append((p.name, v, role, q.name))
                    if v == p.start and not p.entry:
                        links.append((p.name, v, "start", q.name))
    if literal:
        kinds = (lambda v: "room" if v in room_set else None)
    else:
        sensors = set(m.occupancy) | set(m.regions)
        kinds = (lambda v: "room" if v in room_set else "free" if v in sensors else None)
    return Composite(parts, kinds=kinds, links=links, split_roles=not literal,
                     stay_arcs=True, loop_rooms=_loop_rule(m, cp, True, literal))


def star_validate_multi(m: Map, story: StoryLike, history: HistoryLike, *,
                        variant: str = "literal", rooms: Optional[Iterable[str]] = None,
                        side_order: Optional[Sequence[str]] = None) -> PaperResult:
    """The DP of STAR §5 over :func:`star_multi_composite` (multiple agents).

    The corrected variant first rejects a history whose activations and deactivations do
    not alternate, and then decides exactly what ``validate(..., agents="multi")`` decides.
    The literal variant reproduces Fig. 7 and the paper's verdict for eq. (3) but is not
    exact in general (module docstring).
    """
    literal = _check_variant(variant)
    story_l, events = _inputs(m, story, history)
    if not literal:
        bad = _check_history(m, events, "multi")
        if bad is not None:
            return PaperResult(False, "malformed history: " + bad)
    gs = star_multi_composite(m, story_l, events, variant=variant, rooms=rooms,
                              side_order=side_order)
    return gs.search(story_l)


# ============================================================================ ICRA


def icra_subg(G: GraphLike, starts: Iterable[str], goals: Optional[Iterable[str]],
              rooms: Iterable[str]) -> Graph:
    """ICRA ``SUBG(G, start, goal)``: the part of ``G`` on walks from ``starts`` to ``goals``.

    Keeps the vertices that lie on some walk from a start vertex to a goal vertex whose
    intermediate vertices are rooms, and the edges such walks use (ICRA Fig. 4(a)/(b)).
    Sides of other beams are pruned even if physically reachable.  With ``goals=None`` (the
    last interval) the walks end anywhere: every room reachable from a start is kept.
    """
    g = _as_graph(G)
    adj = g.adjacency()
    rset = set(rooms)
    S = set(starts)
    T = set(goals) if goals is not None else None

    def reach(src: Set[str], ends: Set[str]) -> Set[str]:
        seen = set(src)
        dq = deque(sorted(src))
        while dq:
            u = dq.popleft()
            if u not in rset and u not in src:
                continue                       # sensor vertices other than sources: terminal
            for w in adj.get(u, ()):
                if (w in rset or w in ends) and w not in seen:
                    seen.add(w)
                    dq.append(w)
        return seen

    if T is None:
        keep = reach(S, set())
        keep = {v for v in keep if v in rset or v in S}
        edges = {e for e in g.edges if e[0] in keep and e[1] in keep
                 and (e[0] in rset or e[1] in rset)}
        return Graph(frozenset(keep), frozenset(edges))
    fwd = reach(S, T)
    bwd = reach(T, S)
    keep = {v for v in fwd & bwd if v in rset or v in S or v in T}
    edges = set()
    for a, b in g.edges:
        if a in keep and b in keep and (a in rset or b in rset or (a in S and b in T)
                                        or (b in S and a in T)):
            edges.add((a, b))
    return Graph(frozenset(keep), frozenset(edges))


@dataclass(frozen=True)
class Segment:
    """``G_j`` of ICRA: sub-graph for the interval between recordings ``j-1`` and ``j``."""

    j: int
    starts: Tuple[str, ...]
    goals: Optional[Tuple[str, ...]]
    graph: Graph
    inside: Optional[str] = None     # occupancy sensor x is inside during the interval


def icra_subgraphs(m: Map, history: HistoryLike, *, p1: Optional[str] = None,
                   variant: str = "corrected",
                   rooms: Optional[Iterable[str]] = None) -> List[Segment]:
    """ICRA Algorithm 1 lines 2-3: ``G_j = SUBG(G, j == 1 ? p1 : s_{j-1}, s_j)``, ``j = 1..m+1``.

    ``p1=None`` starts ``G_1`` from every room (ICRA §IV lets ``M_1``'s start state reach all
    of ``C_p``; for the paper's example both give Fig. 4(a)).  Between an activation and the
    deactivation of the same sensor x is inside it: the corrected variant uses the single
    vertex ``o`` there (fixture ``subgraphs_G_j``), the literal one ``SUBG(G, o, o)``.
    """
    literal = _check_variant(variant)
    events = parse_history(history, m)
    cp = _rooms(m, rooms)
    rank = _side_rank(m, None)
    G = connectivity_graph(m)
    out: List[Segment] = []
    M = len(events)
    for j in range(1, M + 2):
        if j == 1:
            S = (p1,) if p1 is not None else tuple(cp)
        else:
            S = tuple(_sensor_vertices(m, events[j - 2].sensor, rank))
        T = tuple(_sensor_vertices(m, events[j - 1].sensor, rank)) if j <= M else None
        inside = None
        if 2 <= j <= M and events[j - 2].kind == "A" and events[j - 1] == Event(
                events[j - 2].sensor, "D") and m.is_occupancy(events[j - 2].sensor):
            inside = events[j - 2].sensor
        if inside is not None and not literal:
            g = Graph(frozenset([inside]), frozenset())
        else:
            g = icra_subg(G, S, T, cp)
        out.append(Segment(j, S, T, g, inside))
    return out


@dataclass
class NFA:
    """A nondeterministic finite automaton over room names (``EPS`` = empty move)."""

    name: str
    states: List[str]
    starts: List[str]
    accepting: List[str]
    transitions: List[Tuple[str, str, str]]

    def _closure(self, S: Iterable[str]) -> Set[str]:
        out = set(S)
        stack = list(out)
        while stack:
            q = stack.pop()
            for a, lab, b in self.transitions:
                if a == q and lab == EPS and b not in out:
                    out.add(b)
                    stack.append(b)
        return out

    def accepts(self, word: Sequence[str]) -> bool:
        """Does a run from a start state reach an accepting state on ``word``?"""
        cur = self._closure(self.starts)
        for x in word:
            cur = self._closure(b for a, lab, b in self.transitions if a in cur and lab == x)
        return bool(cur & set(self.accepting))


def _goal_name(g: str, starts: Sequence[str], split: bool) -> str:
    return g + "'" if split and g in starts else g


def _nfa(m: Map, seg: Segment, first: bool, last: bool, literal: bool, split: bool,
         cp: Sequence[str]) -> NFA:
    rset = set(cp)
    V = seg.graph.vertices
    trans: List[Tuple[str, str, str]] = []
    if seg.inside is not None and V == {seg.inside}:
        o = seg.inside                     # corrected G_j: x is inside o
        if split:
            return NFA("M%d" % seg.j, [o, o + "'"], [o], [o + "'"], [(o, EPS, o + "'")])
        return NFA("M%d" % seg.j, [o], [o], [o], [])
    if first:
        starts = ["q0"]
        trans += [("q0", r, r) for r in cp if r in V]
    else:
        starts = [s for s in seg.starts if s in V]
    goals = [g for g in (seg.goals or ()) if g in V]
    sources = set(starts) | (V & rset)

    def add(t: Tuple[str, str, str]) -> None:
        if t not in trans:
            trans.append(t)

    for a, b in sorted(seg.graph.edges):
        for x, y in ((a, b), (b, a)):
            if x not in sources or (x in goals and x not in starts):
                continue
            if y in rset:
                add((x, y, y))
            elif y in goals:
                add((x, EPS, _goal_name(y, starts, split)))
    if split:
        for s in starts:
            if s in goals:
                add((s, EPS, s + "'"))
    loops = [r for r in cp if r in V and (literal or m.regions_of.get(r))]
    for r in loops:
        add((r, r, r))
    accepting = [r for r in cp if r in V] if last else [_goal_name(g, starts, split)
                                                        for g in goals]
    states = _ordered_unique(starts + [r for r in cp if r in V] +
                             [s for s in seg.starts if s in V] + accepting +
                             [x for t in trans for x in (t[0], t[2])])
    return NFA("M%d" % seg.j, states, starts, accepting, trans)


def icra_nfas(m: Map, history: HistoryLike, *, p1: Optional[str] = None,
              variant: str = "corrected", split_roles: Optional[bool] = None,
              rooms: Optional[Iterable[str]] = None) -> List[NFA]:
    """The NFAs ``M_1 .. M_{m+1}`` of ICRA §IV (Fig. 5), one per :func:`icra_subgraphs` part.

    States are the vertices of ``G_j`` (plus ``q0`` in ``M_1``, which moves to every room of
    ``G_1`` on that room's symbol).  Moving into a room reads the room's name; moving into a
    goal vertex is an ``EPS`` move; each room has a self-loop; goal vertices accept, and in
    ``M_{m+1}`` the rooms accept.  The corrected variant gives a goal that is also a start a
    separate state ``g'`` with an ``EPS`` move ``g -> g'`` and makes the inside of an
    occupancy interval the two states ``o -> o'``; ``split_roles`` overrides the split alone.
    The corrected variant also puts self-loops only on rooms that touch a free region.
    Fig. 5(a) is ``M_1`` exactly; Fig. 5(b) as printed has three defects
    (``docs/notes/paper-examples-icra.md`` section 3).
    """
    literal = _check_variant(variant)
    split = (not literal) if split_roles is None else split_roles
    cp = _rooms(m, rooms)
    segs = icra_subgraphs(m, history, p1=p1, variant=variant, rooms=cp)
    return [_nfa(m, s, i == 0, i == len(segs) - 1, literal, split, cp)
            for i, s in enumerate(segs)]


class CompositeAutomaton:
    """The composite automaton ``M`` of ICRA §IV (Fig. 6): ``M_1 .. M_{m+1}`` chained.

    States are ``(j, state)`` with ``j`` 0-based.  A link (an ``EPS`` move) joins each
    accepting state of ``M_j`` to the start state of ``M_{j+1}`` on the other side of the beam
    (or the same occupancy vertex).  ``final`` holds the states joined to the single
    acceptance state ``F``.
    """

    def __init__(self, m: Map, nfas: Sequence[NFA], history: Sequence[Event],
                 end_anywhere: bool) -> None:
        self.nfas = list(nfas)
        self.links: List[Tuple[Tuple[int, str], Tuple[int, str]]] = []
        for j in range(len(self.nfas) - 1):
            ev = history[j]
            for a in self.nfas[j].accepting:
                g = a[:-1] if a.endswith("'") else a
                tgt = m.other_side(g) if m.is_beam(ev.sensor) else g
                if tgt in self.nfas[j + 1].starts:
                    self.links.append(((j, a), (j + 1, tgt)))
        last = len(self.nfas) - 1
        fin = self.nfas[-1].states if end_anywhere else self.nfas[-1].accepting
        self.final: Set[Tuple[int, str]] = {(last, q) for q in fin}
        self._eps: Dict[Tuple[int, str], List[Tuple[int, str]]] = {}
        self._sym: Dict[Tuple[Tuple[int, str], str], List[Tuple[int, str]]] = {}
        for j, A in enumerate(self.nfas):
            for a, lab, b in A.transitions:
                if lab == EPS:
                    self._eps.setdefault((j, a), []).append((j, b))
                else:
                    self._sym.setdefault(((j, a), lab), []).append((j, b))
        for u, v in self.links:
            self._eps.setdefault(u, []).append(v)

    def link_names(self) -> List[Tuple[str, str]]:
        """Links as ``("M1.b11", "M2.b12")`` pairs."""
        return [("M%d.%s" % (u[0] + 1, u[1]), "M%d.%s" % (v[0] + 1, v[1]))
                for u, v in self.links]

    def _closure(self, S: Iterable[Tuple[int, str]]) -> Set[Tuple[int, str]]:
        out = set(S)
        stack = list(out)
        while stack:
            q = stack.pop()
            for w in self._eps.get(q, ()):
                if w not in out:
                    out.add(w)
                    stack.append(w)
        return out

    def accepts(self, word: Sequence[str]) -> bool:
        """Does ``word`` drive ``M`` from its start state to ``F``?"""
        cur = self._closure((0, s) for s in self.nfas[0].starts)
        for x in word:
            cur = self._closure(w for q in cur for w in self._sym.get((q, x), ()))
        return bool(cur & self.final)


def icra_composite(m: Map, history: HistoryLike, *, p1: Optional[str] = None,
                   variant: str = "corrected", split_roles: Optional[bool] = None,
                   end_anywhere: bool = False,
                   rooms: Optional[Iterable[str]] = None) -> CompositeAutomaton:
    """Build ``M`` from :func:`icra_nfas`.

    Both variants join only the acceptance states of ``M_{m+1}`` (its room states: "when
    i = m, we let all vertices in C_p be acceptance states") to ``F``, so x ends inside
    ``p_n``.  ``end_anywhere=True`` joins every state of ``M_{m+1}`` instead, the over-literal
    reading of the next sentence ("we connect all of M_{m+1}'s states to a single acceptance
    state F") taken alone; it accepts stories that end before the last recording.
    ``split_roles`` overrides the start/goal split of :func:`icra_nfas` alone.
    """
    _check_variant(variant)
    events = parse_history(history, m)
    nfas = icra_nfas(m, events, p1=p1, variant=variant, split_roles=split_roles, rooms=rooms)
    return CompositeAutomaton(m, nfas, events, bool(end_anywhere))


def icra_validate_agent_story(m: Map, story: StoryLike, history: HistoryLike, *,
                              variant: str = "corrected",
                              rooms: Optional[Iterable[str]] = None) -> bool:
    """ICRA Algorithm 1, VALIDATEAGENTSTORY, as a run of the composite automaton ``M``.

    Literal: returns false for a one-room story (loop ``for i = 2 to n``, erratum 8) and
    checks nothing about the history.  Corrected: rejects a history no single agent can
    produce, then accepts iff ``M`` (corrected) accepts the story; this equals
    ``validate(..., agents="single")``.
    """
    literal = _check_variant(variant)
    story_l, events = _inputs(m, story, history)
    if literal and len(story_l) == 1:
        return False
    if not literal and _check_history(m, events, "single") is not None:
        return False
    M = icra_composite(m, events, p1=story_l[0], variant=variant, rooms=rooms)
    return M.accepts(story_l)
