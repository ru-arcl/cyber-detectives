#!/usr/bin/env bash
# Regenerate tests/fixtures/golden/ end to end:
#   1. build the original code and the harness (build.sh, run.sh)
#   2. generate the case files (gen_cases.py, fixed seed)
#   3. run every case set under the canonical JVM flags (CD_JVM_FLAGS: -XX:hashCode=2
#      -XX:-OmitStackTraceInFastThrow)
#   4. run every set again under alternative identity-hash settings (order dependence)
#   5. checks: one-fresh-JVM-per-case equals the batch run; --burn-seed has no effect under
#      hashCode=2; the generic map builder reproduces getBasicGame() on every STAR case
#   6. merge into tests/fixtures/golden/*.json + summary.json (make_golden.py; it adds the
#      "crosscheck" block to paper_cases.json with paper_crosscheck.py and oracle.py)
#   7. record java.util.HashSet iteration orders for the javahash tests (javahash_probe.py
#      -> tests/fixtures/golden/javahash.json)
#
#   tools/reference/regen_golden.sh
#
# Environment: CD_CACHE (default tools/reference/.cache), CD_WORK (default $CD_CACHE/work),
# CD_SEED (default: gen_cases.py's DEFAULT_SEED), CD_FRESH_JVM_SAMPLE (cases per set run in
# their own JVM, default 40), plus the build.sh variables.
set -euo pipefail
export LC_ALL=C
export PYTHONDONTWRITEBYTECODE=1  # no __pycache__ next to make_golden.py / oracle.py
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
export CD_CACHE="${CD_CACHE:-$HERE/.cache}"
WORK="${CD_WORK:-$CD_CACHE/work}"
GOLDEN="$REPO/tests/fixtures/golden"
SAMPLE="${CD_FRESH_JVM_SAMPLE:-40}"
say() { printf '[regen] %s\n' "$*" >&2; }

# 1. build (run.sh --meta builds the original code and compiles the harness)
"$HERE/run.sh" --meta > /dev/null
export CD_SKIP_BUILD=1
# shellcheck disable=SC1091
source "$CD_CACHE/env.sh"
case " $CD_JVM_FLAGS " in  # an env.sh from an older build.sh (see run.sh)
  *" -XX:-OmitStackTraceInFastThrow "*) ;;
  *) CD_JVM_FLAGS="$CD_JVM_FLAGS -XX:-OmitStackTraceInFastThrow" ;;
esac
ORIG_COMMIT="$(sed -n 's/^ORIG_COMMIT="\(.*\)"$/\1/p' "$HERE/build.sh")"
BASE_FLAGS="-Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions -XX:-OmitStackTraceInFastThrow"
ALT_COMMON="-XX:+UseSerialGC -XX:ActiveProcessorCount=1"
# label|flags|burn seed
ALTS=(
  "hc5_burn1|$BASE_FLAGS -XX:hashCode=5 $ALT_COMMON|1"
  "hc5_burn2|$BASE_FLAGS -XX:hashCode=5 $ALT_COMMON|2"
  "hc5_burn3|$BASE_FLAGS -XX:hashCode=5 $ALT_COMMON|3"
  "hc3_burn0|$BASE_FLAGS -XX:hashCode=3 $ALT_COMMON|0"
  "hc3_burn4|$BASE_FLAGS -XX:hashCode=3 $ALT_COMMON|4"
  "hc3_burn5|$BASE_FLAGS -XX:hashCode=3 $ALT_COMMON|5"
)

rm -rf "$WORK"
mkdir -p "$WORK/canon" "$WORK/fresh" "$WORK/check"

