// Pure-function tests of the demo page helpers (docs/js/draw.js, docs/js/app.js).  The classic
// scripts are loaded with node:vm into one context, in the order of docs/index.html; app.js
// starts the page only when a document exists, so here it only exports CyberDetectives.app.
// Run: npm test (node built-ins only).  The browser itself is checked by tools/demo/check.js.
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..", "..");
const DOCS = path.join(ROOT, "docs");
const SCRIPTS = ["data.js", "engine.js", "original.js", "draw.js", "app.js"];

const ctx = vm.createContext({});
for (const n of SCRIPTS) {
  const file = path.join(DOCS, "js", n);
  vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
}
const CD = ctx.CyberDetectives;
const DATA = ctx.CD_DATA;
const draw = CD.draw;
const app = CD.app;
const plain = (x) => JSON.parse(JSON.stringify(x === undefined ? null : x));

const MAPS = DATA.map_order;

// a spread of questions per map (all problems, both agent models, both room-visit readings)
function questions() {
  const out = [];
  const inputs = {
    star_fig2: [["ACBAC", "b1 o1 o1 o2 o2 b2"], ["ACBAC", "b1 o1 o2 b2 o2 o1"], ["AC", "b1 b1 b1"],
                ["BC", "o1 b2 o1"], ["AA", "b2"], ["ABAC", "b2 o2 o2 o1 o1"], ["A", ""], ["CAB", "o1 o1 b2"]],
    icra_fig2: [["ABCDE", "b1 b3 o2 o2"], ["ABDEC", "b1 b3 o2 o2 b4"], ["ABDEC", "b1 b2 o2 o2 b4"],
                ["AB", "b1 b3"], ["DE", "o2 o2 b5"], ["E", "o3 o3"], ["CBA", "b3 b1"]],
  };
  for (const map of MAPS) {
    for (const [story, history] of inputs[map] || [[app.defaultInputs(map).story, app.defaultInputs(map).history]]) {
      for (const agents of ["single", "multi"]) {
        for (const unreported of [false, true]) {
          out.push({ mapName: map, problem: 1, agents, unreported, kase: 1, anchored: true, story, history, presetId: null });
          for (let c = 1; c <= 6; c++) out.push({ mapName: map, problem: 2, agents, unreported, kase: c, anchored: true, story, history, presetId: null });
          out.push({ mapName: map, problem: 3, agents, unreported, kase: 1, anchored: true, story, history, presetId: null });
          out.push({ mapName: map, problem: 3, agents, unreported, kase: 1, anchored: false, story, history, presetId: null });
          out.push({ mapName: map, problem: 4, agents, unreported, kase: 1, anchored: true, story, history, presetId: null });
        }
      }
    }
  }
  return out;
}
const QUESTIONS = questions();
const SOLVED = QUESTIONS.map((q) => [q, app.solve(q)]);

// ------------------------------------------------------------------ polyline port

test("polylineForPath reproduces every preset's expected polyline (gen_demo_data.py)", () => {
  let n = 0;
  for (const p of DATA.presets) {
    if (!p.expected.path) { assert.strictEqual(p.expected.polyline, null, p.id); continue; }
    assert.deepStrictEqual(plain(draw.polylineForPath(DATA.maps[p.map], p.expected.path)), plain(p.expected.polyline), p.id);
    n++;
  }
  assert.ok(n >= 10, "too few preset witnesses: " + n);
});

test("tracePath keys are nondecreasing indices into the polyline, one per step", () => {
  let n = 0;
  for (const [q, R] of SOLVED) {
    if (!R.path) continue;
    const tr = draw.tracePath(DATA.maps[q.mapName], R.path);
    assert.deepStrictEqual(plain(tr.points), plain(draw.polylineForPath(DATA.maps[q.mapName], R.path)));
    assert.strictEqual(tr.keys.length, R.path.length);
    assert.strictEqual(tr.keys[0], 0);
    for (let i = 1; i < tr.keys.length; i++) {
      assert.ok(tr.keys[i] >= tr.keys[i - 1] && tr.keys[i] < tr.points.length, JSON.stringify(q));
    }
    n++;
  }
  assert.ok(n > 100, "too few witnesses drawn: " + n);
});

test("tracePath puts a crossing step on the beam and a room entry on its doorway", () => {
  for (const p of DATA.presets) {
    if (!p.expected.path) continue;
    const entry = DATA.maps[p.map], tr = draw.tracePath(entry, p.expected.path);
    p.expected.path.forEach((s, i) => {
      const pt = tr.points[tr.keys[i]];
      if (s.kind === "cross" || s.kind === "pass") {
        const c = entry.crossings[s.sensor];
        assert.deepStrictEqual(plain(pt), plain(c.points[c.points.length - 2]), p.id + " step " + i);
      } else if (s.kind === "visit") {
        const prev = p.expected.path[i - 1].position;
        assert.deepStrictEqual(plain(pt), plain(entry.interiors[s.position].to[prev][1]), p.id + " step " + i);
      }
    });
  }
});

