# rfs.py - part D of the Python runtime: the C library functions of cs/runtime/Fs.cs (the part of the C# partial
# class Cpp.R in that file): errno, stderr and stdin, getenv, ctype in the C locale, atol / strtol0, ferror / fileno /
# setvbuf (no-ops as in the C# port), rename, fgetc, time(object), strncpy, strncmp, memcmp, sprintf and the
# char-array helpers of the C# back end (BufOf, memcpyBuf).
#
# As in the Java port (RFs.java), they are members of a class RFs that part C's class R extends, so R.getenv,
# R.stderr_, ... resolve; the engine calls the module-level names (R_getenv, R_stderr_, ...), which are the same
# functions. Nothing here is also declared in CRuntime.cs (toupper, strtol, Format, File_Exists are elsewhere).
# This module uses no other part's name while it imports (part C's module may import it to inherit RFs); the names it
# needs from parts A and C (CPtr, R.Format, Mem.fread, CFile) are bound on first use through the package. R_stderr_
# and R_stdin_ are made by _package_init(), which the package's __init__ calls once parts A, C and B are imported.
#
# This module also holds the late-binding helpers the other part D modules share (new_str, cbytes, cptr_class,
# formatter, net_environ).
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
import os
import sys
import time as _time

from . import fsnet as _net

__all__ = ['R_errno_', 'R_stderr_', 'R_stdin_', 'R_getenv', 'R_isdigit', 'R_isalpha', 'R_isalnum', 'R_isspace',
           'R_isupper', 'R_islower', 'R_ispunct', 'R_isprint', 'R_isxdigit', 'R_tolower', 'R_atol', 'R_strtol0',
           'R_ferror', 'R_fileno', 'R_setvbuf', 'R_rename', 'R_fgetc', 'R_time_o', 'R_strncpy', 'R_strncmp',
           'R_memcmp', 'R_BufOf', 'R_memcpyBuf', 'R_sprintf', 'RFs']

_PKG = __package__


def _pkg():
    return sys.modules[_PKG]


# ------------------------------------------------------------------------------------------------- late binding

_CPtr = None
_NewStr = None
_NewStr_CPtr_ul = None
_Format = None
_fread = None
_FromBytes = None
_bound = False


def _bind():
    """Part A's names now; part C's are looked up when first used (_c)."""
    global _CPtr, _NewStr, _NewStr_CPtr_ul, _FromBytes, _bound
    p = _pkg()
    _CPtr = cptr_class()
    _NewStr = p.NewStr
    _NewStr_CPtr_ul = p.NewStr_CPtr_ul
    _FromBytes = getattr(p, 'Str_FromBytes', None)       # part A's Str.FromBytes, when it has one
    _bound = True


def _c():
    """Part C's names: R.Format and Mem.fread."""
    global _Format, _fread
    if not _bound:
        _bind()
    p = _pkg()
    _Format = formatter()
    _fread = p.Mem_fread_CPtr_ul_ul_CFile


def cptr_class():
    """Part A's CPtr class (char*: a byte buffer B and an offset O; CPtr(B, O))."""
    p = _pkg()
    c = getattr(p, 'CPtr', None)
    if isinstance(c, type):
        return c
    return type(p.CPtr_ZERO)


def formatter():
    """Part C's glibc-exact printf (C# R.Format(CPtr fmt, object[] args) -> byte[]) as f(fmt, args) -> bytes."""
    p = _pkg()
    fn = getattr(p, 'R_Format', None)
    if fn is None:
        r = getattr(p, 'R', None)
        fn = getattr(r, 'Format', None) if r is not None else None
    if fn is not None:
        import inspect
        try:
            params = list(inspect.signature(fn).parameters.values())
            varargs = any(q.kind == q.VAR_POSITIONAL for q in params)
        except (TypeError, ValueError):
            varargs = False
        if varargs:
            return lambda f, args: _as_bytes(fn(f, *args))
        return lambda f, args: _as_bytes(fn(f, args))
    # without R.Format: snprintf (a manifest name) into a buffer large enough
    snprintf = p.R_snprintf_CPtr_ul_CPtr_oA
    C = cptr_class()

    def via_snprintf(f, args):
        n = 512
        while True:
            buf = C(bytearray(n), 0)
            k = snprintf(buf, n, f, *args)
            if k < n:
                return bytes(buf.B[:k])
            n = k + 1
    return via_snprintf


