# crtmath.py - part C of the Python runtime (package cpp): C#'s System.Math as the generated engine calls it
# (Math_<Name>_d) and the libm functions of cs/runtime/Misc.cs's Cpp.LibM (LibM_<name>).
#
# .NET implements System.Math with the C runtime's libm (glibc here), and CPython's math module calls the same
# system libm, so on the common path the results are glibc's bit for bit with no translation. Where the math module
# raises (ValueError for a domain error, OverflowError for a range error) C returns an IEEE value instead; those
# rare cases are handed to the system libm itself through ctypes, so the value -- the sign of a zero, an infinity,
# the NaN bit pattern -- is exactly glibc's. Where CPython computes a special case itself rather than calling libm
# (atan2 of a NaN returns its own NaN), the argument is routed to libm the same way. tests/crtcheck/pycheck compares
# every function with the system libm (a C program) on 300000 cases each plus a table of special values.
# floor / ceiling / truncate / round are exact operations computed here (keeping -0.0 and NaN), hypot and remainder
# go to libm through ctypes (CPython's own hypot is not glibc's).
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

import ctypes
import ctypes.util
import math
import struct

__all__ = [
    'Math_Acos_d', 'Math_Asin_d', 'Math_Atan_d', 'Math_Atan2_d', 'Math_Cbrt_d', 'Math_Ceiling_d', 'Math_CopySign_d',
    'Math_Cos_d', 'Math_Cosh_d', 'Math_Exp_d', 'Math_Floor_d', 'Math_Log_d', 'Math_Log10_d', 'Math_Log2_d',
    'Math_Pow_d', 'Math_Sin_d', 'Math_Sinh_d', 'Math_Sqrt_d', 'Math_Tan_d', 'Math_Tanh_d', 'Math_Truncate_d',
    'Math_Abs_d', 'Math_Max_d_d', 'Math_Min_d_d', 'Math_Round_d',
    'LibM_expm1', 'LibM_fmod', 'LibM_hypot', 'LibM_log1p', 'LibM_nextafter', 'LibM_remainder', 'LibM_round',
    'LibM_lround', 'LibM_rint', 'LibM_nearbyint', 'LibM_exp2', 'LibM_fdim', 'LibM_fmax', 'LibM_fmin', 'LibM_log',
    'LibM_erf', 'LibM_erfc', 'LibM_tgamma', 'LibM_lgamma',
]

_d = struct.Struct('<d')
_q = struct.Struct('<q')
_INF = float('inf')
_DNAN = _d.unpack(struct.pack('<Q', 0xFFF8000000000000))[0]   # x86's default NaN (sqrt(-1), 0/0): "-nan"
_TWO52 = 4503599627370496.0

_libm = ctypes.CDLL(ctypes.util.find_library('m') or 'libm.so.6')


def _c1(name):
    f = getattr(_libm, name)
    f.restype = ctypes.c_double
    f.argtypes = (ctypes.c_double,)
    return f


def _c2(name):
    f = getattr(_libm, name)
    f.restype = ctypes.c_double
    f.argtypes = (ctypes.c_double, ctypes.c_double)
    return f


_c_acos = _c1('acos')
_c_asin = _c1('asin')
_c_atan = _c1('atan')
_c_atan2 = _c2('atan2')
_c_cbrt = _c1('cbrt')
_c_cos = _c1('cos')
_c_cosh = _c1('cosh')
_c_exp = _c1('exp')
_c_log = _c1('log')
_c_log10 = _c1('log10')
_c_log2 = _c1('log2')
_c_pow = _c2('pow')
_c_sin = _c1('sin')
_c_sinh = _c1('sinh')
_c_tan = _c1('tan')
_c_tanh = _c1('tanh')
_c_expm1 = _c1('expm1')
_c_log1p = _c1('log1p')
_c_fmod = _c2('fmod')
_c_hypot = _c2('hypot')
_c_remainder = _c2('remainder')
_c_nextafter = _c2('nextafter')
_c_exp2 = _c1('exp2')
_c_fdim = _c2('fdim')
_c_fmax = _c2('fmax')
_c_fmin = _c2('fmin')
_c_rint = _c1('rint')
_c_nearbyint = _c1('nearbyint')
_c_erf = _c1('erf')
_c_erfc = _c1('erfc')
_c_tgamma = _c1('tgamma')
_c_lgamma = _c1('lgamma')

_acos = math.acos
_asin = math.asin
_atan = math.atan
_atan2 = math.atan2
_cbrt = math.cbrt
_cos = math.cos
_cosh = math.cosh
_exp = math.exp
_log = math.log
_log10 = math.log10
_log2 = math.log2
_pow = math.pow
_sin = math.sin
_sinh = math.sinh
_sqrt = math.sqrt
_tan = math.tan
_tanh = math.tanh
_expm1 = math.expm1
_log1p = math.log1p
_fmod = math.fmod
_floor = math.floor
_ceil = math.ceil
_trunc = math.trunc
_copysign = math.copysign
_nextafter = math.nextafter


# ------------------------------------------------------------------------------------------------ System.Math

def Math_Acos_d(x):
    try:
        return _acos(x)
    except ValueError:
        return _c_acos(x)


def Math_Asin_d(x):
    try:
        return _asin(x)
    except ValueError:
        return _c_asin(x)


def Math_Atan_d(x):
    return _atan(x)


def Math_Atan2_d(y, x):
    """atan2: CPython answers NaN arguments itself (with its own NaN); glibc returns x + y."""
    if y != y or x != x:
        return _c_atan2(y, x)
    return _atan2(y, x)


def Math_Cbrt_d(x):
    return _cbrt(x)


