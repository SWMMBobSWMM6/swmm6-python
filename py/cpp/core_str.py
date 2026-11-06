# core_str.py - part A of the Python runtime (package cpp): std::string (cs/runtime/Str.cs: Str, a mutable byte
# sequence, so lengths, comparisons and printf padding count bytes as libstdc++ does) and the C++ standard exception
# classes (std::exception and its library subclasses, from cs/runtime/Std.cs). The engine reads s.b[i] (the
# bytearray buffer, bytes 0..n-1) and s.n (the length) directly; b's length is the capacity plus one, as the C#'s
# byte[] is (Str() starts with 16 bytes, growth doubles), and c_str() hands out a NUL-terminated CPtr over b.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

from .core_num import ICopy, ArrRef, Cell
from .core_ptr import CPtr, Iter, Ptr_ZERO, buf_bytes

__all__ = ['Str', 'Str_npos', 'NewStr', 'NewStr_Str', 'NewStr_CPtr', 'NewStr_CPtr_ul', 'NewStr_ul_i', 'NewStr_str',
           'NewStr_Str_ul_ul', 'NewStr_ucA_i_i', 'Str_from_CPtr', 'Str_FromBytes', 'Str_of',
           'Str_Cat_Str_Str', 'Str_Cat_Str_CPtr', 'Str_Cat_CPtr_Str', 'Str_Cat_Str_i', 'Str_Cat_i_Str',
           'Str_to_string_i', 'Str_to_string_l', 'Str_to_string_ul', 'Str_to_string_u', 'Str_to_string_d',
           'Str_to_string_f', 'Str_stod', 'Str_stoi', 'Str_stol', 'fmt_f',
           'CppException', 'LogicError', 'RuntimeError', 'InvalidArgument', 'OutOfRange', 'LengthError',
           'DomainError', 'RangeError', 'OverflowError', 'BadAlloc', 'BadOptionalAccess', 'BadFunctionCall',
           'BadVariantAccess', 'NewCppException', 'NewCppException_Str',
           'NewLogicError_Str', 'NewRuntimeError_Str', 'NewInvalidArgument_Str', 'NewOutOfRange_Str',
           'NewLengthError_Str', 'NewDomainError_Str', 'NewRangeError_Str', 'NewOverflowError_Str',
           'NewLogicError_str', 'NewRuntimeError_str', 'NewInvalidArgument_str', 'NewOutOfRange_str',
           'NewLengthError_str', 'NewDomainError_str', 'NewRangeError_str', 'NewOverflowError_str',
           'NewBadAlloc', 'NewBadOptionalAccess', 'NewBadFunctionCall', 'NewBadVariantAccess']

NPOS = 0xFFFFFFFFFFFFFFFF
Str_npos = NPOS
_new = object.__new__


def _cb(p):
    """The bytes of the C string at CPtr p (to its NUL), values 0..255."""
    B = p.B
    O = p.O
    e = B.index(0, O)
    if type(B) is bytearray:
        return B[O:e]
    return bytes([x & 0xFF for x in B[O:e]])


def _S(b, n):
    """A Str over buffer b (a bytearray of at least n + 1 bytes) holding n bytes."""
    s = _new(Str)
    s.b = b
    s.n = n
    return s


def _own(src, m):
    """A fresh buffer of m + 1 bytes holding src (m bytes) and a zero: C#'s new byte[m + 1] and Array.Copy."""
    t = bytearray(src)
    t.append(0)
    return t


