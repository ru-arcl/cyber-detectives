"""``compat="original"``: an exact port of the original Java code (arc-l/cyber-detective@55f57f8).

- :mod:`.javahash` -- Java 8 ``HashMap``/``HashSet`` iteration order (canonical JVM setting
  ``-XX:hashCode=2``), on which several of the original's outputs depend;
- :mod:`.original` -- line-by-line port of the non-GUI classes (``Graph``, ``DetectiveGame``,
  ``Algorithms``, ...), bugs and crashes included, plus :func:`.original.validate_compat`
  (the applet pipeline used by ``validate(..., compat="original")``) and an emulation of the
  reference harness (:func:`.original.run_harness_case`);
- :mod:`.applet` -- the applet's non-drawing logic (text parsing, Run/Reset, clicks).

Everything here is pinned by ``tests/fixtures/golden/``; see ``docs/DESIGN.md``.
"""

from .original import (ArrayIndexOutOfBoundsException, JavaException, NullPointerException,
                       capture_stdout, validate_compat)

__all__ = ["JavaException", "NullPointerException", "ArrayIndexOutOfBoundsException",
           "capture_stdout", "validate_compat"]
