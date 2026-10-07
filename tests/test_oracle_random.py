"""Seeded randomized differential tests: the default engine against the independent oracles.

For random small region maps (2-5 rooms, 0-3 beams, 0-3 occupancy sensors, 1-4 regions plus
sometimes a sensor-only region like STAR's R3), for general region maps that are not the
cliques of G (``test_general_region_maps_*``) and for the paper maps, with stories and
histories that are simulated from a random walk (consistent by construction), perturbed, or
arbitrary:

* Problem 1: ``validate`` verdict == ``brute.consistent`` (single / multi, strict /
  unreported visits); every witness replays in both independent checkers
  (``brute.check_path_string`` on ``path_string()``, ``brute.check_steps`` on ``path``);
  malformed histories are rejected with a reason.
* Problem 2: ``validate_intervals`` == ``brute.consistent_intervals`` for all six cases.
* Problem 3: ``shortest_superstory`` length == brute minimum, for both readings (default
  ``anchored=True``: p'_1 = p_1, p'_last = p_n; ``anchored=False``: p' may start before p_1 /
  end after p_n); its story is a consistent super-sequence whose path replays.
* Problem 4: ``closest_story`` edits == brute minimum == Levenshtein(story', story); story'
  is consistent and its path replays.

Failures print a JSON reproduction (map, story, history, options).  Scale: CD_TEST_SCALE
(default 1; e.g. 20 for a long sweep).
"""

from __future__ import annotations

import collections
import json
import os
import random

import pytest

import cyber_detectives as cd
from oracles import brute as B

SCALE = float(os.environ.get("CD_TEST_SCALE", "1") or 1)
HERE = os.path.dirname(os.path.abspath(__file__))


def _n(base):
    return max(1, int(base * SCALE))


# ----------------------------------------------------------------------------- helpers


def _engine_map(d):
    return cd.Map.from_dict(d)


def _rep(d, story, hist, **opts):
    return json.dumps({"map": d, "story": list(story), "history": hist, **opts})


def _story_str(x):
    if x is None:
        return None
    return x if isinstance(x, str) else "".join(x)


def _path_string(res):
    """The witness in DESIGN.md path notation (Result.path_string(); or path_to_string)."""
    ps = getattr(res, "path_string", None)
    if callable(ps):
        return ps()
    p2s = getattr(cd, "path_to_string", None)
    if callable(p2s) and getattr(res, "path", None) is not None:
        return p2s(res.path)
    return None


def _check_result_witness(problems, rm, story, hist, res, unreported):
    """Replay the witness of a Problem 3/4 result for its story (single agent)."""
    if problems:
        return
    if getattr(res, "path", None) is None:
        problems.append("no witness path")
        return
    ps = _path_string(res)
    if ps is not None:
        ok, why = B.check_path_string(rm, story, hist, ps, "single", unreported)
        if not ok:
            problems.append("witness %r does not replay: %s" % (ps, why))
    ok, why = B.check_steps(rm, story, hist, res.path, "single", unreported)
    if not ok:
        problems.append("witness steps do not replay: %s" % why)


def _finish(fails, total, what):
    if fails:
        head = "\n\n".join(fails[:5])
        pytest.fail("%d of %d %s disagree with the oracle; first ones:\n\n%s"
                    % (len(fails), total, what, head))


def _instances(rng, agents, count, maps=None, max_story=5, max_events=6, rooms=(2, 5)):
    """(map dict, RegionMap, story, history): 40% simulated, 30% perturbed, 30% arbitrary."""
    made = 0
    while made < count:
        if maps:
            d = rng.choice(maps)
        else:
            d = B.random_region_map(rng, rooms=rooms)
        rm = B.RegionMap.from_dict(d)
        r = rng.random()
        if r < 0.7:
            sim = B.simulate_walk(rm, rng, agents, max_story=max_story, max_events=max_events)
            if sim is None:
                continue
            story, hist = sim[0], sim[1]
            if r >= 0.4:
                story, hist = B.perturb(rng, rm, story, hist)
        else:
            story = B.random_story(rng, rm, 1, min(4, max_story))
            hist = B.random_history(rng, rm, agents, 0, max_events)
        made += 1
        yield d, rm, story, hist


