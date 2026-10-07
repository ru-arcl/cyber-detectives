"""Default engine: unit cases, replay, and randomized checks against independent oracles.

Randomized sweeps scale with ``CD_TEST_SCALE`` (see conftest.py).

- *Generative check* (completeness): simulate agent x (and, in multi-agent mode, other agents)
  walking on a random region map; the story and history it produces must validate, and the
  returned witness must replay.
- *Oracle check* (soundness and completeness): an explicit time-expanded graph, built
  separately from the engine's search (same movement rules; the rule-independent oracle is
  ``tests/oracles/brute.py``, see ``test_oracle_random.py``), decides random (story, history)
  pairs; verdicts must agree and every returned path must replay.
- *from_edges equivalence*: rebuilding a random map from ``G`` alone keeps every verdict.
"""

from __future__ import annotations

import random
import sys
import types
from collections import deque

import pytest

from conftest import sweep_scale
from cyber_detectives import (Event, InputError, InvalidPath, Map, Step, builtin_map,
                              parse_history, replay, validate)
from cyber_detectives import engine as engine_mod

STAR = builtin_map("star_fig2")


def check(m, story, hist, agents="single", unreported=False):
    r = validate(m, story, hist, agents, unreported_visits=unreported)
    if r.consistent:
        assert r.reason is None
        replay(m, r.path, hist, agents, story, unreported_visits=unreported)
    else:
        assert r.path is None and r.reason
    return r


# ---------------------------------------------------------------------- unit cases


def test_paper_eq2_and_feasible_history():
    assert not check(STAR, "ACBAC", "b1 o1 o1 b2 o2 o2").consistent
    r = check(STAR, "ACBAC", "b1 o1 o1 o2 o2 b2")
    assert r.consistent
    assert r.path_string() == "AC[b1d][o1][o2]B[b2r]AC"


def test_story_length_one_and_repeats():
    assert check(STAR, "A", "").consistent
    assert check(STAR, "A", "").path_string() == "A"
    assert check(STAR, "AA", "").path_string() == "AA"
    assert not check(STAR, "AB", "").consistent
    # end convention: x must be inside the last story room after the last recording
    r = check(STAR, "A", "b1")
    assert not r.consistent and "cannot be inside A" in r.reason
    assert check(STAR, "AA", "b1").consistent


def test_reason_mentions_failing_story_element():
    r = check(STAR, "ACBAC", "b1 o1 o1 b2 o2 o2")
    assert "at most 3 of the 5" in r.reason and "A" in r.reason


def test_reason_when_a_recording_cannot_be_explained():
    r = check(STAR, "B", "b1")
    assert not r.consistent and "recording 1 (b1 A)" in r.reason


def test_malformed_histories_are_inconsistent():
    r = validate(STAR, "A", "o1-")
    assert not r.consistent and r.reason.startswith("malformed history: ")
    r = validate(STAR, "ACBAC", "b1 o1 o2 b2 o2 o1")  # eq. (3) as a single-agent history
    assert not r.consistent and "malformed" in r.reason
    r = validate(STAR, "A", "o1+ o1+", agents="multi")
    assert not r.consistent and "already active" in r.reason
    r = validate(STAR, "A", "o1-", agents="multi")
    assert not r.consistent and r.reason.startswith("malformed history: ")
    assert "not active" in r.reason
    for agents in ("single", "multi"):
        assert not oracle(STAR, ["A"], "o1-", agents)
    assert not oracle(STAR, ["A"], "o1+ o1+", "multi")
    r = validate(STAR, "A", [["b1", "D"]])
    assert not r.consistent and "cannot deactivate" in r.reason
    assert not oracle(STAR, ["A"], [["b1", "D"]], "multi")
    assert not oracle(STAR, ["A", "C", "B", "A", "C"], "b1 o1 o2 b2 o2 o1", "single")


def test_oracle_well_formedness_matches_history_module():
    """The test oracle's own history check agrees with ``history.check_history``."""
    from cyber_detectives.history import check_history
    rng = random.Random(4)
    seen = {True: 0, False: 0}
    for _ in range(400):
        m = random_map(rng)
        sensors = list(m.beams) + list(m.occupancy)
        if not sensors:
            continue
        events = parse_history([[rng.choice(sensors), rng.choice("AAD")]
                                for _ in range(rng.randint(0, 6))], m)
        for agents in ("single", "multi"):
            ok = _well_formed(m, events, agents == "multi")
            assert ok == (check_history(m, events, agents) is None), (m.to_dict(), events)
            seen[ok] += 1
    assert min(seen.values()) > 50


