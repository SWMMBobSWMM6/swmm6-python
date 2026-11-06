# crt.py - part C of the Python runtime (package cpp): the C library layer of cs/runtime/CRuntime.cs and Stash.cs
# (C# class Cpp.R except CPtr, which is part A's): NUL-terminated byte strings, strtod / strtol / atoi, sscanf, the
# printf family (glibc-exact), stdio files (Cpp.CFile and the registry FlushAll flushes), stdout, time / ctime, the
# char-array helpers Lit / LitN / Buf / Bufs / BufOf / CopyBuf, the sscanf targets RefI / RefL / RefD / RefF and
# Slot / Back, and the class R (static members, extending part D's rfs.RFs as Java's R extends RFs).
# The typed boxes of params object[] arguments (I64, U32, ...) are part A's; they are recognised by class name.
#
# The C# runtime is the specification; where it departs from glibc in a case the gate does not exercise, this file
# does what glibc does, as the Go and Java ports of this runtime do (the C# engine is gated byte-identical to the C++
# one, so glibc is the reference there): printf takes integer arguments at the size their length modifier says and
# follows glibc for %a rounding, %#g, %s of a null pointer with a precision; sscanf has %n, scan sets, assignment
# suppression and glibc's float scanner, EOF rule and %u; strtod reads NaN payloads and hexadecimal floats exactly.
# Each such place says so. The algorithms follow the Java port (java/src/cpp/R.java) and the Go port (go/cpp/crt2.go,
# format.go); the speed techniques (Python's own correctly rounded % formatting for the digits, compiled format
# strings) follow the SWMM5 Python port (swmm5-ports/py/swmm5/crt.py, not copied).
#
# The C functions of cs/runtime/Fs.cs (getenv, ctype but toupper, atol, strtol0, strncpy, strncmp, memcmp, ferror,
# fileno, setvbuf, fgetc, stderr_, stdin_, memcpyBuf, sprintf, ...) are part D's (Java's class RFs), and so is
# File_Exists (fs.py's is .NET-exact). R_BufOf is defined here too (the task lists it for this part); part D's
# rfs.R_BufOf behaves the same, and the package takes this one (part C before part D).
#
# Names from part A (CPtr, CPtr_ZERO, Str) are bound lazily (_bind, run by _package_init once the package has
# imported every module, or on first use), so this module uses no other part's names while the package imports;
# the one exception the package allows is the base class R inherits from part D's rfs, imported earlier.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

import math
import os
import re
import struct
import sys
import time as _time
from fractions import Fraction as _Fraction

__all__ = [
    # char arrays and literals
    'R_Lit', 'R_LitN', 'R_Buf_i', 'R_Buf_l', 'R_Buf_ul', 'R_BufFrom', 'R_Bufs', 'R_NewBufs', 'R_BufOf', 'R_CopyBuf',
    # strings
    'R_Str', 'R_Bytes', 'R_strlen', 'R_strcpy', 'R_strcat', 'R_strcmp', 'R_strchr', 'R_strrchr', 'R_strstr',
    'R_strcspn', 'R_strtok', 'R_toupper',
    # numbers from text
    'R_strtod_CPtr', 'R_strtod_CPtr_CPtrr', 'R_atof', 'R_strtol', 'R_atoi', 'R_ScanDouble', 'R_get_ERange',
    # sscanf
    'R_RefI', 'R_RefL', 'R_RefD', 'R_RefF', 'R_Slot', 'R_sscanf', 'R_Back_i_T1r__B', 'R_Back_i_T1r_T2r__LB',
    'R_Back_i_T1r__I', 'R_Back_i_T1r__L', 'R_Back_i_T1r__D', 'R_Back_i_T1r__F', 'R_Back_i_T1r__S', 'R_Back_i_T1r__Z',
    'ScanSlot',
    # printf
    'R_fprintf', 'R_snprintf_CPtr_ul_CPtr_oA', 'R_snprintf_CPtr_l_CPtr_oA', 'R_snprintf_CPtr_i_CPtr_oA', 'R_Format',
    'R_FormatFloat',
    # files
    'CFile', 'CFile_Open', 'CStdStream', 'R_stdout_', 'R_fopen', 'R_fclose', 'R_FlushAll', 'R_fflush', 'R_feof',
    'R_rewind', 'R_fseek_CFile_l_i', 'R_fseek_CFile_i_i', 'R_ftell', 'R_fgets', 'R_remove', 'R_mkstemp',
    'R_fwriteD1', 'R_fwriteF1', 'R_fwriteI1', 'R_fwriteL1', 'R_freadD1', 'R_freadF1', 'R_freadI1', 'R_freadL1',
    # time
    'R_time', 'R_time_Ptrl', 'R_ctime_Ptrl', 'R_ctime_l', 'R_difftime',
]

_INF = float('inf')
_d = struct.Struct('<d')
_q = struct.Struct('<q')
_Q = struct.Struct('<Q')
_f = struct.Struct('<f')
_i4 = struct.Struct('<i')
_I4 = struct.Struct('<I')


def _bits2d(b):
    return _d.unpack(_Q.pack(b & 0xFFFFFFFFFFFFFFFF))[0]


_DNAN = _bits2d(0xFFF8000000000000)   # x86's default NaN (negative); glibc's strtod("-nan")
_PNAN = _bits2d(0x7FF8000000000000)   # glibc's strtod("nan")


# ------------------------------------------------------------------------------------------------ part A's names

def _bind():
    """Bind part A's CPtr, CPtr_ZERO and Str (called on first use; the package has finished importing them)."""
    global _CPtr, _CZ, _StrT
    cpp = sys.modules.get('cpp')
    _CPtr = cpp.CPtr
    _CZ = cpp.CPtr_ZERO
    _StrT = getattr(cpp, 'Str', None)


def _CPtr(b, o):
    """CPtr(b, o) until _bind replaces this placeholder with part A's class."""
    _bind()
    return _CPtr(b, o)


_CZ = None      # CPtr_ZERO once bound
_StrT = None


def _cz():
    if _CZ is None:
        _bind()
    return _CZ


# ------------------------------------------------------------------------------------------------ argument kinds

# integer boxes by their class name (so that boxes of the same names made elsewhere are read the same way)
_INT_BOXES = {'I64', 'U64', 'U32', 'I16', 'U16', 'I8', 'U8', 'Ch'}
# type -> 'i' (an integer box), 'f' (F32), 'c' (CPtr), 's' (Str), 'p' (a char array pointer), 'r' (a Ref), ...
_KIND = {int: 'n', bool: 'n', float: 'd', str: 'S', type(None): 'z'}


def _kind(a):
    t = type(a)
    k = _KIND.get(t)
    if k is None:
        k = _classify(t)
        _KIND[t] = k
    return k


def _classify(t):
    name = t.__name__
    if name in _INT_BOXES:
        return 'i'
    if name == 'F32':
        return 'f'
    if hasattr(t, 'B') and hasattr(t, 'O'):
        return 'c'
    if hasattr(t, 'b') and hasattr(t, 'n') and hasattr(t, 'c_str'):
        return 's'
    if hasattr(t, 'a') and hasattr(t, 'o') and name.startswith('Ptr'):
        return 'p'
    if isinstance(getattr(t, 'v', None), property) or 'v' in getattr(t, '__slots__', ()):
        return 'r'
    return '?'


# ------------------------------------------------------------------------------------------------ char arrays

_LITS = {}


def _lit_bytes(s):
    try:
        b = s.encode('latin-1') + b'\0'
    except UnicodeEncodeError:           # C#'s (byte)s[i]: the low byte of each char
        b = bytes(ord(c) & 255 for c in s) + b'\0'
    if len(_LITS) < 200000:
        _LITS[s] = b
    return b


def R_Lit(s):
    """A string literal as a fresh NUL-terminated byte array (each char's low byte)."""
    b = _LITS.get(s)
    if b is None:
        b = _lit_bytes(s)
    return _CPtr(bytearray(b), 0)


def R_LitN(s, n):
    """char a[n] = "literal": n bytes (at least the literal and its NUL), the literal's first, the rest zero."""
    b = _LITS.get(s)
    if b is None:
        b = _lit_bytes(s)
    m = len(b)
    buf = bytearray(n if n > m else m)
    buf[:m] = b
    return _CPtr(buf, 0)


def R_Buf_i(n):
    """char a[n]: n zero bytes (at least one)."""
    return _CPtr(bytearray(n if n > 1 else 1), 0)


