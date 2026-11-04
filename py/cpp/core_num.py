# core_num.py - part A of the Python runtime (package cpp), the value layer every other module builds on:
# IEEE and integer helpers with C#'s results (f32, fdiv0, idiv, irem, fmod, the x86-64 cvttsd2si conversions of
# cs/runtime/Misc.cs Conv and CRuntime.cs R.D2I/D2L), bit casts, the typed boxes for params object[] arguments, the
# Ref protocol (Ref, Cell, ArrRef and friends), the runtime interfaces engine classes derive from (ICopy,
# IDisposable, IDestroyElems) and how containers copy, compare and order their elements (C#'s Elem<T>.Copy,
# ArrCopy.Deep, EqualityComparer<T>.Default, Comparer<T>.Default), with the element makers MK_I, MK_D, MK_Z, MK_N.
# The contract is py/RUNTIME_HELPERS.md (sections 1, 3, 4 and 7); the C# runtime (cs/runtime) is the specification.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

import math as _math
import struct as _struct

__all__ = [
    # IEEE helpers
    'f64_from_bits', 'f64_to_bits', 'f64_to_bits_signed', 'f32_from_bits', 'f32_to_bits', 'signbit', 'DNAN', 'QNAN',
    'INF', 'f32', 'fdiv0', 'fmod',
    # integers
    'idiv', 'irem', 'udiv', 'urem', 'i8', 'u8', 'i16', 'u16', 'i32', 'u32', 'i64', 'u64',
    'f2i32', 'f2i64', 'f2u32', 'f2u64', 'f2i16', 'f2u16', 'f2i8', 'f2u8',
    'Conv_D2I', 'Conv_D2L', 'Conv_D2U', 'Conv_D2UL', 'Conv_D2S', 'Conv_D2US', 'Conv_D2SB', 'Conv_D2B',
    'R_D2I', 'R_D2L',
    # typed boxes for params object[]
    'I64', 'U32', 'U64', 'I16', 'U16', 'I8', 'U8', 'Ch', 'F32',
    # references
    'Ref', 'Cell', 'ArrRef', 'AttrRef', 'FnRef', 'ByteRef',
    # interfaces
    'ICopy', 'IDisposable', 'IDestroyElems',
    # elements
    'Elem_Copy', 'Elem_Deep', 'Elem_NeedsCopy', 'Elem_Eq', 'Elem_Less', 'Elem_Compare', 'deq',
    'MK_I', 'MK_D', 'MK_Z', 'MK_N', 'IMMUTABLE_MAKERS',
    # C# text, control flow
    'cs_tostring', 'cs_equals', 'GotoSignal', 'throw_',
]

# Python's own OverflowError, captured before any runtime class could shadow the name.
_PyOverflowError = OverflowError

_F4 = _struct.Struct('<f')
_D8 = _struct.Struct('<d')
_Q8 = _struct.Struct('<Q')
_q8 = _struct.Struct('<q')
_I4 = _struct.Struct('<I')
_f4pack = _F4.pack
_f4unpack = _F4.unpack
_d8pack = _D8.pack
_d8unpack = _D8.unpack
_Q8pack = _Q8.pack
_Q8unpack = _Q8.unpack
_q8unpack = _q8.unpack
_I4pack = _I4.pack
_I4unpack = _I4.unpack
_copysign = _math.copysign
_mfmod = _math.fmod

INF = float('inf')


# ---------------------------------------------------------------------------------------------------- IEEE

def f64_from_bits(b):
    """BitConverter.Int64BitsToDouble: the double with these 64 bits (signed or unsigned pattern)."""
    return _d8unpack(_Q8pack(b & 0xFFFFFFFFFFFFFFFF))[0]


def f64_to_bits(x):
    """The 64 bits of a double as an unsigned integer (C# (ulong)BitConverter.DoubleToInt64Bits)."""
    return _Q8unpack(_d8pack(x))[0]


def f64_to_bits_signed(x):
    """BitConverter.DoubleToInt64Bits."""
    return _q8unpack(_d8pack(x))[0]


