#!/usr/bin/env python3
"""Live differential test: ``compat="original"`` against the real Java original.

    python3 tools/reference/differential.py [--n 5000] [--seed S] [--jobs J] [--out DIR]
    python3 tools/reference/differential.py --cases FILE      # replay a case file (e.g. a repro)

Generates fresh, seeded random cases (a seed range disjoint from the golden fixtures'),
runs them through the reference harness (``run.sh``, canonical flags
``-XX:hashCode=2``) and through :func:`cyber_detectives.compat.original.run_harness_case`,
and compares every field of every result (``return``, ``stdout``, ``exception`` with class,
message, top/origin frame and full trace, and the extras ``aliases``, ``graph_dump``,
``graph_dump_exception``, ``call_exception``; applet ops step record by step record).
Validate cases that use the applet start are also replayed through
:func:`cyber_detectives.compat.original.validate_compat` (verdict, path, crash and stdout).

Case mix (each "unit" draws one kind and emits one or more cases on the same input):

- STAR Fig. 2 single-agent pairs (validateAgentStory, getAgentStoryStatuses, getAgentStory)
  and multi-agent pairs (validateAgentStoryMulti), from walks, perturbed walks and random
  noise (``gen_cases.mixed_pair``); some with unknown story rooms or ``start: "fixed"``;
- random small region-model maps (``gen_cases.random_map``) and *big* maps (5-9 rooms,
  2-4 beams, 1-4 occupancy sensors, 2-3 regions) built so that at least one neighbour set
  has 11+ members, i.e. a treeified ``HashMap`` bin under ``-XX:hashCode=2``;
- getSubGraph / getSubGraphMulti / getReachableSubgraph with random starts, story vertex
  sets, goals, active occupancy and vp sets (unknown names now and then), game_dump and
  update_starting_vertex (``null`` and unknown vertices included);
- applet runs: walk-derived text, malformed text (odd separators, case, spaces, unknown
  tokens, non-ASCII), and multi-step sessions mixing Run (with partial field updates and
  mode switches), Reset and mouse clicks (feature centres, jittered and arbitrary pixels,
  now and then coordinates beyond 2^24, the int range or as JSON doubles);
- edge-id collisions (bug B7a): maps with 65,535 edgeless filler vertices (compact key
  ``filler_occupancy``) between a small core and a few later vertices whose ids exceed 65535.

On mismatch the script minimises up to ``--max-repros`` distinct mismatch kinds (deleting
history events, story rooms, set members, applet steps / characters / tokens and map
edges while the mismatch persists), prints them and writes them to ``DIR/repro_*.json``
(replay with ``--cases``). Exit status: 0 all equal, 1 mismatch, 2 setup or harness error.

Environment: the ``run.sh`` / ``build.sh`` variables (``CD_CACHE``, ``CD_JAVA_HOME``,
``CD_ORIGINAL_DIR``, ``CD_SKIP_BUILD``, ...); see tools/reference/README.md. Standard library
only.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUN_SH = os.path.join(HERE, "run.sh")
for _p in (HERE, os.path.join(REPO, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in tools/reference
import gen_cases as G  # noqa: E402  (tools/reference/gen_cases.py: walks, random maps, STAR)
sys.dont_write_bytecode = _dwb
from cyber_detectives.compat import javahash  # noqa: E402
from cyber_detectives.compat.original import (  # noqa: E402
    HarnessError, JavaException, capture_stdout, run_harness_case, validate_compat)

# Golden fixtures use random.Random(20101213 * 1000 + k), k < 7. Here every unit i has its
# own generator random.Random(seed * UNIT_STRIDE + i), with a different default seed, so the
# inputs are fresh and any single unit can be regenerated (--only).
DEFAULT_SEED = 20261006
UNIT_STRIDE = 10 ** 7
EXC_KEYS = {"class", "message", "top_frame", "origin_frame", "trace"}
STAR = G.STAR
ROOM_LETTERS = "ABCDEFGHI"

# ============================================================================ generation


def _H(hist):
    return [list(e) for e in hist]


def _maybe_unknown_room(rng, story):
    """Occasionally put a room name the map does not have into the story (Java: null)."""
    if story and rng.random() < 0.04:
        story = list(story)
        story[rng.randrange(len(story))] = rng.choice(["X", "Z", "SV", "b1u"])
    return story


def _pair_cases(rng, uid, mapname, m, multi, ops=None):
    story, hist, kind = G.mixed_pair(rng, m, multi)
    story = _maybe_unknown_room(rng, story)
    if ops is None:
        ops = ["validateAgentStoryMulti"] if multi else list(G.ALGO_OPS_SINGLE)
        if multi and rng.random() < 0.3:
            ops += list(G.ALGO_OPS_SINGLE)
    extra = {}
    if rng.random() < 0.1:
        extra["start"] = "fixed"
    out = []
    for op in ops:
        out.append(G.case("%s__%s" % (uid, op), op, "differential.py (%s)" % kind, map=mapname,
                          mode="multi" if op == "validateAgentStoryMulti" else "single",
                          story=story, history=_H(hist), **extra))
    return out


def big_map(rng, name):
    """A connected region-model map in which some vertex has >= 11 neighbours (a treeified
    bin in its neighbour HashSet when every identity hash is 1)."""
    for _ in range(1000):
        nr, nb, no = rng.randint(5, 9), rng.randint(2, 4), rng.randint(1, 4)
        rooms = list(ROOM_LETTERS[:nr])
        beams = {}
        for i in range(nb):
            style = rng.choice([("u", "d"), ("l", "r"), ("r", "l")])
            beams["b%d" % (i + 1)] = ["b%d%s" % (i + 1, style[0]), "b%d%s" % (i + 1, style[1])]
        occ = ["o%d" % (i + 1) for i in range(no)]
        k = rng.randint(2, 3)
        regions = [set() for _ in range(k)]
        for s0, s1 in beams.values():
            r0, r1 = rng.sample(range(k), 2)
            regions[r0].add(s0)
            regions[r1].add(s1)
        for o in occ:
            for r in rng.sample(range(k), rng.randint(1, min(2, k))):
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
        # sparsify a little so not every region is a full clique
        edges = {e for e in sorted(edges) if rng.random() < 0.9}  # sorted: no str-hash order
        vs = rooms + [s for p in beams.values() for s in p] + occ
        deg = {v: 0 for v in vs}
        for u, w in edges:
            deg[u] += 1
            deg[w] += 1
        if max(deg.values()) < 11:
            continue
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
        m = {"name": name, "source": "differential.py big region-model map (>= 11 neighbours)",
             "rooms": rooms, "beams": beams, "occupancy": occ, "edges": edge_list,
             "regions": [sorted(r) for r in regions]}
        if rng.random() < 0.4:
            order = vs[:]
            rng.shuffle(order)
            m["vertex_order"] = order
        return m
    raise RuntimeError("could not build a big map")


def small_map(rng, name):
    m = G.random_map(rng, 0)
    m["name"] = name
    m["source"] = "differential.py (gen_cases.random_map)"
    return m


def _pick_map(rng, uid, p_star=0.4, p_big=0.3):
    r = rng.random()
    if r < p_star:
        return "star_fig2", STAR, []
    m = big_map(rng, uid + "_map") if r < p_star + p_big else small_map(rng, uid + "_map")
    return m["name"], m, [m]


def _names(rng, pool, lo, hi, p_unknown=0.05):
    out = rng.sample(pool, rng.randint(lo, min(hi, len(pool))))
    if out and rng.random() < p_unknown:
        out[rng.randrange(len(out))] = "X"
    return out


def unit_star_single(rng, uid):
    return _pair_cases(rng, uid, "star_fig2", STAR, False), []


def unit_star_multi(rng, uid):
    return _pair_cases(rng, uid, "star_fig2", STAR, True), []


def unit_small_map(rng, uid):
    m = small_map(rng, uid + "_map")
    cases = _pair_cases(rng, uid + "s", m["name"], m, False) + _pair_cases(rng, uid + "m", m["name"], m, True)
    return cases, [m]


def unit_big_map(rng, uid):
    m = big_map(rng, uid + "_map")
    multi = rng.random() < 0.4
    cases = _pair_cases(rng, uid, m["name"], m, multi)
    if rng.random() < 0.3:
        cases.append(G.case(uid + "__game_dump", "game_dump", "differential.py big map", map=m["name"],
                            story=G.random_story(rng, m, 1, 4), history=_H(G.random_history(rng, m, 0, 4))))
    return cases, [m]


def unit_subgraph(rng, uid):
    name, m, maps = _pick_map(rng, uid)
    vs = G.all_vertices(m)
    story = G.random_story(rng, m, 1, 5)
    s = rng.choice(["SV"] + vs + (["X"] if rng.random() < 0.03 else []))
    pairs = [list(p) for p in m["beams"].values()] + [[o] for o in m["occupancy"]]
    r = rng.random()
    goals = list(rng.choice(pairs)) if r < 0.5 else (_names(rng, vs, 0, 4) if r < 0.85 else [])
    kw = dict(map=name, story=story, s=s, goals=goals)
    if rng.random() < 0.15:
        kw["start"] = "fixed"
    op = rng.choice(["getSubGraph", "getSubGraphMulti", "getReachableSubgraph"])
    if op in ("getSubGraph", "getSubGraphMulti") and rng.random() < 0.6:
        stv = sorted(set(story)) if rng.random() < 0.5 else _names(rng, ["SV"] + vs, 0, 12)
        rng.shuffle(stv)
        kw["story_vertices"] = stv
    if op == "getSubGraphMulti":
        kw["occupancy_active"] = _names(rng, m["occupancy"], 0, len(m["occupancy"]))
        if rng.random() < 0.3:
            kw["goals"] = goals + [s]
    if op == "getReachableSubgraph":
        vp = _names(rng, ["SV"] + vs, 0, len(vs) + 1)
        if rng.random() < 0.8 and s not in vp:
            vp.append(s)
        rng.shuffle(vp)
        kw["vp_set"] = vp
    if rng.random() < 0.2:
        kw["history"] = _H(G.random_history(rng, m, 0, 4))
    return [G.case(uid + "__" + op, op, "differential.py subgraphs", **kw)], maps


def unit_update_starting_vertex(rng, uid):
    name, m, maps = _pick_map(rng, uid, p_star=0.4, p_big=0.3)
    v = rng.choice(G.all_vertices(m) + ["SV", "X", None, None])
    c = G.case(uid + "__update_starting_vertex", "update_starting_vertex", "differential.py", map=name,
               start=rng.choice(["fixed", "fixed", "story"]), story=G.random_story(rng, m, 1, 3), vertex=v)
    return [c], maps


def unit_game_dump(rng, uid):
    name, m, maps = _pick_map(rng, uid, p_star=0.1, p_big=0.5)
    c = G.case(uid + "__game_dump", "game_dump", "differential.py", map=name,
               story=_maybe_unknown_room(rng, G.random_story(rng, m, 0, 4)),
               history=_H(G.random_history(rng, m, 0, 5)),
               start=rng.choice(["fixed", "story", "story"]))
    return [c], maps


# ---- applet

STORY_CHARS = "ABCABCABCABCDSVXac 1,Å"
SENSOR_TOKENS = ["b1", "b2", "o1", "o2"] * 6 + ["b3", "o3", "B1", "O1", " b1", "b1 ", "", "x", "b", "o", "b12",
                                                 "ö1"]


def _malformed_story(rng):
    return "".join(rng.choice(STORY_CHARS) for _ in range(rng.randint(0, 8)))


def _malformed_sensors(rng):
    toks = [rng.choice(SENSOR_TOKENS) for _ in range(rng.randint(0, 9))]
    sep = "," if rng.random() < 0.85 else rng.choice([";", ", ", ",,", " "])
    s = sep.join(toks)
    if rng.random() < 0.1:
        s = rng.choice([",", ",,", " "]) + s
    if rng.random() < 0.1:
        s += rng.choice([",", ",,", " "])
    return s


def _walk_text(rng, multi):
    """(story text, sensor text) the applet can express, from a (perturbed) walk."""
    for _ in range(200):
        story, hist = (G.multi_walk if multi else G.single_walk)(rng, STAR)
        if rng.random() < 0.3:
            story, hist = G.perturb(rng, STAR, story, hist)
        t = (G.applet_multi_text if multi else G.applet_single_text)(hist)
        if t is not None:
            return "".join(story), t
    return "A", ""


_BIG_COORDS = [16777217, 16777219, 33554435, -16777217, 2147483647, -2147483648, 4294967396,
               9223372036854775807, -9223372036854775808, 1e10, -1e10, 2.5, -0.5, 99.99, 1.5e300]


def _click(rng):
    r = rng.random()
    if r < 0.04:  # Java float/int limits of the click scaling and of Number.intValue()
        return [rng.choice(_BIG_COORDS + [rng.randint(-20, 420)]), rng.choice(_BIG_COORDS + [rng.randint(-20, 320)])]
    if r < 0.5:
        x, y = G.CLICK[rng.choice(sorted(G.CLICK))]
        return [x, y]
    if r < 0.8:
        x, y = G.CLICK[rng.choice(sorted(G.CLICK))]
        return [x + rng.randint(-12, 12), y + rng.randint(-12, 12)]
    return [rng.randint(-20, 420), rng.randint(-20, 320)]


def unit_applet_run(rng, uid):
    mode = rng.choice(["single", "multi"])
    if rng.random() < 0.55:
        st, se = _walk_text(rng, mode == "multi")
        kind = "walk"
    else:
        st, se = _malformed_story(rng), _malformed_sensors(rng)
        kind = "malformed"
    c = G.case(uid + "__applet", "applet", "differential.py applet (%s)" % kind, map="star_fig2", mode=mode,
               steps=[{"run": {"story": st, "sensors": se, "mode": mode}}])
    story, hist = G.applet_parse(st, se, mode)
    op = "validateAgentStory" if mode == "single" else "validateAgentStoryMulti"
    d = G.case(uid + "__direct", op, "direct %s on the applet's parse" % op, map="star_fig2", mode=mode,
               story=story, history=hist)
    return [c, d], []


def unit_applet_session(rng, uid):
    steps = []
    for _ in range(rng.randint(1, 10)):
        r = rng.random()
        if r < 0.4:
            run = {}
            if rng.random() < 0.7:
                st, se = (_walk_text(rng, rng.random() < 0.5) if rng.random() < 0.6
                          else (_malformed_story(rng), _malformed_sensors(rng)))
                if rng.random() < 0.85:
                    run["story"] = st
                if rng.random() < 0.85:
                    run["sensors"] = se
            if rng.random() < 0.6:
                run["mode"] = rng.choice(["single", "multi"])
            steps.append({"run": run})
        elif r < 0.5:
            steps.append({"reset": True})
        else:
            steps.append({"click": _click(rng)})
    if "run" not in steps[-1]:
        steps.append({"run": {"mode": rng.choice(["single", "multi"])} if rng.random() < 0.5 else {}})
    return [G.case(uid + "__applet_session", "applet", "differential.py applet session", map="star_fig2",
                   steps=steps)], []


def collision_map(rng, name):
    """A small map whose later vertices get ids above 65535 (``filler_occupancy``), so that
    ``Edge.getEdgeId`` collides (bug B7a): a core of 2-3 rooms and up to 4 sensor vertices,
    65,535 fillers, then 1-2 rooms and maybe some sensor vertices; random edges."""
    core_rooms = ["A", "B", "C"][:rng.randint(2, 3)]
    tail_rooms = ["Z", "Y"][:rng.randint(1, 2)]
    beams, occ, core_s, tail_s = {}, [], [], []
    for b in ["b1", "b2"][:rng.randint(0, 2)]:
        beams[b] = [b + "u", b + "d"]
        (tail_s if rng.random() < 0.25 else core_s).extend(beams[b])
    for o in ["o1", "o2"][:rng.randint(0, 2)]:
        occ.append(o)
        (tail_s if rng.random() < 0.25 else core_s).append(o)
    core, tail = core_rooms + core_s, tail_rooms + tail_s
    rng.shuffle(core)
    rng.shuffle(tail)
    allv = core + tail
    edges = []
    for i in range(len(allv)):
        for j in range(i + 1, len(allv)):
            if rng.random() < 0.35:
                edges.append([allv[i], allv[j]] if rng.random() < 0.5 else [allv[j], allv[i]])
    rng.shuffle(edges)
    if not edges:
        edges = [[allv[0], allv[1]]]
    return {"name": name, "source": "differential.py collision map (bug B7a)", "rooms": core_rooms + tail_rooms,
            "beams": beams, "occupancy": occ, "filler_occupancy": 65535,
            "vertex_order": core + ["..."] + tail, "edges": edges}


def unit_collision(rng, uid):
    m = collision_map(rng, uid + "_map")
    sensors = list(m["beams"]) + list(m["occupancy"])
    story = [rng.choice(m["rooms"]) for _ in range(rng.randint(1, 6))]
    hist = []
    for _ in range(rng.randint(0, 4) if sensors else 0):
        sn = rng.choice(sensors)
        hist.append([sn, "A" if sn in m["beams"] or rng.random() < 0.6 else "D"])
    ops = ["validateAgentStory", "getAgentStoryStatuses", "getAgentStory", "validateAgentStoryMulti"]
    return [G.case("%s__%s" % (uid, op), op, "differential.py collision (B7a)", map=m["name"], story=story,
                   history=hist) for op in ops], [m]


UNIT_KINDS = [
    ("star_single", 18, unit_star_single),
    ("star_multi", 12, unit_star_multi),
    ("small_map", 8, unit_small_map),
    ("big_map", 14, unit_big_map),
    ("subgraph", 14, unit_subgraph),
    ("update_starting_vertex", 3, unit_update_starting_vertex),
    ("game_dump", 5, unit_game_dump),
    ("applet_run", 16, unit_applet_run),
    ("applet_session", 10, unit_applet_session),
    ("collision", 1, unit_collision),
]


def gen_unit(seed, i):
    """Cases and maps of unit i (reproducible from (seed, i) alone)."""
    rng = random.Random(seed * UNIT_STRIDE + i)
    total = sum(w for _, w, _ in UNIT_KINDS)
    r = rng.uniform(0, total)
    for kind, w, fn in UNIT_KINDS:
        if r < w:
            break
        r -= w
    uid = "d%07d" % i
    cases, maps = fn(rng, uid)
    for c in cases:
        c["unit"] = i
        c["unit_kind"] = kind
    return cases, maps


def generate(n, seed, only=None):
    """At least n cases (whole units) plus their maps."""
    cases, maps = [], [STAR]
    units = only if only is not None else iter(range(UNIT_STRIDE))
    for i in units:
        if only is None and len(cases) >= n:
            break
        cs, ms = gen_unit(seed, i)
        cases += cs
        maps += ms
    return {"maps": maps, "cases": cases}


# ============================================================================ running


class SetupError(RuntimeError):
    pass


# The canonical flags (CD_JVM_FLAGS in env.sh, see README.md), which include
# -XX:-OmitStackTraceInFastThrow. Without that flag, once the JIT has compiled a method that
# keeps throwing an implicit NullPointerException at the same site (thousands of null-room
# cases in one JVM), HotSpot throws a preallocated NPE with an EMPTY stack trace, so the
# recorded top_frame/origin_frame/trace would depend on how many similar cases ran before in
# that JVM. The flag changes nothing else (identity hashes are still all 1).
DIFF_JVM_FLAGS = ("-Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions -XX:hashCode=2 "
                  "-XX:-OmitStackTraceInFastThrow")


def harness_env():
    env = dict(os.environ, LC_ALL="C")
    env.setdefault("CD_HARNESS_JVM_FLAGS", DIFF_JVM_FLAGS)
    return env


def java_meta():
    """Build if needed (run.sh --meta) and check the canonical identity-hash setting."""
    p = subprocess.run(["bash", RUN_SH, "--meta"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=harness_env(), universal_newlines=True)
    if p.returncode != 0:
        raise SetupError("run.sh --meta failed (%d):\n%s" % (p.returncode, p.stderr[-3000:]))
    meta = json.loads(p.stdout)
    if "-XX:-OmitStackTraceInFastThrow" not in (meta.get("jvm_input_arguments") or []):
        print("[differential] warning: CD_HARNESS_JVM_FLAGS lacks -XX:-OmitStackTraceInFastThrow; "
              "repeated NPEs may be recorded without a stack trace", file=sys.stderr)
    if meta.get("identity_hash_of_two_objects") != "1,1":
        raise SetupError("identity hashes are not all 1 (%s): run with the canonical flags "
                         "(-XX:hashCode=2); check CD_HARNESS_JVM_FLAGS" % meta.get("identity_hash_of_two_objects"))
    return meta


def run_java(data, jobs, workdir):
    """Run every case through the live harness, split into `jobs` JVMs (results do not
    depend on what else ran in the JVM under -XX:hashCode=2). Returns results in order."""
    cases = data["cases"]
    jobs = max(1, min(jobs, len(cases) or 1))
    size = (len(cases) + jobs - 1) // jobs
    chunks = [cases[k:k + size] for k in range(0, len(cases), size)]
    env = harness_env()
    env["CD_SKIP_BUILD"] = "1"

    def one(k):
        path = os.path.join(workdir, "java_chunk%02d.cases.json" % k)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"maps": data["maps"], "cases": chunks[k]}, f, ensure_ascii=True)
        p = subprocess.run(["bash", RUN_SH, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        if p.returncode != 0:
            raise SetupError("run.sh failed on %s (%d):\n%s" % (path, p.returncode,
                                                                p.stderr.decode("utf-8", "replace")[-3000:]))
        res = json.loads(p.stdout.decode("utf-8"))
        if len(res) != len(chunks[k]):
            raise SetupError("harness returned %d results for %d cases" % (len(res), len(chunks[k])))
        return res

    with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        parts = list(ex.map(one, range(len(chunks))))
    return [r for part in parts for r in part]


def run_python(data, coverage=None):
    """Run every case through the port. If `coverage` is a dict, count the cases in which
    the HashMap emulation built a tree bin (``treeified``) or removed a tree node
    (``tree_removal``) -- the Java 8 code paths that only sets of 11+ members reach."""
    maps = {m["name"]: m for m in data["maps"]}
    hit = {"treeified": False, "tree_removal": False}
    saved = (javahash._make_tree_bin, javahash._remove_tree_node)

    def make_tree_bin(*a, **k):
        hit["treeified"] = True
        return saved[0](*a, **k)

    def remove_tree_node(*a, **k):
        hit["tree_removal"] = True
        return saved[1](*a, **k)
    if coverage is not None:  # count only; the wrapped functions are called unchanged
        javahash._make_tree_bin, javahash._remove_tree_node = make_tree_bin, remove_tree_node
    out = []
    try:
        for c in data["cases"]:
            hit["treeified"] = hit["tree_removal"] = False
            try:
                out.append(run_harness_case(c, maps))
            except HarnessError as e:
                out.append({"id": c.get("id"), "op": c.get("op"), "harness_error": str(e)})
            except Exception:  # a crash of the port itself (not an emulated Java exception)
                out.append({"id": c.get("id"), "op": c.get("op"), "python_crash": traceback.format_exc()})
            if coverage is not None:
                for k, v in hit.items():
                    coverage[k] = coverage.get(k, 0) + int(v)
    finally:
        javahash._make_tree_bin, javahash._remove_tree_node = saved
    return out


# ============================================================================ comparison


def strip_messages(x):
    if isinstance(x, dict):
        if EXC_KEYS <= set(x):
            return {k: v for k, v in x.items() if k != "message"}
        return {k: strip_messages(v) for k, v in x.items()}
    if isinstance(x, list):
        return [strip_messages(v) for v in x]
    return x


def first_diff(a, b, path=""):
    """Path of the first difference between two JSON values, or None."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in a or k not in b:
                return "%s.%s (only in %s)" % (path, k, "java" if k in a else "python")
            d = first_diff(a[k], b[k], "%s.%s" % (path, k))
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, "%s[%d]" % (path, i))
            if d:
                return d
        if len(a) != len(b):
            return "%s (length %d vs %d)" % (path, len(a), len(b))
        return None
    if type(a) is not type(b) or a != b:
        if isinstance(a, str) and isinstance(b, str):
            i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            return "%s (strings differ at char %d)" % (path, i)
        return path
    return None


