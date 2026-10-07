#!/usr/bin/env python3
"""Deterministic (seeded) generator of case files for the reference harness.

    python3 tools/reference/gen_cases.py --out DIR [--seed N]

Writes DIR/<set>.cases.json for each of the eight case sets below. Each file is an object
{"header": {...}, "maps": [...], "cases": [...]} that Harness.java reads directly.
Cases follow tests/fixtures/README.md plus an "op" field (see tools/reference/README.md).

Case sets:
  builtin              the four DetectiveGame games, Algorithms.test*/main, the builder check,
                       the STAR paper examples (tests/fixtures/paper/star.json) and the
                       bug reproductions of docs/notes/original-inventory.md
  star_random_single   random single-agent (story, history) pairs on STAR Fig. 2, each run
                       through validateAgentStory, getAgentStoryStatuses and getAgentStory
  star_random_multi    random multi-agent pairs on STAR Fig. 2 (validateAgentStoryMulti)
  random_maps          random small region-model maps (3-6 rooms, 1-3 beams, 0-3 occupancy)
  subgraphs            getSubGraph / getSubGraphMulti / getReachableSubgraph with random
                       start, vertex and goal sets, plus update_starting_vertex
  applet               the applet's Run-Validation pipeline from raw text-field strings
                       (well-formed and malformed), multi-step sessions with Reset and
                       mouse clicks, and for every single-run case a "__direct" companion
                       that calls validateAgentStory(Multi) on the same parsed input
  paper_cases          every paper example the original can express (tests/fixtures/paper/
                       {star,icra}.json): Problem-1 verdicts/paths/statuses and every
                       subgraph the papers draw (no randomness; regen_golden.sh adds the
                       "crosscheck" block with paper_crosscheck.py, see
                       docs/notes/phase1-crosscheck.md)
  edge_cases           inputs at the limits of Java int arithmetic (no randomness): maps with
                       65,535 filler vertices (compact key "filler_occupancy"), so that later
                       vertex ids exceed 65535 and Edge.getEdgeId collides (bug B7a), and
                       applet clicks whose coordinates overflow int/float

Only the Python standard library is used; random.Random(seed) with per-set derived seeds.
"""
import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_SEED = 20101213
GENERATOR_VERSION = "1.1.0"

# STAR Fig. 2 map. Edge order and vertex_order are chosen so that the generic builder in
# Harness.java reproduces DetectiveGame.getBasicGame() exactly (ids, neighbour insertion
# order, edge orientation); the harness's builder_check op verifies this.
STAR = {
    "name": "star_fig2",
    "source": "STAR Fig. 2 / Fig. 3(a); original DetectiveGame.getBasicGame() (minus its virtual SV-A edge)",
    "rooms": ["A", "B", "C"],
    "beams": {"b1": ["b1u", "b1d"], "b2": ["b2r", "b2l"]},
    "occupancy": ["o1", "o2"],
    "vertex_order": ["A", "B", "C", "b1u", "b1d", "b2l", "b2r", "o1", "o2"],
    "edges": [["A", "b1u"], ["A", "b1d"], ["A", "b2l"], ["A", "o1"], ["A", "C"],
              ["B", "b2r"], ["C", "b1d"], ["C", "o1"], ["b1u", "b2l"], ["b2l", "o1"],
              ["b1u", "o1"], ["b1d", "o1"], ["o1", "o2"], ["b2r", "o2"], ["B", "o2"]],
}

GAMES = ["SingleInfeasible", "SingleFeasible", "MultiFeasible", "MultiInfeasible"]
ALGO_OPS_SINGLE = ["validateAgentStory", "getAgentStoryStatuses", "getAgentStory"]


# ---------------------------------------------------------------- map helpers

def adjacency(m):
    adj = {v: [] for v in all_vertices(m)}
    for u, w in m["edges"]:
        if w not in adj[u]:
            adj[u].append(w)
        if u not in adj[w]:
            adj[w].append(u)
    return adj


def all_vertices(m):
    vs = list(m["rooms"])
    for sides in m["beams"].values():
        vs += sides
    vs += m["occupancy"]
    return vs


def side_info(m):
    """vertex -> (beam name, other side) for beam sides."""
    info = {}
    for b, (s0, s1) in m["beams"].items():
        info[s0] = (b, s1)
        info[s1] = (b, s0)
    return info


# ---------------------------------------------------------------- walks

class Agent:
    def __init__(self, pos):
        self.pos = pos          # current vertex (room, beam side after crossing, or occupancy)
        self.story = [pos]


def step_agent(rng, m, adj, sides, agent, occ_count, hist, record_story=True):
    """Move one agent to a random neighbour of its position, appending sensor events."""
    nbrs = adj[agent.pos]
    if not nbrs:
        return
    nxt = rng.choice(nbrs)
    if agent.pos in m["occupancy"]:
        o = agent.pos
        occ_count[o] -= 1
        if occ_count[o] == 0:
            hist.append([o, "D"])
    if nxt in m["rooms"]:
        agent.pos = nxt
        if record_story:
            agent.story.append(nxt)
    elif nxt in sides:
        beam, other = sides[nxt]
        hist.append([beam, "A"])
        agent.pos = other
    else:  # occupancy
        if occ_count[nxt] == 0:
            hist.append([nxt, "A"])
        occ_count[nxt] += 1
        agent.pos = nxt


