#!/usr/bin/env python3
"""Independent region-model oracle (no graph G, no DP over subgraphs), used by
paper_crosscheck.py for the "crosscheck" block of tests/fixtures/golden/paper_cases.json
(docs/notes/phase1-crosscheck.md, Appendix B).

Places: rooms, free components R*, occupancy regions. BFS over (place, story idx, history idx).
Options: mode single|multi, end strict (inside p_n at t_f) | anywhere, visits strict | loose
(loose = entering a room may go unreported).
Also validates the original's getAgentStory path strings (A[b1u]C[o1]...).
"""
import json
import os
import re
from collections import deque

# repository root: $REPO, else two levels above this file
REPO = os.environ.get("REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_regions():
    star = json.load(open(os.path.join(REPO, "tests/fixtures/paper/star.json")))
    icra = json.load(open(os.path.join(REPO, "tests/fixtures/paper/icra.json")))
    regs = {
        "star_fig2": star["maps"][0]["expected"]["free_components_fig2"],
        "icra_fig1": icra["figures"]["icra_fig1_free_components"]["regions"],
        "icra_fig2": icra["figures"]["fig3_free_components"]["icra_fig2"],
    }
    maps = {m["name"]: m for m in star["maps"] + icra["maps"]}
    return regs, maps


class Model:
    def __init__(self, m, regions):
        self.rooms = set(m["rooms"])
        self.occ = set(m["occupancy"])
        self.beams = {b: tuple(s) for b, s in m["beams"].items()}
        self.side_beam = {s: b for b, ss in self.beams.items() for s in ss}
        self.regions = {r: set(v) for r, v in regions.items()}
        self.comps_of = {}
        for r, vs in self.regions.items():
            for v in vs:
                self.comps_of.setdefault(v, set()).add(r)
        # consistency with the map's clique edges
        E = set()
        for vs in self.regions.values():
            vs = sorted(vs)
            for i in range(len(vs)):
                for j in range(i + 1, len(vs)):
                    E.add((vs[i], vs[j]))
        ME = set(tuple(sorted(e)) for e in m["edges"])
        assert E == ME, (m["name"], E ^ ME)
        for s in self.side_beam:
            assert len(self.comps_of[s]) == 1, s

    def comp(self, side):
        return next(iter(self.comps_of[side]))

    def other(self, side):
        a, b = self.beams[self.side_beam[side]]
        return b if side == a else a


def consistent(M, story, hist, mode="single", end="strict", visits="strict"):
    n, m = len(story), len(hist)
    if n == 0:
        return None
    start = (("room", story[0]), 1, 0)
    seen = {start}
    dq = deque([start])

    def active_after(h):
        act = set()
        for sn, ev in hist[:h]:
            if sn in M.occ:
                if ev == "A":
                    if sn in act:
                        return None
                    act.add(sn)
                else:
                    if sn not in act:
                        return None
                    act.discard(sn)
        return act

    def push(st):
        if st not in seen:
            seen.add(st)
            dq.append(st)

    while dq:
        (kind, p), k, h = st = dq.popleft()
        if k == n and h == m and (end == "anywhere" or (kind == "room" and p == story[-1])):
            return True
        # free moves within the current phase
        if kind == "room":
            for c in M.comps_of.get(p, ()):
                push((("comp", c), k, h))
        if kind == "comp":
            for v in M.regions[p]:
                if v in M.rooms:
                    if k < n and story[k] == v:
                        push((("room", v), k + 1, h))
                    if visits == "loose":
                        push((("room", v), k, h))
            if mode == "multi":
                act = active_after(h)
                if act is None:
                    continue
                for v in M.regions[p]:
                    if v in act:
                        push((("occ", v), k, h))
        if kind == "occ" and mode == "multi":
            act = active_after(h)
            if act is not None and p in act:
                for c in M.comps_of[p]:
                    push((("comp", c), k, h))
        # the next recording
        if h < m:
            sn, ev = hist[h]
            if mode == "single":
                if sn in M.beams and kind == "comp":
                    for s in M.beams[sn]:
                        if M.comp(s) == p:
                            push((("comp", M.comp(M.other(s))), k, h + 1))
                elif sn in M.occ and ev == "A" and kind == "comp" and sn in M.regions[p]:
                    push((("occ", sn), k, h + 1))
                elif sn in M.occ and ev == "D" and kind == "occ" and p == sn:
                    for c in M.comps_of[sn]:
                        push((("comp", c), k, h + 1))
            else:
                if active_after(h + 1) is None:
                    continue
                if sn in M.beams:
                    push(((kind, p), k, h + 1))  # someone else crossed
                    if kind == "comp":
                        for s in M.beams[sn]:
                            if M.comp(s) == p:
                                push((("comp", M.comp(M.other(s))), k, h + 1))
                elif ev == "D":
                    if not (kind == "occ" and p == sn):
                        push(((kind, p), k, h + 1))
                else:
                    push(((kind, p), k, h + 1))
    return False


def check_path(M, story, hist, path):
    """Validate an original getAgentStory string. Returns (valid_end_anywhere, valid_strict_end, reason)."""
    toks = re.findall(r"\[([^\]]+)\]|([A-Z])", path)
    items = [("x", a) if a else ("r", b) for a, b in toks]
    rooms = [v for t, v in items if t == "r"]
    if rooms != list(story):
        return False, False, "room sequence %s != story" % rooms
    recs = [(sn, ev) for sn, ev in hist if not (sn in M.occ and ev == "D")]
    xs = [v for t, v in items if t == "x"]
    if len(xs) != len(recs):
        return False, False, "%d crossings for %d recordings" % (len(xs), len(recs))
    # single-agent pairing must hold for a path to make sense
    cur = None
    ri = 0
    for idx, (t, v) in enumerate(items):
        if t == "r":
            if cur is not None and not (M.comps_of[v] & cur):
                return False, False, "room %s not reachable from components %s" % (v, sorted(cur))
            cur = set(M.comps_of[v])
        else:
            sn, ev = recs[ri]
            ri += 1
            if v in M.side_beam:
                if M.side_beam[v] != sn:
                    return False, False, "crossing [%s] but recording %s" % (v, sn)
                if cur is not None and M.comp(v) not in cur:
                    return False, False, "beam side %s not in components %s" % (v, sorted(cur))
                cur = {M.comp(M.other(v))}
            else:
                if v != sn:
                    return False, False, "[%s] but recording %s" % (v, sn)
                if cur is not None and not (M.comps_of[v] & cur):
                    return False, False, "occupancy %s not adjacent to %s" % (v, sorted(cur))
                cur = set(M.comps_of[v])
    strict = items[-1][0] == "r"
    return True, strict, "ok" if strict else "ends outside p_n (after %s)" % items[-1][1]


def single_history_well_formed(M, hist):
    i = 0
    while i < len(hist):
        sn, ev = hist[i]
        if sn in M.occ:
            if ev != "A" or i + 1 >= len(hist) or hist[i + 1] != [sn, "D"]:
                return False
            i += 2
        else:
            i += 1
    return True


if __name__ == "__main__":
    regs, maps = load_regions()
    models = {k: Model(maps[k], regs[k]) for k in regs}
    for f in ("star", "icra"):
        fx = json.load(open(os.path.join(REPO, "tests/fixtures/paper/%s.json" % f)))
        for c in fx["cases"]:
            if c["map"] not in models:
                continue
            M = models[c["map"]]
            r = {(e, v): consistent(M, c["story"], c["history"], c["mode"], e, v)
                 for e in ("strict", "anywhere") for v in ("strict", "loose")}
            print(c["id"], c["mode"], "fixture=", c["expected"]["consistent"],
                  "SS=%s SL=%s AS=%s AL=%s" % (r[("strict", "strict")], r[("strict", "loose")],
                                               r[("anywhere", "strict")], r[("anywhere", "loose")]),
                  "wellformed_single=", single_history_well_formed(M, c["history"]))