def _paper_maps():
    with open(os.path.join(HERE, "fixtures", "paper", "star.json")) as f:
        star = json.load(f)
    with open(os.path.join(HERE, "fixtures", "paper", "icra.json")) as f:
        icra = json.load(f)
    s2 = dict(next(m for m in star["maps"] if m["name"] == "star_fig2"))
    s2["regions"] = s2["expected"]["free_components_fig2"]
    i2 = dict(next(m for m in icra["maps"] if m["name"] == "icra_fig2"))
    i2["regions"] = icra["figures"]["fig3_free_components"]["icra_fig2"]
    out = []
    for m in (s2, i2):
        out.append({k: m[k] for k in ("name", "rooms", "beams", "occupancy", "regions",
                                      "edges")})
    return out


PAPER_MAPS = _paper_maps()

# ----------------------------------------------------------------------------- Problem 1


def _check_validate(fails, stats, d, rm, story, hist, agents, unreported):
    kw = {"agents": agents}
    if unreported:
        kw["unreported_visits"] = True
    rep = _rep(d, story, hist, **kw)
    r = cd.validate(_engine_map(d), story, hist, **kw)
    want = B.consistent(rm, story, hist, agents, unreported)
    stats[want] += 1
    if bool(r.consistent) != want:
        fails.append("validate -> %s, oracle -> %s (reason %r)\n%s"
                     % (r.consistent, want, getattr(r, "reason", None), rep))
        return
    if not want:
        return
    if getattr(r, "path", None) is None:
        fails.append("consistent but no witness path\n%s" % rep)
        return
    ps = _path_string(r)
    ok, why = B.check_path_string(rm, story, hist, ps, agents, unreported)
    if not ok:
        fails.append("witness %r does not replay: %s\n%s" % (ps, why, rep))
    ok, why = B.check_steps(rm, story, hist, r.path, agents, unreported)
    if not ok:
        fails.append("witness steps of %r do not replay: %s\n%s" % (ps, why, rep))


@pytest.mark.parametrize("unreported", [False, True], ids=["strict", "unreported"])
@pytest.mark.parametrize("agents", ["single", "multi"])
def test_validate_random_maps(agents, unreported):
    rng = random.Random(1000 + 2 * (agents == "multi") + unreported)
    fails, stats = [], collections.Counter()
    total = _n(150)
    for d, rm, story, hist in _instances(rng, agents, total):
        _check_validate(fails, stats, d, rm, story, hist, agents, unreported)
    _finish(fails, total, "validate results")
    assert stats[True] and stats[False], stats


@pytest.mark.parametrize("unreported", [False, True], ids=["strict", "unreported"])
@pytest.mark.parametrize("agents", ["single", "multi"])
def test_validate_paper_maps(agents, unreported):
    rng = random.Random(2000 + 2 * (agents == "multi") + unreported)
    fails, stats = [], collections.Counter()
    total = _n(80)
    for d, rm, story, hist in _instances(rng, agents, total, maps=PAPER_MAPS):
        _check_validate(fails, stats, d, rm, story, hist, agents, unreported)
    _finish(fails, total, "validate results")
    assert stats[True] and stats[False], stats


def _general_instances(rng, count, max_story=5, max_events=7):
    """(map dict, RegionMap, story, history, agents, unreported) on general region maps
    (``B.random_general_region_map``): 25% simulated, 25% perturbed, 50% arbitrary."""
    made = 0
    while made < count:
        d = B.random_general_region_map(rng)
        rm = B.RegionMap.from_dict(d)
        agents = rng.choice(["single", "multi"])
        unreported = rng.random() < 0.5
        r = rng.random()
        if r < 0.5:
            sim = B.simulate_walk(rm, rng, agents, max_story=max_story, max_events=max_events)
            if sim is None:
                continue
            story, hist = sim[0], sim[1]
            if r >= 0.25:
                story, hist = B.perturb(rng, rm, story, hist)
        else:
            story = B.random_story(rng, rm, 1, min(4, max_story))
            hist = B.random_history(rng, rm, agents, 0, max_events)
        made += 1
        yield d, rm, story, hist, agents, unreported