def single_walk(rng, m, max_story=7, max_hist=10):
    """History of one real agent; the story is the rooms it entered."""
    adj, sides = adjacency(m), side_info(m)
    while True:
        a = Agent(rng.choice(m["rooms"]))
        occ = {o: 0 for o in m["occupancy"]}
        hist = []
        n_steps = rng.randint(0, 14)
        for _ in range(n_steps):
            step_agent(rng, m, adj, sides, a, occ, hist)
        # walk on until the agent is back in a room (bounded)
        for _ in range(8):
            if a.pos in m["rooms"]:
                break
            step_agent(rng, m, adj, sides, a, occ, hist)
        if len(a.story) <= max_story and len(hist) <= max_hist:
            return a.story, hist


def multi_walk(rng, m, max_story=7, max_hist=10):
    """Interleaved walks of x (agent 0) and 0-2 other agents; story is x's rooms."""
    adj, sides = adjacency(m), side_info(m)
    while True:
        k = rng.randint(1, 3)
        agents = [Agent(rng.choice(m["rooms"])) for _ in range(k)]
        occ = {o: 0 for o in m["occupancy"]}
        hist = []
        for _ in range(rng.randint(0, 16)):
            i = 0 if rng.random() < 0.5 else rng.randrange(k)
            step_agent(rng, m, adj, sides, agents[i], occ, hist, record_story=(i == 0))
        x = agents[0]
        for _ in range(8):
            if x.pos in m["rooms"]:
                break
            step_agent(rng, m, adj, sides, x, occ, hist)
        if len(x.story) <= max_story and len(hist) <= max_hist:
            return x.story, hist


def random_story(rng, m, lo=1, hi=7):
    return [rng.choice(m["rooms"]) for _ in range(rng.randint(lo, hi))]


def random_history(rng, m, lo=0, hi=10):
    sensors = list(m["beams"]) + list(m["occupancy"])
    h = []
    for _ in range(rng.randint(lo, hi)):
        s = rng.choice(sensors)
        if s in m["beams"]:
            h.append([s, "D" if rng.random() < 0.05 else "A"])  # beam "D": counts as a crossing
        else:
            h.append([s, rng.choice("AD")])
    return h


def perturb(rng, m, story, hist):
    story, hist = list(story), [list(e) for e in hist]
    sensors = list(m["beams"]) + list(m["occupancy"])
    kind = rng.choice(["swap", "drop", "insert", "room", "pair"])
    if kind == "swap" and len(hist) >= 2:
        i = rng.randrange(len(hist) - 1)
        hist[i], hist[i + 1] = hist[i + 1], hist[i]
    elif kind == "drop" and hist:
        del hist[rng.randrange(len(hist))]
    elif kind == "insert" and len(hist) < 10:
        s = rng.choice(sensors)
        hist.insert(rng.randint(0, len(hist)), [s, "A" if s in m["beams"] else rng.choice("AD")])
    elif kind == "pair" and len(hist) <= 8 and m["occupancy"]:
        o = rng.choice(m["occupancy"])
        i = rng.randint(0, len(hist))
        hist[i:i] = [[o, "A"], [o, "D"]]
    else:
        if story:
            story[rng.randrange(len(story))] = rng.choice(m["rooms"])
    return story, hist


def mixed_pair(rng, m, multi):
    r = rng.random()
    if r < 0.45:
        st, h = (multi_walk if multi else single_walk)(rng, m)
        return st, h, "walk"
    if r < 0.75:
        st, h = (multi_walk if multi else single_walk)(rng, m)
        st, h = perturb(rng, m, st, h)
        return st, h, "perturbed_walk"
    return random_story(rng, m), random_history(rng, m), "random"


# ---------------------------------------------------------------- random maps

def random_map(rng, idx):
    """Random region-model map: free regions R1..Rk; every beam has its two sides in two
    different regions, every occupancy sensor touches 1-3 regions, every room 1-2 regions;
    G joins two features that touch a common region (cliques per region), as STAR Fig. 3."""
    nr, nb, no = rng.randint(3, 6), rng.randint(1, 3), rng.randint(0, 3)
    rooms = list("ABCDEF"[:nr])
    beams = {}
    for i in range(nb):
        style = rng.choice([("u", "d"), ("l", "r"), ("r", "l")])
        beams["b%d" % (i + 1)] = ["b%d%s" % (i + 1, style[0]), "b%d%s" % (i + 1, style[1])]
    occ = ["o%d" % (i + 1) for i in range(no)]
    for _attempt in range(1000):
        k = rng.randint(2, 5)
        regions = [set() for _ in range(k)]
        for s0, s1 in beams.values():
            r0, r1 = rng.sample(range(k), 2)
            regions[r0].add(s0)
            regions[r1].add(s1)
        for o in occ:
            for r in rng.sample(range(k), rng.randint(1, min(3, k))):
                regions[r].add(o)
        for a in rooms:
            for r in rng.sample(range(k), rng.randint(1, min(2, k))):
                regions[r].add(a)
        if any(len(r) < 2 for r in regions):
            continue
        edges = set()
        for r in regions:
            mem = sorted(r)
            for i in range(len(mem)):
                for j in range(i + 1, len(mem)):
                    edges.add((mem[i], mem[j]))
        # connected once beam crossings are added
        vs = rooms + [s for p in beams.values() for s in p] + occ
        par = {v: v for v in vs}

        def find(v):
            while par[v] != v:
                par[v] = par[par[v]]
                v = par[v]
            return v
        for u, w in list(edges) + [tuple(p) for p in beams.values()]:
            par[find(u)] = find(w)
        if len({find(v) for v in vs}) != 1:
            continue
        edge_list = [list(e) if rng.random() < 0.5 else [e[1], e[0]] for e in sorted(edges)]
        rng.shuffle(edge_list)
        m = {
            "name": "rand%03d" % idx,
            "source": "gen_cases.py random region-model map",
            "rooms": rooms,
            "beams": beams,
            "occupancy": occ,
            "edges": edge_list,
            "regions": [sorted(r) for r in regions],
        }
        if rng.random() < 0.3:
            order = vs[:]
            rng.shuffle(order)
            m["vertex_order"] = order
        return m
    raise RuntimeError("could not build a connected random map")


