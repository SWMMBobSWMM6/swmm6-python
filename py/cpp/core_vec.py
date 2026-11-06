# core_vec.py - part A of the Python runtime (package cpp): std::vector (Vec and its specialisations VecD, VecI,
# ...), std::optional (Opt), std::pair (Pair), std::min/max/clamp (M), the array helpers (Arr, System.Array.Fill),
# moves and out-parameter copy-back (Mv), destructors (Dtor, Defer). From cs/runtime/Std.cs and Misc.cs.
# A vector is the list a, whose length is the capacity (libstdc++'s growth: a full vector grows to
# size + max(size, 1), into a new list, as the C#'s Array.Resize makes a new array), and the count n; the engine
# reads and writes v.a[i] and v.n directly. Slots past n always hold the element type's default value.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

from .core_num import (ICopy, IDisposable, IDestroyElems, Ref, ArrRef, Elem_Deep, Elem_Copy, Elem_Eq, Elem_Less,
                       IMMUTABLE_MAKERS, deq)
from .core_ptr import Ptr, Iter, RevIter, CPtr, Ptr_ZERO
from .core_str import OutOfRange, BadOptionalAccess

_CODES = ('', 'D', 'F', 'I', 'L', 'B', 'Z', 'S', 'C')
_PCODES = _CODES[1:]
# the default value of each specialisation's element type
_ZERO = {'D': 0.0, 'F': 0.0, 'I': 0, 'L': 0, 'B': 0, 'Z': False, 'S': 0, 'C': 0}

__all__ = ['Vec', 'Opt', 'Pair', 'Defer', 'NewDefer', 'Dtor_Run', 'Array_Fill',
           'NewPair', 'NewPair_A_B', 'Pair_Eq', 'Pair_op_Lt', 'Pair_op_Gt', 'Pair_op_Le', 'Pair_op_Ge',
           'Mv_Reset', 'Mv_Take', 'Mv_TakeRef', 'Mv_Delete', 'Mv_PostStep_CPtrr_i', 'Mv_PostStep_MItEr_i']

_new = object.__new__
_isinstance = isinstance
_list = list


def _copies(src):
    """Elem<T>.Copy of each element of a list (an ICopy cloned, a list copied deeply, anything else shared)."""
    return [x.Clone() if _isinstance(x, ICopy) else (Elem_Deep(x) if type(x) is _list else x) for x in src]


# ---------------------------------------------------------------------------------------------------- Vec

