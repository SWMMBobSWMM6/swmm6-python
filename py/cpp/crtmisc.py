# crtmisc.py - part C of the Python runtime (package cpp): the rest of cs/runtime/Misc.cs that is not part A's
# (Mv, Arr, M, Conv, Dtor, Defer, Box) or part B's (Algo): atomics (Atomic<T> as AtomicD / AtomicF / AtomicI /
# AtomicL / AtomicZ), std::variant (Variant, from Std.cs), std::tuple of more elements (TupN, with Ident), chrono
# (TimePoint, Duration), the one-thread stand-ins Mutex / CondVar / Thread / Lock / OnceFlag, Std (call_once,
# rethrow, get<i>, swap, from_chars, QNaN), ErrorCode, FromCharsResult, Dbg (exception trace when SWMM6_CS_TRACE is
# set), Sys (hardware_concurrency), SysException (a .NET exception), and raw memory on typed arrays (Mem, Bytes).
#
# Typed arrays are Python lists without a run-time element type, so the Mem / Bytes functions take it from their
# specialisation suffix (D double, F float, I int/uint, L long/ulong, S short, B byte/sbyte, Z bool) and see the
# list as its little-endian bytes (x86's layout, C#'s MemoryMarshal.AsBytes). A Python int has no signedness: bytes
# read into an I or L list are decoded signed (int, long), into a B list unsigned (byte), except where the engine's
# only uses are unsigned (memcpy__IB is the hot-start file's uint CRC, memcpy__LD the ulong bits of a double).
# MemPtr and RefGet are delegates: Python callables, nothing to define.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

import os
import struct
import sys
import time as _time
import traceback

def _find_icopy():
    """Part A's interface class ICopy (the containers copy an ICopy element with Clone()): inheriting from a class
    of a module the package imported earlier is allowed while the package imports. It is looked up in the package
    and its loaded modules, so this does not depend on how __init__ binds the names."""
    for name, m in list(sys.modules.items()):
        if (name == 'cpp' or name.startswith('cpp.')) and m is not None:
            c = getattr(m, 'ICopy', None)
            if isinstance(c, type):
                return c
    return None


_ICopy = _find_icopy()
if _ICopy is None:      # this module imported on its own (unit tests): a stand-in
    class _ICopy:
        __slots__ = ()

__all__ = [
    'AtomicD', 'AtomicF', 'AtomicI', 'AtomicL', 'AtomicZ', 'NewAtomicD', 'NewAtomicD_T', 'NewAtomicF', 'NewAtomicF_T',
    'NewAtomicI', 'NewAtomicI_T', 'NewAtomicL', 'NewAtomicL_T', 'NewAtomicZ', 'NewAtomicZ_T',
    'Variant', 'NewVariant', 'NewVariant_b', 'NewVariant_i', 'NewVariant_d', 'NewVariant_Str',
    'TupN', 'NewTupN', 'NewTupN_oA', 'Ident_Of',
    'TimePoint', 'TimePoint_ZERO', 'TimePoint_now', 'TimePoint_op_Sub', 'Duration', 'Duration_ZERO', 'NewDuration_l',
    'NewDuration_Duration', 'Duration_op_Add', 'Duration_op_Sub',
    'Mutex', 'CondVar', 'Thread', 'Lock', 'OnceFlag', 'NewMutex', 'NewCondVar', 'NewThread', 'NewLock', 'NewOnceFlag',
    'Std_Rethrow', 'Std_call_once', 'Std_Nop', 'Std_QNaN', 'Std_SNaN', 'Std_QNaNf', 'Std_iabs_i', 'Std_iabs_l',
    'Std_get0_PairAB', 'Std_get1_PairAB', 'Std_swap', 'Std_exchange', 'Std_release',
    'Std_from_chars_CPtr_CPtr_dr', 'Std_from_chars_CPtr_CPtr_ir', 'Std_from_chars_CPtr_CPtr_lr',
    'Std_from_chars_CPtr_CPtr_ur', 'Std_from_chars_CPtr_CPtr_ulr',
    'FromCharsResult', 'ErrorCode', 'NewErrorCode', 'Dbg_Caught', 'Sys_hardware_concurrency', 'SysException',
    'Mem_Calloc__D', 'Mem_Calloc__F', 'Mem_Calloc__I', 'Mem_Calloc__L', 'Mem_Calloc__B', 'Mem_Diff_T_PtrT',
    'Mem_Diff_PtrT_T', 'Mem_Free', 'Mem_fread_CPtr_ul_ul_CFile', 'Mem_fwrite_CPtr_ul_ul_CFile',
    'Mem_memcpy_CPtr_CPtr_ul', 'Mem_memset_CPtr_i_ul', 'Bytes_View_CPtr',
]

