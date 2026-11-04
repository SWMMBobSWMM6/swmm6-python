# stdalgo.py - libstdc++'s sorting algorithms, step for step (GCC 13 bits/stl_algo.h and bits/stl_heap.h), the
# Python form of cs/runtime/StdAlgo.cs (Cpp.StdAlgo), which is the specification. std::sort is an introsort and is
# not stable: with equal keys the order it leaves depends on every swap it makes, so this port makes the same
# ones. std::stable_sort's result is fixed by stability alone. Checked against libstdc++ by tests/stdcheck
# (sortcheck.py: random keys with many ties, all lengths 0..300 and larger).
#
# StdAlgo_Sort(a, first, last, comp) sorts the Python list a over [first, last) (the engine passes a vector's
# backing list and two indices) with comp(x, y) -> bool. The specialisations (__D, __I, __L, ...) are the same
# algorithm; the translator calls them for vectors of numbers.
#
# Fast paths, taken only where every correct sort leaves the same values in the same places: with std::less
# (Less_Fn) on integers, on doubles with no NaN and no zero of both signs, on strings and on pairs of those, equal
# keys are equal values, so Python's own sort gives the bytes the introsort gives.
#
# Part B of the Python runtime. Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

from math import copysign as _copysign
from operator import lt as _plt

from . import containers as _ct
from .containers import Less_Fn, _sk, _NoKey

__all__ = [
    'StdAlgo_Sort', 'StdAlgo_StableSort', 'StdAlgo_PartialSort',
    'StdAlgo_Sort__D', 'StdAlgo_Sort__F', 'StdAlgo_Sort__I', 'StdAlgo_Sort__L', 'StdAlgo_Sort__B',
    'StdAlgo_Sort__S', 'StdAlgo_Sort__C', 'StdAlgo_Sort__Z',
    'StdAlgo_StableSort__D', 'StdAlgo_StableSort__F', 'StdAlgo_StableSort__I', 'StdAlgo_StableSort__L',
    'StdAlgo_StableSort__B', 'StdAlgo_StableSort__S', 'StdAlgo_StableSort__C', 'StdAlgo_StableSort__Z',
    'StdAlgo_PartialSort__D', 'StdAlgo_PartialSort__F', 'StdAlgo_PartialSort__I', 'StdAlgo_PartialSort__L',
    'StdAlgo_PartialSort__B', 'StdAlgo_PartialSort__S', 'StdAlgo_PartialSort__C', 'StdAlgo_PartialSort__Z',
]

_THRESHOLD = 16


# ------------------------------------------------------------------------------------------- std::sort (introsort)

def _sort(a, first, last, comp):
    """std::sort(first, last, comp) over a[first:last]."""
    if first != last:
        _introsort_loop(a, first, last, ((last - first).bit_length() - 1) * 2, comp)
        _final_insertion_sort(a, first, last, comp)


def _introsort_loop(a, first, last, depth, comp):
    while last - first > _THRESHOLD:
        if depth == 0:
            _partial_sort(a, first, last, last, comp)
            return
        depth -= 1
        # __unguarded_partition_pivot: __move_median_to_first(first, first + 1, mid, last - 1), then
        # __unguarded_partition(first + 1, last, first)
        mid = first + (last - first) // 2
        ia = first + 1
        ic = last - 1
        xa = a[ia]
        xb = a[mid]
        xc = a[ic]
        if comp(xa, xb):
            if comp(xb, xc):
                j = mid
            elif comp(xa, xc):
                j = ic
            else:
                j = ia
        elif comp(xa, xc):
            j = ia
        elif comp(xb, xc):
            j = ic
        else:
            j = mid
        pv = a[j]
        a[j] = a[first]
        a[first] = pv
        # the pivot stays at a[first] while the partition runs over [first + 1, last)
        lo = ia
        hi = last
        while True:
            while comp(a[lo], pv):
                lo += 1
            hi -= 1
            while comp(pv, a[hi]):
                hi -= 1
            if not (lo < hi):
                break
            t = a[lo]
            a[lo] = a[hi]
            a[hi] = t
            lo += 1
        _introsort_loop(a, lo, last, depth, comp)
        last = lo