class Str(ICopy):
    """std::string as bytes (C# Cpp.Str): b, a bytearray whose length is the capacity plus one (b[n] == 0 whenever
    c_str() has been taken), and n, the length. Element access is s.b[i] (C#'s ref byte this[i])."""
    __slots__ = ('b', 'n')

    def __init__(self):
        self.b = bytearray(16)
        self.n = 0

    def _reserve(self, need):
        """Str.Reserve: room for need bytes plus the NUL; growth doubles the buffer (a new one, like Array.Resize,
        which copies the whole old buffer)."""
        b = self.b
        lb = len(b)
        if need + 1 <= lb:
            return b
        cap = lb * 2
        if cap < need + 1:
            cap = need + 1
        nb = bytearray(cap)
        nb[0:lb] = b
        self.b = nb
        return nb

    # ---- size and access
    def size(self):
        return self.n

    def length(self):
        return self.n

    def empty(self):
        return self.n == 0

    def clear(self):
        self.n = 0

    def c_str(self):
        n = self.n
        b = self.b
        if n + 1 > len(b):
            b = self._reserve(n)
        b[n] = 0
        return CPtr(b, 0)

    data = c_str

    def at(self, i):
        if i >= self.n:
            raise OutOfRange('basic_string::at: __n (which is %d) >= this->size() (which is %d)' % (i, self.n))
        return self.b[i]

    def front(self):
        return self.b[0]

    def back(self):
        return self.b[self.n - 1]

    def ref_front(self):
        return ArrRef(self.b, 0)

    def ref_back(self):
        return ArrRef(self.b, self.n - 1)

    def get_Item(self, i):
        return self.b[i]

    def set_Item(self, i, v):
        self.b[i] = v & 0xFF

    def ref_Item(self, i):
        return ArrRef(self.b, i)

    get_Item_ul = get_Item_l = get_Item_i = get_Item
    set_Item_ul = set_Item_l = set_Item_i = set_Item
    ref_Item_ul = ref_Item_l = ref_Item_i = ref_Item

    def Char(self, i):
        """The byte at i as a signed char."""
        x = self.b[i]
        return x - 256 if x > 127 else x

    # ---- copy and move
    def Clone(self):
        n = self.n
        t = self.b[:n]
        t.append(0)
        return _S(t, n)

    def Assign_Str(self, o):
        if o is self:
            return
        m = o.n
        b = self.b
        if m + 1 > len(b):
            b = self._reserve(m)
        b[0:m] = o.b[0:m]
        self.n = m

    Assign = Assign_Str           # ICopy<Str>.Assign

    def Assign_CPtr(self, p):
        src = _cb(p)
        m = len(src)
        self.b = _own(src, m)
        self.n = m

    def Assign_i(self, ch):
        b = self.b
        if 2 > len(b):
            b = self._reserve(1)
        b[0] = ch & 0xFF
        self.n = 1

    def MoveFrom(self, o):
        if o is self:
            return
        self.b = o.b
        self.n = o.n
        o.b = bytearray(16)
        o.n = 0

    MoveFrom_Str = MoveFrom

    def assign_Str(self, o):
        self.Assign_Str(o)
        return self

    def assign_CPtr(self, p):
        self.Assign_CPtr(p)
        return self

    def assign_CPtr_ul(self, p, ln):
        # (no bytes are read for a length of 0: the pointer may be null, as libstdc++ allows)
        self.b = _own(buf_bytes(p.B, p.O, ln) if ln != 0 else b'', ln)
        self.n = ln
        return self

    def assign_CPtr_CPtr(self, b0, e0):
        """assign(first, last) of two char pointers."""
        return self.assign_CPtr_ul(b0, e0.O - b0.O)

    def assign_Ptrc_Ptrc(self, b0, e0):
        """assign(first, last) of two Ptr<sbyte> (vector<char> iterators): the same bytes as char pointers."""
        return self.assign_CPtr_ul(CPtr(b0.a, b0.o), e0.o - b0.o)

    def assign_ul_i(self, count, ch):
        b = self._reserve(count)
        b[0:count] = bytes((ch & 0xFF,)) * count
        self.n = count
        return self

    # ---- append and edit
    def append_Str(self, o):
        n = self.n
        m = o.n
        b = self.b
        if n + m + 1 > len(b):
            b = self._reserve(n + m)
        b[n:n + m] = o.b[0:m]
        self.n = n + m
        return self

    def append_Str_ul_ul(self, o, pos, ln):
        n = self.n
        m = o.n - pos
        if m < 0:
            raise IndexError('basic_string::append: position beyond the string')
        if ln < m:
            m = ln
        b = self._reserve(n + m)
        b[n:n + m] = o.b[pos:pos + m]
        self.n = n + m
        return self

    def append_CPtr(self, p):
        src = _cb(p)
        n = self.n
        m = len(src)
        b = self.b
        if n + m + 1 > len(b):
            b = self._reserve(n + m)
        b[n:n + m] = src
        self.n = n + m
        return self

    def append_CPtr_ul(self, p, ln):
        if ln == 0:                 # nothing, even at a null pointer
            return self
        src = buf_bytes(p.B, p.O, ln)
        n = self.n
        b = self.b
        if n + ln + 1 > len(b):
            b = self._reserve(n + ln)
        b[n:n + ln] = src
        self.n = n + ln
        return self

    def append_ul_i(self, count, ch):
        n = self.n
        b = self._reserve(n + count)
        b[n:n + count] = bytes((ch & 0xFF,)) * count
        self.n = n + count
        return self

    def append_i(self, ch):
        n = self.n
        b = self.b
        if n + 2 > len(b):
            b = self._reserve(n + 1)
        b[n] = ch & 0xFF
        self.n = n + 1
        return self

    def push_back(self, ch):
        n = self.n
        b = self.b
        if n + 2 > len(b):
            b = self._reserve(n + 1)
        b[n] = ch & 0xFF
        self.n = n + 1

    def pop_back(self):
        self.n -= 1

    def resize(self, m, ch=0):
        n = self.n
        b = self._reserve(m)
        if m > n:
            b[n:m] = bytes((ch & 0xFF,)) * (m - n)
        self.n = m

    resize_ul_i = resize
    resize_ul = resize

    def reserve(self, m):
        self._reserve(m)

    def shrink_to_fit(self):
        pass

    def remove_prefix(self, k):
        """string_view::remove_prefix (a view is a Str of its own here)."""
        n = self.n
        b = self.b
        b[0:n - k] = b[k:n]
        n -= k
        b[n] = 0
        self.n = n

    def remove_suffix(self, k):
        n = self.n - k
        self.b[n] = 0
        self.n = n

    def substr(self, pos=0, ln=NPOS):
        n = self.n
        if pos > n:
            raise OutOfRange('basic_string::substr: __pos (which is %d) > this->size() (which is %d)' % (pos, n))
        m = n - pos
        if ln < m:
            m = ln
        t = self.b[pos:pos + m]
        t.append(0)
        return _S(t, m)

    def erase(self, pos=0, ln=NPOS):
        n = self.n
        m = n - pos
        if ln < m:
            m = ln
        b = self.b
        b[pos:n - m] = b[pos + m:n]
        self.n = n - m
        return self

    def insert_ul_Str(self, pos, s):
        n = self.n
        sn = s.n
        b = self._reserve(n + sn)
        b[pos + sn:n + sn] = b[pos:n]
        b[pos:pos + sn] = s.b[0:sn]
        self.n = n + sn
        return self

    def insert_ul_CPtr(self, pos, p):
        return self.insert_ul_Str(pos, NewStr_CPtr(p))

    def insert_ul_ul_i(self, pos, count, ch):
        return self.insert_ul_Str(pos, NewStr_ul_i(count, ch))

    def replace_ul_ul_Str(self, pos, ln, s):
        n = self.n
        m = n - pos
        if ln < m:
            m = ln
        b = self.b
        t = b[0:pos]
        t += s.b[0:s.n]
        t += b[pos + m:n]
        t.append(0)
        self.b = t
        self.n = n - m + s.n
        return self

    def replace_ul_ul_CPtr(self, pos, ln, p):
        return self.replace_ul_ul_Str(pos, ln, NewStr_CPtr(p))

    # ---- search (npos is 2^64 - 1)
    def _find(self, sub, pos):
        n = self.n
        if len(sub) == 0:
            return pos if pos <= n else NPOS
        r = self.b.find(sub, pos, n)
        return NPOS if r < 0 else r

    def find_Str_ul(self, s, pos=0):
        return self._find(s.b[0:s.n], pos)

    def find_CPtr_ul(self, p, pos=0):
        return self._find(_cb(p), pos)

    def find_i_ul(self, ch, pos=0):
        if pos >= self.n:
            return NPOS
        r = self.b.find(ch & 0xFF, pos, self.n)
        return NPOS if r < 0 else r

    def _rfind(self, sub, pos):
        n = self.n
        m = len(sub)
        if m > n:
            return NPOS
        start = n - m
        if pos < start:
            start = pos
        r = self.b.rfind(sub, 0, start + m)
        return NPOS if r < 0 else r

    def rfind_Str_ul(self, s, pos=NPOS):
        return self._rfind(s.b[0:s.n], pos)

    def rfind_CPtr_ul(self, p, pos=NPOS):
        return self._rfind(_cb(p), pos)

    def rfind_i_ul(self, ch, pos=NPOS):
        n = self.n
        if n == 0:
            return NPOS
        i = n - 1
        if pos < i:
            i = pos
        r = self.b.rfind(ch & 0xFF, 0, i + 1)
        return NPOS if r < 0 else r

    def _first_of(self, st, pos):
        b = self.b
        for i in range(pos, self.n):
            if b[i] in st:
                return i
        return NPOS

    def _first_not_of(self, st, pos):
        n = self.n
        if pos >= n:
            return NPOS
        seg = self.b[pos:n]
        k = pos + len(seg) - len(seg.lstrip(st))
        return k if k < n else NPOS

    def _last_of(self, st, pos):
        n = self.n
        if n == 0:
            return NPOS
        i = n - 1
        if pos < i:
            i = pos
        b = self.b
        while i >= 0:
            if b[i] in st:
                return i
            i -= 1
        return NPOS

    def _last_not_of(self, st, pos):
        n = self.n
        if n == 0:
            return NPOS
        i = n - 1
        if pos < i:
            i = pos
        r = self.b[0:i + 1].rstrip(st)
        return len(r) - 1 if len(r) else NPOS

    def find_first_of_Str_ul(self, s, pos=0):
        return self._first_of(bytes(s.b[0:s.n]), pos)

    def find_first_of_CPtr_ul(self, p, pos=0):
        return self._first_of(bytes(_cb(p)), pos)

    def find_first_of_i_ul(self, ch, pos=0):
        return self.find_i_ul(ch, pos)

    def find_first_not_of_Str_ul(self, s, pos=0):
        return self._first_not_of(bytes(s.b[0:s.n]), pos)

    def find_first_not_of_CPtr_ul(self, p, pos=0):
        return self._first_not_of(bytes(_cb(p)), pos)

    def find_first_not_of_i_ul(self, ch, pos=0):
        return self._first_not_of(bytes((ch & 0xFF,)), pos)

    def find_last_of_Str_ul(self, s, pos=NPOS):
        return self._last_of(bytes(s.b[0:s.n]), pos)

    def find_last_of_CPtr_ul(self, p, pos=NPOS):
        return self._last_of(bytes(_cb(p)), pos)

    def find_last_of_i_ul(self, ch, pos=NPOS):
        return self.rfind_i_ul(ch, pos)

    def find_last_not_of_Str_ul(self, s, pos=NPOS):
        return self._last_not_of(bytes(s.b[0:s.n]), pos)

    def find_last_not_of_CPtr_ul(self, p, pos=NPOS):
        return self._last_not_of(bytes(_cb(p)), pos)

    def find_last_not_of_i_ul(self, ch, pos=NPOS):
        return self._last_not_of(bytes((ch & 0xFF,)), pos)

    def starts_with_Str(self, s):
        m = s.n
        return m <= self.n and self.b.startswith(s.b[0:m])

    def starts_with_CPtr(self, p):
        sub = _cb(p)
        return len(sub) <= self.n and self.b.startswith(sub)

    def starts_with_i(self, ch):
        return self.n > 0 and self.b[0] == (ch & 0xFF)

    def ends_with_Str(self, s):
        m = s.n
        return m <= self.n and self.b.endswith(s.b[0:m], 0, self.n)

    def ends_with_CPtr(self, p):
        sub = _cb(p)
        return len(sub) <= self.n and self.b.endswith(sub, 0, self.n)

    def ends_with_i(self, ch):
        return self.n > 0 and self.b[self.n - 1] == (ch & 0xFF)

    def contains_Str(self, s):
        return self.find_Str_ul(s, 0) != NPOS

    def contains_i(self, ch):
        return self.find_i_ul(ch, 0) != NPOS

    # ---- comparison (char_traits<char>::compare is memcmp: unsigned bytes, then the length difference)
    def compare_Str(self, o):
        n = self.n
        on = o.n
        m = n if n < on else on
        x = self.b[0:m]
        y = o.b[0:m]
        if x != y:
            return -1 if x < y else 1
        d = n - on
        return 2147483647 if d > 2147483647 else (-2147483648 if d < -2147483648 else d)

    def compare_CPtr(self, p):
        y = _cb(p)
        n = self.n
        on = len(y)
        m = n if n < on else on
        x = self.b[0:m]
        y = y[0:m]
        if x != y:
            return -1 if x < y else 1
        d = n - on
        return 2147483647 if d > 2147483647 else (-2147483648 if d < -2147483648 else d)

    def compare_ul_ul_Str(self, pos, ln, o):
        return self.substr(pos, ln).compare_Str(o)

    def CompareTo(self, o):
        return self.compare_Str(o)

    def Equals_Str(self, o):
        if o is self:
            return True
        if o is None:
            return False
        n = self.n
        return o.n == n and self.b[0:n] == o.b[0:n]

    def Equals(self, o):
        return isinstance(o, Str) and self.Equals_Str(o)

    Equals_o = Equals

    def __eq__(self, o):
        return isinstance(o, Str) and self.Equals_Str(o)

    def __ne__(self, o):
        return not self.__eq__(o)

    def __hash__(self):
        return hash(bytes(self.b[0:self.n]))

    def GetHashCode(self):
        """C#'s GetHashCode (h = h * 31 + byte, wrapping at 32 bits); std::hash is StdHash's, not this."""
        h = self.n
        for c in self.b[0:self.n]:
            h = (h * 31 + c) & 0xFFFFFFFF
        return h - 0x100000000 if h > 0x7FFFFFFF else h

    def EqLit(self, p):
        sub = _cb(p)
        n = self.n
        return len(sub) == n and self.b[0:n] == sub

    # ---- conversions
    def Bytes(self):
        """A copy of the n bytes (a bytearray)."""
        return self.b[0:self.n]

    def ToString(self):
        return self.b[0:self.n].decode('latin-1')

    __str__ = ToString

    def __repr__(self):
        return 'Str(%r)' % (self.b[0:self.n].decode('latin-1'),)

    def begin(self):
        return Iter(None, self.b, 0)

    def end(self):
        return Iter(None, self.b, self.n)

    cbegin = begin
    cend = end