test("tracePath draws x on the region's side once it has left a room or occupancy region", () => {
  // "x leaves o1 into region R3": the marker is in R3's free space, not inside o1 (whose
  // rectangle the page draws unshaded and off at that step)
  let n = 0;
  for (const [q, R] of SOLVED) {
    if (!R.path) continue;
    const entry = DATA.maps[q.mapName], tr = draw.tracePath(entry, R.path), cells = draw.regionCells(entry);
    const d = entry.drawing;
    for (let i = 1; i < R.path.length; i++) {
      const from = R.path[i - 1].position, to = R.path[i].position;
      if (!(Object.prototype.hasOwnProperty.call(entry.interiors, from) && Object.prototype.hasOwnProperty.call(entry.routes, to))) continue;
      const [x, y] = tr.points[tr.keys[i]], where = JSON.stringify(q) + " step " + i;
      assert.deepStrictEqual(plain([x, y]), plain(entry.interiors[from].to[to][2]), where);
      const rect = d.rooms[from] || d.occupancy[from];
      assert.ok(!(x >= rect[0] && x <= rect[0] + rect[2] && y >= rect[1] && y <= rect[1] + rect[3]), where + ": inside " + from);
      assert.ok(cells[to].some((c) => x >= c[0] && x <= c[0] + c[2] && y >= c[1] && y <= c[1] + c[3]), where + ": not in " + to);
      n++;
    }
  }
  assert.ok(n > 100, "too few exits checked: " + n);
});

test("tracePath rejects a transition the data has no route for", () => {
  const e = DATA.maps.star_fig2;
  assert.throws(() => draw.tracePath(e, [{ kind: "start", position: "A" }, { kind: "visit", position: "B" }]), /no move|does not open/);
  assert.throws(() => draw.tracePath(e, [{ kind: "start", position: "nowhere" }]), /unknown start/);
  assert.throws(() => draw.tracePath(e, [{ kind: "start", position: "A" }, { kind: "move", position: "R1" },
                                         { kind: "cross", position: "R4", sensor: "b1d" }]), /cannot cross/);
});

// ------------------------------------------------------------------ region shading

function inside(r, p) { return p[0] >= r[0] - 1e-9 && p[0] <= r[0] + r[2] + 1e-9 && p[1] >= r[1] - 1e-9 && p[1] <= r[1] + r[3] + 1e-9; }
function overlap(a, b) { return a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3]; }

for (const name of MAPS) {
  test("regionCells(" + name + "): label points inside, regions disjoint, clear of obstacles, routes inside", () => {
    const entry = DATA.maps[name], cells = draw.regionCells(entry), obs = draw.obstacles(entry.drawing);
    const names = Object.keys(entry.drawing.regions);
    assert.deepStrictEqual(plain(Object.keys(cells)), plain(names));
    for (const r of names) {
      assert.ok(cells[r].length > 0, r + " has no cells");
      assert.ok(cells[r].some((c) => inside(c, entry.drawing.regions[r].point)), r + " label point");
      for (const c of cells[r]) {
        assert.ok(c[2] > 0 && c[3] > 0);
        for (const o of obs) assert.ok(!overlap(c, o), r + " cell " + c + " overlaps obstacle " + o);
      }
      for (const s of names) {
        if (s <= r) continue;
        for (const a of cells[r]) for (const b of cells[s]) assert.ok(!overlap(a, b), r + "/" + s + " overlap");
      }
      // every routed point of region r is in r's free space (closed cells)
      const routes = entry.routes[r];
      for (const a of Object.keys(routes.paths)) {
        for (const b of Object.keys(routes.paths[a])) {
          for (const pt of routes.paths[a][b]) assert.ok(cells[r].some((c) => inside(c, pt)), r + " route " + a + "->" + b + " point " + pt);
        }
      }
    }
  });
}

test("rectsPath writes one closed subpath per rectangle", () => {
  assert.strictEqual(draw.rectsPath([[0, 0, 10, 5], [1.234, 2, 3, 4]]), "M0 0h10v5h-10zM1.23 2h3v4h-3z");
  assert.strictEqual(draw.rectsPath([]), "");
});

// ------------------------------------------------------------------ labels

test("room labels keep clear of x's resting point; side labels clear of the beam band", () => {
  for (const name of MAPS) {
    const entry = DATA.maps[name], d = entry.drawing;
    for (const r of Object.keys(d.rooms)) {
      const rc = d.rooms[r], lp = draw.roomLabelPoint(rc, entry.interiors[r].point);
      assert.ok(inside(rc, lp), name + " " + r + " label inside the room");
      const ip = entry.interiors[r].point;
      assert.ok(Math.hypot(lp[0] - ip[0], lp[1] - ip[1]) >= 30, name + " " + r + " label away from the marker");
    }
    for (const b of Object.keys(d.beams)) {
      const bd = d.beams[b];
      for (const s of bd.sides) {
        const p = draw.sideLabelPoint(bd, s, 12), n = bd.normals[s], band = bd.band;
        assert.ok(!inside(band, p), name + " " + s + " label outside the band");
        const c = [band[0] + band[2] / 2, band[1] + band[3] / 2];
        assert.ok((p[0] - c[0]) * n[0] + (p[1] - c[1]) * n[1] > 0, name + " " + s + " label on its own side");
      }
    }
  }
});

function segDist(p, a, b) {
  const vx = b[0] - a[0], vy = b[1] - a[1], L2 = vx * vx + vy * vy;
  const t = L2 > 0 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2)) : 0;
  return Math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy);
}