def Math_Cos_d(x):
    try:
        return _cos(x)
    except ValueError:
        return _c_cos(x)


def Math_Cosh_d(x):
    try:
        return _cosh(x)
    except OverflowError:
        return _INF


def Math_Exp_d(x):
    try:
        return _exp(x)
    except OverflowError:
        return _INF


def Math_Log_d(x):
    try:
        return _log(x)
    except ValueError:
        return _c_log(x)


def Math_Log10_d(x):
    try:
        return _log10(x)
    except ValueError:
        return _c_log10(x)


def Math_Log2_d(x):
    try:
        return _log2(x)
    except ValueError:
        return _c_log2(x)


def Math_Pow_d(x, y):
    """pow: CPython answers NaN arguments itself (pow(-nan, 1) is its x; glibc's is +nan); a NaN result comes only
    from a NaN argument (CPython raises for the others), so it goes to libm."""
    try:
        r = _pow(x, y)
    except (ValueError, OverflowError):
        return _c_pow(x, y)
    return r if r == r else _c_pow(x, y)


def Math_Sin_d(x):
    try:
        return _sin(x)
    except ValueError:
        return _c_sin(x)


def Math_Sinh_d(x):
    try:
        return _sinh(x)
    except OverflowError:
        return _c_sinh(x)


def Math_Sqrt_d(x):
    """sqrt: the engine inlines math.sqrt for x >= 0 and calls this for a negative or NaN argument: x86's default
    NaN for a negative one, the argument itself for a NaN."""
    if x >= 0.0:
        return _sqrt(x)
    return x if x != x else _DNAN


def Math_Tan_d(x):
    try:
        return _tan(x)
    except ValueError:
        return _c_tan(x)


def Math_Tanh_d(x):
    return _tanh(x)


def Math_CopySign_d(x, y):
    return _copysign(x, y)


def Math_Floor_d(x):
    """floor as a double: -0.0, infinities and NaN kept. Below 2^52 Python's float floor division by 1.0 is the
    exact floor with the sign of a zero result kept (-0.5 // 1.0 is -1.0, -0.0 // 1.0 is -0.0)."""
    if -_TWO52 < x < _TWO52:
        return x // 1.0
    return x


def Math_Ceiling_d(x):
    """ceil as a double: ceil(-0.5) is -0.0; infinities and NaN kept."""
    if -_TWO52 < x < _TWO52:
        r = float(_ceil(x))
        return r if r else _copysign(0.0, x)
    return x


def Math_Truncate_d(x):
    if -_TWO52 < x < _TWO52:
        r = float(_trunc(x))
        return r if r else _copysign(0.0, x)
    return x


def Math_Abs_d(x):
    return abs(x)


def Math_Max_d_d(a, b):
    """System.Math.Max (.NET Core 3.0+): NaN if either is NaN; +0.0 over -0.0."""
    if a != a:
        return a
    if b != b:
        return b
    if a == b:
        return b if _q.unpack(_d.pack(a))[0] < 0 else a
    return a if a > b else b


def Math_Min_d_d(a, b):
    if a != a:
        return a
    if b != b:
        return b
    if a == b:
        return a if _q.unpack(_d.pack(a))[0] < 0 else b
    return a if a < b else b


def Math_Round_d(x):
    """System.Math.Round(double): to the nearest integer, ties to even (the sign of a zero kept)."""
    if -_TWO52 < x < _TWO52:
        r = float(round(x))
        return r if r else _copysign(0.0, x)
    return x


# ------------------------------------------------------------------------------------------------ Cpp.LibM

def LibM_nextafter(x, y):
    """C nextafter: the next double after x toward y (y itself when equal, x + y if either is NaN)."""
    return _nextafter(x, y)


def LibM_hypot(x, y):
    return _c_hypot(x, y)


def LibM_fmod(x, y):
    try:
        return _fmod(x, y)
    except ValueError:
        return _c_fmod(x, y)


def LibM_round(x):
    """C round: to the nearest integer, halfway cases away from zero (round(-0.4) is -0.0)."""
    if -_TWO52 < x < _TWO52:
        t = float(_trunc(x))
        d = x - t                                   # exact below 2^52
        if d >= 0.5:
            t += 1.0
        elif d <= -0.5:
            t -= 1.0
        return t if t else _copysign(0.0, x)
    return x


def LibM_lround(x):
    """C lround: round's value as a long (LONG_MIN, x86's invalid result, for NaN or out of range)."""
    r = LibM_round(x)
    if r != r or r >= 9223372036854775808.0 or r < -9223372036854775808.0:
        return -9223372036854775808
    return int(r)


def LibM_expm1(x):
    try:
        return _expm1(x)
    except OverflowError:
        return _INF


def LibM_log1p(x):
    try:
        return _log1p(x)
    except ValueError:
        return _c_log1p(x)


def LibM_remainder(x, y):
    return _c_remainder(x, y)


def LibM_rint(x):
    return _c_rint(x)


def LibM_nearbyint(x):
    return _c_nearbyint(x)


def LibM_exp2(x):
    return _c_exp2(x)


def LibM_fdim(x, y):
    return _c_fdim(x, y)


def LibM_fmax(x, y):
    return _c_fmax(x, y)


def LibM_fmin(x, y):
    return _c_fmin(x, y)


def LibM_log(x):
    return Math_Log_d(x)


def LibM_erf(x):
    return _c_erf(x)


def LibM_erfc(x):
    return _c_erfc(x)


def LibM_tgamma(x):
    return _c_tgamma(x)


def LibM_lgamma(x):
    return _c_lgamma(x)
