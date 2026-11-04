# algo.py - <algorithm> and <numeric> over vector / array iterators and pointers: the Python form of the Algo class
# of cs/runtime/Misc.cs (Cpp.Algo), which is the specification.
#
# Iterators and pointers are part A's immutable values: an Iter has .a (the backing list, or a Str's bytearray)
# and .i (the index), a Ptr .a and .o (the offset); the functions read and write a[...] directly, as the C# does,
# and make the iterators they return with part A's operators (IterD_op_Add, Ptr_op_Add_PtrT_l, ...), bound on
# first use.
#
# Names follow py/RUNTIME_HELPERS.md: Algo_<name><overload suffix><specialisation>. A generic member used with a
# primitive type argument has the suffix __ and the codes (Algo_fill_IterT_IterT_T__D); with a reference type none.
# The behaviour that depends on the element type is: copying an element (Elem<T>.Copy: a reference type's copy is
# a clone), equality (EqualityComparer<T>: double.Equals makes NaN equal to NaN), std::less (Less<T>.Fn: numbers
# with the primitive <), and the class of the iterator returned. Every member of the C# class exists for every
# element code.
#
# Part B of the Python runtime. Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

from bisect import bisect_left as _bisect_left, bisect_right as _bisect_right
from functools import reduce as _reduce
from operator import add as _add, eq as _ieq, lt as _plt

from . import containers as _ct
from .containers import Less_Fn, _deq, _ecopy

_CODES = ('O', 'D', 'F', 'I', 'L', 'B', 'S', 'C', 'Z')
_M32 = 0xFFFFFFFF
_M64 = 0xFFFFFFFFFFFFFFFF

__all__ = []


def _export(name, fn):
    globals()[name] = fn
    __all__.append(name)


# ------------------------------------------------------------------------------------ part A's makers, bound late

class _Late:
    """The first of the named part-A functions the package has, looked up on the first call."""
    __slots__ = ('names', 'fn')

    def __init__(self, *names):
        self.names = names
        self.fn = self._first

    def _first(self, *args):
        import cpp
        for nm in self.names:
            f = getattr(cpp, nm, None)
            if f is not None:
                self.fn = f
                return f(*args)
        raise RuntimeError('cpp.Algo: the runtime has none of ' + ', '.join(self.names))


def _sp(c):
    return '' if c == 'O' else c


_IADD = {c: _Late('Iter%s_op_Add' % _sp(c), 'Iter_op_Add') for c in _CODES}            # iterator + n
_PADD = {c: _Late('Ptr%s_op_Add_PtrT_l' % _sp(c), 'Ptr_op_Add_PtrT_l') for c in _CODES}  # pointer + n
_ARRIT = {c: _Late('NewIter%s_TA_l' % _sp(c), 'NewIterD_TA_l', 'NewIter_TA_l') for c in _CODES}   # Iter(T[], i)
_NEWVEC = {c: _Late('NewVec%s' % c, 'NewVecI') for c in _CODES if c != 'O'}        # new Vec<T>() (numbers)
_NEWVEC_O = _Late('NewVec')                                                         # new Vec<T>(maker)


def _copier(c):
    return _ecopy if c == 'O' else None


def _deqd(a, b):
    """double.Equals: NaN equal to NaN."""
    return a == b or (a != a and b != b)


def _eqf(c):
    if c == 'O':
        return _deq
    if c == 'D' or c == 'F':
        return _deqd
    return _ieq


def _ltf(c):
    """Less<T>.Fn: std::less (numbers with the primitive <)."""
    return Less_Fn if c == 'O' else _plt


# ------------------------------------------------------------------------------------------------ fill, fill_n

def _mk_fill_iter(c):
    cp = _copier(c)
    if cp is None:
        def fill(b, e, v):
            i0 = b.i
            n = e.i - i0
            if n > 0:
                b.a[i0:i0 + n] = [v] * n
        return fill

    def fill(b, e, v):
        i0 = b.i
        n = e.i - i0
        if n > 0:
            b.a[i0:i0 + n] = [cp(v) for _ in range(n)]
    return fill


def _mk_fill_ptr(c):
    cp = _copier(c)

    def fill(b, e, v):
        i0 = b.o
        n = e.o - i0
        if n > 0:
            b.a[i0:i0 + n] = [cp(v) for _ in range(n)] if cp is not None else [v] * n
    return fill