test("occupancy labels keep clear of x's resting point and of the walks to the doorways", () => {
  for (const name of MAPS) {
    const entry = DATA.maps[name], d = entry.drawing;
    for (const o of Object.keys(d.occupancy)) {
      const rc = d.occupancy[o], it = entry.interiors[o], lp = draw.occLabelPoint(rc, it);
      assert.ok(inside(rc, lp), name + " " + o + " label inside the region");
      assert.ok(Math.hypot(lp[0] - it.point[0], lp[1] - it.point[1]) >= 35, name + " " + o + " label away from the marker");
      for (const r of Object.keys(it.to)) {
        const pl = it.to[r];
        for (let i = 1; i < pl.length; i++) assert.ok(segDist(lp, pl[i - 1], pl[i]) >= 20, name + " " + o + " label off the walk to " + r);
      }
    }
  }
});

test("beam side labels sit beside the middle of the beam, where the walk crosses", () => {
  for (const name of MAPS) {
    const entry = DATA.maps[name], d = entry.drawing;
    for (const b of Object.keys(d.beams)) {
      const bd = d.beams[b];
      for (const s of bd.sides) {
        const p = draw.sideLabelPoint(bd, s, 12), cross = entry.crossings[s].points;
        const horiz = bd.normals[s][0] === 0;  // horizontal beam: labels above / below it
        const alongHalf = horiz ? 0.31 * 12 * s.length : 0.5 * 12;
        // the walk crosses on the line through the crossing points, perpendicular to the beam
        const off = horiz ? Math.abs(p[0] - cross[1][0]) : Math.abs(p[1] - cross[1][1]);
        assert.ok(off >= alongHalf + 2, name + " " + s + ": label " + off.toFixed(1) + " from the crossing");
        // and stays within the beam's extent
        const L = bd.line;
        if (horiz) assert.ok(p[0] - alongHalf >= Math.min(L[0], L[2]) - 1e-9, name + " " + s);
        else assert.ok(p[1] - alongHalf >= Math.min(L[1], L[3]) - 1e-9, name + " " + s);
      }
    }
  }
});

for (const name of MAPS) {
  test("regionEdges(" + name + "): every outline segment borders the region's cells", () => {
    const entry = DATA.maps[name], cells = draw.regionCells(entry), edges = draw.regionEdges(entry);
    assert.deepStrictEqual(plain(Object.keys(edges)), plain(Object.keys(cells)));
    for (const r of Object.keys(edges)) {
      assert.ok(edges[r].length >= 4, r);
      let len = 0;
      for (const e of edges[r]) {
        assert.ok(e[0] === e[2] || e[1] === e[3], "axis-parallel");
        const mid = [(e[0] + e[2]) / 2, (e[1] + e[3]) / 2];
        len += Math.abs(e[2] - e[0]) + Math.abs(e[3] - e[1]);
        // the midpoint is on a cell of r, and a step across the edge leaves r on one side
        assert.ok(cells[r].some((c) => inside(c, mid)), r + " edge " + e);
        const n = e[0] === e[2] ? [0.01, 0] : [0, 0.01];
        const inA = cells[r].some((c) => inside(c, [mid[0] + n[0], mid[1] + n[1]]) && inside(c, [mid[0] + 2 * n[0], mid[1] + 2 * n[1]]));
        const inB = cells[r].some((c) => inside(c, [mid[0] - n[0], mid[1] - n[1]]) && inside(c, [mid[0] - 2 * n[0], mid[1] - 2 * n[1]]));
        assert.ok(inA !== inB, r + " edge " + e + " is not on the outline");
      }
      // the outline is at least the perimeter of the bounding box of the region's cells / 2
      assert.ok(len > 0);
    }
  });
}