def _final_insertion_sort(a, first, last, comp):
    if last - first > _THRESHOLD:
        _insertion_sort(a, first, first + _THRESHOLD, comp)
        for i in range(first + _THRESHOLD, last):
            # __unguarded_linear_insert(i)
            val = a[i]
            nxt = i - 1
            while comp(val, a[nxt]):
                a[nxt + 1] = a[nxt]
                nxt -= 1
            a[nxt + 1] = val
    else:
        _insertion_sort(a, first, last, comp)


def _insertion_sort(a, first, last, comp):
    if first == last:
        return
    for i in range(first + 1, last):
        val = a[i]
        if comp(val, a[first]):
            a[first + 1:i + 1] = a[first:i]          # move_backward(first, i, i + 1)
            a[first] = val
        else:
            nxt = i - 1
            while comp(val, a[nxt]):
                a[nxt + 1] = a[nxt]
                nxt -= 1
            a[nxt + 1] = val


# ------------------------------------------------------------------------------- heaps and std::partial_sort

def _partial_sort(a, first, middle, last, comp):
    _heap_select(a, first, middle, last, comp)
    _sort_heap(a, first, middle, comp)


def _heap_select(a, first, middle, last, comp):
    _make_heap(a, first, middle, comp)
    for i in range(middle, last):
        if comp(a[i], a[first]):
            # __pop_heap(first, middle, i)
            value = a[i]
            a[i] = a[first]
            _adjust_heap(a, first, 0, middle - first, value, comp)


def _make_heap(a, first, last, comp):
    if last - first < 2:
        return
    ln = last - first
    parent = (ln - 2) // 2
    while True:
        _adjust_heap(a, first, parent, ln, a[first + parent], comp)
        if parent == 0:
            return
        parent -= 1


def _sort_heap(a, first, last, comp):
    while last - first > 1:
        last -= 1
        # __pop_heap(first, last, last)
        value = a[last]
        a[last] = a[first]
        _adjust_heap(a, first, 0, last - first, value, comp)


def _adjust_heap(a, first, hole, ln, value, comp):
    top = hole
    second = hole
    lim = (ln - 1) // 2 if ln >= 1 else 0      # C#'s (len - 1) / 2 truncates toward zero
    while second < lim:
        second = 2 * (second + 1)
        if comp(a[first + second], a[first + (second - 1)]):
            second -= 1
        a[first + hole] = a[first + second]
        hole = second
    if (ln & 1) == 0 and second == (ln - 2) // 2:
        second = 2 * (second + 1)
        a[first + hole] = a[first + (second - 1)]
        hole = second - 1
    # __push_heap(first, hole, top, value)
    parent = (hole - 1) // 2
    while hole > top and comp(a[first + parent], value):
        a[first + hole] = a[first + parent]
        hole = parent
        parent = (hole - 1) // 2
    a[first + hole] = value


# ------------------------------------------------------------------------------------------ std::stable_sort

def _stable_sort(a, first, last, comp):
    """std::stable_sort: any stable sort gives libstdc++'s result; this is the C# merge sort, step for step."""
    n = last - first
    if n < 2:
        return
    _merge_sort(a, first, last, [None] * n, comp)


def _merge_sort(a, lo, hi, buf, comp):
    if hi - lo < 2:
        return
    mid = lo + (hi - lo) // 2
    _merge_sort(a, lo, mid, buf, comp)
    _merge_sort(a, mid, hi, buf, comp)
    i = lo
    j = mid
    k = 0
    while i < mid and j < hi:
        y = a[j]
        x = a[i]
        if comp(y, x):          # take the right one only if strictly less
            buf[k] = y
            j += 1
        else:
            buf[k] = x
            i += 1
        k += 1
    while i < mid:
        buf[k] = a[i]
        i += 1
        k += 1
    while j < hi:
        buf[k] = a[j]
        j += 1
        k += 1
    a[lo:lo + k] = buf[0:k]


# --------------------------------------------------------------------------------------------------- fast paths

def _plain_doubles(seg):
    """Whether equal doubles in seg are equal values: no NaN, and not both +0.0 and -0.0."""
    zs = 0
    for x in seg:
        if x != x:
            return False
        if x == 0.0:
            zs |= 2 if _copysign(1.0, x) < 0.0 else 1
    return zs != 3