class _VecBase(ICopy, IDestroyElems):
    """What every std::vector shares; _z is the default element value of the subclass."""
    __slots__ = ('a', 'n')
    _z = None

    # ---- size and access (the engine reads v.n and v.a[i] itself)
    def size(self):
        return self.n

    def empty(self):
        return self.n == 0

    def capacity(self):
        return len(self.a)

    def at(self, i):
        if i >= self.n:
            raise OutOfRange('vector::_M_range_check: __n (which is %d) >= this->size() (which is %d)' % (i, self.n))
        return self.a[i]

    def ref_at(self, i):
        if i >= self.n:
            raise OutOfRange('vector::_M_range_check: __n (which is %d) >= this->size() (which is %d)' % (i, self.n))
        return ArrRef(self.a, i)

    def front(self):
        return self.a[0]

    def back(self):
        return self.a[self.n - 1]

    def ref_front(self):
        return ArrRef(self.a, 0)

    def ref_back(self):
        return ArrRef(self.a, self.n - 1)

    def data(self):
        """libstdc++'s data() is the start of the storage, which is nullptr until the vector first allocates
        (capacity 0); the engine tests it for null (C# Cpp.Vec.data)."""
        a = self.a
        return Ptr(a, 0) if a else Ptr_ZERO

    def get_Item(self, i):
        return self.a[i]

    def set_Item(self, i, v):
        self.a[i] = v

    def ref_Item(self, i):
        return ArrRef(self.a, i)

    get_Item_ul = get_Item_l = get_Item_i = get_Item
    set_Item_ul = set_Item_l = set_Item_i = set_Item
    ref_Item_ul = ref_Item_l = ref_Item_i = ref_Item

    # ---- storage
    def _grow(self, need):
        """Vec.Grow: libstdc++'s new capacity, size + max(size, 1), at least need; a new list holding the old one
        (the whole old capacity, as Array.Resize copies it) and defaults."""
        a = self.a
        la = len(a)
        if need <= la:
            return a
        n = self.n
        cap = n + (n if n > 1 else 1)
        if cap < need:
            cap = need
        a = a + [self._z] * (cap - la)
        self.a = a
        return a

    def reserve(self, c):
        a = self.a
        if c > len(a):
            self.a = a + [self._z] * (c - len(a))

    def shrink_to_fit(self):
        a = self.a
        if len(a) != self.n:
            self.a = a[0:self.n]

    def push_back(self, v):
        a = self.a
        n = self.n
        if n == len(a):
            a = self._grow(n + 1)
        a[n] = v
        self.n = n + 1

    def emplace_back(self, v):
        a = self.a
        n = self.n
        if n == len(a):
            a = self._grow(n + 1)
        a[n] = v
        self.n = n + 1
        return v

    def ref_emplace_back(self, v):
        a = self.a
        n = self.n
        if n == len(a):
            a = self._grow(n + 1)
        a[n] = v
        self.n = n + 1
        return ArrRef(a, n)

    def pop_back(self):
        n = self.n - 1
        self.n = n
        self.a[n] = self._z

    def clear(self):
        n = self.n
        if n:
            self.a[0:n] = [self._z] * n
        self.n = 0

    def swap(self, o):
        self.a, o.a = o.a, self.a
        self.n, o.n = o.n, self.n

    # ---- iterators
    def begin(self):
        return Iter(self, self.a, 0)

    def end(self):
        return Iter(self, self.a, self.n)

    cbegin = begin
    cend = end

    def rbegin(self):
        return RevIter(self, self.n)

    def rend(self):
        return RevIter(self, 0)

    # ---- insert and erase
    def insert_IterT_T(self, pos, v):
        p = pos.i
        n = self.n
        a = self.a
        if n == len(a):
            a = self._grow(n + 1)
        a[p + 1:n + 1] = a[p:n]
        a[p] = v
        self.n = n + 1
        return Iter(self, a, p)

    def _insert(self, p, tmp):
        m = len(tmp)
        n = self.n
        a = self._grow(n + m)
        a[p + m:n + m] = a[p:n]
        a[p:p + m] = tmp
        self.n = n + m
        return Iter(self, a, p)

    def insert_IterT_IterT_IterT(self, pos, b, e):
        return self._insert(pos.i, self._copy_range(b.a, b.i, e.i - b.i))

    def insert_IterT_TA(self, pos, il):
        """insert(pos, {a, b, c}): the initializer list's elements, copied."""
        return self._insert(pos.i, self._copy_range(il, 0, len(il)))

    def insert_IterT_PtrT_PtrT(self, pos, b, e):
        return self._insert(pos.i, self._copy_range(b.a, b.o, e.o - b.o))

    def insert_IterT_ul_T(self, pos, cnt, v):
        p = pos.i
        n = self.n
        a = self._grow(n + cnt)
        a[p + cnt:n + cnt] = a[p:n]
        a[p:p + cnt] = self._fill_copies(v, cnt)
        self.n = n + cnt
        return Iter(self, a, p)

    def erase_IterT(self, pos):
        p = pos.i
        n = self.n - 1
        a = self.a
        a[p:n] = a[p + 1:n + 1]
        a[n] = self._z
        self.n = n
        return Iter(self, a, p)

    def erase_IterT_IterT(self, b, e):
        p = b.i
        q = e.i
        n = self.n
        a = self.a
        m = q - p
        a[p:n - m] = a[q:n]
        a[n - m:n] = [self._z] * m
        self.n = n - m
        return Iter(self, a, p)

    # ---- assign
    def assign_ul_T(self, c, v):
        a = self.a
        la = len(a)
        if c > la:
            a = [self._z] * c
            self.a = a
        else:
            a[0:la] = [self._z] * la
        a[0:c] = self._fill_copies(v, c)
        self.n = c

    def assign_IterT_IterT(self, b, e):
        m = e.i - b.i
        self.a = self._copy_range(b.a, b.i, m)
        self.n = m

    def assign_TA(self, il):
        self.a = self._copy_range(il, 0, len(il))
        self.n = len(il)

    Assign_TA = assign_TA
    MoveFrom_TA = assign_TA        # v = {a, b, c}

    def resize_ul_T(self, c, v):
        n = self.n
        if c > n:
            a = self._grow(c)
            a[n:c] = self._fill_copies(v, c - n)
        else:
            self.a[c:n] = [self._z] * (n - c)
        self.n = c

    def __repr__(self):
        return '%s(%r)' % (type(self).__name__, self.a[0:self.n])


