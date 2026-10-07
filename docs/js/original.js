/* Cyber Detectives: JavaScript port of compat="original" (src/cyber_detectives/compat/
 * original.py and javahash.py): the original Java code's validateAgentStory + getAgentStory
 * (single agent) and validateAgentStoryMulti, bugs and crashes included, run the way the
 * applet runs them (validate_compat).
 *
 * Classic script; load it AFTER docs/js/engine.js.  It adds CyberDetectives.original:
 *   validateCompat(mapDict, storyList, eventPairs, agents="single")
 *       -> {consistent, path_string}; throws JavaException (the original crashed) or
 *          ValueError (input the original cannot express)
 *   JavaException (e.name "JavaException"; e.javaClass, e.javaMessage, e.frames,
 *       e.topFrame, e.originFrame, e.describe() -> {class, message, top_frame, origin_frame,
 *       trace}; e.message = Python str(): "java.lang.NullPointerException at ...")
 *   JavaHashSet, JavaHashMap: Java 8 HashMap/HashSet iteration order under the canonical JVM
 *       setting -XX:hashCode=2 (every identity hash is 1), treeified bins included
 *   buildGame, makeHistory, validateAgentStory, getAgentStory, getAgentStoryStatuses,
 *       validateAgentStoryMulti, getSubGraph, getSubGraphMulti, getReachableSubgraph
 * System.out output is not reproduced (the Python port's capture_stdout); only the crashes
 * the printing code can raise are.
 */
