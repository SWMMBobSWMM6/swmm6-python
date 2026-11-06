# streams.py - part D of the Python runtime: a port of cs/runtime/Streams.cs, the iostreams the engine uses, with
# libstdc++'s formatting: a double is written with the printf format libstdc++'s num_put builds from the stream flags
# (%.*g by default, %.*f for fixed, %.*e for scientific, %a for fixed|scientific, '#' for showpoint) through part C's
# glibc-exact formatter (R.Format); width and fill are applied afterwards and width resets. A double is read as
# libstdc++'s num_get reads it (the characters a number can have, converted by strtod; an overflow fails and stores
# +-DBL_MAX); an integer is a token converted by strtol (a token that does not convert whole sets failbit and stores
# 0; an int out of range stores the nearest limit). getline consumes the delimiter; at the end of the input it sets
# eof, and fail too when nothing at all was read. seekg clears eof; on a file, ios::cur is relative to the logical
# read position (the fixed C#). An ifstream on a directory opens, and its first read sets badbit and failbit.
#
# What the C# streams write to and read from (FileStream, MemoryStream, the console) is fsnet.py's .NET behaviour.
# cout and cerr write through to file descriptors 1 and 2 at once, as .NET's console streams do, after flushing the
# C stdio file of the same descriptor (part C's R_stdout_ / R_stderr_), so stream and printf output keep their order.
#
# Names follow py/RUNTIME_HELPERS.md: an overloaded C# method carries the translator's parameter suffix (Put(double) is
# Put_d, get() is get_, precision(long) is precision_l), a constructor is a module function New<Class><sfx>, a static
# member <Class>_<name>. Other parts' names (Str, CPtr, R.Format, R.strtod, ...) are bound on first use (_bind),
# never while this module imports.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
import os
import struct as _struct
import sys

from . import fsnet as _net
from . import rfs as _rt

__all__ = ['Ios_in', 'Ios_out', 'Ios_binary', 'Ios_app', 'Ios_trunc', 'Ios_ate', 'Ios_beg', 'Ios_cur', 'Ios_end',
           'Ios_boolalpha', 'Ios_dec', 'Ios_fixed', 'Ios_hex', 'Ios_internal', 'Ios_left', 'Ios_oct', 'Ios_right',
           'Ios_scientific', 'Ios_showbase', 'Ios_showpoint', 'Ios_showpos', 'Ios_skipws', 'Ios_unitbuf',
           'Ios_uppercase', 'Ios_adjustfield', 'Ios_basefield', 'Ios_floatfield', 'Ios_goodbit', 'Ios_badbit',
           'Ios_eofbit', 'Ios_failbit', 'Ios', 'SetPrec', 'SetW', 'SetFill', 'NewSetPrec', 'NewSetW', 'NewSetFill',
           'IosBase', 'OStream', 'OStringStream', 'NewOStringStream', 'NewOStringStream_Str', 'NewOStringStream_i',
           'OFStream', 'NewOFStream', 'NewOFStream_Str_i', 'NewOFStream_CPtr_i', 'NewOFStream_FsPath_i', 'FStream',
           'NewFStream', 'IStream', 'IStringStream', 'NewIStringStream', 'NewIStringStream_Str',
           'NewIStringStream_Str_i', 'IFStream', 'NewIFStream', 'NewIFStream_Str_i', 'NewIFStream_CPtr_i',
           'NewIFStream_FsPath_i', 'Streams_cout', 'Streams_cerr', 'Streams_clog', 'Streams_getline_IStream_Str',
           'Streams_getline_IStream_Str_c', 'Streams_getline_IStream_Str_i', 'Streams_setprecision',
           'Streams_setw', 'Streams_setfill', 'Streams']

# ------------------------------------------------------------------------------------------------- Cpp.Ios

# the libstdc++ values of ios_base::openmode, seekdir, fmtflags and iostate
Ios_in, Ios_out, Ios_binary, Ios_app, Ios_trunc, Ios_ate = 8, 16, 4, 1, 32, 2
Ios_beg, Ios_cur, Ios_end = 0, 1, 2
Ios_boolalpha, Ios_dec, Ios_fixed, Ios_hex, Ios_internal, Ios_left, Ios_oct, Ios_right = 1, 2, 4, 8, 16, 32, 64, 128
Ios_scientific, Ios_showbase, Ios_showpoint, Ios_showpos, Ios_skipws, Ios_unitbuf = 256, 512, 1024, 2048, 4096, 8192
Ios_uppercase, Ios_adjustfield, Ios_basefield, Ios_floatfield = 16384, 176, 74, 260
Ios_goodbit, Ios_badbit, Ios_eofbit, Ios_failbit = 0, 1, 2, 4


class Ios(object):
    """Cpp.Ios as a namespace (Ios.in_ for the keyword)."""
    __slots__ = ()
    in_, out, binary, app, trunc, ate = Ios_in, Ios_out, Ios_binary, Ios_app, Ios_trunc, Ios_ate
    beg, cur, end = Ios_beg, Ios_cur, Ios_end
    boolalpha, dec, fixed, hex, internal, left, oct, right = 1, 2, 4, 8, 16, 32, 64, 128
    scientific, showbase, showpoint, showpos, skipws, unitbuf = 256, 512, 1024, 2048, 4096, 8192
    uppercase, adjustfield, basefield, floatfield = 16384, 176, 74, 260
    goodbit, badbit, eofbit, failbit = 0, 1, 2, 4