R_Buf_l = R_Buf_i
R_Buf_ul = R_Buf_i


def R_BufFrom(lit, n):
    """char a[n] initialised from a C string (at most n bytes of it)."""
    p = R_Buf_i(n)
    s = _cb(lit)[:n]
    p.B[:len(s)] = s
    return p


def R_Bufs(n, m):
    """char a[n][m]: n zeroed rows of m bytes (a C# array: a list)."""
    return [R_Buf_i(m) for _ in range(n)]


R_NewBufs = R_Bufs


def R_BufOf(n, vals):
    """char buf[n] = { ... }: n bytes (at least one), the given ones first, the rest zero (as Fs.cs)."""
    b = bytearray(n if n > 1 else 1)
    k = min(len(vals), len(b))
    for i in range(k):
        b[i] = vals[i] & 255
    return _CPtr(b, 0)


def R_CopyBuf(p):
    """The copy of a char array member: the whole buffer, the same offset (a null pointer stays null)."""
    B = p.B
    if B is None:
        return p
    return _CPtr(bytearray(B) if type(B) is bytearray else list(B), p.O)


# ------------------------------------------------------------------------------------------------ strings

def _cb(p):
    """The bytes of the C string at p (to its NUL; to the buffer's end when it has none), as bytes."""
    B = p.B
    O = p.O
    if type(B) is bytearray or type(B) is bytes:
        e = B.find(0, O)
        return bytes(B[O:e]) if e >= 0 else bytes(B[O:])
    return _cb_seq(B, O)


def _cbv(p):
    """As _cb, but a bytearray slice may be returned (for formatting, where any bytes-like object will do)."""
    B = p.B
    O = p.O
    if type(B) is bytearray:
        e = B.find(0, O)
        return B[O:e] if e >= 0 else B[O:]
    return _cb_seq(B, O)


def _cb_seq(B, O):
    """A C string in a list of byte (0..255) or sbyte (-128..127) values."""
    out = bytearray()
    n = len(B)
    i = O
    while i < n:
        c = B[i] & 255
        if c == 0:
            break
        out.append(c)
        i += 1
    return bytes(out)


def _put(p, off, data):
    """Store the bytes data at p + off (a bytearray by slice; a list of ints as their values 0..255, as part A's
    CPtr.set stores a byte)."""
    B = p.B
    o = p.O + off
    if type(B) is bytearray:
        B[o:o + len(data)] = data
    else:
        B[o:o + len(data)] = list(data)


def R_Str(p):
    """The Python string of a C string (each byte one char, Latin-1)."""
    return _cb(p).decode('latin-1')


def R_Bytes(p):
    """A copy of a C string's bytes, without the terminator."""
    return _cb(p)


def R_strlen(p):
    B = p.B
    O = p.O
    if type(B) is bytearray:
        e = B.find(0, O)
        if e < 0:
            raise IndexError('strlen: no terminating NUL in the buffer')
        return e - O
    i = O
    while B[i] & 255:
        i += 1
    return i - O


def R_strcpy(d, s):
    src = _cb(s) + b'\0'                 # a copy first: an overlapping copy behaves as memmove
    _put(d, 0, src)
    return d


def R_strcat(d, s):
    src = _cb(s) + b'\0'
    _put(d, R_strlen(d), src)
    return d


def R_strcmp(a, b):
    """strcmp: the difference of the first unsigned bytes that differ (0 when equal)."""
    x = _cb(a)
    y = _cb(b)
    if x == y:
        return 0
    n = len(x) if len(x) < len(y) else len(y)
    for i in range(n):
        if x[i] != y[i]:
            return x[i] - y[i]
    return (x[n] if len(x) > n else 0) - (y[n] if len(y) > n else 0)


def R_strchr(s, c):
    """The first c in the C string s (c == 0: its terminator), or a null pointer."""
    ch = c & 255
    B = s.B
    O = s.O
    if type(B) is bytearray:
        e = B.find(0, O)
        if ch == 0:
            return _CPtr(B, e)
        i = B.find(ch, O, e if e >= 0 else len(B))
        return _CPtr(B, i) if i >= 0 else _cz()
    i = O
    while True:
        x = B[i] & 255
        if x == ch:
            return _CPtr(B, i)
        if x == 0:
            return _cz()
        i += 1


def R_strrchr(s, c):
    """The last c in the C string s, or a null pointer."""
    ch = c & 255
    B = s.B
    O = s.O
    if type(B) is bytearray:
        e = B.find(0, O)
        if ch == 0:
            return _CPtr(B, e)
        i = B.rfind(ch, O, e if e >= 0 else len(B))
        return _CPtr(B, i) if i >= 0 else _cz()
    r = None
    i = O
    while True:
        x = B[i] & 255
        if x == ch:
            r = i
        if x == 0:
            return _CPtr(B, r) if r is not None else _cz()
        i += 1


def R_strstr(h, n):
    """The first occurrence of the C string n in the C string h (h itself for an empty n), or a null pointer."""
    nb = _cb(n)
    if not nb:
        return h
    B = h.B
    O = h.O
    if type(B) is bytearray:
        e = B.find(0, O)
        i = B.find(nb, O, e if e >= 0 else len(B))
        return _CPtr(B, i) if i >= 0 else _cz()
    hb = _cb(h)
    i = hb.find(nb)
    return _CPtr(B, O + i) if i >= 0 else _cz()


def R_strcspn(s, reject):
    sb = _cb(s)
    rb = _cb(reject)
    for i, c in enumerate(sb):
        if c in rb:
            return i
    return len(sb)


_tok = [None]      # strtok's saved position: (buffer, index) or None


def R_strtok(s, delim):
    """strtok: the next token of s (s null: of the string the last call was given), the delimiter after it set to
    NUL; a null pointer when no token is left."""
    if s.B is not None:
        _tok[0] = (s.B, s.O)
    st = _tok[0]
    if st is None:
        return _cz()
    B, p = st
    ds = _cb(delim)
    if type(B) is bytearray:
        n = len(B)
        while p < n and B[p] != 0 and B[p] in ds:          # skip leading delimiters
            p += 1
        if p >= n or B[p] == 0:
            _tok[0] = None
            return _cz()
        start = p
        while p < n and B[p] != 0 and B[p] not in ds:
            p += 1
        if p < n and B[p] != 0:
            B[p] = 0
            _tok[0] = (B, p + 1)
        else:
            _tok[0] = None
        return _CPtr(B, start)
    while B[p] & 255 and (B[p] & 255) in ds:
        p += 1
    if not B[p] & 255:
        _tok[0] = None
        return _cz()
    start = p
    while B[p] & 255 and (B[p] & 255) not in ds:
        p += 1
    if B[p] & 255:
        B[p] = 0
        _tok[0] = (B, p + 1)
    else:
        _tok[0] = None
    return _CPtr(B, start)


def R_toupper(c):
    return c - 32 if 97 <= c <= 122 else c


# ------------------------------------------------------------------------------------------------ numbers from text

_SPACE = b' \t\n\x0b\x0c\r'
# glibc strtod's decimal form after optional space: sign, digits with an optional point (at least one digit), and
# an exponent only when it has digits
_DEC = re.compile(rb'[ \t\n\x0b\x0c\r]*([+-]?)((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)')
_DEC_NS = re.compile(rb'([+-]?)((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)')
_HEXF = re.compile(rb'0[xX]((?:[0-9A-Fa-f]+(?:\.[0-9A-Fa-f]*)?|\.[0-9A-Fa-f]+))(?:[pP]([+-]?[0-9]+))?')


def _low(c):
    return c | 0x20 if 65 <= c <= 90 else c


def _hexval(text, neg):
    """[-]0xMANT.FRAC p EXP as glibc's strtod rounds it (float.fromhex is correctly rounded too)."""
    try:
        v = float.fromhex(text)
    except OverflowError:
        v = _INF
    return -v if neg else v


def _c_unsigned(s):
    """strtoull(s, 0, 0) of a whole n-char-sequence (decimal, 0x hexadecimal or 0 octal); None if it is not one."""
    try:
        if s[:2] in (b'0x', b'0X'):
            if len(s) <= 2:
                return None
            v = int(s[2:], 16)
        elif len(s) > 1 and s[:1] == b'0':
            v = int(s[1:], 8)
        else:
            v = int(s, 10)
    except ValueError:
        return None
    return v if v <= 0xFFFFFFFFFFFFFFFF else 0xFFFFFFFFFFFFFFFF   # strtoull saturates