# ---------------------------------------------------------------------------------------------------- constructors

NewStr = Str                       # new Str() (also the maker of a Str element)


def NewStr_Str(s):
    """The copy constructor."""
    n = s.n
    t = s.b[0:n]
    t.append(0)
    return _S(t, n)


def NewStr_CPtr(p):
    """const char* -> std::string: the bytes to the NUL (the buffer is exactly length + 1, as the C#'s)."""
    B = p.B
    O = p.O
    e = B.index(0, O)
    if type(B) is bytearray:
        return _S(B[O:e + 1], e - O)
    return _S(bytearray([x & 0xFF for x in B[O:e + 1]]), e - O)


Str_from_CPtr = NewStr_CPtr        # the implicit conversion C++ applies


def NewStr_CPtr_ul(p, ln):
    """std::string(const char*, n): n bytes (NULs included; none read, at a pointer that may be null, for n = 0)."""
    return _S(_own(buf_bytes(p.B, p.O, ln) if ln != 0 else b'', ln), ln)


def NewStr_ul_i(count, ch):
    """std::string(count, ch)."""
    t = bytearray((ch & 0xFF,)) * count
    t.append(0)
    return _S(t, count)


def NewStr_Str_ul_ul(s, pos, ln=NPOS):
    """std::string(s, pos, len)."""
    m = s.n - pos
    if ln < m:
        m = ln
    t = s.b[pos:pos + m]
    t.append(0)
    return _S(t, m)