def compare_result(c, jr, pr, jdk8=True):
    """List of (field, diff path, java value, python value)."""
    if "harness_error" in jr or "harness_error" in pr or "python_crash" in pr:
        return [("harness", "", jr.get("harness_error"), pr.get("harness_error") or pr.get("python_crash"))]
    a, b = dict(jr), dict(pr)
    a.pop("stderr", None)  # the original never writes to System.err; a non-empty stderr is reported below
    if "stderr" in jr:
        return [("stderr", ".stderr", jr["stderr"], None)]
    if not jdk8:
        a, b = strip_messages(a), strip_messages(b)
    out = []
    for k in list(a) + [k for k in b if k not in a]:
        va, vb = a.get(k, "<absent>"), b.get(k, "<absent>")
        d = first_diff(va, vb, k)
        if d:
            out.append((k, d, va, vb))
    return out


def _input_key(c):
    return json.dumps([c.get("map"), c.get("story"), c.get("history"), c.get("start") or "story"])


def check_validate_compat(data, java, jdk8=True):
    """Replay applet-start validate cases through validate_compat and compare with the
    harness's validate result (+ the getAgentStory companion on the same input)."""
    maps = {m["name"]: m for m in data["maps"]}
    by_input = {}
    for c, jr in zip(data["cases"], java):
        if c["op"] == "getAgentStory" and "map" in c:
            by_input[_input_key(c)] = jr
    bad = []
    n = 0
    for c, jr in zip(data["cases"], java):
        if c["op"] not in ("validateAgentStory", "validateAgentStoryMulti") or "map" not in c:
            continue
        if (c.get("start") or "story") != "story" or "harness_error" in jr:
            continue
        agents = "multi" if c["op"] == "validateAgentStoryMulti" else "single"
        comp = by_input.get(_input_key(c)) if agents == "single" else None
        # expected (verdict, path, exception, stdout) of the applet pipeline per the Java runs
        if jr["exception"] is not None or jr["return"] is False or agents == "multi":
            exp = (jr["return"] if jr["exception"] is None else None, None, jr["exception"], jr["stdout"])
        elif comp is None:
            continue  # verdict true, but no recorded getAgentStory on this input
        else:
            exp = (True if comp["exception"] is None else None, comp["return"], comp["exception"],
                   jr["stdout"] + comp["stdout"])
        n += 1
        got_exc = None
        ret = (None, None)
        with capture_stdout() as buf:
            try:
                ret = validate_compat(maps[c["map"]], c.get("story") or [], c.get("history") or [], agents)
            except JavaException as e:
                got_exc = e.describe()
            except Exception:
                got_exc = {"python_crash": traceback.format_exc()}
        got = (ret[0] if got_exc is None else None, ret[1] if got_exc is None else None, got_exc, buf.getvalue())
        if not jdk8:
            exp, got = strip_messages(list(exp)), strip_messages(list(got))
        names = ["verdict", "path", "exception", "stdout"]
        for k in range(4):
            if exp[k] != got[k]:
                bad.append((c, names[k], exp[k], got[k]))
                break
    return n, bad