# Whether the last R_ScanDouble (strtod, atof, sscanf's conversions, from_chars) would have set errno to ERANGE in
# glibc: an overflow to +-inf, a nonzero number that became zero, or a result that is tiny (below DBL_MIN after
# rounding to 53 bits, the x86 rule) and inexact. libstdc++'s std::stod throws out_of_range on it (C# Cpp.R.ERange,
# Java R.ERange); part A's Str_stod reads it through R_get_ERange().
ERange = False
_DBL_MIN = 2.2250738585072014e-308


def R_get_ERange():
    """R.ERange (see the module global ERange)."""
    return ERange


def _big(digits):
    """int() of a string of decimal digits of any length (int() refuses more than 4300 digits)."""
    if len(digits) <= 4000:
        return int(digits)
    d = 0
    for j in range(0, len(digits), 4000):
        c = digits[j:j + 4000]
        d = d * 10 ** len(c) + int(c)
    return d


def _dec_range(txt, av):
    """glibc strtod's ERANGE for the decimal text txt (bytes: digits, '.', exponent; no sign) whose correctly rounded
    value is av: overflow, or underflow (tiny after rounding and inexact, decided exactly). Java's R.decRange."""
    if av == _INF:
        return True
    if av > _DBL_MIN:                                       # above DBL_MIN: normal
        return False
    n = len(txt)
    k = 0
    while k < n and 48 <= txt[k] <= 57:
        k += 1
    ip = txt[:k]
    fp = b''
    if k < n and txt[k] == 46:
        k += 1
        s = k
        while k < n and 48 <= txt[k] <= 57:
            k += 1
        fp = txt[s:k]
    e10 = -len(fp)
    digits = (ip + fp).lstrip(b'0')
    if not digits:                                          # a zero is exact
        return False
    if k < n and (txt[k] == 101 or txt[k] == 69):
        k += 1
        en = False
        if k < n and (txt[k] == 43 or txt[k] == 45):
            en = txt[k] == 45
            k += 1
        ex = 0
        while k < n and 48 <= txt[k] <= 57:
            ex = min(ex * 10 + (txt[k] - 48), 1000000000)
            k += 1
        e10 += -ex if en else ex
    nd = len(digits)
    if nd + e10 < -340:                                     # below 1e-340: zero, and not exact
        return True
    if e10 >= 0:
        return False
    d = _big(digits)
    p10 = 10 ** -e10                                        # the value is d / 10^-e10
    # tiny after rounding: below 2^-1022 - 2^-1076, the midpoint under DBL_MIN of the 53-bit grid
    if (d << 1076) >= ((1 << 54) - 1) * p10:
        return False
    # inexact: not av itself, which is m * 2^-1074 for a subnormal (or DBL_MIN, or zero)
    m = _q.unpack(_d.pack(av))[0]
    return (d << 1074) != m * p10


def R_ScanDouble(b, i, skip=True):
    """glibc's strtod on the bytes b from index i (the end of b or a NUL ends the input): (value, end, text), end
    -1 when there is no number; text is the number's characters ([-]digits... or [-]0x...p...) for strtof, None for
    inf / nan. Decimal text is converted by Python's float(), correctly rounded as glibc's strtod is; hexadecimal
    floats, inf / infinity and nan / nan(n-char-sequence) (its payload kept) as glibc reads them. The C# runtime
    reads NaN as .NET's NaN (negative) and "-nan" as its negation, without payloads; glibc is followed.
    Sets ERange as glibc's strtod sets errno to ERANGE (Java's R.scanDouble)."""
    global ERange
    m = (_DEC if skip else _DEC_NS).match(b, i)
    if m is not None:
        num = m.group(2)
        if num == b'0' and m.end() < len(b) and b[m.end()] in b'xX':
            pass                                            # maybe a hexadecimal float: below
        else:
            neg = m.group(1) == b'-'
            v = float(num)
            ERange = False if _DBL_MIN < v < _INF else _dec_range(num, v)
            return (-v if neg else v), m.end(), (b'-' + num if neg else num)
    ERange = False
    n = len(b)
    if skip:
        while i < n and b[i] in _SPACE:
            i += 1
    neg = False
    if i < n and b[i] in b'+-':
        neg = b[i] == 45
        i += 1
    c0 = _low(b[i]) if i < n else 0
    if c0 == 105 and b[i:i + 3].lower() == b'inf':          # inf / infinity
        i += 3
        if b[i:i + 5].lower() == b'inity':
            i += 5
        return (-_INF if neg else _INF), i, None
    if c0 == 110 and b[i:i + 3].lower() == b'nan':          # nan / nan(n-char-sequence)
        i += 3
        if i < n and b[i] == 40:
            k = i + 1
            while k < n and (48 <= b[k] <= 57 or 65 <= b[k] <= 90 or 97 <= b[k] <= 122 or b[k] == 95):
                k += 1
            if k < n and b[k] == 41:
                pl = 0 if k == i + 1 else _c_unsigned(b[i + 1:k])
                if pl is not None:                             # glibc keeps strtoull(seq) as the payload
                    v = _bits2d(0x7FF8000000000000 | (pl & ((1 << 51) - 1)))
                    return (-v if neg else v), k + 1, None
                i = k + 1
        return (_DNAN if neg else _PNAN), i, None
    if c0 == 48 and i + 1 < n and b[i + 1] in b'xX':        # hexadecimal float (C99), as glibc reads it
        h = _HEXF.match(b, i)
        if h is not None:
            mant = h.group(1)
            ex = h.group(2) or b'0'
            text = (b'-0x' if neg else b'0x') + mant + b'p' + ex
            hv = _hexval(text.decode('ascii'), False)
            # (a hexadecimal subnormal is not checked, as in the Java and C#)
            ERange = (hv == 0.0 and mant.replace(b'.', b'').strip(b'0') != b'') or math.isinf(hv)
            return hv, h.end(), text
        return (-0.0 if neg else 0.0), i + 1, (b'-0' if neg else b'0')   # the "0" alone
    return 0.0, -1, None


def R_strtod_CPtr(s):
    b = _cb(s)
    v, e, _ = R_ScanDouble(b, 0)
    return v if e >= 0 else 0.0


def R_strtod_CPtr_CPtrr(s, end):
    """strtod(s, &end): end (a Ref) is set to the character after the number, or to s when there is none."""
    b = _cb(s)
    v, e, _ = R_ScanDouble(b, 0)
    if e < 0:
        end.v = s
        return 0.0
    end.v = _CPtr(s.B, s.O + e)
    return v


def R_atof(s):
    return R_strtod_CPtr(s)


_INT = re.compile(rb'[ \t\n\x0b\x0c\r]*([+-]?)([0-9]+)')


def R_strtol(s, end, base=10):
    """The C# runtime's strtol: optional space and sign, decimal digits (the engine passes base 10), clamped to
    long; end (a Ref, or None) is set to the character after the digits, or to s when there are none."""
    b = _cb(s)
    m = _INT.match(b)
    if m is None:
        if end is not None:
            end.v = s
        return 0
    if end is not None:
        end.v = _CPtr(s.B, s.O + m.end())
    v = int(m.group(2))
    if m.group(1) == b'-':
        v = -v
    if v > 9223372036854775807:
        return 9223372036854775807
    if v < -9223372036854775808:
        return -9223372036854775808
    return v


def R_atoi(s):
    """atoi: strtol's long, then (int): its low 32 bits."""
    v = R_strtol(s, None, 10)
    return ((v + 0x80000000) & 0xFFFFFFFF) - 0x80000000


# ------------------------------------------------------------------------------------------------ float conversion

def _f32(x):
    try:
        return _f.unpack(_f.pack(x))[0]
    except OverflowError:
        return math.copysign(_INF, x)


def _text_f32(text, d):
    """strtof of the number text whose double value is d: correctly rounded to single precision from the text
    itself. Rounding d again rounds twice, which differs only when d lies exactly halfway between two floats (a
    midpoint is a double, so the exact value cannot be on the other side of one otherwise); then the exact value
    decides."""
    f = _f32(d)
    if f == d or d != d:
        return f
    ad = abs(d)
    af = abs(f)
    if af == _INF:
        lo, hi = 3.4028234663852886e38, _INF
    elif af < ad:
        lo = af
        hi = _f.unpack(_I4.pack(_I4.unpack(_f.pack(af))[0] + 1))[0]
    else:
        hi = af
        lo = _f.unpack(_I4.pack(_I4.unpack(_f.pack(af))[0] - 1))[0]
    mid = 3.4028235677973366e38 if hi == _INF else (lo + hi) / 2
    if ad != mid:
        return f
    x = abs(_exact(text))
    mm = _Fraction(mid)
    if x > mm:
        r = hi
    elif x < mm:
        r = lo
    else:                                   # a true tie: the even one (past FLT_MAX, which is odd, that is inf)
        r = lo if (_I4.unpack(_f.pack(lo))[0] & 1) == 0 else hi
    return -r if d < 0 else r