def test_multi_basics():
    # x need not cross recorded beams
    assert check(STAR, "A", "b1 b2", "multi").consistent
    # but cannot cross an unrecorded one
    assert not check(STAR, "AB", "b1", "multi").consistent
    r = check(STAR, "AB", "b2", "multi")
    assert r.consistent and r.path_string() == "A[b2l]B"
    # passing an occupancy region opened by others
    r = check(STAR, "CB", "o1 o2 o1 o2", "multi")
    assert r.path_string() == "C{o1}{o2}B"
    # x must be out of o by its deactivation; waiting inside across it is impossible
    assert not check(STAR, "CB", "o1 o1", "multi").consistent


def test_multi_sensor_may_stay_active():
    assert check(STAR, "CB", "o1 o2", "multi").consistent


def test_unreported_visits():
    m = builtin_map("icra_fig2")
    r = check(m, "A", "b1 b1")
    assert not r.consistent
    r = check(m, "A", "b1 b1", unreported=True)
    assert r.consistent
    # x leaves A, crosses b1, enters B or C unreported, crosses back... the witness must
    # contain exactly one unreported entry, written in parentheses
    assert r.path_string().count("(") == 1 and r.path_string().startswith("A[")
    assert [s.kind for s in r.path].count("unreported") == 1


def test_input_errors():
    with pytest.raises(InputError, match="at least one room"):
        validate(STAR, "", "")
    with pytest.raises(InputError, match="not a room"):
        validate(STAR, "AD", "")
    with pytest.raises(InputError, match="not a sensor"):
        validate(STAR, "A", "b9")
    with pytest.raises(ValueError, match="agents"):
        validate(STAR, "A", "", agents="many")
    with pytest.raises(ValueError, match="compat"):
        validate(STAR, "A", "", compat="java")
    with pytest.raises(ValueError, match="unreported_visits"):
        validate(STAR, "A", "", compat="original", unreported_visits=True)


def test_result_api():
    r = validate(STAR, "AB", "b2")
    assert bool(r) is True and r.agents == "single"
    d = r.to_dict()
    assert d["consistent"] and d["path_string"] == "A[b2l]B" and d["path"][0]["kind"] == "start"
    assert [s.kind for s in r.path] == ["start", "move", "cross", "visit"]
    assert r.path[2] == Step("cross", "R4", 1, 1, sensor="b2l", event=0)
    assert validate(STAR, "B", "b2").path_string() is None


def test_events_and_pairs_equivalent():
    a = validate(STAR, ["A", "C", "B", "A", "C"],
                 [Event("b1", "A"), Event("o1", "A"), Event("o1", "D"), Event("o2", "A"),
                  Event("o2", "D"), Event("b2", "A")])
    b = validate(STAR, "A C B A C", [["b1", "A"], ["o1", "A"], ["o1", "D"], ["o2", "A"],
                                     ["o2", "D"], ["b2", "A"]])
    assert a.path == b.path


# ---------------------------------------------------------------------- compat dispatch


def test_compat_dispatch(monkeypatch):
    calls = []
    fake = types.ModuleType("cyber_detectives.compat.original")

    def validate_compat(map_dict, story, events, agents):
        calls.append((map_dict, story, events, agents))
        return True, "A[b1u]"

    fake.validate_compat = validate_compat
    monkeypatch.setitem(sys.modules, "cyber_detectives.compat.original", fake)
    real_import = engine_mod.importlib.import_module
    monkeypatch.setattr(engine_mod.importlib, "import_module",
                        lambda n: fake if n == "cyber_detectives.compat.original"
                        else real_import(n))
    r = validate(STAR, "A", "b1 o1", compat="original")
    assert r.consistent and r.compat == "original" and r.path is None
    assert r.path_string() == "A[b1u]"
    map_dict, story, events, agents = calls[0]
    assert map_dict == STAR.to_dict() and story == ["A"]
    assert events == [["b1", "A"], ["o1", "A"]] and agents == "single"
    # unknown names reach the original untouched (it has its own failure modes)
    validate(STAR, "AD", [["zz", "A"]], compat="original", agents="multi")
    assert calls[-1][1:] == (["A", "D"], [["zz", "A"]], "multi")