def NewStr_ucA_i_i(src, off, ln):
    """new Str(byte[] src, int off, int len) (src is not read, and may be null, when len is 0)."""
    return _S(_own(buf_bytes(src, off, ln) if ln != 0 else b'', ln), ln)


def NewStr_str(text):
    """new Str(string): the Latin-1 bytes of a C# string (a character beyond Latin-1 becomes '?', as .NET's
    Latin1 encoding replaces it)."""
    e = text.encode('latin-1', 'replace')
    t = bytearray(e)
    t.append(0)
    return _S(t, len(e))


Str_of = NewStr_str


def Str_FromBytes(x):
    """Str.FromBytes(byte[]): a Str holding a copy of x (bytes, bytearray or a list of byte values)."""
    m = len(x)
    return _S(_own(buf_bytes(x, 0, m), m), m)


# ---------------------------------------------------------------------------------------------------- statics

def Str_Cat_Str_Str(x, y):
    n = x.n
    m = y.n
    t = x.b[0:n]
    t += y.b[0:m]
    t.append(0)
    return _S(t, n + m)


def Str_Cat_Str_CPtr(x, y):
    n = x.n
    t = x.b[0:n]
    src = _cb(y)
    t += src
    t.append(0)
    return _S(t, n + len(src))


def Str_Cat_CPtr_Str(x, y):
    src = _cb(x)
    t = bytearray(src)
    t += y.b[0:y.n]
    t.append(0)
    return _S(t, len(src) + y.n)


