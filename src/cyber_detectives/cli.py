"""Command-line interface: ``python -m cyber_detectives`` / ``cyber-detectives``.

Subcommands::

    validate    --map M --story S --history H [--multi] [--compat original]
                [--unreported-visits] [--json]                       Problem 1
    intervals   --map M --story S --history H --case N [--multi]
                [--unreported-visits] [--json]                       Problem 2
    superstory  --map M --story S --history H [--free] [--multi]
                [--unreported-visits] [--json]                       Problem 3
    closest     --map M --story S --history H [--multi]
                [--unreported-visits] [--json]                       Problem 4
    maps        [NAME] [--json]          list builtin maps, or print one as JSON
    view        --map M [--multi] [--dark] [--story S] [--save PNG]
                                         live matplotlib viewer (needs matplotlib)

``--map`` takes a builtin map name or a path to a map JSON file.  Exit status: 0 = consistent
(or success), 1 = inconsistent / no solution, 2 = bad input, 3 = not implemented,
4 = the original Java code (``--compat original``) crashed on this input; ``view`` exits 3
when matplotlib is not installed or (without ``--save``) has no backend that can open a
window.  ``view --save FILE`` writes exactly FILE: the format comes from its extension, PNG
if it has none.

Text output of ``validate`` and the Problem 2-4 commands: the first line is ``consistent`` or
``inconsistent`` (Problems 3/4: ``consistent`` when a consistent story was found, else the
single line ``no solution``), then the ``key: value`` lines that apply: ``case`` (Problem 2),
``story`` (the answer ``p'``), ``inserted`` (its rooms that are not in the given story, with
0-based positions in ``p'``), ``edits`` (count and operations, 0-based positions in the given
story), ``path`` (witness) or ``reason``.  ``--json`` prints the complete result instead.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any, List, Optional

from . import __version__
from .engine import validate
from .history import InputError
from .maps import Map, MapError, builtin_map, builtin_map_names, load_map

EXIT_OK, EXIT_NO, EXIT_INPUT, EXIT_NOTIMPL, EXIT_CRASH = 0, 1, 2, 3, 4


def _get_map(spec: str, what: str = "--map") -> Map:
    """The builtin map or map JSON file named by ``spec`` (``what`` names it in errors)."""
    if spec in builtin_map_names():
        return builtin_map(spec)
    if os.path.exists(spec):
        try:
            return load_map(spec)
        except OSError as e:  # a directory, no permission, ...: bad input, not "inconsistent"
            raise InputError("cannot read %s %r: %s" % (what, spec, e.strerror or e)) from e
    raise InputError("%s %r is neither a builtin map (%s) nor an existing file"
                     % (what, spec, ", ".join(builtin_map_names())))


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--map", required=True, help="builtin map name or path to a map JSON file")
    p.add_argument("--story", required=True,
                   help='rooms in visit order: "ACBAC" or "A C B A C"')
    p.add_argument("--history", required=True,
                   help='recordings: "b1 o1 o1 b2" (an occupancy name toggles), or o1+ / o1-')
    p.add_argument("--unreported-visits", action="store_true",
                   help="allow unreported room entries (ICRA's loose reading)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def _add_multi(p: argparse.ArgumentParser) -> None:
    p.add_argument("--multi", action="store_true",
                   help="other agents may be present (STAR section 5)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cyber-detectives",
        description="Validate an agent's story against beam-detector / occupancy-sensor "
                    "recordings (Yu & LaValle, WAFR 2010 / ICRA 2011).")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p = sub.add_parser("validate", help="Problem 1: is the story consistent with the history?")
    _add_common(p)
    _add_multi(p)
    p.add_argument("--compat", choices=["original"],
                   help="run the port of the original Java code (bugs included)")

    p = sub.add_parser("intervals", help="Problem 2: story and sensors over different intervals")
    _add_common(p)
    p.add_argument("--case", type=int, required=True, choices=range(1, 7), metavar="1..6",
                   help="interval case of ICRA section III-B")
    _add_multi(p)

    p = sub.add_parser("superstory", help="Problem 3: shortest consistent super-story")
    _add_common(p)
    _add_multi(p)
    p.add_argument("--free", action="store_true",
                   help="forgotten visits may also precede p_1 and follow p_n (ICRA section "
                        "V-A reading; the story may then be empty); default: the answer "
                        "starts with p_1 and ends with p_n")

    p = sub.add_parser("closest", help="Problem 4: consistent story with fewest edits")
    _add_common(p)
    _add_multi(p)

    p = sub.add_parser("maps", help="list builtin maps, or print one as JSON")
    p.add_argument("name", nargs="?", help="map to print")
    p.add_argument("--json", action="store_true", help="list as JSON")

    p = sub.add_parser("view", help="live viewer: walk x with the mouse, test stories "
                                    "(needs matplotlib)")
    p.add_argument("--map", required=True, help="builtin map with a drawing (star_fig2, "
                   "icra_fig1, icra_fig2) or a map JSON file with geometry")
    p.add_argument("--story", default="", help="claimed story to start with")
    _add_multi(p)
    p.add_argument("--dark", action="store_true", help="dark theme (default: light)")
    p.add_argument("--save", metavar="PNG", help="write the first frame to this file (PNG "
                   "unless the extension says otherwise: .svg, .pdf, ...); no window")
    return ap


def _to_jsonable(x: Any) -> Any:
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        if hasattr(x, "to_dict"):
            return _to_jsonable(x.to_dict())
        return {f.name: _to_jsonable(getattr(x, f.name)) for f in dataclasses.fields(x)
                if not f.name.startswith("_")}
    if hasattr(x, "to_dict"):
        return _to_jsonable(x.to_dict())
    if isinstance(x, tuple) and hasattr(x, "_asdict"):  # namedtuple
        return _to_jsonable(x._asdict())
    if isinstance(x, dict):
        return {str(k): _to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_to_jsonable(v) for v in x]
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if hasattr(x, "__dict__"):
        return {k: _to_jsonable(v) for k, v in vars(x).items() if not k.startswith("_")}
    return str(x)


def _cmd_validate(a: argparse.Namespace, out) -> int:
    m = _get_map(a.map)
    agents = "multi" if a.multi else "single"
    try:
        r = validate(m, a.story, a.history, agents=agents, compat=a.compat,
                     unreported_visits=a.unreported_visits)
    except Exception as e:  # the original's crashes (compat.original.JavaException)
        if a.compat and type(e).__module__.startswith("cyber_detectives.compat"):
            if a.json:
                json.dump({"map": m.name, "consistent": None, "compat": a.compat,
                           "exception": {"class": type(e).__name__, "message": str(e)}},
                          out, indent=1)
                out.write("\n")
            else:
                out.write("the original code throws %s: %s\n" % (type(e).__name__, e))
            return EXIT_CRASH
        raise
    if a.json:
        d = r.to_dict()
        d["map"] = m.name
        json.dump(d, out, indent=1)
        out.write("\n")
    elif r.consistent:
        ps = r.path_string()
        out.write("consistent\n")
        if ps is not None:
            out.write("path: %s\n" % ps)
    else:
        out.write("inconsistent\n")
        if r.reason:
            out.write("reason: %s\n" % r.reason)
    return EXIT_OK if r.consistent else EXIT_NO


def _cmd_problem(a: argparse.Namespace, out) -> int:
    from . import problems

    m = _get_map(a.map)
    kw = {"unreported_visits": a.unreported_visits,
          "agents": "multi" if a.multi else "single"}
    if a.command == "intervals":
        res = problems.validate_intervals(m, a.story, a.history, case=a.case, **kw)
    elif a.command == "superstory":
        res = problems.shortest_superstory(m, a.story, a.history, anchored=not a.free, **kw)
    else:
        res = problems.closest_story(m, a.story, a.history, **kw)
    ok = res is not None and bool(res.consistent)
    if a.json:
        d = _to_jsonable(res)
        if isinstance(d, dict):
            d["map"] = m.name
        json.dump(d, out, indent=1)
        out.write("\n")
        return EXIT_OK if ok else EXIT_NO
    lines = ["consistent" if ok else "inconsistent"]
    if a.command == "intervals":
        lines.append("case: %d (%s)" % (a.case, problems.INTERVAL_CASES[a.case]))
        if not ok and res is not None and res.reason:
            lines.append("reason: %s" % res.reason)
    elif res is None:  # Problems 3/4: no story to show
        lines = ["no solution"]
    else:
        lines.append("story: %s" % " ".join(res.story))
        if a.command == "superstory":
            if res.inserted:
                lines.append("inserted: %s" % ", ".join(
                    "%s at %d" % (res.story[i], i) for i in res.inserted))
        else:
            lines.append("edits: %d" % res.edits + (
                " (%s)" % "; ".join(map(str, res.operations)) if res.operations else ""))
    ps = res.path_string() if ok else None
    if ps is not None:
        lines.append("path: %s" % ps)
    out.write("".join(line + "\n" for line in lines))
    return EXIT_OK if ok else EXIT_NO


def _cmd_maps(a: argparse.Namespace, out) -> int:
    if a.name:
        m = _get_map(a.name, "map")
        json.dump(m.to_dict(), out, indent=1)
        out.write("\n")
        return EXIT_OK
    rows = []
    for name in builtin_map_names():
        m = builtin_map(name, geometry=False)
        rows.append({"name": name, "title": m.title, "rooms": list(m.rooms),
                     "beams": list(m.beams), "occupancy": list(m.occupancy),
                     "regions": len(m.regions)})
    if a.json:
        json.dump(rows, out, indent=1)
        out.write("\n")
    else:
        for r in rows:
            out.write("%-10s %s\n" % (r["name"], r["title"] or ""))
            out.write("%-10s rooms %s; beams %s; occupancy %s; %d regions\n"
                      % ("", " ".join(r["rooms"]) or "-", " ".join(r["beams"]) or "-",
                         " ".join(r["occupancy"]) or "-", r["regions"]))
    return EXIT_OK


def _cmd_view(a: argparse.Namespace, out, err) -> int:
    m = _get_map(a.map)
    try:
        from . import viewer
        import matplotlib  # noqa: F401
    except ImportError:
        err.write("cyber-detectives: the viewer needs matplotlib "
                  "(pip install 'cyber-detectives[viewer]')\n")
        return EXIT_NOTIMPL
    try:
        viewer.run(m, agents="multi" if a.multi else "single",
                   theme="dark" if a.dark else "light", claim=a.story, save=a.save)
    except viewer.NoDisplayError as e:
        err.write("cyber-detectives: %s\n" % e)
        return EXIT_NOTIMPL
    if a.save:
        out.write("wrote %s\n" % a.save)
    return EXIT_OK


def main(argv: Optional[List[str]] = None, out=None, err=None) -> int:
    """Run the CLI; returns the exit status (see the module docstring)."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _cmd_validate(args, out)
        if args.command == "maps":
            return _cmd_maps(args, out)
        if args.command == "view":
            return _cmd_view(args, out, err)
        return _cmd_problem(args, out)
    except NotImplementedError as e:
        err.write("cyber-detectives: not implemented: %s\n" % e)
        return EXIT_NOTIMPL
    except (InputError, MapError, KeyError, ValueError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        err.write("cyber-detectives: error: %s\n" % msg)
        return EXIT_INPUT


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