# ============================================================================ minimisation


def _shrinks(c, maps):
    """One-step reductions of a case: (case', extra maps)."""
    out = []

    def with_(key, val):
        d = copy.deepcopy(c)
        d[key] = val
        out.append((d, []))
    for key in ("history", "story", "goals", "vp_set", "story_vertices", "occupancy_active"):
        v = c.get(key)
        if isinstance(v, list):
            for i in range(len(v)):
                with_(key, v[:i] + v[i + 1:])
    if c.get("start") == "fixed":
        with_("start", "story")
    steps = c.get("steps")
    if isinstance(steps, list):
        for i in range(len(steps)):
            if len(steps) > 1:
                with_("steps", steps[:i] + steps[i + 1:])
            run = steps[i].get("run")
            if isinstance(run, dict):
                for fld in ("story", "sensors"):
                    t = run.get(fld)
                    if not isinstance(t, str) or not t:
                        continue
                    parts = list(t) if fld == "story" else t.split(",")
                    for j in range(len(parts)):
                        st = copy.deepcopy(steps)
                        rest = parts[:j] + parts[j + 1:]
                        st[i]["run"][fld] = "".join(rest) if fld == "story" else ",".join(rest)
                        with_("steps", st)
    mo = c.get("map")
    spec = maps.get(mo) if isinstance(mo, str) else mo
    if isinstance(spec, dict) and spec.get("name") != "star_fig2":
        for i in range(len(spec.get("edges") or [])):
            m2 = copy.deepcopy(spec)
            del m2["edges"][i]
            d = copy.deepcopy(c)
            d["map"] = m2
            out.append((d, []))
        if spec.get("vertex_order") is not None:
            m2 = copy.deepcopy(spec)
            del m2["vertex_order"]
            d = copy.deepcopy(c)
            d["map"] = m2
            out.append((d, []))
    return out


