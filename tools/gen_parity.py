#!/usr/bin/env python3
"""Generate the JS parity fixtures ``tests/fixtures/parity/*.json`` from the Python package.

The browser demo's engine (``docs/js/engine.js``, ``docs/js/original.js``) must give exactly
the Python answers: verdicts, reasons, witness steps and path strings, Problem 2-4 stories /
edits / operations, ``possible_positions`` states, parse and map error messages, ``replay``
messages, and ``compat="original"`` verdicts, paths and crashes.  This script records them;
``npm test`` (``tests/js/*.test.js``) replays every record through the JS engine, and
``tests/test_parity.py`` regenerates the records in memory and fails when the committed files
are stale (so any Python behaviour change forces a regeneration).

Usage::

    python3 tools/gen_parity.py            # (re)write tests/fixtures/parity/*.json
    python3 tools/gen_parity.py --check    # exit 1 if a file is missing or out of date

Deterministic: a private SplitMix64 generator (no dependence on ``random``'s algorithms) and
fixed seeds; one record per line.  Objects keep Python's insertion order (no sorted keys):
the JS engine reads the JSON text in that order, as Python does, except for all-digit object
keys, which JS iterates first (``Map.to_dict`` writes such beams/regions as pair lists).

Every string in the inputs uses only characters assigned in Unicode 3.2 (checked against
``unicodedata.ucd_3_2_0``), so ``repr`` texts agree across Python 3.9-3.13 and browsers,
whose Unicode versions differ (``docs/js/engine.js``, "Known differences").

Record format (every file)::

    {"generator": ..., "description": ..., "maps": {name: Map.to_dict() (no geometry)},
     "cases": [{"id": ..., "fn": ..., "args": {...}, "expect": {"return": ...}
                                                    | {"error": {"type", "message"}}}]}

``args.map`` names an entry of ``maps`` (the JS side builds it with ``Map.fromDict``).  The
``fn`` values and their argument names are listed in ``FUNCTIONS`` below.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

import cyber_detectives as cd  # noqa: E402
from cyber_detectives import history as hist_mod  # noqa: E402
from cyber_detectives import problems as prob_mod  # noqa: E402
from cyber_detectives.compat import javahash as jh  # noqa: E402
from cyber_detectives.compat import original as orig  # noqa: E402

OUT_DIR = os.path.join(REPO, "tests", "fixtures", "parity")
GOLDEN = os.path.join(REPO, "tests", "fixtures", "golden")
PAPER = os.path.join(REPO, "tests", "fixtures", "paper")
MAX_BYTES = 1_500_000

#: fn -> argument names (documentation for the JS runner, tests/js/engine.test.js)
FUNCTIONS = {
    "map_from_dict": ["dict"],
    "parse_story": ["story", "map?"],
    "parse_history": ["history", "map?", "check_names?"],
    "history_to_string": ["events", "map?"],
    "check_history": ["map", "events", "agents"],
    "validate": ["map", "story", "history", "agents", "unreported_visits", "compat?"],
    "possible_positions": ["map", "history", "story", "agents", "unreported_visits", "starts?"],
    "validate_intervals": ["map", "story", "history", "case", "agents", "unreported_visits"],
    "shortest_superstory": ["map", "story", "history", "anchored", "agents", "unreported_visits"],
    "closest_story": ["map", "story", "history", "agents", "unreported_visits"],
    "replay": ["map", "path", "history", "agents", "story", "unreported_visits"],
    "validate_compat": ["map", "story", "events", "agents"],
    "javahash": ["kind", "ops"],
}


# ---------------------------------------------------------------------- randomness


class Rng:
    """SplitMix64: tiny, fast, and identical on every Python version."""

    MASK = (1 << 64) - 1

    def __init__(self, seed: int) -> None:
        self.s = seed & self.MASK

    def next64(self) -> int:
        self.s = (self.s + 0x9E3779B97F4A7C15) & self.MASK
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self.MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self.MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        return self.next64() % n

    def randint(self, a: int, b: int) -> int:
        return a + self.below(b - a + 1)

    def random(self) -> float:
        return self.next64() / 2.0 ** 64

    def choice(self, seq: Sequence[Any]) -> Any:
        return seq[self.below(len(seq))]

    def sample(self, seq: Sequence[Any], k: int) -> List[Any]:
        pool = list(seq)
        out = []
        for _ in range(k):
            out.append(pool.pop(self.below(len(pool))))
        return out


# ---------------------------------------------------------------------- recording helpers


def check_chars(x: Any, where: str) -> None:
    """Every non-ASCII character of a JSON value is assigned in Unicode 3.2 (long before 13)
    with the same general category as today: its repr is the same in every Python >= 3.9 and
    in every JS engine with Unicode property escapes."""
    if isinstance(x, str):
        for ch in x:
            if ord(ch) > 0x7F:
                old = unicodedata.ucd_3_2_0.category(ch)
                if old == "Cn" or old != unicodedata.category(ch):
                    raise SystemExit("%s: character U+%04X is not stable across Unicode "
                                     "versions" % (where, ord(ch)))
    elif isinstance(x, list):
        for y in x:
            check_chars(y, where)
    elif isinstance(x, dict):
        for k, v in x.items():
            check_chars(k, where)
            check_chars(v, where)


def outcome(fn: Callable[[], Any]) -> Dict[str, Any]:
    try:
        return {"return": fn()}
    except orig.JavaException as e:
        return {"error": {"type": "JavaException", "message": str(e), "java": e.describe()}}
    except (ValueError, KeyError, NotImplementedError) as e:
        return {"error": {"type": type(e).__name__, "message": str(e)}}


def res_dict(r: Any) -> Any:
    if r is None:
        return None
    d = r.to_dict()
    if isinstance(r, prob_mod.ClosestResult):
        d["edit_ops"] = [{"op": o.op, "index": o.index, "old": o.old, "new": o.new}
                         for o in r.operations]
    return d


class Book:
    """Collects the maps and cases of one fixture file."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.maps: Dict[str, Any] = {}
        self.objs: Dict[str, cd.Map] = {}
        self.cases: List[Dict[str, Any]] = []

    def add_map(self, m: cd.Map) -> str:
        d = m.to_dict()
        d.pop("geometry", None)
        if m.name in self.maps:
            assert self.maps[m.name] == d, m.name
        else:
            self.maps[m.name] = d
            self.objs[m.name] = cd.Map.from_dict(d)
        return m.name

    def case(self, cid: str, fn: str, args: Dict[str, Any], expect: Dict[str, Any]) -> None:
        check_chars(args, "%s/%s" % (self.name, cid))
        self.cases.append({"id": cid, "fn": fn, "args": args, "expect": expect})

    # -- the engine calls ------------------------------------------------------------

    def validate(self, cid: str, mname: str, story: Any, hist: Any, agents: str,
                 unrep: bool) -> Optional[dict]:
        m = self.objs[mname]
        ex = outcome(lambda: res_dict(cd.validate(m, story, hist, agents,
                                                  unreported_visits=unrep)))
        self.case(cid, "validate", {"map": mname, "story": story, "history": hist,
                                    "agents": agents, "unreported_visits": unrep}, ex)
        return ex.get("return")

    def positions(self, cid: str, mname: str, story: Any, hist: Any, agents: str,
                  unrep: bool, starts: Optional[str] = None) -> None:
        m = self.objs[mname]
        args = {"map": mname, "history": hist, "story": story, "agents": agents,
                "unreported_visits": unrep}
        if starts is None:
            ex = outcome(lambda: cd.possible_positions(m, hist, story, agents, unrep))
        else:
            args["starts"] = starts
            ex = outcome(lambda: cd.possible_positions(m, hist, story, agents, unrep,
                                                       starts=starts))
        self.case(cid, "possible_positions", args, ex)

    def filters(self, cid: str, mname: str, hist: Any, agents: str) -> None:
        """The story-free filter, both start rules."""
        self.positions(cid + "/filter", mname, None, hist, agents, False)
        self.positions(cid + "/filter_anywhere", mname, None, hist, agents, False, "anywhere")

    def intervals(self, cid: str, mname: str, story: Any, hist: Any, case: int, agents: str,
                  unrep: bool) -> None:
        m = self.objs[mname]
        ex = outcome(lambda: res_dict(cd.validate_intervals(
            m, story, hist, case, agents=agents, unreported_visits=unrep)))
        self.case(cid, "validate_intervals", {"map": mname, "story": story, "history": hist,
                                              "case": case, "agents": agents,
                                              "unreported_visits": unrep}, ex)

    def superstory(self, cid: str, mname: str, story: Any, hist: Any, anchored: bool,
                   agents: str, unrep: bool) -> None:
        m = self.objs[mname]
        ex = outcome(lambda: res_dict(cd.shortest_superstory(
            m, story, hist, anchored=anchored, agents=agents, unreported_visits=unrep)))
        self.case(cid, "shortest_superstory", {"map": mname, "story": story, "history": hist,
                                               "anchored": anchored, "agents": agents,
                                               "unreported_visits": unrep}, ex)

    def closest(self, cid: str, mname: str, story: Any, hist: Any, agents: str,
                unrep: bool) -> None:
        m = self.objs[mname]
        ex = outcome(lambda: res_dict(cd.closest_story(m, story, hist, agents=agents,
                                                       unreported_visits=unrep)))
        self.case(cid, "closest_story", {"map": mname, "story": story, "history": hist,
                                         "agents": agents, "unreported_visits": unrep}, ex)

    def replay(self, cid: str, mname: str, path: List[dict], hist: Any, agents: str,
               story: Any, unrep: bool) -> None:
        m = self.objs[mname]
        steps = [cd.Step(**s) for s in path]
        ex = outcome(lambda: cd.replay(m, steps, hist, agents, story, unrep))
        self.case(cid, "replay", {"map": mname, "path": path, "history": hist,
                                  "agents": agents, "story": story,
                                  "unreported_visits": unrep}, ex)

    def all_problems(self, cid: str, mname: str, story: Any, hist: Any, agents: str,
                     unrep: bool, cases: Sequence[int] = (1, 2, 3, 4, 5, 6)) -> None:
        for c in cases:
            self.intervals("%s/p2c%d" % (cid, c), mname, story, hist, c, agents, unrep)
        for anchored in (True, False):
            self.superstory("%s/p3%s" % (cid, "a" if anchored else "f"), mname, story, hist,
                            anchored, agents, unrep)
        self.closest(cid + "/p4", mname, story, hist, agents, unrep)

    # -- output ------------------------------------------------------------------------

    def render(self) -> str:
        head = {"generator": "tools/gen_parity.py", "description": self.description,
                "functions": FUNCTIONS}
        parts = [json.dumps(head, sort_keys=True)[:-1] + ',"maps":{']
        names = sorted(self.maps)
        for i, n in enumerate(names):
            check_chars(self.maps[n], "%s/maps/%s" % (self.name, n))
            parts.append("%s:%s%s" % (json.dumps(n), json.dumps(self.maps[n]),
                                      "," if i < len(names) - 1 else ""))
        parts.append('},"cases":[')
        for i, c in enumerate(self.cases):
            parts.append(json.dumps(c) + ("," if i < len(self.cases) - 1 else ""))
        parts.append("]}")
        text = "\n".join(parts) + "\n"
        json.loads(text)  # well-formed
        return text


