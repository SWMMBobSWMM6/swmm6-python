# core_ptr.py - part A of the Python runtime (package cpp): the runtime structs that point into storage. CPtr (C's
# char*, the struct at the top of cs/runtime/CRuntime.cs), Ptr<T> (a raw pointer into an array, or to one field of
# an object), Iter<T> and RevIter<T> (vector iterators), CSpan<T> (std::span) and Box<T> (a reference member), all
# from cs/runtime/Std.cs and Misc.cs. Every struct is an immutable object shared freely: arithmetic makes a new one.
# One Python class serves every element type (Ptr<double>, Ptr<int>, ...): the specialised names the engine uses
# (PtrD_op_Add_PtrT_l, IterB_op_Eq, ...) are aliases.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

from .core_num import Ref, ArrRef, ByteRef, IMMUTABLE_MAKERS

# The Java codes of the specialised containers (D double, F float, I int/uint/int-enum, L long/ulong, B byte/sbyte,
# Z bool, S short, C char/ushort); '' is the generic class.
_CODES = ('', 'D', 'F', 'I', 'L', 'B', 'Z', 'S', 'C')

__all__ = ['CPtr', 'CPtr_ZERO', 'CPtr_Null', 'MK_CPtr', 'CPtr_from_Ptrc', 'CPtr_from_Ptruc', 'CPtr_op_Add_CPtr_i',
           'CPtr_op_Add_CPtr_l', 'CPtr_op_Add_CPtr_ul', 'CPtr_op_Sub', 'CPtr_op_Increment', 'CPtr_op_Decrement',
           'CPtr_op_Eq', 'CPtr_op_Ne', 'CPtr_op_Lt', 'CPtr_op_Gt', 'CPtr_op_Le', 'CPtr_op_Ge', 'CPtr_Diff',
           'cstr_len', 'cstr_bytes', 'cstr_text', 'buf_bytes', 'Ptr', 'Iter', 'RevIter', 'CSpan', 'Box']


# ---------------------------------------------------------------------------------------------------- CPtr

def _cpstr(B, O):
    """The text of the NUL-terminated bytes at B[O:] as Latin-1 (to the end of the buffer if there is no NUL)."""
    try:
        e = B.index(0, O)
    except ValueError:
        e = len(B)
    if type(B) is bytearray or type(B) is bytes:
        return B[O:e].decode('latin-1')
    return bytes([x & 0xFF for x in B[O:e]]).decode('latin-1')


class CPtr:
    """C char*: a byte buffer B and an offset O (C# Cpp.CPtr). B is a bytearray (a Str's buffer, a literal, a C
    buffer) or a list of byte or sbyte values (a byte[]/sbyte[] the engine made); None is the null pointer.
    Element reads give the signed char (x86 C's char), writes store the low byte."""
    __slots__ = ('B', 'O')

    def __init__(self, B, O):
        self.B = B
        self.O = O

    def Clone(self):
        return self      # a value: copying it is the copy

    def get_IsNull(self):
        return self.B is None

    # ---- the indexer (C# int this[int i]: get gives the signed char, set stores the low byte)
    def get(self, i):
        x = self.B[self.O + i]
        return x - 256 if x > 127 else x

    def set(self, i, v):
        self.B[self.O + i] = v & 0xFF

    def assign(self, i, v):
        """p[i] = v as an expression: the value is v (the indexer's int); the byte stored is its low 8 bits."""
        self.B[self.O + i] = v & 0xFF
        return v

    def ref(self, i):
        return ByteRef(self.B, self.O + i)

    def preInc(self, i):
        """++p[i]: the incremented int (the indexer's value plus one, not re-wrapped, as C# computes it)."""
        B = self.B
        k = self.O + i
        x = B[k]
        v = (x - 256 if x > 127 else x) + 1
        B[k] = v & 0xFF
        return v

    def preDec(self, i):
        B = self.B
        k = self.O + i
        x = B[k]
        v = (x - 256 if x > 127 else x) - 1
        B[k] = v & 0xFF
        return v

    def postInc(self, i):
        """p[i]++: the old signed char."""
        B = self.B
        k = self.O + i
        x = B[k]
        v = x - 256 if x > 127 else x
        B[k] = (v + 1) & 0xFF
        return v

    def postDec(self, i):
        B = self.B
        k = self.O + i
        x = B[k]
        v = x - 256 if x > 127 else x
        B[k] = (v - 1) & 0xFF
        return v

    get_Item = get
    get_Item_i = get
    set_Item = set
    set_Item_i = set
    ref_Item = ref
    ref_Item_i = ref

    # ---- equality (C#'s Equals / GetHashCode: the same buffer and, unless null, the same offset)
    def Equals(self, o):
        return isinstance(o, CPtr) and o.B is self.B and (self.B is None or o.O == self.O)

    Equals_CPtr = Equals
    Equals_o = Equals

    def __eq__(self, o):
        return isinstance(o, CPtr) and o.B is self.B and (self.B is None or o.O == self.O)

    def __ne__(self, o):
        return not self.__eq__(o)

    def __hash__(self):
        return (0 if self.B is None else id(self.B)) ^ self.O

    def GetHashCode(self):
        return ((0 if self.B is None else id(self.B)) ^ self.O) & 0x7FFFFFFF

    def ToString(self):
        """The NUL-terminated bytes as Latin-1 text, "(null)" for the null pointer."""
        if self.B is None:
            return '(null)'
        return _cpstr(self.B, self.O)

    __str__ = ToString

    def __repr__(self):
        return 'CPtr(%s, %d)' % ('None' if self.B is None else type(self.B).__name__, self.O)