class _VecP(_VecBase):
    """std::vector of a primitive (double, int, bool, ...): elements copy by value."""
    __slots__ = ()

    def __init__(self):
        self.a = []
        self.n = 0

    @staticmethod
    def _copy_range(src, o, m):
        r = src[o:o + m]
        if len(r) != m:
            raise IndexError('cpp.Vec: range outside its array')
        return r

    @staticmethod
    def _fill_copies(v, m):
        return [v] * m

    def resize_ul(self, c):
        n = self.n
        if c > n:
            self._grow(c)          # the new slots already hold the default value
        else:
            self.a[c:n] = [self._z] * (n - c)
        self.n = c

    resize = resize_ul

    def DestroyElems(self):
        pass

    def Clone(self):
        v = _new(type(self))
        v.a = self.a[0:self.n]
        v.n = self.n
        return v

    def Assign_VecT(self, o):
        if o is self:
            return
        self.a = o.a[0:o.n]
        self.n = o.n

    def MoveFrom_VecT(self, o):
        """Move-assign: take o's storage and leave o empty, as libstdc++ does."""
        if o is self:
            return
        self.a = o.a
        self.n = o.n
        o.a = []
        o.n = 0

    def Assign(self, o):
        if type(o) is _list:
            self.assign_TA(o)
        else:
            self.Assign_VecT(o)

    def MoveFrom(self, o):
        if type(o) is _list:
            self.assign_TA(o)
        else:
            self.MoveFrom_VecT(o)


def _vec_eq_plain(x, y):
    n = x.n
    return n == y.n and x.a[0:n] == y.a[0:n]


def _vec_eq_double(x, y):
    """Vec<double>.Eq: EqualityComparer<double> (NaN equals NaN)."""
    n = x.n
    if n != y.n:
        return False
    xa = x.a
    ya = y.a
    for i in range(n):
        p = xa[i]
        q = ya[i]
        if p != q and (p == p or q == q):
            return False
    return True


class Vec(_VecBase):
    """std::vector<T> of a reference or struct type T (C# Cpp.Vec<T>): mk makes a new element (the maker the
    translator passes, or the Func<T> the C# constructor takes); mkx tells whether C# would hold it as its mk field
    (a Func<T> given to the constructor) rather than use Elem<T>.Make, which matters for Assign and MoveFrom
    (C#'s mk ??= o.mk)."""
    __slots__ = ('mk', 'mkx')

    def __init__(self, mk=None):
        self.a = []
        self.n = 0
        self.mk = mk
        self.mkx = False

    @staticmethod
    def _copy_range(src, o, m):
        r = src[o:o + m]
        if len(r) != m:
            raise IndexError('cpp.Vec: range outside its array')
        return [x.Clone() if _isinstance(x, ICopy) else (Elem_Deep(x) if type(x) is _list else x) for x in r]

    @staticmethod
    def _fill_copies(v, m):
        if _isinstance(v, ICopy):
            return [v.Clone() for _ in range(m)]
        if type(v) is _list:
            return [Elem_Deep(v) for _ in range(m)]
        return [v] * m

    def _made(self, m):
        mk = self.mk
        if mk is None:
            return [None] * m
        if mk in IMMUTABLE_MAKERS:
            return [mk()] * m
        return [mk() for _ in range(m)]

    def resize_ul(self, c):
        n = self.n
        if c > n:
            a = self._grow(c)
            a[n:c] = self._made(c - n)
        else:
            self.a[c:n] = [None] * (n - c)
        self.n = c

    resize = resize_ul

    def DestroyElems(self):
        """~vector: each element's destructor in order, first to last (libstdc++ _Destroy)."""
        a = self.a
        for i in range(self.n):
            Dtor_Run(a[i])

    def Clone(self):
        v = _new(Vec)
        v.mk = self.mk
        v.mkx = self.mkx
        v.a = _copies(self.a[0:self.n])
        v.n = self.n
        return v

    def _adopt(self, o):
        if not self.mkx and o.mkx:         # C#: mk ??= o.mk
            self.mk = o.mk
            self.mkx = True

    def Assign_VecT(self, o):
        if o is self:
            return
        self.a = _copies(o.a[0:o.n])
        self.n = o.n
        self._adopt(o)

    def MoveFrom_VecT(self, o):
        """Move-assign: take o's storage and leave o empty, as libstdc++ does."""
        if o is self:
            return
        self.a = o.a
        self.n = o.n
        self._adopt(o)
        o.a = []
        o.n = 0

    def Assign(self, o):
        if type(o) is _list:
            self.assign_TA(o)
        else:
            self.Assign_VecT(o)

    def MoveFrom(self, o):
        if type(o) is _list:
            self.assign_TA(o)
        else:
            self.MoveFrom_VecT(o)


