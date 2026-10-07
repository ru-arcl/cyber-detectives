#!/usr/bin/env python3
"""Add a per-case "crosscheck" block (paper vs fixture vs independent oracle vs original) to the
golden paper_cases.json written by make_golden.py (called by regen_golden.sh; IN may equal OUT).

    python3 tools/reference/paper_crosscheck.py REPO IN OUT

make_golden.py calls crosscheck() directly (--paper-crosscheck REPO), so that summary.json
records the final file size. docs/notes/phase1-crosscheck.md (Appendix C) explains the block."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oracle  # noqa: E402
from oracle import Model, load_regions, consistent, check_path, single_history_well_formed  # noqa: E402

REPO = IN = OUT = None
models = fx = pcase = None


def _init(repo):
    global REPO, models, fx, pcase
    REPO = repo
    oracle.REPO = repo
    regs, maps_ = load_regions()
    models = {k: Model(maps_[k], regs[k]) for k in regs}
    fx = {f: json.load(open(os.path.join(REPO, "tests/fixtures/paper/%s.json" % f))) for f in ("star", "icra")}
    pcase = {(f, c["id"]): c for f in fx for c in fx[f]["cases"]}


def crosscheck(repo, inp, out):
    """Rewrite the golden file inp (as written by make_golden.py) to out with the blocks."""
    global IN, OUT
    _init(repo)
    IN, OUT = inp, out
    main()

END_NOTE = ("ORIGINAL deviates: it accepts once the story is used up, even if x then leaves p_n "
            "(Algorithms.java final-phase test l == n). STAR Alg. 3 l.21 needs the copy of p_n in G_{m+1} and "
            "ICRA SII-A says 'x starts from p1 and ends in pn'. The fixture is right.")
B5_NOTE = ("ORIGINAL (and paper Alg. 3 as printed) accept a history no single agent can produce "
           "(o1 still active while o2 activates; STAR S2.2). The fixture answer (false) is right "
           "by the problem definition. Inventory B5.")


def norm_edges(es):
    return sorted(sorted(e) for e in es)


def sub_check(c, ret):
    ref = c["paper_case"].split("#", 1)[1].split("/")
    cid = c["id"]
    V, E = set(ret["vertices"]), norm_edges(ret["edges"])
    if cid.startswith("star_fig4_"):
        exp = pcase[("star", "star_eq1_eq2_single")]["expected"]["subgraphs_fig4"][ref[-1]]
        ok = V == set(exp["V"]) and E == norm_edges(exp["E"])
        note = "compared with the drawn Fig. 4 subgraph"
        if ref[-1] == "G3":
            note += ("; the original drops the unreachable goal b2r (Alg. 2 l.11 would add it as an isolated "
                     "vertex), which matches the drawing")
        return {"paper_V": exp["V"], "paper_E": exp["E"], "match": ok, "note": note}
    if cid.startswith("star_fig6a"):
        exp = pcase[("star", "star_eq1_eq3_multi")]["expected"]["fig6a_G0_4_before_cliquification"]
        return {"paper_V": exp["V"], "paper_E": exp["E"], "match": V == set(exp["V"]) and E == norm_edges(exp["E"]),
                "note": "Alg. 4 line 2 (before clique-ification) = original getReachableSubgraph"}
    if cid.startswith("star_fig7_"):
        exp = pcase[("star", "star_eq1_eq3_multi")]["expected"]["composite_fig7"]["subgraphs"][ref[-1]]
        O = set(c["occupancy_active"])
        V2 = V - O
        E2 = [e for e in E if not (set(e) & O)]
        ok = V2 == set(exp["V_algorithm4"]) and E2 == norm_edges(exp["E_algorithm4"])
        d = {"paper_V_algorithm4": exp["V_algorithm4"], "paper_E_algorithm4": exp["E_algorithm4"],
             "original_minus_active_occupancy_V": sorted(V2), "match_after_removing_active_occupancy": ok,
             "note": ("the original keeps each active o and its edges (Alg. 4 l.5 removes o); with those removed "
                      "the result equals literal Alg. 4")}
        if ref[-1] == "G0_4":
            drawn = pcase[("star", "star_eq1_eq3_multi")]["expected"]["fig6b_G0_4_after_cliquification"]
            d["matches_fig6b_as_drawn"] = E2 == norm_edges(drawn["E_drawn_in_paper"])
            d["note"] += "; Fig. 6(b) as drawn lacks b2l-b2r (paper erratum), the original has it"
        return d
    if cid.startswith("icra_G"):
        return None  # handled jointly (union over start vertices) below
    raise ValueError(cid)


def main():
    raw = open(IN, encoding="ascii").read()
    d = json.loads(raw, object_pairs_hook=collections.OrderedDict)
    header = d["header"]
    header["crosscheck"] = ("each case carries a 'crosscheck' block: the paper's claim (when the paper states one), "
                            "the independently verified fixture value, a third independent region-model oracle "
                            "(docs/notes/phase1-crosscheck.md), and whether the original agrees. 'expected' is "
                            "still exactly what the original does.")
    # ICRA G_j: union over start vertices of the original's subgraphs that reach a goal
    groups = collections.OrderedDict()
    for c in d["cases"]:
        if c["id"].startswith("icra_G"):
            groups.setdefault(c["paper_case"], []).append(c)
    gres = {}
    for ref, cs in groups.items():
        hkey, j = ref.split("/")[-2], int(ref.split("=")[-1])
        exp = [g for g in fx["icra"]["figures"]["subgraphs_G_j"][hkey] if g["j"] == j][0]
        V, E, used = set(), set(), []
        for c in cs:
            r = c["expected"]["return"]
            if c["goals"] and not (set(r["vertices"]) & set(c["goals"])):
                continue
            used.append(c["s"])
            V |= set(r["vertices"])
            E |= set(tuple(sorted(e)) for e in r["edges"])
        ok = V == set(exp["vertices"]) and sorted(E) == [tuple(x) for x in norm_edges(exp["edges"])]
        res = {"paper_V": exp["vertices"], "paper_E": exp["edges"], "union_of_starts_reaching_a_goal": used,
               "union_V": sorted(V), "union_E": [list(e) for e in sorted(E)], "match": ok,
               "note": "ICRA draws one subgraph per interval with every possible start; the original builds one per start"}
        if hkey == "b1 b3 o2 o2 b4" and j in (1, 2):
            fig = fx["icra"]["figures"]["fig4a" if j == 1 else "fig4b"]
            res["matches_fig4%s" % ("a" if j == 1 else "b")] = (V == set(fig["vertices"]) and sorted(E) == [tuple(x) for x in norm_edges(fig["edges"])])
        gres[ref] = res

    rows = []
    for c in d["cases"]:
        e = c["expected"]
        cc = collections.OrderedDict()
        if c["op"] in ("validateAgentStory", "validateAgentStoryMulti", "getAgentStory", "getAgentStoryStatuses"):
            f, pid = c["paper_case"].split("/")[-1].split(".json#")
            pc = pcase[(f, pid)]
            M = models[c["map"]]
            pclaim = pc["expected"].get("paper_claims", {}).get("consistent")
            cc["paper_states"] = pclaim
            cc["fixture_consistent"] = pc["expected"]["consistent"]
            cc["oracle_consistent"] = consistent(M, c["story"], c["history"], c["mode"], "strict", "strict")
            cc["oracle_consistent_if_story_may_end_before_tf"] = consistent(M, c["story"], c["history"], c["mode"], "anywhere", "strict")
            if c["mode"] == "single":
                cc["history_valid_for_single_agent"] = single_history_well_formed(M, c["history"])
            if c["op"].startswith("validate"):
                cc["original"] = e.get("consistent")
                cc["agrees"] = e.get("consistent") == pc["expected"]["consistent"]
            elif c["op"] == "getAgentStory":
                if e["exception"]:
                    cc["original"] = "crash %s @ %s" % (e["exception"]["class"].split(".")[-1], e["exception"]["origin_frame"].split("(")[-1].rstrip(")"))
                    cc["agrees"] = pc["expected"]["consistent"] is False
                    cc["note"] = "inventory B4: getAgentStory throws instead of returning null on an inconsistent input"
                else:
                    ok, strict, why = check_path(M, c["story"], c["history"], e["return"])
                    cc["original"] = e["return"]
                    cc["path_valid_walk"] = ok
                    cc["path_ends_in_p_n"] = strict
                    cc["path_check"] = why
                    cc["agrees"] = bool(ok and strict and pc["expected"]["consistent"])
            else:
                cc["original"] = "crash" if e["exception"] else "returned"
                cc["agrees"] = (e["exception"] is not None) == (pc["expected"]["consistent"] is False)
                if e["exception"]:
                    cc["note"] = "inventory B4"
            if not cc["agrees"]:
                if cc.get("history_valid_for_single_agent") is False:
                    cc["who_is_wrong"] = B5_NOTE
                elif cc["oracle_consistent_if_story_may_end_before_tf"] and not cc["fixture_consistent"]:
                    cc["who_is_wrong"] = END_NOTE
            assert cc["oracle_consistent"] == cc["fixture_consistent"], c["id"]
        else:
            r = sub_check(c, e["return"])
            if r is None:
                r = gres[c["paper_case"]]
            cc.update(r)
        c["crosscheck"] = cc
        rows.append(c)
    import make_golden  # dump_file (imported here: make_golden imports this module)
    make_golden.dump_file(OUT, header, d["maps"], d["cases"])


if __name__ == "__main__":
    crosscheck(*sys.argv[1:4])