_I32 = 0x80000000
_U32 = 0xFFFFFFFF


def _i32(x):
    return ((x + _I32) & _U32) - _I32


# ------------------------------------------------------------------------------------------------- other parts

try:
    from . import IDisposable as _IDisposable      # part A's interface class (imported earlier by the package)
except ImportError:                                # only when this module is used on its own
    class _IDisposable(object):
        __slots__ = ()

_bound = False
_CPtr = _CPtr_Diff = _strtod = _scan_double = _Format = _FMTS = _fflush = None
_INF = float('inf')
_DBL_MAX = 1.7976931348623157e308


def _bind():
    """Resolve the names of parts A and C this module uses (through the package, which exports every runtime name)."""
    global _bound, _CPtr, _CPtr_Diff, _strtod, _scan_double, _Format, _FMTS, _fflush
    p = sys.modules[__package__]
    _CPtr = _rt.cptr_class()
    _CPtr_Diff = p.CPtr_Diff
    _strtod = p.R_strtod_CPtr_CPtrr
    _scan_double = p.R_ScanDouble
    _fflush = p.R_fflush
    _Format = _rt.formatter()
    lit = p.R_Lit
    # libstdc++'s __num_base::_S_format_float: [showpoint][g, f, e, a]
    _FMTS = ((lit('%.*g'), lit('%.*f'), lit('%.*e'), lit('%a')),
             (lit('%#.*g'), lit('%#.*f'), lit('%#.*e'), lit('%#a')))
    _bound = True


def _std_file(fd):
    """Part C's C stdio FILE for descriptor 1 or 2 (R_stdout_ / R_stderr_), or None."""
    return getattr(sys.modules[__package__], 'R_stdout_' if fd == 1 else 'R_stderr_', None)


# ------------------------------------------------------------------------------------------------- byte helpers

_cbytes = _rt.cbytes          # R.Bytes(CPtr)
_new_str = _rt.new_str        # Str.FromBytes


def _span(b, o, n):
    """n bytes of a buffer from o (Array.Copy / Stream.Write of b, o, n) as a bytes-like object."""
    if o + n > len(b) or o < 0 or n < 0:
        raise _net.sys_exc('ArgumentException', 'Offset and length were out of bounds for the array or count is '
                           'greater than the number of elements from index to the end of the source collection.')
    if isinstance(b, (bytearray, bytes)):
        return memoryview(b)[o:o + n]
    return bytes([x & 255 for x in b[o:o + n]])


# --- the bytes of the numbers a Ptr<T> points at (C# Mem.BytesOf / Mem.BytesInto: MemoryMarshal.AsBytes of the span
# from the pointer to the end of its array, sliced to n bytes): little-endian as x86, ArgumentOutOfRangeException when
# n bytes do not fit, and a write of a partial element changes only its low bytes.

_POD = {'B': (1, 'B'), 'Z': (1, '?'), 'S': (2, 'H'), 'I': (4, 'I'), 'L': (8, 'Q'), 'F': (4, 'f'), 'D': (8, 'd')}
# reading back: the signed view for the integer codes (int, long, short); bytes as unsigned (0..255) so that a byte
# buffer and a char buffer both hold values a CPtr or a Str reads correctly
_POD_IN = {'B': 'B', 'Z': 'B', 'S': 'h', 'I': 'i', 'L': 'q', 'F': 'f', 'D': 'd'}


def _out_of_range():
    return _net.sys_exc('ArgumentOutOfRangeException', 'Specified argument was out of the range of valid values.')


def _pod_start(p, size, n):
    a = p.a
    if a is None:
        raise _net.sys_exc('NullReferenceException', 'Object reference not set to an instance of an object.')
    o = p.o
    if o < 0 or o > len(a) or n < 0 or n > (len(a) - o) * size:
        raise _out_of_range()
    return a, o


def pod_of(code, p, n):
    """Mem.BytesOf(p, n) for a Ptr of element code B, Z, S, I, L, F or D. A null p with no bytes is no bytes:
    libstdc++ passes the data() of an empty vector, which is nullptr, with a length of 0 (os.write(v.data(), 0);
    C# Mem.Bytes)."""
    if n == 0 and p.a is None:
        return b''
    size, fc = _POD[code]
    a, o = _pod_start(p, size, n)
    k = (n + size - 1) // size
    if k == 0:
        return b''
    vals = a[o:o + k]
    if code == 'B':
        return bytes([x & 255 for x in vals])[:n]
    if code == 'Z':
        return bytes([1 if x else 0 for x in vals])[:n]
    if fc in 'HIQ':
        m = (1 << (8 * size)) - 1
        vals = [x & m for x in vals]
    return _struct.pack('<%d%s' % (k, fc), *vals)[:n]