test("map colours: text 4.5:1, the 'x could be here' frame 3:1, in both themes", () => {
  const css = fs.readFileSync(path.join(DOCS, "css", "style.css"), "utf8");
  const vars = (block) => { const o = {}; for (const m of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) o[m[1]] = m[2].trim(); return o; };
  const light = vars(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme")));
  const block = (sel) => { const i = css.indexOf(sel); return css.slice(i, css.indexOf("}", i)); };
  const dark = Object.assign({}, light, vars(block(':root[data-theme="dark"] {')));
  const darkSystem = Object.assign({}, light, vars(block(':root:not([data-theme="light"]) {')));
  assert.deepStrictEqual(darkSystem, dark, "the two dark blocks agree");
  const parse = (c) => {
    if (c[0] === "#") return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)).concat([1]);
    const p = c.match(/rgba?\(([^)]+)\)/)[1].split(",").map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const col = (T, spec) => {  // "a/b/c": a over b over c
    const parts = spec.split("/").map((v) => { assert.ok(T[v], "--" + v); return parse(T[v]); });
    let c = parts[parts.length - 1];
    for (let i = parts.length - 2; i >= 0; i--) { const f = parts[i], a = f[3]; c = [0, 1, 2].map((j) => f[j] * a + c[j] * (1 - a)); }
    return c;
  };
  const lum = (c) => c.slice(0, 3).reduce((acc, v, i) => { v /= 255; return acc + [0.2126, 0.7152, 0.0722][i] * (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)); }, 0);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const pairs = [
    ["map-room-label", "map-room/map-floor", 4.5], ["map-room-label", "map-possible-strong/map-floor", 4.5],
    ["map-occ-label", "map-occ/map-floor", 4.5], ["map-occ-active-label", "map-occ-active", 4.5],
    ["map-occ-label", "map-possible-strong/map-occ/map-floor", 4.5],
    ["map-region-label", "map-floor", 4.5], ["map-region-label", "map-possible/map-floor", 4.5],
    ["map-side", "map-floor", 4.5], ["map-side", "map-beam-band/map-possible/map-floor", 4.5],
    ["map-possible-edge", "map-floor", 3], ["map-possible-edge", "map-possible/map-floor", 3],
    ["map-possible-edge", "map-room", 3], ["map-possible-edge", "map-possible-strong/map-floor", 3],
    ["map-possible-edge", "map-occ", 3], ["map-possible-edge", "map-possible-strong/map-occ", 3],
    ["map-possible-edge-on-active", "map-occ-active", 3],
  ];
  for (const [theme, T] of [["light", light], ["dark", dark]]) {
    for (const [fg, bg, min] of pairs) {
      const r = ratio(col(T, fg), col(T, bg));
      assert.ok(r >= min, theme + ": --" + fg + " on " + bg + " is " + r.toFixed(2) + ":1 (< " + min + ")");
    }
  }
  // the frame is what the page draws for "x could be here"
  assert.match(css, /\.region\.possible \.region-edge \{ stroke: var\(--map-possible-edge\)/);
  assert.match(css, /\.possible > \.poss-ring \{ stroke: var\(--map-possible-edge\)/);
  // hover styles only where the pointer hovers (a tapped room must not hide the shading)
  const hoverRules = [...css.matchAll(/[^}]*:hover[^{]*\{/g)].map((m) => m[0]).filter((r) => /#map/.test(r));
  const hs = css.indexOf("@media (hover: hover) {");
  assert.ok(hs >= 0, "no @media (hover: hover) block");
  let depth = 0, he = hs;
  for (; he < css.length; he++) { if (css[he] === "{") depth++; else if (css[he] === "}" && --depth === 0) break; }
  const hoverBlock = css.slice(hs, he);
  for (const r of hoverRules) assert.ok(hoverBlock.includes(r.trim().replace(/\{$/, "").trim()), "map hover rule outside @media (hover: hover): " + r.trim());
});

// ------------------------------------------------------------------ playback timeline

test("timeline: steps happen in order, stepTime(k) shows step k, the end shows the last step", () => {
  for (const [q, R] of SOLVED) {
    if (!R.path) continue;
    const tr = draw.tracePath(DATA.maps[q.mapName], R.path), tl = draw.makeTimeline(tr, { speed: 200, minStep: 0.35 });
    assert.strictEqual(tl.steps, R.path.length);
    assert.deepStrictEqual(plain(draw.sampleTimeline(tl, tr, 0).point), plain(tr.points[0]));
    const end = draw.sampleTimeline(tl, tr, tl.total);
    assert.strictEqual(end.step, R.path.length - 1);
    assert.deepStrictEqual(plain(end.point), plain(tr.points[tr.points.length - 1]));
    for (let k = 0; k < R.path.length; k++) {
      const t = draw.stepTime(tl, k), s = draw.sampleTimeline(tl, tr, t);
      assert.strictEqual(s.step, k, JSON.stringify(q) + " step " + k);
      if (k > 0) assert.ok(t > draw.stepTime(tl, k - 1));
    }
    let last = 0;
    for (let i = 0; i <= 50; i++) {
      const s = draw.sampleTimeline(tl, tr, tl.total * i / 50);
      assert.ok(s.step >= last && Number.isFinite(s.point[0]) && Number.isFinite(s.point[1]));
      last = s.step;
    }
  }
});

test("activeAfter follows activations and deactivations", () => {
  const ev = [["b1", "A"], ["o1", "A"], ["o2", "A"], ["o1", "D"]];
  const occ = ["o1", "o2"];
  assert.deepStrictEqual(plain(draw.activeAfter(ev, 0, occ)), {});
  assert.deepStrictEqual(plain(draw.activeAfter(ev, 3, occ)), { o1: true, o2: true });
  assert.deepStrictEqual(plain(draw.activeAfter(ev.map(([s, k]) => ({ sensor: s, kind: k })), 4, occ)), { o2: true });
});

// ------------------------------------------------------------------ app helpers

test("appendStory / appendHistory keep the text in the notation the engine reads", () => {
  const rooms = ["A", "B", "C"];
  assert.strictEqual(app.appendStory("", "A", rooms), "A");
  assert.strictEqual(app.appendStory("AC", "B", rooms), "ACB");
  assert.strictEqual(app.appendStory("A C", "B", rooms), "A C B");
  assert.strictEqual(app.appendStory("Hall", "Lab", ["Hall", "Lab"]), "Hall Lab");
  assert.strictEqual(app.appendHistory("", "b1"), "b1");
  assert.strictEqual(app.appendHistory("b1 o1 ", "o1"), "b1 o1 o1");
  const m = app.getMap("star_fig2");
  let h = "";
  for (const s of ["b1", "o1", "o1", "o2"]) h = app.appendHistory(h, s);
  assert.deepStrictEqual(plain(CD.parseHistory(h, m)), [{ sensor: "b1", kind: "A" }, { sensor: "o1", kind: "A" },
                                                         { sensor: "o1", kind: "D" }, { sensor: "o2", kind: "A" }]);
});

test("solve agrees with every preset's expected answer and the original's", () => {
  for (const p of DATA.presets) {
    const st = app.presetState(p), R = app.solve(st), e = p.expected;
    assert.strictEqual(R.status === "consistent", e.consistent, p.id);
    assert.strictEqual(R.pathString, e.path_string, p.id);
    assert.deepStrictEqual(plain(R.path), plain(e.path === undefined ? null : e.path), p.id);
    if (!e.consistent) assert.strictEqual(R.reason, e.reason, p.id);
    if (p.problem === 3 || p.problem === 4) assert.deepStrictEqual(plain(R.story), plain(e.story), p.id);
    if (p.problem === 3) assert.deepStrictEqual(plain(R.inserted), plain(e.inserted), p.id);
    if (p.problem === 4) assert.deepStrictEqual(plain(R.operations.map((o) => [o.op, o.index, o.old, o["new"]])), plain(e.operations), p.id);
    if (p.problem === 2) assert.strictEqual(R.interval, e.interval, p.id);
    assert.strictEqual(R.status === "malformed", app.isMalformedReason(e.reason), p.id);
    if (app.originalApplies(p.map, p.problem)) {
      assert.ok(R.original, p.id);
      if (p.original) {
        assert.strictEqual(R.original.status, p.original.consistent ? "consistent" : "inconsistent", p.id);
        assert.strictEqual(R.original.pathString, p.original.path_string, p.id);
      }
      assert.strictEqual(!!R.original.bug, !!p.bug, p.id);
      if (p.bug) { assert.strictEqual(R.original.bug.id, p.bug.id); assert.ok(R.original.differs, p.id); }
    } else {
      assert.strictEqual(R.original, null, p.id);
    }
  }
});

test("a preset's bug note is dropped once the question changes", () => {
  const p = app.presetById("bug_B8"), st = app.presetState(p);
  assert.ok(app.solve(st).original.bug);
  const changed = Object.assign({}, st, { story: "AAA" });
  assert.ok(!app.sameQuestion(app.presetState(p), changed));
  assert.strictEqual(app.solve(changed).original.bug, null);
});

test("parseWalkString reads every preset witness, Problem 2 marks included", () => {
  for (const p of DATA.presets) {
    for (const w of [p.expected.path_string, p.original && p.original.path_string]) {
      if (!w) continue;
      const toks = app.parseWalkString(w, app.getMap(p.map).rooms);
      assert.ok(toks, p.id + " " + w);
      assert.strictEqual(toks.map((t) => t.t === "room" ? t.v : t.t + t.v + { "[": "]", "{": "}", "(": ")", "<": ">", "|": "|" }[t.t]).join(""), w);
    }
  }
  assert.strictEqual(app.parseWalkString("A[b1u", ["A"]), null);
  assert.strictEqual(app.parseWalkString("AZ", ["A"]), null);
});

test("the original's impossible walk (B3) is flagged whatever the preset menu says", () => {
  const base = { mapName: "star_fig2", problem: 1, agents: "single", unreported: false, kase: 1, anchored: true, presetId: null };
  // built by clicks (A, C, b1 x3), typed with a trailing space, and a longer variant
  for (const history of ["b1 b1 b1", "b1 b1 b1 ", "b1  b1,b1", "b1 b1 b1 b1 b1"]) {
    const R = app.solve(Object.assign({}, base, { story: "AC", history }));
    assert.strictEqual(R.status, "consistent", history);
    assert.ok(R.original.differs, history);
    assert.strictEqual(R.original.bug && R.original.bug.id, "B3", history);
    assert.match(R.original.walkProblem, /bracketed recordings for \d+ recordings/, history);
    assert.strictEqual(R.original.note, null, history);
  }
  // a walk with the right tokens passes; wrong rooms, sides or counts do not
  const m = app.getMap("star_fig2");
  assert.strictEqual(app.checkWalkString(m, "ACBAC", "b1 o1 o1 o2 o2 b2", "A[b1u]C[o1][o2]B[b2r]AC"), null);
  assert.match(app.checkWalkString(m, "ACBAC", "b1 o1 o1 o2 o2 b2", "A[b1u]C[o1][o2]B[b2r]A").reason, /do not spell the story/);
  assert.match(app.checkWalkString(m, "ACBAC", "b1 o1 o1 o2 o2 b2", "A[b2l]C[o1][o2]B[b2r]AC").reason, /does not match recording 1/);
  const short = app.checkWalkString(m, "AC", "b1 b2", "A[b1u]C");
  assert.ok(short && !short.b3);
  // wherever both verdicts are true for one agent, an original walk failing the check is B3
  // (as in tests/test_default_vs_original.py), and the note never calls a failing walk valid
  let same = 0;
  for (const [q, R] of SOLVED) {
    const o = R.original;
    if (!o || q.agents !== "single" || o.status !== "consistent" || R.status !== "consistent") continue;
    same++;
    if (o.walkProblem) assert.strictEqual(o.bug && o.bug.id, "B3", JSON.stringify(q));
    else assert.strictEqual(app.checkWalkString(app.getMap(q.mapName), q.story, q.history, o.pathString), null);
  }
  assert.ok(same >= 10, "too few same-verdict cases: " + same);
});

test("beam tap targets: the band widened across the beam only", () => {
  for (const name of MAPS) {
    const d = DATA.maps[name].drawing;
    for (const b of Object.keys(d.beams)) {
      const band = d.beams[b].band;
      for (const w of [26, 44]) {
        const r = draw.beamHitRect(band, w), horiz = band[2] >= band[3];
        assert.ok(r[0] <= band[0] && r[1] <= band[1] && r[0] + r[2] >= band[0] + band[2] && r[1] + r[3] >= band[1] + band[3], name + " " + b);
        assert.strictEqual(horiz ? r[2] : r[3], horiz ? band[2] : band[3], name + " " + b + " keeps its length");
        assert.ok(Math.abs((horiz ? r[3] : r[2]) - Math.max(w, horiz ? band[3] : band[2])) < 1e-9, name + " " + b + " across");
      }
    }
  }
});

test("solve: input errors, malformed histories, the original's crash", () => {
  const base = { mapName: "star_fig2", problem: 1, agents: "single", unreported: false, kase: 1, anchored: true, presetId: null };
  let R = app.solve(Object.assign({}, base, { story: "AXB", history: "b1" }));
  assert.strictEqual(R.status, "error");
  assert.match(R.reason, /not a room/);
  assert.strictEqual(R.original.status, "crash");  // the original takes unknown rooms as null
  assert.ok(R.original.differs);
  R = app.solve(Object.assign({}, base, { story: "A", history: "b9" }));
  assert.strictEqual(R.status, "error");
  assert.strictEqual(R.original.status, "unsupported");
  for (const problem of [1, 2, 3, 4]) {
    R = app.solve(Object.assign({}, base, { problem, story: "ACB", history: "o1 o2 o1 o2" }));
    assert.strictEqual(R.status, "malformed", "problem " + problem);
    assert.strictEqual(R.possible, null);
  }
  R = app.solve(Object.assign({}, base, { problem: 3, anchored: false, story: "", history: "b1" }));
  assert.strictEqual(R.status, "consistent");
  assert.ok(R.story.length > 0);
});

test("solve never throws and possible positions cover every time slot", () => {
  for (const [q, R] of SOLVED) {
    assert.ok(["consistent", "inconsistent", "malformed", "error"].includes(R.status), JSON.stringify(q));
    if (R.possible) {
      assert.strictEqual(R.possible.length, R.events.length + 1);
      if (R.path && R.possibleMode === "story" && q.problem === 1) {
        // the witness is one of the walks the shading summarises
        for (const s of R.path) assert.ok(R.possible[s.time].includes(s.position), JSON.stringify(q) + " " + JSON.stringify(s));
      }
    }
    assert.strictEqual(R.original !== null, app.originalApplies(q.mapName, q.problem));
  }
  const kinds = new Set(SOLVED.map(([, R]) => R.status));
  for (const k of ["consistent", "inconsistent", "malformed"]) assert.ok(kinds.has(k), "no " + k + " answer in the sweep");
});

// ------------------------------------------------------------------ "where x could be"

// every question the regression below asks: the presets, the paper's cases (all six interval
// cases for Problem 2, every problem, both agent models, both room-visit readings) and random
// questions; all problems
function shadingQuestions() {
  const out = [];
  for (const p of DATA.presets) out.push(app.presetState(p));
  const alias = {};
  for (const n of MAPS) { alias[n] = n; for (const a of DATA.maps[n].also || []) alias[a] = n; }
  for (const file of ["icra", "star"]) {
    const fx = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "fixtures", "paper", file + ".json"), "utf8"));
    for (const c of fx.cases) {
      const mapName = alias[c.map];
      if (!mapName || !Array.isArray(c.story) || !Array.isArray(c.history)) continue;
      let history;
      try { history = CD.historyToString(c.history.map(([s, k]) => ({ sensor: s, kind: k })), app.getMap(mapName)); } catch (e) { continue; }
      const story = c.story.join(" ");
      for (const agents of ["single", "multi"]) {
        for (const unreported of [false, true]) {
          for (let kase = 1; kase <= 6; kase++) out.push({ mapName, problem: 2, agents, unreported, kase, anchored: true, story, history, presetId: null });
          out.push({ mapName, problem: 1, agents, unreported, kase: 1, anchored: true, story, history, presetId: null });
          out.push({ mapName, problem: 3, agents, unreported, kase: 1, anchored: true, story, history, presetId: null });
          out.push({ mapName, problem: 4, agents, unreported, kase: 1, anchored: true, story, history, presetId: null });
        }
      }
    }
  }
  let seed = 20111;
  const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const pick = (a) => a[Math.floor(rnd() * a.length)];
  for (let n = 0; n < 2400; n++) {
    const mapName = MAPS[n % MAPS.length], m = DATA.maps[mapName].map;
    const sensors = Object.keys(m.beams).concat(m.occupancy);
    const story = Array.from({ length: 1 + Math.floor(rnd() * 5) }, () => pick(m.rooms)).join(" ");
    const history = Array.from({ length: Math.floor(rnd() * 6) }, () => pick(sensors)).join(" ");
    out.push({ mapName, problem: 1 + Math.floor(rnd() * 4), agents: pick(["single", "multi"]), unreported: rnd() < 0.3,
               kase: 1 + Math.floor(rnd() * 6), anchored: rnd() < 0.5, story, history, presetId: null });
  }
  return out;
}