_d = struct.Struct('<d')
_q = struct.Struct('<q')
_Q = struct.Struct('<Q')


def _b2d(b):
    return _d.unpack(_Q.pack(b))[0]


# ------------------------------------------------------------------------------------------------ atomics

_M64 = 0xFFFFFFFFFFFFFFFF


def _wrap64(r):
    """fetch_add's result for a long or ulong atomic: wrapped only outside both ranges together (a Python int does
    not say which of the two the atomic is), so that a counter of either type wraps as C# wraps it."""
    if r > _M64:
        return r & _M64
    if r < -0x8000000000000000:
        return ((r + 0x8000000000000000) & _M64) - 0x8000000000000000
    return r


def _wrap32(r):
    if r > 0xFFFFFFFF:
        return r & 0xFFFFFFFF
    if r < -0x80000000:
        return ((r + 0x80000000) & 0xFFFFFFFF) - 0x80000000
    return r


class _Atomic(_ICopy):
    """std::atomic<T> on one thread (C# Cpp.Atomic<T>): v is the value (a field, and so the atomic is a Ref)."""
    __slots__ = ('v',)

    def exchange(self, x):
        o = self.v
        self.v = x
        return o

    def load(self, order=0):
        return self.v

    def store(self, x, order=0):
        self.v = x

    def compare_exchange(self, expected, desired):
        """compare_exchange_weak / _strong on one thread: never fails spuriously; expected is a Ref."""
        v = self.v
        e = expected.v
        if v == e or (v != v and e != e):            # EqualityComparer<double>: NaN equals NaN
            self.v = desired
            return True
        expected.v = v
        return False

    def Clone(self):
        c = type(self)()
        c.v = self.v
        return c

    def Assign(self, o):
        self.v = o.v

    def ref_v(self):
        return self

    def ToString(self):
        return str(self.v)


class AtomicD(_Atomic):
    __slots__ = ()

    def __init__(self, x=0.0):
        self.v = x

    def fetch_add(self, d, order=0):
        o = self.v
        self.v = o + d
        return o

    def fetch_sub(self, d, order=0):
        o = self.v
        self.v = o - d
        return o


class AtomicF(_Atomic):
    __slots__ = ()

    def __init__(self, x=0.0):
        self.v = x

    def fetch_add(self, d, order=0):
        o = self.v
        self.v = struct.unpack('<f', struct.pack('<f', o + d))[0]
        return o

    def fetch_sub(self, d, order=0):
        o = self.v
        self.v = struct.unpack('<f', struct.pack('<f', o - d))[0]
        return o


class AtomicI(_Atomic):
    """std::atomic<int> and std::atomic<unsigned> (the specialisation I)."""
    __slots__ = ()

    def __init__(self, x=0):
        self.v = x

    def fetch_add(self, d, order=0):
        o = self.v
        self.v = _wrap32(o + d)
        return o

    def fetch_sub(self, d, order=0):
        o = self.v
        self.v = _wrap32(o - d)
        return o


class AtomicL(_Atomic):
    """std::atomic<long> and std::atomic<unsigned long> (the specialisation L)."""
    __slots__ = ()

    def __init__(self, x=0):
        self.v = x

    def fetch_add(self, d, order=0):
        o = self.v
        self.v = _wrap64(o + d)
        return o

    def fetch_sub(self, d, order=0):
        o = self.v
        self.v = _wrap64(o - d)
        return o


class AtomicZ(_Atomic):
    __slots__ = ()

    def __init__(self, x=False):
        self.v = x


def NewAtomicD():
    return AtomicD(0.0)


def NewAtomicD_T(x):
    return AtomicD(x)


def NewAtomicF():
    return AtomicF(0.0)


def NewAtomicF_T(x):
    return AtomicF(x)


def NewAtomicI():
    return AtomicI(0)


def NewAtomicI_T(x):
    return AtomicI(x)


def NewAtomicL():
    return AtomicL(0)