# ---------------------------------------------------------------------- random inputs


ROOM_NAME_SETS = [list("ABCD"), list("ABCD"), list("ABCD"), ["R1", "R2", "Hall", "D"],
                  ["Lab", "Office", "A", "B"]]


def random_map(rng: Rng, name: str) -> cd.Map:
    """A small random region map (general: dead ends, both sides of a beam in one region,
    unconnected features are allowed), regions sometimes unnamed (auto names)."""
    while True:
        names = rng.choice(ROOM_NAME_SETS)
        nr, nb, no = rng.randint(1, 4), rng.randint(0, 3), rng.randint(0, 3)
        k = rng.randint(1, 4)
        if nb and k < 2 and rng.random() < 0.8:
            k = 2
        regs: List[List[str]] = [[] for _ in range(k)]

        def put(r: int, f: str) -> None:
            if f not in regs[r]:
                regs[r].append(f)

        rooms = names[:nr]
        beams = {"b%d" % (i + 1): ["b%du" % (i + 1), "b%dd" % (i + 1)] for i in range(nb)}
        occ = ["o%d" % (i + 1) for i in range(no)]
        for s0, s1 in beams.values():
            r0 = rng.below(k)
            r1 = rng.below(k)
            if k >= 2 and r1 == r0 and rng.random() < 0.85:
                r1 = (r0 + 1 + rng.below(k - 1)) % k
            put(r0, s0)
            put(r1, s1)
        for o in occ:
            cnt = 0 if rng.random() < 0.1 else rng.randint(1, min(3, k))
            for r in rng.sample(range(k), cnt):
                put(r, o)
        for a in rooms:
            cnt = 0 if rng.random() < 0.05 else rng.randint(1, min(2, k))
            for r in rng.sample(range(k), cnt):
                put(r, a)
        regs = [r for r in regs if r]
        if not regs:
            continue
        if rng.random() < 0.5:
            regions: Any = regs
        else:
            regions = {"Z%d" % (i + 1): r for i, r in enumerate(regs)}
        return cd.Map(name, rooms, beams, occ, regions)


