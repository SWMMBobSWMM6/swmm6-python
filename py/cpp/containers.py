# containers.py - std::unordered_map / unordered_set with libstdc++'s exact node order (GCC 13 bits/hashtable.h:
# one singly linked list, a bucket array pointing at the node before each bucket's first node, new nodes at the
# front of their bucket, the prime rehash policy with growth factor 2), std::hash as libstdc++ computes it
# (identity for integers, _Hash_bytes for strings), std::map / std::set (sorted), std::stack, std::queue and their
# iterators: the Python form of cs/runtime/Containers.cs (C# namespace Cpp), which is the specification.
#
# The engine iterates hashed containers, so their node order reaches its output; tests/stdcheck (hashcheck.py)
# compares this file's order with libstdc++'s. Lookups do not walk the buckets: each table also keeps a Python dict
# from the key's value (a string's bytes, an integer, a float, an object's identity) to its node, which finds the
# node the bucket walk would find, because the keys are unique. A table with a hasher and key-equal of the engine's
# own (UMapC, USetC: CiHash, CiEqual) keys that dict by the hash code instead and remembers which strings it has
# already looked up. The bucket array and the node list are kept exactly as libstdc++ keeps them, for the order.
#
# Element types are not known in Python, so what C# decides from typeof(K) is decided here from the key's run-time
# type: std::hash<K>, std::equal_to<K> and std::less<K> (Less_Fn) by the value's class.
#
# Part B of the Python runtime; it uses part A's Str, Pair, ICopy and exceptions (bound on first use).
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

import math as _math
import struct as _struct
from bisect import bisect_left as _bisect_left

from cpp import ICopy   # part A's base class of every copyable value (an engine name, imported earlier)

from .primelist import PrimeList_P as _PRIMES

__all__ = [
    'StdHash_HashBytes', 'StdHash_Of_Str', 'StdHash_Of_i', 'StdHash_Of_l', 'StdHash_Of_u', 'StdHash_Of_ul',
    'StdHash_For', 'Hash', 'NewHash', 'Hash_Of', 'EqualTo', 'NewEqualTo', 'Less', 'NewLess', 'Less_Fn',
    'RehashPolicy', 'NewRehashPolicy', 'HNode', 'NewHNode', 'KeyCopy_Of',
    'HashTable', 'NewHashTable', 'MIt', 'NewMIt_HNodeE', 'NewMIt_ListE_i', 'MIt_ZERO', 'MK_MIt',
    'MIt_op_Increment', 'MIt_op_Decrement', 'MIt_op_Eq', 'MIt_op_Ne',
    'UMap', 'NewUMap_FuncKul_FuncKKb_FuncV', 'NewUMap_PairKVA_FuncKul_FuncKKb',
    'USet', 'NewUSet_FuncTul_FuncTTb', 'NewUSet_TA_FuncTul_FuncTTb',
    'IHashFn', 'IBinFn', 'StdEq', 'NewStdEq',
    'UMapC', 'NewUMapC_FuncV', 'NewUMapC_PairKVA', 'USetC', 'NewUSetC', 'NewUSetC_TA',
    'Map', 'NewMap_FuncKKb_FuncV', 'NewMap_PairKVA_FuncKKb', 'Set', 'NewSet_FuncTTb', 'NewSet_TA_FuncTTb',
    'Stk', 'NewStk', 'Que', 'NewQue',
]

_M64 = 0xFFFFFFFFFFFFFFFF

# ------------------------------------------------------------------------------------------------ part A, bound late
# The package imports part A before this module, but the rules allow only inheritance from its classes at import
# time; the classes are found from the constructors the engine itself uses (the manifest names no class).

_Str = None        # part A's Str class
_Pair = None       # part A's Pair class
_NewPair = None    # NewPair_A_B


def _bind():
    global _Str, _Pair, _NewPair
    import cpp
    _NewPair = cpp.NewPair_A_B
    _Pair = type(_NewPair(0, 0))
    _Str = type(cpp.NewStr())


def _package_init():
    """Run by the package once every module is imported: binds part A's classes (the lazy checks stay for use
    before that) and declares MK_MIt's value immutable (part A's IMMUTABLE_MAKERS: a container may fill with it)."""
    _bind()
    import cpp
    im = getattr(cpp, 'IMMUTABLE_MAKERS', None)
    if im is not None:
        im.add(MK_MIt)


def _text_str(text):
    """A Str holding Python text (for exception messages), through its public fields b and n."""
    import cpp
    s = cpp.NewStr()
    e = text.encode('latin-1')
    s.b = bytearray(e) + b'\0'
    s.n = len(e)
    return s


def _out_of_range(text):
    import cpp
    return cpp.NewOutOfRange_Str(_text_str(text))


# ------------------------------------------------------------------------------------------------------- std::hash

_MUL = ((0xc6a4a793 << 32) + 0x5bd1e995) & _M64
_SEED = 0xc70f6907
_from_bytes = int.from_bytes
_pack_d = _struct.Struct('<d').pack


def StdHash_HashBytes(b, off, ln, seed):
    """libstdc++'s std::_Hash_bytes (libsupc++/hash_bytes.cc, the 64-bit branch) over b[off, off + ln): b is a
    bytes, bytearray or list of byte values; the result is a size_t (0 .. 2^64 - 1)."""
    if type(b) is list:
        b = bytes(b[off:off + ln])
        off = 0
    mul = _MUL
    h = (seed ^ (ln * mul)) & _M64
    p = off
    end8 = off + (ln & ~7)
    while p < end8:
        d = (_from_bytes(b[p:p + 8], 'little') * mul) & _M64
        d = ((d ^ (d >> 47)) * mul) & _M64
        h = ((h ^ d) * mul) & _M64
        p += 8
    if ln & 7:
        h = ((h ^ _from_bytes(b[p:off + ln], 'little')) * mul) & _M64
    h = ((h ^ (h >> 47)) * mul) & _M64
    return h ^ (h >> 47)


_HB = {}           # string bytes -> _Hash_bytes, so a name hashed once is not hashed again


