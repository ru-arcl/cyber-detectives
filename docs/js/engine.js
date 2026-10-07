/* Cyber Detectives: JavaScript port of the default engine of the Python package
 * (src/cyber_detectives: maps.py, history.py, engine.py, problems.py).
 *
 * Classic script (no ES module, no dependencies, no network): it defines one global
 * namespace, CyberDetectives (window.CyberDetectives in a browser, globalThis in node/vm).
 * docs/js/original.js adds CyberDetectives.original (the port of the original Java code).
 *
 * The port is structural: same data, same search order, same tie-breaks, same messages, so
 * that verdicts, witnesses and error texts are identical to Python.  tests/js/*.test.js check
 * this against tests/fixtures/parity/*.json, which tools/gen_parity.py generates from Python.
 *
 * Results are plain objects shaped like the Python to_dict() (snake_case keys):
 *   Step   {kind, position, time, story_index, sensor, event}
 *   validate            -> {consistent, reason, agents, compat, path_string, path}
 *   validateIntervals   -> the same plus {case, interval}
 *   shortestSuperstory  -> null | {story, length, inserted, anchored, agents, path_string, path}
 *   closestStory        -> null | {story, edits, operations, edit_ops, agents, path_string, path}
 *   possiblePositions   -> [[position, ...] per time slot]   (options: story, agents,
 *                          unreportedVisits, starts: "rooms" | "anywhere")
 * Errors are Error subclasses (MapError, InputError, InvalidPath; all extend ValueError) whose
 * message equals Python's str(exception).
 *
 * Names are data: every table keyed by a map name is a native Map/Set or an object built with
 * own data properties, so names like "constructor", "toString" or "__proto__" behave like any
 * other name.
 *
 * Accepted inputs (the same forms as Python; docs/DESIGN.md, "Public API"):
 * - story: a string ("ACBAC", "A C B", "R1,R2") or an array of room names;
 * - history: a string of tokens ("b1 o1 o1", "o1+ o1-"), an array of string tokens, or an
 *   array whose items are Event objects, [sensor, kind] pairs or {sensor, kind} objects
 *   (exactly those two own keys; a Map with those two keys also works).  Anything else as the
 *   whole story/history (an object, a Map, a Set, a number, ...) is an InputError with
 *   Python's message;
 * - map dicts (Map.fromDict): beams as {name: [side0, side1]} or [[name, [side0, side1]], ...];
 *   regions as {name: [features]}, [[name, [features]], ...] or [[features], ...] (auto names).
 *   toDict() writes beams (and, separately, regions) as an object unless one of its names
 *   is made of digits only (/^[0-9]+$/); then it writes the [name, value] pair array.
 *
 * Known differences from Python:
 * - a JS object iterates integer-like keys ("7", "12") first, in numeric order, whatever the
 *   order in the JSON text.  So a map dict that gives beams or regions with all-digit names
 *   in object form may iterate them in another order than Python does (use the pair form,
 *   which toDict / to_dict write for such maps); adjacency() returns a plain object, whose
 *   key order has the same property; and compat: "original" hands the original's builder the
 *   beams as an object, so with all-digit beam names it sees them in JS object order;
 * - repr() of a non-ASCII character in a message: Python escapes the characters that are not
 *   printable in its own Unicode database (Python 3.9: Unicode 13.0, 3.13: 15.1), this file
 *   those matched by the JS engine's \p{...} tables (its own Unicode version).  A character
 *   assigned in one version but not the other is written raw by one and escaped (\uXXXX) by
 *   the other.  The parity fixtures use only characters assigned before Unicode 13;
 * - numbers are formatted as in Python only for integers (no float repr).
 */