def _mkvec(code):
    z = _ZERO[code]
    cls = type('Vec' + code, (_VecP,), {'__slots__': (), '_z': z,
                                         '__doc__': 'std::vector of the C# type coded %s (default %r).' % (code, z)})
    cls.__module__ = __name__

    def new_ul(count):
        v = _new(cls)
        v.a = [z] * count
        v.n = count
        return v

    def new_ul_T(count, x):
        v = _new(cls)
        v.a = [x] * count
        v.n = count
        return v

    def new_TA(init):
        v = _new(cls)
        v.a = _list(init)
        v.n = len(v.a)
        return v

    def adopt(arr):
        v = _new(cls)
        v.a = arr
        v.n = len(arr)
        return v

    return cls, new_ul, new_ul_T, new_TA, adopt


_g = globals()
for _c in _PCODES:
    _cls, _ul, _ulT, _TA, _ad = _mkvec(_c)
    _g['Vec' + _c] = _cls
    _g['NewVec' + _c] = _cls                     # new Vec<T>() is the class itself (it is a maker too)
    _g['NewVec' + _c + '_ul'] = _ul
    _g['NewVec' + _c + '_ul_T'] = _ulT
    _g['NewVec' + _c + '_TA'] = _TA
    _g['Vec' + _c + '_Adopt'] = _ad
    _g['Vec' + _c + '_Eq'] = _vec_eq_double if _c in ('D', 'F') else _vec_eq_plain
    __all__ += ['Vec' + _c, 'NewVec' + _c, 'NewVec' + _c + '_ul', 'NewVec' + _c + '_ul_T', 'NewVec' + _c + '_TA',
                'Vec' + _c + '_Adopt', 'Vec' + _c + '_Eq']
del _cls, _ul, _ulT, _TA, _ad, _c

NewVec = Vec                         # new Vec<T>() + the maker of T


def NewVec_FuncT(mk):
    """new Vec<T>(Func<T> mk)."""
    v = Vec(mk)
    v.mkx = True
    return v


def NewVec_ul(count, mk):
    """new Vec<T>(ulong count) + the maker of T: count new elements."""
    v = Vec(mk)
    v.a = v._made(count)
    v.n = count
    return v


def NewVec_ul_FuncT(count, mk):
    """new Vec<T>(ulong count, Func<T> mk)."""
    v = NewVec_ul(count, mk)
    v.mkx = True
    return v


def NewVec_ul_T(count, x, mk):
    """new Vec<T>(ulong count, T v) + the maker of T: count copies of v."""
    v = Vec(mk)
    v.a = Vec._fill_copies(x, count)
    v.n = count
    return v


def NewVec_TA(init, mk):
    """new Vec<T>(T[] init) + the maker of T: copies of the elements."""
    v = Vec(mk)
    v.a = _copies(init)
    v.n = len(init)
    return v