def pod_into(code, p, src, n):
    """Mem.BytesInto(p, src, 0, n): the first n bytes of the numbers p points at replaced by src[0:n] (nothing, even
    at a null pointer, when n is 0)."""
    if n <= 0:
        if n == 0 and p.a is None:
            return
        _pod_start(p, _POD[code][0], 0)
        return
    size, fc = _POD[code]
    a, o = _pod_start(p, size, n)
    k = (n + size - 1) // size
    if code in 'BZ':
        a[o:o + n] = list(src[:n]) if code == 'B' else [x != 0 for x in src[:n]]
        return
    whole = n // size
    vals = list(_struct.unpack('<%d%s' % (whole, _POD_IN[code]), src[:whole * size])) if whole else []
    if k > whole:      # a partial last element: only its low bytes change
        old = pod_of(code, _Ptr1(a, o + whole), size)
        part = bytes(src[whole * size:n]) + old[n - whole * size:]
        vals.append(_struct.unpack('<' + _POD_IN[code], part)[0])
    a[o:o + k] = vals


class _Ptr1(object):
    __slots__ = ('a', 'o')

    def __init__(self, a, o):
        self.a = a
        self.o = o


def _f32(x):
    """(float)d: the double rounded once to single precision."""
    try:
        return _struct.unpack('<f', _struct.pack('<f', x))[0]
    except OverflowError:
        return float('inf') if x > 0 else float('-inf')


# ------------------------------------------------------------------------------------------------- manipulators

class SetPrec(object):
    __slots__ = ('n',)

    def __init__(self, n):
        self.n = n


class SetW(object):
    __slots__ = ('n',)

    def __init__(self, n):
        self.n = n


class SetFill(object):
    __slots__ = ('c',)

    def __init__(self, c):
        self.c = c


NewSetPrec = SetPrec
NewSetW = SetW
NewSetFill = SetFill


# ------------------------------------------------------------------------------------------------- IosBase

class IosBase(object):
    """The state and format part of std::ios_base (Cpp.IosBase)."""
    __slots__ = ('failbit', 'eofbit', 'badbit', 'prec', 'flagsv', 'widthv', 'fillc')

    def __init__(self):
        self.failbit = False
        self.eofbit = False
        self.badbit = False
        self.prec = 6
        self.flagsv = 0
        self.widthv = 0
        self.fillc = 32

    def good(self):
        return not (self.failbit or self.eofbit or self.badbit)

    def fail(self):
        return self.failbit or self.badbit

    def bad(self):
        return self.badbit

    def eof(self):
        return self.eofbit

    def ok(self):
        return not (self.failbit or self.badbit)

    def notok(self):
        return self.failbit or self.badbit

    def clear(self, s=0):
        """clear(state): as in the C# port, every flag is reset whatever the argument."""
        self.failbit = self.eofbit = self.badbit = False

    def precision_(self):
        return self.prec

    def precision_l(self, p):
        o = self.prec
        self.prec = p
        return o

    def width_(self):
        return self.widthv

    def width_l(self, w):
        o = self.widthv
        self.widthv = w
        return o

    def fill_(self):
        return self.fillc

    def fill_i(self, c):
        o = self.fillc
        self.fillc = c
        return o

    def setf_i(self, f):
        o = self.flagsv
        self.flagsv = _i32(o | f)
        return o

    def setf_i_i(self, f, mask):
        o = self.flagsv
        self.flagsv = _i32((o & ~mask) | (f & mask))
        return o

    def unsetf(self, f):
        self.flagsv = _i32(self.flagsv & ~f)

    def rdbuf(self):
        return None

    def imbue(self, l):
        pass

    def exceptions(self, m):
        pass


# ------------------------------------------------------------------------------------------------- OStream