def _mk_fill_n_iter(c):
    cp = _copier(c)
    add = _IADD[c]

    def fill_n(b, n, v):
        if n > 0:
            i0 = b.i
            b.a[i0:i0 + n] = [cp(v) for _ in range(n)] if cp is not None else [v] * n
        return add.fn(b, n)
    return fill_n


def _mk_fill_n_ptr(c):
    cp = _copier(c)
    add = _PADD[c]

    def fill_n(b, n, v):
        if n > 0:
            i0 = b.o
            b.a[i0:i0 + n] = [cp(v) for _ in range(n)] if cp is not None else [v] * n
        return add.fn(b, n)
    return fill_n


# ----------------------------------------------------------------------------------- predicates, find, count

# a loop over the slice: for the short ranges the engine passes, faster than any(map(...))

def _any_of(b, e, f):
    for x in b.a[b.i:e.i]:
        if f(x):
            return True
    return False


def _all_of(b, e, f):
    for x in b.a[b.i:e.i]:
        if not f(x):
            return False
    return True


def _none_of(b, e, f):
    for x in b.a[b.i:e.i]:
        if f(x):
            return False
    return True


def _mk_find(c):
    add = _IADD[c]
    if c == 'O':
        def find(b, e, v):
            a = b.a
            i0 = b.i
            if _ct._Str is None:
                _ct._bind()
            if type(v) is _ct._Str:
                vb = v.b[:v.n]
                S = _ct._Str
                for i in range(i0, e.i):
                    x = a[i]
                    if type(x) is S and x.n == v.n and x.b[:x.n] == vb:
                        return add.fn(b, i - i0)
                return e
            for i in range(i0, e.i):
                if _deq(a[i], v):
                    return add.fn(b, i - i0)
            return e
    elif c == 'D' or c == 'F':
        def find(b, e, v):
            a = b.a
            i0 = b.i
            for i in range(i0, e.i):
                x = a[i]
                if x == v or (x != x and v != v):
                    return add.fn(b, i - i0)
            return e
    else:
        def find(b, e, v):
            i0 = b.i
            if e.i <= i0:
                return e
            try:
                i = b.a.index(v, i0, e.i)
            except ValueError:
                return e
            return add.fn(b, i - i0)
    return find


def _mk_find_if(c, want):
    add = _IADD[c]

    def find_if(b, e, f):
        a = b.a
        i0 = b.i
        for i in range(i0, e.i):
            if bool(f(a[i])) is want:
                return add.fn(b, i - i0)
        return e
    return find_if


def _mk_count(c):
    eq = _eqf(c)

    def count(b, e, v):
        a = b.a
        n = 0
        for i in range(b.i, e.i):
            if eq(a[i], v):
                n += 1
        return n
    return count


def _count_if(b, e, f):
    n = 0
    for x in b.a[b.i:e.i]:
        if f(x):
            n += 1
    return n


# ------------------------------------------------------------------------------------------------- accumulate

def _wrap32(x):
    return ((x + 0x80000000) & _M32) - 0x80000000


def _wrap64(x):
    return ((x + 0x8000000000000000) & _M64) - 0x8000000000000000


def Algo_accumulate_Iterd_Iterd_d(b, e, init):
    """double: the sums made one by one, left to right (never Python's sum, which compensates)."""
    return _reduce(_add, b.a[b.i:e.i], init)


def Algo_accumulate_Iteri_Iteri_i(b, e, init):
    return _wrap32(_reduce(_add, b.a[b.i:e.i], init))


def Algo_accumulate_Iterl_Iterl_l(b, e, init):
    return _wrap64(_reduce(_add, b.a[b.i:e.i], init))


def Algo_accumulate_Iteri_Iteri_d(b, e, init):
    for x in b.a[b.i:e.i]:
        init = init + x
    return float(init)


def Algo_accumulate_Iterul_Iterul_ul(b, e, init):
    return _reduce(_add, b.a[b.i:e.i], init) & _M64


def _accumulate_op(b, e, init, op):
    for x in b.a[b.i:e.i]:
        init = op(init, x)
    return init


for _n in ('Algo_accumulate_Iterd_Iterd_d', 'Algo_accumulate_Iteri_Iteri_i', 'Algo_accumulate_Iterl_Iterl_l',
           'Algo_accumulate_Iteri_Iteri_d', 'Algo_accumulate_Iterul_Iterul_ul'):
    __all__.append(_n)


