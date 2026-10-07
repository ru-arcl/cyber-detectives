#!/usr/bin/env bash
# Reproducible headless build of the original Cyber Detectives Java code.
#
#   tools/reference/build.sh            # cache in tools/reference/.cache
#   CD_CACHE=/some/dir tools/reference/build.sh
#
# Steps (each skipped when its output already exists and verifies):
#   1. clone https://github.com/arc-l/cyber-detective.git at a pinned commit
#   2. download + SHA-256-verify a portable Eclipse Temurin JDK 8 (linux x64)
#   3. download + SHA-1/SHA-256-verify log4j 1.2.12 (needed only by the GUI tree)
#   4. compile the FULL tree (GUI included) and, separately, the core package
#      without log4j and checked against the AWT-free compact1 profile
#   5. headless smoke test: Algorithms.main must end with "Story inconsistent."
#   6. write $CD_CACHE/env.sh with JAVA / JAVAC / class directories
#
# Environment variables (all optional):
#   CD_CACHE         cache directory (default: <script dir>/.cache)
#   CD_ORIGINAL_DIR  use an existing checkout instead of cloning (its HEAD must
#                    be the pinned commit if it is a git checkout)
#   CD_JAVA_HOME     use an existing JDK 8 instead of downloading Temurin
#   CD_ORIGINAL_URL  clone URL (default: the GitHub repository below)
set -euo pipefail
export LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CD_CACHE="${CD_CACHE:-$HERE/.cache}"

ORIG_URL="${CD_ORIGINAL_URL:-https://github.com/arc-l/cyber-detective.git}"
ORIG_COMMIT="55f57f8b307047df615acdd2024140ad4030bbd4"

JDK_RELEASE="jdk8u504-b01"
JDK_FILE="OpenJDK8U-jdk_x64_linux_hotspot_8u504b01.tar.gz"
JDK_URL="https://github.com/adoptium/temurin8-binaries/releases/download/${JDK_RELEASE}/${JDK_FILE}"
JDK_SHA256="9c70e102f527ac674ac2fe9c7d47b9a04e2d19842ba5ab8e9b33f368bbadfaea"

LOG4J_FILE="log4j-1.2.12.jar"
LOG4J_URL="https://repo1.maven.org/maven2/log4j/log4j/1.2.12/${LOG4J_FILE}"
LOG4J_SHA1="057b8740427ee6d7b0b60792751356cad17dc0d9"
LOG4J_SHA256="dc67378cf428c06408e7959e83bdc1518dd22ccd313e7c28a986612d65c276c7"

say() { printf '[build] %s\n' "$*" >&2; }
die() { printf '[build] ERROR: %s\n' "$*" >&2; exit 1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
sha1_of() {
  if command -v sha1sum >/dev/null 2>&1; then sha1sum "$1" | cut -d' ' -f1
  else shasum -a 1 "$1" | cut -d' ' -f1; fi
}

# fetch URL DEST: download to DEST.part, then move into place
fetch() {
  say "downloading $1"
  curl -fsSL --retry 3 -o "$2.part" "$1" || die "download failed: $1"
  mv "$2.part" "$2"
}

mkdir -p "$CD_CACHE"
CD_CACHE="$(cd "$CD_CACHE" && pwd)"

# ---------------------------------------------------------------- 1. original
if [ -n "${CD_ORIGINAL_DIR:-}" ]; then
  ORIG="$(cd "$CD_ORIGINAL_DIR" && pwd)"
else
  ORIG="$CD_CACHE/original"
  if [ ! -d "$ORIG/.git" ]; then
    say "cloning $ORIG_URL"
    rm -rf "$ORIG.part"
    git clone --quiet "$ORIG_URL" "$ORIG.part"
    git -C "$ORIG.part" -c advice.detachedHead=false checkout --quiet "$ORIG_COMMIT"
    mv "$ORIG.part" "$ORIG"
  fi
fi
if [ -d "$ORIG/.git" ] || git -C "$ORIG" rev-parse --git-dir >/dev/null 2>&1; then
  head_commit="$(git -C "$ORIG" rev-parse HEAD)"
  [ "$head_commit" = "$ORIG_COMMIT" ] \
    || die "original checkout $ORIG is at $head_commit, expected $ORIG_COMMIT"
  [ -z "$(git -C "$ORIG" status --porcelain)" ] \
    || die "original checkout $ORIG has local modifications"
fi
SRC="$ORIG/cyber-detective-source"
[ -f "$SRC/projects/cyberDetective/Algorithms.java" ] || die "sources not found under $SRC"
say "original sources: $SRC (commit $ORIG_COMMIT)"

# ---------------------------------------------------------------- 2. JDK
if [ -n "${CD_JAVA_HOME:-}" ]; then
  JAVA_HOME_DIR="$CD_JAVA_HOME"
else
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64|Linux-amd64) ;;
    *) die "the pinned JDK is linux x64 only; set CD_JAVA_HOME to a local JDK 8" ;;
  esac
  JAVA_HOME_DIR="$CD_CACHE/$JDK_RELEASE"
  if [ ! -x "$JAVA_HOME_DIR/bin/javac" ]; then
    tarball="$CD_CACHE/$JDK_FILE"
    if [ ! -f "$tarball" ] || [ "$(sha256_of "$tarball")" != "$JDK_SHA256" ]; then
      rm -f "$tarball"
      fetch "$JDK_URL" "$tarball"
    fi
    got="$(sha256_of "$tarball")"
    [ "$got" = "$JDK_SHA256" ] || { rm -f "$tarball"; die "JDK SHA-256 mismatch: $got"; }
    say "JDK SHA-256 OK ($JDK_SHA256)"
    rm -rf "$JAVA_HOME_DIR"
    tar -xzf "$tarball" -C "$CD_CACHE"
  fi