# 2. cases
seed_args=()
[ -n "${CD_SEED:-}" ] && seed_args=(--seed "$CD_SEED")
python3 "$HERE/gen_cases.py" --out "$WORK/cases" ${seed_args[@]+"${seed_args[@]}"}
SETS=()
for f in "$WORK"/cases/*.cases.json; do SETS+=("$(basename "$f" .cases.json)"); done

# 3. canonical
"$HERE/run.sh" --meta > "$WORK/meta.json"
for s in "${SETS[@]}"; do
  say "canonical: $s"
  "$HERE/run.sh" "$WORK/cases/$s.cases.json" > "$WORK/canon/$s.json"
done

# 4. alternative hash settings
alt_args=()
for spec in "${ALTS[@]}"; do
  IFS='|' read -r label flags burn <<< "$spec"
  mkdir -p "$WORK/alt_$label"
  for s in "${SETS[@]}"; do
    CD_HARNESS_JVM_FLAGS="$flags" "$HERE/run.sh" --burn-seed "$burn" "$WORK/cases/$s.cases.json" \
      > "$WORK/alt_$label/$s.json"
  done
  say "alternative $label done"
  alt_args+=(--alt "$label=$WORK/alt_$label" --alt-desc "$label=$flags --burn-seed $burn")
done

# 5a. fresh JVM per case (first $SAMPLE cases of every set, all of builtin)
say "checking one-JVM-per-case against the batch run ($SAMPLE cases per set)"
python3 - "$WORK" "$SAMPLE" "${SETS[@]}" <<'PY'
import json, os, sys
work, sample, sets = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
for s in sets:
    d = json.load(open(os.path.join(work, "cases", s + ".cases.json")))
    n = len(d["cases"]) if s == "builtin" else min(sample, len(d["cases"]))
    for i, c in enumerate(d["cases"][:n]):
        with open(os.path.join(work, "fresh", "%s.%04d.case.json" % (s, i)), "w") as f:
            json.dump({"maps": d["maps"], "cases": [c]}, f)
PY
for f in "$WORK"/fresh/*.case.json; do
  "$HERE/run.sh" "$f" > "${f%.case.json}.result.json"
done
python3 - "$WORK" "${SETS[@]}" <<'PY'
import glob, json, os, sys
work, sets = sys.argv[1], sys.argv[2:]
canon = {}
for s in sets:
    for r in json.load(open(os.path.join(work, "canon", s + ".json"))):
        canon[(s, r["id"])] = r
bad = n = 0
for f in sorted(glob.glob(os.path.join(work, "fresh", "*.result.json"))):
    s = os.path.basename(f).split(".")[0]
    for r in json.load(open(f)):
        n += 1
        if r != canon[(s, r["id"])]:
            bad += 1
            print("fresh-JVM mismatch:", s, r["id"], file=sys.stderr)
print("[regen] fresh-JVM check: %d cases, %d mismatches" % (n, bad), file=sys.stderr)
sys.exit(1 if bad else 0)
PY

# 5b. --burn-seed must not matter under hashCode=2; 5c. generic builder == getBasicGame()
for s in "${SETS[@]}"; do
  "$HERE/run.sh" --burn-seed 7 "$WORK/cases/$s.cases.json" > "$WORK/check/$s.burn7.json"
  cmp -s "$WORK/canon/$s.json" "$WORK/check/$s.burn7.json" \
    || { say "ERROR: --burn-seed changed canonical output of $s"; exit 1; }
  "$HERE/run.sh" --force-generic "$WORK/cases/$s.cases.json" > "$WORK/check/$s.generic.json"
done
python3 - "$WORK" "${SETS[@]}" <<'PY'
import json, os, sys
work, sets = sys.argv[1], sys.argv[2:]
n = bad = 0
for s in sets:
    a = json.load(open(os.path.join(work, "canon", s + ".json")))
    b = json.load(open(os.path.join(work, "check", s + ".generic.json")))
    for x, y in zip(a, b):
        n += 1
        if x != y:
            bad += 1
            print("generic-builder mismatch:", s, x["id"], file=sys.stderr)
print("[regen] generic builder check: %d cases rebuilt with the generic builder, %d mismatches" % (n, bad),
      file=sys.stderr)
sys.exit(1 if bad else 0)
PY

# 6. merge
mkdir -p "$GOLDEN"
rm -f "$GOLDEN"/*.json
python3 "$HERE/make_golden.py" --cases "$WORK/cases" --results "$WORK/canon" --meta "$WORK/meta.json" \
  "${alt_args[@]}" --out "$GOLDEN" --original-commit "$ORIG_COMMIT" \
  --canonical-flags "$CD_JVM_FLAGS" --paper-crosscheck "$REPO"

# 7. javahash fixture (real JDK HashSet iteration orders)
python3 "$HERE/javahash_probe.py" --out "$GOLDEN/javahash.json"
( cd "$GOLDEN" && sha256sum ./*.json ) > "$WORK/golden.sha256"
say "done: $(ls "$GOLDEN"/*.json | wc -l) files in tests/fixtures/golden (checksums in $WORK/golden.sha256)"
