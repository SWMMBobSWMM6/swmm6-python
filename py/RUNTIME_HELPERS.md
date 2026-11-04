# The Python runtime contract of py/gen/engine.py

Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.

`py/gen/engine.py` is written by `tools/cs2x/Cs2Py.cs` from the verified C# engine (`cs/gen`, `cs/runtime/Hand_*.cs`).
It imports every runtime name it uses explicitly (`from cpp import (...)`), so a missing name fails at import.
`py/cs2py_runtime.txt` lists them: one line per Python name, a tab, and the C# member(s) it stands for. Lines whose
name starts with `.` are **members** (methods, fields, properties) the engine calls on runtime objects; the others are
**module-level names** of package `cpp`. The C# runtime (`cs/runtime/*.cs`) is the specification of every member; this
file adds what is particular to Python: how values are represented, how the translator names and calls things, which
runtime internals it reads directly, and the exact semantics of the helpers it emits that have no C# counterpart.

The structure deliberately mirrors the Java runtime (`java/RUNTIME_PORTING.md`, `java/src/cpp`): the same
specialised class names (`VecD`, `PtrI`, ...), the same element makers, the same overload suffixes, so the Java
runtime is a close model. Where Java uses overloading, Python has Go's names (`NewStr_CPtr_ul`).

## 1. Values