def explainable(m: cd.Map, events: List[List[str]], agents: str) -> bool:
    """Some walk that starts inside a room explains ``events``."""
    return any(cd.possible_positions(m, events, None, agents)[-1])


def random_history(rng: Rng, m: cd.Map, agents: str, max_len: int,
                   explained: bool) -> List[List[str]]:
    """A history; ``explained``: grown event by event, keeping it explainable by some walk."""
    sensors = list(m.beams) + list(m.occupancy)
    out: List[List[str]] = []
    if not sensors:
        return out
    target = rng.randint(0, max_len)
    on: set = set()
    tries = 0
    while len(out) < target and tries < 40:
        tries += 1
        s = rng.choice(sensors)
        if s in m.beams:
            cand = [[s, "A"]]
        elif agents == "single":
            cand = [[s, "A"], [s, "D"]]
        else:
            cand = [[s, "D" if s in on else "A"]]
        if explained and not explainable(m, out + cand, agents):
            continue
        out += cand
        if agents == "multi" and s in m.occupancy:
            on.symmetric_difference_update({s})
    return out


def random_story(rng: Rng, m: cd.Map, lo: int = 1, hi: int = 4) -> List[str]:
    return [rng.choice(m.rooms) for _ in range(rng.randint(lo, hi))]


def consistent_story(rng: Rng, m: cd.Map, hist: List[List[str]], agents: str
                     ) -> Optional[List[str]]:
    r = cd.closest_story(m, random_story(rng, m, 1, 3), hist, agents=agents)
    return None if r is None else r.story


def malformed_history(rng: Rng, m: cd.Map, agents: str) -> List[List[str]]:
    o = rng.choice(m.occupancy)
    k = rng.below(5)
    if k == 0:
        h = [[o, "D"]]
    elif k == 1:
        h = [[o, "A"], [o, "A"]]
    elif k == 2 and m.beams:
        h = [[rng.choice(list(m.beams)), "D"]]
    elif k == 3:
        h = [[o, "A"]] + ([[rng.choice(list(m.beams)), "A"]] if m.beams else []) + [[o, "D"]]
    else:
        h = [[o, "A"]]
    pre = random_history(rng, m, agents, 2, False)
    return pre + h


def as_input(rng: Rng, m: cd.Map, story: List[str], hist: List[List[str]]):
    """Vary the input forms: story list / string, history pairs / token string."""
    if rng.random() < 0.4 and all(len(r) == 1 for r in story):
        story_in: Any = "".join(story)
    elif rng.random() < 0.3:
        story_in = " ".join(story)
    else:
        story_in = list(story)
    hist_in: Any = [list(e) for e in hist]
    if rng.random() < 0.3:
        hist_in = hist_mod.history_to_string([cd.Event(*e) for e in hist], m)
    return story_in, hist_in


#: names that are special as JavaScript object keys (Object.prototype members, ...)
JS_NAMES = ["constructor", "__proto__", "toString", "valueOf", "hasOwnProperty", "prototype",
            "length", "isPrototypeOf", "__defineGetter__", "__lookupSetter__",
            "propertyIsEnumerable", "toLocaleString", "then", "name", "caller", "arguments",
            "apply", "call", "bind", "size", "has", "get", "set", "keys", "__defineSetter__",
            "__lookupGetter__"]


def special_map_dicts() -> List[tuple]:
    """Map dicts whose names are Object.prototype members or all digits, in the forms both
    engines read in the same order (an object with all-digit keys only in JS order)."""
    proto = {"name": "proto", "rooms": ["constructor", "__proto__", "toString"],
             "beams": {"hasOwnProperty": ["valueOf", "length"],
                       "__proto__b": ["prototype", "isPrototypeOf"]},
             "occupancy": ["__defineGetter__", "propertyIsEnumerable"],
             "regions": {"toLocaleString": ["constructor", "valueOf", "__defineGetter__"],
                         "__lookupGetter__": ["__proto__", "length", "prototype",
                                              "__defineGetter__", "propertyIsEnumerable"],
                         "then": ["toString", "isPrototypeOf", "propertyIsEnumerable"]}}
    proto2 = {"name": "proto2", "rooms": ["A", "B"], "beams": {"__proto__": ["bu", "bd"]},
              "occupancy": ["constructor"],
              "regions": {"__proto__R": ["A", "bu", "constructor"],
                          "hasOwnProperty": ["B", "bd", "constructor"]}}
    proto3 = {"name": "proto3", "rooms": ["__proto__", "B"], "beams": {"b": ["bu", "bd"]},
              "occupancy": ["toString"], "regions": [["__proto__", "bu", "toString"],
                                                     ["B", "bd", "toString"]]}
    digits = {"name": "digits", "rooms": ["1", "A", "0"],
              "beams": [["7", ["70", "71"]], ["b", ["bu", "bd"]], ["3", ["30", "31"]]],
              "occupancy": ["5", "o"],
              "regions": [["9", ["1", "70", "5", "30"]], ["Z", ["A", "71", "bu", "5", "o"]],
                          ["2", ["0", "bd", "o", "31"]]]}
    digits_obj = {"name": "digits_obj", "rooms": ["12", "A", "3"],
                  "beams": {"4": ["40", "41"], "10": ["100", "101"], "007": ["s1", "s2"],
                            "b": ["bu", "bd"]},
                  "occupancy": ["o"],
                  "regions": {"2": ["12", "40", "100", "s1", "bu", "o"],
                              "9": ["A", "41", "101", "o"], "R": ["3", "s2", "bd"]}}
    digits_regions = {"name": "digits_regions", "rooms": ["A", "B"],
                      "beams": {"b": ["bu", "bd"]}, "occupancy": [],
                      "regions": [["8", ["A", "bu"]], ["1", ["B", "bd"]]]}
    digits_beams = {"name": "digits_beams", "rooms": ["A", "B"],
                    "beams": [["9", ["bu", "bd"]], ["2", ["cu", "cd"]]], "occupancy": [],
                    "regions": {"R": ["A", "bu", "cu"], "S": ["B", "bd", "cd"]}}
    return [("proto", proto), ("proto2", proto2), ("proto3", proto3), ("digits", digits),
            ("digits_obj", digits_obj), ("digits_regions", digits_regions),
            ("digits_beams", digits_beams)]