# ---------------------------------------------------------------- applet text

def applet_single_text(hist):
    """Single-mode text for a history, or None if the applet cannot express it
    (it can only produce o-A immediately followed by o-D, and beam A events)."""
    toks, i = [], 0
    while i < len(hist):
        s, e = hist[i]
        if s in ("b1", "b2") and e == "A":
            toks.append(s)
            i += 1
        elif s in ("o1", "o2") and e == "A" and i + 1 < len(hist) and hist[i + 1] == [s, "D"]:
            toks.append(s)
            i += 2
        else:
            return None
    return ",".join(toks)


def applet_multi_text(hist):
    """Multi-mode text (o tokens toggle, starting inactive), or None if not expressible."""
    active = {"o1": False, "o2": False}
    toks = []
    for s, e in hist:
        if s in ("b1", "b2"):
            if e != "A":
                return None
        else:
            want = "D" if active[s] else "A"
            if e != want:
                return None
            active[s] = not active[s]
        toks.append(s)
    return ",".join(toks)


def applet_parse(story_text, sensor_text, mode):
    """Python mirror of CyberDetectiveDemoApplet.java:204-263, used only to build the
    "__direct" companion cases (the harness records the Java parse as parsed_story/history).
    Story: one vertex name per character (unknown characters resolve to null in Java; the
    companion passes the character itself, which the harness also resolves to null)."""
    story = list(story_text)
    toks = sensor_text.split(",")  # only exact "o1","o2","b1","b2" matter, so Java's
    hist = []                      # trailing-empty-string rule makes no difference
    active = {"o1": False, "o2": False}
    for t in toks:
        if t in ("o1", "o2"):
            if mode == "single":
                hist += [[t, "A"], [t, "D"]]
            else:
                active[t] = not active[t]
                hist.append([t, "A" if active[t] else "D"])
        elif t in ("b1", "b2"):
            hist.append([t, "A"])
    return story, hist


# canvas pixel centres of the clickable features (Environment.createExampleEnvironment;
# canvas = world / 2). Used to script click sessions.
CLICK = {
    "A": (50, 40), "B": (350, 40), "C": (50, 250),
    "o1": (200, 180), "o2": (350, 220),
    "b1u": (125, 142), "b1d": (125, 152),
    "b2l": (312, 110), "b2r": (322, 110),
}


# ---------------------------------------------------------------- case sets

def case(cid, op, source, **kw):
    c = {"id": cid, "source": source, "op": op}
    c.update(kw)
    return c


def gen_builtin(rng):
    cases = []
    for name in ["testGraphRoutines", "testStoryHistory", "testSingleAgent", "testMultiAgent", "main"]:
        cases.append(case("algorithms_%s" % name, "algorithms_test",
                          "Algorithms.%s() (Algorithms.java:453-496)" % name, name=name))
    cases.append(case("builder_check_star_fig2", "builder_check",
                      "generic builder (Harness.buildGame) vs DetectiveGame.getBasicGame()", map="star_fig2"))
    cases.append(case("game_dump_Basic", "game_dump", "DetectiveGame.getBasicGame()", game="Basic"))
    for gname in GAMES:
        mode = "multi" if gname.startswith("Multi") else "single"
        cases.append(case("game_dump_%s" % gname, "game_dump", "DetectiveGame.get%sGame()" % gname, game=gname))
        for start in ["fixed", "story"]:
            for op in ["validateAgentStory", "validateAgentStoryMulti", "getAgentStoryStatuses", "getAgentStory"]:
                cases.append(case("game_%s_%s_%s" % (gname, op, start), op,
                                  "DetectiveGame.get%sGame(); start=%s" % (gname, start),
                                  game=gname, start=start,
                                  mode="multi" if op == "validateAgentStoryMulti" else "single",
                                  game_mode=mode))
    # paper examples on the STAR Fig. 2 map
    paper = json.load(open(os.path.join(REPO, "tests", "fixtures", "paper", "star.json")))
    for pc in paper["cases"]:
        if pc.get("map") != "star_fig2":
            continue
        ops = ALGO_OPS_SINGLE if pc["mode"] == "single" else ["validateAgentStoryMulti"]
        for op in ops:
            cases.append(case("paper_%s__%s" % (pc["id"], op), op,
                              "tests/fixtures/paper/star.json case %s (%s)" % (pc["id"], pc.get("source", "")),
                              map="star_fig2", mode=pc["mode"], story=pc["story"], history=pc["history"]))
    # bug reproductions from docs/notes/original-inventory.md section 6
    H = lambda *ev: [[e[:-1], e[-1]] for e in ev]  # "o1A" -> ["o1","A"]
    repro = [
        ("B1_multi_false_negative", "validateAgentStoryMulti", "multi", ["B", "C"], H("o1A", "b2A", "o1D")),
        ("B1_multi_without_final_D", "validateAgentStoryMulti", "multi", ["B", "C"], H("o1A", "b2A")),
        ("B2_path_A_b1", "getAgentStory", "single", ["A"], H("b1A")),
        ("B3_path_AC_b1_b1", "getAgentStory", "single", ["A", "C"], H("b1A", "b1A")),
        ("B3_statuses_AC_b1_b1", "getAgentStoryStatuses", "single", ["A", "C"], H("b1A", "b1A")),
        # B4 needs an inconsistent input: story B with SV-A (start "fixed", as the inventory's
        # example); with the applet start (SV-B) the same input is consistent.
        ("B4_statuses_B_empty_fixed", "getAgentStoryStatuses", "single", ["B"], []),
        ("B4_path_B_empty_fixed", "getAgentStory", "single", ["B"], []),
        ("B4_statuses_C_empty_fixed", "getAgentStoryStatuses", "single", ["C"], []),
        ("B4_control_B_empty_applet_start", "getAgentStory", "single", ["B"], []),
        ("B5_single_o1D", "validateAgentStory", "single", ["A"], H("o1D")),
        ("B5_single_o1A_o2A", "validateAgentStory", "single", ["A"], H("o1A", "o2A")),
        ("B5_single_o1A_b1", "validateAgentStory", "single", ["A"], H("o1A", "b1A")),
        ("B5_multi_D_first", "validateAgentStoryMulti", "multi", ["A"], H("o1D")),
        ("B7_beam_D_counts_as_crossing", "validateAgentStory", "single", ["A", "C"], H("b1D")),
        ("empty_story_empty_history", "validateAgentStory", "single", [], []),
        ("empty_story_b1", "validateAgentStory", "single", [], H("b1A")),
        ("empty_story_multi", "validateAgentStoryMulti", "multi", [], []),
        ("unknown_room_in_story", "validateAgentStory", "single", ["A", "X"], H("b1A")),
        ("unknown_first_room", "validateAgentStory", "single", ["X", "A"], []),
        ("repeated_room_AA", "getAgentStory", "single", ["A", "A"], []),
        ("single_feasible_story_applet_start", "getAgentStory", "single", list("ACBAC"),
         H("b1A", "o1A", "o1D", "o2A", "o2D", "b2A")),
    ]
    for rid, op, mode, story, hist in repro:
        extra = {}
        if rid.endswith("_fixed"):
            extra["start"] = "fixed"
        cases.append(case("repro_" + rid, op, "docs/notes/original-inventory.md section 6",
                          map="star_fig2", mode=mode, story=story, history=hist, **extra))
    cases.append(case("repro_first_room_not_adjacent_to_SV_fixed", "validateAgentStory",
                      "inventory: DetectiveGame start (SV-A) with story CA", map="star_fig2",
                      mode="single", story=["C", "A"], history=[], start="fixed"))
    return {"maps": [STAR], "cases": cases}