def Str_Cat_Str_i(x, ch):
    n = x.n
    t = x.b[0:n]
    t.append(ch & 0xFF)
    t.append(0)
    return _S(t, n + 1)


def Str_Cat_i_Str(ch, y):
    t = bytearray((ch & 0xFF,))
    t += y.b[0:y.n]
    t.append(0)
    return _S(t, y.n + 1)


def _ascii(text):
    t = bytearray(text, 'ascii')
    n = len(t)
    t.append(0)
    return _S(t, n)


def Str_to_string_i(v):
    return _ascii(str(v))


Str_to_string_l = Str_to_string_i
Str_to_string_ul = Str_to_string_i
Str_to_string_u = Str_to_string_i


def fmt_f(v):
    """glibc's "%f" of a double: Python's '%f' (both correctly rounded from the exact binary value, ties to even),
    with glibc's text for NaN ("nan", "-nan" with the sign bit set)."""
    if v != v:
        from .core_num import signbit
        return '-nan' if signbit(v) else 'nan'
    return '%f' % v


def Str_to_string_d(v):
    """std::to_string(double): libstdc++ formats it with "%f"."""
    return _ascii(fmt_f(v))


Str_to_string_f = Str_to_string_d

_strtod = _erange = None


def Str_stod(s, idx=Ptr_ZERO):
    """std::stod (libstdc++'s __stoa over strtod, part C's R.strtod): invalid_argument("stod") when nothing
    converts, out_of_range("stod") when strtod sets ERANGE (R.ERange: overflow, or an underflow such as "1e-400" or
    the inexact subnormal 2.2250738585072011e-308); idx, unless null, receives the number of bytes used."""
    global _strtod, _erange
    if _strtod is None:
        from . import R_strtod_CPtr_CPtrr as f, R_get_ERange as g
        _strtod = f
        _erange = g
    p = s.c_str()
    end = Cell(p)
    v = _strtod(p, end)
    if end.v.O == p.O:
        raise InvalidArgument('stod')
    if _erange():
        raise OutOfRange('stod')
    if not idx.get_IsNull():
        idx.set_Deref(end.v.O - p.O)
    return v