def _inline(c, maps):
    d = copy.deepcopy(c)
    if isinstance(d.get("map"), str) and d["map"] != "star_fig2":
        d["map"] = copy.deepcopy(maps[d["map"]])
    return d


def minimise(c, maps, field, workdir, jdk8=True, max_rounds=300):
    """Greedy delta-debugging: keep the first one-step reduction whose Java/Python results
    still differ in `field`. Returns (case, java result, python result)."""
    cur = _inline(c, maps)
    base_maps = [STAR]
    for _ in range(max_rounds):
        cands = [d for d, _ in _shrinks(cur, {})]
        if not cands:
            break
        for k, d in enumerate(cands):
            d["id"] = "shrink%04d" % k
        data = {"maps": base_maps, "cases": cands}
        try:
            jres = run_java(data, 1, workdir)
        except SetupError:
            break
        pres = run_python(data)
        nxt = None
        for d, jr, pr in zip(cands, jres, pres):
            if "harness_error" in jr:
                continue
            if any(f == field for f, _, _, _ in compare_result(d, jr, pr, jdk8)):
                nxt = d
                break
        if nxt is None:
            break
        cur = nxt
    cur["id"] = "repro"
    data = {"maps": base_maps, "cases": [cur]}
    jr = run_java(data, 1, workdir)[0]
    return cur, jr, run_python(data)[0]