def gen_star_random(rng, multi, n):
    cases = []
    seen = set()
    i = 0
    while len(seen) < n:
        story, hist, kind = mixed_pair(rng, STAR, multi)
        key = (tuple(story), tuple(map(tuple, hist)))
        if key in seen:
            continue
        seen.add(key)
        base = "%s%04d" % ("m" if multi else "s", i)
        i += 1
        ops = ["validateAgentStoryMulti"] if multi else ALGO_OPS_SINGLE
        for op in ops:
            cases.append(case("%s__%s" % (base, op), op, "gen_cases.py %s (%s)" % (
                "star_random_multi" if multi else "star_random_single", kind),
                map="star_fig2", mode="multi" if multi else "single",
                story=story, history=hist, generator_kind=kind))
    return {"maps": [STAR], "cases": cases}


def gen_random_maps(rng, n_maps):
    maps, cases = [], []
    for k in range(n_maps):
        m = random_map(rng, k)
        maps.append(m)
        name = m["name"]
        cases.append(case("%s__game_dump" % name, "game_dump", "gen_cases.py random map", map=name,
                          story=[m["rooms"][0]], history=[]))
        for j in range(2):
            story, hist, kind = mixed_pair(rng, m, False)
            for op in ALGO_OPS_SINGLE:
                cases.append(case("%s__s%d__%s" % (name, j, op), op, "gen_cases.py random map (%s)" % kind,
                                  map=name, mode="single", story=story, history=hist, generator_kind=kind))
        for j in range(2):
            story, hist, kind = mixed_pair(rng, m, True)
            cases.append(case("%s__m%d__validateAgentStoryMulti" % (name, j), "validateAgentStoryMulti",
                              "gen_cases.py random map (%s)" % kind, map=name, mode="multi",
                              story=story, history=hist, generator_kind=kind))
        story = random_story(rng, m, 1, 4)
        vs = all_vertices(m)
        sensors = [v for v in vs if v not in m["rooms"]]
        s = rng.choice(["SV"] + vs)
        goals = rng.sample(sensors, rng.randint(0, min(3, len(sensors))))
        cases.append(case("%s__getSubGraph" % name, "getSubGraph", "gen_cases.py random map",
                          map=name, story=story, s=s, goals=goals))
        occ_active = rng.sample(m["occupancy"], rng.randint(0, len(m["occupancy"])))
        cases.append(case("%s__getSubGraphMulti" % name, "getSubGraphMulti", "gen_cases.py random map",
                          map=name, story=story, s=s, goals=goals, occupancy_active=occ_active))
    return {"maps": maps, "cases": cases}


