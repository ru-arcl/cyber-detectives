#!/usr/bin/env bash
# Build the original code (build.sh) and the harness, then run the harness:
#
#   tools/reference/run.sh cases.json > results.json
#   tools/reference/run.sh --burn-seed 3 cases.json > results.json
#   tools/reference/run.sh --meta
#
# All arguments go to Harness. Environment variables (all optional):
#   CD_CACHE              build cache (default: tools/reference/.cache), see build.sh
#   CD_SKIP_BUILD=1       skip build.sh when $CD_CACHE/env.sh already exists
#   CD_HARNESS_JVM_FLAGS  JVM flags (default: CD_JVM_FLAGS from env.sh, i.e.
#                         -Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions -XX:hashCode=2
#                         -XX:-OmitStackTraceInFastThrow)
# Plus CD_ORIGINAL_DIR / CD_JAVA_HOME / CD_ORIGINAL_URL, passed through to build.sh.
set -euo pipefail
export LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CD_CACHE="${CD_CACHE:-$HERE/.cache}"

if [ "${CD_SKIP_BUILD:-0}" != "1" ] || [ ! -f "$CD_CACHE/env.sh" ]; then
  "$HERE/build.sh" >&2
fi
# shellcheck disable=SC1091
source "$CD_CACHE/env.sh"
# An env.sh written by an older build.sh lacks the stack-trace flag; add it (see README.md).
case " $CD_JVM_FLAGS " in
  *" -XX:-OmitStackTraceInFastThrow "*) ;;
  *) CD_JVM_FLAGS="$CD_JVM_FLAGS -XX:-OmitStackTraceInFastThrow" ;;
esac

HCLS="$CD_CACHE/classes/harness"
if [ ! -f "$HCLS/Harness.class" ] || [ "$HERE/Harness.java" -nt "$HCLS/Harness.class" ] \
   || [ "$CD_CLASSES_FULL" -nt "$HCLS/Harness.class" ]; then
  rm -rf "$HCLS"; mkdir -p "$HCLS"
  "$CD_JAVAC" -source 1.5 -target 1.5 -encoding UTF-8 -nowarn -Xlint:-options \
    -cp "$CD_CLASSES_FULL" -d "$HCLS" "$HERE/Harness.java" >&2
fi

FLAGS="${CD_HARNESS_JVM_FLAGS:-$CD_JVM_FLAGS}"
# shellcheck disable=SC2086
exec "$CD_JAVA" $FLAGS -cp "$CD_CLASSES_FULL:$HCLS" Harness "$@"
