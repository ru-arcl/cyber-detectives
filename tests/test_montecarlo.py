"""Geometric Monte Carlo oracle: random agents walking the drawings of the builtin maps.

Every simulated ``(story, history)`` is consistent by construction (the agent really walked
it, see ``cyber_detectives.geometry.simulate_walk``), so ``validate`` must accept it, in
single- and multi-agent mode, with a witness that replays.  Before trusting a simulated
walk we check it independently of the engine: its region-model walk must explain its
history (``tests/oracles/brute.check_region_walk``, on the regions *derived from the
drawing*), and replaying its trajectory geometrically must give back the same story and
history.

Negative side: random local perturbations of short simulated walks (swap / drop / insert a
recording, change / drop a story room; in multi-agent mode mostly edits that keep the history
well formed but shrink or drop occupancy intervals, drop beam recordings or reorder
recordings) are judged by the brute-force oracle (``tests/oracles/brute.consistent``);
whenever it proves the perturbed pair impossible the engine must reject it, and whenever the
oracle finds it consistent the engine must accept.  Multi-agent sweeps run until at least
``10 * CD_TEST_SCALE`` well-formed perturbations per map are proved impossible.

Sizes scale with ``CD_TEST_SCALE`` (default 1).
"""

from __future__ import annotations

import random

import pytest

from conftest import sweep_scale
from oracles import brute as B

from cyber_detectives import builtin_map, builtin_map_names, replay, validate
from cyber_detectives import geometry as G

SCALE = sweep_scale()
N_SINGLE = 25 * SCALE
N_MULTI = 20 * SCALE
N_NEG = 40 * SCALE


def _maps():
    out = []
    for n in builtin_map_names():
        if n in G.builtin_geometry_names() and builtin_map(n).rooms:
            out.append(n)
    return out


MAPS = _maps()


def _replay(m, r, history, agents, story):
    replay(m, r.path, history, agents=agents, story=story)


def _setup(name):
    m = builtin_map(name)
    g = G.as_geometry(m)
    # the oracle works on the regions computed from the drawing, not on the map file
    rm = B.RegionMap(m.rooms, m.beams, m.occupancy, g.derive_regions(), name)
    return m, g, rm


def _seed(name, kind, i):
    return "%s/%s/%d" % (name, kind, i)


def test_maps_with_geometry_are_covered():
    assert "star_fig2" in MAPS and "icra_fig2" in MAPS


@pytest.mark.parametrize("name", MAPS)
def test_single_agent_walks_are_consistent(name):
    m, g, rm = _setup(name)
    lengths = []
    for i in range(N_SINGLE):
        rng = random.Random(_seed(name, "single", i))
        w = G.simulate_walk(g, rng, steps=rng.randint(4, 40))
        lengths.append(len(w.history))
        # the ground truth itself, checked without the engine
        assert w.walk[0] == w.story[0] and w.walk[-1] == w.story[-1]
        assert g.location(*w.points[-1]) == ("room", w.story[-1])
        tr = G.trace_polyline(g, w.points)
        assert (tr.story, tr.history) == (w.story, w.history)
        ok, why = B.check_region_walk(rm, w.story, w.history, w.walk)
        assert ok, (w, w.walk, why)
        # the engine
        for agents in ("single", "multi"):
            r = validate(m, w.story, w.history, agents=agents)
            assert r.consistent, (agents, w, r.reason)
            _replay(m, r, w.history, agents, w.story)
            pts = G.path_polyline(m, r)
            tw = G.trace_polyline(g, pts)
            assert tw.story == w.story, (agents, w, r.path_string())
            if agents == "single":
                # the drawn witness walks into exactly the recorded events
                assert tw.history == w.history, (w, r.path_string())
    assert max(lengths) >= 5, "walks too short to exercise anything"


@pytest.mark.parametrize("name", MAPS)
def test_multi_agent_walks_are_consistent(name):
    m, g, rm = _setup(name)
    for i in range(N_MULTI):
        rng = random.Random(_seed(name, "multi", i))
        k = 1 + i % 4
        mw = G.simulate_multi(g, rng, agents=k, steps=rng.randint(4, 30))
        x = mw.agents[0]
        assert x.story == mw.story
        ok, why = B.check_region_walk(rm, mw.story, mw.history, x.walk, agents="multi")
        assert ok, (mw, x.walk, why)
        r = validate(m, mw.story, mw.history, agents="multi")
        assert r.consistent, (mw, r.reason)
        _replay(m, r, mw.history, "multi", mw.story)
        assert G.trace_polyline(g, G.path_polyline(m, r)).story == mw.story
        if k == 1:
            # one agent: the merged history is x's own, also consistent in single mode
            assert mw.history == x.history
            assert validate(m, mw.story, mw.history).consistent


def _matching_a(hist, i):
    """Index of the activation that the occupancy deactivation ``hist[i]`` closes."""
    j = i - 1
    while hist[j][0] != hist[i][0]:
        j -= 1
    return j