def NewAtomicL_T(x):
    return AtomicL(x)


def NewAtomicZ():
    return AtomicZ(False)


def NewAtomicZ_T(x):
    return AtomicZ(x)


# ------------------------------------------------------------------------------------------------ std::variant

def _bad_variant():
    """std::bad_variant_access: part A's BadVariantAccess (a CppException)."""
    return sys.modules['cpp'].NewBadVariantAccess()


class Variant(_ICopy):
    """std::variant<bool, int, double, std::string> (the engine's user-flag value): the alternative held (v) and
    its index in the declared list (idx)."""
    __slots__ = ('v', 'idx')

    def __init__(self):
        self.v = False
        self.idx = 0

    def Set_b(self, x):
        self.v = x
        self.idx = 0

    def Set_i(self, x):
        self.v = x
        self.idx = 1

    def Set_d(self, x):
        self.v = x
        self.idx = 2

    def Set_Str(self, x):
        self.v = x.Clone()
        self.idx = 3

    def index(self):
        return self.idx

    def Holds(self, idx):
        return self.idx == idx

    def Get(self, idx):
        if self.idx != idx:
            raise _bad_variant()
        return self.v

    def Clone(self):
        c = Variant()
        c.Assign(self)
        return c

    def Assign(self, o):
        self.idx = o.idx
        v = o.v
        self.v = v.Clone() if o.idx == 3 else v

    def MoveFrom(self, o):
        self.idx = o.idx
        self.v = o.v


def NewVariant():
    return Variant()


def NewVariant_b(x):
    r = Variant()
    r.Set_b(x)
    return r


def NewVariant_i(x):
    r = Variant()
    r.Set_i(x)
    return r


def NewVariant_d(x):
    r = Variant()
    r.Set_d(x)
    return r


def NewVariant_Str(x):
    r = Variant()
    r.Set_Str(x)
    return r


# ------------------------------------------------------------------------------------------------ tuples

_ids = {}           # id(object) -> (object, number): the object is kept so its id is not reused


def Ident_Of(o):
    """A unique number per object for this run (C# Cpp.Ident.Of): orders pointers where only identity matters."""
    if o is None:
        return 0
    e = _ids.get(id(o))
    if e is None or e[0] is not o:
        e = (o, len(_ids) + 1)
        _ids[id(o)] = e
    return e[1]


def _tcmp(a, b):
    """One element of std::tuple's operator< (C# TupN.Cmp)."""
    if a is None:
        return 0 if b is None else -1
    t = type(a)
    if t is float or t is int or t is bool:
        return -1 if a < b else (1 if b < a else 0)
    if hasattr(a, 'v') and type(a).__name__ in ('I64', 'U64', 'U32', 'I16', 'U16', 'I8', 'U8', 'Ch', 'F32'):
        x, y = a.v, b.v
        return -1 if x < y else (1 if y < x else 0)
    if t.__name__.startswith('Ptr') and hasattr(a, 'a') and hasattr(a, 'o'):     # Ptr<double>: identity, offset
        x, y = Ident_Of(a.a), Ident_Of(b.a)
        if x != y:
            return -1 if x < y else 1
        return -1 if a.o < b.o else (1 if b.o < a.o else 0)
    c = getattr(a, 'CompareTo', None)
    if c is not None:
        return c(b)
    from cpp import SysException as _SE
    raise _SE('NotSupportedException', 'tuple element ' + t.__name__)


class TupN(_ICopy):
    """std::tuple of more than three elements (a map key compared element by element, as std::tuple's operator<
    does); v is the list of elements."""
    __slots__ = ('v',)

    def __init__(self, *items):
        self.v = list(items)

    def Clone(self):
        c = TupN()
        c.v = list(self.v)
        return c

    def Assign(self, o):
        self.v = list(o.v)

    def MoveFrom(self, o):
        self.v = o.v

    def CompareTo(self, o):
        for x, y in zip(self.v, o.v):
            c = _tcmp(x, y)
            if c:
                return c
        return 0

    def Equals(self, o):
        return o is not None and self.CompareTo(o) == 0

    def GetHashCode(self):
        return len(self.v)

    def __lt__(self, o):
        return self.CompareTo(o) < 0

    def __gt__(self, o):
        return self.CompareTo(o) > 0

    def __le__(self, o):
        return self.CompareTo(o) <= 0

    def __ge__(self, o):
        return self.CompareTo(o) >= 0

    def __eq__(self, o):
        return isinstance(o, TupN) and self.CompareTo(o) == 0

    def __ne__(self, o):
        return not self.__eq__(o)

    def __hash__(self):
        return len(self.v)