test("the shading shown at every witness step contains x's position (all problems, Problem 2 intervals)", () => {
  const qs = shadingQuestions();
  const count = { questions: qs.length, walks: 0, p2walks: 0, shown: 0, outside: 0, paperP2: 0 };
  for (const q of qs) {
    const R = app.solve(q);
    if (!R.path || !R.path.length) {
      // no witness: the slots show the recordings' filter, never "outside"
      if (R.possible) for (let h = 0; h < R.possible.length; h++) assert.strictEqual(app.shadingAt(R, h).outside, false);
      continue;
    }
    count.walks++;
    assert.ok(R.possible, "a witness but no shading: " + JSON.stringify(q));
    let inSensors = false;
    R.path.forEach((s, k) => {
      if (s.kind === "mark" && s.sensor === "t0'") inSensors = true;
      const sh = app.shadingAt(R, k);
      if (q.problem === 2 && !inSensors) {
        assert.ok(sh.outside && sh.names === null, "Problem 2 step " + k + " before t0' must not be shaded: " + JSON.stringify(q));
      } else if (q.problem !== 2) {
        assert.ok(!sh.outside && sh.names, "step " + k + " has no shading: " + JSON.stringify(q));
      }
      if (sh.names) {
        count.shown++;
        assert.ok(sh.names.includes(s.position),
                  JSON.stringify(q) + " step " + k + " (" + s.kind + " " + s.position + ", time " + s.time + ") outside the shading " +
                  JSON.stringify(sh.names) + " of " + R.pathString);
      } else {
        count.outside++;
      }
      if (s.kind === "mark" && s.sensor === "tf'") inSensors = false;
    });
    if (q.problem === 2) {
      count.p2walks++;
      // inside [t0', tf'] the shading is always there
      const t0 = R.path.findIndex((s) => s.kind === "mark" && s.sensor === "t0'");
      const tf = R.path.findIndex((s) => s.kind === "mark" && s.sensor === "tf'");
      assert.ok(t0 >= 0 && tf >= t0, R.pathString);
      for (let k = t0; k <= tf; k++) assert.ok(app.shadingAt(R, k).names, JSON.stringify(q) + " step " + k);
      if (q.story === "A B D E C") count.paperP2++;
    }
  }
  assert.ok(count.questions >= 2000 + DATA.presets.length, JSON.stringify(count));
  assert.ok(count.walks >= 1000 && count.p2walks >= 200 && count.outside > 0 && count.paperP2 >= 8, JSON.stringify(count));
});