class OStream(IosBase):
    """std::ostream (Cpp.OStream). It writes to a FileStream (s: an ofstream or fstream), a MemoryStream (mem: an
    ostringstream) or a console stream (std: 1 for cout, 2 for cerr)."""
    __slots__ = ('s', 'mem', 'std')

    def __init__(self):
        IosBase.__init__(self)
        self.s = None
        self.mem = None
        self.std = 0

    def Raw(self, data):
        """(s ?? mem)?.Write(b, off, n) of a bytes-like object."""
        s = self.s
        if s is not None:
            s.write(data)
        elif self.std:
            if len(data) <= 0:
                return
            f = _std_file(self.std)
            if f is not None:
                if not _bound:
                    _bind()
                _fflush(f)
            _write_fd(self.std, data)
        elif self.mem is not None:
            self.mem.write(data)

    def Pad(self, body):
        """A formatted field: the fill before it (after it when left-adjusted) up to the width, which resets."""
        w = self.widthv
        self.widthv = 0
        n = len(body)
        if n >= w:
            self.Raw(body)
            return
        pad = bytes([self.fillc & 255]) * (w - n)
        if self.flagsv & 32:
            self.Raw(body)
            self.Raw(pad)
        else:
            self.Raw(pad)
            self.Raw(body)

    def Put_Str(self, x):
        self.Pad(bytes(x.b[:x.n]))
        return self

    def Put_CPtr(self, x):
        self.Pad(_cbytes(x))
        return self

    def Put_c(self, c):
        self.Pad(bytes((c & 255,)))
        return self

    def Put_uc(self, c):
        self.Pad(bytes((c & 255,)))
        return self

    def Put_i(self, v):
        self.Pad(str(v).encode('ascii'))
        return self

    Put_l = Put_i
    Put_u = Put_i
    Put_ul = Put_i
    Put_s = Put_i

    def Put_b(self, v):
        self.Pad((b'true' if v else b'false') if self.flagsv & 1 else (b'1' if v else b'0'))
        return self

    def Put_d(self, v):
        """operator<<(double): the conversion letter from the floatfield, '#' for showpoint, the precision."""
        if not _bound:
            _bind()
        fl = self.flagsv
        fm = _FMTS[1 if fl & 1024 else 0]
        fx = fl & 4
        sci = fl & 256
        if fx and sci:
            body = _Format(fm[3], [v])
        else:
            body = _Format(fm[1] if fx else (fm[2] if sci else fm[0]), [_i32(self.prec), v])
        self.Pad(bytes(body))
        return self

    Put_f = Put_d

    def Put_SetPrec(self, p):
        self.prec = p.n
        return self

    def Put_SetW(self, w):
        self.widthv = w.n
        return self

    def Put_SetFill(self, f):
        self.fillc = f.c
        return self

    def Put_OStream(self, o):
        return self

    def Manip_endl(self):
        self.Raw(b'\n')
        self.flush()
        return self

    def Manip_flush(self):
        self.flush()
        return self

    def Manip_setprecision(self, n):
        self.prec = n
        return self

    def Manip_setw(self, n):
        self.widthv = n
        return self

    def Manip_setfill(self, c):
        self.fillc = c
        return self

    def Manip_quoted_Str(self, x):
        """std::quoted(s): s between double quotes, with every '"' and '\\' escaped by a backslash; the width
        applies to the whole quoted string, as in libstdc++ (__ostream_insert of the built string)."""
        body = bytes(x.b[:x.n])
        if b'"' in body or b'\\' in body:
            body = body.replace(b'\\', b'\\\\').replace(b'"', b'\\"')
        self.Pad(b'"' + body + b'"')
        return self

    def Manip_quoted_CPtr(self, x):
        body = _cbytes(x)
        if b'"' in body or b'\\' in body:
            body = body.replace(b'\\', b'\\\\').replace(b'"', b'\\"')
        self.Pad(b'"' + body + b'"')
        return self

    def Manip_fixed(self):
        self.flagsv = (self.flagsv & ~(4 | 256)) | 4
        return self

    def Manip_scientific(self):
        self.flagsv = (self.flagsv & ~(4 | 256)) | 256
        return self

    def Manip_defaultfloat(self):
        self.flagsv &= ~(4 | 256)
        return self

    def Manip_left(self):
        self.flagsv = (self.flagsv & ~(32 | 128)) | 32
        return self

    def Manip_right(self):
        self.flagsv = (self.flagsv & ~(32 | 128)) | 128
        return self

    def Manip_boolalpha(self):
        self.flagsv |= 1
        return self

    def Manip_noboolalpha(self):
        self.flagsv &= ~1
        return self

    def Manip_showpoint(self):
        self.flagsv |= 1024
        return self

    def Manip_noshowpoint(self):
        self.flagsv &= ~1024
        return self

    def write_CPtr_l(self, p, n):
        """write(p, n): n raw bytes (none, even at a null pointer, when n is 0)."""
        if n != 0:
            self.Raw(_span(p.B, p.O, _i32(n)))
        return self

    # write((const char*)p, n) of an array of numbers: its first n bytes (Mem.BytesOf)
    def write_PtrT_l__B(self, p, n):
        self.Raw(pod_of('B', p, _i32(n)))
        return self

    def write_PtrT_l__Z(self, p, n):
        self.Raw(pod_of('Z', p, _i32(n)))
        return self

    def write_PtrT_l__S(self, p, n):
        self.Raw(pod_of('S', p, _i32(n)))
        return self

    def write_PtrT_l__I(self, p, n):
        self.Raw(pod_of('I', p, _i32(n)))
        return self

    def write_PtrT_l__L(self, p, n):
        self.Raw(pod_of('L', p, _i32(n)))
        return self

    def write_PtrT_l__F(self, p, n):
        self.Raw(pod_of('F', p, _i32(n)))
        return self

    def write_PtrT_l__D(self, p, n):
        self.Raw(pod_of('D', p, _i32(n)))
        return self

    def flush(self):
        """s?.Flush(): a file's buffer reaches the file; the console streams write through at once."""
        s = self.s
        if s is not None:
            s.flush()
        return self

    def tellp(self):
        """The put position: the file's (with what is buffered), the MemoryStream's, 0 with neither."""
        if self.s is not None:
            return self.s.position()
        if self.std:
            raise _net.sys_exc('NotSupportedException', 'Stream does not support seeking.')
        return self.mem.pos if self.mem is not None else 0

    def seekp(self, pos, d=0):
        if self.s is not None:
            self.s.seek(pos, d)
        elif self.std:
            raise _net.sys_exc('NotSupportedException', 'Stream does not support seeking.')
        elif self.mem is not None:
            self.mem.seek(pos, d)
        return self


def _write_fd(fd, data):
    """A write to the console: write(2) until done; EPIPE is ignored, as .NET's console stream ignores it."""
    mv = memoryview(data)
    try:
        while mv:
            k = os.write(fd, mv)
            mv = mv[k:]
    except BrokenPipeError:
        pass