def Vec_Adopt(arr, mk=None):
    """Vec<T>.Adopt(T[]): a vector over the list itself."""
    v = Vec(mk)
    v.a = arr
    v.n = len(arr)
    return v


def Vec_Eq(x, y):
    """Vec<T>.Eq: same size and EqualityComparer<T>.Default.Equals of each pair."""
    n = x.n
    if n != y.n:
        return False
    xa = x.a
    ya = y.a
    for i in range(n):
        if not Elem_Eq(xa[i], ya[i]):
            return False
    return True


__all__ += ['NewVec', 'NewVec_FuncT', 'NewVec_ul', 'NewVec_ul_FuncT', 'NewVec_ul_T', 'NewVec_TA', 'Vec_Adopt',
            'Vec_Eq']


# ---------------------------------------------------------------------------------------------------- Opt

class _OptValRef(Ref):
    __slots__ = ('o',)

    def __init__(self, o):
        self.o = o

    def _get(self):
        return self.o.val

    def _set(self, x):
        self.o.val = x

    v = property(_get, _set)


class Opt(ICopy, IDestroyElems):
    """std::optional<T> (C# Cpp.Opt<T>): has, and val (the default value of T when empty)."""
    __slots__ = ('has', 'val')
    _z = None

    def __init__(self):
        self.has = False
        self.val = self._z

    def DestroyElems(self):
        if self.has:
            Dtor_Run(self.val)

    def has_value(self):
        return self.has

    def value(self):
        if not self.has:
            raise BadOptionalAccess()
        return self.val

    def ref_value(self):
        if not self.has:
            raise BadOptionalAccess()
        return _OptValRef(self)

    def get_Deref(self):
        return self.val

    def set_Deref(self, x):
        self.val = x

    def ref_Deref(self):
        return _OptValRef(self)

    def value_or(self, d):
        return self.val if self.has else d

    def reset(self):
        self.has = False
        self.val = self._z

    def MoveFrom(self, o):
        if o is self:
            return
        self.has = o.has
        self.val = o.val

    def emplace(self, v):
        self.has = True
        self.val = v
        return v

    def ref_emplace(self, v):
        self.has = True
        self.val = v
        return _OptValRef(self)

    def Clone(self):
        c = _new(type(self))
        if self.has:
            c.has = True
            c.val = Elem_Copy(self.val)
        else:
            c.has = False
            c.val = self._z
        return c

    def Assign(self, o):
        self.has = o.has
        self.val = Elem_Copy(o.val) if o.has else self._z

    def Set(self, v):
        self.has = True
        self.val = v

    def __repr__(self):
        return '%s(%r)' % (type(self).__name__, self.val) if self.has else type(self).__name__ + '()'


NewOpt = Opt


def NewOpt_T(v):
    """new Opt<T>(T v)."""
    o = _new(Opt)
    o.has = True
    o.val = v
    return o


__all__ += ['NewOpt', 'NewOpt_T']


def _mkopt(code):
    z = _ZERO[code]
    cls = type('Opt' + code, (Opt,), {'__slots__': (), '_z': z})
    cls.__module__ = __name__

    def new_T(v):
        o = _new(cls)
        o.has = True
        o.val = v
        return o

    return cls, new_T


for _c in _PCODES:
    _cls, _T = _mkopt(_c)
    _g['Opt' + _c] = _cls
    _g['NewOpt' + _c] = _cls
    _g['NewOpt' + _c + '_T'] = _T
    __all__ += ['Opt' + _c, 'NewOpt' + _c, 'NewOpt' + _c + '_T']
del _cls, _T, _c


# ---------------------------------------------------------------------------------------------------- Pair

class _FirstRef(Ref):
    __slots__ = ('p',)

    def __init__(self, p):
        self.p = p

    def _get(self):
        return self.p.first

    def _set(self, x):
        self.p.first = x

    v = property(_get, _set)


class _SecondRef(Ref):
    __slots__ = ('p',)

    def __init__(self, p):
        self.p = p

    def _get(self):
        return self.p.second

    def _set(self, x):
        self.p.second = x

    v = property(_get, _set)