def _strtol(p):
    """C#'s R.strtol (base 10 whatever base is asked): spaces, a sign, digits; (value clamped to long, end offset),
    end None when no digit follows."""
    B = p.B
    i = p.O
    while B[i] in (32, 9, 10, 11, 12, 13):
        i += 1
    neg = False
    if B[i] == 43 or B[i] == 45:
        neg = B[i] == 45
        i += 1
    st = i
    v = 0
    while 48 <= B[i] <= 57:
        v = v * 10 + (B[i] - 48)
        i += 1
    if i == st:
        return 0, None
    if neg:
        v = -v
    if v > 9223372036854775807:
        v = 9223372036854775807
    elif v < -9223372036854775808:
        v = -9223372036854775808
    return v, i


def Str_stoi(s, idx=Ptr_ZERO, bas=10):
    """std::stoi: strtol, invalid_argument when nothing converts, out_of_range beyond int."""
    p = s.c_str()
    v, e = _strtol(p)
    if e is None:
        raise InvalidArgument('stoi')
    if v < -2147483648 or v > 2147483647:
        raise OutOfRange('stoi')
    if not idx.get_IsNull():
        idx.set_Deref(e - p.O)
    return v


def Str_stol(s, idx=Ptr_ZERO, bas=10):
    """std::stol."""
    p = s.c_str()
    v, e = _strtol(p)
    if e is None:
        raise InvalidArgument('stol')
    if not idx.get_IsNull():
        idx.set_Deref(e - p.O)
    return v