def _exact(text):
    """The exact rational value of a number text (decimal, or [-]0xMANT.FRACpEXP)."""
    t = text.decode('ascii') if isinstance(text, (bytes, bytearray)) else text
    neg = t.startswith('-')
    if neg:
        t = t[1:]
    if t[:2].lower() == '0x':
        m, _, e = t[2:].partition('p')
        ip, _, fp = m.partition('.')
        v = _Fraction(int((ip + fp) or '0', 16)) * _Fraction(2) ** (int(e or '0') - 4 * len(fp))
    else:
        v = _Fraction(t)
    return -v if neg else v


# ------------------------------------------------------------------------------------------------ sscanf targets

class _SetI:
    """An int lvalue passed to sscanf (C# Cpp.RefI): Set stores the value scanned."""
    __slots__ = ('Set',)

    def __init__(self, f):
        self.Set = f


class _SetL:
    __slots__ = ('Set',)

    def __init__(self, f):
        self.Set = f


class _SetD:
    __slots__ = ('Set',)

    def __init__(self, f):
        self.Set = f


class _SetF:
    __slots__ = ('Set',)

    def __init__(self, f):
        self.Set = f


def R_RefI(a):
    return _SetI(a)


def R_RefL(a):
    return _SetL(a)


def R_RefD(a):
    return _SetD(a)


def R_RefF(a):
    return _SetF(a)


class ScanSlot:
    """The sscanf target that is a C pointer parameter (ref in C#): the value is stashed, then R.Back copies it."""
    __slots__ = ()


R_Slot = ScanSlot()
_stash = []


class _Overflow(Exception):
    pass


def _overflow(msg='Value was either too large or too small.'):
    from cpp import SysException
    return SysException('OverflowException', msg)


def _conv_int(v, lo, hi):
    """Convert.ChangeType of a stashed number to an integer type whose values are [lo, hi] (checked as C#'s Convert
    is; hi covers the unsigned type of the same size, which a Python int cannot tell apart)."""
    if type(v) is float:
        r = round(v)                                     # Convert rounds half to even
        if not (lo <= r <= hi):
            raise _overflow()
        return int(r)
    if not (lo <= v <= hi):
        raise _overflow()
    return v


def _take(i):
    return _stash[i] if i < len(_stash) else None


def R_Back_i_T1r__B(n, a):
    v = _take(0)
    if v is not None:
        a.v = _conv_int(v, -128, 255)
    return n


def R_Back_i_T1r__S(n, a):
    v = _take(0)
    if v is not None:
        a.v = _conv_int(v, -32768, 65535)
    return n


def R_Back_i_T1r__I(n, a):
    v = _take(0)
    if v is not None:
        a.v = _conv_int(v, -2147483648, 0xFFFFFFFF)
    return n


def R_Back_i_T1r__L(n, a):
    v = _take(0)
    if v is not None:
        a.v = _conv_int(v, -9223372036854775808, 9223372036854775807)
    return n


def R_Back_i_T1r__D(n, a):
    v = _take(0)
    if v is not None:
        a.v = float(v)
    return n


def R_Back_i_T1r__F(n, a):
    v = _take(0)
    if v is not None:
        a.v = _f32(float(v))
    return n


def R_Back_i_T1r__Z(n, a):
    v = _take(0)
    if v is not None:
        a.v = bool(v)
    return n


def R_Back_i_T1r_T2r__LB(n, a, b):
    v = _take(0)
    if v is not None:
        a.v = _conv_int(v, -9223372036854775808, 9223372036854775807)
    v = _take(1)
    if v is not None:
        b.v = _conv_int(v, -128, 255)
    return n


# ------------------------------------------------------------------------------------------------ sscanf

_SCF = {}        # format bytes -> compiled directives


def _scan_compile(f):
    """The directives of a scanf format: ('w',) white space, ('l', c) a literal byte, or
    ('%', suppress, width, lmod, conv, set) a conversion (set: the bytes a scan set accepts)."""
    out = []
    k = 0
    n = len(f)
    while k < n:
        c = f[k]
        if c in _SPACE:
            while k < n and f[k] in _SPACE:
                k += 1
            out.append(('w',))
            continue
        if c != 37:
            out.append(('l', c))
            k += 1
            continue
        k += 1
        suppress = False
        if k < n and f[k] == 42:
            suppress = True
            k += 1
        width = 0
        while k < n and 48 <= f[k] <= 57:
            width = width * 10 + f[k] - 48
            k += 1
        lmod = 0
        while k < n and f[k] in b'lhLzjtq':
            if f[k] != 104:
                lmod += 1
            k += 1
        conv = f[k] if k < n else 0
        k += 1
        cset = None
        if conv == 91:                                      # [ scan set ]
            s = bytearray(256)
            negate = False
            if k < n and f[k] == 94:
                negate = True
                k += 1
            first = True
            while k < n and (first or f[k] != 93):
                ch = f[k]
                if ch == 45 and not first and k + 1 < n and f[k + 1] != 93 and f[k - 1] <= f[k + 1]:
                    for x in range(f[k - 1], f[k + 1] + 1):
                        s[x] = 1
                    k += 2
                    first = False
                    continue
                s[ch] = 1
                k += 1
                first = False
            if k < n and f[k] == 93:
                k += 1
            if negate:
                for x in range(256):
                    s[x] ^= 1
                s[0] = 0
            cset = bytes(x for x in range(256) if s[x])
        out.append(('%', suppress, width, lmod, conv, cset))
    return tuple(out)


def _store_int(a, v, lmod):
    """An integer conversion's value into its target (an int unless the l modifier was given)."""
    t = type(a)
    if t is _SetI:
        a.Set(((v + 0x80000000) & 0xFFFFFFFF) - 0x80000000)
    elif t is _SetL:
        a.Set(((v + 0x8000000000000000) & 0xFFFFFFFFFFFFFFFF) - 0x8000000000000000)
    elif t is ScanSlot:
        _stash.append(((v + 0x8000000000000000) & 0xFFFFFFFFFFFFFFFF) - 0x8000000000000000 if lmod
                      else ((v + 0x80000000) & 0xFFFFFFFF) - 0x80000000)
    elif hasattr(a, 'v'):                                   # a Cell / ArrRef: int, or long with l
        a.v = (((v + 0x8000000000000000) & 0xFFFFFFFFFFFFFFFF) - 0x8000000000000000 if lmod
               else ((v + 0x80000000) & 0xFFFFFFFF) - 0x80000000)
    else:
        raise ValueError('sscanf integer target %r' % (a,))


def _store_buf(a, data):
    """A %s / %[ / %c string into a char* target: a CPtr, or a char array pointer (Ptr<sbyte>)."""
    if hasattr(a, 'B'):
        _put(a, 0, data)
        return
    if hasattr(a, 'a') and hasattr(a, 'o'):
        arr = a.a
        o = a.o
        if type(arr) is bytearray:
            arr[o:o + len(data)] = data
        else:                                               # a char array (sbyte values)
            arr[o:o + len(data)] = [c - 256 if c > 127 else c for c in data]
        return
    raise ValueError('sscanf string target %r' % (a,))