def NewTupN():
    return TupN()


def NewTupN_oA(*items):
    return TupN(*items)


# ------------------------------------------------------------------------------------------------ chrono

class TimePoint:
    """A std::chrono time point (an immutable struct): ns, nanoseconds of a monotonic clock. Used only for timing
    (profiling counters, the run's elapsed time); none of it reaches the results."""
    __slots__ = ('ns',)

    def __init__(self, ns=0):
        self.ns = ns

    def Clone(self):
        return self

    def time_since_epoch(self):
        return Duration(self.ns)


class Duration:
    """A duration in nanoseconds (an immutable struct); count() gives seconds (the engine counts durations as
    std::chrono::duration<double>)."""
    __slots__ = ('ns',)

    def __init__(self, ns=0):
        self.ns = ns

    def Clone(self):
        return self

    def count(self):
        return self.ns * 1e-9


TimePoint_ZERO = TimePoint(0)
Duration_ZERO = Duration(0)


def TimePoint_now():
    return TimePoint(_time.perf_counter_ns())


def TimePoint_op_Sub(a, b):
    return Duration(a.ns - b.ns)


def NewDuration_l(n):
    return Duration(n)


def NewDuration_Duration(d):
    return Duration(d.ns)


def Duration_op_Add(a, b):
    return Duration(a.ns + b.ns)


def Duration_op_Sub(a, b):
    return Duration(a.ns - b.ns)


# ------------------------------------------------------------------------------------------------ one thread

class Mutex:
    """The ports run single-threaded (the engine's one worker thread, the output writer, is replaced by a
    synchronous call): these never block."""
    __slots__ = ()

    def lock(self):
        pass

    def unlock(self):
        pass

    def try_lock(self):
        return True


class CondVar:
    __slots__ = ()

    def notify_one(self):
        pass

    def notify_all(self):
        pass


class Thread:
    __slots__ = ()

    def joinable(self):
        return False

    def join(self):
        pass


class Lock:
    __slots__ = ()


class OnceFlag:
    """std::once_flag: whether call_once has run its function."""
    __slots__ = ('done',)

    def __init__(self):
        self.done = False


def NewMutex():
    return Mutex()


def NewCondVar():
    return CondVar()


def NewThread():
    return Thread()


def NewLock():
    return Lock()


def NewOnceFlag():
    return OnceFlag()


# ------------------------------------------------------------------------------------------------ Std

Std_QNaN = _b2d(0x7FF8000000000000)     # C++'s quiet NaN
Std_SNaN = _b2d(0x7FF4000000000000)
Std_QNaNf = struct.unpack('<f', struct.pack('<I', 0x7FC00000))[0]


def Std_Rethrow(e):
    """std::rethrow_exception: raise the stored exception again (its traceback kept)."""
    raise e


def Std_call_once(flag, f):
    """std::call_once: run f the first time only (single-threaded; an exception leaves the flag unset)."""
    if flag.done:
        return
    f()
    flag.done = True


def Std_Nop(x):
    return 0


def Std_iabs_i(x):
    return -x if x < 0 else x


Std_iabs_l = Std_iabs_i


def Std_get0_PairAB(p):
    """std::get<0> of a pair (the value; the suffixes are the pair's type arguments)."""
    return p.first


def Std_get1_PairAB(p):
    return p.second


for _sfx in ('ID', 'II', 'IO', 'LO', 'OD', 'OI', 'OL', 'OS', 'OZ', 'DD', 'DI', 'LL', 'LD', 'IL', 'OO', 'ZO', 'BO',
             'OB', 'IZ', 'IB', 'LI', 'DO', 'IF', 'OF'):
    globals()['Std_get0_PairAB__' + _sfx] = Std_get0_PairAB
    globals()['Std_get1_PairAB__' + _sfx] = Std_get1_PairAB
    __all__ += ['Std_get0_PairAB__' + _sfx, 'Std_get1_PairAB__' + _sfx]


def Std_swap(a, b):
    """std::swap through two Refs."""
    t = a.v
    a.v = b.v
    b.v = t