def f32_from_bits(b):
    """BitConverter.Int32BitsToSingle, widened to the Python float holding it."""
    return _f4unpack(_I4pack(b & 0xFFFFFFFF))[0]


def f32_to_bits(x):
    """The 32 bits of a single (x already single-rounded) as an unsigned integer (BitConverter.SingleToUInt32Bits)."""
    return _I4unpack(_f4pack(x))[0]


def signbit(x):
    """double.IsNegative: the sign bit (true for -0.0 and for a NaN with the sign set)."""
    return _q8unpack(_d8pack(x))[0] < 0


DNAN = f64_from_bits(0xFFF8000000000000)   # x86's default NaN (0/0, inf-inf, sqrt(-1)); glibc prints it "-nan"
QNAN = f64_from_bits(0x7FF8000000000000)   # C++'s quiet_NaN() (Cpp.Std.QNaN)


def _f32_int(x):
    """An int rounded once to the nearest single (ties to even) from its exact value: no double rounding."""
    m = -x if x < 0 else x
    e = m.bit_length() - 24
    if e > 0:
        q = m >> e
        r = m - (q << e)
        half = 1 << (e - 1)
        if r > half or (r == half and (q & 1)):
            q += 1
        m = q << e
        if e > 200:   # far beyond the single range (which ends below 2^128)
            return -INF if x < 0 else INF
    try:
        return _f4unpack(_f4pack(float(-m if x < 0 else m)))[0]
    except _PyOverflowError:
        return -INF if x < 0 else INF


def f32(x):
    """C#'s float: x (a float or an int) rounded once to the nearest IEEE single, ties to even; overflow gives
    +-inf, a NaN stays a NaN (quieted, its payload truncated, as x86's cvtsd2ss does)."""
    try:
        r = _f4unpack(_f4pack(x))[0]
    except _PyOverflowError:
        if type(x) is int:
            return _f32_int(x)
        return _copysign(INF, x)
    if -9007199254740992.0 <= r <= 9007199254740992.0:
        return r          # a float, or an int converted to double exactly first
    if type(x) is int:    # beyond 2^53: struct went through a double; round from the exact value instead
        return _f32_int(x)
    return r


def fdiv0(a, b):
    """a / b for b == 0 (+0.0, -0.0 or int 0): x86 SSE's result as C# computes it. A NaN dividend comes back
    quieted (divsd returns its NaN operand with the quiet bit set, so +NaN stays +NaN: not always DNAN); 0/0 is the
    default NaN; otherwise infinity with the sign of a times the sign of b."""
    if a != a:
        return _d8unpack(_Q8pack(_Q8unpack(_d8pack(a))[0] | 0x0008000000000000))[0]
    if a == 0:
        return DNAN
    if (a < 0) != (_q8unpack(_d8pack(float(b)))[0] < 0):
        return -INF
    return INF


def fmod(a, b):
    """C#'s % on doubles, which is C's fmod (glibc's libm through Python's math.fmod): NaN operands propagate, an
    infinite a or a zero b give the default NaN, an infinite b gives a. Accepts ints."""
    try:
        return _mfmod(a, b)
    except ValueError:
        return DNAN


# ---------------------------------------------------------------------------------------------------- integers

def idiv(a, b):
    """C# signed integer /: truncates toward zero; ZeroDivisionError when b is 0 (C#'s DivideByZeroException).
    long.MinValue / -1 is not wrapped (C# throws there)."""
    q = a // b
    if q < 0 and q * b != a:
        q += 1
    return q


def irem(a, b):
    """C# signed integer %: the remainder with the sign of the dividend; ZeroDivisionError when b is 0."""
    r = (-a if a < 0 else a) % (-b if b < 0 else b)
    return -r if a < 0 else r


def udiv(a, b):
    """C# unsigned integer / (operands already in range): floor division; ZeroDivisionError when b is 0."""
    return a // b


def urem(a, b):
    """C# unsigned integer %."""
    return a % b


def i8(x):
    """unchecked((sbyte)x)."""
    return ((x + 0x80) & 0xFF) - 0x80