def R_sscanf(s, fmt, *args):
    """glibc's sscanf for the directives the engine uses: %d %i %u (decimal), %f %e %g %a (and capitals), %s, %c,
    %[...], %n and %%, with * and widths and the h/l/L/z/j/t/q modifiers. Targets: the R_RefI / RefL / RefD / RefF
    setters, R_Slot (the value is stashed for R_Back), char buffers (CPtr, Ptr<sbyte>) and Refs (Cell, ArrRef).
    It returns the number of assignments, or EOF (-1) when the input ends before the first assignment.
    The C# runtime handles fewer directives (no %n, scan sets or %a) and differs from glibc on the EOF rule and on
    %u of negative or huge numbers; glibc is followed (as the Go and Java ports do)."""
    del _stash[:]
    b = _cb(s)
    fb = _cb(fmt)
    dirs = _SCF.get(fb)
    if dirs is None:
        dirs = _scan_compile(fb)
        if len(_SCF) < 10000:
            _SCF[fb] = dirs
    n = len(b)
    i = 0
    ai = 0
    count = 0
    for d in dirs:
        op = d[0]
        if op == 'w':
            while i < n and b[i] in _SPACE:
                i += 1
            continue
        if op == 'l':
            if i >= n:
                return -1 if count == 0 else count
            if b[i] != d[1]:
                return count
            i += 1
            continue
        _, suppress, width, lmod, conv, cset = d
        if conv != 99 and conv != 91 and conv != 110:        # not c, [ or n: leading white space skipped
            while i < n and b[i] in _SPACE:
                i += 1
        if conv == 110:                                      # n: the characters read so far
            if not suppress:
                _store_int(args[ai], i, lmod)
                ai += 1
            continue
        if i >= n:
            return -1 if count == 0 else count
        w = width if width > 0 else 1 << 30
        if conv == 100 or conv == 105 or conv == 117:        # d i u
            j = i
            lim = i + w
            neg = False
            if j < lim and (b[j] == 43 or b[j] == 45):
                neg = b[j] == 45
                j += 1
            ds = j
            e = lim if lim < n else n
            while j < e and 48 <= b[j] <= 57:
                j += 1
            if j == ds:
                return count
            u = int(b[ds:j])
            i = j
            if conv == 117:                  # strtoul: the magnitude, negated as an unsigned number (ULONG_MAX past it)
                if u > 0xFFFFFFFFFFFFFFFF:
                    u = 0xFFFFFFFFFFFFFFFF
                elif neg:
                    u = (-u) & 0xFFFFFFFFFFFFFFFF
                v = u & 0xFFFFFFFF if lmod == 0 else u
            else:                            # strtol: clamped to long
                v = -u if neg else u
                if v > 9223372036854775807:
                    v = 9223372036854775807
                elif v < -9223372036854775808:
                    v = -9223372036854775808
            if not suppress:
                _store_int(args[ai], v, lmod)
                ai += 1
                count += 1
        elif conv in b'feEgGaAF':
            e, num = _scan_float(b, i, width)
            if e < 0:
                return count
            i = e
            v, ne, text = R_ScanDouble(num, 0, False)
            if ne <= 0:
                return count
            if not suppress:
                a = args[ai]
                ai += 1
                t = type(a)
                if t is _SetD:
                    a.Set(v)
                elif t is _SetF:
                    a.Set(_text_f32(text, v) if text is not None else _f32(v))
                elif t is ScanSlot:
                    _stash.append(v if lmod else (_text_f32(text, v) if text is not None else _f32(v)))
                elif hasattr(a, 'v'):
                    a.v = v if lmod else (_text_f32(text, v) if text is not None else _f32(v))
                else:
                    raise ValueError('sscanf %%%c target %r' % (conv, a))
                count += 1
        elif conv == 115 or conv == 91:                      # s [
            j = i
            e = i + w if i + w < n else n
            if conv == 115:
                while j < e and b[j] not in _SPACE:
                    j += 1
            else:
                while j < e and b[j] in cset:
                    j += 1
            if j == i:
                return count                                 # only %[ can match nothing: a matching failure
            if not suppress:
                _store_buf(args[ai], b[i:j] + b'\0')
                ai += 1
                count += 1
            i = j
        elif conv == 99:                                     # c
            m = width if width > 0 else 1
            j = i + m if i + m < n else n
            if not suppress:
                a = args[ai]
                ai += 1
                t = type(a)
                if t is _SetI:
                    c = b[i]
                    a.Set(c - 256 if c > 127 else c)
                elif t is ScanSlot:
                    for k in range(i, j):
                        c = b[k]
                        _stash.append(c - 256 if c > 127 else c)
                elif t is _SetL:
                    c = b[i]
                    a.Set(c - 256 if c > 127 else c)
                elif hasattr(a, 'B') or (hasattr(a, 'a') and hasattr(a, 'o')):
                    _store_buf(a, b[i:j])
                elif hasattr(a, 'v'):
                    c = b[i]
                    a.v = c - 256 if c > 127 else c
                else:
                    raise ValueError('sscanf %%c target %r' % (a,))
                count += 1
            i = j
        elif conv == 37:                                     # %%
            if b[i] != 37:
                return count
            i += 1
        else:
            raise ValueError('sscanf %%%c' % conv)
    return count


def _scan_float(b, i, width):
    """The scanner of glibc's vfscanf for a floating conversion at b[i] (not a space, not the end): the characters
    it takes (at most width, 0 for no limit) and the index after them, or (-1, None) for a matching failure. Like
    glibc, it reads one character ahead and pushes back only that one, so an exponent marker or a sign with no digits
    after it ("1e", "1e+") is taken (and ignored by the conversion), and a partial "infinity" or a bare "0x" fails.
    "nan" never takes an n-char-sequence."""
    n = len(b)
    left = width if width > 0 else -1      # characters still allowed (-1: no limit)
    p = i
    buf = bytearray()

    def inchar():
        nonlocal p
        if p < n:
            p += 1
            return b[p - 1]
        return -1

    def dec():
        nonlocal left
        if left > 0:
            left -= 1

    def expect(ch):
        if left == 0:
            return False
        c = inchar()
        if c < 0 or _low(c) != ch:
            return False
        dec()
        buf.append(c)
        return True

    c = inchar()
    dec()
    got_sign = 0
    if c == 45 or c == 43:
        got_sign = 1
        buf.append(c)
        if left == 0:
            return -1, None
        c = inchar()
        if c < 0:
            return -1, None
        dec()
    if _low(c) == 110:                                       # nan
        buf.append(c)
        if not expect(97) or not expect(110):
            return -1, None
        return p, bytes(buf)
    if _low(c) == 105:                                       # inf / infinity
        buf.append(c)
        if not expect(110) or not expect(102):
            return -1, None
        if left != 0:
            c = inchar()
            if c >= 0:
                if _low(c) == 105:
                    dec()
                    buf.append(c)
                    if not expect(110) or not expect(105) or not expect(116) or not expect(121):
                        return -1, None
                else:
                    p -= 1                                   # ungetc
        return p, bytes(buf)
    exp_char = 101
    hexa = got_digit = got_dot = got_e = False
    if left != 0 and c == 48:
        buf.append(c)
        c = inchar()
        dec()
        if left != 0 and _low(c) == 120:
            buf.append(c)
            hexa = True
            exp_char = 112
            c = inchar()
            dec()
        else:
            got_digit = True
    while True:
        if 48 <= c <= 57:
            buf.append(c)
            got_digit = True
        elif not got_e and hexa and (48 <= c <= 57 or 97 <= _low(c) <= 102):
            buf.append(c)
            got_digit = True
        elif got_e and buf[-1] == exp_char and (c == 45 or c == 43):
            buf.append(c)
        elif got_digit and not got_e and _low(c) == exp_char:
            buf.append(exp_char)
            got_e = got_dot = True
        elif not got_dot and c == 46:
            buf.append(c)
            got_dot = True
        else:
            if c >= 0:
                p -= 1                                       # not part of the number: pushed back
            break
        if left == 0:
            break
        c = inchar()
        if c < 0:
            break
        dec()
    if len(buf) == got_sign or (hexa and len(buf) == 2 + got_sign):
        return -1, None
    return p, bytes(buf)


# ------------------------------------------------------------------------------------------------ printf family

def _d2l(x):
    """x86-64 cvttsd2si: NaN and out-of-range values give the "integer indefinite" value."""
    if x != x or x >= 9223372036854775808.0 or x < -9223372036854775808.0:
        return -9223372036854775808
    return int(x)


def _argint(a):
    """An integer argument as its C# value: signed from a signed type, the unsigned value from an unsigned one,
    1 / 0 from a bool, truncated toward zero (x86's conversion) from a floating one."""
    k = _kind(a)
    if k == 'n':
        return int(a)
    if k == 'i':
        return a.v
    if k == 'd':
        return _d2l(a)
    if k == 'f':
        return _d2l(a.v)
    raise TypeError('printf: integer argument %r' % (a,))


def _argf(a):
    k = _kind(a)
    if k == 'd':
        return a
    if k == 'n' or k == 'i':
        return float(a if k == 'n' else a.v)
    if k == 'f':
        return a.v
    raise TypeError('printf: floating argument %r' % (a,))


