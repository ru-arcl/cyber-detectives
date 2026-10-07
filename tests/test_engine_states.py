"""``possible_positions``: where agent x can be in each time slot (forward-backward states).

- Unit cases on STAR Fig. 2.
- Randomized check against an explicit time-expanded graph built here from the fixture dict
  (``tests/oracles/brute.py``'s ``RegionMap``, not the engine's map or search): nodes
  ``(slot, place, k)``, every move rule written out again, plain forward and backward
  breadth-first search over the whole graph, intersection projected on places.
- Consistency with the other oracles: the lists are empty exactly when ``brute.consistent``
  says the story is inconsistent; every place of ``validate``'s witness is listed at its
  slot; the story-free filter contains every story's positions.
- Also here: automatic region names never clash with feature names (``maps.py`` fix).

Randomized sweeps scale with ``CD_TEST_SCALE`` (see conftest.py).
"""

from __future__ import annotations

import random
from collections import deque

import pytest

from conftest import sweep_scale
from cyber_detectives import (InputError, Map, MapError, builtin_map, possible_positions,
                              validate)
from oracles import brute as B

STAR = builtin_map("star_fig2")


# ---------------------------------------------------------------------- unit cases


def test_star_story_positions():
    pp = possible_positions(STAR, "b1 o1 o1 o2 o2 b2", "ACBAC")
    assert pp == [["A", "C", "R1", "R2"], ["C", "R1", "R2"], ["o1"], ["R3"], ["o2"],
                  ["B", "R4"], ["A", "C", "R1", "R2"]]
    # inconsistent story: no slot has a position
    assert possible_positions(STAR, "b1 o1 o1 b2 o2 o2", "ACBAC") == [[]] * 7


def test_star_filter_without_story():
    pp = possible_positions(STAR, "b1 o1 o1 o2 o2 b2")
    assert pp[2] == ["o1"] and pp[4] == ["o2"] and pp[5] == ["B", "R4"]
    assert pp[0] == ["A", "C", "R1", "R2"]  # o1 is entered from R1/R2 after the b1 crossing
    # no recordings: x starts inside a room (R3 touches no room) ...
    assert possible_positions(STAR, "") == [["A", "B", "C", "R1", "R2", "R4"]]
    assert possible_positions(STAR, "", starts="rooms") == [["A", "B", "C", "R1", "R2", "R4"]]
    # ... or, starts="anywhere", anywhere but inside an occupancy region
    assert possible_positions(STAR, "", starts="anywhere") == \
        [["A", "B", "C", "R1", "R2", "R3", "R4"]]
    assert possible_positions(STAR, "", "B") == [["B"]]  # leaving B means re-entering it
    assert possible_positions(STAR, "", "BB") == [["B", "R4"]]


def test_filter_start_rules():
    # R3 lies between o1 and o2 and touches no room: x can be there before the first
    # recording only if it may start outside a room
    assert possible_positions(STAR, "o2 o2", starts="anywhere") == \
        [["B", "R3", "R4"], ["o2"], ["B", "R3", "R4"]]
    assert possible_positions(STAR, "o2 o2") == [["B", "R4"], ["o2"], ["B", "R3", "R4"]]
    assert possible_positions(STAR, "o1 o1 o2 o2")[0] == ["A", "C", "R1", "R2"]
    assert possible_positions(STAR, "o1 o1 o2 o2", starts="anywhere")[0] == \
        ["A", "C", "R1", "R2", "R3"]
    # "rooms" is always within "anywhere"
    for hist in ("", "b1", "b2 o1 o1", "o1 o1 o2 o2", "b1 b1 b2", "o2 b1 o1 o2"):
        for agents in ("single", "multi"):
            a = possible_positions(STAR, hist, agents=agents, starts="anywhere")
            r = possible_positions(STAR, hist, agents=agents)
            assert all(set(x) <= set(y) for x, y in zip(r, a)), (hist, agents)
    with pytest.raises(ValueError, match="starts must be 'rooms' or 'anywhere', got 'room'"):
        possible_positions(STAR, "", starts="room")
    with pytest.raises(ValueError, match="needs story=None"):
        possible_positions(STAR, "", "A", starts="anywhere")
    assert possible_positions(STAR, "b1", "AC", starts="rooms") == \
        possible_positions(STAR, "b1", "AC")
    with pytest.raises(TypeError):
        possible_positions(STAR, "", None, "single", False, "rooms")  # keyword-only