def test_compat_missing_module(monkeypatch):
    def boom(name):
        raise ModuleNotFoundError("no", name="cyber_detectives.compat")
    monkeypatch.setattr(engine_mod.importlib, "import_module", boom)
    with pytest.raises(NotImplementedError):
        validate(STAR, "A", "", compat="original")


# ---------------------------------------------------------------------- replay rejects


HS = "b1 o1 o1 o2 o2 b2"   # single-agent base walk: ACBAC, AC[b1d][o1][o2]B[b2r]AC
HM = "o1 o2 o1 o2"          # multi-agent base walk: CB, C{o1}{o2}B


def _single_walk():
    return list(validate(STAR, "ACBAC", HS).path)


def _multi_walk():
    return list(validate(STAR, "CB", HM, "multi").path)


def _swap(path, i, **kw):
    d = path[i].to_dict()
    d.update(kw)
    path = list(path)
    path[i] = Step(**d)
    return path


def _insert(path, i, step):
    path = list(path)
    path.insert(i, step)
    return path


def test_base_walks_replay():
    assert [s.kind for s in _single_walk()] == [
        "start", "move", "visit", "move", "cross", "enter", "exit", "enter", "exit", "visit",
        "move", "cross", "visit", "move", "visit"]
    replay(STAR, _single_walk(), HS, story="ACBAC")
    assert [(s.position, s.time, s.sensor) for s in _multi_walk()] == [
        ("C", 0, None), ("R1", 0, None), ("o1", 1, "o1"), ("R3", 1, "o1"), ("o2", 2, "o2"),
        ("R4", 2, "o2"), ("B", 2, None)]
    replay(STAR, _multi_walk(), HM, "multi", story="CB")