def _as_bytes(r):
    if isinstance(r, (bytes, bytearray, memoryview)):
        return bytes(r)
    if isinstance(r, list):
        return bytes([x & 255 for x in r])
    if hasattr(r, 'b') and hasattr(r, 'n'):        # a Str
        return bytes(r.b[:r.n])
    return bytes(r)


def new_str(data):
    """new Str(byte[], 0, n) of a bytes-like object."""
    if not _bound:
        _bind()
    if _FromBytes is not None:
        return _FromBytes(data)
    n = len(data)
    if n == 0:
        return _NewStr()
    return _NewStr_CPtr_ul(_CPtr(bytearray(data) + b'\0', 0), n)


def cbytes(p):
    """R.Bytes(CPtr): the bytes of a C string up to its NUL (the buffer may be a bytearray or a list of ints)."""
    b = p.B
    o = p.O
    if isinstance(b, (bytearray, bytes)):
        e = b.find(0, o)
        if e < 0:
            raise IndexError('Index was outside the bounds of the array.')
        return bytes(b[o:e])
    e = o
    while b[e] != 0:
        e += 1
    return bytes([x & 255 for x in b[o:e]])


def cstr(p):
    """R.Str(CPtr): the Latin-1 text of a C string (up to its NUL, or the end of the buffer)."""
    b = p.B
    o = p.O
    if isinstance(b, (bytearray, bytes)):
        e = b.find(0, o)
        return bytes(b[o:] if e < 0 else b[o:e]).decode('latin-1')
    e = o
    n = len(b)
    while e < n and b[e] != 0:
        e += 1
    return bytes([x & 255 for x in b[o:e]]).decode('latin-1')


_environ = None


def net_environ():
    """The environment as .NET reads it (names and values decoded from UTF-8, an invalid sequence replaced)."""
    global _environ
    if _environ is None:
        e = {}
        for k, v in os.environb.items():
            e.setdefault(_net.net_str(k), _net.net_str(v))
        _environ = e
    return _environ


def _lit(s):
    """R.Lit(string): a fresh NUL-terminated copy of the text, each char stored as (byte)c."""
    if not _bound:
        _bind()
    try:
        data = bytearray(s.encode('latin-1'))
    except UnicodeEncodeError:      # each UTF-16 unit's low byte, as (byte)s[i] takes it
        data = bytearray(s.encode('utf-16-le', 'surrogatepass')[0::2])
    data.append(0)
    return _CPtr(data, 0)


def _i32(x):
    return ((x + 0x80000000) & 0xFFFFFFFF) - 0x80000000


# ------------------------------------------------------------------------------------------------- the C functions

R_errno_ = 0
R_stderr_ = None       # made by _package_init
R_stdin_ = None


def R_getenv(name):
    """Environment.GetEnvironmentVariable: a fresh copy of the value, or a null pointer."""
    v = net_environ().get(cstr(name))
    if v is None:
        return _pkg().CPtr_ZERO
    return _lit(v)


# ctype in the C locale
def R_isdigit(c):
    return 1 if 48 <= c <= 57 else 0


def R_isalpha(c):
    return 1 if (97 <= c <= 122) or (65 <= c <= 90) else 0


def R_isalnum(c):
    return 1 if (48 <= c <= 57) or (97 <= c <= 122) or (65 <= c <= 90) else 0


def R_isspace(c):
    return 1 if c == 32 or 9 <= c <= 13 else 0


def R_isupper(c):
    return 1 if 65 <= c <= 90 else 0


def R_islower(c):
    return 1 if 97 <= c <= 122 else 0