def _hash_content(cb):
    """std::hash<std::string> of a string's bytes (a bytes object)."""
    h = _HB.get(cb)
    if h is None:
        h = StdHash_HashBytes(cb, 0, len(cb), _SEED)
        if len(_HB) >= 262144:
            _HB.clear()
        _HB[cb] = h
    return h


def _hash_double(d):
    """std::hash<double>: 0 for both zeros, otherwise _Hash_bytes of the eight bytes."""
    if d == 0.0:
        return 0
    return StdHash_HashBytes(_pack_d(d), 0, 8, _SEED)


def StdHash_Of_Str(s):
    return _hash_content(bytes(s.b[:s.n]))


def StdHash_Of_i(v):
    return v & _M64


def StdHash_Of_l(v):
    return v & _M64


def StdHash_Of_u(v):
    return v & _M64


def StdHash_Of_ul(v):
    return v & _M64


def _std_hash(k):
    """std::hash<K>(k), K being k's run-time type (C#'s StdHash.For<K> decides by typeof(K)): an integer of any width
    (enums included) its value sign- or zero-extended to 64 bits, which is the value modulo 2^64 because ints arrive
    in their type's range; a string _Hash_bytes; a double as std::hash<double>; any other object its identity:
    libstdc++ hashes the address, which no other program reproduces (such tables are looked up, not iterated into
    the output)."""
    t = type(k)
    if t is int:
        return k & _M64
    if _Str is None:
        _bind()
    if t is _Str:
        return _hash_content(bytes(k.b[:k.n]))
    if t is float:
        return _hash_double(k)
    if t is bool:
        return 1 if k else 0
    return id(k) & _M64


def StdHash_For():
    """Cpp.StdHash.For<K>(): std::hash<K> as a function."""
    return _std_hash


# The dict key of a table's node: the key's value where C# compares values (a string's bytes, an integer, a
# double; a NaN by its bits, as double.Equals makes NaN equal to NaN), else (None, the object's identity): C# hashes
# such keys by identity, the identity of the key the node stores (for a library value, the container's copy). It
# also gives the C++ hash code of the key (_code_of_dk).

def _dk(k):
    t = type(k)
    if t is int:
        return k
    if _Str is None:
        _bind()
    if t is _Str:
        return bytes(k.b[:k.n])
    if t is float:
        return k if k == k else ('nan', _pack_d(k))
    if t is bool:
        return k
    return (None, id(k))


def _code_of_dk(dk):
    t = type(dk)
    if t is int:
        return dk & _M64
    if t is bytes:
        return _hash_content(dk)
    if t is float:
        return _hash_double(dk)
    if t is bool:
        return 1 if dk else 0
    if dk[0] is None:
        return dk[1] & _M64                            # (None, id): an identity
    return StdHash_HashBytes(dk[1], 0, 8, _SEED)      # ('nan', bits)


def _deq(a, b):
    """EqualityComparer<T>.Default.Equals(a, b), T being a's run-time type: numbers by value (double.Equals makes NaN
    equal to NaN), strings by their bytes, a class with an Equals method by it, other objects by identity."""
    t = type(a)
    if t is int or t is bool:
        return a == b
    if t is float:
        return a == b or (a != a and b != b)
    if _Str is None:
        _bind()
    if t is _Str:
        return type(b) is _Str and a.n == b.n and a.b[:a.n] == b.b[:b.n]
    if a is None or b is None:
        return a is b
    if t is _Pair:
        return a is b                 # Cpp.Pair has no Equals of its own: reference equality
    eq = getattr(a, 'Equals', None)
    if eq is not None:
        return bool(eq(b))
    return a is b


class Hash(ICopy):
    """std::hash<T> (Cpp.Hash<T>)."""
    __slots__ = ()

    def op_Call(self, x):
        return _std_hash(x)

    def Clone(self):
        return Hash()

    def Assign(self, o):
        pass


def NewHash():
    return Hash()


def Hash_Of(x):
    return _std_hash(x)


class EqualTo(ICopy):
    """std::equal_to<T> (Cpp.EqualTo<T>)."""
    __slots__ = ()

    def op_Call(self, a, b):
        return _deq(a, b)

    def Clone(self):
        return EqualTo()

    def Assign(self, o):
        pass


def NewEqualTo():
    return EqualTo()


# ------------------------------------------------------------------------------------------------------- std::less

def _id_lt(a, b):
    """An order by identity for objects with no ordering of their own (std::set<const Column*>, which C++ orders by
    address; the C# Comparer throws there): the ids of live objects are unique, as addresses are, and any
    consistent order gives the same membership."""
    return id(a) < id(b)


def Less_Fn(a, b):
    """Cpp.Less<T>.Fn, std::less<T>, T being a's run-time type: numbers (enums, bool) with the primitive a < b (also
    for doubles, as C++ compares them), strings as char_traits compares them (unsigned bytes, then length), pairs
    first then second, other objects by their CompareTo, objects without one by identity."""
    t = type(a)
    if t is int or t is float:
        return a < b
    if _Str is None:
        _bind()
    if t is _Str:
        return a.b[:a.n] < b.b[:b.n]
    if t is _Pair:
        x = a.first
        y = b.first
        if Less_Fn(x, y):
            return True
        if Less_Fn(y, x):
            return False
        return Less_Fn(a.second, b.second)
    if t is bool:
        return (not a) and b
    if a is None:
        return b is not None         # C#'s Comparer: null first
    if b is None:
        return False
    ct = getattr(a, 'CompareTo', None)
    if ct is not None:
        return ct(b) < 0
    return _id_lt(a, b)


class Less(ICopy):
    """std::less<T> (Cpp.Less<T>); the comparator itself is Less_Fn."""
    __slots__ = ()
    Fn = staticmethod(Less_Fn)

    def op_Call(self, a, b):
        return Less_Fn(a, b)

    def Clone(self):
        return Less()

    def Assign(self, o):
        pass


def NewLess():
    return Less()


class _NoKey(Exception):
    pass


def _sk(k):
    """A sorted container's key as a Python value whose < is Less_Fn's order of k (and whose == is its equivalence):
    a string's bytes, a number, a pair as a tuple of those. Raises _NoKey for anything else (and a NaN, which
    Python's tuple comparison would treat differently)."""
    t = type(k)
    if t is int:
        return k
    if t is _Str:
        return bytes(k.b[:k.n])
    if t is float:
        if k != k:
            raise _NoKey()
        return k
    if t is _Pair:
        return (_sk(k.first), _sk(k.second))
    if t is bool:
        return k
    raise _NoKey()