def gen_subgraphs(rng, n_each):
    cases = []
    vs = all_vertices(STAR)
    rooms = STAR["rooms"]
    sensors = [v for v in vs if v not in rooms]
    pairs = [list(p) for p in STAR["beams"].values()] + [[o] for o in STAR["occupancy"]]
    # Algorithms.testGraphRoutines() calls, with the same arguments, as individual cases
    tgr = [("A", ["b1d", "b1u"]), ("b1u", ["o1"]), ("b1d", ["o1"]), ("o1", ["b2r", "b2l"]),
           ("b2r", ["b2r", "o2"]), ("o2", ["o2", "B"])]
    for k, (s, goals) in enumerate(tgr):
        cases.append(case("testGraphRoutines_call%d" % (k + 1), "getSubGraph",
                          "Algorithms.java:457-462 (storyVertices = game.storyVertices = [SV,A,B,C])",
                          map="star_fig2", start="fixed", s=s, story_vertices=["SV", "A", "B", "C"], goals=goals))

    def goals_for():
        r = rng.random()
        if r < 0.6:
            return list(rng.choice(pairs))
        if r < 0.8:
            return rng.sample(vs, rng.randint(0, 3))
        return []

    for i in range(n_each):
        story = random_story(rng, STAR, 1, 5)
        s = rng.choice(["SV"] + vs)
        stv = sorted(set(story)) if rng.random() < 0.7 else rng.sample(["SV"] + vs, rng.randint(0, 5))
        rng.shuffle(stv)
        cases.append(case("sub%03d__getSubGraph" % i, "getSubGraph", "gen_cases.py subgraphs",
                          map="star_fig2", story=story, s=s, story_vertices=stv, goals=goals_for()))
    for i in range(n_each):
        story = random_story(rng, STAR, 1, 5)
        s = rng.choice(["SV"] + vs)
        occ = rng.sample(STAR["occupancy"], rng.randint(0, 2))
        stv = sorted(set(story))
        rng.shuffle(stv)
        cases.append(case("submulti%03d__getSubGraphMulti" % i, "getSubGraphMulti", "gen_cases.py subgraphs",
                          map="star_fig2", story=story, s=s, story_vertices=stv,
                          occupancy_active=occ, goals=goals_for() + ([s] if rng.random() < 0.3 else [])))
    for i in range(n_each):
        story = random_story(rng, STAR, 1, 5)
        s = rng.choice(["SV"] + vs)
        vp = rng.sample(["SV"] + vs, rng.randint(0, 8))
        if rng.random() < 0.8 and s not in vp:
            vp.append(s)
        rng.shuffle(vp)
        cases.append(case("reach%03d__getReachableSubgraph" % i, "getReachableSubgraph", "gen_cases.py subgraphs",
                          map="star_fig2", story=story, s=s, vp_set=vp, goals=goals_for()))
    # null / unknown arguments (vertexNameMap.get returns null)
    cases.append(case("sub_null_s", "getSubGraph", "unknown start vertex -> null", map="star_fig2",
                      story=["A"], s="X", goals=["b1u"]))
    cases.append(case("sub_null_goal", "getSubGraph", "unknown goal -> null", map="star_fig2",
                      story=["A"], s="A", goals=["X", "b1u"]))
    cases.append(case("reach_null_in_vp", "getReachableSubgraph", "unknown vp member -> null", map="star_fig2",
                      story=["A"], s="A", vp_set=["X", "A", "C"], goals=[]))
    cases.append(case("submulti_null_occ", "getSubGraphMulti", "unknown occupancy -> null", map="star_fig2",
                      story=["A"], s="A", occupancy_active=["X"], goals=["o1"]))
    # DetectiveGame.updateStartingVertex
    for v in ["A", "B", "C", "o1", "SV", "X", None]:
        cases.append(case("update_starting_vertex_%s" % v, "update_starting_vertex",
                          "DetectiveGame.updateStartingVertex (DetectiveGame.java:20-35)",
                          map="star_fig2", start="fixed", vertex=v))
    return {"maps": [STAR], "cases": cases}