def _std_ostream(fd):
    o = OStream()
    o.std = fd
    return o


# ------------------------------------------------------------------------------------------------- OStringStream

class OStringStream(OStream):
    """std::ostringstream over a MemoryStream: str() is every byte written; ostringstream(init) starts with init's
    bytes and the put position at the start, so writes overwrite it."""
    __slots__ = ()

    def __init__(self):
        OStream.__init__(self)
        self.mem = _net.MemStream()

    def str_(self):
        if not _bound:
            _bind()
        return _new_str(self.mem.b)

    def str_Str(self, x):
        """str(x): the contents replaced by x, the put position at its end."""
        m = _net.MemStream()
        m.write(bytes(x.b[:x.n]))
        self.mem = m

    def MoveFrom(self, o):
        """The move assignment: the bytes, the precision and the flags (the source is left empty)."""
        self.mem = o.mem
        self.prec = o.prec
        self.flagsv = o.flagsv
        o.mem = _net.MemStream()


def NewOStringStream():
    return OStringStream()


def NewOStringStream_Str(init):
    o = OStringStream()
    o.mem.write(bytes(init.b[:init.n]))
    o.mem.pos = 0
    return o


def NewOStringStream_i(mode):
    return OStringStream()


# ------------------------------------------------------------------------------------------------- OFStream

class OFStream(OStream, _IDisposable):
    """std::ofstream over a FileStream with a 64 KiB buffer: ios::app appends (FileMode.Append), any other mode
    creates or truncates (FileMode.Create); a file that cannot be opened leaves the stream closed with failbit set.
    Dispose is the destructor, which closes the file; a stream that is never closed keeps its buffered bytes, as the
    C# FileStream does at exit."""
    __slots__ = ()

    def open_Str_i(self, path, mode=16):
        if self.s is not None:      # the C# port drops the old FileStream
            old = self.s
            self.s = None
            old.close()
        try:
            app = (mode & 1) != 0
            fd = _net.open_fd(_net.latin1_str(path.b[:path.n]), _net.APPEND if app else _net.CREATE)
            try:
                self.s = _net.FileOut(fd, 1 << 16, app)
            except BaseException:
                os.close(fd)
                raise
            self.failbit = False
        except Exception:
            self.s = None
            self.failbit = True

    def open_FsPath_i(self, path, mode=16):
        self.open_Str_i(path.p, mode)

    def is_open(self):
        return self.s is not None

    def close(self):
        s = self.s
        if s is not None:
            self.s = None
            s.close()

    def Dispose(self):
        """~basic_ofstream closes the file."""
        self.close()

    def MoveFrom(self, o):
        self.s = o.s
        o.s = None


def NewOFStream():
    return OFStream()


def NewOFStream_Str_i(path, mode=16):
    o = OFStream()
    o.open_Str_i(path, mode)
    return o


def NewOFStream_CPtr_i(path, mode=16):
    o = OFStream()
    _open_cptr(o, path, mode)
    return o


def NewOFStream_FsPath_i(path, mode=16):
    o = OFStream()
    o.open_Str_i(path.p, mode)
    return o


class _PathStr(object):
    """The bytes of a C string, shaped as a Str for open (new Str(path) of a CPtr)."""
    __slots__ = ('b', 'n')

    def __init__(self, data):
        self.b = data
        self.n = len(data)


def _open_cptr(stream, path, mode):
    stream.open_Str_i(_PathStr(_cbytes(path)), mode)


# ------------------------------------------------------------------------------------------------- FStream

class FStream(OStream, _IDisposable):
    """std::fstream as the C# port has it: the file opened, or created, for reading and writing
    (FileMode.OpenOrCreate, not truncated) and written from its start through a FileStream with the default 4 KiB
    buffer."""
    __slots__ = ()

    def __init__(self, path=None, mode=0):
        OStream.__init__(self)
        if path is None:
            return
        try:
            fd = _net.open_fd(_net.latin1_str(path.b[:path.n]), _net.OPEN_OR_CREATE_RW)
            try:
                self.s = _net.FileOut(fd, 4096, False)
            except BaseException:
                os.close(fd)
                raise
        except Exception:
            self.failbit = True

    def is_open(self):
        return self.s is not None

    def close(self):
        s = self.s
        if s is not None:
            self.s = None
            s.close()

    def Dispose(self):
        self.close()


def NewFStream(path, mode):
    return FStream(path, mode)


# ------------------------------------------------------------------------------------------------- IStream

_WS = b' \t\n\r\x0b\x0c'


class _Ref(object):
    __slots__ = ('v',)

    def __init__(self, v):
        self.v = v