def test_general_region_maps_validate():
    """Problem 1 on region maps that are not the cliques of G (dead ends, both beam sides in
    one region, nested regions, unreachable features), all four modes; witnesses replay."""
    rng = random.Random(1500)
    fails, stats = [], collections.Counter()
    total = _n(400)
    for d, rm, story, hist, agents, unreported in _general_instances(rng, total):
        _check_validate(fails, stats, d, rm, story, hist, agents, unreported)
    _finish(fails, total, "validate results")
    assert stats[True] and stats[False], stats


def test_general_region_maps_problems_2_to_4():
    """Problems 2-4 on general region maps, single and multi agent, both Problem 3 readings."""
    rng = random.Random(1600)
    fails, stats = [], collections.Counter()
    total = _n(60)
    for d, rm, story, hist, agents, unreported in _general_instances(rng, total, max_story=3,
                                                                     max_events=4):
        m = _engine_map(d)
        kw = {"agents": agents, "unreported_visits": unreported}
        for case in range(1, 7):
            want = B.consistent_intervals(rm, story, hist, case, agents, unreported)
            got = bool(cd.validate_intervals(m, story, hist, case=case, **kw).consistent)
            stats["p2", want] += 1
            if got != want:
                fails.append("case %d: validate_intervals -> %s, oracle -> %s\n%s"
                             % (case, got, want, _rep(d, story, hist, case=case, **kw)))
        for anchored in (True, False):
            want = B.shortest_superstories(rm, story, hist, agents, unreported,
                                           anchored=anchored, max_extra=MAX_EXTRA)
            res = cd.shortest_superstory(m, story, hist, anchored=anchored, **kw)
            got = None if res is None else _story_str(res.story)
            stats["p3", want.status] += 1
            ok = (got is None) == (want.status == "none")
            if ok and want.status == "found":
                ok = len(got) == want.value
            if ok and want.status == "beyond":
                ok = len(got) > want.bound
            if ok and got is not None:
                ok = (B.is_subsequence("".join(story), got)
                      and B.consistent(rm, got, hist, agents, unreported)
                      and B.check_steps(rm, got, hist, res.path, agents, unreported)[0])
            if not ok:
                fails.append("shortest_superstory -> %r, oracle %s %s %s\n%s"
                             % (got, want.status, want.value, want.stories[:3],
                                _rep(d, story, hist, anchored=anchored, **kw)))
        want = B.closest_stories(rm, story, hist, agents, unreported, max_edits=MAX_EDITS)
        res = cd.closest_story(m, story, hist, **kw)
        got = None if res is None else _story_str(res.story)
        stats["p4", want.status] += 1
        ok = (got is None) == (want.status == "none")
        if ok and got is not None:
            dist = B.levenshtein(got, "".join(story))
            ok = (res.edits == dist
                  and (want.status != "found" or dist == want.value)
                  and (want.status != "beyond" or dist > want.bound)
                  and B.consistent(rm, got, hist, agents, unreported)
                  and B.check_steps(rm, got, hist, res.path, agents, unreported)[0])
        if not ok:
            fails.append("closest_story -> %r, oracle %s %s %s\n%s"
                         % (got, want.status, want.value, want.stories[:3],
                            _rep(d, story, hist, **kw)))
    _finish(fails, total, "Problem 2-4 instances")
    assert stats["p2", True] and stats["p2", False] and stats["p3", "found"], stats