# ---------------------------------------------------------------------------------------------------- exceptions
# std::exception and the library's subclasses. An engine class deriving from one calls CppException.__init__(self)
# with no arguments (the message is then "std::exception", as new CppException() gives). catch (System.Exception)
# is except Exception. Note that RuntimeError and OverflowError here are the C++ classes, not Python's.

class CppException(Exception):
    """std::exception (C# Cpp.CppException): msg, a Str; what() gives msg.c_str(); the .NET Message (str(e)) is its
    text."""
    __slots__ = ('msg',)

    def __init__(self, m=None):
        if m is None:
            s = NewStr_str('std::exception')
        elif type(m) is str:
            s = NewStr_str(m)
        else:
            s = m.Clone()
        Exception.__init__(self, s.ToString())
        self.msg = s

    def what(self):
        return self.msg.c_str()

    @property
    def Message(self):
        return self.args[0] if self.args else ''


class LogicError(CppException):
    """std::logic_error."""
    __slots__ = ()


class RuntimeError(CppException):
    """std::runtime_error (not Python's RuntimeError)."""
    __slots__ = ()


class InvalidArgument(LogicError):
    """std::invalid_argument."""
    __slots__ = ()


class OutOfRange(LogicError):
    """std::out_of_range."""
    __slots__ = ()


class LengthError(LogicError):
    """std::length_error."""
    __slots__ = ()


class DomainError(LogicError):
    """std::domain_error."""
    __slots__ = ()


class RangeError(RuntimeError):
    """std::range_error."""
    __slots__ = ()


class OverflowError(RuntimeError):
    """std::overflow_error (not Python's OverflowError)."""
    __slots__ = ()


class BadAlloc(CppException):
    """std::bad_alloc."""
    __slots__ = ()

    def __init__(self, m=None):
        CppException.__init__(self, 'std::bad_alloc' if m is None else m)


class BadOptionalAccess(CppException):
    """std::bad_optional_access."""
    __slots__ = ()

    def __init__(self, m=None):
        CppException.__init__(self, 'bad optional access' if m is None else m)


class BadFunctionCall(CppException):
    """std::bad_function_call."""
    __slots__ = ()

    def __init__(self, m=None):
        CppException.__init__(self, 'bad_function_call' if m is None else m)


class BadVariantAccess(CppException):
    """std::bad_variant_access (Variant itself is part C's)."""
    __slots__ = ()

    def __init__(self, m=None):
        CppException.__init__(self, 'std::get: wrong index for variant' if m is None else m)


NewCppException = CppException
NewCppException_Str = CppException
NewLogicError_Str = NewLogicError_str = LogicError
NewRuntimeError_Str = NewRuntimeError_str = RuntimeError
NewInvalidArgument_Str = NewInvalidArgument_str = InvalidArgument
NewOutOfRange_Str = NewOutOfRange_str = OutOfRange
NewLengthError_Str = NewLengthError_str = LengthError
NewDomainError_Str = NewDomainError_str = DomainError
NewRangeError_Str = NewRangeError_str = RangeError
NewOverflowError_Str = NewOverflowError_str = OverflowError
NewBadAlloc = BadAlloc
NewBadOptionalAccess = BadOptionalAccess
NewBadFunctionCall = BadFunctionCall
NewBadVariantAccess = BadVariantAccess