# ============================================================================ main


def _short(v, n=700):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "...(%d chars)" % len(s)


def run_differential(n=5000, seed=DEFAULT_SEED, jobs=4, out=None, only=None, cases_file=None,
                     max_repros=5, log=sys.stderr):
    """Run the differential test; returns a summary dict (``mismatches`` lists the
    unminimised mismatches, ``repros`` the minimised ones)."""
    workdir = out or tempfile.mkdtemp(prefix="cd-differential-")
    os.makedirs(workdir, exist_ok=True)
    meta = java_meta()
    jdk8 = str(meta.get("java.version", "")).startswith("1.8")
    if cases_file:
        with open(cases_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"maps": [STAR], "cases": data}
        data.setdefault("maps", [STAR])
    else:
        data = generate(n, seed, only)
    with open(os.path.join(workdir, "cases.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True)
    print("[differential] %d cases, %d maps (seed %s); java %s" % (
        len(data["cases"]), len(data["maps"]), seed, meta.get("java.version")), file=log)
    java = run_java(data, jobs, workdir)
    coverage = {}
    python = run_python(data, coverage)
    with open(os.path.join(workdir, "java.json"), "w", encoding="utf-8") as f:
        json.dump(java, f, ensure_ascii=True)

    mismatches, harness_errors = [], []
    fields_compared = 0
    by_op = {}
    for c, jr, pr in zip(data["cases"], java, python):
        if jr.get("id") != c.get("id"):
            raise SetupError("result order mismatch at %s" % c.get("id"))
        diffs = compare_result(c, jr, pr, jdk8)
        fields_compared += len(set(jr) | set(pr))
        by_op[c["op"]] = by_op.get(c["op"], 0) + 1
        for f, path, va, vb in diffs:
            (harness_errors if f == "harness" else mismatches).append(
                {"kind": "harness", "case": c, "field": f, "where": path, "java": va, "python": vb})
    n_vc, vc_bad = check_validate_compat(data, java, jdk8)
    for c, f, va, vb in vc_bad:
        mismatches.append({"kind": "validate_compat", "case": c, "field": f, "where": f, "java": va, "python": vb})

    maps = {m["name"]: m for m in data["maps"]}
    repros = []
    seen = set()
    for mm in mismatches:
        sig = (mm["kind"], mm["case"]["op"], mm["field"])
        if sig in seen or len(repros) >= max_repros:
            continue
        seen.add(sig)
        if mm["kind"] == "harness":
            cur, jr, pr = minimise(mm["case"], maps, mm["field"], workdir, jdk8)
            d = [x for x in compare_result(cur, jr, pr, jdk8) if x[0] == mm["field"]]
            rep = {"signature": list(sig), "case": cur, "where": d[0][1] if d else mm["where"],
                   "java": d[0][2] if d else mm["java"], "python": d[0][3] if d else mm["python"]}
        else:
            rep = {"signature": list(sig), "case": _inline(mm["case"], maps), "where": mm["where"],
                   "java": mm["java"], "python": mm["python"]}
        path = os.path.join(workdir, "repro_%02d.json" % len(repros))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"maps": [STAR], "cases": [rep["case"]]}, f, ensure_ascii=True, indent=1)
        rep["file"] = path
        repros.append(rep)

    summary = {"cases": len(data["cases"]), "maps": len(data["maps"]), "by_op": by_op,
               "fields_compared": fields_compared, "validate_compat_checked": n_vc,
               "cases_with_tree_bins": coverage.get("treeified", 0),
               "cases_with_tree_removal": coverage.get("tree_removal", 0),
               "mismatches": mismatches, "harness_errors": harness_errors, "repros": repros,
               "workdir": workdir, "java": meta.get("java.version")}
    return summary


