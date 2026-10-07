/* Cyber Detectives demo: drawing helpers (classic script, no dependencies, no network).
 *
 * Load after docs/js/engine.js.  Adds CyberDetectives.draw:
 *   polylineForPath(entry, steps)   -> [[x, y], ...]   exact port of tools/gen_demo_data.py
 *                                      polyline_for_path (entry = CD_DATA.maps[name])
 *   tracePath(entry, steps)         -> {points, keys}: the same polyline, plus keys[i] = index
 *                                      of the point where step i happens
 *   regionCells(entry)              -> {R: [[x, y, w, h], ...]}: the free space of every region,
 *                                      as horizontal runs of an exact cell decomposition
 *   regionEdges(entry)              -> {R: [[x1, y1, x2, y2], ...]}: the outline of every region
 *   rectsPath(rects)                -> SVG path data for a union of rectangles
 *   makeTimeline(trace, opts)       -> {segs, total, steps}: playback time of every step
 *   sampleTimeline(tl, trace, t)    -> {point, step, index}
 *   stepTime(tl, k)                 -> playback time at which step k happens
 *   activeAfter(events, h, occ)     -> occupancy sensors active after the first h recordings
 *   renderMap(svg, entry, opts)     -> a view object (DOM only; see the function)
 * The pure functions are tested by tests/js/ui.test.js through node:vm.
 */