def gen_applet(rng, n_single, n_multi):
    cases = []

    def single_run(cid, source, story_text, sensor_text, mode, kind):
        cases.append(case(cid, "applet", source, map="star_fig2", mode=mode,
                          steps=[{"run": {"story": story_text, "sensors": sensor_text, "mode": mode}}],
                          generator_kind=kind))
        story, hist = applet_parse(story_text, sensor_text, mode)
        op = "validateAgentStory" if mode == "single" else "validateAgentStoryMulti"
        cases.append(case(cid + "__direct", op,
                          "direct %s on the input the applet parses from %s" % (op, cid),
                          map="star_fig2", mode=mode, story=story, history=hist, generator_kind=kind))

    k = 0
    while k < n_single:
        story, hist = single_walk(rng, STAR)
        if rng.random() < 0.3:
            story, hist = perturb(rng, STAR, story, hist)
        t = applet_single_text(hist)
        if t is None:
            continue
        single_run("applet_single%03d" % k, "gen_cases.py applet (walk)", "".join(story), t, "single", "walk")
        k += 1
    k = 0
    while k < n_multi:
        story, hist = multi_walk(rng, STAR)
        if rng.random() < 0.3:
            story, hist = perturb(rng, STAR, story, hist)
        t = applet_multi_text(hist)
        if t is None:
            continue
        single_run("applet_multi%03d" % k, "gen_cases.py applet (walk)", "".join(story), t, "multi", "walk")
        k += 1
    malformed = [
        ("", "b1"), ("", ""), ("A", ""), ("AC", "b1,"), ("AC", ",b1"), ("AC", "b1,,"), ("AC", "b1,,o1"),
        ("AC", " b1"), ("AC", "b1 "), ("AC", "B1"), ("AC", "b3"), ("AC", "o3,b1"), ("AC", "x,b1,y"),
        ("ac", "b1"), ("Ac", "b1"), ("A C", "b1"), ("AD", "b1"), ("DA", ""), ("SV", ""), ("S", ""),
        ("A1", ""), ("AÅ", ""), ("CBA", "o1,o2,b2,b1"), ("A", "o1,o1"), ("A", "o1"),
        ("ACBAC", "b1;o1;o2;b2"), ("ACBAC", "b1,o1,o2,b2"), ("ACBAC", "b1,o1,b2,o2"),
        ("ACBAC", "b1,o1,o2,b2,o2,o1"), ("ACBAC", "b1,o2,o2,o1,b2,o1"), ("BC", "o1,b2,o1"), ("BC", "o1,b2"),
        ("AAAAAAA", ""), ("ABABABA", "b2,b2,b2,b2,b2,b2"), ("A", "b1,b1,b1,b1,b1,b1,b1,b1,b1,b1,b1,b1"),
    ]
    for j, (st, se) in enumerate(malformed):
        for mode in ["single", "multi"]:
            single_run("applet_malformed%02d_%s" % (j, mode), "gen_cases.py applet (hand-written edge case)",
                       st, se, mode, "malformed")
    # multi-step sessions (state carried between Run presses)
    sessions = [
        ("sticky_AD_then_AC", "B6: a crash skips the clear (l.270-271)",
         [{"run": {"story": "AD", "sensors": "b1", "mode": "single"}}, {"run": {"story": "AC", "sensors": "b1"}},
          {"reset": True}, {"run": {"story": "AC", "sensors": "b1"}}]),
        ("sticky_DA_corrupts_SV", "B6: updateStartingVertex(null) corrupts SV until Reset",
         [{"run": {"story": "DA", "sensors": "", "mode": "single"}}, {"run": {"story": "AC", "sensors": "b1"}},
          {"run": {"story": "AC", "sensors": "b1", "mode": "multi"}}, {"reset": True},
          {"run": {"story": "AC", "sensors": "b1", "mode": "single"}}]),
        ("path_crash_after_valid", "getAgentStory runs only after a true verdict",
         [{"run": {"story": "ACBAC", "sensors": "b1,o1,o2,b2", "mode": "single"}},
          {"run": {"story": "ACBAC", "sensors": "b1,o1,b2,o2", "mode": "single"}}]),
        ("mode_switch", "radio buttons between runs",
         [{"run": {"story": "BC", "sensors": "o1,b2,o1", "mode": "multi"}},
          {"run": {"mode": "single"}}, {"run": {"story": "", "sensors": ""}}, {"run": {"story": "A"}}]),
        ("nothing_to_validate_keeps_state", "empty story returns before parsing",
         [{"run": {"story": "AD", "sensors": "", "mode": "single"}}, {"run": {"story": ""}},
          {"run": {"story": "A"}}]),
    ]
    # click sessions: the paper's single-agent story via mouse clicks, and edge clicks
    clicks = lambda names: [{"click": list(CLICK[n])} for n in names]
    sessions += [
        ("click_single_feasible", "mouse flow for ACBAC",
         clicks(["A", "b1u", "C", "o1", "o2", "B", "b2r", "A", "C"]) + [{"run": {"mode": "single"}}]),
        ("click_then_multi", "mouse flow (one o1 token per visit) in multi mode",
         clicks(["A", "o1", "C"]) + [{"run": {"mode": "multi"}}]),
        ("click_unreachable", "clicks on features that are not offered are ignored",
         clicks(["B", "A", "C", "o2", "b1d", "b1d", "C"]) + [{"run": {"mode": "single"}}]),
        ("click_b2_overlap_strip", "y in [180,185) on the b2 strips hits room B (Environment.java:100-108)",
         [{"click": [312, 91]}, {"click": [322, 91]}, {"click": [312, 92]}, {"click": [312, 93]},
          {"click": [5, 5]}, {"click": [399, 299]}, {"click": [0, 0]}, {"click": [-5, 10]}]),
        ("click_after_reset_stale_vertices", "Reset replaces env.game but not the Environment's vertices",
         [{"run": {"story": "C", "sensors": "", "mode": "single"}}] + clicks(["C", "o1"]) +
         [{"reset": True}] + clicks(["A", "o1", "C"]) + [{"run": {}}]),
        ("click_reclick_same", "re-clicking the current room/beam/occupancy",
         clicks(["A", "A", "b1u", "b1d", "o1", "o1", "C"]) + [{"run": {"mode": "single"}},
                                                           {"run": {"mode": "multi"}}]),
    ]
    rnd_feats = list(CLICK)
    for j in range(10):
        seq = [rng.choice(rnd_feats) for _ in range(rng.randint(2, 10))]
        steps = clicks(seq)
        steps.append({"run": {"mode": rng.choice(["single", "multi"])}})
        sessions.append(("click_random%02d" % j, "random click sequence", steps))
    for sid, src, steps in sessions:
        cases.append(case("applet_session_" + sid, "applet", src, map="star_fig2", steps=steps,
                          generator_kind="session"))
    return {"maps": [STAR], "cases": cases}


def _paper_history(s):
    """'b1 o1A o1D' -> [["b1","A"],["o1","A"],["o1","D"]] (beams always A)."""
    out = []
    for t in s.split():
        if t[-1] in "AD" and t[0] == "o":
            out.append([t[:-1], t[-1]])
        else:
            out.append([t, "A"])
    return out