CPtr_ZERO = CPtr(None, 0)     # default(CPtr): the null pointer
CPtr_Null = CPtr_ZERO


def MK_CPtr():
    """Maker of a CPtr element: the null pointer."""
    return CPtr_ZERO


IMMUTABLE_MAKERS.add(MK_CPtr)


def CPtr_from_Ptrc(p):
    """A char* made from a std::vector<char> / std::array<char, N> (sbyte storage): the same list. Reads through it
    sign-extend whatever the list holds; writes store bytes 0..255 (see the report: no live use writes to one)."""
    return CPtr(p.a, p.o)


def CPtr_from_Ptruc(p):
    """A char* made from unsigned char storage: the same list."""
    return CPtr(p.a, p.o)


def CPtr_op_Add_CPtr_i(p, n):
    return CPtr(p.B, p.O + n)


CPtr_op_Add_CPtr_l = CPtr_op_Add_CPtr_i
CPtr_op_Add_CPtr_ul = CPtr_op_Add_CPtr_i
CPtr_op_Add = CPtr_op_Add_CPtr_i


def CPtr_op_Sub(p, n):
    return CPtr(p.B, p.O - n)


CPtr_op_Sub_CPtr_i = CPtr_op_Sub


def CPtr_op_Increment(p):
    return CPtr(p.B, p.O + 1)


def CPtr_op_Decrement(p):
    return CPtr(p.B, p.O - 1)


def CPtr_op_Eq(a, b):
    return a.B is b.B and (a.B is None or a.O == b.O)


def CPtr_op_Ne(a, b):
    return not (a.B is b.B and (a.B is None or a.O == b.O))


def CPtr_op_Lt(a, b):
    return a.O < b.O


def CPtr_op_Gt(a, b):
    return a.O > b.O


def CPtr_op_Le(a, b):
    return a.O <= b.O


def CPtr_op_Ge(a, b):
    return a.O >= b.O


def CPtr_Diff(a, b):
    return a.O - b.O


# ---- helpers for the other modules: C strings and byte ranges of a CPtr's buffer

def cstr_len(p):
    """strlen(p): the bytes before the first NUL (C# R.strlen; ValueError if the buffer has none, where C# runs off
    the array)."""
    return p.B.index(0, p.O) - p.O


def buf_bytes(B, O, m):
    """m bytes of buffer B from offset O as a bytes-like object with values 0..255 (a bytearray slice, or bytes made
    from a list of byte or sbyte values). IndexError when the buffer is shorter, as C#'s Array.Copy throws."""
    if type(B) is bytearray or type(B) is bytes:
        r = B[O:O + m]
    else:
        r = bytes([x & 0xFF for x in B[O:O + m]])
    if len(r) != m or O < 0:
        raise IndexError('cpp: byte range %d+%d outside a buffer of %d' % (O, m, len(B)))
    return r