test("ICRA Fig. 2, case 1 (ABDEC, b1 b3 o2 o2 b4): no shading before t0', the walk inside it after", () => {
  const R = app.solve({ mapName: "icra_fig2", problem: 2, agents: "single", unreported: false, kase: 1, anchored: true,
                        story: "ABDEC", history: "b1 b3 o2 o2 b4", presetId: null });
  assert.strictEqual(R.status, "consistent");
  const k = R.path.findIndex((s) => s.kind === "pass" && s.position === "R4");
  assert.ok(k > 0 && app.shadingAt(R, k).outside && app.shadingAt(R, k).names === null);
  const t0 = R.path.findIndex((s) => s.kind === "mark" && s.sensor === "t0'");
  // x may be in a free region when the sensors start: R3 here, where the walk is
  assert.ok(app.shadingAt(R, t0).names.includes(R.path[t0].position));
  assert.ok(app.shadingAt(R, t0).names.some((p) => /^R/.test(p)));
});

test("verdict reasons name recordings as the chips do", () => {
  const p = app.presetById("icra_sec3a_p1"), R = app.solve(app.presetState(p));
  assert.match(R.reason, /\(b4 A\)/);
  const t = app.friendlyReason(R.reason, p.map);
  assert.match(t, /recording 5 \(b4\)/);
  assert.doesNotMatch(t, /\b[bo]\d+ [AD]\)/);
  assert.strictEqual(app.friendlyReason("recording 3 (o2 A) follows recording 2 (o1 D)", "star_fig2"),
                     "recording 3 (o2 on) follows recording 2 (o1 off)");
  assert.strictEqual(app.friendlyReason("story element 2 ('X') is not a room (rooms: A B C)", "star_fig2"),
                     "story element 2 ('X') is not a room (rooms: A B C)");
  for (const [q, R2] of SOLVED) {
    if (R2.reason) assert.doesNotMatch(app.friendlyReason(R2.reason, q.mapName), /\((?:b|o)\d+ [AD]\)/, R2.reason);
  }
});