# ----------------------------------------------------------------------------------------------- the rehash policy

_FAST_BKT = (2, 2, 2, 3, 5, 5, 7, 7, 11, 11, 11, 11, 13, 13)
_LAST = len(_PRIMES) - 1           # __prime_list + __n_primes - 1 (the sentinel is excluded)
_ML = 1.0                          # max_load_factor


class RehashPolicy(ICopy):
    """The prime rehash policy (libstdc++ src/c++11/hashtable_c++0x.cc and hashtable-aux.cc), Cpp.RehashPolicy.
    The hash tables below keep its NextResize in their own field and do the same arithmetic inline."""
    __slots__ = ('MaxLoad', 'NextResize')

    def __init__(self):
        self.MaxLoad = 1.0
        self.NextResize = 0

    def NextBkt(self, n):
        if n < 14:
            if n == 0:
                return 1
            self.NextResize = int(_math.floor(_FAST_BKT[n] * self.MaxLoad))
            return _FAST_BKT[n]
        lo = _bisect_left(_PRIMES, n, 6, _LAST)
        if lo == _LAST:
            self.NextResize = _M64
        else:
            self.NextResize = int(_math.floor(_PRIMES[lo] * self.MaxLoad))
        return _PRIMES[lo]

    def BktForElements(self, n):
        return int(_math.ceil(n / self.MaxLoad))

    def NeedRehash(self, nBkt, nElt, nIns):
        """(whether to rehash, the new bucket count), as C#'s value tuple."""
        if nElt + nIns > self.NextResize:
            minBkts = max(nElt + nIns, 0 if self.NextResize != 0 else 11) / self.MaxLoad
            if minBkts >= nBkt:
                return (True, self.NextBkt(max(int(_math.floor(minBkts)) + 1, nBkt * 2)))
            self.NextResize = int(_math.floor(nBkt * self.MaxLoad))
            return (False, 0)
        return (False, 0)

    def Clone(self):
        r = RehashPolicy()
        r.MaxLoad = self.MaxLoad
        r.NextResize = self.NextResize
        return r

    def Assign(self, o):
        self.MaxLoad = o.MaxLoad
        self.NextResize = o.NextResize


def NewRehashPolicy():
    return RehashPolicy()


def _next_bkt(t, n):
    """RehashPolicy.NextBkt with the table t's NextResize (max load 1.0)."""
    if n < 14:
        if n == 0:
            return 1
        t._nr = int(_math.floor(_FAST_BKT[n] * _ML))
        return _FAST_BKT[n]
    lo = _bisect_left(_PRIMES, n, 6, _LAST)
    t._nr = _M64 if lo == _LAST else int(_math.floor(_PRIMES[lo] * _ML))
    return _PRIMES[lo]


# ------------------------------------------------------------------------------------------------- the hash table

class HNode:
    """A hash table node (Cpp.HNode<E>); the before-begin sentinel is a node too. dk (the table's dict key) and same
    (the next node with the same hash code, in a table keyed by codes) are this port's own."""
    __slots__ = ('next', 'val', 'code', 'dk', 'same')

    def __init__(self):
        self.next = None
        self.val = None
        self.code = 0
        self.dk = None
        self.same = None


def NewHNode():
    return HNode()


_new_node = HNode.__new__


def _is_runtime_value(t):
    """Whether a key of class t is a library value the container copies (Cpp.KeyCopy: an ICopy class of the
    runtime, package cpp: a string, a vector, a pair) rather than an engine object it holds as a pointer."""
    m = getattr(t, '__module__', '') or ''
    return issubclass(t, ICopy) and (m == 'cpp' or m.startswith('cpp.'))


_KC = {}


def KeyCopy_Of(x):
    """Cpp.KeyCopy<T>.Of: the container's own copy of a key that is a library value; an engine object as it is."""
    t = type(x)
    if t is int or t is float or t is bool:
        return x
    if t is _Str:
        return x.Clone()
    c = _KC.get(t)
    if c is None:
        c = _KC[t] = _is_runtime_value(t)
    return x.Clone() if c else x


def _ecopy(x):
    """Elem<T>.Copy, by the value's run-time type: an ICopy object cloned, a list (a C# array) deeply, anything else
    shared."""
    t = type(x)
    if t is int or t is float or t is bool or x is None:
        return x
    if t is list:
        return [_ecopy(e) for e in x]
    if isinstance(x, ICopy):
        return x.Clone()
    return x


def _first(p):
    return p.first


def _ident(x):
    return x


def _pair_clone(p):
    return p.Clone()