class IStream(IosBase):
    """std::istream as the C# port has it: a buffer of bytes (the whole string of an istringstream, or the last block
    of up to 64 KiB read from an ifstream's file) and the read position in it. dir: an ifstream opened on a
    directory: libstdc++ opens it (open(2) of a directory for reading succeeds) and the first read fails with EISDIR,
    which basic_filebuf::underflow throws as ios_base::failure and the extraction catches: badbit and failbit, not
    eofbit (C# Cpp.IStream.dir)."""
    __slots__ = ('buf', 'pos', 'len', 'src', 'gcountv', 'dir')

    def __init__(self):
        IosBase.__init__(self)
        self.buf = b''
        self.pos = 0
        self.len = 0
        self.src = None
        self.gcountv = 0
        self.dir = False

    def Fill(self):
        pos = self.pos
        if pos < self.len:
            if pos < 0:
                raise IndexError('Index was outside the bounds of the array.')
            return True
        if self.dir:
            self.badbit = True
            self.failbit = True
            return False
        src = self.src
        if src is None:
            return False
        b = src.read(1 << 16)
        self.buf = b
        self.len = len(b)
        self.pos = 0
        return self.len > 0

    def AtEnd(self):
        """A read found no more input: the end of the input sets eofbit; a failed read (dir) set badbit instead."""
        if not self.dir:
            self.eofbit = True

    def peek(self):
        if not self.Fill():
            self.AtEnd()
            return -1
        return self.buf[self.pos]

    def get_(self):
        if not self.Fill():
            self.AtEnd()
            self.failbit = True
            return -1
        self.gcountv = 1
        p = self.pos
        self.pos = p + 1
        return self.buf[p]

    def get_cr(self, c):
        x = self.get_()
        if x >= 0:
            c.v = x - 256 if x > 127 else x
        return self

    def ignore(self, n=1, delim=-1):
        i = 0
        while i < n:
            c = self.get_()
            if c < 0 or c == delim:
                break
            i += 1
        self.failbit = self.failbit and not self.eofbit
        return self

    def gcount(self):
        return self.gcountv

    def read_CPtr_l(self, p, n):
        """read(p, n): up to n bytes; fewer set eof and fail. gcount is the number read."""
        got = 0
        B = p.B
        o = p.O
        isbytes = isinstance(B, bytearray)
        while got < n:
            if not self.Fill():
                break
            pos = self.pos
            k = n - got
            if self.len - pos < k:
                k = self.len - pos
            chunk = self.buf[pos:pos + k]
            if o + got + k > len(B):        # Array.Copy past the end of the destination
                raise _net.sys_exc('ArgumentException', 'Destination array was not long enough. Check the '
                                   'destination index, length, and the array\'s lower bounds. (Parameter '
                                   '\'destinationArray\')')
            if isbytes:
                B[o + got:o + got + k] = chunk
            else:
                B[o + got:o + got + k] = list(chunk)
            self.pos = pos + k
            got += k
        self.gcountv = got
        if got < n:
            self.AtEnd()
            self.failbit = True
        return self

    def _read_tmp(self, n):
        tmp = bytearray(_i32(n))
        self.read_CPtr_l(_TmpPtr(tmp), n)
        return tmp

    # read((char*)p, n) into an array of numbers: its first gcount bytes are replaced (Mem.BytesInto)
    def read_PtrT_l__B(self, p, n):
        t = self._read_tmp(n)
        pod_into('B', p, t, self.gcountv)
        return self

    def read_PtrT_l__Z(self, p, n):
        t = self._read_tmp(n)
        pod_into('Z', p, t, self.gcountv)
        return self

    def read_PtrT_l__S(self, p, n):
        t = self._read_tmp(n)
        pod_into('S', p, t, self.gcountv)
        return self

    def read_PtrT_l__I(self, p, n):
        t = self._read_tmp(n)
        pod_into('I', p, t, self.gcountv)
        return self

    def read_PtrT_l__L(self, p, n):
        t = self._read_tmp(n)
        pod_into('L', p, t, self.gcountv)
        return self

    def read_PtrT_l__F(self, p, n):
        t = self._read_tmp(n)
        pod_into('F', p, t, self.gcountv)
        return self

    def read_PtrT_l__D(self, p, n):
        t = self._read_tmp(n)
        pod_into('D', p, t, self.gcountv)
        return self

    def ReadLine(self, s, delim):
        """getline(s, line, delim): the bytes up to the delimiter (consumed, not stored)."""
        s.clear()
        anyb = False
        d = delim if 0 <= delim <= 255 else -1
        while True:
            if not self.Fill():
                self.AtEnd()
                if not anyb:
                    self.failbit = True
                return anyb
            anyb = True
            buf = self.buf
            pos = self.pos
            ln = self.len
            k = buf.find(d, pos, ln) if d >= 0 else -1
            if k < 0:
                k = ln
            if k > pos:
                _append_bytes(s, buf[pos:k])
            if k < ln:
                self.pos = k + 1
                return True
            self.pos = ln

    def SkipWs(self):
        while self.Fill():
            buf = self.buf
            pos = self.pos
            ln = self.len
            while pos < ln and buf[pos] in _WS:
                pos += 1
            self.pos = pos
            if pos < ln:
                return

    def _token(self):
        """The next whitespace-delimited token as bytes; failbit when there is none, eof when the input ends."""
        self.SkipWs()
        parts = None
        tok = b''
        while self.Fill():
            buf = self.buf
            pos = self.pos
            ln = self.len
            e = pos
            while e < ln and buf[e] not in _WS:
                e += 1
            piece = buf[pos:e]
            self.pos = e
            if parts is None and not tok:
                tok = piece
            else:
                if parts is None:
                    parts = [tok]
                parts.append(piece)
            if e < ln:
                break
        if parts is not None:
            tok = b''.join(parts)
        if not tok:
            self.failbit = True
            if not self.Fill():
                self.AtEnd()
        elif not self.Fill():
            self.AtEnd()
        return tok

    def Get_Str(self, s):
        t = self._token()
        if t:
            if not _bound:
                _bind()
            s.Assign_Str(_new_str(t))
        return self

    def Manip_ws(self):
        """is >> std::ws: skip whitespace (eof is set if the input ends there, failbit is not)."""
        while True:
            if not self.Fill():
                self.AtEnd()
                break
            buf = self.buf
            pos = self.pos
            ln = self.len
            while pos < ln and buf[pos] in _WS:
                pos += 1
            self.pos = pos
            if pos < ln:
                break
        return self

    def _next_c(self):
        """Past the current character: the next one, or -1 at the end of the input (Get_dr's Next)."""
        self.pos += 1
        if self.Fill():
            return self.buf[self.pos]
        return -1

    def Get_dr(self, v):
        """is >> v for a double, as libstdc++ does it (C# Cpp.IStream.Get(ref double), Java IStream.Get_dr): the
        sentry (a stream not good extracts nothing; white space skipped; at the end of the input eof and fail, v
        unchanged); num_get's _M_extract_float takes the characters a C-locale number can have (a sign, one of any
        leading zeros, digits, one '.', and an 'e' after a digit with its sign), stopping at the first other one,
        which stays in the stream ("0x10" is read as 0, then x10 is left); the gnu __convert_to_v converts them with
        strtod: not all of them converted (nothing, "1e", "inf") is failbit with v = 0, an overflow (+-inf) is
        failbit with v = +-DBL_MAX, an underflow is no error. eofbit when the number ends the input."""
        if self.failbit or self.eofbit or self.badbit:     # the sentry: a stream not good extracts nothing
            self.failbit = True
            return self
        self.SkipWs()
        if not self.Fill():
            self.AtEnd()
            self.failbit = True
            return self
        acc = bytearray()
        c = self.buf[self.pos]
        if c == 43 or c == 45:                              # the sign
            acc.append(c)
            c = self._next_c()
        mantissa = dec = sci = False
        while c == 48:                                      # leading zeros, one kept
            if not mantissa:
                acc.append(48)
                mantissa = True
            c = self._next_c()
        while c >= 0:
            if 48 <= c <= 57:
                acc.append(c)
                mantissa = True
            elif c == 46 and not dec and not sci:
                acc.append(46)
                dec = True
            elif (c == 101 or c == 69) and not sci and mantissa:
                acc.append(101)
                sci = True
                c = self._next_c()
                if c < 0:
                    break
                if c == 43 or c == 45:
                    acc.append(c)
                else:
                    continue                                # look at this character again
            else:
                break
            c = self._next_c()
        if c < 0:
            self.AtEnd()
        acc.append(0)
        if not _bound:
            _bind()
        r, n, _ = _scan_double(acc, 0, False)
        if n <= 0 or acc[n] != 0:
            v.v = 0.0
            self.failbit = True
        elif r == _INF:
            v.v = _DBL_MAX
            self.failbit = True
        elif r == -_INF:
            v.v = -_DBL_MAX
            self.failbit = True
        else:
            v.v = r
        return self

    def Get_fr(self, v):
        d = _Ref(0.0)
        self.Get_dr(d)
        v.v = _f32(d.v)
        return self

    def Get_ir(self, v):
        t = self._token()
        if not t:
            v.v = 0
            return self
        r, e = strtol_bytes(t, 0)
        if e != len(t) or r < -2147483648 or r > 2147483647:
            self.failbit = True
            v.v = -2147483648 if r < -2147483648 else (2147483647 if r > 2147483647 else 0)
            return self
        v.v = r
        return self

    def Get_lr(self, v):
        t = self._token()
        if not t:
            v.v = 0
            return self
        r, e = strtol_bytes(t, 0)
        if e != len(t):
            self.failbit = True
            v.v = 0
            return self
        v.v = r
        return self

    def Get_ulr(self, v):
        x = _Ref(0)
        self.Get_lr(x)
        v.v = x.v & 0xFFFFFFFFFFFFFFFF
        return self

    def Get_ur(self, v):
        x = _Ref(0)
        self.Get_lr(x)
        v.v = x.v & 0xFFFFFFFF
        return self

    def Get_cr(self, c):
        self.SkipWs()
        x = self.get_()
        if x >= 0:
            c.v = x - 256 if x > 127 else x
        return self

    def tellg(self):
        """The read position: the file offset of the next byte, or the index in the string."""
        src = self.src
        return src.pos - (self.len - self.pos) if src is not None else self.pos

    def seekg(self, off, d=0):
        self.eofbit = False
        src = self.src
        if src is not None:
            # ios::cur is relative to the read position, not to the file's, which is past the buffered block
            np = off if d == 0 else (self.tellg() + off if d == 1 else src.length() + off)
            if np < 0:
                self.failbit = True
                return self
            src.seek(np)
            self.pos = self.len = 0
        else:
            self.pos = _i32(off if d == 0 else (self.pos + off if d == 1 else self.len + off))
        return self