class Pair(ICopy):
    """std::pair (C# Cpp.Pair<A, B>): a mutable class, so p.first = x works through references."""
    __slots__ = ('first', 'second')

    def __init__(self, a, b):
        self.first = a
        self.second = b

    def Clone(self):
        f = self.first
        s = self.second
        return Pair(f.Clone() if _isinstance(f, ICopy) else (Elem_Deep(f) if type(f) is _list else f),
                    s.Clone() if _isinstance(s, ICopy) else (Elem_Deep(s) if type(s) is _list else s))

    def Assign(self, o):
        self.first = Elem_Copy(o.first)
        self.second = Elem_Copy(o.second)

    def ref_first(self):
        return _FirstRef(self)

    def ref_second(self):
        return _SecondRef(self)

    def CompareTo(self, o):
        """std::pair's operator<: first, then second (each by Less<T>.Fn)."""
        if Elem_Less(self.first, o.first):
            return -1
        if Elem_Less(o.first, self.first):
            return 1
        if Elem_Less(self.second, o.second):
            return -1
        if Elem_Less(o.second, self.second):
            return 1
        return 0

    def __repr__(self):
        return 'Pair(%r, %r)' % (self.first, self.second)


def NewPair(ma, mb):
    """new Pair<A, B>() + the makers of A and B."""
    return Pair(ma(), mb())


NewPair_A_B = Pair


def Pair_Eq(x, y):
    return Elem_Eq(x.first, y.first) and Elem_Eq(x.second, y.second)


def Pair_op_Lt(x, y):
    return x.CompareTo(y) < 0


def Pair_op_Gt(x, y):
    return x.CompareTo(y) > 0


def Pair_op_Le(x, y):
    return x.CompareTo(y) <= 0


def Pair_op_Ge(x, y):
    return x.CompareTo(y) >= 0


# ---------------------------------------------------------------------------------------------------- M
# std::min / max / clamp exactly as libstdc++ writes them (the comparisons decide which argument comes back, which
# matters for -0.0 and NaN). The engine writes the two-argument forms inline; these are for completeness.

def M_min(a, b):
    return b if b < a else a


def M_max(a, b):
    return b if a < b else a


def M_min_T_T_FuncTTb(a, b, lt):
    return b if lt(b, a) else a


def M_max_T_T_FuncTTb(a, b, lt):
    return b if lt(a, b) else a


def M_clamp(v, lo, hi):
    return lo if v < lo else (hi if hi < v else v)


def M_min_dA(il):
    """min(initializer_list): the first smallest (NaN never replaces, a NaN first stays)."""
    r = il[0]
    for i in range(1, len(il)):
        x = il[i]
        if x < r:
            r = x
    return r


def M_max_dA(il):
    """max(initializer_list): the first largest."""
    r = il[0]
    for i in range(1, len(il)):
        x = il[i]
        if r < x:
            r = x
    return r


M_min_iA = M_min_dA
M_max_iA = M_max_dA

__all__ += ['M_min', 'M_max', 'M_clamp', 'M_min_dA', 'M_max_dA', 'M_min_iA', 'M_max_iA']
for _t in ('d_d', 'f_f', 'i_i', 'l_l', 'ul_ul', 'u_u', 'uc_uc', 'c_c', 's_s', 'us_us'):
    _g['M_min_' + _t] = M_min
    _g['M_max_' + _t] = M_max
    __all__ += ['M_min_' + _t, 'M_max_' + _t]
for _t in ('d_d_d', 'i_i_i', 'l_l_l'):
    _g['M_clamp_' + _t] = M_clamp
    __all__.append('M_clamp_' + _t)
for _c in _CODES:
    _s = '__' + _c if _c else ''
    for _n, _f in (('M_min_T_T_FuncTTb', M_min_T_T_FuncTTb), ('M_max_T_T_FuncTTb', M_max_T_T_FuncTTb)):
        _g[_n + _s] = _f
        __all__.append(_n + _s)
del _t, _c, _s, _n, _f


# ---------------------------------------------------------------------------------------------------- Arr

def Arr_Make(n, mk):
    """Arr.Make<T>(n) + the maker of T: n new elements."""
    if mk in IMMUTABLE_MAKERS:
        return [mk()] * n
    return [mk() for _ in range(n)]


