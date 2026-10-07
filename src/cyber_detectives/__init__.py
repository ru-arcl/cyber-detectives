"""Cyber Detectives: validate an agent's story against a sparse sensor network's history.

Python implementation of J. Yu and S. M. LaValle, "Cyber Detectives: Determining When Robots
or People Misbehave" (WAFR 2010) and "Story Validation and Approximate Path Inference with a
Sparse Network of Heterogeneous Sensors" (ICRA 2011).  See ``docs/DESIGN.md``.

>>> from cyber_detectives import builtin_map, validate
>>> m = builtin_map("star_fig2")
>>> validate(m, "ACBAC", "b1 o1 o1 b2 o2 o2").consistent
False
>>> validate(m, "ACBAC", "b1 o1 o1 o2 o2 b2").consistent
True

The names exported here are the public API (``docs/DESIGN.md``, "Public API"):

- maps: :class:`Map`, :func:`builtin_map`, :func:`builtin_map_names`, :func:`load_map`;
- inputs: :func:`parse_story`, :func:`parse_history`, :class:`Event`;
- Problem 1: :func:`validate` (``agents="single"|"multi"``, ``unreported_visits``, and
  ``compat="original"`` for the original Java code's answer, bugs included);
- Problems 2-4: :func:`validate_intervals`, :func:`shortest_superstory`,
  :func:`closest_story`;
- witnesses: :class:`Result`, :class:`Step`, :func:`replay`, :func:`path_to_string`;
- where x can be: :func:`possible_positions` (per time slot; with or without a story);
- errors (all ``ValueError`` subclasses): :class:`MapError`, :class:`InputError`,
  :class:`InvalidPath`.

Submodules that are not re-exported (each is imported on first access, e.g.
``cyber_detectives.subgraphs``, or with ``import cyber_detectives.compat``):

- :mod:`cyber_detectives.subgraphs` -- the paper-literal constructions (STAR Algorithms 1, 2,
  4, the composite graph ``G_s``, ICRA ``SUBG`` and NFAs), as printed and corrected;
- :mod:`cyber_detectives.compat` -- the exact port of the original Java code behind
  ``compat="original"`` (``compat.original``, ``compat.applet``, ``compat.javahash``); its
  known bugs are listed in ``docs/notes/original-bugs.md``;
- :mod:`cyber_detectives.geometry` -- map drawings, hit-testing and simulated walks;
- :mod:`cyber_detectives.engine`, :mod:`.maps`, :mod:`.history`, :mod:`.problems`,
  :mod:`.cli` -- the modules the names above come from.
"""

from __future__ import annotations

import importlib
from typing import Any, List

from .engine import (InvalidPath, Result, Step, path_to_string, possible_positions, replay,
                     validate)
from .history import Event, InputError, parse_history, parse_story
from .maps import Map, MapError, builtin_map, builtin_map_names, load_map
from .problems import closest_story, shortest_superstory, validate_intervals

# Single source of the version: pyproject.toml reads it (setuptools dynamic attr) and
# CITATION.cff must match it (checked by tests/test_packaging.py).
__version__ = "1.0.0"

__all__ = [
    "Map", "MapError", "builtin_map", "builtin_map_names", "load_map",
    "Event", "InputError", "parse_story", "parse_history",
    "validate", "replay", "Result", "Step", "InvalidPath", "path_to_string",
    "possible_positions",
    "validate_intervals", "shortest_superstory", "closest_story",
    "__version__",
]

# Submodules loaded on first attribute access (PEP 562), so that ``import cyber_detectives``
# stays cheap: ``compat`` pulls in the Java HashMap emulation, ``viewer`` needs matplotlib.
_LAZY_SUBMODULES = ("compat", "subgraphs", "geometry", "viewer")


def __getattr__(name: str) -> Any:
    if name in _LAZY_SUBMODULES:
        return importlib.import_module("." + name, __name__)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(_LAZY_SUBMODULES))