class _TmpPtr(object):
    """new CPtr(tmp, 0) over a scratch buffer (read<T> reads into it first)."""
    __slots__ = ('B', 'O')

    def __init__(self, b):
        self.B = b
        self.O = 0


def _append_bytes(s, data):
    """s.append(CPtr, n) of a bytes-like object."""
    if not _bound:
        _bind()
    s.append_CPtr_ul(_CPtr(bytearray(data), 0), len(data))


def strtol_bytes(b, i):
    """C#'s R.strtol(s, ref end, base) on a bytes-like object from index i: leading isspace skipped, an optional
    sign, decimal digits (the C# port reads base 10 whatever the base), the value clamped to the long range. Returns
    (value, end index); the end is i itself when there are no digits."""
    n = len(b)
    j = i
    while j < n and b[j] in _WS:
        j += 1
    neg = False
    if j < n and (b[j] == 43 or b[j] == 45):
        neg = b[j] == 45
        j += 1
    st = j
    while j < n and 48 <= b[j] <= 57:
        j += 1
    if j == st:
        return 0, i
    v = int(b[st:j])
    if neg:
        v = -v
    if v > 9223372036854775807:
        v = 9223372036854775807
    elif v < -9223372036854775808:
        v = -9223372036854775808
    return v, j


# ------------------------------------------------------------------------------------------------- IStringStream