def test_multi_agent_positions():
    pp = possible_positions(STAR, "b1 o1 o2 b2 o2 o1", "ACBAC", agents="multi")
    assert len(pp) == 7
    assert "o1" in pp[2] and "B" in pp[4] and pp[6] == ["A", "C", "R1", "R2"]
    # x may still be inside an active occupancy region after the last recording
    assert "o1" in possible_positions(STAR, "o1", agents="multi")[1]


def test_malformed_and_bad_input():
    assert possible_positions(STAR, "o1+ o1+", agents="multi") == [[], [], []]
    assert possible_positions(STAR, [["o1", "A"], ["b1", "A"], ["o1", "D"]]) == [[]] * 4
    assert possible_positions(STAR, [["b1", "D"]], "A") == [[], []]
    with pytest.raises(InputError):
        possible_positions(STAR, "b1", "")
    with pytest.raises(InputError):
        possible_positions(STAR, "zz")
    with pytest.raises(ValueError):
        possible_positions(STAR, "b1", agents="many")


def test_unreported_visits_widen_positions():
    strict = possible_positions(STAR, "", "AC")
    loose = possible_positions(STAR, "", "AC", unreported_visits=True)
    assert set(strict[0]) <= set(loose[0])
    assert possible_positions(STAR, "", "AB") == [[]]
    assert possible_positions(STAR, "b1 b2", "AB", unreported_visits=True) != [[], [], []]


# ---------------------------------------------------------------------- region names


def test_auto_region_names_skip_taken_names():
    m = Map("x", ["R1", "A"], {"b": ["R2", "s"]}, ["R4"], [["R1", "A", "R2"], ["s", "R4"]])
    assert list(m.regions) == ["R3", "R5"]
    assert m.side_region("R2") == "R3"
    d = m.to_dict()
    assert Map.from_dict(d) == m
    # from_edges goes through the same naming
    g = Map.from_edges("y", ["R1", "R2"], {}, [], [["R1", "R2"]])
    assert list(g.regions) == ["R3"]
    # explicitly named regions still clash
    with pytest.raises(MapError, match="used twice"):
        Map("z", ["R1"], {}, [], {"R1": ["R1"]})


# ---------------------------------------------------------------------- explicit oracle


def oracle_positions(rm, story, hist, multi, unreported, starts="rooms"):
    """Positions per slot from an explicit time-expanded graph (see module docstring)."""
    m = len(hist)
    if B.malformed(rm, hist) is not None:
        return [set() for _ in range(m + 1)]
    active = [frozenset()]
    for s, kd in hist:
        cur = set(active[-1])
        if s in rm.occupancy:
            (cur.add if kd == "A" else cur.discard)(s)
        active.append(frozenset(cur))
    n = len(story) if story is not None else 0

    def free(h, node):
        kind, x = node[0]
        k = node[1]
        if kind == B.ROOM:
            for i in rm.touch[x]:
                yield (B.REG, i), k
        elif kind == B.REG:
            for r in rm.reg_rooms[x]:
                if story is None:
                    yield (B.ROOM, r), k
                else:
                    if k < n and story[k] == r:
                        yield (B.ROOM, r), k + 1
                    if unreported:
                        yield (B.ROOM, r), k
            if multi:
                for o in rm.reg_occ[x]:
                    if o in active[h]:
                        yield (B.OCC, o), k
        elif multi and x in active[h]:
            for i in rm.touch[x]:
                yield (B.REG, i), k

    def event(h, node):
        (kind, x), k = node
        s, kd = hist[h]
        if s in rm.beams:
            if multi:
                yield node
            if kind == B.REG:
                for sd in rm.beams[s]:
                    if rm.side_region[sd] == x:
                        yield (B.REG, rm.side_region[rm.other_side[sd]]), k
        elif multi:
            if not (kd == "D" and node[0] == (B.OCC, s)):
                yield node
        elif kd == "A":
            if kind == B.REG and s in rm.reg_occ[x]:
                yield (B.OCC, s), k
        elif node[0] == (B.OCC, s):
            for i in rm.touch[s]:
                yield (B.REG, i), k

    # all edges of the time-expanded graph
    nodes = [(h, p, k) for h in range(m + 1) for p in rm.positions() for k in range(n + 1)]
    succ = {v: [] for v in nodes}
    for h, p, k in nodes:
        for q, k2 in free(h, (p, k)):
            succ[(h, p, k)].append((h, q, k2))
        if h < m:
            for q, k2 in event(h, (p, k)):
                succ[(h, p, k)].append((h + 1, q, k2))
    pred = {v: [] for v in nodes}
    for v, ws in succ.items():
        for w in ws:
            pred[w].append(v)
    if story is None:
        allowed = (B.ROOM,) if starts == "rooms" else (B.ROOM, B.REG)
        starts = [(0, p, 0) for p in rm.positions() if p[0] in allowed]
        goals = [(m, p, 0) for p in rm.positions() if multi or p[0] != B.OCC]
    else:
        starts = [(0, (B.ROOM, story[0]), 1)]
        goals = [(m, (B.ROOM, story[-1]), n)]

    def bfs(seeds, adj):
        seen = set(seeds)
        dq = deque(seeds)
        while dq:
            v = dq.popleft()
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    dq.append(w)
        return seen

    both = bfs(starts, succ) & bfs(goals, pred)
    out = [set() for _ in range(m + 1)]
    for h, p, _k in both:
        out[h].add(rm.pos_name(p))
    return out


