/* Cyber Detectives demo: the page (classic script, no dependencies, no network).
 *
 * Load last, after data.js, engine.js, original.js and draw.js.  The pure helpers are exported
 * as CyberDetectives.app (tested by tests/js/ui.test.js through node:vm); the page itself starts
 * only when a document is present.
 */
(function (root) {
  "use strict";

  var CD = root.CyberDetectives;
  var DATA = root.CD_DATA;
  if (!CD || !CD.draw || !DATA) throw new Error("load data.js, engine.js, original.js and draw.js before app.js");

  var DEFAULT_INPUTS = {
    star_fig2: { story: "ACBAC", history: "b1 o1 o1 o2 o2 b2" },
    icra_fig2: { story: "ABCDE", history: "b1 b3 o2 o2" }
  };
  var ORIGINAL_MAPS = ["star_fig2", "icra_fig1"];  // the applet's workspace

  // ================================================================== pure helpers

  var mapCache = {};
  function getMap(name) {
    if (!Object.prototype.hasOwnProperty.call(mapCache, name)) {
      mapCache[name] = CD.Map.fromDict(Object.assign({ name: name }, DATA.maps[name].map));
    }
    return mapCache[name];
  }

  function defaultInputs(mapName) {
    if (DEFAULT_INPUTS[mapName]) return DEFAULT_INPUTS[mapName];
    var m = DATA.maps[mapName].map;
    return { story: m.rooms[0], history: "" };
  }

  /** Append a room to the story text; one-letter rooms are written without separators unless
   *  the text already uses them. */
  function appendStory(text, room, rooms) {
    var t = String(text || "").replace(/\s+$/, "");
    var compact = rooms.every(function (r) { return r.length === 1; }) && !/[\s,]/.test(t);
    if (!t) return room;
    return compact && room.length === 1 ? t + room : t + " " + room;
  }

  function appendHistory(text, sensor) {
    var t = String(text || "").replace(/[\s,]+$/, "");
    return t ? t + " " + sensor : sensor;
  }

  function isMalformedReason(reason) { return typeof reason === "string" && reason.indexOf("malformed history: ") === 0; }

  function originalApplies(mapName, problem) {
    if (problem !== 1) return false;
    var e = DATA.maps[mapName];
    return ORIGINAL_MAPS.indexOf(mapName) >= 0 || (e.also || []).some(function (a) { return ORIGINAL_MAPS.indexOf(a) >= 0; });
  }

  function presetById(id) {
    for (var i = 0; i < DATA.presets.length; i++) if (DATA.presets[i].id === id) return DATA.presets[i];
    return null;
  }

  /** The controls' state that a preset sets. */
  function presetState(p) {
    return {
      mapName: p.map, problem: p.problem, agents: p.agents,
      unreported: !!p.options.unreported_visits,
      kase: p.options["case"] || 1,
      anchored: p.options.anchored === undefined ? true : !!p.options.anchored,
      story: p.story, history: p.history, presetId: p.id
    };
  }

  function sameQuestion(a, b) {
    var keys = ["mapName", "problem", "agents", "unreported", "story", "history"];
    for (var i = 0; i < keys.length; i++) if (a[keys[i]] !== b[keys[i]]) return false;
    if (a.problem === 2 && a.kase !== b.kase) return false;
    if (a.problem === 3 && a.anchored !== b.anchored) return false;
    return true;
  }

  function errorText(e) { return (e && e.message) ? e.message : String(e); }

  /** Read a walk in path notation (DESIGN.md "Path notation") into tokens {t, v}: t is "room",
   *  or the opening bracket ("[", "{", "(", "<", "|").  Returns null when it cannot be read. */
  function parseWalkString(text, rooms) {
    var names = rooms.slice().sort(function (a, b) { return b.length - a.length; });
    var closing = { "[": "]", "{": "}", "(": ")", "<": ">", "|": "|" }, toks = [], i = 0;
    while (i < text.length) {
      var c = text.charAt(i);
      if (own(closing, c)) {
        var j = text.indexOf(closing[c], i + 1);
        if (j < 0) return null;
        toks.push({ t: c, v: text.slice(i + 1, j) });
        i = j + 1;
        continue;
      }
      var hit = null;
      for (var k = 0; k < names.length && !hit; k++) if (text.substr(i, names[k].length) === names[k]) hit = names[k];
      if (!hit) return null;
      toks.push({ t: "room", v: hit });
      i += hit.length;
    }
    return toks;
  }

  function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

  /**
   * Structural check of a single-agent walk string such as the original's getAgentStory output:
   * its rooms must spell the story and its bracketed tokens must match the recordings one to one
   * (an occupancy deactivation has no token).  Returns null when the walk passes, or
   * {reason, b3}; b3 is the B3 detector of tests/test_default_vs_original.py (more bracketed
   * crossings than recordings, and two consecutive recordings of the same beam).  Passing does
   * not prove the walk legal (adjacency and timing are not checked).
   */
  function checkWalkString(map, story, history, text) {
    var toks = parseWalkString(String(text), map.rooms);
    if (!toks) return { reason: "the walk " + text + " cannot be read", b3: false };
    var storyL = CD.parseStory(story, map), events = CD.parseHistory(history, map);
    var recs = events.filter(function (e) { return !(map.isOccupancy(e.sensor) && e.kind === "D"); });
    var rooms = toks.filter(function (t) { return t.t === "room"; }).map(function (t) { return t.v; });
    var brs = toks.filter(function (t) { return t.t === "["; }).map(function (t) { return t.v; });
    var sameBeamTwice = recs.some(function (e, i) { return i > 0 && map.isBeam(e.sensor) && recs[i - 1].sensor === e.sensor; });
    if (brs.length !== recs.length) {
      return { reason: "the walk has " + brs.length + " bracketed recording" + (brs.length === 1 ? "" : "s") + " for " +
                       recs.length + " recording" + (recs.length === 1 ? "" : "s") + ", so x cannot have walked it",
               b3: brs.length > recs.length && sameBeamTwice };
    }
    if (rooms.join(" ") !== storyL.join(" ")) {
      return { reason: "the rooms of the walk, " + rooms.join("") + ", do not spell the story " + storyL.join(""), b3: false };
    }
    for (var i = 0; i < recs.length; i++) {
      var e = recs[i], ok = map.isBeam(e.sensor) ? map.beams.get(e.sensor).indexOf(brs[i]) >= 0 : brs[i] === e.sensor;
      if (!ok) return { reason: "the walk's token [" + brs[i] + "] does not match recording " + (events.indexOf(e) + 1) + " (" + e.sensor + ")", b3: false };
    }
    return null;
  }

  /** Run the original's algorithm (Problem 1 on the applet's map). */
  function runOriginal(map, st, verdict) {
    var o = { status: null, pathString: null, message: null, differs: false, bug: null, note: null, walkProblem: null };
    try {
      var r = CD.validate(map, st.story, st.history, { agents: st.agents, compat: "original" });
      o.status = r.consistent ? "consistent" : "inconsistent";
      o.pathString = r.path_string;
    } catch (e) {
      if (CD.original && e instanceof CD.original.JavaException) {
        o.status = "crash";
        o.message = e.message;
      } else {
        o.status = "unsupported";
        o.message = errorText(e);
      }
    }
    if (o.status === "crash") o.differs = true;
    else if (o.status === "consistent" || o.status === "inconsistent") {
      var ov = o.status === "consistent";
      if (verdict === null) o.differs = true;
      else if (ov !== verdict) o.differs = true;
      else if (ov && verdict && st.agents === "single") {
        o.note = "same verdict";
        // the verdicts agree, but the original's walk may still be impossible (B3)
        var bad = o.pathString ? checkWalkString(map, st.story, st.history, o.pathString) : null;
        if (bad) {
          o.differs = true;
          o.note = null;
          o.walkProblem = bad.reason;
          if (bad.b3) o.bug = presetById("bug_B3").bug;
        }
      }
    }
    var p = st.presetId ? presetById(st.presetId) : null;
    if (p && p.bug && sameQuestion(presetState(p), st)) {
      o.bug = p.bug;
      o.differs = true;
    }
    return o;
  }

  /**
   * Answer the question in `st` ({mapName, problem, agents, unreported, kase, anchored, story,
   * history, presetId}).  Returns
   *   {status: "consistent"|"inconsistent"|"malformed"|"error", headline, reason, pathString,
   *    path, story: [rooms] | null (the story the witness tells), inserted, operations,
   *    interval, events: [{sensor, kind}] | null, possible: [[pos]] | null,
   *    possibleMode: "story"|"sensors", storyLength, original: null | {...}}
   * `possible[h]` is where x could be after the first h recordings: given the story and the
   * recordings when the answer tells a story (Problems 1, 3, 4), else the recordings alone.  For
   * Problem 2 it covers only the sensors' interval [t0', tf'] (x may already be on its way at
   * t0', so it may start in a free region); shadingAt says what to show at each witness step.
   */
  function solve(st) {
    var map = getMap(st.mapName);
    var R = { status: "error", headline: "", reason: null, pathString: null, path: null, story: null,
              inserted: [], operations: null, interval: null, events: null, possible: null,
              possibleMode: "sensors", storyLength: 0, original: null, problem: st.problem, agents: st.agents };
    var opts = { agents: st.agents, unreportedVisits: !!st.unreported };
    var verdict = null;
    try {
      R.events = CD.parseHistory(st.history, map).map(function (e) { return { sensor: e.sensor, kind: e.kind }; });
      var storyList = null;
      if (st.problem === 1 || st.problem === 2 || String(st.story).trim() !== "") storyList = CD.parseStory(st.story, map);
      R.storyLength = storyList ? storyList.length : 0;
      var r = null, filterStory = null;
      if (st.problem === 1 || st.problem === 2) {
        r = st.problem === 1 ? CD.validate(map, st.story, st.history, opts)
                             : CD.validateIntervals(map, st.story, st.history, Object.assign({ "case": st.kase }, opts));
        verdict = !!r.consistent;
        R.reason = r.reason;
        R.pathString = r.path_string;
        R.path = r.path;
        R.story = storyList;
        if (st.problem === 2) R.interval = r.interval;
        if (r.consistent) { R.status = "consistent"; R.headline = "Consistent"; if (st.problem === 1) filterStory = storyList; }
        else if (isMalformedReason(r.reason)) { R.status = "malformed"; R.headline = "Malformed history"; }
        else { R.status = "inconsistent"; R.headline = "Inconsistent"; }
      } else {
        r = st.problem === 3 ? CD.shortestSuperstory(map, st.story, st.history, Object.assign({ anchored: !!st.anchored }, opts))
                             : CD.closestStory(map, st.story, st.history, opts);
        if (r === null) {
          var bad = CD.checkHistory(map, CD.parseHistory(st.history, map), st.agents);
          if (bad) { R.status = "malformed"; R.headline = "Malformed history"; R.reason = "malformed history: " + bad; }
          else {
            R.status = "inconsistent";
            R.headline = st.problem === 3 ? "No consistent super-story" : "No consistent story";
            R.reason = st.problem === 3
              ? (st.anchored ? "no consistent story starts with the first room, ends with the last room and contains the story in order"
                             : "no consistent story contains the story in order")
              : "no story at all explains the recordings";
          }
          R.story = storyList;
        } else {
          R.status = "consistent";
          R.story = r.story;
          R.pathString = r.path_string;
          R.path = r.path;
          filterStory = r.story;
          if (st.problem === 3) {
            R.inserted = r.inserted;
            R.headline = r.inserted.length ? "Shortest consistent story" : "Consistent as told";
          } else {
            R.operations = r.edit_ops;
            R.headline = r.edits ? "Closest consistent story" : "Consistent as told";
          }
        }
      }
      if (R.status !== "malformed") {
        R.possibleMode = filterStory ? "story" : "sensors";
        // without a story x starts in a room (as every story does), except in Problem 2: the
        // sensors' interval may begin while x is already on its way
        R.possible = CD.possiblePositions(map, st.history, {
          story: filterStory, agents: st.agents, unreportedVisits: !!st.unreported,
          starts: filterStory || st.problem !== 2 ? "rooms" : "anywhere"
        });
        if (!R.possible.some(function (l) { return l.length; })) R.possible = null;
      }
    } catch (e) {
      R.status = "error";
      R.headline = "Input error";
      R.reason = errorText(e);
      R.path = null;
    }
    if (originalApplies(st.mapName, st.problem)) R.original = runOriginal(map, st, verdict);
    return R;
  }

  function kindOf(mapName, name) {
    try { return getMap(mapName).kind(name); } catch (e) { return null; }
  }

  /** One-line description of witness step k ({kind, position, sensor, event, story_index}). */
  function describeStep(path, k, events, mapName) {
    var s = path[k], prev = k > 0 ? path[k - 1] : null;
    var rec = function () {
      if (s.event === null || s.event === undefined || !events || !events[s.event]) return "";
      var e = events[s.event];
      return " (recording " + (s.event + 1) + ": " + e.sensor + (kindOf(mapName, e.sensor) === "occupancy" ? (e.kind === "D" ? " off" : " on") : "") + ")";
    };
    switch (s.kind) {
      case "start": return "x starts in room " + s.position + ".";
      case "begin": return "x starts in room " + s.position + ", before both intervals begin.";
      case "visit": return "x enters room " + s.position + " (story element " + s.story_index + ").";
      case "unreported": return "x passes through room " + s.position + " without reporting it.";
      case "cross": return "x crosses beam " + beamOfSide(mapName, s.sensor) + " from side " + s.sensor + rec() + ".";
      case "pass": return "x crosses beam " + beamOfSide(mapName, s.sensor) + " from side " + s.sensor + " while no sensor is recording.";
      case "enter": return "x enters occupancy region " + s.position + rec() + ".";
      case "exit": return "x leaves " + s.sensor + " into region " + s.position + rec() + ".";
      case "unseen": return prev && s.position === s.sensor
        ? "x enters " + s.position + " while no sensor is recording."
        : "x leaves " + (s.sensor || (prev && prev.position)) + " into region " + s.position + " unseen.";
      case "mark": return {
        "t0": "The story's interval begins (t0).", "tf": "The story's interval ends (tf).",
        "t0'": "The sensors start recording (t0').", "tf'": "The sensors stop recording (tf')."
      }[s.sensor] || "Interval boundary " + s.sensor + ".";
      case "move":
        if (s.sensor && s.position === s.sensor) return "x walks into " + s.position + ", kept active by other agents.";
        if (s.sensor) return "x leaves " + s.sensor + " into region " + s.position + ".";
        return "x leaves " + (prev ? prev.position : "") + " into region " + s.position + ".";
      default: return s.kind + " " + s.position;
    }
  }

  /**
   * What the "where x could be" shading shows at witness step k of R.path (or, with no witness,
   * after k recordings): {names: [positions] | null, outside: bool}.  names is null when nothing
   * is shaded; outside is true at Problem 2 steps outside the sensors' interval [t0', tf'], where
   * the recordings say nothing about x (and the story, outside [t0, tf], says nothing either).
   * Every witness position lies inside the names shown for its step (tests/js/ui.test.js).
   */
  function shadingAt(R, k) {
    if (!R || !R.possible) return { names: null, outside: false };
    if (!R.path || !R.path.length) {
      return { names: k >= 0 && k < R.possible.length ? R.possible[k] : null, outside: false };
    }
    var s = R.path[Math.max(0, Math.min(R.path.length - 1, k))];
    if (R.problem === 2) {
      var inside = false;
      for (var i = 0; i <= k && i < R.path.length; i++) {
        var p = R.path[i];
        if (p.kind === "mark" && p.sensor === "t0'") inside = true;
        // the step that marks tf' is still inside: x stands where the recordings left it
        if (p.kind === "mark" && p.sensor === "tf'" && i < k) inside = false;
      }
      if (!inside) return { names: null, outside: true };
    }
    return { names: R.possible[s.time] || null, outside: false };
  }

  /** The engine names a recording "(o2 A)"; the page writes it as the history chips do:
   *  "(b4)" for a beam, "(o2 on)" / "(o2 off)" for an occupancy sensor. */
  function friendlyReason(reason, mapName) {
    if (typeof reason !== "string") return reason;
    return reason.replace(/\(([A-Za-z0-9_]+) ([AD])\)/g, function (all, name, kind) {
      var k = kindOf(mapName, name);
      if (k === "occupancy") return "(" + name + (kind === "A" ? " on" : " off") + ")";
      if (k === "beam") return kind === "A" ? "(" + name + ")" : "(" + name + " deactivated)";
      return all;
    });
  }

  /** One Problem 4 edit ({op, index, old, new}; index counts the story elements before it) in
   *  words, for a story of storyLength rooms. */
  function editText(o, storyLength) {
    if (o.op === "insert") {
      if (storyLength === 0) return "add " + o["new"];
      if (o.index >= storyLength) return "append " + o["new"] + " at the end";
      return "insert " + o["new"] + " before position " + (o.index + 1);
    }
    if (o.op === "delete") return "delete " + o.old + " at position " + (o.index + 1);
    return "replace " + o.old + " at position " + (o.index + 1) + " by " + o["new"];
  }

  function beamOfSide(mapName, side) {
    var beams = DATA.maps[mapName].map.beams;
    for (var b in beams) if (beams[b].indexOf(side) >= 0) return b;
    return side;
  }

  // ================================================================== page

  function startPage(doc, win) {
    var $ = function (id) { return doc.getElementById(id); };
    var reduceMotion = false;
    try { reduceMotion = !!(win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (e) { /* ignore */ }

    var els = {
      map: $("map"), mapTitle: $("map-title"), preset: $("preset"), presetCaption: $("preset-caption"),
      mapSelect: $("map-select"), problem: $("problem"), kase: $("case"), caseField: $("case-field"),
      anchored: $("anchored"), anchoredField: $("anchored-field"), unreported: $("unreported"),
      story: $("story"), history: $("history"), undo: $("undo"), clear: $("clear"), form: $("controls"),
      result: $("result"), original: $("original"), stale: $("stale-note"),
      storyChips: $("story-chips"), historyChips: $("history-chips"),
      play: $("play"), back: $("step-back"), fwd: $("step-fwd"), scrub: $("scrub"), speed: $("speed"),
      caption: $("step-caption"), showPossible: $("show-possible"), showRegions: $("show-regions"),
      theme: $("theme-toggle"), mapFrame: doc.querySelector(".map-frame")
    };

    var st = { mapName: DATA.map_order[0], problem: 1, agents: "single", unreported: false, kase: 1, anchored: true,
               story: "", history: "", presetId: null };
    var undoStack = [];
    var view = null, result = null, resultFor = null;
    var pb = { mode: "none", trace: null, tl: null, t: 0, playing: false, last: 0, raf: 0, slots: 0, slot: 0, tick: 0,
               drawError: null, possKey: null };

    // ---------------------------------------------------------------- theme
    function systemDark() {
      try { return !!(win.matchMedia && win.matchMedia("(prefers-color-scheme: dark)").matches); } catch (e) { return false; }
    }
    function effectiveTheme() {
      var t = doc.documentElement.getAttribute("data-theme");
      return t === "light" || t === "dark" ? t : (systemDark() ? "dark" : "light");
    }
    function syncThemeButton() {
      var dark = effectiveTheme() === "dark";
      els.theme.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      els.theme.querySelector(".theme-label").textContent = dark ? "Light" : "Dark";
      els.theme.classList.toggle("is-dark", dark);
    }
    els.theme.addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      doc.documentElement.setAttribute("data-theme", next);
      try { win.localStorage.setItem("cd-theme", next); } catch (e) { /* not persisted */ }
      syncThemeButton();
    });
    try {
      var mq = win.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", syncThemeButton);
    } catch (e) { /* ignore */ }

    // ---------------------------------------------------------------- controls
    function option(sel, value, label) {
      var o = doc.createElement("option");
      o.value = value; o.textContent = label;
      sel.appendChild(o);
      return o;
    }
    DATA.map_order.forEach(function (n) { option(els.mapSelect, n, DATA.maps[n].title); });
    Object.keys(CD.INTERVAL_CASES).forEach(function (k) { option(els.kase, k, k + ": " + CD.INTERVAL_CASES[k]); });
    option(els.preset, "", "Your own question");
    var groups = [["From the papers", function (p) { return !p.bug; }],
                  ["Bugs of the original 2010 code", function (p) { return !!p.bug; }]];
    groups.forEach(function (g) {
      var og = doc.createElement("optgroup");
      og.label = g[0];
      DATA.presets.filter(g[1]).forEach(function (p) { option(og, p.id, p.title); });
      els.preset.appendChild(og);
    });

    function writeControls() {
      els.mapSelect.value = st.mapName;
      els.problem.value = String(st.problem);
      els.kase.value = String(st.kase);
      els.anchored.value = st.anchored ? "1" : "0";
      els.unreported.checked = !!st.unreported;
      Array.prototype.forEach.call(doc.querySelectorAll('input[name="agents"]'), function (r) { r.checked = r.value === st.agents; });
      els.story.value = st.story;
      els.history.value = st.history;
      els.preset.value = st.presetId || "";
      els.caseField.hidden = st.problem !== 2;
      els.anchoredField.hidden = st.problem !== 3;
      var p = st.presetId ? presetById(st.presetId) : null;
      els.presetCaption.textContent = p ? p.caption + (p.ref ? " (" + p.ref + ")" : "") : "";
      els.presetCaption.hidden = !p;
      els.mapTitle.textContent = DATA.maps[st.mapName].long_title;
    }

    /** The answer no longer matches the inputs: dim it (panel and walk), stop playback, hide
     *  the shading and show the inputs in the chips; undone edits bring it all back. */
    function markStale() {
      var stale = !!(resultFor && !sameQuestion(resultFor, st));
      els.stale.hidden = !stale;
      els.result.classList.toggle("is-stale", stale);
      els.original.classList.toggle("is-stale", stale);
      els.mapFrame.classList.toggle("is-stale", stale);
      if (stale && pb.playing) setPlaying(false);
      renderFrame();
    }

    function edited() {
      if (st.presetId) {
        var p = presetById(st.presetId);
        if (!p || !sameQuestion(presetState(p), st)) { st.presetId = null; els.preset.value = ""; els.presetCaption.hidden = true; }
      }
      markStale();
    }

    // an undo entry is the whole question (map, problem, options and inputs), so undoing a
    // map or example switch never leaves one map's story on another map
    function pushUndo() {
      undoStack.push(Object.assign({}, st));
      if (undoStack.length > 200) undoStack.shift();
      els.undo.disabled = false;
    }

    function setMap(name, keepInputs) {
      var changed = name !== st.mapName || !view;
      st.mapName = name;
      if (!keepInputs) {
        var d = defaultInputs(name);
        st.story = d.story; st.history = d.history;
      }
      if (changed) {
        pb.possKey = null;
        view = CD.draw.renderMap(els.map, DATA.maps[name], { onPick: onPick });
        view.setRegionLabels(els.showRegions.checked);
        view.setScale();
      }
    }

    function onPick(kind, name) {
      pushUndo();
      if (kind === "room") {
        st.story = appendStory(st.story, name, DATA.maps[st.mapName].map.rooms);
        els.story.value = st.story;
      } else {
        st.history = appendHistory(st.history, name);
        els.history.value = st.history;
      }
      edited();
    }

    els.preset.addEventListener("change", function () {
      var p = presetById(els.preset.value);
      if (!p) { st.presetId = null; writeControls(); markStale(); return; }
      pushUndo();
      var ps = presetState(p);
      setMap(ps.mapName, true);
      Object.assign(st, ps);
      // the caption names free regions (R4, ...): show their labels
      if (p.region_labels && !els.showRegions.checked) { els.showRegions.checked = true; view.setRegionLabels(true); }
      writeControls();
      run(true);
    });
    els.mapSelect.addEventListener("change", function () {
      pushUndo();
      setMap(els.mapSelect.value, false);
      st.presetId = null;
      writeControls();
      run(true);
    });
    els.problem.addEventListener("change", function () {
      st.problem = parseInt(els.problem.value, 10);
      writeControls(); edited();
    });
    els.kase.addEventListener("change", function () { st.kase = parseInt(els.kase.value, 10); edited(); });
    els.anchored.addEventListener("change", function () { st.anchored = els.anchored.value === "1"; edited(); });
    els.unreported.addEventListener("change", function () { st.unreported = els.unreported.checked; edited(); });
    Array.prototype.forEach.call(doc.querySelectorAll('input[name="agents"]'), function (r) {
      r.addEventListener("change", function () { if (r.checked) { st.agents = r.value; edited(); } });
    });
    els.story.addEventListener("input", function () { st.story = els.story.value; edited(); });
    els.history.addEventListener("input", function () { st.history = els.history.value; edited(); });
    els.undo.addEventListener("click", function () {
      var u = undoStack.pop();
      if (!u) return;
      var mapChanged = u.mapName !== st.mapName;
      setMap(u.mapName, true);
      Object.assign(st, u);
      els.undo.disabled = undoStack.length === 0;
      writeControls();
      // the answer on screen belongs to the other map: answer the restored question instead
      if (mapChanged) run(false);
      else edited();
    });
    els.clear.addEventListener("click", function () {
      if (!st.story && !st.history) return;
      pushUndo();
      st.story = ""; st.history = "";
      els.story.value = ""; els.history.value = "";
      edited();
    });
    els.form.addEventListener("submit", function (ev) { ev.preventDefault(); run(true); });
    els.showRegions.addEventListener("change", function () { view.setRegionLabels(els.showRegions.checked); });
    els.showPossible.addEventListener("change", function () { renderFrame(); });

    // ---------------------------------------------------------------- chips
    function chip(list, textValue, cls, title) {
      var li = doc.createElement("li");
      li.className = "chip" + (cls ? " " + cls : "");
      li.textContent = textValue;
      if (title) li.title = title;
      list.appendChild(li);
      return li;
    }
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
    function eventLabel(e) {
      return e.sensor + (kindOf(st.mapName, e.sensor) === "occupancy" ? (e.kind === "A" ? " on" : " off") : "");
    }
    function renderChipsFromInputs() {
      clear(els.storyChips); clear(els.historyChips);
      var map = getMap(st.mapName), story = null, events = null;
      try { story = CD.parseStory(st.story, map); } catch (e) { story = null; }
      try { events = CD.parseHistory(st.history, map); } catch (e) { events = null; }
      if (story) story.forEach(function (r) { chip(els.storyChips, r, ""); });
      else if (String(st.story).trim()) chip(els.storyChips, st.story, "chip-bad", "not a story on this map");
      if (events) events.forEach(function (e) { chip(els.historyChips, eventLabel(e), ""); });
      else if (String(st.history).trim()) chip(els.historyChips, st.history, "chip-bad", "not a history on this map");
      if (!els.storyChips.firstChild) chip(els.storyChips, "empty", "chip-empty");
      if (!els.historyChips.firstChild) chip(els.historyChips, "no recordings", "chip-empty");
    }
    var chipsKey = null;
    function renderChips(storyIndex, h) {
      var fromInputs = !result || els.stale.hidden === false || result.status === "error";
      var key = fromInputs ? ["inputs", st.mapName, st.story, st.history].join("\u0000") : [storyIndex, h].join(",");
      if (key === chipsKey) return;
      chipsKey = key;
      if (fromInputs) { renderChipsFromInputs(); return; }
      clear(els.storyChips); clear(els.historyChips);
      var story = result.story || [], events = result.events || [];
      story.forEach(function (r, i) {
        var cls = [];
        if (result.inserted && result.inserted.indexOf(i) >= 0) cls.push("chip-inserted");
        if (storyIndex !== null) {
          if (i < storyIndex - 1) cls.push("chip-done");
          else if (i === storyIndex - 1) cls.push("chip-current");
        }
        chip(els.storyChips, r, cls.join(" "), cls.indexOf("chip-inserted") >= 0 ? "inserted visit" : null);
      });
      events.forEach(function (e, i) {
        var cls = "";
        if (h !== null) cls = i < h - 1 ? "chip-done" : (i === h - 1 ? "chip-current" : "");
        chip(els.historyChips, eventLabel(e), cls);
      });
      if (!story.length) chip(els.storyChips, "empty", "chip-empty");
      if (!events.length) chip(els.historyChips, "no recordings", "chip-empty");
    }

    // ---------------------------------------------------------------- result panel
    function node(tag, cls, textValue, parent) {
      var n = doc.createElement(tag);
      if (cls) n.className = cls;
      if (textValue !== undefined && textValue !== null) n.textContent = textValue;
      if (parent) parent.appendChild(n);
      return n;
    }
    function badge(parent, status, label) {
      return node("span", "badge badge-" + status, label, parent);
    }
    var STATUS_LABEL = { consistent: "consistent", inconsistent: "inconsistent", malformed: "malformed", error: "input error" };
    var PROBLEM_TEXT = { 1: "Problem 1: is the story consistent with the recordings?",
                         2: "Problem 2: story and sensors cover different time intervals.",
                         3: "Problem 3: the story may leave visits out; find the shortest consistent story containing it.",
                         4: "Problem 4: the story may contain errors; find the closest consistent story." };

    function renderResult() {
      var R = result, box = els.result;
      clear(box);
      var head = node("div", "verdict", null, box);
      badge(head, R.status, STATUS_LABEL[R.status]);
      node("strong", "headline", R.headline, head);
      var ctx = PROBLEM_TEXT[R.problem] + " " + (R.agents === "multi" ? "Other agents may be around." : "x is alone.") +
                (st.unreported && R.problem ? " Room visits may go unreported." : "");
      node("p", "context", ctx, box);
      if (R.reason) {
        var reason = friendlyReason(R.reason.replace(/^malformed history: /, ""), st.mapName);
        node("p", "reason", reason.charAt(0).toUpperCase() + reason.slice(1) + ".", box);
      }
      var dl = node("dl", "facts", null, box);
      function fact(term, value, cls) {
        node("dt", null, term, dl);
        var dd = node("dd", cls || null, null, dl);
        if (typeof value === "string") dd.textContent = value; else if (value) dd.appendChild(value);
        return dd;
      }
      if (R.interval) fact("Interval order", R.interval, "mono");
      if (R.problem === 3 && R.status === "consistent") {
        var frag = doc.createDocumentFragment();
        R.story.forEach(function (r, i) { node(R.inserted.indexOf(i) >= 0 ? "mark" : "span", null, r, frag); });
        fact("Shortest story", frag, "mono story-out");
        fact("Inserted", R.inserted.length ? R.inserted.length + " visit" + (R.inserted.length === 1 ? "" : "s") +
             " (highlighted)" : "none: the story is consistent as told");
      }
      if (R.problem === 4 && R.status === "consistent") {
        fact("Closest story", R.story.join(""), "mono");
        var ops = R.operations.map(function (o) { return editText(o, R.storyLength); });
        fact("Edits", ops.length ? ops.length + ": " + ops.join("; ") : "none: the story is consistent as told");
      }
      if (R.pathString) {
        var dd = fact("Witness walk", R.pathString, "mono path");
        dd.title = "Rooms in order; [b1d] = x crossed b1 from side b1d; [o1] = x went through occupancy region o1; " +
                   "{o1} = through o1 while others kept it active; (D) = a room entry the story does not report; " +
                   "<b12>, <o1> = crossed b1 from side b12, or entered o1, while the sensors were not recording; " +
                   "|t0|, |tf| = the story's interval begins, ends; |t0'|, |tf'| = the sensors' interval begins, ends";
      }
      if (R.possible) {
        fact("Shading", R.possibleMode === "story"
          ? "where x could be at the current time, given the story and the recordings"
          : R.problem === 2
            ? "where x could be while the sensors record, given the recordings alone; outside the sensors' interval x may be anywhere"
            : "where x could be at the current time, given the recordings alone", "muted");
      }
      if (!dl.firstChild) box.removeChild(dl);
      renderOriginal();
    }

    function renderOriginal() {
      var o = result.original, box = els.original;
      clear(box);
      box.hidden = !o;
      if (!o) return;
      node("h3", null, "What the original 2010 applet says", box);
      var row = node("div", "verdict", null, box);
      if (o.status === "crash") badge(row, "crash", "crashed");
      else if (o.status === "unsupported") badge(row, "error", "cannot run");
      else badge(row, o.status, STATUS_LABEL[o.status]);
      if (o.pathString) node("code", "mono path", o.pathString, row);
      if (o.message) node("p", "reason mono-wrap", o.message, box);
      if (o.status !== "crash" && o.status !== "unsupported" && st.agents === "multi") {
        node("p", "muted small", "The original's multi-agent check returns no walk.", box);
      }
      if (st.unreported) node("p", "muted small", "The original has no option for unreported visits; it always uses its own rules.", box);
      if (o.differs) {
        var note = node("div", "differs", null, box);
        node("strong", null, o.bug ? "Bug " + o.bug.id + ": " : "Differs: ", note);
        var why = o.bug ? o.bug.explanation + "." :
          (o.status === "crash" ? "the original crashes on this input." :
           result.status === "error" ? "we reject this input, the original answers it." :
           o.walkProblem ? "same verdict, but the original's answer is wrong: " + o.walkProblem + "." :
           "the original's verdict differs from ours.");
        if (o.bug && o.walkProblem) why += " Here " + o.walkProblem.replace(/^the walk/, "the original's walk") + ".";
        node("span", null, why, note);
      } else if (o.note === "same verdict" && o.pathString && result.pathString && o.pathString !== result.pathString) {
        node("p", "muted small", "Same verdict; the original reports a different walk (it explores the map in another order).", box);
      }
    }

    // ---------------------------------------------------------------- playback
    function speedFactor() { return parseFloat(els.speed.value) || 1; }

    /** The caption is a polite live region: write it only when the text changes, and keep it
     *  silent during playback (one announcement per step would flood a screen reader). */
    function setCaption(textValue) {
      if (els.caption.textContent !== textValue) els.caption.textContent = textValue;
    }

    function setPlaying(on) {
      pb.playing = on;
      els.play.dataset.state = on ? "playing" : "paused";
      els.play.setAttribute("aria-label", on ? "Pause" : "Play");
      els.play.title = on ? "Pause" : "Play";
      els.caption.setAttribute("aria-live", on ? "off" : "polite");
      if (on) {
        pb.last = 0;
        pb.tick = 0;
        if (!pb.raf) pb.raf = win.requestAnimationFrame(frame);
      } else if (pb.raf) {
        win.cancelAnimationFrame(pb.raf);
        pb.raf = 0;
      }
    }

    function atEnd() {
      if (pb.mode === "walk") return pb.t >= pb.tl.total - 1e-9;
      if (pb.mode === "slots") return pb.slot >= pb.slots - 1;
      return true;
    }

    function frame(ts) {
      pb.raf = 0;
      if (!pb.playing) return;
      var dt = pb.last ? Math.min(0.1, (ts - pb.last) / 1000) : 0;
      pb.last = ts;
      if (pb.mode === "walk" && !reduceMotion) {
        pb.t = Math.min(pb.tl.total, pb.t + dt * speedFactor());
      } else {
        // reduced motion (or no walk): jump from step to step
        pb.tick += dt * speedFactor();
        if (pb.tick >= 0.8) { pb.tick = 0; stepBy(1, true); }
      }
      renderFrame();
      if (atEnd()) { setPlaying(false); return; }
      pb.raf = win.requestAnimationFrame(frame);
    }

    function currentStep() {
      if (pb.mode !== "walk") return 0;
      return CD.draw.sampleTimeline(pb.tl, pb.trace, pb.t).step;
    }

    function stepBy(dir, fromPlay) {
      if (pb.mode === "walk") {
        var k = currentStep(), tk = CD.draw.stepTime(pb.tl, k), n = pb.tl.steps;
        var target;
        if (dir > 0) target = k + 1 < n ? CD.draw.stepTime(pb.tl, k + 1) : pb.tl.total;
        else target = pb.t > tk + 1e-6 ? tk : CD.draw.stepTime(pb.tl, Math.max(0, k - 1));
        pb.t = target;
      } else if (pb.mode === "slots") {
        pb.slot = Math.max(0, Math.min(pb.slots - 1, pb.slot + dir));
      }
      if (!fromPlay) { setPlaying(false); renderFrame(); }
    }

    els.play.addEventListener("click", function () {
      if (pb.mode === "none") return;
      if (pb.playing) { setPlaying(false); return; }
      if (atEnd()) { pb.t = 0; pb.slot = 0; }
      setPlaying(true);
    });
    els.back.addEventListener("click", function () { stepBy(-1, false); });
    els.fwd.addEventListener("click", function () { stepBy(1, false); });
    els.scrub.addEventListener("input", function () {
      setPlaying(false);
      var v = parseFloat(els.scrub.value);
      if (pb.mode === "walk") pb.t = v / 1000 * pb.tl.total;
      else if (pb.mode === "slots") pb.slot = Math.round(v);
      renderFrame();
    });

    function setupPlayback() {
      setPlaying(false);
      pb.mode = "none"; pb.t = 0; pb.slot = 0; pb.trace = null; pb.tl = null; pb.drawError = null;
      if (result && result.path && result.path.length) {
        try {
          pb.trace = CD.draw.tracePath(DATA.maps[st.mapName], result.path);
          pb.tl = CD.draw.makeTimeline(pb.trace, { speed: 200, minStep: 0.35 });
          pb.mode = "walk";
        } catch (e) {
          pb.mode = "none";
          pb.drawError = "The witness cannot be drawn: " + errorText(e);
        }
      }
      if (pb.mode === "none" && result && result.events && result.possible) {
        pb.mode = "slots";
        pb.slots = result.events.length + 1;
      }
      els.scrub.max = pb.mode === "slots" ? String(pb.slots - 1) : "1000";
      els.scrub.step = pb.mode === "slots" ? "1" : "any";
      var off = pb.mode === "none";
      [els.play, els.back, els.fwd, els.scrub].forEach(function (b) { b.disabled = off; });
      els.scrub.value = "0";
      view.setTrail(pb.mode === "walk" ? pb.trace.points : null);
    }

    function renderFrame() {
      if (!view) return;
      var events = (result && result.events) || [], h = null, storyIndex = null, caption = "", valueText = "";
      var shade = { names: null, outside: false };
      if (pb.mode === "walk") {
        var s = CD.draw.sampleTimeline(pb.tl, pb.trace, pb.t), step = result.path[s.step];
        h = step.time;
        storyIndex = step.story_index;
        shade = shadingAt(result, s.step);
        view.setMarker(s.point);
        view.setProgress(pb.trace.points.slice(0, s.index + 1).concat([s.point]));
        caption = "Step " + (s.step + 1) + " of " + result.path.length + ": " + describeStep(result.path, s.step, events, st.mapName);
        if (shade.outside && els.showPossible.checked && result.possible) caption += " Outside the sensors' interval x may be anywhere (no shading).";
        valueText = caption;
        els.scrub.value = String(pb.tl.total > 0 ? Math.round(pb.t / pb.tl.total * 1000) : 0);
      } else if (pb.mode === "slots") {
        h = pb.slot;
        shade = shadingAt(result, h);
        view.setMarker(null);
        view.setProgress(null);
        caption = "No witness walk. After " + h + " of " + events.length + " recordings, the shading shows where the recordings alone allow x to be" +
                  (result.problem === 2 ? " while the sensors record." : ".");
        valueText = "Slot " + (h + 1) + " of " + pb.slots + ": after " + h + " of " + events.length + " recordings";
        els.scrub.value = String(pb.slot);
      } else {
        view.setMarker(null);
        view.setProgress(null);
        if (pb.drawError) caption = pb.drawError;
        else if (result && result.status !== "consistent") caption = "No walk to show.";
      }
      setCaption(caption);
      // the slider announces the step, not a number out of 1000
      if (!valueText) els.scrub.removeAttribute("aria-valuetext");
      else if (els.scrub.getAttribute("aria-valuetext") !== valueText) els.scrub.setAttribute("aria-valuetext", valueText);
      var active = h !== null ? CD.draw.activeAfter(events, h, DATA.maps[st.mapName].map.occupancy) : {};
      var fired = h !== null && h > 0 && events[h - 1] ? events[h - 1].sensor : null;
      view.setSensors({ active: active, fired: fired });
      var poss = null;
      if (els.showPossible.checked && result && result.possible && h !== null && els.stale.hidden) poss = shade.names;
      var possKey = poss ? poss.join(" ") : "";
      if (possKey !== pb.possKey) { pb.possKey = possKey; view.setPossible(poss); }
      renderChips(storyIndex, h);
    }

    function run(autoplay) {
      result = solve(st);
      resultFor = Object.assign({}, st);
      chipsKey = null;
      els.stale.hidden = true;
      els.result.classList.remove("is-stale");
      els.original.classList.remove("is-stale");
      els.mapFrame.classList.remove("is-stale");
      renderResult();
      setupPlayback();
      renderFrame();
      if (autoplay && !reduceMotion && pb.mode === "walk") setPlaying(true);
    }

    // ---------------------------------------------------------------- start
    var resizeTimer = 0;
    win.addEventListener("resize", function () {
      if (resizeTimer) win.clearTimeout(resizeTimer);
      resizeTimer = win.setTimeout(function () { resizeTimer = 0; if (view) view.setScale(); }, 60);
    });
    syncThemeButton();
    setMap(st.mapName, false);
    els.undo.disabled = true;
    writeControls();
    run(true);
  }

  CD.app = {
    DEFAULT_INPUTS: DEFAULT_INPUTS,
    getMap: getMap,
    defaultInputs: defaultInputs,
    appendStory: appendStory,
    appendHistory: appendHistory,
    isMalformedReason: isMalformedReason,
    originalApplies: originalApplies,
    presetById: presetById,
    presetState: presetState,
    sameQuestion: sameQuestion,
    parseWalkString: parseWalkString,
    checkWalkString: checkWalkString,
    solve: solve,
    describeStep: describeStep,
    shadingAt: shadingAt,
    friendlyReason: friendlyReason,
    editText: editText,
    beamOfSide: beamOfSide
  };

  if (root.document && root.document.getElementById && root.document.getElementById("map")) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", function () { startPage(root.document, root); });
    } else {
      startPage(root.document, root);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