for _sfx in ('D', 'F', 'I', 'L', 'B', 'S', 'C', 'Z'):
    globals()['Std_swap__' + _sfx] = Std_swap
    __all__.append('Std_swap__' + _sfx)


def Std_exchange(a, v):
    old = a.v
    a.v = v
    return old


def Std_release(a):
    r = a.v
    a.v = None
    return r


class FromCharsResult:
    """std::from_chars_result (an immutable struct): ptr, the CPtr after the characters read, and ec, the errc
    (0, 22 invalid_argument, 34 result_out_of_range)."""
    __slots__ = ('ptr', 'ec')

    def __init__(self, ptr=None, ec=0):
        self.ptr = ptr
        self.ec = ec

    def Clone(self):
        return self


def _cbytes(first, last):
    """The bytes of [first, last) of one buffer (a bytearray or a list of byte / sbyte values)."""
    B = first.B
    if type(B) is bytearray or type(B) is bytes:
        return bytes(B[first.O:last.O])
    return bytes(x & 255 for x in B[first.O:last.O])


def Std_from_chars_CPtr_CPtr_dr(first, last, v):
    """std::from_chars for double (chars_format::general), as libstdc++ has it: the value strtod reads from the same
    characters (both are correctly rounded), except that a sign other than '-', leading white space and a
    hexadecimal prefix are not read ("0x10" is 0 and stops at the x), and a value too large or too small for a
    double is result_out_of_range (34) with the value left unchanged. The C# runtime does not make the last two
    distinctions (it would read "0x10" as hexadecimal and store an overflowed or underflowed value); libstdc++ is
    followed, as in the Go and Java ports."""
    from cpp import R_ScanDouble, CPtr
    t = _cbytes(first, last)
    k = 1 if t[:1] == b'-' else 0
    c = t[k] if k < len(t) else 0
    lc = c | 0x20 if 65 <= c <= 90 else c
    if not (48 <= c <= 57 or c == 46 or lc == 105 or lc == 110):
        return FromCharsResult(first, 22)
    if c == 48 and k + 1 < len(t) and t[k + 1] in b'xX':
        t = t[:k + 1]                                   # read the 0 alone
    r, e, _ = R_ScanDouble(t, 0, False)
    if e <= 0:
        return FromCharsResult(first, 22)
    ptr = CPtr(first.B, first.O + e)
    if r - r != 0.0 and r == r and lc != 105:            # overflow
        return FromCharsResult(ptr, 34)
    if r == 0.0:                                         # underflow: a nonzero digit before the exponent
        for j in range(k, e):
            d = t[j]
            if d == 101 or d == 69:
                break
            if 49 <= d <= 57:
                return FromCharsResult(ptr, 34)
    v.v = r
    return FromCharsResult(ptr, 0)


def _int_chars(first, last, signed, lo, hi, v):
    """std::from_chars for integers, base 10: an optional '-' (signed types only), digits; an empty match is
    invalid_argument (22) with ptr = first; a value out of the type's range is result_out_of_range (34), all the
    digits consumed, the value left unchanged."""
    from cpp import CPtr
    t = _cbytes(first, last)
    i = 0
    neg = False
    if signed and t[:1] == b'-':
        neg = True
        i = 1
    j = i
    n = len(t)
    while j < n and 48 <= t[j] <= 57:
        j += 1
    if j == i:
        return FromCharsResult(first, 22)
    r = int(t[i:j])
    if neg:
        r = -r
    ptr = CPtr(first.B, first.O + j)
    if not lo <= r <= hi:
        return FromCharsResult(ptr, 34)
    v.v = r
    return FromCharsResult(ptr, 0)


def Std_from_chars_CPtr_CPtr_ir(first, last, v):
    return _int_chars(first, last, True, -2147483648, 2147483647, v)


def Std_from_chars_CPtr_CPtr_lr(first, last, v):
    return _int_chars(first, last, True, -9223372036854775808, 9223372036854775807, v)


def Std_from_chars_CPtr_CPtr_ur(first, last, v):
    return _int_chars(first, last, False, 0, 0xFFFFFFFF, v)


def Std_from_chars_CPtr_CPtr_ulr(first, last, v):
    return _int_chars(first, last, False, 0, 0xFFFFFFFFFFFFFFFF, v)