def renamed(rng: Rng, m: cd.Map, scheme: str, name: str) -> cd.Map:
    """``m`` with its names replaced by JS-special names or by (mostly) all-digit names;
    beams and regions passed as [name, value] pairs."""
    old = list(m.rooms) + list(m.beams) + list(m.sides) + list(m.occupancy) + list(m.regions)
    if scheme == "js":
        pool = rng.sample(JS_NAMES, len(old))
    else:
        pool = [str(n) for n in rng.sample(range(40), len(old))]
    new = {}
    for o, n in zip(old, pool):
        new[o] = o if rng.random() < 0.25 else n
    return cd.Map(name, [new[r] for r in m.rooms],
                  [[new[b], [new[s] for s in ss]] for b, ss in m.beams.items()],
                  [new[o] for o in m.occupancy],
                  [[new[r], [new[f] for f in fs]] for r, fs in m.regions.items()])


def gen_names(seed: int, count: int) -> Book:
    bk = Book("names", "names that are special as JS object keys (constructor, __proto__, ...) "
                       "or all digits (JS iterates them first): parsing, validate, "
                       "possible_positions, replay, Problems 2-4")
    for cid, d in special_map_dicts():
        bk.add_map(cd.Map.from_dict(d))
    proto = bk.objs["proto"]
    # the toggling token notation and its rendering with special names
    hists = ["hasOwnProperty __defineGetter__ __defineGetter__ __proto__b",
             "__defineGetter__ __defineGetter__ __defineGetter__ __defineGetter__",
             "__defineGetter__ propertyIsEnumerable propertyIsEnumerable __defineGetter__",
             "__defineGetter__+ __defineGetter__+", "hasOwnProperty- constructor"]
    for i, h in enumerate(hists):
        bk.case("parse/proto/%d" % i, "parse_history", {"history": h, "map": "proto",
                                                        "check_names": True},
                outcome(lambda: [list(e) for e in cd.parse_history(h, proto)]))
        try:
            evs = [list(e) for e in cd.parse_history(h, proto)]
        except cd.InputError:
            continue
        bk.case("to_string/proto/%d" % i, "history_to_string", {"events": evs, "map": "proto"},
                outcome(lambda: hist_mod.history_to_string([cd.Event(*e) for e in evs], proto)))
        for agents in ("single", "multi"):
            bk.case("check/proto/%d/%s" % (i, agents), "check_history",
                    {"map": "proto", "events": evs, "agents": agents},
                    outcome(lambda: hist_mod.check_history(proto, [cd.Event(*e) for e in evs],
                                                           agents)))
    for s in ("constructor __proto__ toString", ["__proto__", "constructor"], "toString",
              "valueOf"):
        bk.case("story/proto/%r" % (s,), "parse_story", {"story": s, "map": "proto"},
                outcome(lambda: cd.parse_story(s, proto)))
    fixed = [("proto", "constructor __proto__", "hasOwnProperty __defineGetter__ "
              "__defineGetter__ propertyIsEnumerable propertyIsEnumerable"),
             ("proto", ["constructor", "toString"], "hasOwnProperty __proto__b"),
             ("proto2", "A B", [["constructor", "A"], ["__proto__", "A"], ["constructor", "D"]]),
             ("proto2", "A", [["constructor", "A"]]),
             ("proto3", ["__proto__", "B"], "b"),
             ("proto3", ["__proto__"], "toString toString"),
             ("digits", "1 A 0", "7 5 5 b 3"), ("digits", "10", [["3", "A"]]),
             ("digits", ["0", "1"], "5 5 o o 3"),
             ("digits_obj", "12 A 3", "4 007"), ("digits_obj", ["3", "12"], "007 10 b"),
             ("digits_regions", "A B", "b"), ("digits_beams", "AB", "9 2 2")]
    for j, (mname, story, hist) in enumerate(fixed):
        for agents in ("single", "multi"):
            cid = "fixed%02d/%s" % (j, agents[0])
            r = bk.validate(cid + "/p1", mname, story, hist, agents, False)
            if r and r.get("path"):
                bk.replay(cid + "/replay", mname, r["path"], hist, agents, story, False)
            bk.validate(cid + "/p1u", mname, story, hist, agents, True)
            bk.positions(cid + "/pos", mname, story, hist, agents, False)
            bk.filters(cid, mname, hist, agents)
            bk.all_problems(cid, mname, story, hist, agents, False, cases=(1, 2, 6))
    rng = Rng(seed)
    for i in range(count):
        agents = "single" if i % 2 == 0 else "multi"
        scheme = "js" if i % 4 < 2 else "digits"
        m = renamed(rng, random_map(rng, "tmp"), scheme, "n%s%03d" % (scheme[0], i))
        mname = bk.add_map(m)
        m = bk.objs[mname]
        hist = random_history(rng, m, agents, 6, rng.random() < 0.7)
        story = consistent_story(rng, m, hist, agents) or random_story(rng, m)
        story_in, hist_in = as_input(rng, m, story, hist)
        cid = "n%03d" % i
        unrep = rng.random() < 0.3
        r = bk.validate(cid + "/p1", mname, story_in, hist_in, agents, unrep)
        if r and r.get("path"):
            bk.replay(cid + "/replay", mname, r["path"], hist_in, agents, story_in, unrep)
        bk.positions(cid + "/pos", mname, story_in, hist_in, agents, unrep)
        bk.filters(cid, mname, hist_in, agents)
        bk.all_problems(cid, mname, story, hist, agents, unrep, cases=(rng.randint(1, 6),))
        bk.case(cid + "/to_string", "history_to_string", {"events": hist, "map": mname},
                outcome(lambda: hist_mod.history_to_string([cd.Event(*e) for e in hist], m)))
        tok = hist_mod.history_to_string([cd.Event(*e) for e in hist], m)
        bk.case(cid + "/parse", "parse_history", {"history": tok, "map": mname,
                                                  "check_names": True},
                outcome(lambda: [list(e) for e in cd.parse_history(tok, m)]))
    # compat="original" on the special maps (the original's builder gets beams as an object)
    special = [cid for cid, _d in special_map_dicts()]
    for i in range(60):
        mname = special[i % len(special)]
        m = bk.objs[mname]
        agents = "single" if i % 2 == 0 else "multi"
        hist = random_history(rng, m, agents, 5, rng.random() < 0.6)
        story = random_story(rng, m, 1, 4)
        bk.case("compat%02d" % i, "validate",
                {"map": mname, "story": story, "history": hist, "agents": agents,
                 "unreported_visits": False, "compat": "original"},
                outcome(lambda: res_dict(cd.validate(m, story, hist, agents, compat="original"))))
    return bk


# ---------------------------------------------------------------------- the files