fi
JAVAC="$JAVA_HOME_DIR/bin/javac"
JAVA="$JAVA_HOME_DIR/bin/java"
[ -x "$JAVAC" ] || die "no javac at $JAVAC"
say "using $("$JAVAC" -version 2>&1)"

# ---------------------------------------------------------------- 3. log4j
LOG4J="$CD_CACHE/$LOG4J_FILE"
if [ ! -f "$LOG4J" ] || [ "$(sha1_of "$LOG4J")" != "$LOG4J_SHA1" ]; then
  rm -f "$LOG4J"
  fetch "$LOG4J_URL" "$LOG4J"
  # cross-check against Maven Central's published .sha1 as well as the pinned value
  published="$(curl -fsSL --retry 3 "$LOG4J_URL.sha1" | cut -c1-40)" || die "cannot fetch $LOG4J_URL.sha1"
  [ "$published" = "$LOG4J_SHA1" ] || die "Maven Central .sha1 ($published) differs from pinned $LOG4J_SHA1"
fi
[ "$(sha1_of "$LOG4J")" = "$LOG4J_SHA1" ] || { rm -f "$LOG4J"; die "log4j SHA-1 mismatch"; }
[ "$(sha256_of "$LOG4J")" = "$LOG4J_SHA256" ] || { rm -f "$LOG4J"; die "log4j SHA-256 mismatch"; }
say "log4j SHA-1/SHA-256 OK"

# ---------------------------------------------------------------- 4. compile
# -source/-target 1.5 is the lowest level javac 8 accepts for this code (1.4
# rejects generics). Classes are compiled against JDK 8's rt.jar (no Java 5
# bootclasspath), hence -Xlint:-options.
FULL="$CD_CACHE/classes/full"
CORE="$CD_CACHE/classes/core"
CORE_C1="$CD_CACHE/classes/core-compact1"
rm -rf "$FULL" "$CORE" "$CORE_C1"
mkdir -p "$FULL" "$CORE" "$CORE_C1"

ALL_LIST="$CD_CACHE/sources-all.txt"
CORE_LIST="$CD_CACHE/sources-core.txt"
(cd "$SRC" && find . -name '*.java' | sed 's#^\./##' | sort) > "$ALL_LIST"
(cd "$SRC" && { ls projects/cyberDetective/*.java; echo common/util/IDGenerator.java; } | sort) > "$CORE_LIST"

JFLAGS=(-source 1.5 -target 1.5 -encoding UTF-8 -nowarn -Xlint:-options)

say "compiling full tree ($(wc -l < "$ALL_LIST") files, GUI included) -> $FULL"
(cd "$SRC" && "$JAVAC" "${JFLAGS[@]}" -cp "$LOG4J" -d "$FULL" @"$ALL_LIST")

say "compiling core package ($(wc -l < "$CORE_LIST") files) without log4j -> $CORE"
(cd "$SRC" && "$JAVAC" "${JFLAGS[@]}" -cp "" -d "$CORE" @"$CORE_LIST")

# compact1 contains no java.awt / java.applet / javax.swing: compiling the core
# under it proves the core does not touch AWT. (-profile requires -target 8.)
say "checking core against the AWT-free compact1 profile"
(cd "$SRC" && "$JAVAC" -source 8 -target 8 -profile compact1 -encoding UTF-8 -nowarn \
   -cp "" -d "$CORE_C1" @"$CORE_LIST")

# ---------------------------------------------------------------- 5. smoke test
say "headless smoke test: Algorithms.main (testMultiAgent)"
out="$("$JAVA" -Djava.awt.headless=true -cp "$CORE" projects.cyberDetective.Algorithms)"
last="$(printf '%s\n' "$out" | tail -n 1)"
[ "$last" = "Story inconsistent." ] || die "unexpected Algorithms.main result: '$last'"
say "Algorithms.main -> $last"

# ---------------------------------------------------------------- 6. env file
cat > "$CD_CACHE/env.sh" <<EOF
# generated by tools/reference/build.sh; source this file
CD_JAVA='$JAVA'
CD_JAVAC='$JAVAC'
CD_ORIGINAL_SRC='$SRC'
CD_CLASSES_FULL='$FULL'
CD_CLASSES_CORE='$CORE'
CD_LOG4J='$LOG4J'
# Canonical JVM flags for golden (order-dependent) output and stable exception traces;
# see README.md.
CD_JVM_FLAGS='-Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions -XX:hashCode=2 -XX:-OmitStackTraceInFastThrow'
EOF
say "done; wrote $CD_CACHE/env.sh"