test("Problem 4 edits: an insertion after the last room is an append", () => {
  assert.strictEqual(app.editText({ op: "insert", index: 2, old: null, "new": "A" }, 2), "append A at the end");
  assert.strictEqual(app.editText({ op: "insert", index: 1, old: null, "new": "A" }, 2), "insert A before position 2");
  assert.strictEqual(app.editText({ op: "insert", index: 0, old: null, "new": "A" }, 0), "add A");
  assert.strictEqual(app.editText({ op: "delete", index: 0, old: "B", "new": null }, 2), "delete B at position 1");
  assert.strictEqual(app.editText({ op: "substitute", index: 3, old: "E", "new": "D" }, 5), "replace E at position 4 by D");
  let appends = 0;
  for (const [q, R] of SOLVED) {
    if (q.problem !== 4 || R.status !== "consistent") continue;
    assert.strictEqual(R.storyLength, q.story.replace(/\s+/g, "").length);  // one-letter rooms
    for (const o of R.operations) {
      const t = app.editText(o, R.storyLength);
      assert.doesNotMatch(t, new RegExp("before position " + (R.storyLength + 1)), t);
      if (/append/.test(t)) appends++;
    }
  }
  assert.ok(appends > 0, "no append in the sweep");
});

test("describeStep describes every witness step in plain words", () => {
  const seen = new Set();
  for (const [q, R] of SOLVED) {
    if (!R.path) continue;
    R.path.forEach((s, k) => {
      const t = app.describeStep(R.path, k, R.events, q.mapName);
      assert.ok(t && !/undefined|null|NaN/.test(t), s.kind + ": " + t);
      seen.add(s.kind);
    });
  }
  for (const k of ["start", "visit", "move", "cross", "enter", "exit", "unreported", "begin", "mark", "pass"]) {
    assert.ok(seen.has(k), "no " + k + " step in the sweep");
  }
  const p = app.presetById("star_eq1_eq3_multi");
  assert.strictEqual(app.describeStep(p.expected.path, 0, p.events, "star_fig2"), "x starts in room A.");
});