def report(summary, log=sys.stdout):
    print("cases: %d (%s)" % (summary["cases"], ", ".join(
        "%s %d" % kv for kv in sorted(summary["by_op"].items()))), file=log)
    print("fields compared: %d; validate_compat replays: %d; cases with tree bins: %d (tree removals: %d)" % (
        summary["fields_compared"], summary["validate_compat_checked"], summary["cases_with_tree_bins"],
        summary["cases_with_tree_removal"]), file=log)
    print("mismatching fields: %d; harness errors: %d; work dir: %s" % (
        len(summary["mismatches"]), len(summary["harness_errors"]), summary["workdir"]), file=log)
    for h in summary["harness_errors"][:10]:
        print("HARNESS ERROR %s: java=%s python=%s" % (h["case"]["id"], _short(h["java"]), _short(h["python"])),
              file=log)
    for r in summary["repros"]:
        print("\n=== mismatch %s (minimal repro: %s)" % ("/".join(r["signature"]), r["file"]), file=log)
        print("case:   %s" % _short(r["case"], 3000), file=log)
        print("where:  %s" % r["where"], file=log)
        print("java:   %s" % _short(r["java"]), file=log)
        print("python: %s" % _short(r["python"]), file=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=5000, help="minimum number of cases (default 5000)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed (default %d)" % DEFAULT_SEED)
    ap.add_argument("--jobs", type=int, default=4, help="parallel JVMs (default 4)")
    ap.add_argument("--out", help="work directory (cases.json, java.json, repro_*.json); default: a temp dir")
    ap.add_argument("--only", type=int, nargs="+", metavar="UNIT", help="generate only these unit indices")
    ap.add_argument("--cases", help="replay this harness case file instead of generating cases")
    ap.add_argument("--max-repros", type=int, default=5, help="distinct mismatch kinds to minimise (default 5)")
    a = ap.parse_args(argv)
    try:
        s = run_differential(a.n, a.seed, a.jobs, a.out, a.only, a.cases, a.max_repros)
    except SetupError as e:
        print("[differential] setup error: %s" % e, file=sys.stderr)
        return 2
    report(s)
    if s["harness_errors"]:
        return 2
    return 1 if s["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