def R_ispunct(c):
    return 1 if 32 < c < 127 and not ((48 <= c <= 57) or (97 <= c <= 122) or (65 <= c <= 90)) else 0


def R_isprint(c):
    return 1 if 32 <= c < 127 else 0


def R_isxdigit(c):
    return 1 if (48 <= c <= 57) or (97 <= c <= 102) or (65 <= c <= 70) else 0


def R_tolower(c):
    return c + 32 if 65 <= c <= 90 else c


def _strtol(s):
    """C#'s R.strtol(s, ref end, b) on a C string (base 10 whatever b is, the value clamped to the long range)."""
    b = s.B
    i = s.O
    while True:
        c = b[i] & 255
        if c == 32 or 9 <= c <= 13:
            i += 1
        else:
            break
    neg = False
    c = b[i] & 255
    if c == 43 or c == 45:
        neg = c == 45
        i += 1
    st = i
    v = 0
    while True:
        c = b[i] & 255
        if 48 <= c <= 57:
            v = v * 10 + (c - 48)
            i += 1
        else:
            break
    if i == st:
        return 0
    if neg:
        v = -v
    if v > 9223372036854775807:
        return 9223372036854775807
    if v < -9223372036854775808:
        return -9223372036854775808
    return v


def R_atol(s):
    return _strtol(s)


def R_strtol0(s, b=10):
    return _strtol(s)


def R_ferror(f):
    return 0


def R_fileno(f):
    return 3


def R_setvbuf(f, b, mode, size):
    return 0


def R_rename(a, b):
    """File.Move(a, b, overwrite: true): 0, or -1 when it fails."""
    try:
        _net.move_file(cstr(a), cstr(b))
        return 0
    except Exception:
        return -1


def R_fgetc(f):
    """f.S.ReadByte(), the end of the file setting its eof flag (Mem.fread of one byte does exactly that)."""
    if _fread is None:
        _c()
    tmp = _CPtr(bytearray(1), 0)
    if _fread(tmp, 1, 1, f) == 1:
        return tmp.B[0]
    return -1


def R_time_o(p):
    """time(p) for a pointer argument the engine passes as an object (C#'s time(object?)); p is not written."""
    return _time.time_ns() // 1000000000


def R_strncpy(d, s, n):
    m = _i32(n)
    dB, dO, sB, sO = d.B, d.O, s.B, s.O
    if m <= 0:
        return d
    if isinstance(sB, bytearray) and isinstance(dB, bytearray) and dO + m <= len(dB):
        k = sB.find(0, sO, sO + m)
        if k >= 0 or sO + m <= len(sB):
            c = (k if k >= 0 else sO + m) - sO
            dB[dO:dO + c] = sB[sO:sO + c]
            if c < m:
                dB[dO + c:dO + m] = bytes(m - c)
            return d
    i = 0
    while i < m and sB[sO + i] != 0:
        dB[dO + i] = sB[sO + i] & 255
        i += 1
    while i < m:
        dB[dO + i] = 0
        i += 1
    return d


def R_strncmp(a, b, n):
    m = _i32(n)
    aB, aO, bB, bO = a.B, a.O, b.B, b.O
    i = 0
    while i < m:
        x = aB[aO + i] & 255
        y = bB[bO + i] & 255
        if x != y:
            return x - y
        if x == 0:
            return 0
        i += 1
    return 0


def R_memcmp(a, b, n):
    m = _i32(n)
    if m <= 0:
        return 0
    aB, aO, bB, bO = a.B, a.O, b.B, b.O
    if isinstance(aB, bytearray) and isinstance(bB, bytearray) and aO + m <= len(aB) and bO + m <= len(bB):
        x = aB[aO:aO + m]
        y = bB[bO:bO + m]
        if x == y:
            return 0
        for i in range(m):
            if x[i] != y[i]:
                return x[i] - y[i]
    for i in range(m):
        x = aB[aO + i] & 255
        y = bB[bO + i] & 255
        if x != y:
            return x - y
    return 0