def _malformed(rng, rm, agents):
    """A history no agent (multi) / no single agent can produce, with a non-empty story."""
    o = rng.choice(rm.occupancy)
    kinds = ["D_first", "double_A"]
    if rm.beams:
        kinds.append("beam_D")
    if agents == "single":
        kinds += ["unpaired", "other_event_inside", "overlap"]
    k = rng.choice(kinds)
    if k == "D_first":
        h = [[o, "D"]]
    elif k == "double_A":
        h = [[o, "A"], [o, "A"]]
    elif k == "beam_D":
        h = [[rng.choice(list(rm.beams)), "D"]]
    elif k == "unpaired":
        h = [[o, "A"]]
    elif k == "other_event_inside":
        other = rng.choice(list(rm.beams) + [x for x in rm.occupancy if x != o] or [o])
        h = [[o, "A"], [other, "A"], [o, "D"]] + ([[other, "D"]] if other in rm.occupancy
                                                   and other != o else [])
        if other == o:
            h = [[o, "A"], [o, "A"], [o, "D"]]
    else:
        others = [x for x in rm.occupancy if x != o]
        if not others:
            h = [[o, "D"]]
        else:
            p = rng.choice(others)
            h = [[o, "A"], [p, "A"], [o, "D"], [p, "D"]]
    pre = B.random_history(rng, rm, agents, 0, 2)
    return pre + h if agents == "single" else h + pre


@pytest.mark.parametrize("agents", ["single", "multi"])
def test_malformed_histories_rejected_with_reason(agents):
    rng = random.Random(3000 + (agents == "multi"))
    fails, total = [], 0
    while total < _n(40):
        d = B.random_region_map(rng, occupancy=(1, 3))
        rm = B.RegionMap.from_dict(d)
        story = B.random_story(rng, rm)
        hist = _malformed(rng, rm, agents)
        if B.consistent(rm, story, hist, agents):
            continue  # the "malformation" happened to be legal (e.g. multi pre + h)
        total += 1
        r = cd.validate(_engine_map(d), story, hist, agents=agents)
        if r.consistent or not r.reason:
            fails.append("validate -> %s, reason %r\n%s"
                         % (r.consistent, r.reason, _rep(d, story, hist, agents=agents)))
    _finish(fails, total, "malformed histories")


# ----------------------------------------------------------------------------- Problem 2


@pytest.mark.parametrize("unreported", [False, True], ids=["strict", "unreported"])
def test_validate_intervals_random(unreported):
    rng = random.Random(4000 + unreported)
    fails, stats = [], collections.Counter()
    total = _n(50)
    kw = {"unreported_visits": True} if unreported else {}
    for d, rm, story, hist in _instances(rng, "single", total, max_events=5):
        m = _engine_map(d)
        for case in range(1, 7):
            want = B.consistent_intervals(rm, story, hist, case, "single", unreported)
            r = cd.validate_intervals(m, story, hist, case=case, **kw)
            got = bool(r.consistent if hasattr(r, "consistent") else r)
            stats[want] += 1
            if got != want:
                fails.append("case %d: validate_intervals -> %s, oracle -> %s\n%s"
                             % (case, got, want, _rep(d, story, hist, case=case, **kw)))
    _finish(fails, 6 * total, "validate_intervals results")
    assert stats[True] and stats[False], stats


# ----------------------------------------------------------------------------- Problems 3, 4

MAX_EXTRA = 2 if SCALE < 3 else 3
MAX_EDITS = 2


def _small_instances(rng, count):
    return _instances(rng, "single", count, max_story=3, max_events=4, rooms=(2, 4))