def _argstr(a):
    """A %s argument's bytes (any bytes-like), or None for a null pointer: a C string, a char array pointer, a
    std::string (Str), a Python str."""
    k = _kind(a)
    if k == 'c':
        return None if a.B is None else _cbv(a)
    if k == 's':
        return bytes(a.b[:a.n])
    if k == 'S':
        try:
            return a.encode('latin-1')
        except UnicodeEncodeError:
            return bytes(ord(c) & 255 for c in a)
    if k == 'z':
        return None
    if k == 'p':
        arr = a.a
        if arr is None:
            return None
        return _cb_seq(arr, a.o)
    raise TypeError('printf: string argument %r' % (a,))


_SIGNED = {8: lambda v: ((v + 0x80) & 0xFF) - 0x80, 16: lambda v: ((v + 0x8000) & 0xFFFF) - 0x8000,
           32: lambda v: ((v + 0x80000000) & 0xFFFFFFFF) - 0x80000000,
           64: lambda v: ((v + 0x8000000000000000) & 0xFFFFFFFFFFFFFFFF) - 0x8000000000000000}
_MASK = {8: 0xFF, 16: 0xFFFF, 32: 0xFFFFFFFF, 64: 0xFFFFFFFFFFFFFFFF}

# fast-path argument kinds
_K_F, _K_I32, _K_IW, _K_U, _K_S, _K_C = range(6)

_FC = {}         # format bytes -> (python format or None, kinds, segments)


def _fmt_compile(fb):
    """Split a printf format into literal bytes and conversion specs (left, plus, space, alt, zero, width, prec,
    size, conv) -- width -1 and prec -2 for '*', prec -1 for none, size the integer width in bits -- and, when every
    spec is one Python's bytes % operator formats exactly as glibc does for the arguments it will get, the Python
    format and the argument kinds of the fast path."""
    segs = []
    py = bytearray()
    kinds = []
    fast = True
    i = 0
    n = len(fb)
    lit = 0
    while i < n:
        if fb[i] != 37:
            i += 1
            continue
        if i > lit:
            segs.append(fb[lit:i])
            py += fb[lit:i]
        i += 1
        if i < n and fb[i] == 37:
            segs.append(b'%')
            py += b'%%'
            i += 1
            lit = i
            continue
        left = plus = space = alt = zero = False
        while i < n:
            c = fb[i]
            if c == 45:
                left = True
            elif c == 43:
                plus = True
            elif c == 32:
                space = True
            elif c == 35:
                alt = True
            elif c == 48:
                zero = True
            else:
                break
            i += 1
        width = 0
        if i < n and fb[i] == 42:
            width = -1
            i += 1
        else:
            while i < n and 48 <= fb[i] <= 57:
                width = width * 10 + fb[i] - 48
                i += 1
        prec = -1
        if i < n and fb[i] == 46:
            i += 1
            prec = 0
            if i < n and fb[i] == 42:
                prec = -2
                i += 1
            else:
                while i < n and 48 <= fb[i] <= 57:
                    prec = prec * 10 + fb[i] - 48
                    i += 1
        size = 32
        while i < n:
            c = fb[i]
            if c == 104:
                size = 8 if size == 16 else 16
            elif c in b'lLqjzt':
                size = 64
            else:
                break
            i += 1
        conv = fb[i] if i < n else 0
        i += 1
        lit = i
        segs.append((left, plus, space, alt, zero, width, prec, size, conv))
        if not fast:
            continue
        if width < 0 or prec == -2:
            fast = False
            continue
        w = str(width).encode() if width else b''
        if conv == 100 or conv == 105:                       # d i
            if prec >= 0:
                fast = False
                continue
            py += b'%' + (b'-' if left else b'') + (b'+' if plus else b'') + (b' ' if space else b'') + \
                (b'0' if zero else b'') + w + b'd'
            kinds.append(_K_I32 if size == 32 else _K_IW + 10 * size)
        elif conv in b'uxXo':
            if prec >= 0 or alt:
                fast = False
                continue
            py += b'%' + (b'-' if left else b'') + (b'0' if zero else b'') + w + \
                (b'd' if conv == 117 else bytes((conv,)))
            kinds.append(_K_U + 10 * size)
        elif conv == 99:                                     # c (glibc pads with spaces whatever the flags)
            py += b'%' + (b'-' if left else b'') + w + b'c'
            kinds.append(_K_C)
        elif conv == 115:                                    # s (likewise)
            py += b'%' + (b'-' if left else b'') + w + (b'.' + str(prec).encode() if prec >= 0 else b'') + b's'
            kinds.append(_K_S)
        elif conv in b'fFeEgG':
            if alt and conv in b'gG':
                fast = False                                 # glibc's %#g differs from Python's when it carries
                continue
            py += b'%' + (b'-' if left else b'') + (b'+' if plus else b'') + (b' ' if space else b'') + \
                (b'#' if alt else b'') + (b'0' if zero else b'') + w + \
                (b'.' + str(prec).encode() if prec >= 0 else b'') + bytes((conv,))
            kinds.append(_K_F)
        else:
            fast = False
    if lit < n:
        segs.append(fb[lit:])
        py += fb[lit:]
    return (bytes(py) if fast else None), tuple(kinds), tuple(segs)


def R_Format(fmt, args):
    """The bytes printf writes for fmt (a CPtr) and args: glibc's formatting, with the flags - + space # 0, width
    and precision (* too), the length modifiers hh h l ll L q j z t, and the conversions d i u o x X c s f F e E g
    G a A %. Arguments are the Python values of the C# types (section 7 of RUNTIME_HELPERS.md): int (C# int), float
    (double), bool, the typed boxes I64 / U64 / U32 / I16 / U16 / I8 / U8 / Ch / F32, CPtr / Str / str / Ptr<sbyte>
    / None strings. An integer argument is taken at the size its length modifier says, as glibc reads it from the
    va_list (the C# runtime prints the whole C# value, which differs only where the C++ would print a truncated
    value)."""
    fb = _cb(fmt)
    ent = _FC.get(fb)
    if ent is None:
        ent = _fmt_compile(fb)
        if len(_FC) < 20000:
            _FC[fb] = ent
    py, kinds, segs = ent
    if py is not None and len(args) == len(kinds):
        vals = []
        add = vals.append
        for k, a in zip(kinds, args):
            t = type(a)
            if k == _K_F:
                if t is not float:
                    a = _argf(a)
                if a - a != 0.0:
                    break                                    # inf / nan: the full formatter
                add(a)
            elif k == _K_S:
                if t is str:
                    try:
                        add(a.encode('latin-1'))
                        continue
                    except UnicodeEncodeError:
                        pass
                v = _argstr(a)
                if v is None:
                    break
                add(v)
            elif k == _K_I32:
                if t is not int:
                    a = _argint(a)
                if not -2147483648 <= a <= 2147483647:
                    a = ((a + 0x80000000) & 0xFFFFFFFF) - 0x80000000
                add(a)
            elif k == _K_C:
                add((a if t is int else _argint(a)) & 255)
            else:
                size = k // 10
                v = a if t is int else _argint(a)
                if k % 10 == _K_U:
                    add(v & _MASK[size])
                else:
                    add(_SIGNED[size](v))
        else:
            return py % tuple(vals)
    return _format_slow(segs, args)