(function (root) {
  "use strict";

  var CD = root.CyberDetectives || (root.CyberDetectives = {});
  var NativeMap = root.Map;

  // ======================================================================== Python helpers

  /** A Python tuple, for repr() in messages. */
  function PyTuple(items) { this.items = items; }

  var NONPRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;

  function hex(n, width) {
    var s = n.toString(16);
    while (s.length < width) s = "0" + s;
    return s;
  }

  function reprStr(s) {
    var quote = "'";
    if (s.indexOf("'") >= 0 && s.indexOf('"') < 0) quote = '"';
    var out = quote;
    for (var ch of s) {
      var c = ch.codePointAt(0);
      if (ch === quote || ch === "\\") out += "\\" + ch;
      else if (ch === "\t") out += "\\t";
      else if (ch === "\n") out += "\\n";
      else if (ch === "\r") out += "\\r";
      else if (c < 0x20 || c === 0x7f) out += "\\x" + hex(c, 2);
      else if (c < 0x7f) out += ch;
      else if (NONPRINTABLE.test(ch)) {
        if (c < 0x100) out += "\\x" + hex(c, 2);
        else if (c < 0x10000) out += "\\u" + hex(c, 4);
        else out += "\\U" + hex(c, 8);
      } else out += ch;
    }
    return out + quote;
  }

  /** A Map, also one made in another realm (iframe, node:vm). */
  function isNativeMap(x) {
    return x instanceof NativeMap || Object.prototype.toString.call(x) === "[object Map]";
  }

  /** Python repr() of a JSON-like value. */
  function pyRepr(x) {
    if (x === null || x === undefined) return "None";
    if (x === true) return "True";
    if (x === false) return "False";
    if (typeof x === "string") return reprStr(x);
    if (typeof x === "number") return String(x);
    if (x instanceof PyTuple) {
      if (x.items.length === 1) return "(" + pyRepr(x.items[0]) + ",)";
      return "(" + x.items.map(pyRepr).join(", ") + ")";
    }
    if (Array.isArray(x)) return "[" + x.map(pyRepr).join(", ") + "]";
    if (x instanceof Event) return "Event(sensor=" + pyRepr(x.sensor) + ", kind=" + pyRepr(x.kind) + ")";
    if (isNativeMap(x)) {
      var parts = [];
      x.forEach(function (v, k) { parts.push(pyRepr(k) + ": " + pyRepr(v)); });
      return "{" + parts.join(", ") + "}";
    }
    if (typeof x === "object") {
      return "{" + Object.keys(x).map(function (k) { return pyRepr(k) + ": " + pyRepr(x[k]); }).join(", ") + "}";
    }
    return String(x);
  }

  /** Python str() of a JSON-like value. */
  function pyStr(x) {
    if (typeof x === "string") return x;
    if (x instanceof Event) return x.sensor + " " + x.kind;
    return pyRepr(x);
  }

  function pyTypeName(x) {
    if (x === null || x === undefined) return "NoneType";
    if (typeof x === "boolean") return "bool";
    if (typeof x === "string") return "str";
    if (typeof x === "number") return Number.isInteger(x) ? "int" : "float";
    if (Array.isArray(x)) return "list";
    return "dict";
  }

  /** Iterate like Python list(x) for the value kinds a JSON map/story can hold. */
  function pyList(x) {
    if (typeof x === "string") return Array.from(x);
    if (Array.isArray(x)) return x.slice();
    if (isNativeMap(x)) return Array.from(x.keys());
    if (x !== null && typeof x === "object") return Object.keys(x);
    throw new TypeError(pyRepr(x) + " is not iterable");
  }

  /** (key, value) pairs of a mapping (plain object or Map) in iteration order. */
  function items(x) {
    if (isNativeMap(x)) return Array.from(x.entries());
    return Object.keys(x).map(function (k) { return [k, x[k]]; });
  }

  function isMapping(x) {
    return isNativeMap(x) || (x !== null && typeof x === "object" && !Array.isArray(x));
  }

  function hasOwn(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

  /** obj[k] = v as an own data property, even for k = "__proto__" (names are data, not code). */
  function setOwn(obj, k, v) {
    Object.defineProperty(obj, k, { value: v, writable: true, enumerable: true, configurable: true });
    return obj;
  }

  /** A plain object from [key, value] pairs (own data properties, any key). */
  function objectFrom(pairs) {
    var o = {};
    pairs.forEach(function (kv) { setOwn(o, kv[0], kv[1]); });
    return o;
  }

  /** String comparison by UTF-16 code units (Python compares code points; names are ASCII). */
  function cmpStr(a, b) { return a < b ? -1 : (a > b ? 1 : 0); }

  function cmpPairs(a, b) { return cmpStr(a[0], b[0]) || cmpStr(a[1], b[1]); }

  function deepCopy(x) { return x === undefined || x === null ? x : JSON.parse(JSON.stringify(x)); }

  // Python's \s (str.isspace) and str.strip()
  var WS = "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  var SEP_RE = new RegExp("[" + WS + ",]+");
  var SEP_RE_G = new RegExp("[" + WS + ",]+", "g");
  var STRIP_RE = new RegExp("^[" + WS + "]+|[" + WS + "]+$", "g");

  function pyStrip(s) { return s.replace(STRIP_RE, ""); }

  function sepSplit(s) { return s.split(SEP_RE_G); }

  // ======================================================================== errors

  function defineError(name, Base) {
    var E = function (message) {
      var e = new Base(message);
      Object.setPrototypeOf(e, E.prototype);
      e.name = name;
      return e;
    };
    E.prototype = Object.create(Base.prototype, {
      constructor: { value: E, writable: true, configurable: true },
      name: { value: name, writable: true, configurable: true }
    });
    Object.setPrototypeOf(E, Base);
    return E;
  }

  var ValueError = defineError("ValueError", Error);
  var MapError = defineError("MapError", ValueError);
  var InputError = defineError("InputError", ValueError);
  var InvalidPath = defineError("InvalidPath", ValueError);
  var KeyError = defineError("KeyError", Error);
  var NotImplementedError = defineError("NotImplementedError", Error);

  // ======================================================================== maps (maps.py)

  // Python's re '$' also matches before a trailing newline.
  var NAME_RE = /^[A-Za-z0-9_]+\n?$/;

  function checkName(what, name) {
    if (typeof name !== "string" || !NAME_RE.test(name)) {
      throw new MapError(what + " name " + pyRepr(name) +
                         " must be a non-empty string of letters, digits or '_'");
    }
    return name;
  }

  /** beams as [name, sides] items: a mapping, or a list of [name, sides] pairs. */
  function beamItems(beams) {
    if (isMapping(beams)) return items(beams);
    if (!Array.isArray(beams)) {
      throw new MapError("beams must be an object or a list of [name, sides] pairs, got " + pyRepr(beams));
    }
    beams.forEach(function (p, i) {
      if (!(Array.isArray(p) && p.length === 2)) {
        throw new MapError("beams item " + (i + 1) + " (" + pyRepr(p) + ") is not a [name, sides] pair");
      }
    });
    return beams.map(function (p) { return [p[0], p[1]]; });
  }

  /** A non-empty list whose items are all [string, list] pairs (named regions). */
  function isPairList(list) {
    return list.length > 0 && list.every(function (p) {
      return Array.isArray(p) && p.length === 2 && typeof p[0] === "string" && Array.isArray(p[1]);
    });
  }

  /** {name: value}, or [[name, value], ...] if a name is made of digits only (see toDict). */
  function namedJson(pairs) {
    if (pairs.some(function (kv) { return /^[0-9]+$/.test(kv[0]); })) {
      return pairs.map(function (kv) { return [kv[0], kv[1]]; });
    }
    return objectFrom(pairs);
  }

  function autoRegionNames(count, rooms, beams, occupancy) {
    var taken = new Set();
    rooms.forEach(function (r) { taken.add(r); });
    beams.forEach(function (ss, b) { taken.add(b); ss.forEach(function (s) { taken.add(s); }); });
    occupancy.forEach(function (o) { taken.add(o); });
    var out = [];
    var i = 0;
    while (out.length < count) {
      i += 1;
      var name = "R" + i;
      if (!taken.has(name)) out.push(name);
    }
    return out;
  }

  /**
   * A sensor-network map in the region model (Python cyber_detectives.Map).
   *
   *   new CyberDetectives.Map(name, rooms, beams, occupancy, regions,
   *                           {geometry, title, source, provenance, edges, vertex_order})
   *
   * beams: {beam: [side0, side1]} (object or Map) or an array of [beam, [side0, side1]] pairs;
   * regions: {region: [features]} (object or Map), an array of [region, [features]] pairs, or
   * an array of feature arrays (named R1, R2, ... skipping taken names).  A non-empty array
   * whose items are all [string, array] pairs is the pair form.
   * Fields: name, rooms, beams (Map), occupancy, regions (Map), regionsOf (Map feature ->
   * [regions]), sideBeam (Map side -> beam), sides, features, sensors, geometry, title,
   * source, provenance, vertex_order.
   */
  function CDMap(name, rooms, beams, occupancy, regions, opts) {
    opts = opts || {};
    if (typeof name !== "string" || !name) throw new MapError("map name must be a non-empty string");
    this.name = name;
    this.rooms = pyList(rooms).map(function (r) { return checkName("room", r); });
    this.beams = new NativeMap();
    var self = this;
    beamItems(beams).forEach(function (kv) {
      var b = kv[0];
      checkName("beam", b);
      if (!Array.isArray(kv[1])) {
        throw new MapError("beam " + pyRepr(b) + " must have exactly two sides, got " + pyRepr(kv[1]));
      }
      var sides = kv[1].slice();
      if (sides.length !== 2) {
        throw new MapError("beam " + pyRepr(b) + " must have exactly two sides, got " + pyRepr(sides));
      }
      sides.forEach(function (s) { checkName("beam side", s); });
      if (sides[0] === sides[1]) {
        throw new MapError("beam " + pyRepr(b) + " has two sides with the same name " + pyRepr(sides[0]));
      }
      self.beams.set(b, [sides[0], sides[1]]);
    });
    this.occupancy = pyList(occupancy).map(function (o) { return checkName("occupancy sensor", o); });

    var regItems;
    if (isMapping(regions)) {
      regItems = items(regions);
    } else if (!Array.isArray(regions)) {
      throw new MapError("regions must be an object or a list, got " + pyRepr(regions));
    } else if (isPairList(regions)) {
      regItems = regions.map(function (p) { return [p[0], p[1]]; });
    } else {
      var regList = regions.slice();
      var names = autoRegionNames(regList.length, this.rooms, this.beams, this.occupancy);
      regItems = regList.map(function (v, i) { return [names[i], v]; });
    }
    this.regions = new NativeMap();
    regItems.forEach(function (kv) {
      checkName("region", kv[0]);
      if (typeof kv[1] === "string") {
        throw new MapError("region " + pyRepr(kv[0]) + ": features must be a list of names, not a string");
      }
      if (!Array.isArray(kv[1])) {
        throw new MapError("region " + pyRepr(kv[0]) + ": features must be a list of names, got " + pyRepr(kv[1]));
      }
      self.regions.set(kv[0], kv[1].slice());
    });

    this.geometry = opts.geometry === undefined ? null : opts.geometry;
    this.title = opts.title === undefined ? null : opts.title;
    this.source = opts.source === undefined ? null : opts.source;
    this.provenance = opts.provenance === undefined ? null : opts.provenance;
    this.vertex_order = (opts.vertex_order === undefined || opts.vertex_order === null)
      ? null : pyList(opts.vertex_order);
    this._edgeOrder = (opts.edges === undefined || opts.edges === null) ? null
      : opts.edges.map(function (e) { return [pyStr(e[0]), pyStr(e[1])]; });

    this._index();
    this._validate();
  }

  CDMap.prototype._index = function () {
    var self = this;
    this.sideBeam = new NativeMap();
    this.beams.forEach(function (ss, b) { ss.forEach(function (s) { self.sideBeam.set(s, b); }); });
    var sides = [];
    this.beams.forEach(function (ss) { sides.push(ss[0], ss[1]); });
    this.sides = sides;
    this.features = this.rooms.concat(sides, this.occupancy);
    this.sensors = Array.from(this.beams.keys()).concat(this.occupancy);
    this._roomSet = new Set(this.rooms);
    this._occSet = new Set(this.occupancy);
    var acc = new NativeMap();
    this.features.forEach(function (f) { if (!acc.has(f)) acc.set(f, []); });
    this.regions.forEach(function (feats, rname) {
      feats.forEach(function (f) {
        if (acc.has(f) && acc.get(f).indexOf(rname) < 0) acc.get(f).push(rname);
      });
    });
    this.regionsOf = acc;
  };

  CDMap.prototype._validate = function () {
    var self = this;
    // 1. all names distinct across kinds
    var seen = new NativeMap();
    var groups = [["room", this.rooms], ["beam", Array.from(this.beams.keys())],
                  ["beam side", Array.from(this.sideBeam.keys())],
                  ["occupancy sensor", this.occupancy], ["region", Array.from(this.regions.keys())]];
    groups.forEach(function (g) {
      g[1].forEach(function (n) {
        if (seen.has(n)) {
          throw new MapError("name " + pyRepr(n) + " is used twice (as " + seen.get(n) + " and as " + g[0] + ")");
        }
        seen.set(n, g[0]);
      });
    });
    // 2. region features are known features, no duplicates
    var feats = new Set(this.features);
    this.regions.forEach(function (fs, rname) {
      if (!fs.length) throw new MapError("region " + pyRepr(rname) + " touches no feature");
      fs.forEach(function (f) {
        if (!feats.has(f)) {
          var what = typeof f === "string" ? seen.get(f) : undefined;
          var hint = what ? " (a " + what + "; regions touch rooms, beam sides and occupancy sensors)" : "";
          throw new MapError("region " + pyRepr(rname) + " touches unknown feature " + pyRepr(f) + hint);
        }
      });
      if (new Set(fs).size !== fs.length) {
        throw new MapError("region " + pyRepr(rname) + " lists a feature twice: " + pyRepr(fs));
      }
    });
    // 3. each beam side touches exactly one region
    this.sideBeam.forEach(function (b, s) {
      var rs = self.regionsOf.get(s);
      if (rs.length !== 1) {
        throw new MapError("beam side " + pyRepr(s) + " (of " + pyRepr(b) + ") touches " + rs.length +
                           " regions " + pyRepr(rs) + "; each side must touch exactly one");
      }
    });
    // 4. optional edge list equals the derived G
    if (this._edgeOrder !== null) {
      var given = new Set();
      this._edgeOrder.forEach(function (e) {
        var a = e[0], b = e[1];
        if (!feats.has(a) || !feats.has(b)) {
          throw new MapError("edge " + pyRepr(a) + "-" + pyRepr(b) + " names an unknown feature");
        }
        if (a === b) throw new MapError("edge " + pyRepr(a) + "-" + pyRepr(b) + " is a self-loop");
        given.add(edgeKey(a, b));
      });
      var derived = new Set(this.edges().map(function (e) { return edgeKey(e[0], e[1]); }));
      var same = given.size === derived.size;
      given.forEach(function (k) { if (!derived.has(k)) same = false; });
      if (!same) {
        var diff = function (x, y) {
          var out = [];
          x.forEach(function (k) { if (!y.has(k)) out.push(k.split("\u0000")); });
          out.sort(cmpPairs);
          return out.map(function (p) { return new PyTuple(p); });
        };
        throw new MapError("map " + pyRepr(this.name) + ": edge list does not match the regions: edges " +
                           "implied by the regions but not listed " + pyRepr(diff(derived, given)) +
                           "; listed but not implied " + pyRepr(diff(given, derived)));
      }
    }
    // 5. optional vertex order is a permutation of the features
    if (this.vertex_order !== null) {
      var a1 = this.vertex_order.slice().sort(cmpStr);
      var a2 = Array.from(feats).sort(cmpStr);
      var eq = a1.length === a2.length && a1.every(function (x, i) { return x === a2[i]; });
      if (!eq) {
        throw new MapError("map " + pyRepr(this.name) + ": vertex_order " + pyRepr(this.vertex_order) +
                           " is not a permutation of the features " + pyRepr(a2));
      }
    }
  };

  function edgeKey(a, b) { return a < b ? a + "\u0000" + b : b + "\u0000" + a; }

  /** "room", "beam", "side", "occupancy" or "region"; KeyError for an unknown name. */
  CDMap.prototype.kind = function (name) {
    if (this._roomSet.has(name)) return "room";
    if (this.beams.has(name)) return "beam";
    if (this.sideBeam.has(name)) return "side";
    if (this._occSet.has(name)) return "occupancy";
    if (this.regions.has(name)) return "region";
    throw new KeyError(pyRepr(name));
  };
  CDMap.prototype.isRoom = function (n) { return this._roomSet.has(n); };
  CDMap.prototype.isBeam = function (n) { return this.beams.has(n); };
  CDMap.prototype.isOccupancy = function (n) { return this._occSet.has(n); };
  CDMap.prototype.otherSide = function (side) {
    var ss = this.beams.get(this.sideBeam.get(side));
    return side === ss[0] ? ss[1] : ss[0];
  };
  CDMap.prototype.sideRegion = function (side) { return this.regionsOf.get(side)[0]; };

  /** Edges of G (STAR Fig. 3(a)), each sorted, list sorted. */
  CDMap.prototype.edges = function () {
    var out = new NativeMap();
    this.regions.forEach(function (fs) {
      for (var i = 0; i < fs.length; i++) {
        for (var j = i + 1; j < fs.length; j++) {
          var p = cmpStr(fs[i], fs[j]) <= 0 ? [fs[i], fs[j]] : [fs[j], fs[i]];
          out.set(p[0] + "\u0000" + p[1], p);
        }
      }
    });
    return Array.from(out.values()).sort(cmpPairs);
  };

  /** G as {feature: [neighbours in feature order]} (a plain object; see "Known differences"). */
  CDMap.prototype.adjacency = function () {
    var order = new NativeMap();
    this.features.forEach(function (f, i) { order.set(f, i); });
    var adj = new NativeMap();
    this.features.forEach(function (f) { adj.set(f, new Set()); });
    this.edges().forEach(function (e) { adj.get(e[0]).add(e[1]); adj.get(e[1]).add(e[0]); });
    return objectFrom(this.features.map(function (f) {
      return [f, Array.from(adj.get(f)).sort(function (a, b) { return order.get(a) - order.get(b); })];
    }));
  };

  /** Region graph (STAR Fig. 3(b)): sorted [region, room/beam/occupancy] pairs. */
  CDMap.prototype.regionGraphEdges = function () {
    var out = new NativeMap();
    var self = this;
    this.regions.forEach(function (fs, rname) {
      fs.forEach(function (f) {
        var g = self.sideBeam.has(f) ? self.sideBeam.get(f) : f;
        var p = cmpStr(rname, g) <= 0 ? [rname, g] : [g, rname];
        out.set(p[0] + "\u0000" + p[1], p);
      });
    });
    return Array.from(out.values()).sort(cmpPairs);
  };

  CDMap.prototype._regionEdgeOrder = function () {
    var seen = new Set();
    var out = [];
    this.regions.forEach(function (fs) {
      for (var i = 0; i < fs.length; i++) {
        for (var j = i + 1; j < fs.length; j++) {
          var k = edgeKey(fs[i], fs[j]);
          if (!seen.has(k)) { seen.add(k); out.push([fs[i], fs[j]]); }
        }
      }
    });
    return out;
  };

  /**
   * The fixture JSON form (Python Map.to_dict()).  beams and regions are objects, except that
   * each becomes an array of [name, value] pairs when one of its names is all digits.
   */
  CDMap.prototype.toDict = function () {
    var out = { name: this.name };
    if (this.title !== null) out.title = this.title;
    if (this.source !== null) out.source = this.source;
    if (this.provenance !== null) out.provenance = deepCopy(this.provenance);
    out.rooms = this.rooms.slice();
    out.beams = namedJson(Array.from(this.beams, function (kv) { return [kv[0], kv[1].slice()]; }));
    out.occupancy = this.occupancy.slice();
    if (this.vertex_order !== null) out.vertex_order = this.vertex_order.slice();
    out.regions = namedJson(Array.from(this.regions, function (kv) { return [kv[0], kv[1].slice()]; }));
    out.edges = (this._edgeOrder !== null ? this._edgeOrder : this._regionEdgeOrder())
      .map(function (e) { return [e[0], e[1]]; });
    if (this.geometry !== null) out.geometry = deepCopy(this.geometry);
    return out;
  };

  CDMap.prototype.equals = function (other) {
    if (!(other instanceof CDMap)) return false;
    var arrEq = function (a, b) { return a.length === b.length && a.every(function (x, i) { return x === b[i]; }); };
    if (this.name !== other.name || !arrEq(this.rooms, other.rooms) || !arrEq(this.occupancy, other.occupancy)) return false;
    var mapEq = function (a, b, asSet) {
      if (a.size !== b.size) return false;
      var ok = true;
      a.forEach(function (v, k) {
        if (!b.has(k)) { ok = false; return; }
        var w = b.get(k);
        if (asSet) {
          var s1 = new Set(v), s2 = new Set(w);
          if (s1.size !== s2.size) ok = false;
          s1.forEach(function (x) { if (!s2.has(x)) ok = false; });
        } else if (!arrEq(v, w)) ok = false;
      });
      return ok;
    };
    return mapEq(this.beams, other.beams, false) && mapEq(this.regions, other.regions, true);
  };

  /** All maximal cliques (isolated vertices are 1-cliques); order unspecified. */
  function maximalCliques(adj) {
    var out = [];
    function bk(r, p, x) {
      if (!p.size && !x.size) { out.push(r.slice()); return; }
      var pivot = null, best = -1;
      p.forEach(function (u) { consider(u); });
      x.forEach(function (u) { consider(u); });
      function consider(u) {
        var c = 0;
        adj.get(u).forEach(function (w) { if (p.has(w)) c++; });
        if (c > best) { best = c; pivot = u; }
      }
      var cand = [];
      p.forEach(function (v) { if (!adj.get(pivot).has(v)) cand.push(v); });
      cand.forEach(function (v) {
        var nv = adj.get(v);
        var p2 = new Set(), x2 = new Set();
        p.forEach(function (w) { if (nv.has(w)) p2.add(w); });
        x.forEach(function (w) { if (nv.has(w)) x2.add(w); });
        bk(r.concat([v]), p2, x2);
        p.delete(v);
        x.add(v);
      });
    }
    bk([], new Set(adj.keys()), new Set());
    return out;
  }

  /** Map.from_edges: one region per maximal clique of G (see the Python caveat). */
  CDMap.fromEdges = function (name, rooms, beams, occupancy, edges, opts) {
    var sides = [];
    beamItems(beams).forEach(function (kv) {  // (malformed sides are reported by the constructor)
      if (Array.isArray(kv[1])) kv[1].forEach(function (s) { sides.push(s); });
    });
    var feats = pyList(rooms).concat(sides, pyList(occupancy));
    var known = new Set(feats);
    var adj = new NativeMap();
    feats.forEach(function (f) { if (!adj.has(f)) adj.set(f, new Set()); });
    edges.forEach(function (e) {
      var a = e[0], b = e[1];
      if (!known.has(a) || !known.has(b)) {
        throw new MapError("edge " + pyRepr(a) + "-" + pyRepr(b) + " names an unknown feature");
      }
      if (a === b) throw new MapError("edge " + pyRepr(a) + "-" + pyRepr(b) + " is a self-loop");
      adj.get(a).add(b);
      adj.get(b).add(a);
    });
    var order = new NativeMap();
    feats.forEach(function (f, i) { order.set(f, i); });
    var cliques = maximalCliques(adj).map(function (c) {
      return c.sort(function (a, b) { return order.get(a) - order.get(b); });
    });
    cliques.sort(function (a, b) {
      for (var i = 0; i < Math.min(a.length, b.length); i++) {
        var d = order.get(a[i]) - order.get(b[i]);
        if (d) return d;
      }
      return a.length - b.length;
    });
    var o = Object.assign({}, opts || {});
    if (o.edges === undefined) o.edges = edges.map(function (e) { return e.slice(); });
    return new CDMap(name, rooms, beams, occupancy, cliques, o);
  };

  /** Map.from_dict: the fixture JSON format (regions, else edges). */
  CDMap.fromDict = function (d) {
    if (!isMapping(d) || isNativeMap(d)) {
      throw new MapError("a map must be a JSON object, got " + pyTypeName(d));
    }
    ["name", "rooms", "beams", "occupancy"].forEach(function (key) {
      if (!hasOwn(d, key)) throw new MapError("map is missing the key " + pyRepr(key));
    });
    var get = function (key) { return hasOwn(d, key) && d[key] !== undefined ? d[key] : null; };
    var kw = {
      geometry: deepCopy(get("geometry")),
      title: get("title"),
      source: get("source"),
      provenance: deepCopy(get("provenance")),
      vertex_order: get("vertex_order")
    };
    if (get("regions") !== null) {
      kw.edges = get("edges");
      return new CDMap(d.name, d.rooms, d.beams, d.occupancy, d.regions, kw);
    }
    if (get("edges") === null) {
      throw new MapError("map " + pyRepr(d.name) + " has neither 'regions' nor 'edges'");
    }
    return CDMap.fromEdges(d.name, d.rooms, d.beams, d.occupancy, d.edges, kw);
  };

  // ======================================================================== history.py

  /** One sensor recording (Python Event): sensor name and kind "A" or "D". */
  function Event(sensor, kind) {
    this.sensor = sensor;
    this.kind = kind;
  }
  Event.prototype.toString = function () { return this.sensor + " " + this.kind; };

  function evEq(e, sensor, kind) { return e.sensor === sensor && e.kind === kind; }

  /** Story as an array of room names (string of letters, separated string, or array). */
  function parseStory(story, map) {
    var names;
    if (typeof story === "string") {
      var s = pyStrip(story);
      if (!s) names = [];
      else if (SEP_RE.test(s)) names = sepSplit(s).filter(function (t) { return t; });
      else names = Array.from(s);
    } else {
      if (!Array.isArray(story)) {
        throw new InputError("story must be a string or a list of room names, got " + pyRepr(story));
      }
      names = story.slice();
      names.forEach(function (x) {
        if (typeof x !== "string" || !x) throw new InputError("story element " + pyRepr(x) + " is not a room name");
      });
    }
    if (map) {
      names.forEach(function (x, i) {
        if (!map.isRoom(x)) {
          throw new InputError("story element " + (i + 1) + " (" + pyRepr(x) + ") is not a room of map " +
                               pyRepr(map.name) + " (rooms: " + map.rooms.join(" ") + ")");
        }
      });
    }
    return names;
  }

  /**
   * History as an array of Event.  Accepts an array whose items are Event objects,
   * [sensor, kind] pairs or {sensor, kind} objects (exactly those two keys), or a string (or
   * array of string tokens) of sensor names where an occupancy name toggles that sensor
   * ("o2 o2" = activation, deactivation) and o1+ / o1- are explicit.  Anything else (an
   * object or Map as the whole history, a Set, ...) is an InputError.  opts.checkNames
   * (default true).
   */
  function parseHistory(history, map, opts) {
    var checkNames = !(opts && opts.checkNames === false);
    if (typeof history === "string") {
      var toks = sepSplit(pyStrip(history)).filter(function (t) { return t; });
      return parseTokens(toks, map, checkNames);
    }
    if (!Array.isArray(history)) {
      throw new InputError("history must be a string or a list of events, got " + pyRepr(history));
    }
    var list = history;
    if (list.length && list.every(function (x) { return typeof x === "string"; })) {
      return parseTokens(list, map, checkNames);
    }
    return list.map(function (x, i) {
      var sensor, kind;
      if (Array.isArray(x) && x.length === 2) { sensor = x[0]; kind = x[1]; }
      else if (x instanceof Event) { sensor = x.sensor; kind = x.kind; }
      else if (isNativeMap(x) && x.size === 2 && x.has("sensor") && x.has("kind")) {
        sensor = x.get("sensor"); kind = x.get("kind");
      } else if (isMapping(x) && !isNativeMap(x) && Object.keys(x).length === 2 &&
                 hasOwn(x, "sensor") && hasOwn(x, "kind")) {
        sensor = x.sensor; kind = x.kind;
      } else {
        throw new InputError("history item " + (i + 1) + " (" + pyRepr(x) + ") is not a (sensor, kind) pair");
      }
      if (typeof sensor !== "string" || typeof kind !== "string") {
        throw new InputError("history item " + (i + 1) + " (" + pyRepr(x) + ") must hold two strings");
      }
      if (checkNames) {
        if (kind !== "A" && kind !== "D") {
          throw new InputError("history item " + (i + 1) + " (" + pyRepr(x) + "): kind must be 'A' or 'D'");
        }
        if (map && !(map.isBeam(sensor) || map.isOccupancy(sensor))) {
          throw new InputError("history item " + (i + 1) + ": " + pyRepr(sensor) + " is not a sensor of map " +
                               pyRepr(map.name) + " (sensors: " + map.sensors.join(" ") + ")");
        }
      }
      return new Event(sensor, kind);
    });
  }

  function parseTokens(tokens, map, checkNames) {
    var active = new NativeMap();
    var out = [];
    tokens.forEach(function (tok, i) {
      var explicit = null;
      var name = tok;
      var last = tok.charAt(tok.length - 1);
      if (Array.from(tok).length > 1 && (last === "+" || last === "-")) {
        explicit = last === "+" ? "A" : "D";
        name = tok.slice(0, -1);
      }
      if (map) {
        if (map.isBeam(name)) {
          if (explicit === "D") {
            throw new InputError("history token " + (i + 1) + " (" + pyRepr(tok) + "): beam detectors only activate");
          }
          out.push(new Event(name, "A"));
          return;
        }
        if (map.isOccupancy(name)) {
          var kind = explicit || (active.get(name) ? "D" : "A");
          active.set(name, kind === "A");
          out.push(new Event(name, kind));
          return;
        }
        if (checkNames) {
          throw new InputError("history token " + (i + 1) + ": " + pyRepr(name) + " is not a sensor of map " +
                               pyRepr(map.name) + " (sensors: " + map.sensors.join(" ") + ")");
        }
        out.push(new Event(name, explicit || "A"));
        return;
      }
      if (explicit === null) {
        if (checkNames) {
          throw new InputError("history token " + (i + 1) + " (" + pyRepr(tok) + "): a map is needed to tell " +
                               "beams from occupancy sensors (or write " + tok + "+ / " + tok + "-)");
        }
        out.push(new Event(name, "A"));
      } else {
        out.push(new Event(name, explicit));
      }
    });
    return out;
  }

  /** Render a history as tokens (beams by name, occupancy as o1+/o1- or toggling names). */
  function historyToString(events, map) {
    events = events.map(toEvent);
    var togglingOk = new NativeMap();
    if (map) {
      var state = new NativeMap();
      events.forEach(function (e) {
        if (map.isOccupancy(e.sensor)) {
          var ok = (e.kind === "A") !== (state.get(e.sensor) || false);
          togglingOk.set(e.sensor, (togglingOk.has(e.sensor) ? togglingOk.get(e.sensor) : true) && ok);
          state.set(e.sensor, e.kind === "A");
        }
      });
    }
    return events.map(function (e) {
      if (map && map.isBeam(e.sensor) && e.kind === "A") return e.sensor;
      if (map && togglingOk.get(e.sensor)) return e.sensor;
      return e.sensor + (e.kind === "A" ? "+" : "-");
    }).join(" ");
  }

  function toEvent(e) {
    if (e instanceof Event) return e;
    if (Array.isArray(e)) return new Event(e[0], e[1]);
    if (isNativeMap(e)) return new Event(e.get("sensor"), e.get("kind"));
    return new Event(e.sensor, e.kind);
  }

  function fmtRec(i, e) { return "recording " + (i + 1) + " (" + e.sensor + " " + e.kind + ")"; }

  function checkKinds(map, events) {
    for (var i = 0; i < events.length; i++) {
      var e = events[i];
      if (map.isBeam(e.sensor)) {
        if (e.kind !== "A") return fmtRec(i, e) + ": a beam detector cannot deactivate";
      } else if (!map.isOccupancy(e.sensor)) {
        return fmtRec(i, e) + ": unknown sensor";
      } else if (e.kind !== "A" && e.kind !== "D") {
        return fmtRec(i, e) + ": kind must be A or D";
      }
    }
    return null;
  }

  function checkSingleAgentHistory(map, events) {
    events = events.map(toEvent);
    var bad = checkKinds(map, events);
    if (bad) return bad;
    var i = 0, n = events.length;
    while (i < n) {
      var e = events[i];
      if (map.isOccupancy(e.sensor)) {
        if (e.kind === "D") {
          return fmtRec(i, e) + ": deactivation without the agent having entered " + e.sensor + " just before";
        }
        if (i + 1 >= n) {
          return fmtRec(i, e) + ": " + e.sensor + " is never deactivated, so the agent would still be inside it " +
            "after the last recording";
        }
        var nxt = events[i + 1];
        if (!evEq(nxt, e.sensor, "D")) {
          return fmtRec(i + 1, nxt) + " follows " + fmtRec(i, e) + ": a single agent inside " + e.sensor +
            " cannot cause another recording before leaving it";
        }
        i += 2;
      } else {
        i += 1;
      }
    }
    return null;
  }

  function checkMultiAgentHistory(map, events) {
    events = events.map(toEvent);
    var bad = checkKinds(map, events);
    if (bad) return bad;
    var active = new NativeMap();
    for (var i = 0; i < events.length; i++) {
      var e = events[i];
      if (map.isOccupancy(e.sensor)) {
        var on = active.get(e.sensor) || false;
        if (e.kind === "A" && on) return fmtRec(i, e) + ": " + e.sensor + " is already active";
        if (e.kind === "D" && !on) return fmtRec(i, e) + ": " + e.sensor + " is not active";
        active.set(e.sensor, e.kind === "A");
      }
    }
    return null;
  }

  function checkHistory(map, events, agents) {
    if (agents === undefined) agents = "single";
    if (agents === "single") return checkSingleAgentHistory(map, events);
    if (agents === "multi") return checkMultiAgentHistory(map, events);
    throw new ValueError("agents must be 'single' or 'multi', got " + pyRepr(agents));
  }

  // ======================================================================== engine.py

  function Step(kind, position, time, storyIndex, sensor, event) {
    return { kind: kind, position: position, time: time, story_index: storyIndex,
             sensor: sensor === undefined ? null : sensor, event: event === undefined ? null : event };
  }

  /** The witness in the original's notation, e.g. "A[b1u]C[o1][o2]B[b2r]AC". */
  function pathToString(path) {
    var out = [];
    path.forEach(function (s) {
      if (s.kind === "start" || s.kind === "visit") out.push(s.position);
      else if (s.kind === "cross" || s.kind === "enter") out.push("[" + s.sensor + "]");
      else if (s.kind === "move" && s.sensor !== null && s.sensor !== undefined && s.position === s.sensor) {
        out.push("{" + s.sensor + "}");
      } else if (s.kind === "unreported") out.push("(" + s.position + ")");
    });
    return out.join("");
  }

  /** Pre-computed data for one search (Python engine._Problem). */
  function Problem(m, story, events, multi, loose) {
    this.m = m;
    this.story = story;
    this.events = events;
    this.multi = multi;
    this.loose = loose;
    this.n = story.length;
    this.active = [];
    var cur = new Set();
    this.active.push(new Set(cur));
    events.forEach(function (e) {
      if (m.isOccupancy(e.sensor)) {
        if (e.kind === "A") cur.add(e.sensor); else cur.delete(e.sensor);
      }
      this.active.push(new Set(cur));
    }, this);
  }

  /** Moves in the open interval after h recordings: [[pos, k, step], ...]. */
  Problem.prototype.freeMoves = function (h, pos, k) {
    var m = this.m, out = [];
    var kind = m.kind(pos);
    if (kind === "room") {
      m.regionsOf.get(pos).forEach(function (r) { out.push([r, k, Step("move", r, h, k)]); });
    } else if (kind === "region") {
      m.regions.get(pos).forEach(function (f) {
        if (m.isRoom(f)) {
          if (k < this.n && this.story[k] === f) out.push([f, k + 1, Step("visit", f, h, k + 1)]);
          if (this.loose) out.push([f, k, Step("unreported", f, h, k)]);
        } else if (this.multi && m.isOccupancy(f) && this.active[h].has(f)) {
          out.push([f, k, Step("move", f, h, k, f)]);
        }
      }, this);
    } else if (kind === "occupancy") {
      if (this.multi && this.active[h].has(pos)) {
        m.regionsOf.get(pos).forEach(function (r) { out.push([r, k, Step("move", r, h, k, pos)]); });
      }
    }
    return out;
  };

  /** Transitions across recording h (0-based): [[pos, step|null], ...]. */
  Problem.prototype.eventMoves = function (h, pos, k) {
    var m = this.m, out = [];
    var e = this.events[h];
    var t = h + 1;
    var kind = m.kind(pos);
    if (m.isBeam(e.sensor)) {
      if (this.multi) out.push([pos, null]);
      if (kind === "region") {
        m.beams.get(e.sensor).forEach(function (side) {
          if (m.sideRegion(side) === pos) {
            var dst = m.sideRegion(m.otherSide(side));
            out.push([dst, Step("cross", dst, t, k, side, h)]);
          }
        });
      }
    } else if (this.multi) {
      if (!(e.kind === "D" && pos === e.sensor)) out.push([pos, null]);
    } else if (e.kind === "A") {
      if (kind === "region" && m.regions.get(pos).indexOf(e.sensor) >= 0) {
        out.push([e.sensor, Step("enter", e.sensor, t, k, e.sensor, h)]);
      }
    } else if (pos === e.sensor) {
      m.regionsOf.get(pos).forEach(function (r) { out.push([r, Step("exit", r, t, k, e.sensor, h)]); });
    }
    return out;
  };

  function sk(pos, k) { return pos + "|" + k; }

  function search(p) {
    var story = p.story, events = p.events, n = p.n;
    var mEv = events.length;
    var parent = new NativeMap();
    parent.set("0|" + sk(story[0], 1), [null, Step("start", story[0], 0, 1)]);
    var frontier = [[story[0], 1]];
    var bestK = 1;
    var layer = null;
    for (var h = 0; h <= mEv; h++) {
      layer = new NativeMap();
      frontier.forEach(function (s) { layer.set(sk(s[0], s[1]), s); });
      var queue = frontier.slice();
      for (var qi = 0; qi < queue.length; qi++) {
        var pos = queue[qi][0], k = queue[qi][1];
        var moves = p.freeMoves(h, pos, k);
        for (var mi = 0; mi < moves.length; mi++) {
          var key = sk(moves[mi][0], moves[mi][1]);
          if (!layer.has(key)) {
            var st = [moves[mi][0], moves[mi][1]];
            layer.set(key, st);
            parent.set(h + "|" + key, [h + "|" + sk(pos, k), moves[mi][2]]);
            queue.push(st);
          }
        }
      }
      bestK = 0;
      layer.forEach(function (s) { if (s[1] > bestK) bestK = s[1]; });
      if (h === mEv) break;
      var nxt = new NativeMap();
      layer.forEach(function (s) {
        p.eventMoves(h, s[0], s[1]).forEach(function (mv) {
          var key2 = sk(mv[0], s[1]);
          if (!nxt.has(key2)) {
            nxt.set(key2, [mv[0], s[1]]);
            parent.set((h + 1) + "|" + key2, [h + "|" + sk(s[0], s[1]), mv[1]]);
          }
        });
      });
      if (!nxt.size) {
        var e = events[h];
        return [null, "no walk consistent with the story so far can explain recording " + (h + 1) +
                " (" + e.sensor + " " + e.kind + "); at most " + bestK + " of " + n +
                " story elements were accounted for before it"];
      }
      frontier = Array.from(nxt.values());
    }
    var goalKey = sk(story[n - 1], n);
    if (!layer.has(goalKey)) {
      if (bestK < n) {
        return [null, "every recording can be explained, but at most " + bestK + " of the " + n +
                " story elements can be visited in order (story element " + (bestK + 1) + ", " +
                story[bestK] + ", cannot follow)"];
      }
      return [null, "the whole story can be visited, but x cannot be inside " + story[n - 1] +
              " (the last story room) after the last recording"];
    }
    var steps = [];
    var cur = mEv + "|" + goalKey;
    while (cur !== null) {
      var pr = parent.get(cur);
      if (pr[1] !== null) steps.push(pr[1]);
      cur = pr[0];
    }
    steps.reverse();
    return [steps, ""];
  }

  function checkAgents(agents) {
    if (agents !== "single" && agents !== "multi") {
      throw new ValueError("agents must be 'single' or 'multi', got " + pyRepr(agents));
    }
  }

  function opt(o, camel, snake, dflt) {
    if (o && o[camel] !== undefined) return o[camel];
    if (o && snake && o[snake] !== undefined) return o[snake];
    return dflt;
  }

  function result(consistent, reason, path, agents, compat, pathString) {
    return { consistent: consistent, reason: reason, agents: agents, compat: compat || null,
             path_string: pathString === undefined ? (path ? pathToString(path) : null) : pathString,
             path: path };
  }

  /**
   * Problem 1: validate(map, story, history, {agents: "single"|"multi", unreportedVisits,
   * compat: null|"original"}) -> {consistent, reason, agents, compat, path_string, path}.
   */
  function validate(m, story, history, opts) {
    var agents = opt(opts, "agents", null, "single");
    var compat = opt(opts, "compat", null, null);
    var loose = !!opt(opts, "unreportedVisits", "unreported_visits", false);
    checkAgents(agents);
    if (compat !== null) {
      if (compat !== "original") throw new ValueError("compat must be None or 'original', got " + pyRepr(compat));
      if (loose) throw new ValueError("unreported_visits is not available with compat='original'");
      return validateOriginal(m, story, history, agents);
    }
    var storyL = parseStory(story, m);
    if (!storyL.length) throw new InputError("the story must name at least one room");
    var events = parseHistory(history, m);
    var bad = checkHistory(m, events, agents);
    if (bad !== null) return result(false, "malformed history: " + bad, null, agents);
    var r = search(new Problem(m, storyL, events, agents === "multi", loose));
    if (r[0] === null) return result(false, r[1], null, agents);
    return result(true, null, r[0], agents);
  }

  function validateOriginal(m, story, history, agents) {
    if (!CD.original || !CD.original.validateCompat) {
      throw new NotImplementedError("compat='original' needs docs/js/original.js (CyberDetectives.original), " +
                                    "which is not loaded");
    }
    var storyL = parseStory(story);
    var events = parseHistory(history, m, { checkNames: false });
    var spec = m.toDict();
    // the original's builder reads beams as an object (toDict may give the pair form)
    spec.beams = objectFrom(Array.from(m.beams, function (kv) { return [kv[0], kv[1].slice()]; }));
    var r = CD.original.validateCompat(spec, storyL,
                                       events.map(function (e) { return [e.sensor, e.kind]; }), agents);
    return result(!!r.consistent, r.consistent ? null : "the original code returns false", null, agents,
                  "original", r.consistent ? r.path_string : null);
  }

  /**
   * replay(map, path, history, {agents, story, unreportedVisits}): throws InvalidPath when the
   * witness walk breaks a rule (same checks and messages as Python's replay).
   */
  function replay(m, path, history, opts) {
    var agents = opt(opts, "agents", null, "single");
    var storyArg = opt(opts, "story", null, null);
    var loose = !!opt(opts, "unreportedVisits", "unreported_visits", false);
    checkAgents(agents);
    var multi = agents === "multi";
    var events = parseHistory(history, m);
    var storyL = storyArg !== null ? parseStory(storyArg, m) : null;
    if (!path || !path.length) throw new InvalidPath("empty path");

    function fail(i, msg) {
      throw new InvalidPath("step " + (i + 1) + " (" + (i < path.length ? path[i].kind : "end") + "): " + msg);
    }

    var active = [new Set()];
    events.forEach(function (e) {
      var s = new Set(active[active.length - 1]);
      if (m.isOccupancy(e.sensor)) { if (e.kind === "A") s.add(e.sensor); else s.delete(e.sensor); }
      active.push(s);
    });

    function where(name) {
      try { return m.kind(name); } catch (e) { if (e instanceof KeyError) return "unknown"; throw e; }
    }
    function nz(x) { return x === undefined ? null : x; }

    var first = path[0];
    if (first.kind !== "start" || first.time !== 0 || first.story_index !== 1) {
      fail(0, "a walk must begin with a start step at time 0");
    }
    if (where(first.position) !== "room") fail(0, "x must start inside a room");
    var visits = [first.position];
    var pos = first.position, t = 0, k = 1;

    function waitUntil(i, until) {
      for (var h = t; h < until; h++) {
        var e = events[h];
        if (!multi) fail(i, "recording " + (h + 1) + " (" + e.sensor + " " + e.kind + ") is not explained by x");
        if (e.kind === "D" && pos === e.sensor) {
          fail(i, "x is inside " + pos + " when it deactivates (recording " + (h + 1) + ")");
        }
      }
      t = until;
    }

    for (var i = 1; i < path.length; i++) {
      var s = path[i];
      var sSensor = nz(s.sensor), sEvent = nz(s.event);
      if (s.time < t || s.time > events.length) fail(i, "time " + s.time + " out of order (now " + t + ")");
      var nw;
      if (s.kind === "cross" || s.kind === "enter" || s.kind === "exit") {
        if (sEvent === null || s.time !== sEvent + 1) fail(i, "event step without matching event index");
        waitUntil(i, sEvent);
        var e = events[sEvent];
        if (s.kind === "cross") {
          if (!m.isBeam(e.sensor) || m.beams.get(e.sensor).indexOf(sSensor) < 0) {
            fail(i, "crossing side " + pyRepr(sSensor) + " does not belong to recording " + pyStr(e));
          }
          if (where(pos) !== "region" || m.sideRegion(sSensor) !== pos) {
            fail(i, "x at " + pos + " is not next to beam side " + sSensor);
          }
          nw = m.sideRegion(m.otherSide(sSensor));
        } else if (multi) {
          fail(i, "in multi-agent mode occupancy recordings do not move x");
        } else if (s.kind === "enter") {
          if (!evEq(e, sSensor, "A") || !m.isOccupancy(e.sensor)) {
            fail(i, "enter step does not match recording " + pyStr(e));
          }
          if (where(pos) !== "region" || m.regions.get(pos).indexOf(sSensor) < 0) {
            fail(i, "x at " + pos + " cannot enter " + sSensor);
          }
          nw = sSensor;
        } else {
          if (!evEq(e, sSensor, "D") || pos !== sSensor) {
            fail(i, "exit step does not match recording " + pyStr(e) + " / position " + pos);
          }
          nw = s.position;
          if (where(nw) !== "region" || m.regions.get(nw).indexOf(sSensor) < 0) {
            fail(i, sSensor + " does not open into " + nw);
          }
        }
        if (s.position !== nw) fail(i, "position " + s.position + ", expected " + nw);
        pos = nw;
        t = s.time;
      } else if (s.kind === "visit" || s.kind === "move" || s.kind === "unreported") {
        if (sEvent !== null) fail(i, "free move with an event index");
        waitUntil(i, s.time);
        var a = where(pos), b = where(s.position);
        var ro = (a === "region" && b === "occupancy") || (a === "occupancy" && b === "region");
        var want = ro ? (a === "occupancy" ? pos : s.position) : null;
        if (sSensor !== want) fail(i, "sensor " + pyRepr(sSensor) + ", expected " + pyRepr(want));
        if ((a === "room" && b === "region") || (a === "region" && b === "room")) {
          var room = a === "room" ? pos : s.position, reg = a === "room" ? s.position : pos;
          if (m.regions.get(reg).indexOf(room) < 0) fail(i, room + " does not touch " + reg);
          if (b === "room") {
            if (s.kind === "visit") { visits.push(s.position); k += 1; }
            else if (s.kind === "move" || !loose) fail(i, "entering " + s.position + " without reporting it");
          } else if (s.kind !== "move") {
            fail(i, "leaving a room is a plain move");
          }
        } else if (ro) {
          var occ = a === "occupancy" ? pos : s.position, reg2 = a === "occupancy" ? s.position : pos;
          if (s.kind !== "move" || !multi) fail(i, "x cannot pass " + occ + " without a recording in single-agent mode");
          if (m.regions.get(reg2).indexOf(occ) < 0) fail(i, occ + " does not touch " + reg2);
          if (!active[t].has(occ)) fail(i, occ + " is not active between recordings " + t + " and " + (t + 1));
        } else {
          fail(i, "cannot move from " + pos + " (" + a + ") to " + s.position + " (" + b + ")");
        }
        pos = s.position;
      } else {
        fail(i, "unknown step kind " + pyRepr(s.kind));
      }
      if (s.story_index !== k) fail(i, "story_index " + s.story_index + ", expected " + k);
    }
    waitUntil(path.length, events.length);
    if (storyL !== null && (visits.length !== storyL.length || visits.some(function (v, j) { return v !== storyL[j]; }))) {
      throw new InvalidPath("reported visits " + visits.join("") + " do not spell the story " + storyL.join(""));
    }
    if (pos !== visits[visits.length - 1] || where(pos) !== "room") {
      throw new InvalidPath("x ends at " + pos + ", not inside the last story room " + visits[visits.length - 1]);
    }
  }

  function positionsPass(p, starts, goal) {
    var m = p.m;
    var mEv = p.events.length;
    var layers = [];
    var frontier = starts.slice();
    for (var h = 0; h <= mEv; h++) {
      var layer = new NativeMap();
      frontier.forEach(function (s) { layer.set(sk(s[0], s[1]), s); });
      var queue = frontier.slice();
      for (var qi = 0; qi < queue.length; qi++) {
        p.freeMoves(h, queue[qi][0], queue[qi][1]).forEach(function (mv) {
          var key = sk(mv[0], mv[1]);
          if (!layer.has(key)) { var st = [mv[0], mv[1]]; layer.set(key, st); queue.push(st); }
        });
      }
      layers.push(layer);
      if (h === mEv) break;
      var nxt = new NativeMap();
      layer.forEach(function (s) {
        p.eventMoves(h, s[0], s[1]).forEach(function (mv) {
          var key = sk(mv[0], s[1]);
          if (!nxt.has(key)) nxt.set(key, [mv[0], s[1]]);
        });
      });
      frontier = Array.from(nxt.values());
    }
    var order = new NativeMap();
    m.rooms.concat(Array.from(m.regions.keys()), m.occupancy).forEach(function (x, i) { order.set(x, i); });
    var out = [];
    var alive = new Set();
    for (var hh = mEv; hh >= 0; hh--) {
      var lay = layers[hh];
      var seeds = [];
      lay.forEach(function (s, key) {
        if (hh === mEv) {
          if (goal === null || (s[0] === goal[0] && s[1] === goal[1])) seeds.push(key);
        } else if (p.eventMoves(hh, s[0], s[1]).some(function (mv) { return alive.has(sk(mv[0], s[1])); })) {
          seeds.push(key);
        }
      });
      var back = new NativeMap();
      lay.forEach(function (s, key) {
        p.freeMoves(hh, s[0], s[1]).forEach(function (mv) {
          var k2 = sk(mv[0], mv[1]);
          if (!back.has(k2)) back.set(k2, []);
          back.get(k2).push(key);
        });
      });
      var good = new Set(seeds);
      var q = seeds.slice();
      for (var j = 0; j < q.length; j++) {
        (back.get(q[j]) || []).forEach(function (r) { if (!good.has(r)) { good.add(r); q.push(r); } });
      }
      alive = good;
      var posSet = new Set();
      good.forEach(function (key) { posSet.add(lay.get(key)[0]); });
      out[hh] = Array.from(posSet).sort(function (a, b) { return order.get(a) - order.get(b); });
    }
    return out;
  }

  /**
   * possiblePositions(map, history, {story, agents, unreportedVisits, starts}) -> one array of
   * positions per time slot (slot h = between recordings h and h+1), in map order (rooms,
   * regions, occupancy).  story null/undefined = the pure sensor filter (room entries free,
   * end anywhere), starting per `starts`: "rooms" (default; inside some room) or "anywhere"
   * (inside some room or in some free region, never inside an occupancy region).  "anywhere"
   * needs story null; any other `starts` value is a ValueError.
   */
  function possiblePositions(m, history, opts) {
    var story = opt(opts, "story", null, null);
    var agents = opt(opts, "agents", null, "single");
    var loose = !!opt(opts, "unreportedVisits", "unreported_visits", false);
    var starts = opt(opts, "starts", null, "rooms");
    checkAgents(agents);
    if (starts !== "rooms" && starts !== "anywhere") {
      throw new ValueError("starts must be 'rooms' or 'anywhere', got " + pyRepr(starts));
    }
    if (starts === "anywhere" && story !== null) {
      throw new ValueError("starts='anywhere' needs story=None (with a story, x starts inside its first room)");
    }
    var events = parseHistory(history, m);
    var storyL = null;
    if (story !== null) {
      storyL = parseStory(story, m);
      if (!storyL.length) throw new InputError("the story must name at least one room");
    }
    var empty = function () { var o = []; for (var i = 0; i <= events.length; i++) o.push([]); return o; };
    if (checkHistory(m, events, agents) !== null) return empty();
    var multi = agents === "multi";
    if (storyL === null) {
      var p0 = new Problem(m, [], events, multi, true);
      var places = starts === "anywhere" ? m.rooms.concat(Array.from(m.regions.keys())) : m.rooms;
      return positionsPass(p0, places.map(function (x) { return [x, 0]; }), null);
    }
    var p = new Problem(m, storyL, events, multi, loose);
    return positionsPass(p, [[storyL[0], 1]], [storyL[storyL.length - 1], storyL.length]);
  }

  // ======================================================================== problems.py

  var INTERVAL_CASES = {
    1: "t0 < tf < t0' < tf'",
    2: "t0 < t0' < tf < tf'",
    3: "t0' < t0 < tf < tf'",
    4: "t0 < t0' < tf' < tf",
    5: "t0' < t0 < tf' < tf",
    6: "t0' < tf' < t0 < tf"
  };
  var P1_MARKS = [new Set(["t0", "t0'"]), new Set(["tf", "tf'"])];
  function caseMarks(c) { return INTERVAL_CASES[c].split(" < ").map(function (x) { return new Set([x]); }); }

  var VERDICT = "verdict", SUPER = "superstory", EDIT = "edit";

  function noCompat(compat) {
    if (compat !== null && compat !== undefined) {
      throw new ValueError("compat=" + pyRepr(compat) + ": Problems 2-4 have no original code");
    }
  }

  function Info(kind, position, sensor, event, op) {
    return { kind: kind, position: position, sensor: sensor === undefined ? null : sensor,
             event: event === undefined ? null : event, op: op === undefined ? null : op };
  }

  // state: [h, ph, pos, k, fresh, anchor]; fresh in {null, true, false}, anchor null|room
  function stKey(st) {
    return st[0] + "|" + st[1] + "|" + st[2] + "|" + st[3] + "|" +
      (st[4] === null ? "n" : (st[4] ? "t" : "f")) + "|" + (st[5] === null ? "\u0000" : st[5]);
  }

  function Timeline(m, story, events, multi, loose, marks, mode, anchored) {
    this.m = m;
    this.story = story;
    this.n = story.length;
    this.events = events;
    this.nEv = events.length;
    this.multi = multi;
    this.loose = loose;
    this.marks = marks;
    this.mode = mode;
    this.anchored = anchored === undefined ? true : anchored;
    this.base = new Problem(m, story, events, multi, loose);
    this.flags = [];
    var passed = new Set();
    for (var ph = 0; ph <= marks.length; ph++) {
      if (ph) marks[ph - 1].forEach(function (x) { passed.add(x); });
      this.flags.push([passed.has("t0") && !passed.has("tf"), passed.has("t0'") && !passed.has("tf'"),
                       passed.has("tf")]);
    }
    this.positions = m.rooms.concat(Array.from(m.regions.keys()), m.occupancy);
  }

  Timeline.prototype.report = function (room, k, first) {
    var out = [];
    var matches = k < this.n && this.story[k] === room;
    if (matches) out.push([0, k + 1, "match"]);
    if (this.mode === VERDICT) return out;
    if (this.mode === SUPER) {
      if (!(first && this.anchored)) out.push([1, k, "insert"]);
      return out;
    }
    if (k < this.n && !matches) out.push([1, k + 1, "substitute"]);
    out.push([1, k, "insert"]);
    return out;
  };

  Timeline.prototype.entered = function (room, reported, anchor) {
    if (!this.loose) return [null, null];
    if (reported) return [true, null];
    return [anchor === room, anchor];
  };

  Timeline.prototype.successors = function (st) {
    var h = st[0], ph = st[1], pos = st[2], k = st[3], fresh = st[4], anchor = st[5];
    var m = this.m, self = this, out = [];
    var fl = this.flags[ph], storyOn = fl[0], sensorsOn = fl[1], tfPassed = fl[2];
    var kind = m.kind(pos);
    if (ph < this.marks.length) this.mark(st).forEach(function (x) { out.push(x); });
    if (kind === "room") {
      m.regionsOf.get(pos).forEach(function (r) { out.push([0, [h, ph, r, k, null, anchor], Info("move", r)]); });
    } else if (kind === "region") {
      m.regions.get(pos).forEach(function (f) {
        var fk = m.kind(f);
        if (fk === "room") {
          if (storyOn) {
            self.report(f, k, false).forEach(function (rp) {
              var fa = self.entered(f, true, anchor);
              out.push([rp[0], [h, ph, f, rp[1], fa[0], fa[1]], Info("visit", f, null, null, rp[2])]);
            });
          }
          if (self.loose || !storyOn) {
            var fa2 = self.entered(f, false, anchor);
            out.push([0, [h, ph, f, k, fa2[0], fa2[1]], Info("unreported", f)]);
          }
        } else if (fk === "occupancy") {
          if (!sensorsOn) out.push([0, [h, ph, f, k, null, anchor], Info("unseen", f, f)]);
          else if (self.multi && self.base.active[h].has(f)) out.push([0, [h, ph, f, k, null, anchor], Info("move", f, f)]);
        } else if (!sensorsOn) {
          var dst = m.sideRegion(m.otherSide(f));
          out.push([0, [h, ph, dst, k, null, anchor], Info("pass", dst, f)]);
        }
      });
    } else if (kind === "occupancy") {
      if (!sensorsOn || (this.multi && this.base.active[h].has(pos))) {
        m.regionsOf.get(pos).forEach(function (r) {
          out.push([0, [h, ph, r, k, null, anchor], Info(sensorsOn ? "move" : "unseen", r, pos)]);
        });
      }
    }
    if (this.mode === EDIT && k < this.n && !tfPassed) {
      out.push([1, [h, ph, pos, k + 1, fresh, anchor], Info("delete", pos, null, null, "delete")]);
    }
    if (sensorsOn && h < this.nEv) {
      this.base.eventMoves(h, pos, k).forEach(function (mv) {
        var npos = mv[0], step = mv[1];
        var nf = npos === pos ? fresh : null;
        if (step === null) out.push([0, [h + 1, ph, npos, k, nf, anchor], Info("wait", npos, null, h)]);
        else out.push([0, [h + 1, ph, npos, k, nf, anchor], Info(step.kind, npos, step.sensor, h)]);
      });
    }
    return out;
  };

  Timeline.prototype.mark = function (st) {
    var h = st[0], ph = st[1], pos = st[2], k = st[3], fresh = st[4], anchor = st[5];
    var mk = this.marks[ph];
    var label = ["t0", "t0'", "tf", "tf'"].filter(function (x) { return mk.has(x); }).join(",");
    var kind = this.m.kind(pos);
    var out = [];
    if ((mk.has("t0'") || mk.has("tf'")) && kind === "occupancy") return out;
    if (mk.has("tf")) {
      if (kind !== "room" || k !== this.n || (this.loose && !fresh)) return out;
      if ((this.mode === VERDICT || (this.mode === SUPER && this.anchored)) && pos !== this.story[this.n - 1]) return out;
    }
    if (mk.has("t0")) {
      if (kind !== "room") return out;
      var nf = this.loose ? true : null, na = this.loose ? pos : null;
      this.report(pos, k, true).forEach(function (rp) {
        out.push([rp[0], [h, ph + 1, pos, rp[1], nf, na], Info("mark", pos, label, null, rp[2])]);
      });
      return out;
    }
    out.push([0, [h, ph + 1, pos, k, fresh, anchor], Info("mark", pos, label)]);
    return out;
  };

  Timeline.prototype.isGoal = function (st) { return st[0] === this.nEv && st[1] === this.marks.length; };

  /** Binary min-heap of [cost, moves, seq, state] (keys are distinct, so pops are deterministic). */
  function Heap() { this.a = []; }
  function heapLess(x, y) { return x[0] !== y[0] ? x[0] < y[0] : (x[1] !== y[1] ? x[1] < y[1] : x[2] < y[2]); }
  Heap.prototype.push = function (x) {
    var a = this.a;
    a.push(x);
    var i = a.length - 1;
    while (i > 0) {
      var pi = (i - 1) >> 1;
      if (!heapLess(a[i], a[pi])) break;
      var tmp = a[i]; a[i] = a[pi]; a[pi] = tmp;
      i = pi;
    }
  };
  Heap.prototype.pop = function () {
    var a = this.a;
    var top = a[0];
    var last = a.pop();
    if (a.length) {
      a[0] = last;
      var i = 0;
      for (;;) {
        var l = 2 * i + 1, r = l + 1, s = i;
        if (l < a.length && heapLess(a[l], a[s])) s = l;
        if (r < a.length && heapLess(a[r], a[s])) s = r;
        if (s === i) break;
        var tmp = a[i]; a[i] = a[s]; a[s] = tmp;
        i = s;
      }
    }
    return top;
  };

  /** Dijkstra on (cost, transitions), FIFO tie-break: [cost, [[state, info|null], ...]] or null. */
  Timeline.prototype.solve = function () {
    var dist = new NativeMap(), parent = new NativeMap();
    var heap = new Heap();
    var seq = 0;
    this.positions.forEach(function (p) {
      var s0 = [0, 0, p, 0, null, null];
      var key = stKey(s0);
      dist.set(key, [0, 0]);
      parent.set(key, null);
      heap.push([0, 0, seq, s0]);
      seq += 1;
    });
    while (heap.a.length) {
      var item = heap.pop();
      var d = item[0], mv = item[1], st = item[3];
      var sKey = stKey(st);
      var best = dist.get(sKey);
      if (d > best[0] || (d === best[0] && mv > best[1])) continue;
      if (this.isGoal(st)) {
        var chain = [];
        var cur = st;
        while (cur !== null) {
          var pr = parent.get(stKey(cur));
          chain.push([cur, pr ? pr[1] : null]);
          cur = pr ? pr[0] : null;
        }
        chain.reverse();
        return [d, chain];
      }
      var succ = this.successors(st);
      for (var i = 0; i < succ.length; i++) {
        var c = succ[i][0], nst = succ[i][1], info = succ[i][2];
        var nk = d + c, nm = mv + 1;
        var key = stKey(nst);
        var old = dist.get(key);
        if (old === undefined || nk < old[0] || (nk === old[0] && nm < old[1])) {
          dist.set(key, [nk, nm]);
          parent.set(key, [st, info]);
          heap.push([nk, nm, seq, nst]);
          seq += 1;
        }
      }
    }
    return null;
  };

  function editOpString(o) {
    if (o.op === "substitute") return "substitute " + o.old + " at " + o.index + " by " + o["new"];
    if (o.op === "insert") return "insert " + o["new"] + " before " + o.index;
    return "delete " + o.old + " at " + o.index;
  }

  function storyWalk(tl, chain) {
    var nw = [], steps = [], ops = [], inserted = [];
    var started = false;
    function report(prevK, room, op) {
      nw.push(room);
      if (op === "insert") {
        inserted.push(nw.length - 1);
        ops.push({ op: "insert", index: prevK, old: null, "new": room });
      } else if (op === "substitute") {
        ops.push({ op: "substitute", index: prevK, old: tl.story[prevK], "new": room });
      }
    }
    var prev = null;
    for (var i = 0; i < chain.length; i++) {
      var st = chain[i][0], info = chain[i][1];
      if (info === null) { prev = st; continue; }
      var h = prev[0], kPrev = prev[3];
      if (info.kind === "delete") {
        ops.push({ op: "delete", index: kPrev, old: tl.story[kPrev], "new": null });
      } else if (info.kind === "mark") {
        var lab = info.sensor.split(",");
        if (lab.indexOf("t0") >= 0) {
          started = true;
          report(kPrev, info.position, info.op);
          steps.push(Step("start", info.position, 0, 1));
        } else if (lab.indexOf("tf") >= 0) {
          break;
        }
      } else if (!started) {
        // moves before t0 are dropped
      } else if (info.kind === "visit") {
        report(kPrev, info.position, info.op);
        steps.push(Step("visit", info.position, h, nw.length));
      } else if (info.kind === "unreported") {
        steps.push(Step("unreported", info.position, h, nw.length));
      } else if (info.kind === "move") {
        steps.push(Step("move", info.position, h, nw.length, info.sensor));
      } else if (info.kind === "cross" || info.kind === "enter" || info.kind === "exit") {
        steps.push(Step(info.kind, info.position, info.event + 1, nw.length, info.sensor, info.event));
      } else if (info.kind === "wait") {
        // nothing
      } else {
        throw new Error("unexpected transition " + info.kind);
      }
      prev = st;
    }
    return [nw, steps, ops, inserted];
  }

  function intervalWalk(chain) {
    var steps = [];
    var reported = 0;
    var prev = null;
    chain.forEach(function (ci) {
      var st = ci[0], info = ci[1];
      if (info === null) { steps.push(Step("begin", st[2], 0, 0)); prev = st; return; }
      var h = prev[0];
      if (info.kind === "mark") {
        if (info.op !== null) reported = 1;
        steps.push(Step("mark", info.position, h, reported, info.sensor));
      } else if (info.kind === "visit") {
        reported += 1;
        steps.push(Step("visit", info.position, h, reported));
      } else if (["unreported", "move", "pass", "unseen"].indexOf(info.kind) >= 0) {
        steps.push(Step(info.kind, info.position, h, reported, info.sensor));
      } else if (["cross", "enter", "exit"].indexOf(info.kind) >= 0) {
        steps.push(Step(info.kind, info.position, info.event + 1, reported, info.sensor, info.event));
      }
      prev = st;
    });
    return steps;
  }

  /** Problem 2 witness as text: |t0|, <b1u> for unrecorded passes, (X) for other entries. */
  function intervalPathString(path) {
    if (path === null) return null;
    var out = [];
    path.forEach(function (s) {
      if (s.kind === "mark") {
        out.push("|" + s.sensor + "|");
        if (s.sensor.split(",").indexOf("t0") >= 0) out.push(s.position);
      } else if (s.kind === "pass" || (s.kind === "unseen" && s.position === s.sensor)) {
        out.push("<" + s.sensor + ">");
      } else if (s.kind !== "begin") {
        out.push(pathToString([s]));
      }
    });
    return out.join("");
  }

  function inputs(m, story, history, agents, needStory) {
    checkAgents(agents);
    var storyL = parseStory(story, m);
    if (needStory && !storyL.length) throw new InputError("the story must name at least one room");
    var events = parseHistory(history, m);
    return [storyL, events, checkHistory(m, events, agents)];
  }

  /**
   * Problem 2: validateIntervals(map, story, history, {case: 1..6 (default 2), agents,
   * unreportedVisits}) -> {consistent, reason, agents, compat, path_string, path, case, interval}.
   */
  function validateIntervals(m, story, history, opts) {
    noCompat(opt(opts, "compat", null, null));
    var c = opt(opts, "case", null, 2);
    var agents = opt(opts, "agents", null, "single");
    var loose = !!opt(opts, "unreportedVisits", "unreported_visits", false);
    if (!(typeof c === "number" && Number.isInteger(c) && c >= 1 && c <= 6)) {
      throw new ValueError("case must be 1..6, got " + pyRepr(c));
    }
    var inp = inputs(m, story, history, agents, true);
    var mk = function (consistent, reason, path) {
      var r = result(consistent, reason, path, agents, null, intervalPathString(path));
      r["case"] = c;
      r.interval = INTERVAL_CASES[c];
      return r;
    };
    if (inp[2] !== null) return mk(false, "malformed history: " + inp[2], null);
    var tl = new Timeline(m, inp[0], inp[1], agents === "multi", loose, caseMarks(c), VERDICT);
    var sol = tl.solve();
    if (sol === null) {
      return mk(false, "no walk tells the story over [t0, tf] and explains the history over [t0', tf'] with " +
                INTERVAL_CASES[c] + " (case " + c + ")", null);
    }
    return mk(true, null, intervalWalk(sol[1]));
  }

  /**
   * Problem 3: shortestSuperstory(map, story, history, {anchored (default true), agents,
   * unreportedVisits}) -> null | {story, length, inserted, anchored, agents, path_string, path}.
   */
  function shortestSuperstory(m, story, history, opts) {
    noCompat(opt(opts, "compat", null, null));
    var anchored = opt(opts, "anchored", null, true);
    var agents = opt(opts, "agents", null, "single");
    var loose = !!opt(opts, "unreportedVisits", "unreported_visits", false);
    var inp = inputs(m, story, history, agents, anchored);
    if (inp[2] !== null) return null;
    var tl = new Timeline(m, inp[0], inp[1], agents === "multi", loose, P1_MARKS, SUPER, anchored);
    var sol = tl.solve();
    if (sol === null) return null;
    var w = storyWalk(tl, sol[1]);
    return { story: w[0], length: w[0].length, inserted: w[3], anchored: anchored, agents: agents,
             path_string: pathToString(w[1]), path: w[1] };
  }

  /**
   * Problem 4: closestStory(map, story, history, {agents, unreportedVisits}) -> null |
   * {story, edits, operations (strings), edit_ops ([{op, index, old, new}]), agents,
   * path_string, path}.
   */
  function closestStory(m, story, history, opts) {
    noCompat(opt(opts, "compat", null, null));
    var agents = opt(opts, "agents", null, "single");
    var loose = !!opt(opts, "unreportedVisits", "unreported_visits", false);
    var inp = inputs(m, story, history, agents, false);
    if (inp[2] !== null) return null;
    var tl = new Timeline(m, inp[0], inp[1], agents === "multi", loose, P1_MARKS, EDIT);
    var sol = tl.solve();
    if (sol === null) return null;
    var w = storyWalk(tl, sol[1]);
    return { story: w[0], edits: sol[0], operations: w[2].map(editOpString), edit_ops: w[2], agents: agents,
             path_string: pathToString(w[1]), path: w[1] };
  }

  // ======================================================================== exports

  CD.version = "1.0.0";
  CD.Map = CDMap;
  CD.Event = Event;
  CD.ValueError = ValueError;
  CD.MapError = MapError;
  CD.InputError = InputError;
  CD.InvalidPath = InvalidPath;
  CD.KeyError = KeyError;
  CD.NotImplementedError = NotImplementedError;
  CD.parseStory = parseStory;
  CD.parseHistory = parseHistory;
  CD.historyToString = historyToString;
  CD.checkHistory = checkHistory;
  CD.checkSingleAgentHistory = checkSingleAgentHistory;
  CD.checkMultiAgentHistory = checkMultiAgentHistory;
  CD.validate = validate;
  CD.replay = replay;
  CD.pathToString = pathToString;
  CD.possiblePositions = possiblePositions;
  CD.validateIntervals = validateIntervals;
  CD.shortestSuperstory = shortestSuperstory;
  CD.closestStory = closestStory;
  CD.intervalPathString = intervalPathString;
  CD.INTERVAL_CASES = INTERVAL_CASES;
  CD._py = { repr: pyRepr, str: pyStr, Tuple: PyTuple, typeName: pyTypeName };
})(typeof globalThis !== "undefined" ? globalThis : this);