def Arr_MakeWith(n, mk):
    """Arr.MakeWith<T>(n, Func<T> mk): n elements, each made by mk."""
    return [mk() for _ in range(n)]


def Arr_Make2(n, m, mk):
    """Arr.Make2<T>(n, m) + the maker of T: n rows of m (each a fresh list)."""
    return [Arr_Make(m, mk) for _ in range(n)]


def Arr_Clone(a):
    """Arr.Clone<T>: a new array; an element of a class (ICopy) type cloned, any other element shared (a nested
    array is not copied: C# copies only when T itself is a class)."""
    if a is None:
        return None
    return [x.Clone() if _isinstance(x, ICopy) else x for x in a]


def Arr_Assign(dst, src):
    """Arr.Assign<T>: copy assignment of each element of a class type (dst[i].Assign(src[i])), else the values."""
    for i in range(len(src)):
        d = dst[i]
        if _isinstance(d, ICopy):
            d.Assign(src[i])
        else:
            dst[i] = src[i]


def Arr_Fill(a, b, e, v):
    """Arr.Fill<T>(a, b, e, v): a[b..e) = copies of v."""
    for i in range(b, e):
        a[i] = Elem_Copy(v)


def Arr_Eq(x, y):
    """Arr.Eq<T>: same length and EqualityComparer<T>.Default.Equals of each pair."""
    if len(x) != len(y):
        return False
    for i in range(len(x)):
        if not Elem_Eq(x[i], y[i]):
            return False
    return True


def Array_Fill(lst, v):
    """System.Array.Fill(T[] a, T v): every element the same value (the same reference for a class)."""
    lst[0:len(lst)] = [v] * len(lst)


__all__ += ['Arr_Make', 'Arr_MakeWith', 'Arr_Make2', 'Arr_Clone', 'Arr_Assign', 'Arr_Fill', 'Arr_Eq']


def _arr_spec(code):
    z = _ZERO[code]

    def make(n):
        return [z] * n

    def make2(n, m):
        return [[z] * m for _ in range(n)]

    def clone(a):
        return None if a is None else a[:]

    def assign(dst, src):
        dst[0:len(src)] = src

    def fill(a, b, e, v):
        a[b:e] = [v] * (e - b)

    if code in ('D', 'F'):
        def eq(x, y):
            if len(x) != len(y):
                return False
            for i in range(len(x)):
                p = x[i]
                q = y[i]
                if p != q and (p == p or q == q):
                    return False
            return True
    else:
        def eq(x, y):
            return x == y
    return make, make2, clone, assign, fill, eq


for _c in _PCODES:
    for _n, _f in zip(('Arr_Make', 'Arr_Make2', 'Arr_Clone', 'Arr_Assign', 'Arr_Fill', 'Arr_Eq'), _arr_spec(_c)):
        _g[_n + '__' + _c] = _f
        __all__.append(_n + '__' + _c)
del _c, _n, _f


# ---------------------------------------------------------------------------------------------------- Dtor, Defer

def Dtor_Run(o):
    """Cpp.Dtor.Run: a C++ destructor: an IDisposable's Dispose (an engine class's destructor body, members, base;
    a file stream's close), a container's elements (IDestroyElems), each element of an array."""
    if o is None:
        return
    if _isinstance(o, IDisposable):
        o.Dispose()
    elif _isinstance(o, IDestroyElems):
        o.DestroyElems()
    elif type(o) is _list:
        for x in o:
            if x is not None and type(x) is not float and type(x) is not int:
                Dtor_Run(x)


class Defer(IDisposable):
    """A destructor deferred to the end of the enclosing block (a using declaration), unless cancelled (the object
    is returned to the caller)."""
    __slots__ = ('a',)

    def __init__(self, a):
        self.a = a

    def Cancel(self):
        self.a = None

    def Dispose(self):
        x = self.a
        self.a = None
        if x is not None:
            x()


NewDefer = Defer


# ---------------------------------------------------------------------------------------------------- Mv

def Mv_Reset(p, q):
    """unique_ptr::reset(q) and unique_ptr assignment: store the new pointer, then delete the old one (p a Ref)."""
    old = p.v
    p.v = q
    if old is not None and old is not q:
        Dtor_Run(old)