def _perturb_multi(rng, rm, story, hist):
    """A local change aimed at what the multi-agent rules constrain, keeping the history
    well formed: shrink an occupancy interval (move its D earlier or its A later), drop an
    occupancy interval or a beam recording, swap adjacent recordings of different sensors
    (when that keeps alternation), or edit the story.  Falls back to ``brute.perturb``."""
    story, hist = list(story), [list(e) for e in hist]
    occ_d = [i for i, (s, k) in enumerate(hist) if s in rm.occupancy and k == "D"]
    beams = [i for i, (s, _k) in enumerate(hist) if s in rm.beams]
    choice = rng.randrange(6)
    if choice in (0, 1) and occ_d:
        i = rng.choice(occ_d)
        j = _matching_a(hist, i)
        if i - j >= 2:
            # choice 0: D earlier, still after its A; choice 1: A later, still before its D
            e = hist.pop(i if choice == 0 else j)
            hist.insert(rng.randint(j + 1, i - 1), e)
            return story, hist
    elif choice == 2 and occ_d:
        i = rng.choice(occ_d)
        j = _matching_a(hist, i)
        del hist[i], hist[j]
        return story, hist
    elif choice == 3 and beams:
        hist.pop(rng.choice(beams))
        return story, hist
    elif choice == 4 and len(hist) >= 2:
        idx = [i for i in range(len(hist) - 1) if hist[i][0] != hist[i + 1][0]]
        if idx:
            i = rng.choice(idx)
            hist[i], hist[i + 1] = hist[i + 1], hist[i]
            return story, hist
    elif choice == 5:
        k = rng.randrange(3)
        if k == 0:
            story[rng.randrange(len(story))] = rng.choice(rm.rooms)
        elif k == 1 and len(story) >= 2:
            story.pop(rng.randrange(len(story)))
        else:
            story.insert(rng.randint(0, len(story)), rng.choice(rm.rooms))
        return story, hist
    return B.perturb(rng, rm, story, hist)


def _perturbed_cases(name, agents, limit):
    """Yield (story, history, oracle verdict) for perturbed short simulated walks."""
    m, g, rm = _setup(name)
    for i in range(limit):
        rng = random.Random(_seed(name, "neg-" + agents, i))
        if agents == "single":
            w = G.simulate_walk(g, rng, steps=rng.randint(3, 12))
            story, hist = w.story, w.history
        else:
            mw = G.simulate_multi(g, rng, agents=1 + i % 3, steps=rng.randint(3, 10))
            story, hist = mw.story, mw.history
        if len(hist) > 14:
            continue
        if agents == "multi" and i % 4:
            p_story, p_hist = _perturb_multi(rng, rm, story, hist)
        else:
            p_story, p_hist = B.perturb(rng, rm, story, hist)
        if (p_story, p_hist) == (story, hist):
            continue
        try:
            truth = B.consistent(rm, p_story, p_hist, agents=agents)
        except B.OracleError:  # pragma: no cover - outside the oracle's budget
            continue
        yield p_story, p_hist, truth


N_MULTI_REJECT = 10 * SCALE  # well-formed multi-agent perturbations the oracle must disprove


@pytest.mark.parametrize("agents", ["single", "multi"])
@pytest.mark.parametrize("name", MAPS)
def test_perturbed_walks_match_brute_force(name, agents):
    m, _g, rm = _setup(name)
    rejected = rejected_wf = total = 0
    limit = N_NEG if agents == "single" else 40 * N_NEG
    for story, hist, truth in _perturbed_cases(name, agents, limit):
        r = validate(m, story, hist, agents=agents)
        assert r.consistent == truth, (agents, story, hist, truth, r.reason)
        total += 1
        rejected += not truth
        rejected_wf += not truth and B.malformed(rm, hist) is None
        if agents == "multi" and total >= N_NEG and rejected_wf >= N_MULTI_REJECT:
            break
    assert total >= N_NEG // 2
    if agents == "single":
        assert rejected >= total // 5, "too few perturbations proved impossible to mean much"
    else:
        # multi-agent mode forgives much more (others explain extra recordings): keep
        # perturbing until enough well-formed cases are proved impossible
        assert rejected_wf >= N_MULTI_REJECT, (rejected_wf, total)


@pytest.mark.parametrize("name", MAPS)
def test_swapped_beam_recordings(name):
    """Swap two adjacent recordings of different beams in a simulated single-agent walk;
    when brute force proves the result impossible, the engine must reject it."""
    m, g, rm = _setup(name)
    proved = 0
    for i in range(2 * N_NEG):
        rng = random.Random(_seed(name, "swap", i))
        w = G.simulate_walk(g, rng, steps=rng.randint(4, 14))
        if len(w.history) > 14:
            continue
        idx = [j for j in range(len(w.history) - 1)
               if w.history[j][0] in m.beams and w.history[j + 1][0] in m.beams
               and w.history[j][0] != w.history[j + 1][0]]
        if not idx:
            continue
        j = rng.choice(idx)
        hist = [list(e) for e in w.history]
        hist[j], hist[j + 1] = hist[j + 1], hist[j]
        if B.consistent(rm, w.story, hist):
            continue
        proved += 1
        r = validate(m, w.story, hist)
        assert not r.consistent, (w, hist)
        assert not validate(m, w.story, hist, agents="single").consistent
    assert proved >= 1