def gen_paper(rng):
    """The paper cross-check set (docs/notes/phase1-crosscheck.md, Appendix A). Uses no
    randomness (rng is ignored)."""
    H = _paper_history
    star = json.load(open(os.path.join(REPO, "tests", "fixtures", "paper", "star.json")))
    icra = json.load(open(os.path.join(REPO, "tests", "fixtures", "paper", "icra.json")))
    icra_maps = {m["name"]: m for m in icra["maps"]}
    maps = [STAR, icra_maps["icra_fig1"], icra_maps["icra_fig2"]]
    cases = []

    # 1. Problem-1 verdicts / paths for every paper case the original can express
    for fname, fx in (("star", star), ("icra", icra)):
        for pc in fx["cases"]:
            if pc["map"] not in ("star_fig2", "icra_fig1", "icra_fig2"):
                continue
            ops = ALGO_OPS_SINGLE if pc["mode"] == "single" else ["validateAgentStoryMulti"]
            for op in ops:
                cases.append(case("%s__%s" % (pc["id"], op), op,
                                  "tests/fixtures/paper/%s.json case %s (%s)" % (fname, pc["id"], pc.get("source", "")),
                                  map=pc["map"], mode=pc["mode"], story=pc["story"], history=pc["history"],
                                  paper_case="tests/fixtures/paper/%s.json#%s" % (fname, pc["id"])))

    # 2. STAR Fig. 4 subgraphs (Alg. 1/2) for eq. (1)+(2); VG = SENSORVERTICES(r_j) (both beam sides)
    st = list("ACBAC")
    eq2 = H("b1 o1A o1D b2 o2A o2D")
    fig4 = [("G1", "A", ["b1u", "b1d"]), ("G21", "b1d", ["o1"]), ("G22", "b1u", ["o1"]),
            ("G3", "o1", ["b2r", "b2l"]), ("G4", "b2r", ["o2"]), ("G5", "o2", [])]
    for name, s, goals in fig4:
        cases.append(case("star_fig4_%s__getSubGraph" % name, "getSubGraph",
                          "STAR Fig. 4 %s (Alg. 1); star.json star_eq1_eq2_single.expected.subgraphs_fig4" % name,
                          map="star_fig2", mode="single", story=st, history=eq2, s=s, goals=goals,
                          paper_case="tests/fixtures/paper/star.json#star_eq1_eq2_single/subgraphs_fig4/%s" % name))
    # 3. STAR Fig. 6(a): Alg. 4 line 2 (GETREACHABLESUBGRAPH with VC = Cp u O u {s}) before clique-ification
    eq3 = H("b1 o1A o2A b2 o2D o1D")
    cases.append(case("star_fig6a__getReachableSubgraph", "getReachableSubgraph",
                      "STAR Fig. 6(a): G0_4 before clique-ification (Alg. 4 line 2)",
                      map="star_fig2", mode="multi", story=st, history=eq3, s="A",
                      vp_set=["A", "B", "C", "o1", "o2", "A"], goals=["b2r", "b2l"],
                      paper_case="tests/fixtures/paper/star.json#star_eq1_eq3_multi/fig6a_G0_4_before_cliquification"))
    # 4. STAR Fig. 6(b) / Fig. 7: the nine Alg. 4 subgraphs
    for name, sg in star["cases"][1]["expected"]["composite_fig7"]["subgraphs"].items():
        cases.append(case("star_fig7_%s__getSubGraphMulti" % name, "getSubGraphMulti",
                          "STAR Fig. 7 %s (Alg. 4)%s" % (name, "; also Fig. 6(b)" if name == "G0_4" else ""),
                          map="star_fig2", mode="multi", story=st, history=eq3, s=sg["start"],
                          goals=sg["goals"], occupancy_active=sg["active_occupancy"],
                          paper_case="tests/fixtures/paper/star.json#star_eq1_eq3_multi/composite_fig7/%s" % name))
    # 5. ICRA Fig. 4(a)/(b) and the subgraphs G_j of both ABDEC histories, one call per start vertex
    abdec = list("ABDEC")
    rooms = ["A", "B", "C", "D", "E"]
    for hkey, hb in (("b1 b3 o2 o2 b4", "b3"), ("b1 b2 o2 o2 b4", "b2")):
        hist = H("b1 %s o2A o2D b4" % hb)
        side = icra_maps["icra_fig2"]["beams"]
        plan = [(1, ["A"], side["b1"]), (2, side["b1"], side[hb]), (3, side[hb], ["o2"]),
                (5, ["o2"], side["b4"]), (6, side["b4"], [])]
        for j, starts, goals in plan:
            for s in starts:
                cases.append(case("icra_G%d_%s_from_%s__getSubGraph" % (j, hkey.replace(" ", ""), s), "getSubGraph",
                                  "ICRA subgraph G_%d for history %s, start %s%s" % (
                                      j, hkey, s, " (Fig. 4(a)/(b))" if hb == "b3" and j <= 2 else ""),
                                  map="icra_fig2", mode="single", story=abdec, history=hist, s=s, goals=goals,
                                  story_vertices=rooms,
                                  paper_case="tests/fixtures/paper/icra.json#figures/subgraphs_G_j/%s/j=%d" % (hkey, j)))
    return {"maps": maps, "cases": cases}


# ---------------------------------------------------------------- edge_cases

FILLERS = 65535  # SV=2, then the vertices before "...", then 65,535 fillers: later ids > 65535


def _filler_map(name, rooms, beams, before, after, edges, note):
    return {"name": name, "note": note, "rooms": rooms, "beams": beams, "occupancy": [],
            "filler_occupancy": FILLERS, "vertex_order": before + ["..."] + after, "edges": edges}