class HashTable:
    """libstdc++'s _Hashtable for unique keys (Cpp.HashTable<K, E>). E is the stored element (a Pair for UMap, the
    key for USet). _b is the bucket array (each entry the node before the bucket's first node, or None), _bb the
    before-begin sentinel, _n the element count, _nr the policy's NextResize.

    _d finds nodes. With std::hash and std::equal_to (_cm true) it maps each key's value (_dk) to its node. With a
    hasher or key-equal given (_cm false) it maps hash codes to nodes, chained through HNode.same, the key-equal
    deciding; _pc / _nc then remember the strings found (to their node) and not found, for the engine's hashers,
    which are functions of the string's bytes alone."""
    __slots__ = ('_kf', 'hash', 'eq', '_b', '_bb', '_n', '_nr', '_d', '_cm', '_pc', '_nc', '_lck', '_lcode', '_lk')

    def __init__(self, key, hash=None, eq=None, cache=False):
        if _Str is None:
            _bind()
        self._kf = key
        self._cm = hash is None and eq is None
        self.hash = hash if hash is not None else _std_hash
        self.eq = eq if eq is not None else _deq
        self._b = [None]
        bb = _new_node(HNode)
        bb.next = None
        bb.val = None
        bb.code = 0
        bb.dk = None
        bb.same = None
        self._bb = bb
        self._n = 0
        self._nr = 0
        self._d = {}
        if cache and not self._cm:
            self._pc = {}
            self._nc = set()
        else:
            self._pc = None
            self._nc = None
        self._lck = None
        self._lcode = None
        self._lk = None

    # -- lookup: the node holding k, or None (then _lck / _lcode / _lk say what _add needs)

    def _lookup(self, k):
        if self._cm:
            t = type(k)
            if t is int:
                ck = k
            elif t is _Str:
                ck = bytes(k.b[:k.n])
            else:
                ck = _dk(k)
            n = self._d.get(ck)
            if n is None:
                self._lck = ck
            return n
        pc = self._pc
        ck = None
        if pc is not None and type(k) is _Str:
            ck = bytes(k.b[:k.n])
            n = pc.get(ck)
            if n is not None:
                return n
            if ck in self._nc:
                self._lck = ck
                self._lcode = None
                self._lk = k
                return None
        code = self.hash(k)
        n = self._d.get(code)
        if n is not None:
            eq = self.eq
            kf = self._kf
            while n is not None and not eq(k, kf(n.val)):
                n = n.same
        if ck is not None:
            if n is not None:
                pc[ck] = n
            else:
                nc = self._nc
                if len(nc) >= 65536:
                    nc.clear()
                nc.add(ck)
        if n is None:
            self._lck = ck
            self._lcode = code
            self._lk = k
        return n

    def _add(self, e):
        """Links e, whose key the last _lookup did not find, at the front of its bucket (after the rehash check)."""
        if self._cm:
            dk = self._lck
            t = type(dk)
            if t is int:
                code = dk & _M64
            elif t is bytes:
                code = _hash_content(dk)
            elif t is tuple and dk[0] is None:
                dk = (None, id(self._kf(e)))       # the stored key's identity
                code = dk[1] & _M64
            else:
                code = _code_of_dk(dk)
        else:
            code = self._lcode
            if code is None:
                code = self.hash(self._lk)
            dk = code
            self._lk = None
        n1 = self._n + 1
        if n1 > self._nr:
            self._grow(n1)
        node = _new_node(HNode)
        node.val = e
        node.code = code
        node.dk = dk
        node.same = None
        b = self._b
        nb = len(b)
        i = code % nb
        before = b[i]
        if before is not None:
            node.next = before.next
            before.next = node
        else:
            bb = self._bb
            nx = bb.next
            node.next = nx
            bb.next = node
            if nx is not None:
                b[nx.code % nb] = node
            b[i] = bb
        self._n = n1
        d = self._d
        if self._cm:
            d[dk] = node
        else:
            node.same = d.get(code)
            d[code] = node
            pc = self._pc
            if pc is not None:
                nc = self._nc
                if nc:
                    nc.clear()
                ck = self._lck
                if ck is not None:
                    pc[ck] = node
        return node

    def _grow(self, need):
        """_M_need_rehash(bucket_count, size, 1) when size + 1 > NextResize; rehashes if it says so."""
        nr = self._nr
        nbk = len(self._b)
        min_bkts = max(need, 0 if nr != 0 else 11) / _ML
        if min_bkts >= nbk:
            self._rehash_to(_next_bkt(self, max(int(_math.floor(min_bkts)) + 1, nbk * 2)))
        else:
            self._nr = int(_math.floor(nbk * _ML))

    def _rehash_to(self, n):
        """_M_rehash_aux for unique keys: relink every node into n buckets, keeping each bucket's order."""
        nb = [None] * n
        bb = self._bb
        p = bb.next
        bb.next = None
        bbeg = 0
        while p is not None:
            nxt = p.next
            i = p.code % n
            q = nb[i]
            if q is None:
                p.next = bb.next
                bb.next = p
                nb[i] = bb
                if p.next is not None:
                    nb[bbeg] = p
                bbeg = i
            else:
                p.next = q.next
                q.next = p
            p = nxt
        self._b = nb

    def _erase_node(self, i, prev, n):
        b = self._b
        nb = len(b)
        if prev is b[i]:
            # removing the bucket's first node (_M_remove_bucket_begin)
            nx = n.next
            nbk = (nx.code % nb) if nx is not None else 0
            if nx is None or nbk != i:
                if nx is not None:
                    b[nbk] = b[i]
                if b[i] is self._bb:
                    self._bb.next = nx
                b[i] = None
        elif n.next is not None:
            nbk = n.next.code % nb
            if nbk != i:
                b[nbk] = prev
        prev.next = n.next
        self._n -= 1
        d = self._d
        if self._cm:
            del d[n.dk]
        else:
            code = n.code
            f = d.get(code)
            if f is n:
                if n.same is None:
                    del d[code]
                else:
                    d[code] = n.same
            else:
                while f.same is not n:
                    f = f.same
                f.same = n.same
            if self._pc is not None:
                self._pc.clear()

    def EraseAt(self, n):
        i = n.code % len(self._b)
        prev = self._b[i]
        while prev.next is not n:
            prev = prev.next
        self._erase_node(i, prev, n)

    def _erase_key(self, k):
        n = self._lookup(k)
        if n is None:
            return False
        self.EraseAt(n)
        return True

    # -- the C# members

    def size(self):
        return self._n

    def empty(self):
        return self._n == 0

    def clear(self):
        """Keeps the bucket count, as libstdc++'s clear does."""
        self._b = [None] * len(self._b)
        self._bb.next = None
        self._n = 0
        self._d.clear()
        if self._pc is not None:
            self._pc.clear()
            self._nc.clear()

    def bucket_count(self):
        return len(self._b)

    def reserve(self, n):
        self.rehash(int(_math.ceil(n / _ML)))

    def rehash(self, n):
        """_M_rehash_policy._M_next_bkt(max(n, bkt_for_elements(size + 1))), applied only if it differs."""
        want = _next_bkt(self, max(n, int(_math.ceil((self._n + 1) / _ML))))
        if want != len(self._b):
            self._rehash_to(want)

    def Items(self):
        """The elements in node order; like the C# iterator, each step reads the next link when the loop asks."""
        p = self._bb.next
        while p is not None:
            yield p.val
            p = p.next

    def begin(self):
        return _mitn(self._bb.next)

    def end(self):
        return _MIT_END

    def find(self, k):
        n = self._lookup(k)
        return _MIT_END if n is None else _mitn(n)

    def count_(self, k):
        return 0 if self._lookup(k) is None else 1

    def count(self, k):
        return 0 if self._lookup(k) is None else 1

    def contains(self, k):
        return self._lookup(k) is not None

    def erase_K(self, k):
        return 1 if self._erase_key(k) else 0

    def erase_MItE(self, it):
        n = it.node
        nx = n.next
        self.EraseAt(n)
        return _mitn(nx)

    def _copy_from(self, o, copy):
        """_M_assign: the same bucket count, the same node order, each element copied by copy (None: shared)."""
        nbk = len(o._b)
        b = [None] * nbk
        bb = self._bb
        bb.next = None
        self._nr = o._nr
        cm = self._cm
        kf = self._kf
        d = {}
        prev = bb
        cnt = 0
        p = o._bb.next
        while p is not None:
            q = _new_node(HNode)
            v = p.val if copy is None else copy(p.val)
            q.val = v
            code = p.code
            q.code = code
            q.next = None
            q.same = None
            prev.next = q
            i = code % nbk
            if b[i] is None:
                b[i] = prev
            if cm:
                dk = _dk(kf(v))
                q.dk = dk
                d[dk] = q
            else:
                q.dk = code
                q.same = d.get(code)
                d[code] = q
            prev = q
            cnt += 1
            p = p.next
        self._b = b
        self._n = cnt
        self._d = d
        if self._pc is not None:
            self._pc = {}
            self._nc = set()