def gen_maps() -> Book:
    bk = Book("maps", "Map.from_dict: canonical to_dict, G, adjacency, region graph, regions_of, "
                      "kinds; and MapError messages")

    def rec(cid: str, d: Any) -> None:
        def run():
            m = cd.Map.from_dict(d)
            out = m.to_dict()
            return {"to_dict": out, "edges": [list(e) for e in m.edges()],
                    "adjacency": m.adjacency(),
                    "region_graph_edges": [list(e) for e in m.region_graph_edges()],
                    "features": list(m.features), "sides": list(m.sides),
                    "sensors": list(m.sensors),
                    "regions_of": {f: list(v) for f, v in m.regions_of.items()},
                    "kinds": {n: m.kind(n) for n in list(m.features) + list(m.beams)
                              + list(m.regions)}}
        bk.case(cid, "map_from_dict", {"dict": d}, outcome(run))

    for name in cd.builtin_map_names():
        d = cd.builtin_map(name, geometry=False).to_dict()
        rec("builtin/" + name, d)
        g = {k: d[k] for k in ("name", "rooms", "beams", "occupancy", "edges")}
        rec("builtin_edges_only/" + name, g)
    base = {"name": "t", "rooms": ["A", "B"], "beams": {"b": ["bl", "br"]},
            "occupancy": ["o"], "regions": {"R1": ["A", "bl", "o"], "R2": ["B", "br", "o"]}}
    bad: List[Any] = [
        {"regions": {"R1": ["A", "bl", "o"], "R2": ["B", "br", "o", "bl"]}},
        {"regions": {"R1": ["A", "o"], "R2": ["B", "br", "o"]}},
        {"regions": {"R1": ["A", "bl", "zz"], "R2": ["B", "br"]}},
        {"regions": {"R1": ["A", "bl", "b"], "R2": ["B", "br"]}},
        {"regions": {"R1": [], "R2": ["B", "br", "bl"]}},
        {"regions": {"R1": ["A", "A", "bl"], "R2": ["B", "br"]}},
        {"regions": {"R1": "A", "R2": ["B", "br", "bl"]}},
        {"regions": {"A": ["A", "bl"], "R2": ["B", "br"]}},
        {"rooms": ["A", "B", "A"]},
        {"rooms": ["A", "B-1"]},
        {"rooms": ["A", ""]},
        {"rooms": ["A", 3]},
        {"rooms": ["A", "B\n"]},
        {"beams": {"b": ["bl"]}},
        {"beams": {"b": ["bl", "bl"]}},
        {"beams": {"b": ["b", "br"]}},
        {"beams": {"b o": ["bl", "br"]}},
        {"occupancy": ["o", "o2"]},
        {"occupancy": ["A"]},
        {"name": ""},
        {"name": 5},
        {"edges": [["A", "bl"], ["A", "o"]]},
        {"edges": [["A", "bl"], ["A", "o"], ["bl", "o"], ["B", "br"], ["B", "o"], ["br", "o"],
                   ["A", "B"]]},
        {"edges": [["A", "bl"], ["A", "o"], ["bl", "o"], ["B", "br"], ["B", "o"], ["br", "o"]]},
        {"edges": [["A", "zz"]]},
        {"edges": [["A", "A"]]},
        {"vertex_order": ["A", "B", "bl", "br"]},
        {"vertex_order": ["A", "B", "bl", "br", "o"]},
        {"regions": None, "edges": [["A", "bl"], ["A", "o"], ["bl", "o"], ["B", "br"],
                                    ["B", "o"], ["br", "o"]]},
        {"regions": None, "edges": [["A", "B"], ["A", "o"], ["B", "o"], ["bl", "A"],
                                    ["br", "B"]]},
        {"regions": None, "edges": None},
        {"regions": None, "edges": [["A", "zz"]]},
        {"regions": None, "edges": [["o", "o"]]},
        {"regions": [["A", "bl", "o"], ["B", "br", "o"]], "rooms": ["A", "B", "R1"]},
        {"regions": [["R1", "bl", "o"], ["B", "br", "o"]], "rooms": ["R1", "B"],
         "beams": {"R2": ["bl", "br"]}},
        {"regions": [["A", "bl"], ["B", "br"], ["o"]], "occupancy": ["o"],
         "title": "t", "source": "s", "provenance": {"x": [1, 2]}},
        {"rooms": "AB", "regions": [["A", "bl", "o"], ["B", "br", "o"]]},
        # beams / regions as [name, value] pairs, and malformed containers
        {"beams": [["b", ["bl", "br"]]]},
        {"beams": [["b", ["bl", "br"]]], "regions": [["R2", ["B", "br", "o"]],
                                                     ["R1", ["A", "bl", "o"]]]},
        {"beams": "b"},
        {"beams": 5},
        {"beams": [["b", ["bl", "br"], 1]]},
        {"beams": [["b"]]},
        {"beams": [[1, ["bl", "br"]]]},
        {"beams": [["b", "lr"]]},
        {"beams": {"b": "lr"}},
        {"beams": {"b": 3}},
        {"regions": [["R1", ["A", "bl", "o"]], ["B", "br", "o"]]},
        {"regions": [["R1", ["A", "bl", "o"]], ["R2", "B"]]},
        {"regions": [["R1", ["A", "bl", "o"]], ["R2", 7]]},
        {"regions": {"R1": ["A", "bl", "o"], "R2": 7}},
        {"regions": {"R1": ["A", "bl", ["o"]], "R2": ["B", "br"]}},
        {"regions": 7},
        {"regions": "AB"},
        {"regions": [], "beams": {}},
        {"regions": None, "beams": [["b", ["bl", "br"]]],
         "edges": [["A", "bl"], ["A", "o"], ["bl", "o"], ["B", "br"], ["B", "o"], ["br", "o"]]},
        {"regions": None, "beams": [["b", 3]], "edges": [["A", "o"]]},
    ]
    for i, patch in enumerate(bad):
        d = dict(base)
        d.update(patch)
        rec("variant/%02d" % i, d)
    for i, d in enumerate([[1, 2], "map", None, {"name": "x"}, {"name": "x", "rooms": []},
                           {"name": "x", "rooms": [], "beams": {}}]):
        rec("not_a_map/%d" % i, d)
    for cid, d in special_map_dicts():
        rec("names/" + cid, d)
        g = {k: d[k] for k in ("name", "rooms", "beams", "occupancy")}
        g["edges"] = cd.Map.from_dict(d).to_dict()["edges"]
        rec("names_edges_only/" + cid, g)
    rng = Rng(101)
    for i in range(40):
        m = random_map(rng, "rnd%02d" % i)
        d = m.to_dict()
        rec("random/%02d" % i, d)
        if i % 2 == 0:
            g = {k: d[k] for k in ("name", "rooms", "beams", "occupancy", "edges")}
            rec("random_edges_only/%02d" % i, g)
    return bk