| C# | Python |
|---|---|
| int, uint, long, ulong, short, ushort, sbyte, byte, char, engine enum | `int`, always **in the type's value range** when it reaches the runtime (uint 0..2^32-1, long -2^63..2^63-1, ...) |
| bool | `bool` |
| double | `float` (always a Python float when passed to the runtime; never an int) |
| float | `float` holding a value already rounded to single precision |
| string (System.String) | `str` (C# chars < 256 are Latin-1 code points; `\uNNNN` otherwise) |
| null | `None` |
| T[] (any element type) | `list`; `new T[n]` is `[default] * n` (0, 0.0, False, None or the struct's `_ZERO`) |
| engine class | a Python class with `__slots__` (section 6) |
| runtime class | the runtime's class |
| runtime struct (CPtr, Ptr*, Iter*, RevIter*, MIt, CSpan*, Duration, TimePoint, FromCharsResult) | an **immutable** object, shared freely; the engine never assigns a struct's field. Its default is the module constant `<Name>_ZERO` (e.g. `CPtr_ZERO`, `PtrD_ZERO`, `CSpan_ZERO`) |
| delegate (Func, Action, engine FNnn, RefAt, RefGet) | a Python callable; a `ref` parameter is passed a Ref (section 3) |
| `ref T` | a Ref (section 3) |

Every runtime member that returns an integer must return it in its C# type's range; one that returns `double` must
return a `float`; one that returns `float` a single-rounded `float`. Arguments arrive the same way.

A `byte[]`/`sbyte[]` the engine makes is a list of ints in the element type's range; a runtime byte buffer may be a
`bytearray` (`Str.b` is one, section 5). Code that reads bytes through a `CPtr` must accept either.

`long`/`ulong` share one specialisation code (`L`) and `int`/`uint` another (`I`), as in Java: `AtomicL` serves
`Atomic<long>` and `Atomic<ulong>` (the manifest names the C# type argument of each use). A specialised class that does
arithmetic on its elements (Atomic.fetch_add) cannot tell the signedness; the engine's atomics are counters that never
reach a wrap boundary.

## 2. Names

- Runtime class `Cpp.X` (nested `Cpp.X.Y`): `X` (`X_Y`); with one primitive type argument, the specialised
  containers `Vec Ptr Iter RevIter CSpan Opt Atomic RefAt RefGet` add the Java code of the argument (`D` double, `F`
  float, `I` int/uint/int-enum, `L` long/ulong, `B` byte/sbyte/byte-enum, `Z` bool, `S` short, `C` char/ushort):
  `Vec<double>` is `VecD`. Other generic classes are one class (`Map`, `UMap`, `Pair`, `Vec` for reference elements).
- Constructors: module-level functions `New<Class><sfx>`; `sfx` is `_` and the codes of the generic definition's
  parameter types when the class has more than one (non-private) constructor, none for the parameterless one
  (`NewStr`, `NewStr_CPtr_ul`, `NewVecD_ul_T`, `NewPtrD_TA_l`). Codes: i int, l long, ul ulong, u uint, d double,
  f float, b bool, c sbyte, uc byte, s short, us ushort, ch char, o object, str string, a type parameter by its name,
  arrays add A, generic types concatenate (`PtrT`, `FuncKKb`), `r` marks ref.
- Static methods: `<Class>_<name><sfx><spec>`; static fields `<Class>_<name>`; static properties
  `<Class>_get_<name>()` / `<Class>_set_<name>(v)` / `<Class>_ref_<name>()`. `sfx` as above when the class has several
  methods of the name; `spec` (Java's rule) is `__` and one code per type argument (class's, then method's; `O` for
  a reference type) when a generic member is used with a primitive argument: `Algo_fill_IterT_IterT_T__D`,
  `Bytes_View_PtrT__F`, `Mv_Back__OD`. A generic member used only with reference types has no `spec`.
- Instance methods: `<name><sfx><spec>` (`append_CPtr_ul`, `read_PtrT_l__D`). Instance fields keep their C# names.
- Properties and indexers (Java's names): `get_<Name>(args)`, `set_<Name>(v)`; an indexer is `Item`, with `_<code of
  its index type>` when the class has several (`get_Item_l`). A property or indexer that returns `ref T` has three
  forms: `get_<Name>(args)` (the value), `ref_<Name>(args)` (a Ref) and `set_<Name>(args..., v)` (assignment through
  the reference): `p[i] = x` on a `Ptr<double>` is `p.set_Item_l(i, x)`.
- A method that returns `ref T`: the plain name returns the value, `ref_<name>(args)` a Ref (an assignment
  `m[k] = v` is `m.ref_Index(k).v = v`).
- Operators: `<Class>_op_Add`, `op_Sub`, `op_Mul`, `op_Div`, `op_Eq`, `op_Ne`, `op_Lt`, `op_Gt`, `op_Le`, `op_Ge`,
  `op_Increment`, `op_Decrement` (+ `sfx` when overloaded, + `spec` for a generic class not specialised); implicit
  conversions `<Class>_from_<code>` (`CPtr_from_Ptruc`, `PtrD_from_IterT`).
- `ToString()` of a runtime value: the method `.ToString()`; `foreach` over a runtime value: a Python `for` over what
  the C# expression returns (the engine uses `Items()`, which must return a Python iterable).
- A field the engine takes a reference to: `ref_<field>()` returns a Ref.

## 3. References

A Ref is any object with a readable and writable attribute `v`. The engine passes and receives them for every C#
`ref`/`out`.

- `Cell(v)`: a class with one slot `v`. A local or parameter whose address is taken anywhere lives in a Cell for its
  whole life (read `x.v`); passing it by ref passes the Cell itself.
- `ArrRef(lst, i)`: a Ref to `lst[i]` (`v` a property reading and writing `lst[i]`). Used for array elements and for
  `Vec` elements (`ArrRef(vec.a, i)`, so it refers to the backing list as C#'s `ref a[i]` does).
- References to engine fields are small classes the module defines itself (`FR_<Class>_<field>(obj)`,
  `SR_<static>()`).
- Runtime members' `ref_` forms return Refs. A runtime member taking `ref T` receives a Ref and assigns `r.v`.
- A `RefAt<T>`/`RefGet<T>` delegate is a callable returning a Ref; `new Ptr<T>(obj, at)` (`NewPtrD_o_RefAtT`) keeps it
  and reads/writes the field through `at(obj).v`.

## 4. Element makers

C# makes container elements with `Elem<T>.Make()` (default(T), or T's public parameterless constructor). The
translator passes makers as Java's does: a **maker** is a zero-argument callable returning a new default element.

- Appended as the **last** argument to the constructors of the generic (non-specialised) `Vec<T>` (maker of T),
  `Map<K,V>`, `UMap<K,V>`, `UMapC<...>` (maker of V) that have no `Func` parameter; given in place of a null `Func<T>`
  argument; one per type argument to a parameterless `Pair()`/`Tup()`; appended to `Mv_Take`, `Arr_Make`,
  `Arr_Make2` when T is a reference type (with a primitive T those are `Mv_Take__D` etc. and take no maker).
  The manifest marks these entries "+ makers" / "+ maker".
- Makers the runtime defines (they return immutable values, so a runtime may fill with `[mk()] * n`): `MK_I` (0),
  `MK_D` (0.0), `MK_Z` (False), `MK_N` (None), `MK_<Struct>` (the struct's `_ZERO`, e.g. `MK_CPtr`, `MK_PtrD`).
  Other makers are engine callables (an engine class, or its `<Class>__new` factory) or runtime constructors
  (`NewStr`, `lambda: NewVec(MK_x)`).
- Copying an element (C#'s `Elem<T>.Copy`) needs no maker: an `ICopy` instance (engine classes derive from the
  runtime class `ICopy`) is copied with `.Clone()`, a list (a C# array) deeply, anything else is shared.

## 5. Runtime internals the translator uses directly

These are part of the contract because the module reads them without a call (for speed):

| C# | Python the module writes |
|---|---|
| `vec[i]` on `Vec<T>` (read, write, `ref`) | `vec.a[i]` (`a`: the backing list; elements `0..n-1`; it may be longer) |
| `vec.size()`, `vec.empty()` | `vec.n`, `(vec.n == 0)` (`n`: the element count, a field kept exact) |
| `vec.data()[i]` | `vec.a[i]` |
| `s[i]` on `Str` (`ref byte`) | `s.b[i]` (`b`: a `bytearray`; bytes `0..n-1`, values 0..255) |
| `s.size()`, `s.length()`, `s.empty()` on `Str` | `s.n`, `s.n`, `(s.n == 0)` |
| `new Ptr<T>(arr, off)[i]` | `arr[off + i]` (that constructor always makes an array pointer) |
| `p[i]` on `CPtr` | `p.get(i)` (an sbyte value), `p.set(i, v)`, `p.ref(i)`, `p.preInc(i)`, `p.postInc(i)`, `p.preDec(i)`, `p.postDec(i)` (Java's names; inc/dec return the new/old sbyte) |
| `M.min(a, b)`, `M.max(a, b)`, `M.clamp(v, lo, hi)` of numbers | inline: `(b if b < a else a)`, `(b if a < b else a)`, `(lo if v < lo else (hi if hi < v else v))` |
| `Math.Abs(x)` | `abs(x)` |
| `Math.Sqrt(x)` | `(_m_sqrt(x) if x >= 0.0 else Math_Sqrt_d(x))` (`math.sqrt`; the runtime handles NaN and negatives) |
| `double.IsNaN`, `IsInfinity`, `IsFinite`, `IsPositiveInfinity`, `IsNegativeInfinity` | `math.isnan`, `math.isinf`, `math.isfinite`, `x == inf`, `x == -inf` |
| `object.ReferenceEquals(a, b)`, `==` on classes and delegates | `a is b` |

The runtime's other `Vec`/`Str` members must keep `a`/`b` and `n` consistent with those reads.

## 6. Engine objects (what the runtime may call)

- An engine class `X` is allocated by `X()`: every field of its hierarchy set to its C# default (what the protected
  `Raw` constructor does). A constructor with a body runs as `X__new<sfx>(args)` (a module function: allocate, then
  `X._ctor<sfx>(o, args)`). An engine class deriving from a runtime class calls the runtime base's
  `__init__(self)` **with no arguments** first (only `CppException` is used: its `__init__(self)` must work with no
  arguments, giving the default "std::exception" message, as `new CppException()` does).
- Engine classes have no instance `__dict__` (`__slots__`); a field that hides a field of a base class (engine or
  runtime) is renamed `<name>_<Class>` (the engine's `GwfFail.msg` becomes `msg_..._GwfFail`, so `CppException.msg`
  stays the runtime's).
- Methods implementing runtime interface members keep their C# names (`Clone`, `Assign`, `MoveFrom`, `HashOf`,
  `Call2`, `DestroyElems`, `Dispose`, `Equals`, `CompareTo`, `GetHashCode`); the runtime calls them by name. Engine
  classes list the runtime interfaces they implement as bases (`ICopy`, `IHashFn`, `IBinFn`, `IDisposable`,
  `IDestroyElems`): these must be classes with `__slots__ = ()`.
- `using` and `using var` call `.Dispose()` on a non-None value in a `finally`.

## 7. Helpers with no C# member (exact semantics)

The reference implementations below were checked against C# (.NET 8, x86-64) by the translator's differential test.

```python
import math, struct
_f = struct.Struct('<f'); _d = struct.Struct('<d'); _q = struct.Struct('<q'); _Q = struct.Struct('<Q')
_INF = float('inf')
def f64_from_bits(b): return _d.unpack(_Q.pack(b & 0xFFFFFFFFFFFFFFFF))[0]   # also used for NaN constants
def f64_to_bits_signed(x): return _q.unpack(_d.pack(x))[0]                     # BitConverter.DoubleToInt64Bits
DNAN = f64_from_bits(0xFFF8000000000000)   # x86's default NaN (0/0, inf-inf, sqrt(-1)); glibc prints it "-nan"
def signbit(x): return _q.unpack(_d.pack(x))[0] < 0                            # double.IsNegative
```

- **`f32(x)`**: x (a float, or an int) rounded once to the nearest IEEE single (ties to even); overflow gives ±inf,
  NaN stays NaN. `struct.pack('<f', x)` does this for floats and raises OverflowError exactly when the rounded value
  is infinite (return `math.copysign(inf, x)` then). An int beyond 2^53 must be rounded from its exact value, not via
  a double (no double rounding). Used after every C# float operation and conversion (`f32(a * b)`): for + - * / of
  floats the double result rounded once equals C#'s single-precision result.
- **`fdiv0(a, b)`**: the value of `a / b` when `b` is +0.0, -0.0 or int 0 (the module calls it only then, writing
  `(a / b if b else fdiv0(a, b))`); a and b may be ints. NaN when a is NaN or zero (`DNAN`), else ±inf with the sign
  of a times the sign of b (`signbit` of `float(b)`).
- **`idiv(a, b)`**: C# signed 64-bit `/`: truncates toward zero; raises (ZeroDivisionError) when b is 0. (32-bit and
  narrower signed division is written inline as `int(a / b)`, exact for operands below 2^53; unsigned as `a // b`.)
  `long.MinValue / -1` is not wrapped (C# throws there).
- **`irem(a, b)`**: C# signed `%`: the remainder with the sign of the dividend (`-7 % 3 == -1`, `7 % -3 == 1`); raises
  for b == 0. (By a positive constant it is inline: `(x % c if x >= 0 else -(-x % c))`.)
- **`fmod(a, b)`**: C#'s `%` on doubles (C fmod): NaN if either is NaN, a is ±inf or b is 0 (`DNAN`); a if b is ±inf;
  else `math.fmod`. Accepts ints. (Unused by the current engine.)
- **`f2i32 f2i64 f2u32 f2u64 f2i16 f2u16 f2i8 f2u8`** (C# `(int)x` etc. of a double, unchecked, .NET 8 on x86-64; the
  current engine uses none of them, it calls `Conv_D2I` etc.): `f2i32` truncates when -2147483649.0 < x < 2147483648.0
  else -2147483648 (NaN too); `f2i64` truncates when -2^63 <= x < 2^63 else -2^63; `f2u32(x) = f2i64(x) & 0xFFFFFFFF`;
  `f2u64` is `f2i64(x)` mod 2^64 below 2^63, else `f2i64(x - 2^63) ^ 2^63`; the 8- and 16-bit ones take `f2i32(x)`
  modulo their width (signed ones re-signed). They accept ints.
- **`Math_<Name>_<code>`** (`Math_Pow_d`, `Math_Exp_d`, `Math_Log_d`, `Math_Floor_d`, `Math_Ceiling_d`,
  `Math_Truncate_d`, `Math_Sqrt_d`, trig, `Math_Atan2_d`, `Math_CopySign_d`, `Math_Cbrt_d`, ...): System.Math, which
  .NET implements with the C runtime's libm (glibc here). Python's `math` calls the same libm for finite results;
  where it raises (domain errors, overflow, `log(0)`) return what glibc returns (NaN as `DNAN` for invalid
  operations, ±inf, -inf for log(0)). `Math_Floor_d`/`Ceiling`/`Truncate` must return floats and keep -0.0 and NaN.
  `Math_Sqrt_d` is reached only for a negative or NaN argument: NaN (`DNAN` for negatives, the argument for a NaN).
- **`Cell`**, **`ArrRef`**: section 3.
- **`GotoSignal(k)`**: an exception class with attribute `k` (raised for a goto that leaves a loop inside a dispatch
  loop; the current engine has none).
- **`SysException(kind, message)`**: a .NET exception (`new XxxException(msg)`: kind is the C# class name, e.g.
  `'InvalidOperationException'`, `'NotImplementedException'`); a subclass of Exception. `catch (System.Exception)` is
  `except Exception`, which also catches it, the runtime's exceptions and Python's ZeroDivisionError/IndexError (C#'s
  DivideByZeroException/IndexOutOfRangeException).
- **`throw_(e)`**: raises e (a C# throw expression).
- **`cs_tostring(v)`**: C#'s `ToString()` / string concatenation of a value: ints `str(v)`, `True`/`False`, a str as
  it is, None as `''`, a double as .NET Core's shortest round-trip text ("R": `1E+15` from 1e15 up, `1E-05` below
  1e-4, `-0` for -0.0, `NaN`, `∞`), another object `.ToString()`. The engine uses it only on strings today.
- **`cs_equals(a, b)`** (`object.Equals` on a runtime value), **`exc_message(e)`** (System.Exception.Message as a
  str), **`Array_Fill(lst, v)`**, **`File_Exists(path)`**: as C#. (Unused by the current engine except as listed in the
  manifest.)
- **Typed boxes for `params object[]`** (`R.fprintf`, `R.snprintf`, `R.sscanf`, `TupN`, ...): Python ints have no
  width, so an argument whose C# type is not int, double, bool or a reference arrives wrapped: `I64(v)`, `U32(v)`,
  `U64(v)`, `I16(v)`, `U16(v)`, `I8(v)`, `U8(v)`, `Ch(v)` (char), `F32(v)` (float); each has `.v`, the value. The
  formatter must treat them as the C# runtime treats those .NET types. An enum arrives as its underlying type.

## 8. What the module does itself (not runtime work)

Integer wrap-around (inline masking where a value is stored or used width-sensitively), double/int mixing,
float-to-int dispatch, string literals, static initialisation order, goto dispatch, switch, closures. See
`tools/cs2x/PY.md` and the comments at the top of `tools/cs2x/PyFn.cs`.
