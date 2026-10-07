#!/usr/bin/env python3
"""Record java.util.HashSet iteration orders from the real JDK for the javahash tests.

    python3 tools/reference/javahash_probe.py --out tests/fixtures/golden/javahash.json
    python3 tools/reference/javahash_probe.py --check      # live JDK vs compat.javahash

Generates deterministic operation scripts (seeded), runs them through JavaHashProbe.java
under the canonical golden JVM flags (-XX:+UnlockExperimentalVMOptions -XX:hashCode=2) and
writes, per script, the operations and a digest of the iteration order after every
operation. tests/test_compat_javahash.py replays the scripts with
cyber_detectives.compat.javahash and compares the digests.

The scripts exercise everything that decides Java 8 HashMap iteration order: resizes and
lo/hi splits, bins that treeify (identity-hash objects that all share hash 1, Integer keys
that collide in one bin, String keys with equal String.hashCode), removals that rebalance or
untreeify a tree bin, tree splits on resize, and the null key.

Environment: as run.sh (CD_CACHE, CD_SKIP_BUILD=1 to reuse an existing build, ...).
"""
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SEED = 20101213
VERSION = "1.0.0"
FLAGS = "-XX:+UnlockExperimentalVMOptions -XX:hashCode=2"


def digest(line):
    return hashlib.sha1(line.encode("utf-8")).hexdigest()[:12]


# ----------------------------------------------------------------------------- scripts

def _walk(rng, universe, n_ops, p_add):
    """Random add/remove walk over a key universe (tokens)."""
    present, ops = [], []
    for _ in range(n_ops):
        if present and (rng.random() > p_add or len(present) == len(universe)):
            k = rng.choice(present) if rng.random() < 0.9 else rng.choice(universe)
            ops.append("-" + k)
            if k in present:
                present.remove(k)
        else:
            k = rng.choice(universe)
            ops.append("+" + k)
            if k not in present:
                present.append(k)
    return ops


def _grow_shrink(rng, universe, n, order="random"):
    ks = universe[:n]
    ops = ["+" + k for k in ks]
    rem = ks[:]
    if order == "random":
        rng.shuffle(rem)
    elif order == "reverse":
        rem.reverse()
    ops += ["-" + k for k in rem]
    return ops


def colliding_strings(n_blocks):
    """All 2**n strings made of 'Aa'/'BB' blocks share one String.hashCode."""
    out = [""]
    for _ in range(n_blocks):
        out = [s + b for s in out for b in ("Aa", "BB")]
    return out