def gen_inputs() -> Book:
    bk = Book("inputs", "parse_story, parse_history, history_to_string, check_history")
    star = bk.add_map(cd.builtin_map("star_fig2", geometry=False))
    multi = bk.add_map(cd.Map("names", ["Lab", "Office", "A"], {"door": ["in", "out"]},
                              ["o1", "hall"],
                              {"R1": ["Lab", "in", "o1"], "R2": ["Office", "A", "out", "hall"],
                               "R3": ["o1", "hall"]}))
    maps = {star: bk.objs[star], multi: bk.objs[multi]}

    def story(cid: str, s: Any, mname: Optional[str]) -> None:
        m = maps.get(mname) if mname else None
        args: Dict[str, Any] = {"story": s}
        if mname:
            args["map"] = mname
        bk.case(cid, "parse_story", args, outcome(lambda: cd.parse_story(s, m)))

    def history(cid: str, h: Any, mname: Optional[str], check: bool = True) -> None:
        m = maps.get(mname) if mname else None
        args: Dict[str, Any] = {"history": h, "check_names": check}
        if mname:
            args["map"] = mname
        bk.case(cid, "parse_history", args, outcome(
            lambda: [list(e) for e in cd.parse_history(h, m, check_names=check)]))

    stories = ["ACBAC", "A C B", "A,C,,B", "  ACB  ", "", "   ", "A\tB\nC", "R1,R2", "AXB",
               "Lab Office", "LabOffice", "Lab, A ,Office", ["A", "C"], [], ["A", ""],
               ["A", 3], ["A", None], 5, None, "éA", "A B", "A\x85B", "A\x1cB",
               "A B", "A﻿B", "'A'", "\"A\"", "A'\"B", "\\A", "A\x07",
               {"A": 1}, {}, {"A": "C"}, True, "A\U0001d400", "\U000e0001A", "A\ue000"]
    for i, s in enumerate(stories):
        story("story/%02d/nomap" % i, s, None)
        story("story/%02d/star" % i, s, star)
        story("story/%02d/names" % i, s, multi)
    hists = ["b1 o1 o1 b2 o2 o2", "b1,o1,o1", "o1+ o1-", "o1 o1+ o1", "o1- o1", "b1+", "b1-",
             "b1 zz", "", "  ", "o1+,o2+,o1-,o2-", "door hall hall o1", "in", "door-",
             [["b1", "A"], ["o1", "A"]], [["b1", "X"]], [["zz", "A"]], [["b1"]],
             [["b1", "A", "x"]], [["b1", 1]], [[1, "A"]], ["b1", "o1"], ["b1", ["o1", "A"]],
             [], 7, None, [None], "o1+ zz- b1 q", "é+", "+", "-", "++", "o1++",
             # {sensor, kind} items; a mapping or other non-list as the whole history
             [{"sensor": "b1", "kind": "A"}, {"sensor": "o1", "kind": "A"}],
             [{"kind": "A", "sensor": "b1"}], [{"sensor": "b1", "kind": "X"}], [{"sensor": "b1"}],
             [{"sensor": "b1", "kind": "A", "x": 1}], [{"sensor": 1, "kind": "A"}],
             [{"sensor": "zz", "kind": "A"}], [{"sensor": "b1", "kind": "D"}],
             [["b1", "A"], {"sensor": "o1", "kind": "A"}], ["b1", {"sensor": "o1", "kind": "A"}],
             {"b1": "A"}, {"sensor": "b1", "kind": "A"}, {}, True, 1.5,
             "\U0001d400", "o1+\ue000"]
    for i, h in enumerate(hists):
        history("history/%02d/nomap" % i, h, None)
        history("history/%02d/nomap_nocheck" % i, h, None, False)
        history("history/%02d/star" % i, h, star)
        history("history/%02d/star_nocheck" % i, h, star, False)
        history("history/%02d/names" % i, h, multi)
    evs = [[["o1", "A"], ["o1", "D"]], [["o1", "D"]], [["o1", "A"], ["o1", "A"]],
           [["b1", "A"], ["o2", "A"], ["o2", "D"], ["o2", "A"]], [["b1", "D"]],
           [["zz", "A"]], [["o1", "X"]], [["o1", "A"]], [["o1", "A"], ["b1", "A"], ["o1", "D"]],
           [["o1", "A"], ["o2", "A"], ["o1", "D"], ["o2", "D"]], []]
    for i, e in enumerate(evs):
        events = [cd.Event(*x) for x in e]
        for mname in (None, star):
            m = maps.get(mname) if mname else None
            args: Dict[str, Any] = {"events": e}
            if mname:
                args["map"] = mname
            bk.case("to_string/%02d/%s" % (i, mname or "nomap"), "history_to_string", args,
                    outcome(lambda: hist_mod.history_to_string(events, m)))
        for agents in ("single", "multi", "both"):
            bk.case("check/%02d/%s" % (i, agents), "check_history",
                    {"map": star, "events": e, "agents": agents},
                    outcome(lambda: hist_mod.check_history(maps[star], events, agents)))
    return bk


def _paper_maps(bk: Book) -> None:
    for name in cd.builtin_map_names():
        bk.add_map(cd.builtin_map(name, geometry=False))