def gen_edge_cases(rng):
    """Bug B7a (Edge.getEdgeId = min*65536 + max in int arithmetic) on compact maps, and
    click coordinates beyond int/float precision. Deterministic; ``rng`` is unused."""
    maps = [
        # ids: SV=2 A=3 B=4 C=5, fillers 6..65540, Z=65541; getEdgeId(A, Z) = getEdgeId(B, C)
        _filler_map("b7a_line", ["A", "B", "C", "Z"], {}, ["A", "B", "C"], ["Z"],
                    [["A", "B"], ["B", "C"]],
                    "A-B-C plus an isolated room Z with id 65541: areNeighbors(A, Z) is true "
                    "because getEdgeId(3, 65541) == getEdgeId(4, 5) (the edge B-C)"),
        # ids: SV=2 A=3 B=4 C=5 b1u=6 b1d=7, fillers, Z=65543; getEdgeId(A, Z) = getEdgeId(B, b1d)
        _filler_map("b7a_beam", ["A", "B", "C", "Z"], {"b1": ["b1u", "b1d"]},
                    ["A", "B", "C", "b1u", "b1d"], ["Z"],
                    [["A", "B"], ["B", "b1d"], ["A", "b1u"], ["b1u", "b1d"]],
                    "as b7a_line with a beam: after s = GP.vertexMap.get(Z) = null the next "
                    "recording's hasEdgeBetweenVertices(v, s) throws in Edge.getEdgeId"),
        # the map's own edges collide: B-b1d is never stored (A-Z has its id), SV-Z overwrites A-b1d's id
        _filler_map("b7a_twin_edges", ["A", "B", "C", "Z"], {"b1": ["b1u", "b1d"]},
                    ["A", "B", "C", "b1u", "b1d"], ["Z"],
                    [["A", "b1d"], ["B", "b1d"], ["A", "Z"], ["Z", "b1u"], ["A", "B"], ["B", "C"]],
                    "edges A-Z and B-b1d share id 4*65536+7, so G stores A-Z (built first) and "
                    "drops B-b1d; updateStartingVertex(Z) stores SV-Z under A-b1d's id 3*65536+7"),
    ]
    ops = ["validateAgentStory", "getAgentStoryStatuses", "getAgentStory", "validateAgentStoryMulti"]
    plan = [
        ("b7a_line", ["A", "Z", "A", "B", "C"], [], "s = null after A->Z, NPE at the next story test"),
        ("b7a_line", ["A", "B", "C", "B", "A", "Z"], [],
         "s = null on the last room: verdict true for an unreachable Z (false positive)"),
        ("b7a_beam", ["A", "B", "A", "Z"], [["b1", "A"]],
         "s = null with a recording pending: NPE in Edge.getEdgeId via hasEdgeBetweenVertices"),
        ("b7a_twin_edges", ["A", "B", "A", "Z"], [["b1", "A"]], "story on a map whose edges collide"),
        ("b7a_twin_edges", ["Z", "A", "B"], [["b1", "A"]], "starting room Z: SV-Z overwrites A-b1d"),
    ]
    cases = []
    for k, (mn, story, hist, what) in enumerate(plan):
        for op in ops:
            cases.append(case("b7a_%d_%s" % (k, op), op, "bug B7a (edge-id collision): " + what,
                              map=mn, story=story, history=hist))
    for op, s_, goals in (("getSubGraph", "A", ["b1u", "b1d", "Z"]), ("getSubGraph", "Z", ["b1u", "b1d"]),
                          ("getSubGraph", "B", ["Z", "b1d"]), ("getSubGraphMulti", "B", ["b1d"])):
        kw = {"occupancy_active": ["b1d"]} if op == "getSubGraphMulti" else {}
        cases.append(case("b7a_twin_edges_%s_from_%s" % (op, s_), op,
                          "bug B7a: subgraph of a map whose edges collide (goals %s)" % ",".join(goals),
                          map="b7a_twin_edges", story=["A", "B", "C", "Z"], history=[], start="fixed",
                          s=s_, goals=goals, **kw))
    clicks = [[16777217, 16777219], [33554435, 16777215], [4294967396, 10], [2147483647, -2147483648],
              [9223372036854775807, -9223372036854775808], [1e10, 10], [-1e10, -3.7], [1.5e300, 2.9999],
              [-0.5, 99.99], [60, 40]]
    cases.append(case("applet_click_limits", "applet",
                      "mouseClicked scaling (int)(px * 800f / 400) for coordinates beyond 2^24 and the "
                      "int range; the harness converts JSON numbers with Number.intValue()",
                      steps=[{"click": xy} for xy in clicks]))
    return {"maps": maps, "cases": cases}


SETS = [
    ("builtin", lambda r: gen_builtin(r)),
    ("star_random_single", lambda r: gen_star_random(r, False, 320)),
    ("star_random_multi", lambda r: gen_star_random(r, True, 320)),
    ("random_maps", lambda r: gen_random_maps(r, 40)),
    ("subgraphs", lambda r: gen_subgraphs(r, 100)),
    ("applet", lambda r: gen_applet(r, 80, 80)),
    ("paper_cases", lambda r: gen_paper(r)),
    ("edge_cases", lambda r: gen_edge_cases(r)),
]
# sets that use no randomness (header: set_seed null, python_random "not used")
DETERMINISTIC_SETS = {"paper_cases", "edge_cases"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output directory for <set>.cases.json")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for k, (name, fn) in enumerate(SETS):
        set_seed = a.seed * 1000 + k
        data = fn(random.Random(set_seed))
        ids = [c["id"] for c in data["cases"]]
        assert len(ids) == len(set(ids)), "duplicate case ids in " + name
        det = name in DETERMINISTIC_SETS
        out = {
            "header": {
                "set": name,
                "generator": "tools/reference/gen_cases.py",
                "generator_version": GENERATOR_VERSION,
                "seed": a.seed,
                "set_seed": None if det else set_seed,
                "python_random": ("not used" if det else
                                  "random.Random(set_seed), set_seed = seed*1000 + set index"),
                "num_cases": len(data["cases"]),
            },
            "maps": data["maps"],
            "cases": data["cases"],
        }
        with open(os.path.join(a.out, name + ".cases.json"), "w", encoding="ascii") as f:
            f.write(json.dumps(out, ensure_ascii=True, separators=(",", ":")) + "\n")
        print("%-20s %5d cases" % (name, len(data["cases"])), file=sys.stderr)


if __name__ == "__main__":
    main()