def u8(x):
    """unchecked((byte)x)."""
    return x & 0xFF


def i16(x):
    """unchecked((short)x)."""
    return ((x + 0x8000) & 0xFFFF) - 0x8000


def u16(x):
    """unchecked((ushort)x) (also char)."""
    return x & 0xFFFF


def i32(x):
    """unchecked((int)x)."""
    return ((x + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def u32(x):
    """unchecked((uint)x)."""
    return x & 0xFFFFFFFF


def i64(x):
    """unchecked((long)x)."""
    return ((x + 0x8000000000000000) & 0xFFFFFFFFFFFFFFFF) - 0x8000000000000000


def u64(x):
    """unchecked((ulong)x)."""
    return x & 0xFFFFFFFFFFFFFFFF


# ---- double -> integer, as .NET 8 on x86-64 computes unchecked casts (cvttsd2si: NaN and out of range give the
# "integer indefinite" value, the most negative integer). They accept ints too.

def f2i32(x):
    """C# (int)x of a double."""
    if -2147483649.0 < x < 2147483648.0:
        return int(x)
    return -2147483648


def f2i64(x):
    """C# (long)x of a double."""
    if -9223372036854775808.0 <= x < 9223372036854775808.0:
        return int(x)
    return -9223372036854775808


def f2u32(x):
    """C# (uint)x of a double: the 64-bit conversion, truncated."""
    return f2i64(x) & 0xFFFFFFFF


def f2u64(x):
    """C# (ulong)x of a double: below 2^63 the signed conversion, above it that of x - 2^63 with the top bit set."""
    if x < 9223372036854775808.0:
        return f2i64(x) & 0xFFFFFFFFFFFFFFFF
    return (f2i64(x - 9223372036854775808.0) & 0xFFFFFFFFFFFFFFFF) ^ 0x8000000000000000


def f2i16(x):
    """C# (short)x of a double: the 32-bit conversion, truncated."""
    return ((f2i32(x) + 0x8000) & 0xFFFF) - 0x8000


def f2u16(x):
    """C# (ushort)x / (char)x of a double."""
    return f2i32(x) & 0xFFFF


def f2i8(x):
    """C# (sbyte)x of a double."""
    return ((f2i32(x) + 0x80) & 0xFF) - 0x80


def f2u8(x):
    """C# (byte)x of a double."""
    return f2i32(x) & 0xFF


# Cpp.Conv (cs/runtime/Misc.cs): GCC's double -> integer conversions on x86-64.
def Conv_D2I(x):
    """Cpp.Conv.D2I: (int)x, int.MinValue for NaN and out of range."""
    if -2147483649.0 < x < 2147483648.0:
        return int(x)
    return -2147483648


def Conv_D2L(x):
    """Cpp.Conv.D2L: (long)x, long.MinValue for NaN and out of range."""
    if -9223372036854775808.0 <= x < 9223372036854775808.0:
        return int(x)
    return -9223372036854775808


def Conv_D2U(x):
    """Cpp.Conv.D2U: GCC's double -> unsigned int, the 64-bit conversion truncated."""
    if -9223372036854775808.0 <= x < 9223372036854775808.0:
        return int(x) & 0xFFFFFFFF
    return 0


def Conv_D2UL(x):
    """Cpp.Conv.D2UL: GCC's double -> unsigned long: below 2^63 the signed conversion, above it the conversion of
    x - 2^63 with the top bit flipped (NaN gives 0)."""
    if x < 9223372036854775808.0:
        if -9223372036854775808.0 <= x:
            return int(x) & 0xFFFFFFFFFFFFFFFF
        return 0x8000000000000000
    return (Conv_D2L(x - 9223372036854775808.0) & 0xFFFFFFFFFFFFFFFF) ^ 0x8000000000000000


def Conv_D2S(x):
    """Cpp.Conv.D2S: (short)D2I(x)."""
    return ((Conv_D2I(x) + 0x8000) & 0xFFFF) - 0x8000


def Conv_D2US(x):
    """Cpp.Conv.D2US: (ushort)D2I(x)."""
    return Conv_D2I(x) & 0xFFFF


def Conv_D2SB(x):
    """Cpp.Conv.D2SB: (sbyte)D2I(x)."""
    return ((Conv_D2I(x) + 0x80) & 0xFF) - 0x80


def Conv_D2B(x):
    """Cpp.Conv.D2B: (byte)D2I(x)."""
    return Conv_D2I(x) & 0xFF


# Cpp.R.D2I / D2L (cs/runtime/CRuntime.cs): the same conversions, written the other way round.
def R_D2I(x):
    """Cpp.R.D2I: int.MinValue for NaN, x >= 2^31 or x <= -2^31 - 1, else (int)x."""
    if x != x or x >= 2147483648.0 or x <= -2147483649.0:
        return -2147483648
    return int(x)


def R_D2L(x):
    """Cpp.R.D2L: long.MinValue for NaN, x >= 2^63 or x < -2^63, else (long)x."""
    if x != x or x >= 9223372036854775808.0 or x < -9223372036854775808.0:
        return -9223372036854775808
    return int(x)


# ---------------------------------------------------------------------------------------------------- typed boxes
# A params object[] argument whose C# type is not int, double, bool or a reference arrives wrapped (Python ints
# have no width): the formatter treats each as the C# runtime treats that .NET type. .v is the value.

class _Box:
    __slots__ = ('v',)

    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return '%s(%r)' % (type(self).__name__, self.v)


class I64(_Box):
    """A boxed C# long."""
    __slots__ = ()


class U32(_Box):
    """A boxed C# uint."""
    __slots__ = ()


class U64(_Box):
    """A boxed C# ulong."""
    __slots__ = ()


class I16(_Box):
    """A boxed C# short."""
    __slots__ = ()


class U16(_Box):
    """A boxed C# ushort."""
    __slots__ = ()


class I8(_Box):
    """A boxed C# sbyte."""
    __slots__ = ()


class U8(_Box):
    """A boxed C# byte."""
    __slots__ = ()


class Ch(_Box):
    """A boxed C# char (.v is its UTF-16 code unit as an int)."""
    __slots__ = ()


class F32(_Box):
    """A boxed C# float (.v already single-rounded)."""
    __slots__ = ()


# ---------------------------------------------------------------------------------------------------- references
# A Ref is any object with a readable and writable attribute v (RUNTIME_HELPERS.md section 3). The engine's own
# field references (FR_<Class>_<field>) are classes the module defines; these are the runtime's.

class Ref:
    """Base of the runtime's references (for documentation and isinstance; any object with .v is a Ref)."""
    __slots__ = ()


class Cell(Ref):
    """A variable whose address is taken: one slot v."""
    __slots__ = ('v',)

    def __init__(self, v=None):
        self.v = v

    def __repr__(self):
        return 'Cell(%r)' % (self.v,)


class ArrRef(Ref):
    """A reference to element i of a Python list (or bytearray): C#'s ref a[i]. For a Vec element it is made on the
    backing list (ArrRef(vec.a, i)), so it keeps referring to that storage, as C#'s ref does."""
    __slots__ = ('l', 'i')

    def __init__(self, l, i):
        self.l = l
        self.i = i

    def _get(self):
        return self.l[self.i]

    def _set(self, x):
        self.l[self.i] = x

    v = property(_get, _set)


class AttrRef(Ref):
    """A reference to attribute name of object o (a field reference made at run time)."""
    __slots__ = ('o', 'name')

    def __init__(self, o, name):
        self.o = o
        self.name = name

    def _get(self):
        return getattr(self.o, self.name)

    def _set(self, x):
        setattr(self.o, self.name, x)

    v = property(_get, _set)


class FnRef(Ref):
    """A reference made of a getter (no arguments) and a setter (one argument); either may be None."""
    __slots__ = ('g', 's')

    def __init__(self, g, s):
        self.g = g
        self.s = s

    def _get(self):
        return self.g()

    def _set(self, x):
        self.s(x)

    v = property(_get, _set)


class ByteRef(Ref):
    """C#'s CPtr element as a reference (typed as the indexer, int): reads sign-extend the byte (x86 C's char),
    writes store the low 8 bits. The buffer may be a bytearray (0..255) or a list of byte or sbyte values."""
    __slots__ = ('b', 'k')

    def __init__(self, b, k):
        self.b = b
        self.k = k

    def _get(self):
        x = self.b[self.k]
        return x - 256 if x > 127 else x

    def _set(self, x):
        self.b[self.k] = x & 0xFF

    v = property(_get, _set)


# ---------------------------------------------------------------------------------------------------- interfaces
# Engine classes list the runtime interfaces they implement as bases; the runtime's own classes derive from them
# too, so isinstance decides what C# decides from the static type (Elem<T>.IsClass, Dtor.Run's cases).

class ICopy:
    """Cpp.ICopy<T>: Clone() is the copy constructor, Assign(o) the copy assignment."""
    __slots__ = ()


class IDisposable:
    """System.IDisposable: Dispose()."""
    __slots__ = ()


class IDestroyElems:
    """Cpp.IDestroyElems: DestroyElems() runs the destructors of the elements a container holds."""
    __slots__ = ()


# ---------------------------------------------------------------------------------------------------- elements

def Elem_Deep(x):
    """Cpp.ArrCopy.Deep: a C array copied with its object (T a[n][m], an array of classes): every row and element.
    Python lists carry no element type, so each element is copied as Elem_Copy would copy it: a list deeply, an
    ICopy with Clone, anything else (a number, a str, a runtime struct) shared."""
    return [e.Clone() if isinstance(e, ICopy) else (Elem_Deep(e) if type(e) is list else e) for e in x]


def Elem_Copy(x):
    """Cpp.Elem<T>.Copy: C++ copy construction of an element: an ICopy instance is cloned, an array (a list) copied
    deeply, anything else (numbers, strings, runtime structs, None) is itself."""
    if isinstance(x, ICopy):
        return x.Clone()
    if type(x) is list:
        return Elem_Deep(x)
    return x


def Elem_NeedsCopy(x):
    """Cpp.Elem<T>.NeedsCopy for one value: copying it means more than sharing the reference."""
    return isinstance(x, ICopy) or type(x) is list


def deq(a, b):
    """double.Equals: == except that NaN equals NaN (0.0 equals -0.0, as == has it)."""
    return a == b or (a != a and b != b)


def Elem_Eq(a, b):
    """EqualityComparer<T>.Default.Equals: numbers by value (a double NaN equals NaN), strings and Str by content,
    runtime structs by their Equals, an object with an Equals method by it, other objects by identity."""
    if a is b:
        return True
    if a is None or b is None:
        return False
    t = type(a)
    if t is float:
        return a == b or (a != a and b != b)
    if t is int or t is bool or t is str:
        return a == b
    f = getattr(a, 'Equals', None)
    if f is not None:
        return f(b)
    return False


def Elem_Compare(a, b):
    """Comparer<T>.Default.Compare(a, b) as -1, 0 or 1: null before non-null, double by double.CompareTo (NaN
    before every number, NaN equal to NaN, -0.0 equal to 0.0), bool false before true, numbers and str by value
    (a C# string compares ordinally here: the engine's keys are ASCII), Str by char_traits (unsigned bytes, then
    length), an object with a CompareTo method by it."""
    if a is None:
        return 0 if b is None else -1
    if b is None:
        return 1
    t = type(a)
    if t is float:
        if a < b:
            return -1
        if a > b:
            return 1
        if a == b:
            return 0
        # at least one NaN
        if a != a:
            return 0 if b != b else -1
        return 1
    if t is int or t is bool or t is str:
        return -1 if a < b else (1 if a > b else 0)
    f = getattr(a, 'CompareTo', None)
    if f is None:
        raise TypeError('cpp.Elem_Compare: no ordering for ' + t.__name__)
    c = f(b)
    return -1 if c < 0 else (1 if c > 0 else 0)


def Elem_Less(a, b):
    """std::less as the C# runtime's Less<T>.Fn computes it: Str by compare, otherwise
    Comparer<T>.Default.Compare(a, b) < 0 (see Elem_Compare)."""
    t = type(a)
    if t is float:
        return a < b or (a != a and b == b)
    if t is int or t is bool:
        return a < b
    return Elem_Compare(a, b) < 0


# ---- element makers (RUNTIME_HELPERS.md section 4): zero-argument callables returning a new default element.

def MK_I():
    """Maker of an integer (or enum, char) element: 0."""
    return 0


def MK_D():
    """Maker of a double (or float) element: 0.0."""
    return 0.0


def MK_Z():
    """Maker of a bool element: False."""
    return False


def MK_N():
    """Maker of a reference with no default object (an array, an interface, a delegate): None. It stands for
    C#'s Elem<T>.Make giving null."""
    return None


# Makers whose result is immutable, so a container may fill with [mk()] * n. The struct makers (MK_CPtr, MK_PtrD,
# ...) are added by the modules that define them.
IMMUTABLE_MAKERS = {MK_I, MK_D, MK_Z, MK_N}


# ---------------------------------------------------------------------------------------------------- C# text

def _net_double(x):
    """.NET Core's double.ToString() (the shortest round-trip digits in the "G" layout, nMaxDigits =
    max(digits, 17)): scientific when the decimal exponent is 17 or more or below -4 ("1E+17", "1E-05"; 1e16 is
    "10000000000000000"), "-0" for -0.0, "NaN" for every NaN, and the invariant culture's "Infinity" (checked
    against .NET 8 by tests/pycheck)."""
    if x != x:
        return 'NaN'
    if x == INF:
        return _NET_INF
    if x == -INF:
        return '-' + _NET_INF
    if x == 0.0:
        return '-0' if signbit(x) else '0'
    r = repr(x)
    neg = r[0] == '-'
    if neg:
        r = r[1:]
    mant, _, ex = r.partition('e')
    ip, _, fp = mant.partition('.')
    if fp == '0':
        fp = ''
    digs = ip + fp
    scale = int(ex or '0') + len(ip)            # the value is 0.digs * 10^scale
    k = len(digs) - len(digs.lstrip('0'))
    digs = digs[k:].rstrip('0')
    scale -= k
    if scale > 17 or scale < -3:
        e = scale - 1
        t = digs[0] + ('.' + digs[1:] if len(digs) > 1 else '') + ('E+' if e >= 0 else 'E-') + '%02d' % abs(e)
    elif scale <= 0:
        t = '0.' + '0' * -scale + digs
    elif scale >= len(digs):
        t = digs + '0' * (scale - len(digs))
    else:
        t = digs[:scale] + '.' + digs[scale:]
    return '-' + t if neg else t


_NET_INF = 'Infinity'   # NumberFormatInfo.InvariantInfo.PositiveInfinitySymbol of .NET 8 (not '\u221e')


def cs_tostring(v):
    """C#'s ToString() / string concatenation of a value: an int in decimal, True/False, a str itself, None as '',
    a double as .NET Core writes it (see _net_double: not quite what RUNTIME_HELPERS.md section 7 says), another
    object by its ToString()."""
    t = type(v)
    if t is str:
        return v
    if t is bool:
        return 'True' if v else 'False'
    if t is int:
        return str(v)
    if t is float:
        return _net_double(v)
    if v is None:
        return ''
    return v.ToString()


def cs_equals(a, b):
    """object.Equals on a runtime value (as EqualityComparer<T>.Default.Equals: see Elem_Eq; a boxed int and a
    boxed long are not told apart, as Python ints carry no width)."""
    return Elem_Eq(a, b)


# ---------------------------------------------------------------------------------------------------- control flow

class GotoSignal(Exception):
    """Raised for a goto that leaves a loop inside a dispatch loop (attribute k: the label); the engine has none."""

    def __init__(self, k):
        Exception.__init__(self, k)
        self.k = k


def throw_(e):
    """A C# throw expression."""
    raise e