S, M = _single_walk, _multi_walk
# (id, walk builder, history, agents, story, the exact InvalidPath message).  Each walk is a
# base walk with one minimal edit that breaks exactly one rule of replay().
REPLAY_REJECTS = [
    # adjacency of free moves
    ("room-region", lambda: _swap(S(), 1, position="R4"), HS, "single", "ACBAC",
     "step 2 (move): A does not touch R4"),
    ("region-room", lambda: _swap(S(), 2, position="B"), HS, "single", "ACBAC",
     "step 3 (visit): B does not touch R1"),
    ("occupancy-region", lambda: _swap(M(), 3, position="R4"), HM, "multi", "CB",
     "step 4 (move): o1 does not touch R4"),
    ("room-room", lambda: _swap(S(), 1, position="C"), HS, "single", "ACBAC",
     "step 2 (move): cannot move from A (room) to C (room)"),
    # event steps
    ("cross-not-adjacent", lambda: _swap(S(), 4, sensor="b1u"), HS, "single", "ACBAC",
     "step 5 (cross): x at R1 is not next to beam side b1u"),
    ("cross-from-room", lambda: S()[:3] + S()[4:], HS, "single", "ACBAC",
     "step 4 (cross): x at C is not next to beam side b1d"),
    ("cross-wrong-beam", lambda: _swap(S(), 4, sensor="b2r"), HS, "single", "ACBAC",
     "step 5 (cross): crossing side 'b2r' does not belong to recording b1 A"),
    ("cross-wrong-position", lambda: _swap(S(), 4, position="R1"), HS, "single", "ACBAC",
     "step 5 (cross): position R1, expected R2"),
    ("event-index", lambda: _swap(S(), 4, event=1), HS, "single", "ACBAC",
     "step 5 (cross): event step without matching event index"),
    ("enter-not-adjacent", lambda: _swap(_swap(S(), 6, position="R1"), 7, position="o2"),
     HS, "single", "ACBAC", "step 8 (enter): x at R1 cannot enter o2"),
    ("enter-wrong-sensor", lambda: _swap(S(), 5, sensor="o2", position="o2"), HS, "single",
     "ACBAC", "step 6 (enter): enter step does not match recording o1 A"),
    ("exit-not-adjacent", lambda: _swap(S(), 6, position="R4"), HS, "single", "ACBAC",
     "step 7 (exit): o1 does not open into R4"),
    ("exit-wrong-sensor", lambda: _swap(S(), 6, sensor="o2"), HS, "single", "ACBAC",
     "step 7 (exit): exit step does not match recording o1 D / position o1"),
    ("multi-occupancy-event", S, HS, "multi", "ACBAC",
     "step 6 (enter): in multi-agent mode occupancy recordings do not move x"),
    # timing
    ("time-backwards", lambda: _swap(S(), 10, time=4), HS, "single", "ACBAC",
     "step 11 (move): time 4 out of order (now 5)"),
    ("time-after-last", lambda: _swap(S(), 14, time=7), HS, "single", "ACBAC",
     "step 15 (visit): time 7 out of order (now 6)"),
    ("unexplained-recording", S, HS + " b1", "single", "ACBAC",
     "step 16 (end): recording 7 (b1 A) is not explained by x"),
    ("inside-deactivating", lambda: _swap(M(), 3, time=3), HM, "multi", "CB",
     "step 4 (move): x is inside o1 when it deactivates (recording 3)"),
    ("occupancy-inactive", M, "o2 o2 o1 o1", "multi", "CB",
     "step 3 (move): o1 is not active between recordings 1 and 2"),
    ("single-passes-occupancy", lambda: _insert(S(), 4, Step("move", "o1", 0, 2, sensor="o1")),
     HS, "single", "ACBAC",
     "step 5 (move): x cannot pass o1 without a recording in single-agent mode"),
    # step fields
    ("free-move-event", lambda: _swap(S(), 1, event=0), HS, "single", "ACBAC",
     "step 2 (move): free move with an event index"),
    ("unreported-entry", lambda: _swap(S(), 2, kind="move"), HS, "single", "ACBAC",
     "step 3 (move): entering C without reporting it"),
    ("leave-as-visit", lambda: _swap(S(), 1, kind="visit"), HS, "single", "ACBAC",
     "step 2 (visit): leaving a room is a plain move"),
    ("unknown-kind", lambda: _swap(S(), 1, kind="teleport"), HS, "single", "ACBAC",
     "step 2 (teleport): unknown step kind 'teleport'"),
    ("story-index", lambda: _swap(S(), 2, story_index=1), HS, "single", "ACBAC",
     "step 3 (visit): story_index 1, expected 2"),
    ("sensor-occupancy-exit", lambda: _swap(M(), 3, sensor=None), HM, "multi", "CB",
     "step 4 (move): sensor None, expected 'o1'"),
    ("sensor-occupancy-entry", lambda: _swap(M(), 2, sensor="o2"), HM, "multi", "CB",
     "step 3 (move): sensor 'o2', expected 'o1'"),
    ("sensor-plain-move", lambda: _swap(S(), 1, sensor="o1"), HS, "single", "ACBAC",
     "step 2 (move): sensor 'o1', expected None"),
    ("sensor-visit", lambda: _swap(M(), 6, sensor="o2"), HM, "multi", "CB",
     "step 7 (visit): sensor 'o2', expected None"),
    # start and end
    ("no-start", lambda: S()[1:], HS, "single", "ACBAC",
     "step 1 (move): a walk must begin with a start step at time 0"),
    ("start-outside-room", lambda: _swap(S(), 0, position="R1"), HS, "single", "ACBAC",
     "step 1 (start): x must start inside a room"),
    ("end-outside-room", lambda: S()[:-1], HS, "single", None,
     "x ends at R1, not inside the last story room A"),
    ("story-mismatch", S, HS, "single", "ACBAA",
     "reported visits ACBAC do not spell the story ACBAA"),
    ("empty", lambda: [], HS, "single", None, "empty path"),
]


@pytest.mark.parametrize("build, hist, agents, story, msg",
                         [c[1:] for c in REPLAY_REJECTS], ids=[c[0] for c in REPLAY_REJECTS])
def test_replay_rejects_tampering(build, hist, agents, story, msg):
    with pytest.raises(InvalidPath) as ei:
        replay(STAR, build(), hist, agents, story)
    assert str(ei.value) == msg