class ErrorCode:
    """std::error_code: v, the errno value (a class: shared, not copied)."""
    __slots__ = ('v',)

    def __init__(self):
        self.v = 0

    def Bool(self):
        return self.v != 0

    def value(self):
        return self.v

    def clear(self):
        self.v = 0

    _TEXT = {0: 'Success', 2: 'No such file or directory', 13: 'Permission denied', 17: 'File exists',
             20: 'Not a directory', 21: 'Is a directory', 95: 'Operation not supported'}

    def message(self):
        """glibc's strerror text for the errno values the filesystem calls set (a Str)."""
        from cpp import NewStr_CPtr, R_Lit
        return NewStr_CPtr(R_Lit(self._TEXT.get(self.v, 'Unknown error %d' % self.v)))


def NewErrorCode():
    return ErrorCode()


# ------------------------------------------------------------------------------------------------ Dbg, Sys, exceptions

_trace = os.environ.get('SWMM6_CS_TRACE') is not None


def Dbg_Caught(e):
    """With the environment variable SWMM6_CS_TRACE set, each exception a translated catch handler catches is
    printed to stderr (the engine itself turns most of them into error codes)."""
    if not _trace:
        return
    try:
        text = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
    except Exception:
        text = repr(e) + '\n'
    sys.stderr.write('[caught] ' + text)
    sys.stderr.flush()


def Sys_hardware_concurrency():
    """std::thread::hardware_concurrency: libstdc++ on Linux asks glibc's get_nprocs, the processors online (glibc's
    sysconf(_SC_NPROCESSORS_ONLN) is get_nprocs), not the affinity limit; 0 when unknown. The engine prints it in
    THREADS warnings."""
    try:
        n = os.sysconf('SC_NPROCESSORS_ONLN')
        return n if n > 0 else 0
    except (ValueError, OSError, AttributeError):
        return os.cpu_count() or 0


class SysException(Exception):
    """A .NET exception the C# engine throws itself (new XxxException(msg)): kind, the C# class name
    ('InvalidOperationException', 'NotImplementedException', 'OverflowException', ...), and the message. It is not
    a CppException; `except Exception` (C#'s catch (System.Exception)) takes it."""

    def __init__(self, kind='Exception', message=''):
        Exception.__init__(self, message)
        self.kind = kind
        self.Message = message

    def __str__(self):
        return self.Message

    def ToString(self):
        return 'System.%s: %s' % (self.kind, self.Message)


# ------------------------------------------------------------------------------------------------ Mem, Bytes

# element codes: (size, struct code to read, struct code to write (masked), mask)
_EL = {
    'D': (8, 'd', 'd', None), 'F': (4, 'f', 'f', None), 'I': (4, 'i', 'I', 0xFFFFFFFF),
    'U': (4, 'I', 'I', 0xFFFFFFFF), 'L': (8, 'q', 'Q', _M64), 'UL': (8, 'Q', 'Q', _M64),
    'S': (2, 'h', 'H', 0xFFFF), 'C': (2, 'H', 'H', 0xFFFF), 'B': (1, 'B', 'B', 0xFF), 'Z': (1, '?', 'B', None),
}


_ST = {}


def _st(k, c):
    """struct.Struct('<' k c), cached."""
    st = _ST.get((k, c))
    if st is None:
        st = struct.Struct('<%d%s' % (k, c))
        if len(_ST) < 4096:
            _ST[(k, c)] = st
    return st


def _elem_bytes(code, a, o, n):
    """The first n bytes of the list a from element o, as the little-endian bytes of its elements. No bytes are no
    bytes even at a null pointer (a None list): libstdc++ passes the data() of an empty vector, which is nullptr,
    with a length of 0 (C# Mem.Bytes)."""
    if n == 0:
        return b''
    size, _, wc, mask = _EL[code]
    k = (n + size - 1) // size
    els = a[o:o + k]
    if len(els) < k:
        raise IndexError('memory read past the array')
    if code == 'Z':
        b = bytes(1 if x else 0 for x in els)
    elif mask is None:
        b = _st(k, wc).pack(*els)
    else:
        b = _st(k, wc).pack(*[x & mask for x in els])
    return b if len(b) == n else b[:n]


def _all_bytes(code, a, o):
    return _elem_bytes(code, a, o, (len(a) - o) * _EL[code][0])