def NewHashTable(key, hash=None, eq=None):
    """new Cpp.HashTable<K, E>(key, hash, eq): hash None is std::hash<K>, eq None std::equal_to<K>."""
    return HashTable(key, hash, eq)


# ------------------------------------------------------------------------------------------------------- iterators

class MIt:
    """An iterator over a hashed or sorted container (C# struct Cpp.MIt<E>): a node (None at end) or a position in a
    sorted container's list. A struct, so an immutable value: ++ and -- make new iterators."""
    __slots__ = ('node', 'list', 'pos')

    def __init__(self, node=None, lst=None, pos=0):
        self.node = node
        self.list = lst
        self.pos = pos

    def Clone(self):
        return self              # a value: copying it is the copy

    def get_Deref(self):
        lst = self.list
        return lst[self.pos] if lst is not None else self.node.val

    def Equals(self, o):
        return isinstance(o, MIt) and MIt_op_Eq(self, o)

    def GetHashCode(self):
        return self.pos

    def __eq__(self, o):
        return isinstance(o, MIt) and MIt_op_Eq(self, o)

    def __ne__(self, o):
        return not self.__eq__(o)

    __hash__ = object.__hash__


_new_mit = MIt.__new__


def _mitn(n):
    it = _new_mit(MIt)
    it.node = n
    it.list = None
    it.pos = 0
    return it


def _mitl(lst, p):
    it = _new_mit(MIt)
    it.node = None
    it.list = lst
    it.pos = p
    return it


_MIT_END = MIt()
MIt_ZERO = _MIT_END            # default(MIt<E>): no node, no list (a hashed container's end())


def MK_MIt():
    return _MIT_END


def NewMIt_HNodeE(n):
    return _mitn(n)


def NewMIt_ListE_i(lst, p):
    return _mitl(lst, p)


def MIt_op_Increment(x):
    return _mitl(x.list, x.pos + 1) if x.list is not None else _mitn(x.node.next)


def MIt_op_Decrement(x):
    return _mitl(x.list, x.pos - 1)


def MIt_op_Eq(a, b):
    """A list position compares by position (a hashed container's end counts as no position); two nodes by identity."""
    al = a.list
    bl = b.list
    if al is not None or bl is not None:
        pa = a.pos if al is not None else 0x7FFFFFFF
        pb = b.pos if bl is not None else 0x7FFFFFFF
        return pa == pb
    return a.node is b.node


def MIt_op_Ne(a, b):
    return not MIt_op_Eq(a, b)


class _SecondRef:
    """A Ref (.v) to a pair's mapped value: what UMap/Map Index returns by reference."""
    __slots__ = ('p',)

    def __init__(self, p):
        self.p = p

    @property
    def v(self):
        return self.p.second

    @v.setter
    def v(self, x):
        self.p.second = x


class _ListRef:
    """A Ref (.v) to element i of a Python list."""
    __slots__ = ('l', 'i')

    def __init__(self, lst, i):
        self.l = lst
        self.i = i

    @property
    def v(self):
        return self.l[self.i]

    @v.setter
    def v(self, x):
        self.l[self.i] = x


# ---------------------------------------------------------------------------------------- unordered_map / _set

class UMap(HashTable, ICopy):
    """std::unordered_map<K, V> (Cpp.UMap<K, V>) on HashTable's node order. mk makes a mapped value for operator[]
    and try_emplace (the translator's maker; C#: an explicit maker or Elem<V>.Make)."""
    __slots__ = ('mk',)

    def __init__(self, h=None, eq=None, mk=None, cache=False):
        HashTable.__init__(self, _first, h, eq, cache)
        self.mk = mk

    def _makev(self):
        mk = self.mk
        return mk() if mk is not None else None

    def _node(self, k):
        """operator[]'s node: k's, inserted with a made value if absent (the map owns a copy of the key)."""
        n = self._lookup(k)
        if n is None:
            mk = self.mk
            n = self._add(_NewPair(KeyCopy_Of(k), mk() if mk is not None else None))
        return n

    def Index(self, k):
        """operator[] read as a value."""
        n = self._lookup(k)
        if n is None:
            mk = self.mk
            n = self._add(_NewPair(KeyCopy_Of(k), mk() if mk is not None else None))
        return n.val.second

    def ref_Index(self, k):
        """operator[] as a reference."""
        n = self._lookup(k)
        if n is None:
            mk = self.mk
            n = self._add(_NewPair(KeyCopy_Of(k), mk() if mk is not None else None))
        return _SecondRef(n.val)

    def _at(self, k):
        n = self._lookup(k)
        if n is None:
            raise _out_of_range('_Map_base::at')
        return n

    def at(self, k):
        return self._at(k).val.second

    def ref_at(self, k):
        return _SecondRef(self._at(k).val)

    def _insert_kv(self, k, v):
        n = self._lookup(k)
        if n is not None:
            return _NewPair(_mitn(n), False)
        return _NewPair(_mitn(self._add(_NewPair(KeyCopy_Of(k), v))), True)

    def insert(self, p):
        return self._insert_kv(p.first, p.second)

    def emplace_K_V(self, k, v):
        return self._insert_kv(k, v)

    def emplace_PairKV(self, p):
        return self._insert_kv(p.first, p.second)

    def try_emplace_K_V(self, k, v):
        return self._insert_kv(k, v)

    def try_emplace_K(self, k):
        n = self._lookup(k)
        if n is not None:
            return _NewPair(_mitn(n), False)
        return _NewPair(_mitn(self._add(_NewPair(KeyCopy_Of(k), self._makev()))), True)

    def insert_or_assign(self, k, v):
        n = self._lookup(k)
        if n is not None:
            n.val.second = v
            return _NewPair(_mitn(n), False)
        return _NewPair(_mitn(self._add(_NewPair(KeyCopy_Of(k), v))), True)

    def _init_list(self, il):
        for p in il:
            if self._lookup(p.first) is None:
                self._add(p.Clone())

    def Clone(self):
        m = UMap(None if self._cm else self.hash, None if self._cm else self.eq, self.mk)
        m._copy_from(self, _pair_clone)
        return m

    def Assign(self, o):
        if self is not o:
            self._copy_from(o, _pair_clone)

    def MoveFrom(self, o):
        if self is not o:
            self._copy_from(o, None)
            o.clear()