def scripts():
    rng = random.Random(SEED)
    out = []

    def add(sid, kind, ops):
        out.append({"id": sid, "kind": kind, "ops": ops})

    objs = ["o%d" % i for i in range(160)]
    # identity-hash objects (all hash 1 under -XX:hashCode=2): insertion order up to 10,
    # tree bins from 11 members on
    for n in (5, 9, 10, 11, 12, 13, 16, 20, 30, 49, 50, 64, 100, 130):
        add("obj_grow_shrink_%d" % n, "obj", _grow_shrink(rng, objs, n))
    for n in (11, 12, 20, 40):
        add("obj_grow_shrink_fifo_%d" % n, "obj", _grow_shrink(rng, objs, n, "fifo"))
        add("obj_grow_shrink_reverse_%d" % n, "obj", _grow_shrink(rng, objs, n, "reverse"))
    for k in range(12):
        size = rng.choice([12, 16, 25, 40, 70, 120])
        add("obj_walk_%02d" % k, "obj", _walk(rng, objs[:size], rng.randint(150, 400), rng.choice([0.55, 0.65, 0.8])))
    for k in range(4):
        add("obj_walk_null_%02d" % k, "obj", _walk(rng, objs[:30] + ["null"], 250, 0.7))
    # Integer keys
    small = [str(i) for i in range(0, 300)]
    for k in range(6):
        add("int_small_walk_%02d" % k, "int", _walk(rng, small, 300, rng.choice([0.6, 0.75])))
    for step in (16, 32, 64, 128, 256):
        u = [str(step * i + rng.randint(0, 3) * 0) for i in range(60)]
        add("int_multiples_%d_grow_shrink" % step, "int", _grow_shrink(rng, u, 60))
        add("int_multiples_%d_walk" % step, "int", _walk(rng, u, 300, 0.7))
    for k in range(4):
        u = [str(rng.randint(-2 ** 31, 2 ** 31 - 1)) for _ in range(150)]
        add("int_random32_%02d" % k, "int", _walk(rng, u, 350, 0.75))
    u = [str(a * 65536 + b) for a in range(2, 30) for b in range(a, 30)]
    rng.shuffle(u)
    add("int_edge_ids", "int", _walk(rng, u[:120], 400, 0.8))
    u = [str((i << 16) | (i & 0xF)) for i in range(80)]  # equal low bits after spreading
    add("int_spread_collisions", "int", _walk(rng, u, 400, 0.75))
    add("int_null", "int", _walk(rng, small[:40] + ["null"], 200, 0.7))
    # String keys, many with equal hashCode (Comparable tree order)
    col = colliding_strings(6)  # 64 strings, one hash
    for k in range(4):
        u = col[:]
        rng.shuffle(u)
        add("str_colliding_walk_%02d" % k, "str", _walk(rng, u, 300, 0.75))
    add("str_colliding_grow_shrink", "str", _grow_shrink(rng, col, 64))
    names = ["SV", "A", "B", "C", "D", "E", "F", "b1u", "b1d", "b2l", "b2r", "b3u", "b3d", "o1", "o2", "o3"]
    words = names + ["k%d" % i for i in range(100)] + colliding_strings(4)
    add("str_mixed_walk", "str", _walk(rng, words, 400, 0.75))
    add("str_null", "str", _walk(rng, names + ["null"], 150, 0.7))
    return out


def script_text(scr):
    lines = []
    for s in scr:
        lines.append("new " + s["kind"])
        for op in s["ops"]:
            lines.append(op[0] + " " + op[1:])
    lines.append("ihash")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- JDK

def java_env():
    env = dict(os.environ)
    env.setdefault("CD_CACHE", os.path.join(HERE, ".cache"))
    if env.get("CD_SKIP_BUILD") != "1" or not os.path.exists(os.path.join(env["CD_CACHE"], "env.sh")):
        subprocess.check_call([os.path.join(HERE, "build.sh")], env=env, stdout=sys.stderr)
    out = subprocess.check_output(["bash", "-c", 'source "$CD_CACHE/env.sh"; echo "$CD_JAVA"; echo "$CD_JAVAC"'],
                                  env=env, universal_newlines=True).split("\n")
    return env, out[0], out[1]


def run_java(text):
    env, java, javac = java_env()
    cls = os.path.join(env["CD_CACHE"], "classes", "javahash_probe")
    src = os.path.join(HERE, "JavaHashProbe.java")
    target = os.path.join(cls, "JavaHashProbe.class")
    if not os.path.exists(target) or os.path.getmtime(src) > os.path.getmtime(target):
        os.makedirs(cls, exist_ok=True)
        subprocess.check_call([javac, "-source", "1.5", "-target", "1.5", "-nowarn", "-Xlint:-options",
                               "-encoding", "UTF-8", "-d", cls, src])
    meta = subprocess.check_output([java, "-XshowSettings:properties", "-version"], stderr=subprocess.STDOUT,
                                   universal_newlines=True)
    props = {}
    for ln in meta.splitlines():
        if "=" in ln:
            k, v = ln.strip().split("=", 1)
            props[k.strip()] = v.strip()
    res = subprocess.run([java] + FLAGS.split() + ["-cp", cls, "JavaHashProbe"], input=text.encode("utf-8"),
                         stdout=subprocess.PIPE, check=True)
    jdk = {k: props.get(k) for k in ("java.version", "java.runtime.version", "java.vm.name", "java.vm.version",
                                     "java.vendor", "os.arch")}
    return res.stdout.decode("utf-8").split("\n"), jdk