(function (root) {
  "use strict";

  var CD = root.CyberDetectives;
  if (!CD || !CD._py) throw new Error("load docs/js/engine.js before docs/js/original.js");
  var pyRepr = CD._py.repr;
  var ValueError = CD.ValueError;
  var NativeMap = root.Map;

  // ======================================================================== javahash.py

  var IDENTITY_HASH = 1;
  var DEFAULT_INITIAL_CAPACITY = 16;
  var MAXIMUM_CAPACITY = 1 << 30;
  var TREEIFY_THRESHOLD = 8;
  var UNTREEIFY_THRESHOLD = 6;
  var MIN_TREEIFY_CAPACITY = 64;
  var INT_MAX = 2147483647;

  function javaStringHash(s) {
    var h = 0;
    for (var i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
    return h;
  }

  function identityHash(k) {
    if (k === null) return 0;
    return k.java_identity_hash !== undefined ? k.java_identity_hash : IDENTITY_HASH;
  }

  function javaHashCode(k) {
    if (k === null) return 0;
    if (typeof k === "number") return k | 0;
    if (typeof k === "string") return javaStringHash(k);
    return identityHash(k);
  }

  function spread(h) { return (h ^ (h >>> 16)) | 0; }

  function hashOf(k) { return k === null ? 0 : spread(javaHashCode(k)); }

  function className(k) {
    if (typeof k === "number") return "java.lang.Integer";
    if (typeof k === "string") return "java.lang.String";
    if (k.JAVA_CLASS) return k.JAVA_CLASS;
    return (k.constructor && k.constructor.name) || "Object";
  }

  function comparableClassFor(k) {
    if (typeof k === "number") return "java.lang.Integer";
    if (typeof k === "string") return "java.lang.String";
    return null;
  }

  function compareComparables(kc, k, x) {
    if (x === null || className(x) !== kc) return 0;
    return k < x ? -1 : (k > x ? 1 : 0);  // String.compareTo: UTF-16 code units, as JS
  }

  function tieBreakOrder(a, b) {
    var d = 0;
    if (a !== null && b !== null) {
      var ca = className(a), cb = className(b);
      d = ca < cb ? -1 : (ca > cb ? 1 : 0);
    }
    if (a === null || b === null || d === 0) d = identityHash(a) <= identityHash(b) ? -1 : 1;
    return d;
  }

  function TreeNode(h, key, nxt) {
    this.hash = h;
    this.key = key;
    this.next = nxt === undefined ? null : nxt;
    this.prev = null;
    this.parent = null;
    this.left = null;
    this.right = null;
    this.red = false;
  }
  TreeNode.prototype.root = function () {
    var r = this;
    while (r.parent !== null) r = r.parent;
    return r;
  };

  function TreeBin() {
    this.first = null;
    this.nodes = new NativeMap();
  }
  TreeBin.prototype.keys = function () {
    var out = [];
    for (var e = this.first; e !== null; e = e.next) out.push(e.key);
    return out;
  };

  function rotateLeft(root, p) {
    if (p !== null && p.right !== null) {
      var r = p.right;
      var rl = p.right = r.left;
      if (rl !== null) rl.parent = p;
      var pp = r.parent = p.parent;
      if (pp === null) { root = r; r.red = false; }
      else if (pp.left === p) pp.left = r;
      else pp.right = r;
      r.left = p;
      p.parent = r;
    }
    return root;
  }

  function rotateRight(root, p) {
    if (p !== null && p.left !== null) {
      var l = p.left;
      var lr = p.left = l.right;
      if (lr !== null) lr.parent = p;
      var pp = l.parent = p.parent;
      if (pp === null) { root = l; l.red = false; }
      else if (pp.right === p) pp.right = l;
      else pp.left = l;
      l.right = p;
      p.parent = l;
    }
    return root;
  }

  function balanceInsertion(root, x) {
    x.red = true;
    for (;;) {
      var xp = x.parent;
      if (xp === null) { x.red = false; return x; }
      var xpp = xp.parent;
      if (!xp.red || xpp === null) return root;
      var xppl = xpp.left;
      if (xp === xppl) {
        var xppr = xpp.right;
        if (xppr !== null && xppr.red) {
          xppr.red = false; xp.red = false; xpp.red = true; x = xpp;
        } else {
          if (x === xp.right) {
            x = xp;
            root = rotateLeft(root, x);
            xp = x.parent;
            xpp = xp === null ? null : xp.parent;
          }
          if (xp !== null) {
            xp.red = false;
            if (xpp !== null) { xpp.red = true; root = rotateRight(root, xpp); }
          }
        }
      } else {
        if (xppl !== null && xppl.red) {
          xppl.red = false; xp.red = false; xpp.red = true; x = xpp;
        } else {
          if (x === xp.left) {
            x = xp;
            root = rotateRight(root, x);
            xp = x.parent;
            xpp = xp === null ? null : xp.parent;
          }
          if (xp !== null) {
            xp.red = false;
            if (xpp !== null) { xpp.red = true; root = rotateLeft(root, xpp); }
          }
        }
      }
    }
  }

  function balanceDeletion(root, x) {
    for (;;) {
      if (x === null || x === root) return root;
      var xp = x.parent;
      if (xp === null) { x.red = false; return x; }
      if (x.red) { x.red = false; return root; }
      var xpl = xp.left, sl, sr;
      if (xpl === x) {
        var xpr = xp.right;
        if (xpr !== null && xpr.red) {
          xpr.red = false; xp.red = true;
          root = rotateLeft(root, xp);
          xp = x.parent;
          xpr = xp === null ? null : xp.right;
        }
        if (xpr === null) x = xp;
        else {
          sl = xpr.left; sr = xpr.right;
          if ((sr === null || !sr.red) && (sl === null || !sl.red)) {
            xpr.red = true; x = xp;
          } else {
            if (sr === null || !sr.red) {
              if (sl !== null) sl.red = false;
              xpr.red = true;
              root = rotateRight(root, xpr);
              xp = x.parent;
              xpr = xp === null ? null : xp.right;
            }
            if (xpr !== null) {
              xpr.red = xp === null ? false : xp.red;
              sr = xpr.right;
              if (sr !== null) sr.red = false;
            }
            if (xp !== null) { xp.red = false; root = rotateLeft(root, xp); }
            x = root;
          }
        }
      } else {
        if (xpl !== null && xpl.red) {
          xpl.red = false; xp.red = true;
          root = rotateRight(root, xp);
          xp = x.parent;
          xpl = xp === null ? null : xp.left;
        }
        if (xpl === null) x = xp;
        else {
          sl = xpl.left; sr = xpl.right;
          if ((sl === null || !sl.red) && (sr === null || !sr.red)) {
            xpl.red = true; x = xp;
          } else {
            if (sl === null || !sl.red) {
              if (sr !== null) sr.red = false;
              xpl.red = true;
              root = rotateLeft(root, xpl);
              xp = x.parent;
              xpl = xp === null ? null : xp.left;
            }
            if (xpl !== null) {
              xpl.red = xp === null ? false : xp.red;
              sl = xpl.left;
              if (sl !== null) sl.red = false;
            }
            if (xp !== null) { xp.red = false; root = rotateRight(root, xp); }
            x = root;
          }
        }
      }
    }
  }

  function moveRootToFront(tb, root) {
    if (root === null) return;
    var first = tb.first;
    if (root !== first) {
      tb.first = root;
      var rp = root.prev, rn = root.next;
      if (rn !== null) rn.prev = rp;
      if (rp !== null) rp.next = rn;
      if (first !== null) first.prev = root;
      root.next = first;
      root.prev = null;
    }
  }

  function dirFor(h, k, p, kcBox) {
    var ph = p.hash;
    if (ph > h) return -1;
    if (ph < h) return 1;
    var kc = kcBox[0];
    if (kc === undefined) kc = kcBox[0] = comparableClassFor(k);
    if (kc === null) return tieBreakOrder(k, p.key);
    var d = compareComparables(kc, k, p.key);
    if (d === 0) d = tieBreakOrder(k, p.key);
    return d;
  }

  function treeify(tb, nodes) {
    var root = null;
    nodes.forEach(function (x) {
      x.left = x.right = null;
      if (root === null) { x.parent = null; x.red = false; root = x; return; }
      var k = x.key, h = x.hash, kcBox = [undefined], p = root;
      for (;;) {
        var d = dirFor(h, k, p, kcBox);
        var xp = p;
        p = d <= 0 ? p.left : p.right;
        if (p === null) {
          x.parent = xp;
          if (d <= 0) xp.left = x; else xp.right = x;
          root = balanceInsertion(root, x);
          break;
        }
      }
    });
    moveRootToFront(tb, root);
  }

  function makeTreeBin(keys, hashes) {
    var tb = new TreeBin();
    var prev = null, nodes = [];
    keys.forEach(function (k) {
      var n = new TreeNode(hashes.get(k), k);
      n.prev = prev;
      if (prev !== null) prev.next = n; else tb.first = n;
      prev = n;
      nodes.push(n);
      tb.nodes.set(k, n);
    });
    treeify(tb, nodes);
    return tb;
  }

  function putTreeVal(tb, h, k) {
    var root = tb.first.parent !== null ? tb.first.root() : tb.first;
    var kcBox = [undefined], p = root;
    for (;;) {
      var d = dirFor(h, k, p, kcBox);
      var xp = p;
      p = d <= 0 ? p.left : p.right;
      if (p === null) {
        var xpn = xp.next;
        var x = new TreeNode(h, k, xpn);
        if (d <= 0) xp.left = x; else xp.right = x;
        xp.next = x;
        x.parent = x.prev = xp;
        if (xpn !== null) xpn.prev = x;
        tb.nodes.set(k, x);
        moveRootToFront(tb, balanceInsertion(root, x));
        return;
      }
    }
  }

  function removeTreeNode(tb, node) {
    tb.nodes.delete(node.key);
    var first = tb.first, root = tb.first;
    var succ = node.next, pred = node.prev;
    if (pred === null) tb.first = first = succ; else pred.next = succ;
    if (succ !== null) succ.prev = pred;
    if (first === null) return [];
    if (root.parent !== null) root = root.root();
    if (root === null || root.right === null || root.left === null || root.left.left === null) {
      return tb.keys();
    }
    var p = node, pl = node.left, pr = node.right, replacement, pp;
    if (pl !== null && pr !== null) {
      var s = pr;
      while (s.left !== null) s = s.left;
      var c = s.red; s.red = p.red; p.red = c;
      var sr = s.right;
      pp = p.parent;
      if (s === pr) { p.parent = s; s.right = p; }
      else {
        var sp = s.parent;
        p.parent = sp;
        if (sp !== null) { if (s === sp.left) sp.left = p; else sp.right = p; }
        s.right = pr;
        if (pr !== null) pr.parent = s;
      }
      p.left = null;
      p.right = sr;
      if (sr !== null) sr.parent = p;
      s.left = pl;
      if (pl !== null) pl.parent = s;
      s.parent = pp;
      if (pp === null) root = s;
      else if (p === pp.left) pp.left = s;
      else pp.right = s;
      replacement = sr !== null ? sr : p;
    } else if (pl !== null) replacement = pl;
    else if (pr !== null) replacement = pr;
    else replacement = p;
    if (replacement !== p) {
      pp = replacement.parent = p.parent;
      if (pp === null) { root = replacement; replacement.red = false; }
      else if (p === pp.left) pp.left = replacement;
      else pp.right = replacement;
      p.left = p.right = p.parent = null;
    }
    var r = p.red ? root : balanceDeletion(root, replacement);
    if (replacement === p) {
      pp = p.parent;
      p.parent = null;
      if (pp !== null) {
        if (p === pp.left) pp.left = null;
        else if (p === pp.right) pp.right = null;
      }
    }
    moveRootToFront(tb, r);
    return null;
  }

  function tableSizeFor(cap) {
    var n = cap - 1;
    n |= n >> 1; n |= n >> 2; n |= n >> 4; n |= n >> 8; n |= n >> 16;
    return n < 0 ? 1 : (n >= MAXIMUM_CAPACITY ? MAXIMUM_CAPACITY : n + 1);
  }

  /** The bucket structure of java.util.HashMap (Java 8), iteration order only. */
  function Table(initialCapacity) {
    this._h = new NativeMap();    // key -> spread hash
    this._bins = new NativeMap(); // bin index -> array of keys | TreeBin
    this._cap = 0;
    if (initialCapacity === undefined || initialCapacity === null) this._thr = 0;
    else {
      if (initialCapacity < 0) throw new ValueError("Illegal initial capacity: " + initialCapacity);
      this._thr = tableSizeFor(Math.min(initialCapacity, MAXIMUM_CAPACITY));
    }
    this._order = null;
  }

  Table.prototype._insert = function (k) {
    var hs = this._h;
    if (hs.has(k)) return false;
    if (this._cap === 0) this._resize();
    var h = hashOf(k);
    hs.set(k, h);
    this._order = null;
    var i = (this._cap - 1) & h;
    var b = this._bins.get(i);
    if (b === undefined) this._bins.set(i, [k]);
    else if (Array.isArray(b)) {
      b.push(k);
      if (b.length > TREEIFY_THRESHOLD) this._treeifyBin(h);
    } else putTreeVal(b, h, k);
    if (hs.size > this._thr) this._resize();
    return true;
  };

  Table.prototype._delete = function (k) {
    var hs = this._h;
    if (!hs.has(k)) return false;
    var h = hs.get(k);
    hs.delete(k);
    this._order = null;
    var i = (this._cap - 1) & h;
    var b = this._bins.get(i);
    if (Array.isArray(b)) {
      b.splice(b.indexOf(k), 1);
      if (!b.length) this._bins.delete(i);
    } else {
      var rest = removeTreeNode(b, b.nodes.get(k));
      if (rest !== null) {
        if (rest.length) this._bins.set(i, rest); else this._bins.delete(i);
      }
    }
    return true;
  };

  Table.prototype._treeifyBin = function (h) {
    if (this._cap < MIN_TREEIFY_CAPACITY) { this._resize(); return; }
    var i = (this._cap - 1) & h;
    var b = this._bins.get(i);
    if (b !== undefined) this._bins.set(i, makeTreeBin(b, this._h));
  };

  Table.prototype._resize = function () {
    var oldCap = this._cap, oldThr = this._thr, newCap, newThr = 0;
    if (oldCap > 0) {
      if (oldCap >= MAXIMUM_CAPACITY) { this._thr = INT_MAX; return; }
      newCap = oldCap * 2;
      if (newCap < MAXIMUM_CAPACITY && oldCap >= DEFAULT_INITIAL_CAPACITY) newThr = oldThr * 2;
    } else if (oldThr > 0) newCap = oldThr;
    else { newCap = DEFAULT_INITIAL_CAPACITY; newThr = 12; }
    if (newThr === 0) {
      var ft = newCap * 0.75;
      newThr = (newCap < MAXIMUM_CAPACITY && ft < MAXIMUM_CAPACITY) ? Math.floor(ft) : INT_MAX;
    }
    this._thr = newThr;
    this._cap = newCap;
    this._order = null;
    if (oldCap === 0) return;
    var hs = this._h;
    var newBins = new NativeMap();
    this._bins.forEach(function (b, j) {
      var keys = Array.isArray(b) ? b : b.keys();
      if (keys.length === 1) { newBins.set(hs.get(keys[0]) & (newCap - 1), b); return; }
      var lo = keys.filter(function (k) { return (hs.get(k) & oldCap) === 0; });
      var hi = keys.filter(function (k) { return (hs.get(k) & oldCap) !== 0; });
      if (Array.isArray(b)) {
        if (lo.length) newBins.set(j, lo);
        if (hi.length) newBins.set(j + oldCap, hi);
      } else {
        if (lo.length) {
          if (lo.length <= UNTREEIFY_THRESHOLD) newBins.set(j, lo);
          else if (hi.length) newBins.set(j, makeTreeBin(lo, hs));
          else newBins.set(j, b);
        }
        if (hi.length) {
          if (hi.length <= UNTREEIFY_THRESHOLD) newBins.set(j + oldCap, hi);
          else if (lo.length) newBins.set(j + oldCap, makeTreeBin(hi, hs));
          else newBins.set(j + oldCap, b);
        }
      }
    });
    this._bins = newBins;
  };

  /** Keys in HashIterator order (cached until the next mutation). */
  Table.prototype._keys = function () {
    if (this._order === null) {
      var bins = this._bins, o = [];
      var idx = Array.from(bins.keys()).sort(function (a, b) { return a - b; });
      idx.forEach(function (i) {
        var b = bins.get(i);
        if (Array.isArray(b)) o.push.apply(o, b); else o.push.apply(o, b.keys());
      });
      this._order = o;
    }
    return this._order;
  };

  Table.prototype._clear = function () {
    this._h = new NativeMap();
    this._bins = new NativeMap();
    this._order = null;
  };

  Table.prototype.tableCapacity = function () { return this._cap; };
  Table.prototype.size = function () { return this._h.size; };
  Table.prototype.isEmpty = function () { return this._h.size === 0; };

  /** java.util.HashSet with Java 8 iteration order (canonical identity hash 1). */
  function JavaHashSet(items, initialCapacity) {
    Table.call(this, initialCapacity);
    if (items) items.forEach(function (x) { this._insert(x); }, this);
  }
  JavaHashSet.prototype = Object.create(Table.prototype);
  JavaHashSet.prototype.constructor = JavaHashSet;
  JavaHashSet.fromCollection = function (c) {
    var s = new JavaHashSet(null, Math.max(Math.floor(c.length / 0.75) + 1, 16));
    c.forEach(function (x) { s._insert(x); });
    return s;
  };
  JavaHashSet.prototype.add = function (k) { return this._insert(k); };
  JavaHashSet.prototype.addAll = function (xs) {
    var changed = false;
    Array.from(xs).forEach(function (x) { if (this._insert(x)) changed = true; }, this);
    return changed;
  };
  JavaHashSet.prototype.remove = function (k) { return this._delete(k); };
  JavaHashSet.prototype.contains = function (k) { return this._h.has(k); };
  JavaHashSet.prototype.clear = function () { this._clear(); };
  JavaHashSet.prototype.toArray = function () { return this._keys().slice(); };

  /** java.util.HashMap with Java 8 iteration order. */
  function JavaHashMap(initialCapacity) {
    Table.call(this, initialCapacity);
    this._v = new NativeMap();
  }
  JavaHashMap.prototype = Object.create(Table.prototype);
  JavaHashMap.prototype.constructor = JavaHashMap;
  JavaHashMap.prototype.put = function (k, v) {
    var old = this._v.has(k) ? this._v.get(k) : null;
    this._insert(k);
    this._v.set(k, v);
    return old;
  };
  JavaHashMap.prototype.get = function (k) { return this._v.has(k) ? this._v.get(k) : null; };
  JavaHashMap.prototype.containsKey = function (k) { return this._h.has(k); };
  JavaHashMap.prototype.remove = function (k) {
    if (this._delete(k)) { var v = this._v.get(k); this._v.delete(k); return v; }
    return null;
  };
  JavaHashMap.prototype.clear = function () { this._clear(); this._v = new NativeMap(); };
  JavaHashMap.prototype.keys = function () { return this._keys().slice(); };
  JavaHashMap.prototype.values = function () {
    var v = this._v;
    return this._keys().map(function (k) { return v.get(k); });
  };

  // ======================================================================== exceptions

  var PKG = "projects.cyberDetective.";

  function frame(cls, method, line) { return PKG + cls + "." + method + "(" + cls + ".java:" + line + ")"; }
  function fa(method, line) { return frame("Algorithms", method, line); }

  /** A Java exception thrown by the original code (Python compat.original.JavaException). */
  function JavaException(javaClass, top, message) {
    var msg = javaClass + (message === null || message === undefined ? "" : ": " + message) + " at " + top;
    var e = new Error(msg);
    Object.setPrototypeOf(e, JavaException.prototype);
    e.name = "JavaException";
    e.javaClass = javaClass;
    e.javaMessage = message === undefined ? null : message;
    e.frames = [top];
    return e;
  }
  JavaException.prototype = Object.create(Error.prototype, {
    constructor: { value: JavaException, writable: true, configurable: true },
    name: { value: "JavaException", writable: true, configurable: true }
  });
  JavaException.prototype.addFrame = function (f) { this.frames.push(f); return this; };
  Object.defineProperty(JavaException.prototype, "topFrame", { get: function () { return this.frames[0]; } });
  Object.defineProperty(JavaException.prototype, "originFrame", {
    get: function () {
      for (var i = 0; i < this.frames.length; i++) {
        var f = this.frames[i];
        if (f.indexOf("projects.") === 0 || f.indexOf("common.") === 0) return f;
      }
      return null;
    }
  });
  JavaException.prototype.describe = function () {
    return { "class": this.javaClass, message: this.javaMessage, top_frame: this.topFrame,
             origin_frame: this.originFrame, trace: this.frames.slice() };
  };

  function NPE(top) { return new JavaException("java.lang.NullPointerException", top, null); }
  function AIOOBE(top, msg) { return new JavaException("java.lang.ArrayIndexOutOfBoundsException", top, msg); }

  /** Run fn; on a JavaException append the caller frame and rethrow. */
  function at(f, fn) {
    try { return fn(); } catch (e) {
      if (e instanceof JavaException) e.addFrame(f);
      throw e;
    }
  }

  function npeHasEdge(method, line) {
    return NPE(frame("Edge", "getEdgeId", 17)).addFrame(frame("Graph", "hasEdgeBetweenVertices", 26))
      .addFrame(fa(method, line));
  }

  // ======================================================================== the classes

  function Vertex(name, id) {
    this.name = name === undefined ? null : name;
    this.id = id === undefined ? 0 : id;
    this.neighbors = new JavaHashSet();
    this.asso_vertex = null;
  }
  Vertex.prototype.JAVA_CLASS = PKG + "Vertex";
  Vertex.prototype.addNeighbor = function (v) { this.neighbors.add(v); };
  Vertex.prototype.getCopy = function () {
    var v = new Vertex();
    v.id = this.id;
    v.name = this.name;
    return v;
  };

  function getEdgeId(a, b) { return a < b ? (a * 65536 + b) | 0 : (b * 65536 + a) | 0; }

  function getEdgeIdV(v1, v2) {
    if (v1 === null || v2 === null) throw NPE(frame("Edge", "getEdgeId", 17));
    return getEdgeId(v1.id, v2.id);
  }

  function Edge(v1, v2) {
    this.vertices = [v1, v2];
    var self = this;
    this.id = at(frame("Edge", "<init>", 13), function () { return getEdgeIdV(v1, v2); });
    void self;
  }
  Edge.prototype.getCopy = function () { return new Edge(this.vertices[0].getCopy(), this.vertices[1].getCopy()); };

  function Graph() {
    this.vertex_ids = new Set();
    this.vertex_map = new JavaHashMap();
    this.vertex_name_map = new NativeMap();
    this.edge_ids = new Set();
    this.edge_map = new JavaHashMap();
  }
  Graph.prototype.nameGet = function (n) { return this.vertex_name_map.has(n) ? this.vertex_name_map.get(n) : null; };
  Graph.prototype.hasEdgeBetweenVertices = function (v1, v2) {
    var self = this;
    return at(frame("Graph", "hasEdgeBetweenVertices", 26), function () {
      return self.edge_map.containsKey(getEdgeIdV(v1, v2));
    });
  };
  Graph.prototype.getEdgeBetweenVertices = function (v1, v2) {
    var self = this;
    return at(frame("Graph", "getEdgeBetweenVertices", 31), function () {
      return self.edge_map.get(getEdgeIdV(v1, v2));
    });
  };
  Graph.prototype.hasEdgeBetweenIds = function (a, b) { return this.edge_map.containsKey(getEdgeId(a, b)); };
  Graph.prototype.addEdgelessVertex = function (v) {
    this.vertex_ids.add(v.id);
    this.vertex_map.put(v.id, v);
    this.vertex_name_map.set(v.name, v);
  };
  Graph.prototype.addCopyOfEdgelessVertex = function (v) {
    if (v === null) throw NPE(frame("Graph", "addCopyOfEdgelessVertex", 56));
    this.addEdgelessVertex(v.getCopy());
  };
  Graph.prototype.addEdge = function (ed) {
    if (!this.edge_ids.has(ed.id)) {
      var vs = ed.vertices;
      for (var i = 0; i < 2; i++) {
        var vi = vs[i];
        if (!this.vertex_ids.has(vi.id)) {
          this.vertex_ids.add(vi.id);
          this.vertex_map.put(vi.id, vi);
          this.vertex_name_map.set(vi.name, vi);
        } else {
          vs[i] = this.vertex_map.get(vi.id);
        }
      }
      vs[0].neighbors.add(vs[1]);
      vs[1].neighbors.add(vs[0]);
      this.edge_ids.add(ed.id);
      this.edge_map.put(ed.id, ed);
    }
  };
  /** Graph.dump() without System.out: only the NPEs on null members remain observable. */
  Graph.prototype.dump = function () {
    this.vertex_map.values().forEach(function (v) {
      if (v.neighbors.contains(null)) throw NPE(frame("Graph", "dump", 105));
    });
    this.edge_map.values().forEach(function (e) {
      if (e.vertices[0] === null || e.vertices[1] === null) throw NPE(frame("Graph", "dump", 113));
    });
  };

  function Story() { this.visited_vertices = []; }
  Story.prototype.addVertex = function (v) { this.visited_vertices.push(v); };
  Story.prototype.getStoryAsArray = function () { return this.visited_vertices.slice(); };
  Story.prototype.getVertexSetAsArray = function () {
    var s = new JavaHashSet();
    s.addAll(this.visited_vertices);
    return s.toArray();
  };

  var ACTIVATION = 1, DEACTIVATION = 2, SENSOR_TYPE_OCCUPANCY = 1, SENSOR_TYPE_BEAM = 2;

  function SensorRecording(sensor, event) { this.sensor = sensor; this.event = event; }
  function ObservationHistory() { this.sensor_recordings = []; }
  ObservationHistory.prototype.addSensorRecording = function (r) { this.sensor_recordings.push(r); };
  ObservationHistory.prototype.getOhAsArray = function () { return this.sensor_recordings.slice(); };

  function BeamDetector(name, sv) { this.type = SENSOR_TYPE_BEAM; this.name = name; this.sensor_vertices = sv; }
  function OccupancySensor(sv) {
    this.type = SENSOR_TYPE_OCCUPANCY;
    if (sv === null) throw NPE(frame("OccupancySensor", "<init>", 7));
    this.name = sv.name;
    this.sensor_vertices = [sv];
  }

  function DetectiveGame() {
    this.story_vertices = null;
    this.sensor_vertices = null;
    this.graph = null;
    this.story = null;
    this.ob_his = null;
    this.room_ids = new JavaHashSet();
    this.beam_ids = new JavaHashSet();
    this.occu_ids = new JavaHashSet();
  }
  /** DetectiveGame.updateStartingVertex (DetectiveGame.java:20-35), quirks included (B6). */
  DetectiveGame.prototype.updateStartingVertex = function (v) {
    var g = this.graph;
    var sv = g.nameGet("SV");
    if (sv === null) throw NPE(frame("DetectiveGame", "updateStartingVertex", 22));
    var arr = sv.neighbors.toArray();
    if (!arr.length) throw AIOOBE(frame("DetectiveGame", "updateStartingVertex", 22), "0");
    var svn = arr[0];
    if (svn === null) throw NPE(frame("DetectiveGame", "updateStartingVertex", 23));
    svn.neighbors.remove(sv);
    sv.neighbors.remove(svn);
    var eid = getEdgeId(sv.id, svn.id);
    g.edge_ids.delete(eid);
    g.edge_map.remove(eid);
    sv.addNeighbor(v);
    if (v === null) throw NPE(frame("DetectiveGame", "updateStartingVertex", 30));
    v.addNeighbor(sv);
    var e = new Edge(sv, v);
    g.edge_ids.add(e.id);
    g.edge_map.put(e.id, e);
  };

  function addEdgesFromNeighbors(g) {
    g.vertex_map.values().forEach(function (v) {
      v.neighbors.toArray().forEach(function (n) {
        if (!g.hasEdgeBetweenVertices(v, n)) {
          var e = new Edge(v, n);
          g.edge_ids.add(e.id);
          g.edge_map.put(e.id, e);
        }
      });
    });
  }

  // ======================================================================== Algorithms

  function getSubGraph(g, s, storyVertices, vg) {
    var vpSet = new Set(storyVertices);
    vpSet.add(s);
    return at(fa("getSubGraph", 17), function () { return getReachableSubgraph(g, s, vpSet, vg); });
  }

  function getSubGraphMulti(g, s, occuSensors, storyVertices, vg) {
    var vpSet = new Set(storyVertices);
    occuSensors.forEach(function (o) { vpSet.add(o); });
    vpSet.add(s);
    var gp = at(fa("getSubGraphMulti", 31), function () { return getReachableSubgraph(g, s, vpSet, vg); });
    occuSensors.forEach(function (o) {
      if (o === null) throw NPE(fa("getSubGraphMulti", 34));
      if (gp.vertex_map.containsKey(o.id)) {
        var ns = gp.vertex_map.get(o.id).neighbors.toArray();
        for (var j = 0; j < ns.length; j++) {
          for (var k = j + 1; k < ns.length; k++) {
            if (!gp.hasEdgeBetweenIds(ns[j].id, ns[k].id)) {
              var ed = at(fa("getSubGraphMulti", 39), function () { return new Edge(ns[j], ns[k]); });
              gp.addEdge(ed);
            }
          }
        }
      }
    });
    return gp;
  }

  /** vpSet: anything with has() (JS Set) or contains() (JavaHashSet). */
  function getReachableSubgraph(g, s, vpSet, vg) {
    var inVp = function (v) { return vpSet.has ? vpSet.has(v) : vpSet.contains(v); };
    var temp = new Graph(), sub = new Graph();
    var es = g.edge_map.values();
    at(fa("getReachableSubgraph", 56), function () { temp.addCopyOfEdgelessVertex(s); });
    sub.addCopyOfEdgelessVertex(s);
    es.forEach(function (e) {
      if (inVp(e.vertices[0]) && inVp(e.vertices[1])) temp.addEdge(e.getCopy());
    });
    var vvid = new Set();
    var queue = [s.id];
    var tVmap = temp.vertex_map, tEdges = temp.edge_map, sEdges = sub.edge_map;
    while (queue.length) {
      var cv = tVmap.get(queue[0]);
      vvid.add(cv.id);
      queue.shift();
      var cid = cv.id;
      cv.neighbors.toArray().forEach(function (n) {
        var nid = n.id;
        var eid = cid < nid ? (cid * 65536 + nid) | 0 : (nid * 65536 + cid) | 0;
        if (tEdges.containsKey(eid)) {
          if (!sEdges.containsKey(eid)) sub.addEdge(tEdges.get(eid).getCopy());
          if (!vvid.has(nid)) queue.push(nid);
        }
      });
    }
    var sgvs = sub.vertex_map.values();
    vg.forEach(function (v2) {
      sgvs.forEach(function (sv) {
        var v1 = g.vertex_map.get(sv.id);
        if (v1 === null) throw NPE(fa("getReachableSubgraph", 100));
        if (v1.neighbors.contains(v2)) {
          var ed = at(fa("getReachableSubgraph", 101), function () {
            var x = g.getEdgeBetweenVertices(v1, v2);
            if (x === null) throw NPE(frame("Graph", "addCopyOfEdge", 90));
            return x;
          });
          sub.addEdge(ed.getCopy());
        }
      });
    });
    return sub;
  }

  function isDeactivation(sr) { return sr.sensor.type === SENSOR_TYPE_OCCUPANCY && sr.event === DEACTIVATION; }
  function isActivation(sr) { return sr.sensor.type === SENSOR_TYPE_OCCUPANCY && sr.event === ACTIVATION; }

  function flip(g, v, r) {
    if (r.type === SENSOR_TYPE_OCCUPANCY) return g.vertex_map.get(v.id);
    var s0 = r.sensor_vertices[0], s1 = r.sensor_vertices[1];
    if (s0 === null || v.name === null) throw NPE(fa("flip", 130));
    if (v.name === s0.name) {
      if (s1 === null) throw NPE(fa("flip", 131));
      return g.vertex_map.get(s1.id);
    }
    return g.vertex_map.get(s0.id);
  }

  function flipAt(g, v, r, method, line) { return at(fa(method, line), function () { return flip(g, v, r); }); }

  /** dumpStatus without System.out: the NPEs only, in the order Java hits them. */
  function dumpStatus(p, status) {
    if (status[0].contains(null)) throw NPE(fa("dumpStatus", 144));
    for (var i = 0; i < p.length; i++) {
      if (p[i] === null) throw NPE(fa("dumpStatus", 150));
      if (status[i + 1].contains(null)) throw NPE(fa("dumpStatus", 154));
    }
  }

  function areNeighbors(g, a, b) { return g.hasEdgeBetweenIds(a, b) || a === b; }

  function subGraphAt(g, s, storyVs, vg, caller, line) {
    return at(fa(caller, line), function () { return getSubGraph(g, s, storyVs, vg); });
  }
  function dumpStatusAt(p, status, caller, line) { at(fa(caller, line), function () { dumpStatus(p, status); }); }

  function newSets(n) { var o = []; for (var i = 0; i < n; i++) o.push(new JavaHashSet()); return o; }

  /** Algorithms.validateAgentStory (Algorithms.java:167-224), STAR Alg. 3, bugs included. */
  function validateAgentStory(g, sv, story, obHis) {
    var p = story.getStoryAsArray(), r = obHis.getOhAsArray();
    var n1 = p.length + 1;
    var S = newSets(n1), SP = newSets(n1);
    S[0].add(sv);
    var storyVs = story.getVertexSetAsArray();
    var m = r.length;
    for (var i = 0; i <= m; i++) {
      if (i < m && isDeactivation(r[i])) continue;
      var vg = i < m ? r[i].sensor.sensor_vertices : [];
      for (var j = 0; j < n1; j++) {
        var arr = S[j].toArray();
        for (var a = 0; a < arr.length; a++) {
          var s = arr[a];
          var gp = subGraphAt(g, s, storyVs, vg, "validateAgentStory", 193);
          var gvm = gp.vertex_map;
          s = gvm.get(s.id);
          for (var l = j; l < n1; l++) {
            if (i === m && l === n1 - 1) return true;
            for (var q = 0; q < vg.length; q++) {
              var vgi = vg[q];
              if (vgi === null) throw NPE(fa("validateAgentStory", 201));
              var v = gvm.get(vgi.id);
              if (v === null) continue;
              if (s === null) throw npeHasEdge("validateAgentStory", 203);
              if (v === s || gp.hasEdgeBetweenIds(v.id, s.id)) {
                SP[l].add(flipAt(g, v, r[i].sensor, "validateAgentStory", 204));
              }
            }
            if (l < n1 - 1) {
              var pl = p[l];
              if (pl === null || s === null) throw NPE(fa("validateAgentStory", 207));
              if (areNeighbors(gp, pl.id, s.id)) { s = gvm.get(pl.id); continue; }
            }
            break;
          }
        }
      }
      S = SP;
      SP = newSets(n1);
      dumpStatusAt(p, S, "validateAgentStory", 221);
    }
    return false;
  }

  /** Algorithms.getAgentStoryStatuses (Algorithms.java:315-368), row aliasing and B4 included. */
  function getAgentStoryStatuses(g, sv, story, obHis) {
    var p = story.getStoryAsArray(), r = obHis.getOhAsArray();
    var m = r.length, n1 = p.length + 1;
    var S = [];
    for (var x = 0; x <= m; x++) S.push(newSets(n1));
    S[0][0].add(sv);
    var storyVs = story.getVertexSetAsArray();
    for (var i = 0; i <= m; i++) {
      if (i < m && isDeactivation(r[i])) { S[i + 1] = S[i]; continue; }
      var vg = i < m ? r[i].sensor.sensor_vertices : [];
      var Si = S[i];
      for (var j = 0; j < Si.length; j++) {
        var arr = Si[j].toArray();
        for (var a = 0; a < arr.length; a++) {
          var s = arr[a];
          var gp = subGraphAt(g, s, storyVs, vg, "getAgentStoryStatuses", 342);
          var gvm = gp.vertex_map;
          s = gvm.get(s.id);
          for (var l = j; l < Si.length; l++) {
            if (i === m && l === Si.length - 1) return S;
            for (var q = 0; q < vg.length; q++) {
              var vgi = vg[q];
              if (vgi === null) throw NPE(fa("getAgentStoryStatuses", 350));
              var v = gvm.get(vgi.id);
              if (v === null) continue;
              if (s === null) throw npeHasEdge("getAgentStoryStatuses", 352);
              if (v === s || gp.hasEdgeBetweenIds(v.id, s.id)) {
                S[i + 1][l].add(flipAt(g, v, r[i].sensor, "getAgentStoryStatuses", 353));
              }
            }
            if (l < Si.length - 1) {
              var pl = p[l];
              if (pl === null || s === null) throw NPE(fa("getAgentStoryStatuses", 356));
              if (areNeighbors(gp, pl.id, s.id)) { s = gvm.get(pl.id); continue; }
            }
            break;
          }
        }
      }
      if (i + 1 >= S.length) throw AIOOBE(fa("getAgentStoryStatuses", 365), String(i + 1));
      dumpStatusAt(p, S[i + 1], "getAgentStoryStatuses", 365);
    }
    return null;
  }

  /** Algorithms.getAgentStory (Algorithms.java:226-313): the path string, B2-B4 included. */
  function getAgentStory(g, sv, story, obHis) {
    var p = story.getStoryAsArray(), r = obHis.getOhAsArray();
    var S = at(fa("getAgentStory", 229), function () { return getAgentStoryStatuses(g, sv, story, obHis); });
    var srLoc = [], srVer = [];
    var storyVs = story.getVertexSetAsArray();
    var keepBreaking = false, J = 0, last = null;
    var m = r.length;
    for (var i = m; i >= 0; i--) {
      keepBreaking = false;
      if (i < m && isDeactivation(r[i])) continue;
      var vg = i < m ? r[i].sensor.sensor_vertices : [];
      var Si = S[i];
      var n1 = Si.length;
      for (var j = 0; j < n1; j++) {
        var Sj = Si[j].toArray();
        for (var k = 0; k < Sj.length; k++) {
          var s = Sj[k];
          var gp = subGraphAt(g, s, storyVs, vg, "getAgentStory", 252);
          var gvm = gp.vertex_map;
          s = gvm.get(s.id);
          for (var l = j; l < n1; l++) {
            var gv;
            if (i === m && l === n1 - 1) {
              J = j;
              srLoc.push(j);
              gv = g.vertex_map.get(Sj[k].id);
              if (gv === null) throw NPE(fa("getAgentStory", 259));
              srVer.push(gv.asso_vertex !== null ? gv.asso_vertex : Sj[k]);
              last = Sj[k];
              keepBreaking = true;
              break;
            }
            for (var q = 0; q < vg.length; q++) {
              var vgi = vg[q];
              if (vgi === null) throw NPE(fa("getAgentStory", 269));
              var v = gvm.get(vgi.id);
              if (v === null) continue;
              if (s === null) throw npeHasEdge("getAgentStory", 271);
              if (v === s || gp.hasEdgeBetweenIds(v.id, s.id)) {
                var vp = flipAt(g, v, r[i].sensor, "getAgentStory", 272);
                if (l === J) {
                  if (vp === null || last === null) throw NPE(fa("getAgentStory", 273));
                  if (vp.id === last.id) {
                    J = j;
                    last = Sj[k];
                    srLoc.push(j);
                    gv = g.vertex_map.get(Sj[k].id);
                    if (gv === null) throw NPE(fa("getAgentStory", 277));
                    srVer.push(gv.asso_vertex !== null ? gv.asso_vertex : Sj[k]);
                    break;
                  }
                }
              }
            }
            if (l < n1 - 1) {
              var pl = p[l];
              if (pl === null || s === null) throw NPE(fa("getAgentStory", 286));
              if (areNeighbors(gp, pl.id, s.id)) { s = gvm.get(pl.id); continue; }
            }
            break;
          }
          if (i === m && keepBreaking) break;
        }
        if (i === m && keepBreaking) break;
      }
      dumpStatusAt(p, S[i], "getAgentStory", 297);
    }
    var path = [];
    for (var a = 0; a < p.length; a++) {
      if (p[a] === null) throw NPE(fa("getAgentStory", 302));
      path.push(p[a].name === null ? "null" : p[a].name);
    }
    for (var b = 0; b < srLoc.length - 1; b++) {
      var loc = srLoc[b];
      if (srVer[b] === null) throw NPE(fa("getAgentStory", 306));
      if (loc > path.length) {
        throw AIOOBE("java.util.Vector.insertElementAt(Vector.java:603)", loc + " > " + path.length)
          .addFrame(fa("getAgentStory", 306));
      }
      path.splice(loc, 0, "[" + (srVer[b].name === null ? "null" : srVer[b].name) + "]");
    }
    return path.join("");
  }

  /** Algorithms.validateAgentStoryMulti (Algorithms.java:370-449), STAR Alg. 4, B1 included. */
  function validateAgentStoryMulti(g, sv, story, obHis) {
    var p = story.getStoryAsArray(), r = obHis.getOhAsArray();
    var n1 = p.length + 1;
    var S = newSets(n1), SP = newSets(n1);
    S[0].add(sv);
    var O = new JavaHashSet();
    var storyVs = story.getVertexSetAsArray();
    var m = r.length;
    for (var i = 0; i <= m; i++) {
      var vg = [];
      if (i < m && isDeactivation(r[i])) { O.remove(r[i].sensor.sensor_vertices[0]); continue; }
      var act = i < m && isActivation(r[i]);
      if (act) O.add(r[i].sensor.sensor_vertices[0]);
      if (i < m) vg = r[i].sensor.sensor_vertices;
      for (var j = 0; j < n1; j++) {
        var arr = S[j].toArray();
        for (var a = 0; a < arr.length; a++) {
          var s = arr[a];
          var vgp = act ? vg.concat([s]) : vg;
          var occ = O.toArray();
          var gp = at(fa("validateAgentStoryMulti", 416), function () {
            return getSubGraphMulti(g, s, occ, storyVs, vgp);
          });
          var gvm = gp.vertex_map;
          s = gvm.get(s.id);
          at(fa("validateAgentStoryMulti", 418), function () { gp.dump(); });
          for (var l = j; l < n1; l++) {
            if (i === m && l === n1 - 1) return true;
            for (var q = 0; q < vg.length; q++) {
              var vgi = vg[q];
              if (vgi === null) throw NPE(fa("validateAgentStoryMulti", 424));
              var v = gvm.get(vgi.id);
              if (v === null) continue;
              if (s === null) throw npeHasEdge("validateAgentStoryMulti", 426);
              if (v === s || gp.hasEdgeBetweenIds(v.id, s.id)) {
                SP[l].add(flipAt(g, v, r[i].sensor, "validateAgentStoryMulti", 427));
              }
            }
            if (l < n1 - 1) {
              var pl = p[l];
              if (pl === null || s === null) throw NPE(fa("validateAgentStoryMulti", 430));
              if (areNeighbors(gp, pl.id, s.id)) { s = gvm.get(pl.id); continue; }
            }
            break;
          }
        }
      }
      for (var jj = 0; jj < n1; jj++) S[jj].addAll(SP[jj].toArray());
      SP = newSets(n1);
      dumpStatusAt(p, S, "validateAgentStoryMulti", 446);
    }
    return false;
  }

  // ======================================================================== builders

  var FILLER_MARK = "...";

  function isDict(o) { return o !== null && typeof o === "object" && !Array.isArray(o); }
  function pyTruthy(o) {
    if (o === null || o === undefined || o === false || o === 0 || o === "") return false;
    if (Array.isArray(o)) return o.length > 0;
    if (isDict(o)) return Object.keys(o).length > 0;
    return true;
  }
  function get(spec, key) { return spec[key] === undefined ? null : spec[key]; }

  function strList(o, what) {
    if (o === null || o === undefined) return [];
    if (!Array.isArray(o) || !o.every(function (x) { return typeof x === "string"; })) {
      throw new ValueError(what + " must be a list of strings");
    }
    return o.slice();
  }

  function beamItems(spec) {
    var beams = pyTruthy(spec.beams) ? spec.beams : {};
    if (!isDict(beams)) throw new ValueError("beams must be an object");
    return Object.keys(beams).map(function (b) { return [b, strList(beams[b], "beam sides")]; });
  }

  /** expand_map: the compact key "filler_occupancy": N (N edgeless sensors f1..fN). */
  function expandMap(spec) {
    if (!isDict(spec)) throw new ValueError("a map must be a JSON object");
    if (!("filler_occupancy" in spec)) return spec;
    var n = spec.filler_occupancy;
    if (typeof n !== "number" || !Number.isInteger(n) || n < 0 || n > 0x7fffffff) {
      throw new ValueError("filler_occupancy must be a non-negative integer");
    }
    var fillers = [];
    for (var i = 1; i <= n; i++) fillers.push("f" + i);
    var out = {};
    Object.keys(spec).forEach(function (k) { if (k !== "filler_occupancy") out[k] = spec[k]; });
    out.occupancy = strList(get(spec, "occupancy"), "occupancy").concat(fillers);
    if (get(spec, "vertex_order") !== null) {
      var vo = strList(spec.vertex_order, "vertex_order");
      var cnt = vo.filter(function (x) { return x === FILLER_MARK; }).length;
      if (cnt > 1) throw new ValueError("vertex_order has more than one " + pyRepr(FILLER_MARK));
      if (cnt === 1) {
        var at_ = vo.indexOf(FILLER_MARK);
        vo = vo.slice(0, at_).concat(fillers, vo.slice(at_ + 1));
      } else vo = vo.concat(fillers);
      out.vertex_order = vo;
    }
    return out;
  }

  /** build_game: the reference harness's generic builder (= getBasicGame() for STAR Fig. 2). */
  function buildGame(spec) {
    spec = expandMap(spec);
    var rooms = strList(get(spec, "rooms"), "rooms");
    var beams = beamItems(spec);
    var occ = strList(get(spec, "occupancy"), "occupancy");
    var beamSides = [];
    beams.forEach(function (b) { b[1].forEach(function (s) { beamSides.push(s); }); });
    var order = get(spec, "vertex_order") !== null ? strList(spec.vertex_order, "vertex_order")
      : rooms.concat(beamSides, occ);
    var allv = new Set(rooms.concat(beamSides, occ));
    if (allv.size !== rooms.length + beamSides.length + occ.length || allv.has("SV")) {
      throw new ValueError("duplicate or reserved vertex names in map");
    }
    var orderSet = new Set(order);
    var same = orderSet.size === allv.size;
    allv.forEach(function (x) { if (!orderSet.has(x)) same = false; });
    if (order.length !== allv.size || !same) {
      throw new ValueError("vertex_order is not a permutation of the map's vertices");
    }
    if (!rooms.length) throw new ValueError("map has no rooms");
    beams.forEach(function (b) { if (b[1].length !== 2) throw new ValueError("a beam needs exactly two sides"); });

    var nextId = 1;  // the first generated id (1) is discarded
    var game = new DetectiveGame();
    var g = new Graph();
    var sv = new Vertex("SV", ++nextId);
    var byName = new NativeMap();
    var created = [];
    order.forEach(function (n) {
      var v = new Vertex(n, ++nextId);
      byName.set(n, v);
      created.push(v);
    });
    beams.forEach(function (b) {
      byName.get(b[1][0]).asso_vertex = byName.get(b[1][1]);
      byName.get(b[1][1]).asso_vertex = byName.get(b[1][0]);
    });
    beamSides.forEach(function (n) { game.beam_ids.add(byName.get(n).id); });
    rooms.forEach(function (n) { game.room_ids.add(byName.get(n).id); });
    occ.forEach(function (n) { game.occu_ids.add(byName.get(n).id); });
    game.graph = g;
    game.story_vertices = [sv].concat(rooms.map(function (n) { return byName.get(n); }));
    var sensorNames = new Set(beamSides.concat(occ));
    game.sensor_vertices = created.filter(function (v) { return sensorNames.has(v.name); });

    var mapOrder = [sv].concat(rooms.concat(beamSides, occ).map(function (n) { return byName.get(n); }));
    mapOrder.forEach(function (v) { g.vertex_ids.add(v.id); });
    mapOrder.forEach(function (v) { g.vertex_map.put(v.id, v); });
    mapOrder.forEach(function (v) { g.vertex_name_map.set(v.name, v); });

    var adj = new NativeMap();
    [sv].concat(created).forEach(function (v) { adj.set(v, []); });
    var room0 = byName.get(rooms[0]);
    adj.get(sv).push(room0);
    adj.get(room0).push(sv);
    var edges = pyTruthy(spec.edges) ? spec.edges : [];
    if (!Array.isArray(edges)) throw new ValueError("edges must be a list");
    edges.forEach(function (e) {
      if (!Array.isArray(e) || e.length < 2 || !e.every(function (x) { return typeof x === "string"; })) {
        throw new ValueError("an edge must be a list of two vertex names, got " + pyRepr(e));
      }
      var u = byName.has(e[0]) ? byName.get(e[0]) : null, w = byName.has(e[1]) ? byName.get(e[1]) : null;
      if (u === null || w === null) throw new ValueError("edge with unknown vertex " + pyRepr(e));
      adj.get(u).push(w);
      adj.get(w).push(u);
    });
    [sv].concat(created).forEach(function (v) {
      adj.get(v).forEach(function (n) { v.addNeighbor(n); });
    });
    addEdgesFromNeighbors(g);
    game.story = new Story();
    game.ob_his = new ObservationHistory();
    return game;
  }

  /** make_history: append [sensor, "A"|"D"] pairs as Harness.addHistory does. */
  function makeHistory(game, spec, pairs) {
    var g = game.graph;
    spec = expandMap(spec);
    var beams = new NativeMap(beamItems(spec));
    var occ = strList(get(spec, "occupancy"), "occupancy");
    var sensors = new NativeMap();
    if (!Array.isArray(pairs)) throw new ValueError("a history must be a list of [sensor, event] pairs");
    pairs.forEach(function (pr) {
      if (!Array.isArray(pr) || pr.length < 2 || typeof pr[0] !== "string" || typeof pr[1] !== "string") {
        throw new ValueError("a recording must be a [sensor, event] pair of strings, got " + pyRepr(pr));
      }
      var name = pr[0], ev = pr[1];
      var sn = sensors.has(name) ? sensors.get(name) : null;
      if (sn === null) {
        if (beams.has(name)) {
          var sides = beams.get(name);
          if (sides.length < 2) throw new ValueError("beam " + pyRepr(name) + " needs two sides");
          sn = new BeamDetector(name, [g.nameGet(sides[0]), g.nameGet(sides[1])]);
        } else if (occ.indexOf(name) >= 0) {
          var v = g.nameGet(name);
          if (v === null) throw new ValueError("occupancy vertex missing: " + name);
          sn = new OccupancySensor(v);
        } else {
          throw new ValueError("unknown sensor " + pyRepr(name));
        }
        sensors.set(name, sn);
      }
      var e;
      if (ev === "A") e = ACTIVATION;
      else if (ev === "D") e = DEACTIVATION;
      else throw new ValueError("unknown event " + pyRepr(ev));
      game.ob_his.addSensorRecording(new SensorRecording(sn, e));
    });
  }

  /**
   * The original's verdict (and getAgentStory path) the way the applet computes it
   * (Python compat.original.validate_compat) -> {consistent, path_string}.
   */
  function validateCompat(mapDict, storyList, eventPairs, agents) {
    if (agents === undefined) agents = "single";
    if (agents !== "single" && agents !== "multi") {
      throw new ValueError("agents must be 'single' or 'multi', got " + pyRepr(agents));
    }
    var game = buildGame(mapDict);
    var g = game.graph;
    storyList.forEach(function (n) {
      if (typeof n !== "string") throw new ValueError("story entries must be strings, got " + pyRepr(n));
      game.story.addVertex(g.nameGet(n));
    });
    makeHistory(game, mapDict, eventPairs);
    var p = game.story.getStoryAsArray();
    if (p.length) game.updateStartingVertex(p[0]);
    var sv = g.nameGet("SV");
    if (agents === "single") {
      var ok = validateAgentStory(g, sv, game.story, game.ob_his);
      return { consistent: ok, path_string: ok ? getAgentStory(g, sv, game.story, game.ob_his) : null };
    }
    return { consistent: validateAgentStoryMulti(g, sv, game.story, game.ob_his), path_string: null };
  }

  CD.original = {
    validateCompat: validateCompat,
    JavaException: JavaException,
    JavaHashSet: JavaHashSet,
    JavaHashMap: JavaHashMap,
    javaStringHash: javaStringHash,
    buildGame: buildGame,
    expandMap: expandMap,
    makeHistory: makeHistory,
    validateAgentStory: validateAgentStory,
    getAgentStory: getAgentStory,
    getAgentStoryStatuses: getAgentStoryStatuses,
    validateAgentStoryMulti: validateAgentStoryMulti,
    getSubGraph: getSubGraph,
    getSubGraphMulti: getSubGraphMulti,
    getReachableSubgraph: getReachableSubgraph,
    Vertex: Vertex,
    Edge: Edge,
    Graph: Graph,
    DetectiveGame: DetectiveGame,
    IDENTITY_HASH: IDENTITY_HASH
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