def R_BufOf(n, vals):
    """char buf[n] = { ... }: n bytes (at least one), the given ones first, the rest zero."""
    if not _bound:
        _bind()
    b = bytearray(n if n > 1 else 1)
    k = len(vals)
    if k:
        if k > len(b):
            k = len(b)
        b[:k] = bytes([v & 255 for v in vals[:k]])
    return _CPtr(b, 0)


def R_memcpyBuf(d, s):
    """Copy-assigning a char array member: all its bytes."""
    dB, dO, sB, sO = d.B, d.O, s.B, s.O
    k = len(dB) - dO
    if len(sB) - sO < k:
        k = len(sB) - sO
    if k <= 0:
        return
    if isinstance(dB, bytearray) and not isinstance(sB, (bytearray, bytes)):
        dB[dO:dO + k] = bytes([x & 255 for x in sB[sO:sO + k]])
    else:
        dB[dO:dO + k] = sB[sO:sO + k]


def R_sprintf(buf, fmt, *args):
    if _Format is None:
        _c()
    b = _Format(fmt, args)
    B, O = buf.B, buf.O
    k = len(b)
    if O + k + 1 > len(B):
        raise _net.sys_exc('ArgumentException', 'Destination array was not long enough. Check the destination '
                           'index, length, and the array\'s lower bounds. (Parameter \'destinationArray\')')
    B[O:O + k] = b
    B[O + k] = 0
    return k


# ------------------------------------------------------------------------------------------------- class RFs

class RFs(object):
    """The members of Cpp.R declared in Fs.cs; part C's R extends this class."""
    __slots__ = ()
    errno_ = 0
    stderr_ = None
    stdin_ = None
    getenv = staticmethod(R_getenv)
    isdigit = staticmethod(R_isdigit)
    isalpha = staticmethod(R_isalpha)
    isalnum = staticmethod(R_isalnum)
    isspace = staticmethod(R_isspace)
    isupper = staticmethod(R_isupper)
    islower = staticmethod(R_islower)
    ispunct = staticmethod(R_ispunct)
    isprint = staticmethod(R_isprint)
    isxdigit = staticmethod(R_isxdigit)
    tolower = staticmethod(R_tolower)
    atol = staticmethod(R_atol)
    strtol0 = staticmethod(R_strtol0)
    ferror = staticmethod(R_ferror)
    fileno = staticmethod(R_fileno)
    setvbuf = staticmethod(R_setvbuf)
    rename = staticmethod(R_rename)
    fgetc = staticmethod(R_fgetc)
    time_o = staticmethod(R_time_o)
    strncpy = staticmethod(R_strncpy)
    strncmp = staticmethod(R_strncmp)
    memcmp = staticmethod(R_memcmp)
    BufOf = staticmethod(R_BufOf)
    memcpyBuf = staticmethod(R_memcpyBuf)
    sprintf = staticmethod(R_sprintf)


# ------------------------------------------------------------------------------------------------- stderr, stdin

def _std_cfile(fd):
    """A C stdio FILE over descriptor fd as part C makes R_stdout_ (new CFile(Console.OpenStandardXxx(), true))."""
    p = _pkg()
    return p.CFile(p.CStdStream(fd), True)      # part C's R_stdout_ is CFile(CStdStream(1), True)


def _package_init():
    """Called by the package's __init__ once parts A, C and B are imported: R.stderr_ and R.stdin_ (part C's own,
    when its module declares them, else made here)."""
    global R_stderr_, R_stdin_
    p = _pkg()
    if R_stderr_ is None:
        e = getattr(p, 'R_stderr_', None)
        R_stderr_ = e if e is not None else _std_cfile(2)
    if R_stdin_ is None:
        i = getattr(p, 'R_stdin_', None)
        R_stdin_ = i if i is not None else _std_cfile(0)
    RFs.stderr_ = R_stderr_
    RFs.stdin_ = R_stdin_