def record(scr, lines):
    pos = 0
    for s in scr:
        s["orders_sha1_12"] = []
        for _op in s["ops"]:
            s["orders_sha1_12"].append(digest(lines[pos]))
            pos += 1
        s["final_order"] = lines[pos - 1].split(" ") if s["ops"] and lines[pos - 1] else []
    ihash = lines[pos]
    return ihash


def python_orders(s):
    sys.path.insert(0, os.path.join(REPO, "src"))
    from cyber_detectives.compat.javahash import JavaHashSet

    class Obj:
        def __init__(self, label):
            self.label = label

    objs = {}
    st = JavaHashSet()
    out = []

    def key(tok):
        if tok == "null":
            return None
        if s["kind"] == "obj":
            if tok not in objs:
                objs[tok] = Obj(tok)
            return objs[tok]
        return int(tok) if s["kind"] == "int" else tok

    def label(k):
        if k is None:
            return "null"
        return k.label if s["kind"] == "obj" else str(k)

    for op in s["ops"]:
        k = key(op[1:])
        st.add(k) if op[0] == "+" else st.remove(k)
        out.append(" ".join(label(x) for x in st))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="write the fixture here")
    ap.add_argument("--check", action="store_true", help="compare compat.javahash with the live JDK")
    a = ap.parse_args()
    scr = scripts()
    lines, jdk = run_java(script_text(scr))
    ihash = record(scr, lines)
    n_ops = sum(len(s["ops"]) for s in scr)
    if ihash.strip() != "1 1":
        sys.exit("unexpected identity hashes under %s: %r" % (FLAGS, ihash))
    if a.check:
        bad = 0
        for s in scr:
            py = python_orders(s)
            for i, (d, line) in enumerate(zip(s["orders_sha1_12"], py)):
                if digest(line) != d:
                    print("MISMATCH %s op %d (%s): python %s" % (s["id"], i, s["ops"][i], line), file=sys.stderr)
                    bad += 1
                    break
        print("[javahash] %d scripts, %d operations, %d mismatching scripts" % (len(scr), n_ops, bad),
              file=sys.stderr)
        if bad:
            sys.exit(1)
    if a.out:
        doc = {
            "header": {
                "kind": "golden",
                "set": "javahash",
                "description": ("java.util.HashSet iteration order after every operation, recorded from the real "
                                "JDK by tools/reference/JavaHashProbe.java; pins cyber_detectives.compat.javahash. "
                                "orders_sha1_12[i] = first 12 hex digits of sha1(the keys after ops[i] joined by one "
                                "space, 'null' for the null key)."),
                "generator": "tools/reference/javahash_probe.py",
                "generator_version": VERSION,
                "seed": SEED,
                "jdk": jdk,
                "jvm_flags": FLAGS,
                "identity_hash_of_two_objects": ihash.strip().replace(" ", ","),
                "op_syntax": "'+K' = add(K), '-K' = remove(K); kind obj = distinct identity-hash objects named K, "
                             "int = java.lang.Integer, str = java.lang.String; 'null' = the null key",
                "num_scripts": len(scr),
                "num_ops": n_ops,
            },
            "scripts": scr,
        }
        with open(a.out, "w", encoding="ascii", newline="\n") as f:
            f.write("{\n")
            f.write('"header": %s,\n' % json.dumps(doc["header"], indent=1))
            f.write('"scripts": [\n')
            for i, s in enumerate(scr):
                f.write(json.dumps(s, separators=(",", ":")))
                f.write(",\n" if i + 1 < len(scr) else "\n")
            f.write("]\n}\n")
        print("[javahash] wrote %s: %d scripts, %d operations" % (a.out, len(scr), n_ops), file=sys.stderr)


if __name__ == "__main__":
    main()