def _instances(rng, count):
    made = 0
    while made < count:
        general = rng.random() < 0.4
        d = (B.random_general_region_map(rng, rooms=(1, 4)) if general
             else B.random_region_map(rng, rooms=(2, 4)))
        rm = B.RegionMap.from_dict(d)
        agents = rng.choice(["single", "multi"])
        r = rng.random()
        story = hist = None
        if r < 0.5:
            sim = B.simulate_walk(rm, rng, agents, max_story=4, max_events=5)
            if sim is None:
                continue
            story, hist = sim[0], sim[1]
            if r >= 0.3:
                story, hist = B.perturb(rng, rm, story, hist)
        else:
            story = B.random_story(rng, rm, 1, 4)
            hist = B.random_history(rng, rm, agents, 0, 5, malformed_prob=0.15)
        made += 1
        yield d, rm, story, hist, agents, rng.random() < 0.3


def test_possible_positions_match_explicit_graph():
    rng = random.Random(7100)
    fails = []
    total = 0
    for d, rm, story, hist, agents, unrep in _instances(rng, 150 * sweep_scale()):
        m = Map.from_dict(d)
        multi = agents == "multi"
        for st, starts in ((story, "rooms"), (None, "rooms"), (None, "anywhere")):
            total += 1
            got = possible_positions(m, hist, st, agents, unrep, starts=starts)
            want = oracle_positions(rm, tuple(st) if st is not None else None,
                                    tuple(tuple(e) for e in hist), multi, unrep, starts)
            if [set(x) for x in got] != want or len(got) != len(hist) + 1:
                fails.append("%r story=%r hist=%r %s unrep=%s starts=%s: got %r want %r"
                             % (d, st, hist, agents, unrep, starts, got, want))
                continue
            order = list(m.rooms) + list(m.regions) + list(m.occupancy)
            for slot in got:
                assert slot == sorted(slot, key=order.index)
        # agreement with the brute-force verdict and with validate's witness
        pp = possible_positions(m, hist, story, agents, unrep)
        ok = B.consistent(rm, story, hist, agents, unrep)
        if ok != any(pp):
            fails.append("emptiness %r vs consistent=%s: %r %r %r" % (pp, ok, d, story, hist))
        if ok:
            assert all(pp), (d, story, hist)
            res = validate(m, story, hist, agents, unreported_visits=unrep)
            for s in res.path:
                assert s.position in pp[s.time], (d, story, hist, s)
            free = possible_positions(m, hist, None, agents)
            anywhere = possible_positions(m, hist, None, agents, starts="anywhere")
            assert all(set(a) <= set(b) <= set(c) for a, b, c in zip(pp, free, anywhere))
    assert not fails, "%d of %d disagree; first:\n%s" % (len(fails), total, "\n".join(fails[:3]))