def test_replay_multi_rules():
    r = validate(STAR, "CB", "o1 o2 o1 o2", "multi")
    replay(STAR, r.path, "o1 o2 o1 o2", "multi", "CB")
    # same walk, but o1 and o2 are closed when x passes them
    with pytest.raises(InvalidPath, match="not active"):
        replay(STAR, r.path, "o2 o2 o1 o1", "multi", "CB")
    # single-agent replay of a multi walk must fail (passing o1 without a recording)
    with pytest.raises(InvalidPath):
        replay(STAR, r.path, "o1 o2 o1 o2", "single", "CB")
    # an unreported entry needs unreported_visits
    m = builtin_map("icra_fig2")
    r = validate(m, "A", "b1 b1", unreported_visits=True)
    with pytest.raises(InvalidPath, match="without reporting"):
        replay(m, r.path, "b1 b1", story="A")


# ---------------------------------------------------------------------- random maps


ROOMS = "ABCDE"


def random_map(rng, name="rnd"):
    nrooms = rng.randint(1, 4)
    nbeams = rng.randint(0, 3)
    nocc = rng.randint(0, 3)
    nreg = rng.randint(1, 5)
    rooms = list(ROOMS[:nrooms])
    beams = {"b%d" % (i + 1): ["b%dx" % (i + 1), "b%dy" % (i + 1)] for i in range(nbeams)}
    occ = ["o%d" % (i + 1) for i in range(nocc)]
    regs = [[] for _ in range(nreg)]
    for ss in beams.values():
        for s in ss:
            regs[rng.randrange(nreg)].append(s)
    for r in rooms:
        k = rng.choice([0, 1, 1, 1, 2, 2, 3])
        for i in rng.sample(range(nreg), min(k, nreg)):
            regs[i].append(r)
    for o in occ:
        k = rng.choice([1, 1, 2, 2, 3])
        for i in rng.sample(range(nreg), min(k, nreg)):
            regs[i].append(o)
    regs = [r for r in regs if r]
    return Map(name, rooms, beams, occ, regs)


def random_inputs(rng, m, agents):
    story = [rng.choice(m.rooms) for _ in range(rng.randint(1, 5))]
    sensors = list(m.beams) + list(m.occupancy)
    hist = []
    if sensors:
        active = set()
        for _ in range(rng.randint(0, 6)):
            s = rng.choice(sensors)
            if m.is_beam(s):
                hist.append([s, "A"])
            elif agents == "single" and rng.random() < 0.85:
                hist += [[s, "A"], [s, "D"]]
            elif rng.random() < 0.9:
                hist.append([s, "D" if s in active else "A"])
                (active.discard if s in active else active.add)(s)
            else:
                hist.append([s, rng.choice("AD")])
    return story, hist


def simulate(rng, m, agents, unreported=False, max_steps=14):
    """Walk agent x (and other agents in multi mode) and record what the sensors see.

    Returns (story, history) or None if x did not end inside a room.
    """
    start = rng.choice(m.rooms)
    pos = ("room", start)
    story = [start]
    hist = []
    inside = {o: 0 for o in m.occupancy}   # number of agents inside, x included
    others = {o: 0 for o in m.occupancy}
    for _ in range(rng.randint(0, max_steps)):
        opts = []
        kind, p = pos
        if kind == "room":
            opts += [("go", ("region", r)) for r in m.regions_of[p]]
        elif kind == "region":
            for f in m.regions[p]:
                if m.is_room(f):
                    opts.append(("visit", f))
                elif m.is_occupancy(f):
                    opts.append(("enter", f))
                else:
                    opts.append(("cross", f))
        else:
            opts += [("leave", r) for r in m.regions_of[p]]
        if agents == "multi":
            opts += [("other_beam", b) for b in m.beams]
            opts += [("other_in", o) for o in m.occupancy]
            opts += [("other_out", o) for o in m.occupancy if others[o]]
        if not opts:
            break
        act, arg = rng.choice(opts)
        if act == "go":
            pos = arg
        elif act == "visit":
            pos = ("room", arg)
            if not (unreported and rng.random() < 0.3):
                story.append(arg)
        elif act == "cross":
            hist.append([m.side_beam[arg], "A"])
            pos = ("region", m.side_region(m.other_side(arg)))
        elif act == "enter":
            if inside[arg] == 0:
                hist.append([arg, "A"])
            inside[arg] += 1
            pos = ("occ", arg)
        elif act == "leave":
            inside[p] -= 1
            if inside[p] == 0:
                hist.append([p, "D"])
            pos = ("region", arg)
        elif act == "other_beam":
            hist.append([arg, "A"])
        elif act == "other_in":
            if inside[arg] == 0:
                hist.append([arg, "A"])
            inside[arg] += 1
            others[arg] += 1
        elif act == "other_out":
            inside[arg] -= 1
            others[arg] -= 1
            if inside[arg] == 0:
                hist.append([arg, "D"])
    if pos[0] != "room":
        return None
    if unreported and story[-1] != pos[1]:
        story.append(pos[1])  # x is in this room at t_f; the last entry must be reported
    return story, hist