def _format_slow(segs, args):
    """The full formatter (every spec, every argument value), glibc's output byte for byte."""
    o = bytearray()
    ai = 0
    for seg in segs:
        if type(seg) is bytes:
            o += seg
            continue
        left, plus, space, alt, zero, width, prec, size, conv = seg
        if width == -1:
            width = _argint(args[ai])
            width = ((width + 0x80000000) & 0xFFFFFFFF) - 0x80000000
            ai += 1
            if width < 0:
                left = True
                width = -width
        if prec == -2:
            prec = _argint(args[ai])
            prec = ((prec + 0x80000000) & 0xFFFFFFFF) - 0x80000000
            ai += 1
            if prec < 0:
                prec = -1
        sign = b''
        prefix = b''
        numeric = True
        if ai >= len(args) and conv != 0:
            raise IndexError('printf: too few arguments')
        if conv == 100 or conv == 105:                       # d i
            v = _SIGNED[size](_argint(args[ai]))
            ai += 1
            if v < 0:
                sign = b'-'
            elif plus:
                sign = b'+'
            elif space:
                sign = b' '
            body = b'%d' % (-v if v < 0 else v)
            if prec >= 0:
                zero = False
                if prec == 0 and v == 0:
                    body = b''
                body = body.rjust(prec, b'0')
        elif conv in b'uxXo':
            u = _argint(args[ai]) & _MASK[size]
            ai += 1
            body = b'%d' % u if conv == 117 else (b'%o' % u if conv == 111 else (b'%x' % u if conv == 120 else b'%X' % u))
            if prec >= 0:
                zero = False
                if prec == 0 and u == 0:
                    body = b''
                body = body.rjust(prec, b'0')
            if alt:
                if conv == 111 and (not body or body[0] != 48):
                    body = b'0' + body
                elif conv == 120 and u != 0:
                    prefix = b'0x'
                elif conv == 88 and u != 0:
                    prefix = b'0X'
        elif conv == 99:                                     # c
            numeric = False
            body = bytes((_argint(args[ai]) & 255,))
            ai += 1
        elif conv == 115:                                    # s
            numeric = False
            p = _argstr(args[ai])
            ai += 1
            if p is None:
                body = b'(null)' if prec < 0 or prec >= 6 else b''
            else:
                body = bytes(p[:prec]) if prec >= 0 else bytes(p)
        elif conv in b'fFeEgGaA':
            v = _argf(args[ai])
            ai += 1
            if _q.unpack(_d.pack(v))[0] < 0:
                sign = b'-'
            elif plus:
                sign = b'+'
            elif space:
                sign = b' '
            upper = conv in b'FEGA'
            if v != v or v - v != 0.0:
                body = (b'nan' if v != v else b'inf')
                if upper:
                    body = body.upper()
                zero = False
            elif conv == 97 or conv == 65:
                body = _hexfloat(abs(v), prec, alt)
                prefix = b'0x'
                if upper:
                    body = body.upper()
                    prefix = b'0X'
            else:
                body = R_FormatFloat(abs(v), conv, 6 if prec < 0 else prec, alt)
        else:
            raise ValueError('printf %%%c' % conv)
        m = len(sign) + len(prefix) + len(body)
        if m >= width:
            o += sign + prefix + body
        elif left:
            o += sign + prefix + body + b' ' * (width - m)
        elif zero and numeric:
            o += sign + prefix + b'0' * (width - m) + body
        else:
            o += b' ' * (width - m) + sign + prefix + body
    return bytes(o)


def R_FormatFloat(a, conv, prec, alt):
    """Exact decimal formatting of a finite non-negative double for %f %e %g (and capitals) as bytes, glibc's
    rounding: half to even on the exact binary value (Python's % operator rounds the same way, exactly)."""
    if conv == 102 or conv == 70:
        return (b'%#.*f' if alt else b'%.*f') % (prec, a)
    if conv == 101 or conv == 69:
        return (b'%#.*E' if conv == 69 else b'%#.*e') % (prec, a) if alt else \
            (b'%.*E' if conv == 69 else b'%.*e') % (prec, a)
    P = 1 if prec == 0 else prec
    if not alt:
        return (b'%.*G' if conv == 71 else b'%.*g') % (P, a)
    # %#g as C99 specifies it, with glibc's quirk: when rounding to P digits carries the value up to 10^P from an
    # exponent of P - 1, glibc keeps the fixed form's count of fraction digits (none) in the exponential form:
    # %#g of 999999.5 is "1.e+06", not "1.00000e+06" (%#.3g of 999.5 is "1.e+03")
    X = int((b'%.*e' % (P - 1, a)).split(b'e')[1]) if a != 0.0 else 0
    if X == P and P > 1 and _exp10(a) == P - 1:
        return (b'%#.0E' if conv == 71 else b'%#.0e') % a
    if P > X >= -4:
        return b'%#.*f' % (P - 1 - X, a)
    return (b'%#.*E' if conv == 71 else b'%#.*e') % (P - 1, a)


def _exp10(a):
    """The decimal exponent of the exact value of a finite positive double (floor(log10(a)), exactly)."""
    f = _Fraction(a)
    e = len(str(f.numerator)) - len(str(f.denominator))
    # 10^e <= a < 10^(e+1) after adjusting
    if f < _Fraction(10) ** e:
        e -= 1
    elif f >= _Fraction(10) ** (e + 1):
        e += 1
    return e


def _hexfloat(a, prec, alt):
    """glibc's %a of a non-negative finite double without the 0x prefix: h.hhhp+e with trailing zero digits dropped
    (0p+0 for zero, 0.hhhp-1022 for a subnormal); with a precision, rounded to that many digits, the leading digit
    and the fraction together (half to even, as glibc does in the default rounding mode). The C# runtime rounds the
    fraction alone; glibc is followed."""
    bits = _Q.unpack(_d.pack(a))[0]
    ex = (bits >> 52) & 0x7FF
    man = bits & 0xFFFFFFFFFFFFF
    lead = 1
    if ex == 0:
        lead = 0
        e = -1022 if man else 0
    else:
        e = ex - 1023
    if prec < 0:
        digits = ('%013x' % man).rstrip('0')
    elif prec < 13:
        drop = (13 - prec) * 4
        full = (lead << 52) | man
        keep = full >> drop
        rem = full & ((1 << drop) - 1)
        half = 1 << (drop - 1)
        if rem > half or (rem == half and (keep & 1)):
            keep += 1
        lead = keep >> (prec * 4)
        digits = ('%0*x' % (prec, keep & ((1 << (prec * 4)) - 1))) if prec > 0 else ''
    else:
        digits = ('%013x' % man) + '0' * (prec - 13)
    s = str(lead)
    if digits or alt:
        s += '.' + digits
    return (s + ('p+%d' % e if e >= 0 else 'p-%d' % -e)).encode('ascii')


def R_fprintf(f, fmt, *args):
    b = R_Format(fmt, args)
    if f is None:
        return -1
    f.S.write(b)
    return len(b)


def R_snprintf_CPtr_ul_CPtr_oA(buf, n, fmt, *args):
    """snprintf with an unsigned size n: at most n - 1 bytes and the terminator; the length of the whole output."""
    b = R_Format(fmt, args)
    if n:
        m = len(b) if len(b) < n - 1 else n - 1
        _put(buf, 0, b[:m] + b'\0')
    return len(b)


def R_snprintf_CPtr_l_CPtr_oA(buf, n, fmt, *args):
    return R_snprintf_CPtr_ul_CPtr_oA(buf, n & 0xFFFFFFFFFFFFFFFF, fmt, *args)


def R_snprintf_CPtr_i_CPtr_oA(buf, n, fmt, *args):
    return R_snprintf_CPtr_ul_CPtr_oA(buf, n & 0xFFFFFFFFFFFFFFFF, fmt, *args)


# ------------------------------------------------------------------------------------------------ files

CFile_Open = {}       # the files fopen opened and fclose has not closed (an ordered set): exit() flushes them


class CFile:
    """C's FILE* (C# Cpp.CFile): S, the stream (a Python binary file object, or a CStdStream for a standard
    stream: write, read, readline, seek, tell, flush, close), the end-of-file flag Eof and IsStd. A file is opened
    with a 64 KiB buffer, as the C# runtime's BufferedStream over a FileStream: reads, writes and seeks interleave
    and the position is the logical one. Dir: a directory opened with fopen(dir, "r"): glibc opens it, and every
    read then fails with EISDIR, which sets the error indicator Err, not end-of-file (C# Cpp.CFile.Dir)."""
    __slots__ = ('S', 'Eof', 'IsStd', 'floor', 'Dir', 'Err', '__weakref__')

    def __init__(self, s, std=False):
        self.S = s
        self.Eof = False
        self.IsStd = std
        self.floor = 0            # append mode: no seeking before the length at open (.NET's FileMode.Append)
        self.Dir = False
        self.Err = False
        if not std:
            CFile_Open[self] = None

    def ReadFailed(self):
        """A read found no more data: end-of-file, or for a directory an error."""
        if self.Dir:
            self.Err = True
        else:
            self.Eof = True


class _NullStream:
    """.NET's Stream.Null as a CFile's stream (the stream of a directory opened for reading): reads give nothing,
    writes are discarded, seeks stay at 0."""
    __slots__ = ()

    def write(self, b):
        return len(b)

    def read(self, n=-1):
        return b''

    def readline(self, limit=-1):
        return b''

    def seek(self, off, whence=0):
        return 0

    def tell(self):
        return 0

    def flush(self):
        pass

    def close(self):
        pass