def NewUMap_FuncKul_FuncKKb_FuncV(h=None, eq=None, mk=None, *makers):
    """new UMap<K, V>(h, eq, mk): h None is std::hash<K>, eq None std::equal_to<K>, mk the maker of V."""
    if mk is None and makers:
        mk = makers[0]
    return UMap(h, eq, mk)


def NewUMap_PairKVA_FuncKul_FuncKKb(il, h=None, eq=None, mk=None):
    """new UMap<K, V>(initializer list, h, eq) + the maker of V: each pair copied in, a repeated key keeping the
    first."""
    m = UMap(h, eq, mk)
    m._init_list(il)
    return m


class USet(HashTable, ICopy):
    """std::unordered_set<T> (Cpp.USet<T>) on HashTable's node order."""
    __slots__ = ()

    def __init__(self, h=None, eq=None, cache=False):
        HashTable.__init__(self, _ident, h, eq, cache)

    def insert(self, x):
        n = self._lookup(x)
        if n is not None:
            return _NewPair(_mitn(n), False)
        return _NewPair(_mitn(self._add(KeyCopy_Of(x))), True)

    def emplace(self, x):
        return self.insert(x)

    def _init_list(self, il):
        for x in il:
            if self._lookup(x) is None:
                self._add(x)

    def Clone(self):
        m = USet(None if self._cm else self.hash, None if self._cm else self.eq)
        m._copy_from(self, _ecopy)
        return m

    def Assign(self, o):
        if self is not o:
            self._copy_from(o, _ecopy)

    def MoveFrom(self, o):
        if self is not o:
            self._copy_from(o, None)
            o.clear()


def NewUSet_FuncTul_FuncTTb(h=None, eq=None):
    return USet(h, eq)


def NewUSet_TA_FuncTul_FuncTTb(il, h=None, eq=None):
    m = USet(h, eq)
    m._init_list(il)
    return m


# ---------------------------------------------------------------------------- the engine's hashers and key-equals

_HASHFNS = []
_BINFNS = []


class IHashFn:
    """A hasher of the engine's own (struct CiHash { size_t operator()(...) }): the generated class implements
    HashOf by calling its operator(). Subclasses register themselves, for UMapC / USetC (see _engine_fns)."""
    __slots__ = ()

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.__module__ != __name__:
            _HASHFNS.append(cls)


class IBinFn:
    """A two-argument predicate of the engine's own (a key-equal such as CiEqual): Call2(a, b)."""
    __slots__ = ()

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.__module__ != __name__:      # not StdEq
            _BINFNS.append(cls)


class StdEq(IBinFn, ICopy):
    """std::equal_to, for a container whose hasher is the engine's but whose key-equal is the default."""
    __slots__ = ()

    def Call2(self, a, b):
        return _deq(a, b)

    def Clone(self):
        return StdEq()

    def Assign(self, o):
        pass


def NewStdEq():
    return StdEq()


_ENGINE_FNS = None


def _pick(classes, name, what):
    if len(classes) == 1:
        return classes[0]
    named = [c for c in classes if c.__name__ == name]
    if len(named) == 1:
        return named[0]
    raise RuntimeError('cpp.UMapC/USetC: the translator passes no %s, and the engine defines %d %s classes (%s); '
                       'cannot tell which one is meant' % (what, len(classes), what,
                                                          ', '.join(c.__name__ for c in classes) or 'none'))


def _scan_modules():
    """The IHashFn / IBinFn subclasses of the loaded modules (when a base class's __init_subclass__ did not pass
    the registration on)."""
    import sys
    for m in list(sys.modules.values()):
        for v in list(getattr(m, '__dict__', {}).values()):
            if isinstance(v, type) and getattr(v, '__module__', None) != __name__:
                if issubclass(v, IHashFn) and v is not IHashFn and v not in _HASHFNS:
                    _HASHFNS.append(v)
                if issubclass(v, IBinFn) and v is not IBinFn and v not in _BINFNS:
                    _BINFNS.append(v)


def _engine_fns():
    """C#'s new H() and new E() for UMapC<K, V, H, E>: the translator writes NewUMapC_FuncV(mk) without them, and
    the engine instantiates one pair only (CiHash, CiEqual), so the classes come from the IHashFn / IBinFn subclasses
    the engine defined. Returns (hash function, key-equal function)."""
    global _ENGINE_FNS
    if _ENGINE_FNS is None:
        if not _HASHFNS or not _BINFNS:
            _scan_modules()
        h = _pick(_HASHFNS, 'CiHash', 'IHashFn')()
        e = _pick(_BINFNS, 'CiEqual', 'IBinFn')()
        _ENGINE_FNS = (h.HashOf, e.Call2)
    return _ENGINE_FNS