def gen_paper() -> Book:
    bk = Book("paper", "every paper case (tests/fixtures/paper): Problem 1 (strict and "
                       "unreported visits), possible_positions, Problems 2-4")
    _paper_maps(bk)
    for fname in ("star", "icra"):
        with open(os.path.join(PAPER, fname + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        for c in data["cases"]:
            mname = c["map"]
            if mname not in bk.objs:
                continue
            agents = c.get("mode", "single")
            if agents not in ("single", "multi"):
                agents = "single"
            story, hist = c["story"], c["history"]
            cid = "%s/%s" % (fname, c["id"])
            for unrep in (False, True):
                tag = "/unrep" if unrep else ""
                r = bk.validate(cid + "/p1" + tag, mname, story, hist, agents, unrep)
                bk.positions(cid + "/pos" + tag, mname, story, hist, agents, unrep)
                if r and r.get("path"):
                    bk.replay(cid + "/replay" + tag, mname, r["path"], hist, agents, story, unrep)
                bk.all_problems(cid + tag, mname, story, hist, agents, unrep)
            bk.filters(cid, mname, hist, agents)
            other = "multi" if agents == "single" else "single"
            bk.validate(cid + "/p1_" + other, mname, story, hist, other, False)
    return bk


def gen_random(agents: str, seed: int, count: int) -> Book:
    bk = Book("random_" + agents, "seeded random %s-agent cases: validate (strict and "
              "unreported visits), possible_positions (story and filter), replay of every "
              "witness, malformed histories, input errors" % agents)
    rng = Rng(seed)
    i = 0
    while i < count:
        m = random_map(rng, "r%s%03d" % (agents[0], i))
        mname = bk.add_map(m)
        m = bk.objs[mname]
        kind = rng.below(10)
        if kind < 5:
            hist = random_history(rng, m, agents, 6, True)
            story = consistent_story(rng, m, hist, agents) or random_story(rng, m)
            if kind == 4:  # perturb
                if story and rng.random() < 0.5:
                    story = list(story)
                    story[rng.below(len(story))] = rng.choice(m.rooms)
                elif hist:
                    hist = hist[:-1]
        elif kind < 8:
            hist = random_history(rng, m, agents, 6, False)
            story = random_story(rng, m)
        elif kind == 8 and m.occupancy:
            hist = malformed_history(rng, m, agents)
            story = random_story(rng, m)
        else:
            hist = random_history(rng, m, agents, 4, True)
            story = random_story(rng, m) + (["Nowhere"] if rng.random() < 0.5 else [])
            if rng.random() < 0.5:
                hist = hist + [["zz", "A"]]
        story_in, hist_in = as_input(rng, m, story, hist)
        cid = "%s%03d" % (agents[0], i)
        unrep = rng.random() < 0.5
        r = bk.validate(cid + "/p1", mname, story_in, hist_in, agents, False)
        r2 = bk.validate(cid + "/p1u", mname, story_in, hist_in, agents, True) if unrep else None
        bk.positions(cid + "/pos", mname, story_in, hist_in, agents, unrep)
        if rng.random() < 0.5:
            bk.filters(cid, mname, hist_in, agents)
        for res, u in ((r, False), (r2, True)):
            if res and res.get("path"):
                bk.replay(cid + "/replay" + ("u" if u else ""), mname, res["path"], hist_in,
                          agents, story_in, u)
        i += 1
    return bk


def gen_problems(seed: int, count: int) -> Book:
    bk = Book("problems", "seeded random Problems 2-4 (all six interval cases, anchored and "
                          "free superstories, closest stories), both agent models")
    rng = Rng(seed)
    for i in range(count):
        agents = "single" if i % 2 == 0 else "multi"
        m = random_map(rng, "p%03d" % i)
        mname = bk.add_map(m)
        m = bk.objs[mname]
        explained = rng.random() < 0.7
        hist = random_history(rng, m, agents, 4, explained)
        story = random_story(rng, m, 1 if rng.random() < 0.9 else 0, 3)
        unrep = rng.random() < 0.3
        cid = "p%03d" % i
        cases = (1, 2, 3, 4, 5, 6) if i % 3 == 0 else (rng.randint(1, 6),)
        if story:
            for c in cases:
                bk.intervals("%s/p2c%d" % (cid, c), mname, story, hist, c, agents, unrep)
        bk.superstory(cid + "/p3a", mname, story, hist, True, agents, unrep)
        bk.superstory(cid + "/p3f", mname, story, hist, False, agents, unrep)
        bk.closest(cid + "/p4", mname, story, hist, agents, unrep)
    # argument errors
    star = bk.add_map(cd.builtin_map("star_fig2", geometry=False))
    m = bk.objs[star]
    for c in (0, 7, "2", None):
        bk.case("err/case_%r" % (c,), "validate_intervals",
                {"map": star, "story": "AC", "history": "b1", "case": c, "agents": "single",
                 "unreported_visits": False},
                outcome(lambda: res_dict(cd.validate_intervals(m, "AC", "b1", c))))
    bk.intervals("err/agents", star, "AC", "b1", 2, "many", False)
    bk.intervals("err/empty_story", star, "", "b1", 2, "single", False)
    bk.superstory("err/empty_anchored", star, "", "b1", True, "single", False)
    bk.superstory("ok/empty_free", star, "", "b1 o1 o1", False, "single", False)
    bk.closest("ok/empty_closest", star, "", "b2", "single", False)
    bk.closest("err/bad_room", star, "AZ", "b2", "single", False)
    bk.superstory("malformed", star, "A", "o1", True, "single", False)
    return bk


def _mutants(rng: Rng, path: List[dict], m: cd.Map) -> List[List[dict]]:
    out = []
    names = list(m.rooms) + list(m.regions) + list(m.occupancy) + list(m.sides) + ["nowhere"]
    for _ in range(4):
        p = [dict(s) for s in path]
        i = rng.below(len(p))
        k = rng.below(9)
        if k == 0:
            p[i]["time"] += rng.choice([-1, 1])
        elif k == 1:
            p[i]["story_index"] += rng.choice([-1, 1])
        elif k == 2:
            p[i]["position"] = rng.choice(names)
        elif k == 3:
            p[i]["sensor"] = rng.choice([None] + names)
        elif k == 4:
            p[i]["kind"] = rng.choice(["start", "visit", "move", "unreported", "cross", "enter",
                                       "exit", "jump"])
        elif k == 5:
            p.pop(i)
        elif k == 6 and len(p) > 1:
            j = rng.below(len(p))
            p[i], p[j] = p[j], p[i]
        elif k == 7:
            p[i]["event"] = rng.choice([None, 0, 1, 2])
        else:
            p.append(dict(p[-1]))
        out.append(p)
    return out


def gen_replay(seed: int, count: int) -> Book:
    bk = Book("replay", "replay() on mutated witnesses: InvalidPath messages (and the "
                        "mutants that still pass)")
    rng = Rng(seed)
    made = 0
    i = 0
    while made < count:
        agents = "single" if i % 2 == 0 else "multi"
        i += 1
        m = random_map(rng, "w%03d" % i)
        hist = random_history(rng, m, agents, 5, True)
        story = consistent_story(rng, m, hist, agents)
        if story is None:
            continue
        unrep = rng.random() < 0.3
        r = cd.validate(m, story, hist, agents, unreported_visits=unrep)
        if not r.consistent:
            continue
        mname = bk.add_map(m)
        path = [s.to_dict() for s in r.path]
        bk.replay("w%03d/ok" % made, mname, path, hist, agents, story, unrep)
        for j, p in enumerate(_mutants(rng, path, bk.objs[mname])):
            bk.replay("w%03d/mut%d" % (made, j), mname, p, hist, agents,
                      story if rng.random() < 0.8 else None, unrep)
        made += 1
    star = bk.add_map(cd.builtin_map("star_fig2", geometry=False))
    bk.replay("empty", star, [], "", "single", "A", False)
    return bk


def gen_compat() -> Book:
    """compat="original" on the maps the demo offers (star_fig2, icra_fig1), the bug
    reproductions, crashes, and the golden random maps (large HashSets, treeified bins)."""
    bk = Book("compat", "compat=\"original\" (validate_compat and validate(..., "
                        "compat='original')): verdicts, getAgentStory paths, Java crashes; "
                        "plus javahash scripts")
    stars = [bk.add_map(cd.builtin_map(n, geometry=False)) for n in ("star_fig2", "icra_fig1")]

    def vc(cid: str, mname: str, d: Dict[str, Any], story: Any, events: Any, agents: str) -> None:
        def run():
            ok, path = orig.validate_compat(d, story, events, agents)
            return {"consistent": ok, "path_string": path}
        bk.case(cid, "validate_compat", {"map": mname, "story": story, "events": events,
                                         "agents": agents}, outcome(run))

    inputs: List[tuple] = []
    for fname, agents in (("star_random_single", "single"), ("star_random_multi", "multi")):
        with open(os.path.join(GOLDEN, fname + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        seen = set()
        for c in data["cases"]:
            key = json.dumps([c["story"], c["history"]])
            if key in seen:
                continue
            seen.add(key)
            inputs.append((c["id"].split("__")[0], c["story"], c["history"], agents))
    with open(os.path.join(GOLDEN, "builtin.json"), encoding="utf-8") as f:
        data = json.load(f)
    for c in data["cases"]:
        if c.get("map") == "star_fig2" and c.get("start") is None and "story" in c:
            agents = "multi" if c["op"] == "validateAgentStoryMulti" else "single"
            inputs.append((c["id"], c["story"], c.get("history") or [], agents))
    extra = [(["Z"], [], "single"), (["A", "Z"], [], "multi"), ([], [["b1", "A"]], "multi"),
             (["A"], [["zz", "A"]], "single"), (["A"], [["b1", "X"]], "single"),
             (["A", "C"], [["b1", "D"]], "multi"), (["B", "A"], [["b2", "A"]], "single"),
             (["C", "B", "A"], [["o1", "A"], ["o1", "D"], ["b2", "A"], ["b2", "A"]], "single")]
    for j, (s, h, a) in enumerate(extra):
        inputs.append(("extra%d" % j, s, h, a))
    for mname in stars:
        d = bk.maps[mname]
        for cid, s, h, a in inputs:
            vc("%s/%s/%s" % (mname, cid, a), mname, d, s, h, a)
    # through validate(..., compat="original") with the string forms
    m = bk.objs["star_fig2"]
    for j, (s, h, a) in enumerate([("ACBAC", "b1 o1 o1 o2 o2 b2", "single"),
                                   ("ACBAC", "b1 o1 o1 b2 o2 o2", "single"),
                                   ("ACBAC", "b1 o1 o2 b2 o2 o1", "multi"),
                                   ("AXC", "b1", "single"), ("A", "b1 zz", "single"),
                                   ("A", "o1-", "single"), ("", "", "single")]):
        bk.case("validate_original/%d" % j, "validate",
                {"map": "star_fig2", "story": s, "history": h, "agents": a,
                 "unreported_visits": False, "compat": "original"},
                outcome(lambda: res_dict(cd.validate(m, s, h, a, compat="original"))))
    bk.case("validate_original/unrep", "validate",
            {"map": "star_fig2", "story": "A", "history": "", "agents": "single",
             "unreported_visits": True, "compat": "original"},
            outcome(lambda: res_dict(cd.validate(m, "A", "", compat="original",
                                                 unreported_visits=True))))
    # golden random maps (more vertices: treeified HashSet bins)
    with open(os.path.join(GOLDEN, "random_maps.json"), encoding="utf-8") as f:
        data = json.load(f)
    gmaps = {d["name"]: d for d in data["maps"]}
    for c in data["cases"]:
        if c["op"] not in ("validateAgentStory", "validateAgentStoryMulti"):
            continue
        d = {k: v for k, v in gmaps[c["map"]].items() if k != "source"}
        bk.maps["golden_" + c["map"]] = d
        agents = "multi" if c["op"] == "validateAgentStoryMulti" else "single"
        vc("golden/%s" % c["id"], "golden_" + c["map"], d, c["story"], c["history"], agents)
    # javahash: the golden probe scripts, with a checksum of the order after each op
    with open(os.path.join(GOLDEN, "javahash.json"), encoding="utf-8") as f:
        data = json.load(f)
    for sc in data["scripts"]:
        bk.case("javahash/" + sc["id"], "javahash", {"kind": sc["kind"], "ops": sc["ops"]},
                {"return": javahash_run(sc["kind"], sc["ops"])})
    return bk


class _Obj:
    """An object key with the canonical identity hash (Java object without hashCode)."""

    def __init__(self, name: str) -> None:
        self.name = name


def fnv1a(text: str) -> str:
    h = 0x811C9DC5
    for ch in text.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return "%08x" % h


def javahash_run(kind: str, ops: List[str]) -> Dict[str, Any]:
    """Apply ``+K`` / ``-K`` ops to a JavaHashSet; FNV-1a of the order after each op."""
    s = jh.JavaHashSet()
    objs: Dict[str, Any] = {}

    def key(tok: str) -> Any:
        if tok == "null":
            return None
        if kind == "int":
            return int(tok)
        if kind == "str":
            return tok
        if tok not in objs:
            objs[tok] = _Obj(tok)
        return objs[tok]

    def name(k: Any) -> str:
        return "null" if k is None else (k.name if isinstance(k, _Obj) else str(k))

    sums = []
    for op in ops:
        k = key(op[1:])
        (s.add if op[0] == "+" else s.remove)(k)
        sums.append(fnv1a(" ".join(name(x) for x in s)))
    return {"fnv": sums, "final": [name(x) for x in s], "capacity": s.table_capacity()}


def build_all() -> Dict[str, str]:
    """{file name: text} for every parity fixture."""
    books = [gen_maps(), gen_inputs(), gen_paper(), gen_random("single", 20240611, 260),
             gen_random("multi", 20240612, 260), gen_problems(20240613, 90),
             gen_replay(20240614, 70), gen_compat(), gen_names(20240615, 80)]
    out = {}
    for bk in books:
        text = bk.render()
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise SystemExit("%s.json is %d bytes (> %d)" % (bk.name, len(text), MAX_BYTES))
        out[bk.name + ".json"] = text
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only compare with the files on disk")
    args = ap.parse_args(argv)
    files = build_all()
    stale = []
    for name, text in files.items():
        path = os.path.join(OUT_DIR, name)
        old = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = f.read()
        if old != text:
            stale.append(name)
            if not args.check:
                os.makedirs(OUT_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
    extra = sorted(set(n for n in os.listdir(OUT_DIR) if n.endswith(".json")) - set(files)) \
        if os.path.isdir(OUT_DIR) else []
    for name, text in files.items():
        print("%-20s %6d cases %9d bytes%s" % (name, len(json.loads(text)["cases"]),
                                               len(text.encode("utf-8")),
                                               "  (changed)" if name in stale else ""))
    if extra:
        print("unexpected files in %s: %s" % (OUT_DIR, ", ".join(extra)))
    if args.check and (stale or extra):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