def _well_formed(m, events, multi):
    """The agent model's history constraints, checked here without ``history.py``.

    Beams only record ``A``.  Single agent: x is the only agent, so every occupancy ``A`` is
    immediately followed by the matching ``D`` (x sits inside in between) and every ``D``
    immediately preceded by it.  Multi-agent: per sensor, ``A`` and ``D`` alternate, starting
    with ``A`` (a sensor may stay active at the end).
    """
    on = set()
    i = 0
    while i < len(events):
        s, kind = events[i]
        if s in m.beams:
            if kind != "A":
                return False
        elif multi:
            if (kind == "A") == (s in on):
                return False
            (on.add if kind == "A" else on.discard)(s)
        else:
            if kind != "A" or i + 1 == len(events) or tuple(events[i + 1]) != (s, "D"):
                return False
            i += 1
        i += 1
    return True


def oracle(m, story, hist, agents, unreported=False):
    """Explicit time-expanded graph over (interval, place, story index); BFS from start.

    A second formulation of the search, as an explicit graph built up front rather than the
    engine's layered closure: places are ("room"|"reg"|"occ", name) and the movement rules
    are read straight off the region lists.  The rules themselves mirror the engine's
    reading of the model, so this catches search/bookkeeping errors, not a misreading of the
    model; that independence comes from the brute-force enumerator ``tests/oracles/brute.py``
    (``tests/test_oracle_random.py``).  History well-formedness is checked by
    ``_well_formed`` above, not by ``history.check_history``.
    """
    events = parse_history(hist, m)
    if not _well_formed(m, events, agents == "multi"):
        return False
    multi = agents == "multi"
    n, T = len(story), len(events)
    reg_of = {}
    for r, fs in m.regions.items():
        for f in fs:
            reg_of.setdefault(f, []).append(r)
    act = [set()]
    for e in events:
        a = set(act[-1])
        if e.sensor in m.occupancy:
            if e.kind == "A":
                a.add(e.sensor)
            else:
                a.discard(e.sensor)
        act.append(a)
    adj = {}

    def edge(u, v):
        adj.setdefault(u, []).append(v)

    places = ([("room", r) for r in m.rooms] + [("reg", r) for r in m.regions]
              + [("occ", o) for o in m.occupancy])
    for t in range(T + 1):
        for k in range(1, n + 1):
            for pl in places:
                kind, name = pl
                u = (t, pl, k)
                # free moves
                if kind == "room":
                    for r in reg_of.get(name, []):
                        edge(u, (t, ("reg", r), k))
                elif kind == "reg":
                    for f in m.regions[name]:
                        if f in m.rooms:
                            if k < n and story[k] == f:
                                edge(u, (t, ("room", f), k + 1))
                            if unreported:
                                edge(u, (t, ("room", f), k))
                        elif f in m.occupancy and multi and f in act[t]:
                            edge(u, (t, ("occ", f), k))
                elif multi and name in act[t]:
                    for r in reg_of[name]:
                        edge(u, (t, ("reg", r), k))
                if t == T:
                    continue
                e = events[t]
                if e.sensor in m.beams:
                    if multi:
                        edge(u, (t + 1, pl, k))
                    if kind == "reg":
                        a, b = m.beams[e.sensor]
                        for s, o in ((a, b), (b, a)):
                            if reg_of[s] == [name]:
                                edge(u, (t + 1, ("reg", reg_of[o][0]), k))
                elif multi:
                    if not (e.kind == "D" and pl == ("occ", e.sensor)):
                        edge(u, (t + 1, pl, k))
                elif e.kind == "A":
                    if kind == "reg" and e.sensor in m.regions[name]:
                        edge(u, (t + 1, ("occ", e.sensor), k))
                elif pl == ("occ", e.sensor):
                    for r in reg_of[e.sensor]:
                        edge(u, (t + 1, ("reg", r), k))
    start = (0, ("room", story[0]), 1)
    goal = (T, ("room", story[-1]), n)
    seen = {start}
    dq = deque([start])
    while dq:
        u = dq.popleft()
        if u == goal:
            return True
        for v in adj.get(u, []):
            if v not in seen:
                seen.add(v)
                dq.append(v)
    return False