class CStdStream:
    """A standard stream for a CFile: written through to its file descriptor at once (as C#'s Console streams
    are), read unbuffered; it cannot seek."""
    __slots__ = ('fd', 'pos')

    def __init__(self, fd):
        self.fd = fd
        self.pos = 0

    def write(self, b):
        mv = memoryview(b)
        n = len(mv)
        k = 0
        while k < n:
            k += os.write(self.fd, mv[k:])
        self.pos += n
        return n

    def read(self, n=-1):
        if n is None or n < 0:
            chunks = []
            while True:
                c = os.read(self.fd, 65536)
                if not c:
                    break
                chunks.append(c)
            b = b''.join(chunks)
        else:
            b = bytearray()
            while len(b) < n:
                c = os.read(self.fd, n - len(b))
                if not c:
                    break
                b += c
            b = bytes(b)
        self.pos += len(b)
        return b

    def readline(self, limit=-1):
        out = bytearray()
        while limit < 0 or len(out) < limit:
            c = os.read(self.fd, 1)
            if not c:
                break
            out += c
            if c == b'\n':
                break
        self.pos += len(out)
        return bytes(out)

    def seek(self, off, whence=0):
        raise OSError('seek on a standard stream')

    def tell(self):
        return self.pos

    def flush(self):
        pass

    def close(self):
        pass


R_stdout_ = CFile(CStdStream(1), True)


def R_fopen(name, mode):
    """fopen as the C# runtime does it: r (r+: read and write) an existing file, w (w+) a new or truncated file, a
    (a+ alike: .NET's Append is write-only) an append-only file that cannot seek before its old end; b and t are
    ignored; None when the file cannot be opened. A directory opened with "r" opens, as glibc opens it, and its
    reads fail (CFile.Dir)."""
    fn = _cb(name).decode('latin-1')
    m = _cb(mode)
    if not m:
        return None
    plus = 43 in m
    try:
        c = m[0]
        if c == 114 and not plus and os.path.isdir(fn):      # r on a directory (C#: Directory.Exists)
            d = CFile(_NullStream())
            d.Dir = True
            return d
        if c == 114:                                          # r: an existing file (r+ fails on a directory)
            f = open(fn, 'r+b' if plus else 'rb', buffering=1 << 16)
            return CFile(f)
        if c == 119:                                          # w
            f = open(fn, 'w+b' if plus else 'wb', buffering=1 << 16)
            return CFile(f)
        if c == 97:                                           # a
            if os.path.isdir(fn):
                return None
            fd = os.open(fn, os.O_WRONLY | os.O_CREAT, 0o666)
            f = open(fd, 'wb', buffering=1 << 16)
            f.seek(0, 2)
            cf = CFile(f)
            cf.floor = f.tell()
            return cf
        return None
    except (OSError, ValueError):
        return None


def R_fclose(f):
    if f is None:
        return -1
    if not f.IsStd:
        f.S.close()
        CFile_Open.pop(f, None)
    else:
        f.S.flush()
    return 0


def _std(name):
    cpp = sys.modules.get('cpp')
    return getattr(cpp, name, None) if cpp is not None else None


def R_FlushAll():
    """What C's exit() does to open streams: flush every one (stdout and stderr too)."""
    for f in list(CFile_Open):
        try:
            f.S.flush()
        except Exception:
            pass
    for f in (R_stdout_, _std('R_stderr_')):
        try:
            if f is not None:
                f.S.flush()
        except Exception:
            pass


def R_fflush(f):
    """fflush; fflush(NULL) flushes every stream, as glibc does (the C# runtime does nothing for null)."""
    if f is None:
        R_FlushAll()
    else:
        f.S.flush()
    return 0


def R_feof(f):
    return 1 if f.Eof else 0


def R_rewind(f):
    f.S.seek(0)
    f.Eof = False


def R_fseek_CFile_l_i(f, off, whence):
    """fseek: 0, or -1 when the position would be negative (or before the start of an append) or the stream cannot
    seek; it clears the end-of-file flag."""
    try:
        S = f.S
        if whence == 0:
            p = off
        elif whence == 1:
            p = S.tell() + off
        else:
            p = S.seek(0, 2) + off
        if p < f.floor:
            return -1
        S.seek(p, 0)
        f.Eof = False
        return 0
    except (OSError, ValueError):
        return -1


R_fseek_CFile_i_i = R_fseek_CFile_l_i


def R_ftell(f):
    return f.S.tell()


def R_fgets(buf, n, f):
    """fgets: up to n - 1 bytes, to and including a newline; a null pointer when nothing was read (end of file)."""
    if n <= 1:
        return _cz()
    line = f.S.readline(n - 1)
    if len(line) < n - 1 and not line.endswith(b'\n'):
        f.ReadFailed()
    if not line:
        return _cz()
    _put(buf, 0, line + b'\0')
    return buf


def R_remove(name):
    """remove: 0 when the file was deleted, -1 when it could not be (as glibc; .NET's File.Delete also succeeds for
    a missing file)."""
    try:
        os.remove(_cb(name).decode('latin-1'))
        return 0
    except OSError:
        return -1


_tmp_counter = [0]


def R_mkstemp(template):
    """mkstemp: replaces the template's last six characters and creates the file; a stand-in descriptor (3)."""
    import random
    n = R_strlen(template)
    _tmp_counter[0] += 1
    rnd = random.Random(os.getpid() ^ _time.time_ns() ^ (_tmp_counter[0] * 7919))
    chars = b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    for _ in range(1000):
        _put(template, n - 6, bytes(rnd.choice(chars) for _ in range(6)))
        try:
            fd = os.open(_cb(template).decode('latin-1'), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
            return 3
        except FileExistsError:
            continue
        except OSError:
            return -1
    return -1


def R_fwriteF1(v, f):
    f.S.write(_f.pack(v))
    return 1


def R_fwriteD1(v, f):
    f.S.write(_d.pack(v))
    return 1


def R_fwriteI1(v, f):
    f.S.write(_I4.pack(v & 0xFFFFFFFF))
    return 1


def R_fwriteL1(v, f):
    f.S.write(_Q.pack(v & 0xFFFFFFFFFFFFFFFF))
    return 1


def _read_exact(f, n):
    b = f.S.read(n)
    if len(b) < n:
        f.ReadFailed()
        return None
    return b


def R_freadF1(r, f):
    """fread of one float into the Ref r: 1, or 0 (r unchanged, end of file set) on a short read."""
    b = _read_exact(f, 4)
    if b is None:
        return 0
    r.v = _f.unpack(b)[0]
    return 1


def R_freadD1(r, f):
    b = _read_exact(f, 8)
    if b is None:
        return 0
    r.v = _d.unpack(b)[0]
    return 1


def R_freadI1(r, f):
    b = _read_exact(f, 4)
    if b is None:
        return 0
    r.v = _i4.unpack(b)[0]
    return 1


def R_freadL1(r, f):
    b = _read_exact(f, 8)
    if b is None:
        return 0
    r.v = _q.unpack(b)[0]
    return 1


# ------------------------------------------------------------------------------------------------ time

def R_time():
    return int(_time.time() // 1)


def R_time_Ptrl(p):
    """time(p): the seconds since the epoch, also stored at p unless p is null."""
    t = int(_time.time() // 1)
    if not p.get_IsNull():
        p.set_Item_i(0, t)
    return t


def R_difftime(end, start):
    return float(end - start)


_DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_MONS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def R_ctime_l(t):
    """ctime: "Www Mmm dd hh:mm:ss yyyy\\n" in the local time zone (asctime's format), as a fresh C string."""
    d = _time.localtime(t)
    s = '%s %s %2d %02d:%02d:%02d %d\n' % (_DAYS[d.tm_wday], _MONS[d.tm_mon - 1], d.tm_mday, d.tm_hour, d.tm_min,
                                           d.tm_sec, d.tm_year)
    return R_Lit(s)


def R_ctime_Ptrl(p):
    return R_ctime_l(p.get_Item_i(0))


# ------------------------------------------------------------------------------------------------ package hooks

def _package_init():
    """Called by the package's __init__ once every module is imported: binds part A's CPtr, CPtr_ZERO and Str."""
    _bind()


try:                    # part D's class RFs (the C functions of Fs.cs): a module the package imports earlier
    from .rfs import RFs as _RFs
except ImportError:     # this module imported on its own
    _RFs = object


class R(_RFs):
    """C# Cpp.R as a class of static members (Java's R extends RFs); the engine calls the module-level names."""
    __slots__ = ()


def _r_members():
    g = globals()
    for name in __all__:
        if name.startswith('R_') and name not in ('R_stdout_', 'R_Slot'):
            setattr(R, name[2:], staticmethod(g[name]))
    R.stdout_ = R_stdout_
    R.Slot = R_Slot


_r_members()
__all__.append('R')