# ------------------------------------------------------------------------------- lower_bound, upper_bound, ...

def _mk_lower_bound(c):
    add = _IADD[c]
    if c == 'O':
        def lower_bound(b, e, v):
            a = b.a
            first = b.i
            ln = e.i - first
            while ln > 0:
                half = ln >> 1
                mid = first + half
                if Less_Fn(a[mid], v):
                    first = mid + 1
                    ln = ln - half - 1
                else:
                    ln = half
            return add.fn(b, first - b.i)
    else:
        # bisect probes the same midpoints as the C# loop, with the primitive <
        def lower_bound(b, e, v):
            i0 = b.i
            return add.fn(b, _bisect_left(b.a, v, i0, e.i) - i0) if e.i > i0 else add.fn(b, 0)
    return lower_bound


def _mk_lower_bound_fn(c):
    add = _IADD[c]

    def lower_bound(b, e, v, lt):
        a = b.a
        first = b.i
        ln = e.i - first
        while ln > 0:
            half = ln >> 1
            mid = first + half
            if lt(a[mid], v):
                first = mid + 1
                ln = ln - half - 1
            else:
                ln = half
        return add.fn(b, first - b.i)
    return lower_bound


def _mk_upper_bound(c):
    add = _IADD[c]
    if c == 'O':
        def upper_bound(b, e, v):
            a = b.a
            first = b.i
            ln = e.i - first
            while ln > 0:
                half = ln >> 1
                mid = first + half
                if Less_Fn(v, a[mid]):
                    ln = half
                else:
                    first = mid + 1
                    ln = ln - half - 1
            return add.fn(b, first - b.i)
    else:
        def upper_bound(b, e, v):
            i0 = b.i
            return add.fn(b, _bisect_right(b.a, v, i0, e.i) - i0) if e.i > i0 else add.fn(b, 0)
    return upper_bound


def _mk_upper_bound_fn(c):
    add = _IADD[c]

    def upper_bound(b, e, v, lt):
        a = b.a
        first = b.i
        ln = e.i - first
        while ln > 0:
            half = ln >> 1
            mid = first + half
            if lt(v, a[mid]):
                ln = half
            else:
                first = mid + 1
                ln = ln - half - 1
        return add.fn(b, first - b.i)
    return upper_bound


def _mk_binary_search(c):
    lt = _ltf(c)

    def binary_search(b, e, v):
        a = b.a
        first = b.i
        ln = e.i - first
        while ln > 0:
            half = ln >> 1
            mid = first + half
            if lt(a[mid], v):
                first = mid + 1
                ln = ln - half - 1
            else:
                ln = half
        return first != e.i and not lt(v, a[first])
    return binary_search


def _mk_is_sorted(c):
    lt = _ltf(c)
    return lambda b, e: _is_sorted_fn(b, e, lt)


def _is_sorted_fn(b, e, lt):
    """std::is_sorted: no element is less than the one before it."""
    a = b.a
    for i in range(b.i + 1, e.i):
        if lt(a[i], a[i - 1]):
            return False
    return True


def _mk_adjacent_find(c, with_fn):
    add = _IADD[c]
    eq0 = _eqf(c)

    def adjacent_find(b, e, eq=None):
        if eq is None:
            eq = eq0
        a = b.a
        i0 = b.i
        for i in range(i0, e.i - 1):
            if eq(a[i], a[i + 1]):
                return add.fn(b, i - i0)
        return e
    if with_fn:
        return adjacent_find
    return lambda b, e: adjacent_find(b, e)


def _mk_unique(c, with_fn):
    """std::unique: keeps the first of each run of equal elements (libstdc++'s order of moves)."""
    add = _IADD[c]
    eq0 = _eqf(c)

    def unique(b, e, eq=None):
        if eq is None:
            eq = eq0
        if b.i == e.i:
            return e
        a = b.a
        first = b.i
        last = e.i
        nxt = first + 1
        while nxt != last:                         # adjacent_find
            if eq(a[first], a[nxt]):
                break
            first = nxt
            nxt += 1
        if nxt == last:
            return e
        dest = first
        first = nxt + 1
        while first != last:
            if not eq(a[dest], a[first]):
                dest += 1
                a[dest] = a[first]
            first += 1
        return add.fn(b, dest + 1 - b.i)
    if with_fn:
        return unique
    return lambda b, e: unique(b, e)