def cstr_bytes(p):
    """The bytes of the C string at p (up to its NUL), values 0..255."""
    B = p.B
    O = p.O
    e = B.index(0, O)
    if type(B) is bytearray or type(B) is bytes:
        return B[O:e]
    return bytes([x & 0xFF for x in B[O:e]])


def cstr_text(p):
    """C#'s R.Str(p): the C string at p as Latin-1 text."""
    return _cpstr(p.B, p.O)


# ---------------------------------------------------------------------------------------------------- Ptr

def _code(f):
    return getattr(f, '__code__', f)


class Ptr:
    """A raw pointer to T inside an array (C# Cpp.Ptr<T>): the list a and the offset o; null has a None list. A
    pointer made by &obj.field keeps the object ob and a RefAt callable at (at(ob) gives a Ref to the field; a and
    o unused): reads and writes go to the field itself."""
    __slots__ = ('a', 'o', 'ob', 'at')

    def __init__(self, a, o, ob=None, at=None):
        self.a = a
        self.o = o
        self.ob = ob
        self.at = at

    def Clone(self):
        return self      # a value: copying it is the copy

    def get_IsNull(self):
        return self.a is None and self.at is None

    # ---- the indexers (this[long], this[ulong], this[int]): a field pointer ignores the index, as the C# does
    def get_Item_i(self, i):
        at = self.at
        if at is None:
            return self.a[self.o + i]
        return at(self.ob).v

    get_Item_l = get_Item_i
    get_Item_ul = get_Item_i
    get_Item = get_Item_i

    def set_Item_i(self, i, v):
        at = self.at
        if at is None:
            self.a[self.o + i] = v
        else:
            at(self.ob).v = v

    set_Item_l = set_Item_i
    set_Item_ul = set_Item_i
    set_Item = set_Item_i

    def ref_Item_i(self, i):
        at = self.at
        if at is None:
            return ArrRef(self.a, self.o + i)
        return at(self.ob)

    ref_Item_l = ref_Item_i
    ref_Item_ul = ref_Item_i
    ref_Item = ref_Item_i

    def get_Deref(self):
        at = self.at
        if at is None:
            return self.a[self.o]
        return at(self.ob).v

    def set_Deref(self, v):
        at = self.at
        if at is None:
            self.a[self.o] = v
        else:
            at(self.ob).v = v

    def ref_Deref(self):
        at = self.at
        if at is None:
            return ArrRef(self.a, self.o)
        return at(self.ob)

    # ---- equality: two field pointers are equal when they reach the same object through the same lambda (C#
    # compares the delegates' methods; two Python lambdas from one site share their code object); two array
    # pointers when the lists are the same and, unless null, the offsets too.
    def Equals(self, q):
        return isinstance(q, Ptr) and Ptr_op_Eq(self, q)

    Equals_PtrT = Equals
    Equals_o = Equals

    def __eq__(self, q):
        return isinstance(q, Ptr) and Ptr_op_Eq(self, q)

    def __ne__(self, q):
        return not self.__eq__(q)

    def __hash__(self):
        return hash(self.o)

    def GetHashCode(self):
        return ((self.o + 0x80000000) & 0xFFFFFFFF) - 0x80000000

    def __repr__(self):
        if self.at is not None:
            return 'Ptr(field of %s)' % type(self.ob).__name__
        return 'Ptr(%s, %d)' % ('None' if self.a is None else 'list', self.o)


Ptr_ZERO = Ptr(None, 0)      # default(Ptr<T>): the null pointer (one object serves every T)


def MK_Ptr():
    """Maker of a Ptr element: the null pointer."""
    return Ptr_ZERO


IMMUTABLE_MAKERS.add(MK_Ptr)


NewPtr_TA_l = Ptr                 # new Ptr<T>(T[] a, long o): the class itself (no extra frame)


def NewPtr_o_RefAtT(ob, at):
    """new Ptr<T>(object ob, RefAt<T> at): &ob.field (at(ob) is a Ref to it)."""
    return Ptr(None, 0, ob, at)


def Ptr_op_Add_PtrT_l(p, k):
    return Ptr(p.a, p.o + k, p.ob, p.at)


Ptr_op_Add_PtrT_ul = Ptr_op_Add_PtrT_l


def Ptr_op_Sub_PtrT_l(p, k):
    return Ptr(p.a, p.o - k, p.ob, p.at)