class UMapC(UMap):
    """std::unordered_map<K, V, H, E> with the engine's hasher H and key-equal E (Cpp.UMapC): the same table, hashed
    by H, so lookups and the iteration order are the C++ ones."""
    __slots__ = ()

    def __init__(self, mk=None, fns=None):
        h, e = fns if fns is not None else _engine_fns()
        UMap.__init__(self, h, e, mk, True)

    def Clone(self):
        m = UMapC(self.mk, (self.hash, self.eq))
        m._copy_from(self, _pair_clone)
        return m


def NewUMapC_FuncV(mk=None, *makers):
    if mk is None and makers:
        mk = makers[0]
    return UMapC(mk)


def NewUMapC_PairKVA(il, mk=None):
    m = UMapC(mk)
    m._init_list(il)
    return m


class USetC(USet):
    """std::unordered_set<T, H, E> with the engine's hasher and key-equal (Cpp.USetC)."""
    __slots__ = ()

    def __init__(self, fns=None):
        h, e = fns if fns is not None else _engine_fns()
        USet.__init__(self, h, e, True)

    def Clone(self):
        m = USetC((self.hash, self.eq))
        m._copy_from(self, _ecopy)
        return m


def NewUSetC():
    return USetC()


def NewUSetC_TA(il):
    m = USetC()
    m._init_list(il)
    return m


# ----------------------------------------------------------------------------------------------- std::map / set

class Map(ICopy):
    """std::map (Cpp.Map<K, V>): the pairs kept sorted by the comparator in a list (a red-black tree's order is the
    sorted order). With std::less (the default) a parallel list of the keys as Python values (_sk) is searched with
    bisect, whose probes are those of the C# binary search; with another comparator, or a key _sk cannot express,
    the search calls the comparator."""
    __slots__ = ('lt', 'items', 'keys', 'mk')

    def __init__(self, less=None, mk=None):
        if _Str is None:
            _bind()
        if less is None:
            less = Less_Fn
        self.lt = less
        self.items = []
        self.keys = [] if less is Less_Fn else None
        self.mk = mk

    def _slow(self):
        self.keys = None

    def _lower(self, k):
        """(index of the first pair whose key is not less than k, k's sorted key or None)."""
        keys = self.keys
        if keys is not None:
            try:
                ck = _sk(k)
            except _NoKey:
                self.keys = None
            else:
                return _bisect_left(keys, ck), ck
        lt = self.lt
        items = self.items
        lo = 0
        hi = len(items)
        while lo < hi:
            mid = (lo + hi) // 2
            if lt(items[mid].first, k):
                lo = mid + 1
            else:
                hi = mid
        return lo, None

    def _at_key(self, i, k, ck):
        """Whether the pair at i (a lower bound of k) holds k."""
        if i >= len(self.items):
            return False
        if ck is not None:
            return not (ck < self.keys[i])
        return not self.lt(k, self.items[i].first)

    def _put(self, i, ck, p):
        self.items.insert(i, p)
        keys = self.keys
        if keys is not None:
            keys.insert(i, ck)

    def _find(self, k):
        i, ck = self._lower(k)
        return i if self._at_key(i, k, ck) else -1

    def _index(self, k):
        i, ck = self._lower(k)
        if not self._at_key(i, k, ck):
            mk = self.mk
            self._put(i, ck, _NewPair(KeyCopy_Of(k), mk() if mk is not None else None))
        return self.items[i]

    def Index(self, k):
        return self._index(k).second

    def ref_Index(self, k):
        return _SecondRef(self._index(k))

    def at(self, k):
        i = self._find(k)
        if i < 0:
            raise _out_of_range('map::at')
        return self.items[i].second

    def ref_at(self, k):
        i = self._find(k)
        if i < 0:
            raise _out_of_range('map::at')
        return _SecondRef(self.items[i])

    def insert(self, p):
        i, ck = self._lower(p.first)
        if self._at_key(i, p.first, ck):
            return _NewPair(_mitl(self.items, i), False)
        self._put(i, ck, _NewPair(KeyCopy_Of(p.first), p.second))
        return _NewPair(_mitl(self.items, i), True)

    def emplace_K_V(self, k, v):
        return self.insert(_NewPair(k, v))

    def emplace_PairKV(self, p):
        return self.insert(p)

    def try_emplace_K_V(self, k, v):
        return self.insert(_NewPair(k, v))

    def insert_or_assign(self, k, v):
        i = self._find(k)
        if i >= 0:
            self.items[i].second = v
            return _NewPair(_mitl(self.items, i), False)
        return self.insert(_NewPair(k, v))

    def find(self, k):
        i = self._find(k)
        return _mitl(self.items, len(self.items) if i < 0 else i)

    def lower_bound(self, k):
        return _mitl(self.items, self._lower(k)[0])

    def upper_bound(self, k):
        i, ck = self._lower(k)
        while self._at_key(i, k, ck):
            i += 1
        return _mitl(self.items, i)

    def count(self, k):
        return 1 if self._find(k) >= 0 else 0

    def contains(self, k):
        return self._find(k) >= 0

    def erase_K(self, k):
        i = self._find(k)
        if i < 0:
            return 0
        del self.items[i]
        if self.keys is not None:
            del self.keys[i]
        return 1

    def erase_MItPairKV(self, it):
        del self.items[it.pos]
        if self.keys is not None:
            del self.keys[it.pos]
        return _mitl(self.items, it.pos)

    def size(self):
        return len(self.items)

    def empty(self):
        return not self.items

    def clear(self):
        self.items.clear()
        if self.lt is Less_Fn:
            self.keys = []

    def begin(self):
        return _mitl(self.items, 0)

    def end(self):
        return _mitl(self.items, len(self.items))

    def Items(self):
        return self.items

    def _keys_of(self, o):
        return list(o.keys) if (o.keys is not None and self.lt is Less_Fn) else None

    def Clone(self):
        m = Map(self.lt, self.mk)
        m.items = [p.Clone() for p in self.items]
        m.keys = m._keys_of(self)
        return m

    def Assign(self, o):
        if self is o:
            return
        self.items = [p.Clone() for p in o.items]
        self.keys = self._keys_of(o)

    def MoveFrom(self, o):
        if self is o:
            return
        self.items = o.items
        self.keys = self._keys_of(o)
        o.items = []
        o.keys = [] if o.lt is Less_Fn else None