def _elem_write(code, a, o, data):
    """The bytes data into the list a from element o (a partial last element keeps its other bytes)."""
    n = len(data)
    if n == 0:
        return
    size, rc, _, _ = _EL[code]
    k = (n + size - 1) // size
    if o + k > len(a):
        raise IndexError('memory write past the array')
    if n != k * size:
        data = bytes(data) + _elem_bytes(code, a, o + k - 1, size)[n - (k - 1) * size:]
    if code == 'Z':
        a[o:o + k] = [x != 0 for x in data]
    else:
        a[o:o + k] = _st(k, rc).unpack(data)


def _cp_bytes(p, n):
    """n bytes of a CPtr's buffer from its offset (none at a null pointer when n is 0)."""
    if n == 0:
        return b''
    B = p.B
    O = p.O
    if type(B) is bytearray or type(B) is bytes:
        if O + n > len(B):
            raise IndexError('memory read past the buffer')
        return bytes(B[O:O + n])
    return bytes(x & 255 for x in B[O:O + n])


def _cp_write(p, data):
    if len(data) == 0:          # no bytes, even at a null pointer
        return
    B = p.B
    O = p.O
    if O + len(data) > len(B):
        raise IndexError('memory write past the buffer')
    if type(B) is bytearray:
        B[O:O + len(data)] = data
    else:                                   # a list of ints: values 0..255, as part A's CPtr.set stores a byte
        B[O:O + len(data)] = list(data)