@pytest.mark.parametrize("agents", ["single", "multi"])
@pytest.mark.parametrize("unreported", [False, True])
def test_generated_walks_validate(agents, unreported):
    rng = random.Random({"single": 11, "multi": 23}[agents] + 7 * unreported)
    done = 0
    target = 150 * sweep_scale()
    tries = 0
    while done < target and tries < 20 * target:
        tries += 1
        m = random_map(rng)
        out = simulate(rng, m, agents, unreported)
        if out is None:
            continue
        story, hist = out
        r = check(m, story, hist, agents, unreported)
        assert r.consistent, (m.to_dict(), story, hist, agents, r.reason)
        done += 1
    assert done == target


def test_generated_walks_on_paper_maps():
    rng = random.Random(5)
    target = 60 * sweep_scale()  # walks with a non-empty history, per map and mode
    for name in ("star_fig2", "icra_fig2", "icra_fig1"):
        m = builtin_map(name)
        for agents in ("single", "multi"):
            done = tries = 0
            while done < target and tries < 20 * target:
                tries += 1
                out = simulate(rng, m, agents, max_steps=25)
                if out is None or not out[1]:
                    continue
                story, hist = out
                assert check(m, story, hist, agents).consistent, (name, story, hist, agents)
                done += 1
            assert done == target, (name, agents, done, tries)


@pytest.mark.parametrize("agents", ["single", "multi"])
def test_against_oracle_random_maps(agents):
    rng = random.Random({"single": 101, "multi": 202}[agents])
    stats = {True: 0, False: 0}
    for _ in range(300 * sweep_scale()):
        m = random_map(rng)
        story, hist = random_inputs(rng, m, agents)
        unreported = rng.random() < 0.25
        r = check(m, story, hist, agents, unreported)
        want = oracle(m, story, hist, agents, unreported)
        assert r.consistent == want, (m.to_dict(), story, hist, agents, unreported, r.reason)
        stats[want] += 1
    assert stats[True] > 20 and stats[False] > 20  # the sweep exercises both verdicts


@pytest.mark.parametrize("agents", ["single", "multi"])
def test_against_oracle_star_fig2(agents):
    rng = random.Random({"single": 7, "multi": 8}[agents])
    for _ in range(300 * sweep_scale()):
        story, hist = random_inputs(rng, STAR, agents)
        r = check(STAR, story, hist, agents)
        assert r.consistent == oracle(STAR, story, hist, agents), (story, hist, agents)


def test_from_edges_keeps_verdicts():
    """A map rebuilt from G alone (maximal cliques) gives the same verdicts (see the
    Map.from_edges docstring for why), including icra_fig2 with its spurious clique."""
    rng = random.Random(99)
    maps = [builtin_map("icra_fig2"), builtin_map("star_fig2")]
    maps += [random_map(rng) for _ in range(40 * sweep_scale())]
    for m in maps:
        # isolated rooms are the documented exception
        if any(not m.regions_of[r] for r in m.rooms):
            continue
        m2 = Map.from_edges(m.name, m.rooms, m.beams, m.occupancy, m.edges())
        for _ in range(15):
            agents = rng.choice(["single", "multi"])
            story, hist = random_inputs(rng, m, agents)
            a = validate(m, story, hist, agents).consistent
            b = check(m2, story, hist, agents).consistent
            assert a == b, (m.to_dict(), story, hist, agents)


def test_search_size_is_linear():
    """Long inputs stay fast: O((n+1)(m+1)|map|) states."""
    rng = random.Random(3)
    m = builtin_map("icra_fig2")
    out = None
    while out is None or len(out[1]) < 40:
        out = simulate(rng, m, "single", max_steps=400)
    story, hist = out
    assert check(m, story, hist).consistent