def Ptr_op_Sub_PtrT_PtrT(p, q):
    return p.o - q.o


def Ptr_op_Increment(p):
    return Ptr(p.a, p.o + 1, p.ob, p.at)


def Ptr_op_Decrement(p):
    return Ptr(p.a, p.o - 1, p.ob, p.at)


def Ptr_op_Eq(p, q):
    pa = p.at
    qa = q.at
    if pa is not None or qa is not None:
        return (pa is not None and qa is not None and p.ob is q.ob and _code(pa) is _code(qa) and p.o == q.o)
    return p.a is q.a and (p.a is None or p.o == q.o)


def Ptr_op_Ne(p, q):
    return not Ptr_op_Eq(p, q)


def Ptr_op_Lt(p, q):
    return p.o < q.o


def Ptr_op_Gt(p, q):
    return p.o > q.o


def Ptr_op_Le(p, q):
    return p.o <= q.o


def Ptr_op_Ge(p, q):
    return p.o >= q.o


def Ptr_from_IterT(it):
    """implicit operator Ptr<T>(Iter<T>): the iterator's storage and index."""
    return Ptr(it.a, it.i)


# ---------------------------------------------------------------------------------------------------- Iter

class Iter:
    """A vector iterator (C# Cpp.Iter<T>): the vector v (None when made from a bare array or a Str's buffer), the
    storage a when made, and an index i. Arithmetic re-reads the vector's current storage, as the C# does."""
    __slots__ = ('v', 'a', 'i')

    def __init__(self, v, a, i):
        self.v = v
        self.a = a
        self.i = i

    def Clone(self):
        return self      # a value: copying it is the copy

    def get_Deref(self):
        return self.a[self.i]

    def set_Deref(self, x):
        self.a[self.i] = x

    def ref_Deref(self):
        return ArrRef(self.a, self.i)

    def get_Item(self, k):
        return self.a[self.i + k]

    def set_Item(self, k, x):
        self.a[self.i + k] = x

    def ref_Item(self, k):
        return ArrRef(self.a, self.i + k)

    get_Item_l = get_Item
    set_Item_l = set_Item
    ref_Item_l = ref_Item

    def Equals(self, o):
        return isinstance(o, Iter) and o.i == self.i

    Equals_IterT = Equals
    Equals_o = Equals

    def __eq__(self, o):
        return isinstance(o, Iter) and o.i == self.i

    def __ne__(self, o):
        return not self.__eq__(o)

    def __hash__(self):
        return hash(self.i)

    def GetHashCode(self):
        return ((self.i + 0x80000000) & 0xFFFFFFFF) - 0x80000000

    def __repr__(self):
        return 'Iter(%s, %d)' % ('vec' if self.v is not None else 'array', self.i)


Iter_ZERO = Iter(None, None, 0)    # default(Iter<T>)


def MK_Iter():
    return Iter_ZERO


IMMUTABLE_MAKERS.add(MK_Iter)


def NewIter_TA_l(a, i):
    """new Iter<T>(T[] a, long i)."""
    return Iter(None, a, i)


def NewIter_VecT_l(v, i):
    """new Iter<T>(Vec<T> v, long i)."""
    return Iter(v, v.a, i)


_new = object.__new__


def Iter_op_Add(x, k):
    v = x.v
    q = _new(Iter)
    if v is not None:
        q.v = v
        q.a = v.a          # the vector's current storage, as the C# re-reads it
    else:
        q.v = None
        q.a = x.a
    q.i = x.i + k
    return q


def Iter_op_Sub_IterT_l(x, k):
    return Iter_op_Add(x, -k)


def Iter_op_Sub_IterT_IterT(x, y):
    return x.i - y.i


def Iter_op_Increment(x):
    v = x.v
    q = _new(Iter)
    if v is not None:
        q.v = v
        q.a = v.a
    else:
        q.v = None
        q.a = x.a
    q.i = x.i + 1
    return q


def Iter_op_Decrement(x):
    return Iter_op_Add(x, -1)


def Iter_op_Eq(x, y):
    return x.i == y.i


def Iter_op_Ne(x, y):
    return x.i != y.i


def Iter_op_Lt(x, y):
    return x.i < y.i


def Iter_op_Gt(x, y):
    return x.i > y.i


def Iter_op_Le(x, y):
    return x.i <= y.i