class IStringStream(IStream):
    """std::istringstream: a copy of the string to read from."""
    __slots__ = ()

    def str_Str(self, s):
        """str(x): a copy of x to read from the start; fail and eof are cleared."""
        self.buf = bytes(s.b[:s.n])
        self.pos = 0
        self.len = len(self.buf)
        self.failbit = self.eofbit = False

    def str_(self):
        if not _bound:
            _bind()
        return _new_str(self.buf[:self.len])

    def MoveFrom(self, o):
        self.buf = o.buf
        self.pos = o.pos
        self.len = o.len


def NewIStringStream():
    return IStringStream()


def NewIStringStream_Str(s):
    i = IStringStream()
    i.str_Str(s)
    return i


def NewIStringStream_Str_i(s, mode):
    i = IStringStream()
    i.str_Str(s)
    return i


# ------------------------------------------------------------------------------------------------- IFStream

class IFStream(IStream, _IDisposable):
    """std::ifstream over a FileStream: a missing file leaves the stream closed with failbit set; a directory opens,
    as libstdc++ opens it, and every read then fails (IStream.dir). Dispose is the destructor, which closes the
    file."""
    __slots__ = ()

    def open_Str_i(self, path, mode=8):
        if self.src is not None:
            self.src.close()
            self.src = None
        self.dir = False
        try:
            name = _net.latin1_str(path.b[:path.n])
            if _net.dir_exists(name):
                # libstdc++ opens a directory for reading; every read then fails (see IStream.dir)
                self.dir = True
                self.failbit = False
                self.pos = self.len = 0
                return
            fd = _net.open_fd(name, _net.READ)
            self.src = _net.FileIn(fd)
            self.failbit = False
            self.pos = self.len = 0
        except Exception:
            self.src = None
            self.failbit = True

    def open_FsPath_i(self, path, mode=8):
        self.open_Str_i(path.p, mode)

    def is_open(self):
        return self.src is not None or self.dir

    def close(self):
        src = self.src
        if src is not None:
            self.src = None
            src.close()
        self.dir = False

    def Dispose(self):
        self.close()

    def MoveFrom(self, o):
        self.src = o.src
        self.buf = o.buf
        self.pos = o.pos
        self.len = o.len
        self.dir = o.dir
        o.src = None
        o.dir = False


def NewIFStream():
    return IFStream()


def NewIFStream_Str_i(path, mode=8):
    f = IFStream()
    f.open_Str_i(path, mode)
    return f


def NewIFStream_CPtr_i(path, mode=8):
    f = IFStream()
    _open_cptr(f, path, mode)
    return f


def NewIFStream_FsPath_i(path, mode=8):
    f = IFStream()
    f.open_Str_i(path.p, mode)
    return f


# ------------------------------------------------------------------------------------------------- Cpp.Streams

Streams_cout = _std_ostream(1)
Streams_cerr = _std_ostream(2)
Streams_clog = Streams_cerr


def Streams_getline_IStream_Str(s, line):
    s.ReadLine(line, 10)
    return s


def Streams_getline_IStream_Str_c(s, line, delim):
    s.ReadLine(line, delim & 255)
    return s


def Streams_getline_IStream_Str_i(s, line, delim):
    s.ReadLine(line, delim & 255)
    return s


def Streams_setprecision(n):
    return SetPrec(n)


def Streams_setw(n):
    return SetW(n)


def Streams_setfill(c):
    return SetFill(c)


class Streams(object):
    """Cpp.Streams as a namespace."""
    __slots__ = ()
    cout = Streams_cout
    cerr = Streams_cerr
    clog = Streams_clog
    getline_IStream_Str = staticmethod(Streams_getline_IStream_Str)
    getline_IStream_Str_c = staticmethod(Streams_getline_IStream_Str_c)
    getline_IStream_Str_i = staticmethod(Streams_getline_IStream_Str_i)
    setprecision = staticmethod(Streams_setprecision)
    setw = staticmethod(Streams_setw)
    setfill = staticmethod(Streams_setfill)
