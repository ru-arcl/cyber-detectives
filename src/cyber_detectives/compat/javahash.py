"""Exact emulation of Java 8 ``java.util.HashMap`` / ``java.util.HashSet`` iteration order.

The original Cyber Detectives code stores vertices in ``HashSet<Vertex>`` and graphs in
``HashMap<Integer, ...>``; several of its outputs (``Graph.dump``, ``dumpStatus``,
``getAgentStory`` paths, even the number of ``GP.dump()`` blocks before
``validateAgentStoryMulti`` returns) depend on the order in which these containers iterate.
This module reproduces that order exactly, following the JDK 8 source
(``java/util/HashMap.java``, OpenJDK 8u504, the JDK the golden fixtures were recorded with):

- lazy table allocation, default capacity 16, load factor 0.75, doubling ``resize()`` that
  splits every bin into a "lo" and a "hi" bin preserving relative order;
- ``hash(key) = h ^ (h >>> 16)`` on ``key.hashCode()``;
- bins kept as linked lists (new keys appended at the tail) until a bin holds more than
  ``TREEIFY_THRESHOLD = 8`` nodes, then ``treeifyBin``: a resize while the table is smaller
  than ``MIN_TREEIFY_CAPACITY = 64``, otherwise a red-black tree of ``TreeNode`` whose
  iteration order is the ``next`` chain: ``putTreeVal`` links a new node right after its
  tree parent, ``moveRootToFront`` moves the root to the head of the chain, ties are broken
  by ``tieBreakOrder`` (class name, then ``System.identityHashCode``), and ``split`` /
  ``removeTreeNode`` untreeify small bins.

Key semantics (``hashCode`` / ``equals``) follow the Java classes the original uses:

- ``int`` behaves like ``java.lang.Integer`` (hash = value, equality by value, comparable);
- ``str`` behaves like ``java.lang.String`` (``String.hashCode``, comparable);
- ``None`` is the null key (hash 0);
- every other object behaves like a class that does not override ``hashCode``/``equals``
  (identity equality, ``System.identityHashCode``). Under the canonical JVM setting of the
  golden fixtures, ``-XX:+UnlockExperimentalVMOptions -XX:hashCode=2``, HotSpot returns the
  constant 1 for every identity hash (``ObjectSynchronizer::get_next_hash``,
  ``hashCode == 2`` branch: "value = 1; for sensitivity testing"), which is
  :data:`IDENTITY_HASH`. An object may override it with a ``java_identity_hash`` attribute
  (used by the tests to model other settings).

Only the behaviour that affects iteration order is modelled; values are stored in a Python
dict. Iteration order is cached between mutations, so iterating an unchanged container is
cheap.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, List, Optional

__all__ = [
    "IDENTITY_HASH",
    "JavaHashMap",
    "JavaHashSet",
    "java_hash_code",
    "java_string_hash",
    "spread",
]

#: identityHashCode of every object under -XX:hashCode=2 (HotSpot synchronizer.cpp).
IDENTITY_HASH = 1

DEFAULT_INITIAL_CAPACITY = 16
MAXIMUM_CAPACITY = 1 << 30
DEFAULT_LOAD_FACTOR = 0.75
TREEIFY_THRESHOLD = 8
UNTREEIFY_THRESHOLD = 6
MIN_TREEIFY_CAPACITY = 64


def _int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def java_string_hash(s: str) -> int:
    """``java.lang.String.hashCode()``: s[0]*31^(n-1) + ... over UTF-16 code units, int32."""
    h = 0
    if s.isascii():
        for c in s:
            h = (31 * h + ord(c)) & 0xFFFFFFFF
    else:
        data = s.encode("utf-16-be", "surrogatepass")
        for i in range(0, len(data), 2):
            h = (31 * h + ((data[i] << 8) | data[i + 1])) & 0xFFFFFFFF
    return _int32(h)


def _identity_hash(k: Any) -> int:
    """``System.identityHashCode(k)``; 0 for null."""
    if k is None:
        return 0
    return getattr(k, "java_identity_hash", IDENTITY_HASH)


def java_hash_code(k: Any) -> int:
    """``k.hashCode()`` for the key kinds described in the module docstring."""
    if k is None:
        return 0
    t = type(k)
    if t is int:
        return _int32(k)
    if t is str:
        return java_string_hash(k)
    return getattr(k, "java_identity_hash", IDENTITY_HASH)


def spread(h: int) -> int:
    """``HashMap.hash``: ``h ^ (h >>> 16)`` (h is a signed 32-bit int)."""
    return h ^ ((h & 0xFFFFFFFF) >> 16)


def _hash(k: Any) -> int:
    if k is None:
        return 0
    return spread(java_hash_code(k))


def _class_name(k: Any) -> str:
    t = type(k)
    if t is int:
        return "java.lang.Integer"
    if t is str:
        return "java.lang.String"
    return getattr(t, "JAVA_CLASS", t.__module__ + "." + t.__qualname__)


def _comparable_class_for(k: Any) -> Optional[str]:
    """``HashMap.comparableClassFor``: only Integer and String keys are comparable here."""
    t = type(k)
    if t is int:
        return "java.lang.Integer"
    if t is str:
        return "java.lang.String"
    return None


def _compare_comparables(kc: str, k: Any, x: Any) -> int:
    """``HashMap.compareComparables``: k.compareTo(x) if x is of class kc, else 0."""
    if x is None or _class_name(x) != kc:
        return 0
    if kc == "java.lang.String":
        if not (k.isascii() and x.isascii()):  # String.compareTo compares UTF-16 code units
            k = k.encode("utf-16-be", "surrogatepass")
            x = x.encode("utf-16-be", "surrogatepass")
    return -1 if k < x else (1 if k > x else 0)


def _tie_break_order(a: Any, b: Any) -> int:
    """``TreeNode.tieBreakOrder``."""
    d = 0
    if a is not None and b is not None:
        ca, cb = _class_name(a), _class_name(b)
        d = -1 if ca < cb else (1 if ca > cb else 0)
    if a is None or b is None or d == 0:
        d = -1 if _identity_hash(a) <= _identity_hash(b) else 1
    return d


# ----------------------------------------------------------------------------- tree bins


class _TreeNode:
    """``HashMap.TreeNode``: red-black tree links plus the prev/next iteration chain."""

    __slots__ = ("hash", "key", "next", "prev", "parent", "left", "right", "red")

    def __init__(self, h: int, key: Any, nxt: Optional["_TreeNode"] = None):
        self.hash = h
        self.key = key
        self.next = nxt
        self.prev: Optional[_TreeNode] = None
        self.parent: Optional[_TreeNode] = None
        self.left: Optional[_TreeNode] = None
        self.right: Optional[_TreeNode] = None
        self.red = False

    def root(self) -> "_TreeNode":
        r = self
        while r.parent is not None:
            r = r.parent
        return r


class _TreeBin:
    """A treeified bin; ``first`` is ``tab[index]`` (always the root after a mutation)."""

    __slots__ = ("first", "nodes")

    def __init__(self) -> None:
        self.first: Optional[_TreeNode] = None
        self.nodes: Dict[Any, _TreeNode] = {}  # key -> node (lookup only)

    def keys(self) -> List[Any]:
        out = []
        e = self.first
        while e is not None:
            out.append(e.key)
            e = e.next
        return out

    def __len__(self) -> int:
        return len(self.nodes)


def _rotate_left(root, p):
    if p is not None and p.right is not None:
        r = p.right
        rl = p.right = r.left
        if rl is not None:
            rl.parent = p
        pp = r.parent = p.parent
        if pp is None:
            root = r
            r.red = False
        elif pp.left is p:
            pp.left = r
        else:
            pp.right = r
        r.left = p
        p.parent = r
    return root


def _rotate_right(root, p):
    if p is not None and p.left is not None:
        l = p.left
        lr = p.left = l.right
        if lr is not None:
            lr.parent = p
        pp = l.parent = p.parent
        if pp is None:
            root = l
            l.red = False
        elif pp.right is p:
            pp.right = l
        else:
            pp.left = l
        l.right = p
        p.parent = l
    return root


def _balance_insertion(root, x):
    x.red = True
    while True:
        xp = x.parent
        if xp is None:
            x.red = False
            return x
        xpp = xp.parent
        if not xp.red or xpp is None:
            return root
        xppl = xpp.left
        if xp is xppl:
            xppr = xpp.right
            if xppr is not None and xppr.red:
                xppr.red = False
                xp.red = False
                xpp.red = True
                x = xpp
            else:
                if x is xp.right:
                    x = xp
                    root = _rotate_left(root, x)
                    xp = x.parent
                    xpp = None if xp is None else xp.parent
                if xp is not None:
                    xp.red = False
                    if xpp is not None:
                        xpp.red = True
                        root = _rotate_right(root, xpp)
        else:
            if xppl is not None and xppl.red:
                xppl.red = False
                xp.red = False
                xpp.red = True
                x = xpp
            else:
                if x is xp.left:
                    x = xp
                    root = _rotate_right(root, x)
                    xp = x.parent
                    xpp = None if xp is None else xp.parent
                if xp is not None:
                    xp.red = False
                    if xpp is not None:
                        xpp.red = True
                        root = _rotate_left(root, xpp)


def _balance_deletion(root, x):
    while True:
        if x is None or x is root:
            return root
        xp = x.parent
        if xp is None:
            x.red = False
            return x
        if x.red:
            x.red = False
            return root
        xpl = xp.left
        if xpl is x:
            xpr = xp.right
            if xpr is not None and xpr.red:
                xpr.red = False
                xp.red = True
                root = _rotate_left(root, xp)
                xp = x.parent
                xpr = None if xp is None else xp.right
            if xpr is None:
                x = xp
            else:
                sl, sr = xpr.left, xpr.right
                if (sr is None or not sr.red) and (sl is None or not sl.red):
                    xpr.red = True
                    x = xp
                else:
                    if sr is None or not sr.red:
                        if sl is not None:
                            sl.red = False
                        xpr.red = True
                        root = _rotate_right(root, xpr)
                        xp = x.parent
                        xpr = None if xp is None else xp.right
                    if xpr is not None:
                        xpr.red = False if xp is None else xp.red
                        sr = xpr.right
                        if sr is not None:
                            sr.red = False
                    if xp is not None:
                        xp.red = False
                        root = _rotate_left(root, xp)
                    x = root
        else:  # symmetric
            if xpl is not None and xpl.red:
                xpl.red = False
                xp.red = True
                root = _rotate_right(root, xp)
                xp = x.parent
                xpl = None if xp is None else xp.left
            if xpl is None:
                x = xp
            else:
                sl, sr = xpl.left, xpl.right
                if (sl is None or not sl.red) and (sr is None or not sr.red):
                    xpl.red = True
                    x = xp
                else:
                    if sl is None or not sl.red:
                        if sr is not None:
                            sr.red = False
                        xpl.red = True
                        root = _rotate_left(root, xpl)
                        xp = x.parent
                        xpl = None if xp is None else xp.left
                    if xpl is not None:
                        xpl.red = False if xp is None else xp.red
                        sl = xpl.left
                        if sl is not None:
                            sl.red = False
                    if xp is not None:
                        xp.red = False
                        root = _rotate_right(root, xp)
                    x = root


def _move_root_to_front(tb: _TreeBin, root: Optional[_TreeNode]) -> None:
    """``TreeNode.moveRootToFront`` (the bin index is implied by ``tb``)."""
    if root is None:
        return
    first = tb.first
    if root is not first:
        tb.first = root
        rp = root.prev
        rn = root.next
        if rn is not None:
            rn.prev = rp
        if rp is not None:
            rp.next = rn
        if first is not None:
            first.prev = root
        root.next = first
        root.prev = None


def _dir_for(h: int, k: Any, p: _TreeNode, kc_box: list) -> int:
    """Direction of key (h, k) relative to node p, as treeify / putTreeVal compute it (the
    key is known not to be in the tree, so the equality branch cannot fire)."""
    ph = p.hash
    if ph > h:
        return -1
    if ph < h:
        return 1
    kc = kc_box[0]
    if kc is None:
        kc = kc_box[0] = _comparable_class_for(k)
    if kc is None:
        return _tie_break_order(k, p.key)
    d = _compare_comparables(kc, k, p.key)
    if d == 0:
        d = _tie_break_order(k, p.key)
    return d


def _treeify(tb: _TreeBin, nodes: List[_TreeNode]) -> None:
    """``TreeNode.treeify`` over nodes already linked in ``next`` order starting at
    ``tb.first``."""
    root = None
    for x in nodes:
        x.left = x.right = None
        if root is None:
            x.parent = None
            x.red = False
            root = x
        else:
            k, h = x.key, x.hash
            kc_box = [None]
            p = root
            while True:
                d = _dir_for(h, k, p, kc_box)
                xp = p
                p = p.left if d <= 0 else p.right
                if p is None:
                    x.parent = xp
                    if d <= 0:
                        xp.left = x
                    else:
                        xp.right = x
                    root = _balance_insertion(root, x)
                    break
    _move_root_to_front(tb, root)


def _make_tree_bin(keys: List[Any], hashes: Dict[Any, int]) -> _TreeBin:
    """``treeifyBin`` / split re-treeify: TreeNodes linked in list order, then treeify."""
    tb = _TreeBin()
    prev = None
    nodes = []
    for k in keys:
        n = _TreeNode(hashes[k], k)
        n.prev = prev
        if prev is not None:
            prev.next = n
        else:
            tb.first = n
        prev = n
        nodes.append(n)
        tb.nodes[k] = n
    _treeify(tb, nodes)
    return tb


def _put_tree_val(tb: _TreeBin, h: int, k: Any) -> None:
    """``TreeNode.putTreeVal`` for a key known to be absent."""
    root = tb.first.root() if tb.first.parent is not None else tb.first
    kc_box = [None]
    p = root
    while True:
        d = _dir_for(h, k, p, kc_box)
        xp = p
        p = p.left if d <= 0 else p.right
        if p is None:
            xpn = xp.next
            x = _TreeNode(h, k, xpn)
            if d <= 0:
                xp.left = x
            else:
                xp.right = x
            xp.next = x
            x.parent = x.prev = xp
            if xpn is not None:
                xpn.prev = x
            tb.nodes[k] = x
            _move_root_to_front(tb, _balance_insertion(root, x))
            return


def _remove_tree_node(tb: _TreeBin, node: _TreeNode) -> Optional[List[Any]]:
    """``TreeNode.removeTreeNode(map, tab, movable=true)``. Returns the remaining keys as a
    plain list when the bin is untreeified (or becomes empty: ``[]``), else None."""
    del tb.nodes[node.key]
    first = root = tb.first
    succ, pred = node.next, node.prev
    if pred is None:
        tb.first = first = succ
    else:
        pred.next = succ
    if succ is not None:
        succ.prev = pred
    if first is None:
        return []
    if root.parent is not None:
        root = root.root()
    if root is None or root.right is None or root.left is None or root.left.left is None:
        return tb.keys()  # too small: untreeify (the chain already excludes node)
    p, pl, pr = node, node.left, node.right
    if pl is not None and pr is not None:
        s = pr
        while s.left is not None:  # find successor
            s = s.left
        c = s.red
        s.red = p.red
        p.red = c  # swap colors
        sr = s.right
        pp = p.parent
        if s is pr:  # p was s's direct parent
            p.parent = s
            s.right = p
        else:
            sp = s.parent
            p.parent = sp
            if sp is not None:
                if s is sp.left:
                    sp.left = p
                else:
                    sp.right = p
            s.right = pr
            if pr is not None:
                pr.parent = s
        p.left = None
        p.right = sr
        if sr is not None:
            sr.parent = p
        s.left = pl
        if pl is not None:
            pl.parent = s
        s.parent = pp
        if pp is None:
            root = s
        elif p is pp.left:
            pp.left = s
        else:
            pp.right = s
        replacement = sr if sr is not None else p
    elif pl is not None:
        replacement = pl
    elif pr is not None:
        replacement = pr
    else:
        replacement = p
    if replacement is not p:
        pp = replacement.parent = p.parent
        if pp is None:
            root = replacement
            replacement.red = False
        elif p is pp.left:
            pp.left = replacement
        else:
            pp.right = replacement
        p.left = p.right = p.parent = None

    r = root if p.red else _balance_deletion(root, replacement)

    if replacement is p:  # detach
        pp = p.parent
        p.parent = None
        if pp is not None:
            if p is pp.left:
                pp.left = None
            elif p is pp.right:
                pp.right = None
    _move_root_to_front(tb, r)
    return None


# ----------------------------------------------------------------------------- the table


def _table_size_for(cap: int) -> int:
    n = cap - 1
    n |= n >> 1
    n |= n >> 2
    n |= n >> 4
    n |= n >> 8
    n |= n >> 16
    return 1 if n < 0 else (MAXIMUM_CAPACITY if n >= MAXIMUM_CAPACITY else n + 1)


class _Table:
    """The bucket structure shared by :class:`JavaHashMap` and :class:`JavaHashSet`."""

    __slots__ = ("_h", "_bins", "_cap", "_thr", "_order")

    def __init__(self, initial_capacity: Optional[int] = None):
        self._h: Dict[Any, int] = {}  # key -> spread hash (also the membership test)
        self._bins: Dict[int, Any] = {}  # bin index -> list of keys | _TreeBin
        self._cap = 0  # table.length (0 = table not allocated yet)
        if initial_capacity is None:
            self._thr = 0
        else:
            if initial_capacity < 0:
                raise ValueError("Illegal initial capacity: %d" % initial_capacity)
            self._thr = _table_size_for(min(initial_capacity, MAXIMUM_CAPACITY))
        self._order: Optional[List[Any]] = None

    # -- core operations (HashMap.putVal / removeNode / resize / treeifyBin) --

    def _insert(self, k: Any) -> bool:
        """putVal for the key; returns False if it was already present."""
        hs = self._h
        if k in hs:
            return False
        if self._cap == 0:
            self._resize()
        h = _hash(k)
        hs[k] = h
        self._order = None
        i = (self._cap - 1) & h
        b = self._bins.get(i)
        if b is None:
            self._bins[i] = [k]
        elif type(b) is list:
            b.append(k)
            if len(b) > TREEIFY_THRESHOLD:  # binCount >= TREEIFY_THRESHOLD - 1
                self._treeify_bin(h)
        else:
            _put_tree_val(b, h, k)
        if len(hs) > self._thr:
            self._resize()
        return True

    def _delete(self, k: Any) -> bool:
        hs = self._h
        h = hs.pop(k, _MISSING)
        if h is _MISSING:
            return False
        self._order = None
        i = (self._cap - 1) & h
        b = self._bins[i]
        if type(b) is list:
            b.remove(k)  # identity/value equality matches the Java key semantics
            if not b:
                del self._bins[i]
        else:
            rest = _remove_tree_node(b, b.nodes[k])
            if rest is not None:
                if rest:
                    self._bins[i] = rest
                else:
                    del self._bins[i]
        return True

    def _treeify_bin(self, h: int) -> None:
        if self._cap < MIN_TREEIFY_CAPACITY:
            self._resize()
            return
        i = (self._cap - 1) & h
        b = self._bins.get(i)
        if b is not None:
            self._bins[i] = _make_tree_bin(b, self._h)

    def _resize(self) -> None:
        old_cap, old_thr = self._cap, self._thr
        new_thr = 0
        if old_cap > 0:
            if old_cap >= MAXIMUM_CAPACITY:
                self._thr = 2 ** 31 - 1
                return
            new_cap = old_cap << 1
            if new_cap < MAXIMUM_CAPACITY and old_cap >= DEFAULT_INITIAL_CAPACITY:
                new_thr = old_thr << 1
        elif old_thr > 0:
            new_cap = old_thr
        else:
            new_cap = DEFAULT_INITIAL_CAPACITY
            new_thr = int(DEFAULT_LOAD_FACTOR * DEFAULT_INITIAL_CAPACITY)
        if new_thr == 0:
            ft = new_cap * DEFAULT_LOAD_FACTOR  # exact for powers of two
            new_thr = int(ft) if (new_cap < MAXIMUM_CAPACITY and ft < MAXIMUM_CAPACITY) else 2 ** 31 - 1
        self._thr = new_thr
        self._cap = new_cap
        self._order = None
        if old_cap == 0:
            return
        hs = self._h
        new_bins: Dict[int, Any] = {}
        for j, b in self._bins.items():
            if type(b) is list:
                if len(b) == 1:
                    k = b[0]
                    new_bins[hs[k] & (new_cap - 1)] = b
                    continue
                lo = [k for k in b if hs[k] & old_cap == 0]
                hi = [k for k in b if hs[k] & old_cap != 0]
                if lo:
                    new_bins[j] = lo
                if hi:
                    new_bins[j + old_cap] = hi
            else:
                # TreeNode.split: relink into lo/hi chains preserving order; a part with
                # <= UNTREEIFY_THRESHOLD nodes becomes a list, a part that is the whole bin
                # keeps its tree, otherwise the part is treeified again.
                keys = b.keys()
                if len(keys) == 1:  # "e.next == null" shortcut in resize()
                    new_bins[hs[keys[0]] & (new_cap - 1)] = b
                    continue
                lo = [k for k in keys if hs[k] & old_cap == 0]
                hi = [k for k in keys if hs[k] & old_cap != 0]
                if lo:
                    if len(lo) <= UNTREEIFY_THRESHOLD:
                        new_bins[j] = lo
                    elif hi:
                        new_bins[j] = _make_tree_bin(lo, hs)
                    else:
                        new_bins[j] = b
                if hi:
                    if len(hi) <= UNTREEIFY_THRESHOLD:
                        new_bins[j + old_cap] = hi
                    elif lo:
                        new_bins[j + old_cap] = _make_tree_bin(hi, hs)
                    else:
                        new_bins[j + old_cap] = b
        self._bins = new_bins

    def _keys(self) -> List[Any]:
        """Keys in HashIterator order (cached until the next mutation)."""
        o = self._order
        if o is None:
            bins = self._bins
            if len(bins) == 1:
                for b in bins.values():
                    o = list(b) if type(b) is list else b.keys()
            else:
                o = []
                for i in sorted(bins):
                    b = bins[i]
                    if type(b) is list:
                        o.extend(b)
                    else:
                        o.extend(b.keys())
            self._order = o
        return o

    def _clear(self) -> None:
        # HashMap.clear(): nulls the slots but keeps the table length and threshold
        self._h.clear()
        self._bins = {}
        self._order = None

    # -- introspection (tests) --

    def table_capacity(self) -> int:
        """``table.length`` (0 before the first insertion)."""
        return self._cap

    def bin_layout(self) -> List[tuple]:
        """[(index, 'list'|'tree', [keys in chain order]), ...] sorted by index."""
        out = []
        for i in sorted(self._bins):
            b = self._bins[i]
            out.append((i, "list", list(b)) if type(b) is list else (i, "tree", b.keys()))
        return out


_MISSING = object()


class JavaHashSet(_Table):
    """``java.util.HashSet`` (a ``HashMap`` with a dummy value), Java 8 iteration order."""

    __slots__ = ()

    def __init__(self, items: Optional[Iterable[Any]] = None, initial_capacity: Optional[int] = None):
        super().__init__(initial_capacity)
        if items is not None:
            for x in items:
                self._insert(x)

    @classmethod
    def from_collection(cls, c: List[Any]) -> "JavaHashSet":
        """``new HashSet<>(Collection c)``: capacity max(size/.75 + 1, 16), then addAll."""
        s = cls(initial_capacity=max(int(len(c) / 0.75) + 1, 16))
        for x in c:
            s._insert(x)
        return s

    def add(self, k: Any) -> bool:
        """``add``: True if the element was not present."""
        return self._insert(k)

    def add_all(self, items: Iterable[Any]) -> bool:
        """``AbstractCollection.addAll`` (one ``add`` per element, in order)."""
        changed = False
        for x in list(items):
            if self._insert(x):
                changed = True
        return changed

    def remove(self, k: Any) -> bool:
        """``remove``: True if the element was present."""
        return self._delete(k)

    def contains(self, k: Any) -> bool:
        return k in self._h

    def clear(self) -> None:
        self._clear()

    def to_array(self) -> List[Any]:
        """``toArray()``: a fresh list in iteration order."""
        return list(self._keys())

    def is_empty(self) -> bool:
        return not self._h

    def size(self) -> int:
        return len(self._h)

    def __contains__(self, k: Any) -> bool:
        return k in self._h

    def __iter__(self) -> Iterator[Any]:
        return iter(self._keys())

    def __len__(self) -> int:
        return len(self._h)

    def __repr__(self) -> str:
        return "JavaHashSet(%r)" % (self._keys(),)


class JavaHashMap(_Table):
    """``java.util.HashMap``, Java 8 iteration order (keys, values and entries)."""

    __slots__ = ("_v",)

    def __init__(self, initial_capacity: Optional[int] = None):
        super().__init__(initial_capacity)
        self._v: Dict[Any, Any] = {}

    def put(self, k: Any, v: Any) -> Any:
        """``put``: returns the previous value (None if absent). An existing key keeps its
        position."""
        old = self._v.get(k)
        self._insert(k)
        self._v[k] = v
        return old

    def get(self, k: Any) -> Any:
        return self._v.get(k)

    def contains_key(self, k: Any) -> bool:
        return k in self._h

    def remove(self, k: Any) -> Any:
        """``remove``: returns the removed value (None if absent)."""
        if self._delete(k):
            return self._v.pop(k)
        return None

    def clear(self) -> None:
        self._clear()
        self._v.clear()

    def keys(self) -> List[Any]:
        """``keySet()`` in iteration order (a fresh list)."""
        return list(self._keys())

    def values(self) -> List[Any]:
        """``values()`` in iteration order (a fresh list, as ``values().toArray()``)."""
        v = self._v
        return [v[k] for k in self._keys()]

    def items(self) -> List[tuple]:
        v = self._v
        return [(k, v[k]) for k in self._keys()]

    def is_empty(self) -> bool:
        return not self._h

    def size(self) -> int:
        return len(self._h)

    def __contains__(self, k: Any) -> bool:
        return k in self._h

    def __iter__(self) -> Iterator[Any]:
        return iter(self._keys())

    def __len__(self) -> int:
        return len(self._h)

    def __repr__(self) -> str:
        return "JavaHashMap(%r)" % (self.items(),)