def Iter_op_Ge(x, y):
    return x.i >= y.i


# ---------------------------------------------------------------------------------------------------- RevIter

class RevIter:
    """std::reverse_iterator over a vector (C# Cpp.RevIter<T>): the vector and one past the element it designates."""
    __slots__ = ('v', 'i')

    def __init__(self, v, i):
        self.v = v
        self.i = i

    def Clone(self):
        return self

    def get_Deref(self):
        return self.v.a[self.i - 1]

    def set_Deref(self, x):
        self.v.a[self.i - 1] = x

    def ref_Deref(self):
        return ArrRef(self.v.a, self.i - 1)

    def Equals(self, o):
        return isinstance(o, RevIter) and o.i == self.i

    Equals_o = Equals

    def __eq__(self, o):
        return isinstance(o, RevIter) and o.i == self.i

    def __ne__(self, o):
        return not self.__eq__(o)

    def __hash__(self):
        return hash(self.i)

    def GetHashCode(self):
        return ((self.i + 0x80000000) & 0xFFFFFFFF) - 0x80000000


RevIter_ZERO = RevIter(None, 0)


def MK_RevIter():
    return RevIter_ZERO


IMMUTABLE_MAKERS.add(MK_RevIter)


def NewRevIter_VecT_l(v, i):
    return RevIter(v, i)


def RevIter_op_Increment(x):
    return RevIter(x.v, x.i - 1)


def RevIter_op_Eq(x, y):
    return x.i == y.i


def RevIter_op_Ne(x, y):
    return x.i != y.i


# ---------------------------------------------------------------------------------------------------- CSpan

class CSpan:
    """std::span<T> (C# Cpp.CSpan<T>): n elements of the list a starting at o. The engine reads a, o and n."""
    __slots__ = ('a', 'o', 'n')

    def __init__(self, a, o, n):
        self.a = a
        self.o = o
        self.n = n

    def Clone(self):
        return self

    def get_Item(self, i):
        return self.a[self.o + i]

    def set_Item(self, i, x):
        self.a[self.o + i] = x

    def ref_Item(self, i):
        return ArrRef(self.a, self.o + i)

    get_Item_l = get_Item
    set_Item_l = set_Item
    ref_Item_l = ref_Item

    def size(self):
        return self.n

    def empty(self):
        return self.n == 0

    def data(self):
        return Ptr(self.a if self.a is not None else [], self.o)

    def front(self):
        return self.a[self.o]

    def back(self):
        return self.a[self.o + self.n - 1]

    def ref_front(self):
        return ArrRef(self.a, self.o)

    def ref_back(self):
        return ArrRef(self.a, self.o + self.n - 1)

    def subspan(self, off, cnt=0xFFFFFFFFFFFFFFFF):
        return CSpan(self.a, self.o + off, self.n - off if cnt == 0xFFFFFFFFFFFFFFFF else cnt)

    subspan_ul_ul = subspan

    def first(self, cnt):
        return CSpan(self.a, self.o, cnt)

    def last(self, cnt):
        return CSpan(self.a, self.o + self.n - cnt, cnt)

    # default(CSpan) equality is the struct's: the same list, offset and count
    def Equals(self, x):
        return isinstance(x, CSpan) and x.a is self.a and x.o == self.o and x.n == self.n

    def __eq__(self, x):
        return isinstance(x, CSpan) and x.a is self.a and x.o == self.o and x.n == self.n

    def __ne__(self, x):
        return not self.__eq__(x)

    def __hash__(self):
        return hash((id(self.a), self.o, self.n))


CSpan_ZERO = CSpan(None, 0, 0)    # default(CSpan<T>)


def MK_CSpan():
    return CSpan_ZERO


IMMUTABLE_MAKERS.add(MK_CSpan)


NewCSpan_TA_l_l = CSpan           # new CSpan<T>(T[] a, long o, long n)


# ---------------------------------------------------------------------------------------------------- Box

class Box:
    """A reference member of a class (T& held by an object, C# Cpp.Box<T>): an array cell a[o], or the place a
    RefGet callable g reaches (g() gives a Ref)."""
    __slots__ = ('a', 'o', 'g')

    def __init__(self, a, o, g=None):
        self.a = a
        self.o = o
        self.g = g

    def get_V(self):
        g = self.g
        if g is not None:
            return g().v
        return self.a[self.o]

    def set_V(self, x):
        g = self.g
        if g is not None:
            g().v = x
        else:
            self.a[self.o] = x

    def ref_V(self):
        g = self.g
        if g is not None:
            return g()
        return ArrRef(self.a, self.o)