def _remove_if_impl(b, e, f, add):
    a = b.a
    first = b.i
    last = e.i
    while first != last and not f(a[first]):
        first += 1
    if first == last:
        return e
    res = first
    for i in range(first + 1, last):
        x = a[i]
        if not f(x):
            a[res] = x
            res += 1
    return add.fn(b, res - b.i)


def _mk_remove_if(c):
    add = _IADD[c]
    return lambda b, e, f: _remove_if_impl(b, e, f, add)


def _mk_remove(c):
    add = _IADD[c]
    eq = _eqf(c)
    return lambda b, e, v: _remove_if_impl(b, e, lambda x: eq(x, v), add)


def _mk_max_element(c, with_fn):
    add = _IADD[c]
    lt0 = _ltf(c)

    def max_element(b, e, lt=None):
        if lt is None:
            lt = lt0
        i0 = b.i
        if i0 == e.i:
            return e
        a = b.a
        best = i0
        if lt is _plt:
            # max with a key keeps the first of the largest, comparing with >, which is the C# loop's <
            best = max(range(i0, e.i), key=a.__getitem__)
        else:
            for i in range(i0 + 1, e.i):
                if lt(a[best], a[i]):
                    best = i
        return add.fn(b, best - i0)
    if with_fn:
        return max_element
    return lambda b, e: max_element(b, e)


def _mk_min_element(c, with_fn):
    add = _IADD[c]
    lt0 = _ltf(c)

    def min_element(b, e, lt=None):
        if lt is None:
            lt = lt0
        i0 = b.i
        if i0 == e.i:
            return e
        a = b.a
        best = i0
        if lt is _plt:
            best = min(range(i0, e.i), key=a.__getitem__)
        else:
            for i in range(i0 + 1, e.i):
                if lt(a[i], a[best]):
                    best = i
        return add.fn(b, best - i0)
    if with_fn:
        return min_element
    return lambda b, e: min_element(b, e)


# ------------------------------------------------------------------------------------------------ copy, copy_n

def _copy_into(src, s0, n, dst, d0, cp):
    if n > 0:
        seg = src[s0:s0 + n]
        dst[d0:d0 + n] = [cp(x) for x in seg] if cp is not None else seg


def _mk_copy(c, sp, dp):
    """copy(b, e, o) for the source kind sp and destination kind dp ('i' Iter, 'p' Ptr)."""
    cp = _copier(c)
    add = (_IADD if dp == 'i' else _PADD)[c]
    if cp is None and sp == 'i' and dp == 'i':
        def copy(b, e, o):
            bi = b.i
            n = e.i - bi
            if n > 0:
                oi = o.i
                o.a[oi:oi + n] = b.a[bi:bi + n]
            return add.fn(o, n)
        return copy
    if sp == 'i':
        if dp == 'i':
            def copy(b, e, o):
                n = e.i - b.i
                _copy_into(b.a, b.i, n, o.a, o.i, cp)
                return add.fn(o, n)
        else:
            def copy(b, e, o):
                n = e.i - b.i
                _copy_into(b.a, b.i, n, o.a, o.o, cp)
                return add.fn(o, n)
    else:
        if dp == 'i':
            def copy(b, e, o):
                n = e.o - b.o
                _copy_into(b.a, b.o, n, o.a, o.i, cp)
                return add.fn(o, n)
        else:
            def copy(b, e, o):
                n = e.o - b.o
                _copy_into(b.a, b.o, n, o.a, o.o, cp)
                return add.fn(o, n)
    return copy


def _mk_copy_n(c, kind):
    cp = _copier(c)
    add = (_IADD if kind == 'i' else _PADD)[c]
    if kind == 'i':
        def copy_n(b, n, o):
            _copy_into(b.a, b.i, n, o.a, o.i, cp)
            return add.fn(o, n)
    else:
        def copy_n(b, n, o):
            _copy_into(b.a, b.o, n, o.a, o.o, cp)
            return add.fn(o, n)
    return copy_n


# ------------------------------------------------------------------------------- transform, replace, iota, ...

def _transform_byte(b, e, o, f):
    """transform over a string's bytes with a function returning int or sbyte (::toupper and lambdas around it):
    each result stored as a char, as the C++ assignment converts it."""
    a = b.a
    oa = o.a
    d = o.i - b.i
    for i in range(b.i, e.i):
        oa[i + d] = f(a[i]) & 0xFF
    return _IADD['B'].fn(o, e.i - b.i)