def NewMap_FuncKKb_FuncV(less=None, mk=None, *makers):
    """new Map<K, V>(less, mk): less None is std::less<K>, mk the maker of V."""
    if mk is None and makers:
        mk = makers[0]
    return Map(less, mk)


def NewMap_PairKVA_FuncKKb(il, less=None, mk=None):
    """new Map<K, V>(initializer list, less) + the maker of V."""
    m = Map(less, mk)
    for p in il:
        m.insert(p.Clone())
    return m


class Set(ICopy):
    """std::set (Cpp.Set<T>): the elements kept sorted in a list, searched as Map searches its keys."""
    __slots__ = ('lt', 'items', 'keys')

    def __init__(self, less=None):
        if _Str is None:
            _bind()
        if less is None:
            less = Less_Fn
        self.lt = less
        self.items = []
        self.keys = [] if less is Less_Fn else None

    def _lower(self, k):
        keys = self.keys
        if keys is not None:
            try:
                ck = _sk(k)
            except _NoKey:
                self.keys = None
            else:
                return _bisect_left(keys, ck), ck
        lt = self.lt
        items = self.items
        lo = 0
        hi = len(items)
        while lo < hi:
            mid = (lo + hi) // 2
            if lt(items[mid], k):
                lo = mid + 1
            else:
                hi = mid
        return lo, None

    def _at_key(self, i, k, ck):
        if i >= len(self.items):
            return False
        if ck is not None:
            return not (ck < self.keys[i])
        return not self.lt(k, self.items[i])

    def insert(self, x):
        i, ck = self._lower(x)
        if self._at_key(i, x, ck):
            return _NewPair(_mitl(self.items, i), False)
        self.items.insert(i, KeyCopy_Of(x))
        if self.keys is not None:
            self.keys.insert(i, ck)
        return _NewPair(_mitl(self.items, i), True)

    def emplace(self, x):
        return self.insert(x)

    def find(self, k):
        i, ck = self._lower(k)
        return _mitl(self.items, i if self._at_key(i, k, ck) else len(self.items))

    def count(self, k):
        i, ck = self._lower(k)
        return 1 if self._at_key(i, k, ck) else 0

    def contains(self, k):
        return self.count(k) > 0

    def erase_T(self, k):
        i, ck = self._lower(k)
        if self._at_key(i, k, ck):
            del self.items[i]
            if self.keys is not None:
                del self.keys[i]
            return 1
        return 0

    def erase_MItT(self, it):
        del self.items[it.pos]
        if self.keys is not None:
            del self.keys[it.pos]
        return _mitl(self.items, it.pos)

    def size(self):
        return len(self.items)

    def empty(self):
        return not self.items

    def clear(self):
        self.items.clear()
        if self.lt is Less_Fn:
            self.keys = []

    def begin(self):
        return _mitl(self.items, 0)

    def end(self):
        return _mitl(self.items, len(self.items))

    def Items(self):
        return self.items

    def _keys_of(self, o):
        return list(o.keys) if (o.keys is not None and self.lt is Less_Fn) else None

    def Clone(self):
        m = Set(self.lt)
        m.items = [_ecopy(x) for x in self.items]
        m.keys = m._keys_of(self)
        return m

    def Assign(self, o):
        if self is o:
            return
        self.items = [_ecopy(x) for x in o.items]
        self.keys = self._keys_of(o)

    def MoveFrom(self, o):
        if self is o:
            return
        self.items = o.items
        self.keys = self._keys_of(o)
        o.items = []
        o.keys = [] if o.lt is Less_Fn else None


def NewSet_FuncTTb(less=None):
    return Set(less)


def NewSet_TA_FuncTTb(il, less=None):
    m = Set(less)
    for x in il:
        m.insert(x)
    return m


# ------------------------------------------------------------------------------------------- std::stack / queue

class Stk(ICopy):
    """std::stack over std::deque (Cpp.Stk<T>): push (the element as given, no copy, as the C# does), pop, top."""
    __slots__ = ('items',)

    def __init__(self):
        self.items = []

    def push(self, x):
        self.items.append(x)

    def pop(self):
        self.items.pop()

    def top(self):
        return self.items[-1]

    def ref_top(self):
        return _ListRef(self.items, len(self.items) - 1)

    def empty(self):
        return not self.items

    def size(self):
        return len(self.items)

    def Clone(self):
        s = Stk()
        s.items = [_ecopy(x) for x in self.items]
        return s

    def Assign(self, o):
        if self is o:
            return
        self.items[:] = [_ecopy(x) for x in o.items]

    def MoveFrom(self, o):
        if self is o:
            return
        self.items[:] = o.items
        o.items.clear()


def NewStk():
    return Stk()


class Que(ICopy):
    """std::queue over std::deque (Cpp.Que<T>): first in, first out; the list is compacted as the C# does."""
    __slots__ = ('items', 'head')

    def __init__(self):
        self.items = []
        self.head = 0

    def push(self, x):
        self.items.append(x)

    def pop(self):
        items = self.items
        items[self.head] = None
        self.head += 1
        if self.head > 64 and self.head * 2 > len(items):
            del items[:self.head]
            self.head = 0

    def front(self):
        return self.items[self.head]

    def ref_front(self):
        return _ListRef(self.items, self.head)

    def back(self):
        return self.items[-1]

    def ref_back(self):
        return _ListRef(self.items, len(self.items) - 1)

    def empty(self):
        return len(self.items) == self.head

    def size(self):
        return len(self.items) - self.head

    def Clone(self):
        q = Que()
        q.items = [_ecopy(x) for x in self.items[self.head:]]
        return q

    def Assign(self, o):
        if self is o:
            return
        self.items[:] = [_ecopy(x) for x in o.items[o.head:]]
        self.head = 0

    def MoveFrom(self, o):
        if self is o:
            return
        self.items[:] = o.items[o.head:]
        self.head = 0
        o.items.clear()
        o.head = 0


def NewQue():
    return Que()