NewBox_TA_l = Box                 # new Box<T>(T[] a, long o)


def NewBox_RefGetT(g):
    """new Box<T>(RefGet<T> g)."""
    return Box(None, 0, g)


def Box_Of(g):
    """Box<T>.Of(RefGet<T> g)."""
    return Box(None, 0, g)


# ---------------------------------------------------------------------------------------------------- aliases
# The specialised names: one function serves every element type.

def _alias(ns, names):
    for base, f in names:
        for c in _CODES:
            n = base.replace('@', c)
            ns[n] = f
            __all__.append(n)


_alias(globals(), [
    ('Ptr@_ZERO', Ptr_ZERO), ('MK_Ptr@', MK_Ptr), ('NewPtr@_TA_l', NewPtr_TA_l), ('NewPtr@_o_RefAtT', NewPtr_o_RefAtT),
    ('Ptr@_op_Add_PtrT_l', Ptr_op_Add_PtrT_l), ('Ptr@_op_Add_PtrT_ul', Ptr_op_Add_PtrT_ul),
    ('Ptr@_op_Sub_PtrT_l', Ptr_op_Sub_PtrT_l), ('Ptr@_op_Sub_PtrT_PtrT', Ptr_op_Sub_PtrT_PtrT),
    ('Ptr@_op_Increment', Ptr_op_Increment), ('Ptr@_op_Decrement', Ptr_op_Decrement),
    ('Ptr@_op_Eq', Ptr_op_Eq), ('Ptr@_op_Ne', Ptr_op_Ne), ('Ptr@_op_Lt', Ptr_op_Lt), ('Ptr@_op_Gt', Ptr_op_Gt),
    ('Ptr@_op_Le', Ptr_op_Le), ('Ptr@_op_Ge', Ptr_op_Ge), ('Ptr@_from_IterT', Ptr_from_IterT),
    ('Iter@_ZERO', Iter_ZERO), ('MK_Iter@', MK_Iter), ('NewIter@_TA_l', NewIter_TA_l),
    ('NewIter@_VecT_l', NewIter_VecT_l),
    ('Iter@_op_Add', Iter_op_Add), ('Iter@_op_Sub_IterT_l', Iter_op_Sub_IterT_l),
    ('Iter@_op_Sub_IterT_IterT', Iter_op_Sub_IterT_IterT), ('Iter@_op_Increment', Iter_op_Increment),
    ('Iter@_op_Decrement', Iter_op_Decrement), ('Iter@_op_Eq', Iter_op_Eq), ('Iter@_op_Ne', Iter_op_Ne),
    ('Iter@_op_Lt', Iter_op_Lt), ('Iter@_op_Gt', Iter_op_Gt), ('Iter@_op_Le', Iter_op_Le), ('Iter@_op_Ge', Iter_op_Ge),
    ('RevIter@_ZERO', RevIter_ZERO), ('MK_RevIter@', MK_RevIter), ('NewRevIter@_VecT_l', NewRevIter_VecT_l),
    ('RevIter@_op_Increment', RevIter_op_Increment), ('RevIter@_op_Eq', RevIter_op_Eq),
    ('RevIter@_op_Ne', RevIter_op_Ne),
    ('CSpan@_ZERO', CSpan_ZERO), ('MK_CSpan@', MK_CSpan), ('NewCSpan@_TA_l_l', NewCSpan_TA_l_l),
    ('Box@_Of', Box_Of), ('NewBox@_TA_l', NewBox_TA_l), ('NewBox@_RefGetT', NewBox_RefGetT),
])
for _c in _CODES[1:]:
    globals()['Box_Of__' + _c] = Box_Of            # Box<T>.Of with a primitive T: Box_Of__Z
    __all__.append('Box_Of__' + _c)
    for _k in ('Ptr', 'Iter', 'RevIter', 'CSpan', 'Box'):
        globals()[_k + _c] = globals()[_k]          # PtrD, IterD, ... name the one class
        __all__.append(_k + _c)
del _c, _k