(function (root) {
  "use strict";

  var CD = root.CyberDetectives || (root.CyberDetectives = {});
  var SVGNS = "http://www.w3.org/2000/svg";
  var LABEL = "@label";

  function own(o, k) { return o !== null && o !== undefined && Object.prototype.hasOwnProperty.call(o, k); }

  // ------------------------------------------------------------------ witness -> polyline

  /** polyline_for_path with step bookkeeping: keys[i] is the index of the point at which step i
   *  happens (the doorway: inside the room/occupancy region on entry, on the region's side on
   *  exit; the beam; or where x stands).  Throws Error on a transition the data
   *  has no route for. */
  function tracePath(entry, steps) {
    var routes = entry.routes, interiors = entry.interiors, crossings = entry.crossings;
    var pts = [], keys = [];

    function addAll(seq) {
      for (var i = 0; i < seq.length; i++) {
        var p = seq[i], last = pts[pts.length - 1];
        if (!last || last[0] !== p[0] || last[1] !== p[1]) pts.push([p[0], p[1]]);
      }
    }
    function route(region, a, b) {
      if (a === b) return;
      var r = own(routes, region) ? routes[region].paths : null;
      if (!r || !own(r, a) || !own(r[a], b)) throw new Error("no route in " + region + " from " + a + " to " + b);
      addAll(r[a][b]);
    }

    if (!steps || !steps.length) return { points: pts, keys: keys };
    var pos = steps[0].position, anchor = null;
    if (own(interiors, pos)) addAll([interiors[pos].point]);
    else if (own(routes, pos)) { anchor = LABEL; addAll([routes[pos].anchors[LABEL]]); }
    else throw new Error("unknown start position " + pos);
    keys.push(0);
    for (var i = 1; i < steps.length; i++) {
      var st = steps[i], q = st.position, kind = st.kind, key;
      if (kind === "cross" || kind === "pass") {
        var c = own(crossings, st.sensor) ? crossings[st.sensor] : null;
        if (!c || c.from_region !== pos || c.to_region !== q || anchor === null) {
          throw new Error("cannot cross from " + pos + " at " + st.sensor + " into " + q);
        }
        route(pos, anchor, st.sensor);
        addAll(c.points);
        key = pts.length - 2;
        anchor = c.to_side;
      } else if (q === pos) {
        key = pts.length - 1;  // nothing to draw (e.g. a mark)
      } else if (own(interiors, pos) && own(routes, q)) {
        if (!own(interiors[pos].to, q)) throw new Error(pos + " does not open into " + q);
        addAll(interiors[pos].to[q]);
        key = pts.length - 1;  // x has left pos: draw it on q's side of the doorway
        anchor = pos;
      } else if (own(routes, pos) && own(interiors, q)) {
        if (!own(interiors[q].to, pos)) throw new Error(q + " does not open into " + pos);
        route(pos, anchor, q);
        addAll(interiors[q].to[pos].slice().reverse());
        key = pts.length - 2;
        anchor = null;
      } else {
        throw new Error("no move from " + pos + " to " + q + " (step kind " + kind + ")");
      }
      keys.push(Math.max(key, keys[keys.length - 1]));
      pos = q;
    }
    if (own(routes, pos)) route(pos, anchor, LABEL);
    return { points: pts, keys: keys };
  }

  function polylineForPath(entry, steps) { return tracePath(entry, steps).points; }

  // ------------------------------------------------------------------ region shading

  function inRect(r, x, y) { return x > r[0] && x < r[0] + r[2] && y > r[1] && y < r[1] + r[3]; }

  /** Obstacles of the drawing: walls (with caps), rooms, occupancy regions, beam bands. */
  function obstacles(d) {
    var obs = d.wall_rects.slice(), k;
    for (k in d.rooms) if (own(d.rooms, k)) obs.push(d.rooms[k]);
    for (k in d.occupancy) if (own(d.occupancy, k)) obs.push(d.occupancy[k]);
    for (k in d.beams) if (own(d.beams, k)) obs.push(d.beams[k].band);
    return obs;
  }

  function uniqSorted(a) {
    a.sort(function (p, q) { return p - q; });
    var out = [];
    for (var i = 0; i < a.length; i++) if (!out.length || a[i] !== out[out.length - 1]) out.push(a[i]);
    return out;
  }

  /** Cell decomposition of the free space: the grid on every obstacle edge, each free cell
   *  owned by the region whose label point it is 4-connected to (owner -1: none). */
  function decompose(entry) {
    var d = entry.drawing, bb = d.bounding_box.rect, obs = obstacles(d);
    var xs = [bb[0], bb[0] + bb[2]], ys = [bb[1], bb[1] + bb[3]];
    obs.forEach(function (r) {
      [r[0], r[0] + r[2]].forEach(function (x) { if (x > bb[0] && x < bb[0] + bb[2]) xs.push(x); });
      [r[1], r[1] + r[3]].forEach(function (y) { if (y > bb[1] && y < bb[1] + bb[3]) ys.push(y); });
    });
    xs = uniqSorted(xs); ys = uniqSorted(ys);
    var nx = xs.length - 1, ny = ys.length - 1, free = new Uint8Array(nx * ny), owner = new Int32Array(nx * ny);
    for (var j = 0; j < ny; j++) {
      for (var i = 0; i < nx; i++) {
        var cx = (xs[i] + xs[i + 1]) / 2, cy = (ys[j] + ys[j + 1]) / 2, blocked = false;
        for (var o = 0; o < obs.length && !blocked; o++) blocked = inRect(obs[o], cx, cy);
        free[j * nx + i] = blocked ? 0 : 1;
        owner[j * nx + i] = -1;
      }
    }
    function cellOf(v, cuts) {
      for (var i = 0; i < cuts.length - 1; i++) if (v >= cuts[i] && v < cuts[i + 1]) return i;
      return -1;
    }
    var names = Object.keys(d.regions), out = {};
    names.forEach(function (name, idx) {
      var p = d.regions[name].point, ci = cellOf(p[0], xs), cj = cellOf(p[1], ys);
      out[name] = [];
      if (ci < 0 || cj < 0 || !free[cj * nx + ci]) return;
      var stack = [cj * nx + ci];
      owner[cj * nx + ci] = idx;
      while (stack.length) {
        var c = stack.pop(), x = c % nx, y = (c - x) / nx;
        var nb = [x > 0 ? c - 1 : -1, x < nx - 1 ? c + 1 : -1, y > 0 ? c - nx : -1, y < ny - 1 ? c + nx : -1];
        for (var t = 0; t < 4; t++) {
          var n = nb[t];
          if (n >= 0 && free[n] && owner[n] === -1) { owner[n] = idx; stack.push(n); }
        }
      }
    });
    return { xs: xs, ys: ys, nx: nx, ny: ny, owner: owner, names: names, out: out };
  }

  /** Free space of each region: cells of the grid on every obstacle edge, flood-filled
   *  (4-connected) from the region's label point; returned as merged horizontal runs. */
  function regionCells(entry) {
    var g = decompose(entry), xs = g.xs, ys = g.ys, nx = g.nx, ny = g.ny, owner = g.owner, names = g.names, out = g.out;
    for (var row = 0; row < ny; row++) {
      var i0 = 0;
      while (i0 < nx) {
        var who = owner[row * nx + i0];
        var i1 = i0;
        while (i1 + 1 < nx && owner[row * nx + i1 + 1] === who) i1++;
        if (who >= 0) out[names[who]].push([xs[i0], ys[row], xs[i1 + 1] - xs[i0], ys[row + 1] - ys[row]]);
        i0 = i1 + 1;
      }
    }
    return out;
  }

  /** Outline of each region's free space: {R: [[x1, y1, x2, y2], ...]}, axis-parallel
   *  segments between a cell of R and a cell (or the outside) that is not R's, collinear
   *  neighbours merged.  The page strokes it so that "x could be here" does not rest on a
   *  faint fill alone. */
  function regionEdges(entry) {
    var g = decompose(entry), xs = g.xs, ys = g.ys, nx = g.nx, ny = g.ny, owner = g.owner, out = {};
    g.names.forEach(function (n) { out[n] = []; });
    function who(i, j) { return i < 0 || j < 0 || i >= nx || j >= ny ? -1 : owner[j * nx + i]; }
    var i, j, run;
    // horizontal edges on grid line j, for the cell below (side 0) and the cell above (side 1)
    for (j = 0; j <= ny; j++) {
      for (var side = 0; side < 2; side++) {
        run = null;
        for (i = 0; i <= nx; i++) {
          var a = i < nx ? who(i, side ? j - 1 : j) : -1, b = i < nx ? who(i, side ? j : j - 1) : -1;
          var r = a >= 0 && a !== b ? a : -1;
          if (run && run.r !== r) { out[g.names[run.r]].push([xs[run.i0], ys[j], xs[i], ys[j]]); run = null; }
          if (r >= 0 && !run) run = { r: r, i0: i };
        }
      }
    }
    for (i = 0; i <= nx; i++) {
      for (var side2 = 0; side2 < 2; side2++) {
        run = null;
        for (j = 0; j <= ny; j++) {
          var c = j < ny ? who(side2 ? i - 1 : i, j) : -1, d = j < ny ? who(side2 ? i : i - 1, j) : -1;
          var q = c >= 0 && c !== d ? c : -1;
          if (run && run.r !== q) { out[g.names[run.r]].push([xs[i], ys[run.j0], xs[i], ys[j]]); run = null; }
          if (q >= 0 && !run) run = { r: q, j0: j };
        }
      }
    }
    return out;
  }

  function num(v) { return String(Math.round(v * 100) / 100); }

  function segmentsPath(segs) {
    return segs.map(function (s) { return "M" + num(s[0]) + " " + num(s[1]) + "L" + num(s[2]) + " " + num(s[3]); }).join("");
  }

  function rectsPath(rects) {
    return rects.map(function (r) {
      return "M" + num(r[0]) + " " + num(r[1]) + "h" + num(r[2]) + "v" + num(r[3]) + "h" + num(-r[2]) + "z";
    }).join("");
  }

  // ------------------------------------------------------------------ playback timeline

  function dist(a, b) { return Math.sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1])); }

  /** Playback segments.  Segment k (1 <= k < steps) walks from the point of step k-1 to the
   *  point of step k; a last segment walks on to the end of the polyline.  Durations are in
   *  seconds at speed 1 (`speed` units per second, at least `minStep`). */
  function makeTimeline(trace, opts) {
    var speed = (opts && opts.speed) || 170, minStep = (opts && opts.minStep) || 0.3;
    var pts = trace.points, keys = trace.keys, segs = [], t = 0;
    function seg(step, i0, i1, min, tail) {
      var cum = [0], len = 0;
      for (var i = i0 + 1; i <= i1; i++) { len += dist(pts[i - 1], pts[i]); cum.push(len); }
      var dur = Math.max(min, len / speed);
      segs.push({ step: step, i0: i0, i1: i1, len: len, t0: t, t1: t + dur, cum: cum, tail: !!tail });
      t += dur;
    }
    for (var k = 1; k < keys.length; k++) seg(k, keys[k - 1], keys[k], minStep);
    if (keys.length && keys[keys.length - 1] < pts.length - 1) seg(keys.length - 1, keys[keys.length - 1], pts.length - 1, 0, true);
    return { segs: segs, total: t, steps: keys.length };
  }

  /** State at playback time t: x's point, `step` = the last step that has happened, and
   *  `index` = the polyline point x has most recently passed. */
  function sampleTimeline(tl, trace, t) {
    var pts = trace.points, segs = tl.segs;
    if (!pts.length) return { point: null, step: 0, index: 0 };
    if (!segs.length || t <= 0) return { point: pts[0].slice(), step: 0, index: 0 };
    for (var k = 0; k < segs.length; k++) {
      var sg = segs[k];
      if (t < sg.t1) {
        var f = (t - sg.t0) / (sg.t1 - sg.t0), target = f * sg.len, idx = 1;
        var done = sg.tail ? sg.step : sg.step - 1;
        if (sg.len === 0) return { point: pts[sg.i0].slice(), step: done, index: sg.i0 };
        while (idx < sg.cum.length - 1 && sg.cum[idx] < target) idx++;
        var a = pts[sg.i0 + idx - 1], b = pts[sg.i0 + idx];
        var segLen = sg.cum[idx] - sg.cum[idx - 1], g = segLen > 0 ? (target - sg.cum[idx - 1]) / segLen : 1;
        return { point: [a[0] + (b[0] - a[0]) * g, a[1] + (b[1] - a[1]) * g],
                 step: done, index: sg.i0 + idx - 1 };
      }
    }
    return { point: pts[pts.length - 1].slice(), step: tl.steps - 1, index: pts.length - 1 };
  }

  /** Playback time at which step k has just happened (the end of its segment). */
  function stepTime(tl, k) {
    if (k <= 0) return 0;
    for (var i = 0; i < tl.segs.length; i++) if (tl.segs[i].step === k && !tl.segs[i].tail) return tl.segs[i].t1;
    return tl.total;
  }

  /** Occupancy sensors (names in `occupancy`) active after the first h recordings
   *  ({sensor, kind} or [sensor, kind]). */
  function activeAfter(events, h, occupancy) {
    var on = {};
    for (var i = 0; i < Math.min(h, events.length); i++) {
      var e = events[i], s = e.sensor !== undefined ? e.sensor : e[0], k = e.kind !== undefined ? e.kind : e[1];
      if (occupancy.indexOf(s) < 0) continue;
      if (k === "A") on[s] = true; else delete on[s];
    }
    return on;
  }

  // ------------------------------------------------------------------ SVG view

  /** Where to write a room's name: the first corner (top left first) away from x's resting point (the
   *  interior point, where the marker stops), so the marker never hides the name. */
  function roomLabelPoint(rc, interior) {
    var dx = Math.min(34, rc[2] * 0.22), dy = Math.min(34, rc[3] * 0.22);
    var corners = [[rc[0] + dx + 6, rc[1] + dy + 6], [rc[0] + rc[2] - dx - 6, rc[1] + dy + 6],
                   [rc[0] + dx + 6, rc[1] + rc[3] - dy - 6], [rc[0] + rc[2] - dx - 6, rc[1] + rc[3] - dy - 6]];
    if (!interior) return corners[0];
    for (var i = 0; i < corners.length; i++) if (dist(corners[i], interior) >= 55) return corners[i];
    var best = corners[0], bestD = -1;
    corners.forEach(function (c) {
      var dd = dist(c, interior);
      if (dd > bestD + 1e-9) { best = c; bestD = dd; }
    });
    return best;
  }

  function segDist(p, a, b) {
    var vx = b[0] - a[0], vy = b[1] - a[1], L2 = vx * vx + vy * vy;
    var t = L2 > 0 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2)) : 0;
    return dist(p, [a[0] + t * vx, a[1] + t * vy]);
  }

  /** Where to write an occupancy region's name: like a room's, but the candidate (corners,
   *  then edge middles) farthest from x's resting point and the walks to the doorways
   *  (interior.to), so neither the marker nor the walk covers the name.  rc: rectangle;
   *  interior: {point, to: {R: [points]}} or null. */
  function occLabelPoint(rc, interior) {
    var mx = Math.min(30, rc[2] * 0.25), my = Math.min(24, rc[3] * 0.22);
    var x0 = rc[0] + mx, x1 = rc[0] + rc[2] - mx, y0 = rc[1] + my, y1 = rc[1] + rc[3] - my;
    var cx = rc[0] + rc[2] / 2, cy = rc[1] + rc[3] / 2;
    var cands = [[x0, y0], [x1, y0], [x0, y1], [x1, y1], [cx, y0], [cx, y1], [x0, cy], [x1, cy]];
    if (!interior) return cands[0];
    var lines = [];
    Object.keys(interior.to || {}).forEach(function (r) { lines.push(interior.to[r]); });
    function score(p) {
      var best = dist(p, interior.point);
      lines.forEach(function (pl) {
        for (var i = 1; i < pl.length; i++) best = Math.min(best, segDist(p, pl[i - 1], pl[i]));
      });
      return best;
    }
    var best = cands[0], bestS = -1;
    cands.forEach(function (c) {
      var sc = score(c);
      if (sc > bestS + 1e-9) { best = c; bestS = sc; }
    });
    return best;
  }

  /** Where to write the name of beam side s at font size fs: on the side's half, clear of the
   *  band, and moved along the beam towards its start (top or left) so that the walk, which
   *  crosses at the middle of the beam, passes beside the name (text is centred on the point). */
  function sideLabelPoint(bd, s, fs) {
    var L = bd.line, n = bd.normals[s], band = bd.band;
    var mid = [(L[0] + L[2]) / 2, (L[1] + L[3]) / 2];
    var half = n[0] !== 0 ? band[2] / 2 : band[3] / 2;
    var textHalf = n[0] !== 0 ? 0.31 * fs * s.length : 0.6 * fs;
    var off = half + textHalf + 3;
    var len = dist([L[0], L[1]], [L[2], L[3]]);
    var alongHalf = n[0] !== 0 ? 0.5 * fs : 0.31 * fs * s.length;  // the text's half extent along the beam
    // clear of x's marker (radius 13) at the crossing when the beam is long enough
    var shift = Math.max(0, Math.min(len / 2 - alongHalf, alongHalf + 15));
    var u = len > 0 ? [(L[0] - L[2]) / len, (L[1] - L[3]) / len] : [0, 0];
    if (u[0] > 0 || u[1] > 0) u = [-u[0], -u[1]];  // towards the top / left end
    return [mid[0] + n[0] * off + u[0] * shift, mid[1] + n[1] * off + u[1] * shift];
  }

  function el(name, attrs, parent) {
    var e = root.document.createElementNS(SVGNS, name);
    if (attrs) for (var k in attrs) if (own(attrs, k)) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function rectAttrs(r, cls) { return { x: r[0], y: r[1], width: r[2], height: r[3], "class": cls }; }
  /** r shrunk by d on every side (walls are drawn over the edges of rooms and regions, so a
   *  focus ring must sit inside them to be seen). */
  function inset(r, d) { return [r[0] + d, r[1] + d, Math.max(0, r[2] - 2 * d), Math.max(0, r[3] - 2 * d)]; }

  /** The clickable area of a beam: its band, widened across the beam to `across` units. */
  function beamHitRect(band, across) {
    var horiz = band[2] >= band[3], grow = Math.max(0, across - Math.min(band[2], band[3])) / 2;
    return horiz ? [band[0], band[1] - grow, band[2], band[3] + 2 * grow]
                 : [band[0] - grow, band[1], band[2] + 2 * grow, band[3]];
  }
  function setRect(e, r) {
    e.setAttribute("x", num(r[0])); e.setAttribute("y", num(r[1]));
    e.setAttribute("width", num(r[2])); e.setAttribute("height", num(r[3]));
  }
  function text(parent, x, y, s, cls) {
    var t = el("text", { x: x, y: y, "class": cls }, parent);
    t.textContent = s;
    return t;
  }
  /** A beam side's name with its suffix set apart (b2 + l, b2 + 1), so that the side l of b2
   *  never reads as side 1 of b2 (or as beam b21). */
  function sideText(parent, x, y, beam, side) {
    var t = el("text", { x: x, y: y, "class": "side-label" }, parent);
    if (side.indexOf(beam) === 0 && side.length > beam.length) {
      el("tspan", { "class": "side-beam" }, t).textContent = beam;
      el("tspan", { "class": "side-suffix" }, t).textContent = side.slice(beam.length);
    } else {
      t.textContent = side;
    }
    return t;
  }

  /**
   * Draw `entry` into `svg` (emptied first).  opts.onPick(kind, name) is called when a room,
   * beam or occupancy region is clicked (or activated with Enter/Space).
   * The returned view has:
   *   setRegionLabels(bool), setPossible([names] | null), setSensors({active: {o1:true},
   *   fired: name|null}), setTrail(points | null), setProgress(points travelled | null),
   *   setMarker([x, y] | null), setScale() (recompute label sizes after a resize).
   */
  function renderMap(svg, entry, opts) {
    var d = entry.drawing, bb = d.bounding_box.rect, pad = 4;
    var onPick = (opts && opts.onPick) || function () {};
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute("viewBox", [bb[0] - pad, bb[1] - pad, bb[2] + 2 * pad, bb[3] + 2 * pad].join(" "));
    var titleEl = el("title", null, svg);
    titleEl.textContent = entry.long_title;

    var gFloor = el("g", { "class": "layer-floor" }, svg);
    el("rect", rectAttrs(bb, "floor"), gFloor);
    var gRegions = el("g", { "class": "layer-regions" }, svg);
    var cells = regionCells(entry), edges = regionEdges(entry), regionEls = {};
    Object.keys(cells).forEach(function (r) {
      var rg = el("g", { "class": "region", "data-name": r }, gRegions);
      el("path", { d: rectsPath(cells[r]), "class": "region-fill" }, rg);
      el("path", { d: segmentsPath(edges[r]), "class": "region-edge" }, rg);
      regionEls[r] = rg;
    });

    var gFeatures = el("g", { "class": "layer-features" }, svg);
    var featureEls = {}, labelEls = {};
    function pickable(g, kind, name, label) {
      g.setAttribute("class", g.getAttribute("class") + " hit");
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", label);
      g.setAttribute("data-kind", kind);
      g.setAttribute("data-name", name);
      var t = el("title", null, g);
      t.textContent = label;
      g.addEventListener("click", function () { onPick(kind, name); });
      g.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onPick(kind, name); }
      });
    }
    Object.keys(d.rooms).forEach(function (r) {
      var rc = d.rooms[r], g = el("g", { "class": "room" }, gFeatures);
      el("rect", rectAttrs(rc, "room-fill"), g);
      el("rect", rectAttrs(inset(rc, 4), "poss-ring"), g);  // "x could be here" frame
      el("rect", Object.assign(rectAttrs(inset(rc, 9), "focus-ring"), { rx: 4 }), g);
      var lp = roomLabelPoint(rc, entry.interiors[r] ? entry.interiors[r].point : null);
      labelEls[r] = text(g, lp[0], lp[1], r, "room-label");
      featureEls[r] = g;
      pickable(g, "room", r, "Room " + r + ": add to the story");
    });
    Object.keys(d.occupancy).forEach(function (o) {
      var rc = d.occupancy[o], g = el("g", { "class": "occ" }, gFeatures);
      el("rect", rectAttrs(rc, "occ-fill"), g);
      el("rect", rectAttrs(rc, "occ-poss"), g);  // "x could be here" tint, over the off fill
      el("rect", rectAttrs(inset(rc, 4), "occ-poss-ring poss-ring"), g);  // and a frame (also when the sensor is on)
      el("rect", Object.assign(rectAttrs(inset(rc, 9), "focus-ring"), { rx: 4 }), g);
      var olp = occLabelPoint(rc, entry.interiors[o] || null);
      labelEls[o] = text(g, olp[0], olp[1], o, "occ-label");
      featureEls[o] = g;
      pickable(g, "occupancy", o, "Occupancy sensor " + o + ": add a recording (toggles on / off)");
    });

    var gWalls = el("g", { "class": "layer-walls" }, svg);
    el("path", { d: rectsPath(d.wall_rects), "class": "walls" }, gWalls);

    var gBeams = el("g", { "class": "layer-beams" }, svg);
    var beamEls = {}, sideLabels = [], beamHits = [];
    Object.keys(d.beams).forEach(function (b) {
      var bd = d.beams[b], g = el("g", { "class": "beam" }, gBeams), band = bd.band;
      // hit area: the band, widened to at least 26 units across (more on phones, see setScale)
      var hit = beamHitRect(band, 26);
      var hitEl = el("rect", rectAttrs(hit, "beam-hit"), g);
      el("rect", rectAttrs(band, "beam-band"), g);
      var ringEl = el("rect", Object.assign(rectAttrs(hit, "focus-ring"), { rx: 4 }), g);
      beamHits.push({ band: band, hit: hitEl, ring: ringEl });
      var L = bd.line;
      el("line", { x1: L[0], y1: L[1], x2: L[2], y2: L[3], "class": "beam-glow" }, g);
      el("line", { x1: L[0], y1: L[1], x2: L[2], y2: L[3], "class": "beam-line" }, g);
      bd.sides.forEach(function (s) {
        var p = bd.side_labels[s];
        sideLabels.push({ el: sideText(g, p[0], p[1], b, s), name: s, beam: bd });
      });
      beamEls[b] = g;
      pickable(g, "beam", b, "Beam " + b + ": add a recording");
    });

    var gLabels = el("g", { "class": "layer-region-labels", visibility: "hidden" }, svg);
    Object.keys(d.regions).forEach(function (r) {
      var p = d.regions[r].point;
      text(gLabels, p[0], p[1], r, "region-label");
    });

    var gPath = el("g", { "class": "layer-path" }, svg);
    var trail = el("polyline", { "class": "trail", points: "" }, gPath);
    var travelled = el("polyline", { "class": "travelled", points: "" }, gPath);
    var marker = el("g", { "class": "marker", visibility: "hidden" }, gPath);
    el("circle", { cx: 0, cy: 0, r: 13, "class": "marker-halo" }, marker);
    el("circle", { cx: 0, cy: 0, r: 9, "class": "marker-dot" }, marker);
    text(marker, 0, 0.5, "x", "marker-label");

    function ptsAttr(points) {
      return points ? points.map(function (p) { return num(p[0]) + "," + num(p[1]); }).join(" ") : "";
    }
    function toggleClass(e, cls, on) {
      var c = (e.getAttribute("class") || "").split(/\s+/).filter(function (x) { return x; });
      var has = c.indexOf(cls) >= 0;
      if (has === !!on) return;  // unchanged: no DOM write (this runs on every animation frame)
      c = c.filter(function (x) { return x !== cls; });
      if (on) c.push(cls);
      e.setAttribute("class", c.join(" "));
    }

    var view = {
      regionCells: cells,
      setRegionLabels: function (on) { gLabels.setAttribute("visibility", on ? "visible" : "hidden"); },
      setPossible: function (names) {
        var set = {};
        (names || []).forEach(function (n) { set[n] = true; });
        Object.keys(regionEls).forEach(function (r) { toggleClass(regionEls[r], "possible", !!set[r]); });
        Object.keys(featureEls).forEach(function (f) { toggleClass(featureEls[f], "possible", !!set[f]); });
      },
      setSensors: function (s) {
        var active = (s && s.active) || {}, fired = s && s.fired;
        Object.keys(d.occupancy).forEach(function (o) {
          toggleClass(featureEls[o], "active", !!active[o]);
          toggleClass(featureEls[o], "fired", fired === o);
        });
        Object.keys(beamEls).forEach(function (b) { toggleClass(beamEls[b], "fired", fired === b); });
      },
      setTrail: function (points) { trail.setAttribute("points", ptsAttr(points)); },
      setProgress: function (points) { travelled.setAttribute("points", ptsAttr(points)); },
      setMarker: function (p) {
        if (!p) { marker.setAttribute("visibility", "hidden"); return; }
        marker.setAttribute("visibility", "visible");
        marker.setAttribute("transform", "translate(" + num(p[0]) + " " + num(p[1]) + ")");
      },
      setScale: function () {
        // keep small labels readable when the map is drawn narrow (phones)
        var w = svg.getBoundingClientRect().width || bb[2];
        var unitsPerPx = (bb[2] + 2 * pad) / w;
        svg.style.setProperty("--fs-side", Math.max(12, 10 * unitsPerPx).toFixed(1) + "px");
        svg.style.setProperty("--fs-occ", Math.max(17, 12 * unitsPerPx).toFixed(1) + "px");
        svg.style.setProperty("--fs-region", Math.max(15, 11 * unitsPerPx).toFixed(1) + "px");
        svg.style.setProperty("--fs-room", Math.max(34, 16 * unitsPerPx).toFixed(1) + "px");
        var fsSide = Math.max(12, 10 * unitsPerPx);
        // beam tap targets at least ~34 css px across (capped so they stay in their corridor)
        var across = Math.min(44, Math.max(26, 34 * unitsPerPx));
        beamHits.forEach(function (bh) {
          var r = beamHitRect(bh.band, across);
          setRect(bh.hit, r);
          setRect(bh.ring, r);
        });
        sideLabels.forEach(function (sl) {
          var q = sideLabelPoint(sl.beam, sl.name, fsSide);
          sl.el.setAttribute("x", num(q[0]));
          sl.el.setAttribute("y", num(q[1]));
        });
        marker.firstChild.setAttribute("r", (13 * Math.max(1, 0.75 * unitsPerPx)).toFixed(1));
        marker.childNodes[1].setAttribute("r", (9 * Math.max(1, 0.75 * unitsPerPx)).toFixed(1));
        marker.childNodes[2].style.fontSize = (13 * Math.max(1, 0.75 * unitsPerPx)).toFixed(1) + "px";
      }
    };
    return view;
  }

  CD.draw = {
    LABEL: LABEL,
    tracePath: tracePath,
    polylineForPath: polylineForPath,
    obstacles: obstacles,
    regionCells: regionCells,
    regionEdges: regionEdges,
    rectsPath: rectsPath,
    makeTimeline: makeTimeline,
    sampleTimeline: sampleTimeline,
    stepTime: stepTime,
    activeAfter: activeAfter,
    roomLabelPoint: roomLabelPoint,
    occLabelPoint: occLabelPoint,
    sideLabelPoint: sideLabelPoint,
    beamHitRect: beamHitRect,
    renderMap: renderMap
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