Algo_transform_Iteruc_Iteruc_Iteruc_Funcuci = _transform_byte
Algo_transform_Iteruc_Iteruc_Iteruc_Funcucc = _transform_byte
__all__ += ['Algo_transform_Iteruc_Iteruc_Iteruc_Funcuci', 'Algo_transform_Iteruc_Iteruc_Iteruc_Funcucc']


def _mk_transform(cu):
    add = _IADD[cu]

    def transform(b, e, o, f):
        a = b.a
        oa = o.a
        d = o.i - b.i
        for i in range(b.i, e.i):
            oa[i + d] = f(a[i])
        return add.fn(o, e.i - b.i)
    return transform


def _mk_replace(c):
    eq = _eqf(c)

    def replace(b, e, old, nw):
        a = b.a
        for i in range(b.i, e.i):
            if eq(a[i], old):
                a[i] = nw
    return replace


def _reverse(b, e):
    i0 = b.i
    i1 = e.i
    if i1 - i0 > 1:
        a = b.a
        a[i0:i1] = a[i0:i1][::-1]


def Algo_iota_Iteri_Iteri_i(b, e, v):
    a = b.a
    for i in range(b.i, e.i):
        a[i] = v
        v = _wrap32(v + 1)


def Algo_iota_Iterul_Iterul_ul(b, e, v):
    a = b.a
    for i in range(b.i, e.i):
        a[i] = v
        v = (v + 1) & _M64


__all__ += ['Algo_iota_Iteri_Iteri_i', 'Algo_iota_Iterul_Iterul_ul']


def _mk_equal(c):
    eq = _eqf(c)

    def equal(b, e, b2):
        a = b.a
        a2 = b2.a
        d = b2.i - b.i
        for i in range(b.i, e.i):
            if not eq(a[i], a2[i + d]):
                return False
        return True
    return equal


def _distance_iter(a, b):
    return b.i - a.i


def _distance_ptr(a, b):
    return b.o - a.o


def _mk_next(c):
    add = _IADD[c]
    return lambda a, n=1: add.fn(a, n)


def _mk_prev(c):
    add = _IADD[c]
    return lambda a, n=1: add.fn(a, -n)


def _vec_of(c, src, items):
    """new Vec<T>() holding items (Algo.VecFrom sets the array and the count, as the C# does). A vector of a
    reference type takes the maker of the vector the source iterator belongs to, if part A's iterator says."""
    if c == 'O':
        mk = getattr(getattr(src, 'v', None), 'mk', None)
        if mk is None:
            import cpp
            mk = cpp.MK_N
        v = _NEWVEC_O.fn(mk)
    else:
        v = _NEWVEC[c].fn()
    v.a = items
    v.n = len(items)
    return v


def _mk_vec_from(c, kind):
    cp = _copier(c)
    if kind == 'i':
        def vec_from(b, e):
            seg = b.a[b.i:e.i]
            return _vec_of(c, b, [cp(x) for x in seg] if cp is not None else list(seg))
    else:
        def vec_from(b, e):
            seg = b.a[b.o:e.o]
            return _vec_of(c, b, [cp(x) for x in seg] if cp is not None else list(seg))
    return vec_from


def _begin_vec(v):
    return v.begin()


def _end_vec(v):
    return v.end()


def _mk_begin_arr(c):
    mk = _ARRIT[c]
    return lambda a: mk.fn(a, 0)


def _mk_end_arr(c):
    mk = _ARRIT[c]
    return lambda a: mk.fn(a, len(a))


def _size_vec(v):
    return v.n


def _size_arr(a):
    return len(a)


# ------------------------------------------------------------------------------------------------- the names

def _name(base, codes):
    return base if all(c == 'O' for c in codes) else base + '__' + ''.join(codes)