def _mk_ptr(code):
    def mk(n, size):
        from cpp import NewPtrD_TA_l, NewPtrF_TA_l, NewPtrI_TA_l, NewPtrL_TA_l, NewPtrB_TA_l
        el = {'D': (NewPtrD_TA_l, 0.0, 8), 'F': (NewPtrF_TA_l, 0.0, 4), 'I': (NewPtrI_TA_l, 0, 4),
              'L': (NewPtrL_TA_l, 0, 8), 'B': (NewPtrB_TA_l, 0, 1)}[code]
        return el[0]([el[1]] * (n * size // el[2]), 0)
    mk.__name__ = 'Mem_Calloc__' + code
    mk.__doc__ = ('calloc(n, size) cast to T*: n*size bytes of zeroed T. Zero bytes give an empty array, which, like '
                  "glibc's unique pointer for a zero-size request, is never dereferenced. free() is the collector's.")
    return mk


Mem_Calloc__D = _mk_ptr('D')
Mem_Calloc__F = _mk_ptr('F')
Mem_Calloc__I = _mk_ptr('I')
Mem_Calloc__L = _mk_ptr('L')
Mem_Calloc__B = _mk_ptr('B')


def Mem_Free(p):
    pass


def _index_of(a, obj):
    for i, x in enumerate(a):
        if x is obj:
            return i
    for i, x in enumerate(a):
        if x == obj:
            return i
    return -1


def Mem_Diff_T_PtrT(obj, p):
    """&obj - p: the index of an object in an array of objects (pointers to objects are references)."""
    return _index_of(p.a, obj) - p.o


def Mem_Diff_PtrT_T(p, obj):
    return p.o - _index_of(p.a, obj)


def _memcpy_from_cptr(code):
    def f(d, s, n):
        _elem_write(code, d.a, d.o, _cp_bytes(s, n))
        return d
    f.__doc__ = 'memcpy(T* d, const char* s, n): n bytes of s into the elements of d.'
    return f


def _memcpy_typed(dcode, scode):
    def f(d, s, n):
        if n:
            _elem_write(dcode, d.a, d.o, _elem_bytes(scode, s.a, s.o, n))
        return d
    f.__doc__ = 'memcpy(T* d, const U* s, n): through the bytes, so overlapping arrays behave as memmove.'
    return f


def _memcpy_to_cptr(scode):
    def f(d, s, n):
        _cp_write(d, _elem_bytes(scode, s.a, s.o, n))
        return d
    return f


for _c in ('B', 'D', 'F', 'I', 'L', 'S', 'Z', 'C'):
    globals()['Mem_memcpy_PtrT_CPtr_ul__' + _c] = _memcpy_from_cptr(_c)
    globals()['Mem_memcpy_CPtr_PtrU_ul__' + _c] = _memcpy_to_cptr(_c)
    __all__ += ['Mem_memcpy_PtrT_CPtr_ul__' + _c, 'Mem_memcpy_CPtr_PtrU_ul__' + _c]
# memcpy<T, U>: the destination's code first; the engine's only uses are unsigned: a uint CRC from a byte vector
# (IB) and the ulong bits of a double (LD)
Mem_memcpy_PtrT_PtrU_ul__IB = _memcpy_typed('U', 'B')
Mem_memcpy_PtrT_PtrU_ul__LD = _memcpy_typed('UL', 'D')
Mem_memcpy_PtrT_PtrU_ul__DL = _memcpy_typed('D', 'L')
Mem_memcpy_PtrT_PtrU_ul__BI = _memcpy_typed('B', 'I')
Mem_memcpy_PtrT_PtrU_ul__IF = _memcpy_typed('I', 'F')
Mem_memcpy_PtrT_PtrU_ul__FI = _memcpy_typed('F', 'I')
__all__ += ['Mem_memcpy_PtrT_PtrU_ul__' + s for s in ('IB', 'LD', 'DL', 'BI', 'IF', 'FI')]


def Mem_memcpy_CPtr_CPtr_ul(d, s, n):
    if n != 0:                  # memcpy(d, s, 0) reads and writes nothing, even at null pointers
        _cp_write(d, _cp_bytes(s, n))
    return d


def Mem_memset_CPtr_i_ul(d, c, n):
    _cp_write(d, bytes((c & 255,)) * n)
    return d


def _memset_typed(code):
    def f(d, c, n):
        _elem_write(code, d.a, d.o, bytes((c & 255,)) * n)
        return d
    return f


for _c in ('B', 'D', 'F', 'I', 'L', 'S', 'Z'):
    globals()['Mem_memset_PtrT_i_ul__' + _c] = _memset_typed(_c)
    __all__.append('Mem_memset_PtrT_i_ul__' + _c)


def Mem_fwrite_CPtr_ul_ul_CFile(p, size, count, f):
    """fwrite of size*count bytes of a char buffer (none, even at a null pointer, when that is 0); returns count."""
    if size * count != 0:
        f.S.write(_cp_bytes(p, size * count))
    return count


def Mem_fread_CPtr_ul_ul_CFile(p, size, count, f):
    """fread of size*count bytes into a char buffer: the number of whole items read; a short read sets the
    end-of-file flag (the error flag for a directory: CFile.ReadFailed)."""
    want = size * count
    b = f.S.read(want) if want else b''
    if len(b) < want:
        f.ReadFailed()
    _cp_write(p, b)
    return len(b) // size if size else 0


def _fwrite_typed(code):
    def fw(p, size, count, f):
        f.S.write(_elem_bytes(code, p.a, p.o, size * count))
        return count
    return fw


def _fread_typed(code):
    def fr(p, size, count, f):
        want = size * count
        b = f.S.read(want) if want else b''
        if len(b) < want:
            f.ReadFailed()
        _elem_write(code, p.a, p.o, b)
        return len(b) // size if size else 0
    fr.__doc__ = ('fread of size*count bytes into a typed pointer: the bytes read are copied in (a partial element '
                  'too); the number of whole items read; a short read sets the end-of-file flag (the error flag '
                  'for a directory: CFile.ReadFailed).')
    return fr


for _c in ('D', 'F', 'I', 'L', 'B'):
    globals()['Mem_fwrite_PtrT_ul_ul_CFile__' + _c] = _fwrite_typed(_c)
    globals()['Mem_fread_PtrT_ul_ul_CFile__' + _c] = _fread_typed(_c)
    __all__ += ['Mem_fwrite_PtrT_ul_ul_CFile__' + _c, 'Mem_fread_PtrT_ul_ul_CFile__' + _c]


def Bytes_View_CPtr(p):
    return p


def _view(code):
    def v(p):
        from cpp import CPtr, CPtr_ZERO
        if p.a is None and p.at is None:
            return CPtr_ZERO            # (const char*) of a null pointer is null
        return CPtr(bytearray(_all_bytes(code, p.a, p.o)), 0)
    v.__doc__ = ('The bytes of the numbers p points at, to the end of its array, as a char* for reading (a copy); '
                 'a null pointer gives the null char*.')
    return v


for _c in ('B', 'D', 'F', 'I', 'L', 'S', 'Z', 'C'):
    globals()['Bytes_View_PtrT__' + _c] = _view(_c)
    __all__.append('Bytes_View_PtrT__' + _c)
