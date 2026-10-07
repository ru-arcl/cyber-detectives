// docs/js/original.js beyond the parity records (tests/js/engine.test.js runs compat.json):
// the namespace contract, the JavaException shape and validate(..., {compat: "original"}).
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..", "..");
const read = (n) => fs.readFileSync(path.join(ROOT, "docs", "js", n), "utf8");

function context(names) {
  const ctx = vm.createContext({});
  for (const n of names) vm.runInContext(read(n), ctx, { filename: n });
  return ctx;
}

const plain = (x) => JSON.parse(JSON.stringify(x));

const STAR = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "fixtures", "parity", "compat.json"),
                                        "utf8")).maps.star_fig2;

test("original.js needs engine.js first and defines CyberDetectives.original only", () => {
  assert.throws(() => context(["original.js"]), /load docs\/js\/engine.js before/);
  const ctx = context(["engine.js", "original.js"]);
  const keys = Object.keys(ctx).sort();
  assert.deepStrictEqual(keys, ["CyberDetectives"]);
  assert.strictEqual(typeof ctx.CyberDetectives.original.validateCompat, "function");
});

test("validateCompat: STAR examples and the getAgentStory path", () => {
  const CD = context(["engine.js", "original.js"]).CyberDetectives;
  const ev = (s) => CD.parseHistory(s, CD.Map.fromDict(STAR)).map((e) => [e.sensor, e.kind]);
  assert.deepStrictEqual(plain(CD.original.validateCompat(STAR, ["A", "C", "B", "A", "C"],
                                                         ev("b1 o1 o1 o2 o2 b2"), "single")),
                         { consistent: true, path_string: "A[b1u]C[o1][o2]B[b2r]AC" });
  assert.deepStrictEqual(plain(CD.original.validateCompat(STAR, ["A", "C", "B", "A", "C"],
                                                         ev("b1 o1 o1 b2 o2 o2"), "single")),
                         { consistent: false, path_string: null });
  // bug B1: the original's multi-agent false negative
  assert.strictEqual(CD.original.validateCompat(STAR, ["B", "C"], ev("o1+ b2 o1-"), "multi").consistent,
                     false);
  const m = CD.Map.fromDict(STAR);
  assert.strictEqual(CD.validate(m, "BC", "o1+ b2 o1-", { agents: "multi" }).consistent, true);
  const r = CD.validate(m, "ACBAC", "b1 o1 o1 o2 o2 b2", { compat: "original" });
  assert.deepStrictEqual(plain(r), { consistent: true, reason: null, agents: "single", compat: "original",
                                     path_string: "A[b1u]C[o1][o2]B[b2r]AC", path: null });
});

test("crashes are JavaExceptions with the Java location", () => {
  const CD = context(["engine.js", "original.js"]).CyberDetectives;
  let err = null;
  try {
    CD.original.validateCompat(STAR, ["A", "X"], [["b1", "A"]], "single");
  } catch (e) {
    err = e;
  }
  assert.ok(err instanceof CD.original.JavaException);
  assert.strictEqual(err.name, "JavaException");
  assert.strictEqual(err.javaClass, "java.lang.NullPointerException");
  assert.strictEqual(err.topFrame, "projects.cyberDetective.Algorithms.validateAgentStory(Algorithms.java:207)");
  assert.strictEqual(err.message, "java.lang.NullPointerException at " + err.topFrame);
  assert.deepStrictEqual(plain(err.describe()), {
    class: "java.lang.NullPointerException", message: null, top_frame: err.topFrame,
    origin_frame: err.topFrame, trace: [err.topFrame] });
  assert.throws(() => CD.original.validateCompat(STAR, ["A"], [["zz", "A"]], "single"),
                (e) => e instanceof CD.ValueError && e.message === "unknown sensor 'zz'");
});

test("JavaHashSet treeifies a bin of 11 identity-hashed objects like Java 8", () => {
  const CD = context(["engine.js", "original.js"]).CyberDetectives;
  const s = new CD.original.JavaHashSet();
  const objs = [];
  for (let i = 0; i < 12; i++) { objs.push({ name: "o" + i }); s.add(objs[i]); }
  assert.strictEqual(s.tableCapacity(), 64);
  assert.strictEqual(s.size(), 12);
  assert.strictEqual(new Set(s.toArray()).size, 12);
  // the order is pinned by tests/fixtures/golden/javahash.json through compat.json; here
  // only check that removal keeps a consistent set
  for (const o of objs.slice(0, 7)) s.remove(o);
  assert.deepStrictEqual(plain(s.toArray().map((o) => o.name).sort()), ["o10", "o11", "o7", "o8", "o9"]);
});