for _c in _CODES:
    _s = (_c,)
    for _base, _fn in (
            ('Algo_fill_IterT_IterT_T', _mk_fill_iter(_c)),
            ('Algo_fill_PtrT_PtrT_T', _mk_fill_ptr(_c)),
            ('Algo_fill_n_IterT_ul_T', _mk_fill_n_iter(_c)),
            ('Algo_fill_n_PtrT_ul_T', _mk_fill_n_ptr(_c)),
            ('Algo_any_of', _any_of),
            ('Algo_all_of', _all_of),
            ('Algo_none_of', _none_of),
            ('Algo_find', _mk_find(_c)),
            ('Algo_find_if', _mk_find_if(_c, True)),
            ('Algo_find_if_not', _mk_find_if(_c, False)),
            ('Algo_count', _mk_count(_c)),
            ('Algo_count_if', _count_if),
            ('Algo_lower_bound_IterT_IterT_T', _mk_lower_bound(_c)),
            ('Algo_lower_bound_IterT_IterT_T_FuncTTb', _mk_lower_bound_fn(_c)),
            ('Algo_upper_bound_IterT_IterT_T', _mk_upper_bound(_c)),
            ('Algo_upper_bound_IterT_IterT_T_FuncTTb', _mk_upper_bound_fn(_c)),
            ('Algo_binary_search', _mk_binary_search(_c)),
            ('Algo_is_sorted_IterT_IterT', _mk_is_sorted(_c)),
            ('Algo_is_sorted_IterT_IterT_FuncTTb', _is_sorted_fn),
            ('Algo_adjacent_find_IterT_IterT', _mk_adjacent_find(_c, False)),
            ('Algo_adjacent_find_IterT_IterT_FuncTTb', _mk_adjacent_find(_c, True)),
            ('Algo_unique_IterT_IterT', _mk_unique(_c, False)),
            ('Algo_unique_IterT_IterT_FuncTTb', _mk_unique(_c, True)),
            ('Algo_remove_if', _mk_remove_if(_c)),
            ('Algo_remove', _mk_remove(_c)),
            ('Algo_max_element_IterT_IterT', _mk_max_element(_c, False)),
            ('Algo_max_element_IterT_IterT_FuncTTb', _mk_max_element(_c, True)),
            ('Algo_min_element_IterT_IterT', _mk_min_element(_c, False)),
            ('Algo_min_element_IterT_IterT_FuncTTb', _mk_min_element(_c, True)),
            ('Algo_copy_IterT_IterT_IterT', _mk_copy(_c, 'i', 'i')),
            ('Algo_copy_IterT_IterT_PtrT', _mk_copy(_c, 'i', 'p')),
            ('Algo_copy_PtrT_PtrT_PtrT', _mk_copy(_c, 'p', 'p')),
            ('Algo_copy_PtrT_PtrT_IterT', _mk_copy(_c, 'p', 'i')),
            ('Algo_copy_n_IterT_ul_IterT', _mk_copy_n(_c, 'i')),
            ('Algo_copy_n_PtrT_ul_PtrT', _mk_copy_n(_c, 'p')),
            ('Algo_replace', _mk_replace(_c)),
            ('Algo_reverse', _reverse),
            ('Algo_equal', _mk_equal(_c)),
            ('Algo_distance_IterT_IterT', _distance_iter),
            ('Algo_distance_PtrT_PtrT', _distance_ptr),
            ('Algo_next', _mk_next(_c)),
            ('Algo_prev', _mk_prev(_c)),
            ('Algo_VecFrom', _mk_vec_from(_c, 'i')),
            ('Algo_VecFromPtr', _mk_vec_from(_c, 'p')),
            ('Algo_begin_VecT', _begin_vec),
            ('Algo_end_VecT', _end_vec),
            ('Algo_begin_TA', _mk_begin_arr(_c)),
            ('Algo_end_TA', _mk_end_arr(_c)),
            ('Algo_size_VecT', _size_vec),
            ('Algo_size_TA', _size_arr)):
        _export(_name(_base, _s), _fn)
    for _c2 in _CODES:
        _s2 = (_c, _c2)
        # accumulate<T, A>(b, e, init, op); lower_bound<T, V> / upper_bound<T, V> with a comparator (the iterator
        # returned is T's); transform<T, U> (the iterator returned is U's)
        _export(_name('Algo_accumulate_IterT_IterT_A_FuncATA', _s2), _accumulate_op)
        _export(_name('Algo_lower_bound_IterT_IterT_V_FuncTVb', _s2), _mk_lower_bound_fn(_c))
        _export(_name('Algo_upper_bound_IterT_IterT_V_FuncVTb', _s2), _mk_upper_bound_fn(_c))
        _export(_name('Algo_transform_IterT_IterT_IterU_FuncTU', _s2), _mk_transform(_c2))

del _c, _s, _c2, _s2, _base, _fn