@pytest.mark.parametrize("anchored", [True, False], ids=["anchored", "free"])
@pytest.mark.parametrize("unreported", [False, True], ids=["strict", "unreported"])
def test_shortest_superstory_random(unreported, anchored):
    """Both readings of Problem 3 (DESIGN.md "Problem 3"): the default ``anchored=True``
    (p'_1 = p_1, p'_last = p_n) and ``anchored=False`` (ICRA §V-A / Algorithm 2), each
    against the oracle's enumeration under the same reading."""
    rng = random.Random(5000 + unreported)
    fails, stats = [], collections.Counter()
    total = _n(40)
    kw = {"unreported_visits": True} if unreported else {}
    for d, rm, story, hist in _small_instances(rng, total):
        rep = _rep(d, story, hist, anchored=anchored, **kw)
        want = B.shortest_superstories(rm, story, hist, "single", unreported,
                                       anchored=anchored, max_extra=MAX_EXTRA)
        stats[want.status] += 1
        res = cd.shortest_superstory(_engine_map(d), story, hist, anchored=anchored,
                    **kw)
        got = None if res is None else _story_str(getattr(res, "story", None))
        if want.status == "none":
            if got is not None:
                fails.append("no super-story exists, engine returned %r\n%s" % (got, rep))
            continue
        if got is None:
            fails.append("engine found no super-story, oracle: %s %s %s\n%s"
                         % (want.status, want.value, want.stories[:5], rep))
            continue
        problems = []
        if res.length != len(got):
            problems.append("length %r != len(story) %d" % (res.length, len(got)))
        if not B.is_subsequence("".join(story), got):
            problems.append("%r is not a super-sequence" % got)
        if anchored and (got[:1] != story[0] or got[-1:] != story[-1]):
            problems.append("%r is not anchored at %s...%s" % (got, story[0], story[-1]))
        if not B.consistent(rm, got, hist, "single", unreported):
            problems.append("%r is not consistent" % got)
        if want.status == "found" and len(got) != want.value:
            problems.append("length %d, oracle minimum %d %s"
                            % (len(got), want.value, want.stories[:5]))
        if want.status == "beyond" and len(got) <= want.bound:
            problems.append("length %d but nothing up to %d exists" % (len(got), want.bound))
        _check_result_witness(problems, rm, got, hist, res, unreported)
        if problems:
            fails.append("; ".join(problems) + "\n" + rep)
    _finish(fails, total, "shortest_superstory results")
    assert stats["found"], stats


@pytest.mark.parametrize("unreported", [False, True], ids=["strict", "unreported"])
def test_closest_story_random(unreported):
    rng = random.Random(6000 + unreported)
    fails, stats = [], collections.Counter()
    total = _n(40)
    kw = {"unreported_visits": True} if unreported else {}
    for d, rm, story, hist in _small_instances(rng, total):
        rep = _rep(d, story, hist, **kw)
        want = B.closest_stories(rm, story, hist, "single", unreported, max_edits=MAX_EDITS)
        stats[want.status] += 1
        res = cd.closest_story(_engine_map(d), story, hist, **kw)
        got = None if res is None else _story_str(getattr(res, "story", None))
        if want.status == "none":
            if got is not None:
                fails.append("no consistent story exists, engine returned %r\n%s" % (got, rep))
            continue
        if got is None:
            fails.append("engine found no story, oracle: %s %s %s\n%s"
                         % (want.status, want.value, want.stories[:5], rep))
            continue
        problems = []
        dist = B.levenshtein(got, "".join(story))
        if res.edits != dist:
            problems.append("edits %r != Levenshtein distance %d of %r" % (res.edits, dist, got))
        if not got:
            problems.append("empty story")
        elif not B.consistent(rm, got, hist, "single", unreported):
            problems.append("%r is not consistent" % got)
        if want.status == "found" and dist != want.value:
            problems.append("distance %d, oracle minimum %d %s" % (dist, want.value,
                                                                   want.stories[:5]))
        if want.status == "beyond" and dist <= want.bound:
            problems.append("distance %d but nothing within %d exists" % (dist, want.bound))
        ops = getattr(res, "operations", None)
        if isinstance(ops, (list, tuple)) and res.edits is not None and len(ops) < res.edits:
            problems.append("%d operations for %r edits" % (len(ops), res.edits))
        _check_result_witness(problems, rm, got, hist, res, unreported)
        if problems:
            fails.append("; ".join(problems) + "\n" + rep)
    _finish(fails, total, "closest_story results")
    assert stats["found"], stats