def _floats(keys, out):
    """The doubles in a list of sorted keys, pairs (tuples) included."""
    for k in keys:
        t = type(k)
        if t is float:
            out.append(k)
        elif t is tuple:
            _floats(k, out)


def _less_ints(a, first, last, stable):
    seg = a[first:last]
    seg.sort()
    a[first:last] = seg


def _less_doubles(a, first, last, stable):
    seg = a[first:last]
    if _plain_doubles(seg):
        seg.sort()
        a[first:last] = seg
    elif stable:
        _stable_sort(a, first, last, _plt)
    else:
        _sort(a, first, last, _plt)


def _less_values(a, first, last, stable):
    """Less_Fn over values whose order _sk expresses (strings, numbers, pairs): equal keys are equal values, so a
    sort by key is the introsort's result; otherwise the algorithm itself, with Less_Fn."""
    if _ct._Str is None:
        _ct._bind()
    seg = a[first:last]
    try:
        keys = [_sk(x) for x in seg]
    except _NoKey:
        keys = None
    if keys is not None:
        dbl = []
        _floats(keys, dbl)
        if not dbl or _plain_doubles(dbl):
            order = sorted(range(len(seg)), key=keys.__getitem__)
            a[first:last] = [seg[i] for i in order]
            return
    if stable:
        _stable_sort(a, first, last, Less_Fn)
    else:
        _sort(a, first, last, Less_Fn)


# --------------------------------------------------------------------------------------------- the C# members

def StdAlgo_Sort(a, first, last, comp):
    """Cpp.StdAlgo.Sort<T>(T[] a, long first, long last, Func<T, T, bool> comp): std::sort over a[first:last]."""
    if last - first < 2:
        return
    if comp is Less_Fn:
        _less_values(a, first, last, False)
    else:
        _sort(a, first, last, comp)


def StdAlgo_StableSort(a, first, last, comp):
    """Cpp.StdAlgo.StableSort<T>: std::stable_sort over a[first:last]."""
    if last - first < 2:
        return
    if comp is Less_Fn:
        _less_values(a, first, last, True)
    else:
        _stable_sort(a, first, last, comp)


def StdAlgo_PartialSort(a, first, middle, last, comp):
    """Cpp.StdAlgo.PartialSort<T>: std::partial_sort(first, middle, last, comp)."""
    _partial_sort(a, first, middle, last, comp)


def _int_sort(a, first, last, comp):
    if last - first < 2:
        return
    if comp is Less_Fn:
        _less_ints(a, first, last, False)
    else:
        _sort(a, first, last, comp)


def _int_stable(a, first, last, comp):
    if last - first < 2:
        return
    if comp is Less_Fn:
        _less_ints(a, first, last, True)
    else:
        _stable_sort(a, first, last, comp)


def _dbl_sort(a, first, last, comp):
    if last - first < 2:
        return
    if comp is Less_Fn:
        _less_doubles(a, first, last, False)
    else:
        _sort(a, first, last, comp)


def _dbl_stable(a, first, last, comp):
    if last - first < 2:
        return
    if comp is Less_Fn:
        _less_doubles(a, first, last, True)
    else:
        _stable_sort(a, first, last, comp)


def _partial(a, first, middle, last, comp):
    _partial_sort(a, first, middle, last, _plt if comp is Less_Fn else comp)


StdAlgo_Sort__D = StdAlgo_Sort__F = _dbl_sort
StdAlgo_Sort__I = StdAlgo_Sort__L = StdAlgo_Sort__B = StdAlgo_Sort__S = StdAlgo_Sort__C = StdAlgo_Sort__Z = _int_sort
StdAlgo_StableSort__D = StdAlgo_StableSort__F = _dbl_stable
StdAlgo_StableSort__I = StdAlgo_StableSort__L = StdAlgo_StableSort__B = _int_stable
StdAlgo_StableSort__S = StdAlgo_StableSort__C = StdAlgo_StableSort__Z = _int_stable
StdAlgo_PartialSort__D = StdAlgo_PartialSort__F = StdAlgo_PartialSort__I = StdAlgo_PartialSort__L = _partial
StdAlgo_PartialSort__B = StdAlgo_PartialSort__S = StdAlgo_PartialSort__C = StdAlgo_PartialSort__Z = _partial