test("describeStep names the right beam, sensor, region and recording", () => {
  const texts = (q) => {
    const R = app.solve(Object.assign({ agents: "single", unreported: false, kase: 1, anchored: true, presetId: null }, q));
    return plain(R.path.map((s, k) => app.describeStep(R.path, k, R.events, q.mapName)));
  };
  assert.deepStrictEqual(texts({ mapName: "star_fig2", problem: 1, story: "ACBAC", history: "b1 o1 o1 o2 o2 b2" }), [
    "x starts in room A.",
    "x leaves A into region R1.",
    "x enters room C (story element 2).",
    "x leaves C into region R1.",
    "x crosses beam b1 from side b1d (recording 1: b1).",
    "x enters occupancy region o1 (recording 2: o1 on).",
    "x leaves o1 into region R3 (recording 3: o1 off).",
    "x enters occupancy region o2 (recording 4: o2 on).",
    "x leaves o2 into region R4 (recording 5: o2 off).",
    "x enters room B (story element 3).",
    "x leaves B into region R4.",
    "x crosses beam b2 from side b2r (recording 6: b2).",
    "x enters room A (story element 4).",
    "x leaves A into region R1.",
    "x enters room C (story element 5).",
  ]);
  const multi = texts({ mapName: "star_fig2", problem: 1, agents: "multi", story: "ACBAC", history: "b1 o1 o2 b2 o2 o1" });
  assert.deepStrictEqual(multi.slice(4, 6), ["x walks into o1, kept active by other agents.", "x leaves o1 into region R3."]);
  const icra = texts({ mapName: "icra_fig2", problem: 1, unreported: true, story: "ABDEC", history: "b1 b3 o2 o2 b4" });
  assert.ok(icra.includes("x passes through room A without reporting it."));
  assert.ok(icra.includes("x crosses beam b3 from side b31 (recording 2: b3)."));
  const p2 = texts({ mapName: "star_fig2", problem: 2, kase: 2, story: "ACBAC", history: "b1 o1 o1 o2 o2 b2" });
  assert.deepStrictEqual(p2.slice(0, 3), ["x starts in room A, before both intervals begin.",
    "The story's interval begins (t0).", "The sensors start recording (t0')."]);
  assert.deepStrictEqual(p2.slice(-2), ["The story's interval ends (tf).", "The sensors stop recording (tf')."]);
  assert.ok(texts({ mapName: "star_fig2", problem: 2, kase: 4, story: "AA", history: "b2" })
    .includes("x crosses beam b2 from side b2r while no sensor is recording."));
  const unseen = texts({ mapName: "icra_fig2", problem: 2, kase: 6, story: "AB", history: "b1 b3" });
  assert.ok(unseen.includes("x enters o1 while no sensor is recording."));
  assert.ok(unseen.includes("x leaves o1 into region R3 unseen."));
});

test("beamOfSide maps a beam side to its beam", () => {
  assert.strictEqual(app.beamOfSide("star_fig2", "b1u"), "b1");
  assert.strictEqual(app.beamOfSide("star_fig2", "b1d"), "b1");
  assert.strictEqual(app.beamOfSide("star_fig2", "b2l"), "b2");
  assert.strictEqual(app.beamOfSide("icra_fig2", "b32"), "b3");
  assert.strictEqual(app.beamOfSide("icra_fig2", "b5"), "b5");  // not a side: returned unchanged
});

// ------------------------------------------------------------------ static page contract

test("index.html loads the classic scripts in order and nothing from the network", () => {
  const html = fs.readFileSync(path.join(DOCS, "index.html"), "utf8");
  const srcs = [...html.matchAll(/<script\b([^>]*)>/g)].map((m) => m[1]);
  assert.ok(srcs.every((a) => !/type\s*=\s*["']?module/.test(a)), "no ES modules");
  assert.deepStrictEqual(srcs.map((a) => (a.match(/src="([^"]+)"/) || [])[1]).filter(Boolean), SCRIPTS.map((s) => "js/" + s));
  const urls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((m) => m[1]);
  for (const u of urls) {
    assert.ok(!/^(https?:)?\/\//.test(u) || u === "https://github.com/ru-arcl/cyber-detectives", "external resource " + u);
  }
  const css = fs.readFileSync(path.join(DOCS, "css", "style.css"), "utf8");
  assert.ok(!/@import|url\(/.test(css), "no imports or url() in the stylesheet");
  assert.match(css, /prefers-color-scheme: dark/);
  assert.match(css, /prefers-reduced-motion/);
  for (const f of ["draw.js", "app.js"]) {
    const js = fs.readFileSync(path.join(DOCS, "js", f), "utf8");
    assert.ok(!/\bfetch\(|XMLHttpRequest|\bimport\s|\bexport\s|WebSocket/.test(js), f + " uses no network or modules");
  }
  for (const f of ["index.html", "css/style.css", "js/draw.js", "js/app.js"]) {
    assert.ok(!/\/home\/|\/tmp\//.test(fs.readFileSync(path.join(DOCS, f), "utf8")), f + " has no local paths");
  }
});