def Mv_Back(v, x, box):
    """Copy a boxed out-parameter back into its variable (x a Ref) after the call produced value v."""
    x.v = box[0]
    return v


def Mv_Take(x, mk):
    """std::move of a container or class into a new owner: the source (a Ref) is left a new default object."""
    r = x.v
    x.v = mk()
    return r


def Mv_TakeRef(x):
    """std::move of a unique_ptr or std::function: the source is left null."""
    r = x.v
    x.v = None
    return r


def Mv_PostStep_PtrTr_i(p, d):
    """p++ / p-- of a pointer held in a Ref: the old value."""
    r = p.v
    p.v = Ptr(r.a, r.o + d, r.ob, r.at)
    return r


def Mv_PostStep_IterTr_i(p, d):
    r = p.v
    v = r.v
    p.v = Iter(v, v.a, r.i + d) if v is not None else Iter(None, r.a, r.i + d)
    return r


def Mv_PostStep_CPtrr_i(p, d):
    r = p.v
    p.v = CPtr(r.B, r.O + d)
    return r


def Mv_PostStep_RevIterTr_i(p, d):
    r = p.v
    p.v = RevIter(r.v, r.i - 1)
    return r


_mit_ops = None


def Mv_PostStep_MItEr_i(p, d):
    """p++ / p-- of a map iterator (part B's MIt, reached through the package)."""
    global _mit_ops
    if _mit_ops is None:
        from . import MIt_op_Increment, MIt_op_Decrement
        _mit_ops = (MIt_op_Increment, MIt_op_Decrement)
    r = p.v
    p.v = _mit_ops[0](r) if d > 0 else _mit_ops[1](r)
    return r


def Mv_Delete(o):
    """delete p: an IDisposable's Dispose."""
    if _isinstance(o, IDisposable):
        o.Dispose()


def _take_value(z):
    def take(x):
        """std::move of a primitive held in a Ref: the value, the source left the type's default."""
        r = x.v
        x.v = z
        return r
    return take


for _c in _CODES:
    _s = '__' + _c if _c else ''
    for _n, _f in (('Mv_PostStep_PtrTr_i', Mv_PostStep_PtrTr_i), ('Mv_PostStep_IterTr_i', Mv_PostStep_IterTr_i),
                   ('Mv_PostStep_RevIterTr_i', Mv_PostStep_RevIterTr_i), ('Mv_Reset', Mv_Reset),
                   ('Mv_TakeRef', Mv_TakeRef)):
        _g[_n + _s] = _f
        if _n + _s not in __all__:
            __all__.append(_n + _s)
    if _c:
        _g['Mv_Take__' + _c] = _take_value(_ZERO[_c])
        __all__.append('Mv_Take__' + _c)
    for _c2 in ('O',) + _PCODES:
        _n = 'Mv_Back__' + (_c or 'O') + _c2
        _g[_n] = Mv_Back
        if _n not in __all__:
            __all__.append(_n)
_g['Mv_Back'] = Mv_Back
__all__.append('Mv_Back')


def Mv_Back_U(v, x, box):
    """Mv.Back into a uint variable: the boxed value read as unsigned 32 bits (a runtime reading memory into an int
    box cannot tell int from uint, so it decodes signed; the copy-back makes it the uint C# holds)."""
    x.v = box[0] & 0xFFFFFFFF
    return v


def Mv_Back_Q(v, x, box):
    """Mv.Back into a ulong variable: the boxed value read as unsigned 64 bits."""
    x.v = box[0] & 0xFFFFFFFFFFFFFFFF
    return v


for _c in ('O',) + _PCODES:     # Mv_Back__<R code>U and __<R code>Q (the translator's codes for T = uint, ulong)
    for _n, _f in (('Mv_Back__' + _c + 'U', Mv_Back_U), ('Mv_Back__' + _c + 'Q', Mv_Back_Q)):
        _g[_n] = _f
        __all__.append(_n)
__all__ += ['Mv_Back_U', 'Mv_Back_Q']
del _c, _s, _n, _f, _c2
