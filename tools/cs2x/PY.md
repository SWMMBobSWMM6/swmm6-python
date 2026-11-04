# C# to Python: how tools/cs2x/Cs2Py.cs should map the generated engine

The Python engine is written from the verified C# engine (cs/gen, 480/480 against the SWMM6 reference) the way
the Go, Java and TypeScript engines are: Roslyn's semantic model, one IOperation tree per method, printed as
Python. The Java translator (Cs2Java.cs, JavaTypes.cs, JavaFn.cs; JAVA.md) is the closest model for structure;
the Go translator (GoFn.cs) for the overload-suffix names of runtime members. This file records the Python
decisions. The SWMM5 Python port (/home/claude/swmm5-ports/py, translated from C, 465/465 against EPA) shows
what fast, exact Python looks like; its runtime (`py/swmm5/` helpers) can be reused where it fits.

## Output

- `py/gen/engine.py`: one module, classes base-first (a topological order of the engine hierarchy), free
  functions and static members as module-level functions and variables (`X_name`, as in Go), which are faster
  to call than static methods. It imports the runtime as `from cpp import *` or `import cpp` (choose for speed:
  module-global names are looked up faster than attributes).
- `py/cs2py_runtime.txt` (manifest of runtime members and their Python names) and `py/cs2py_todo.txt`.

## Numbers

Python's int is unbounded and its float is an IEEE double.

- Fixed-width integers keep C#'s wrap-around by masking. Because + - * & | ^ << commute with reduction mod 2^n,
  a value needs wrapping only where it is stored in a variable, field or element of a fixed-width type, and
  before an operation whose result depends on the width: / and % (C# truncates toward zero: helpers), >> (sign),
  comparisons, conversions to float or text, and use as an index. Signed 32-bit: `((x + 0x80000000) & 0xFFFFFFFF)
  - 0x80000000`; unsigned: `x & 0xFFFFFFFF`; the same for 8, 16 and 64 bits. Choose inline expressions or
  helpers for speed. A loop counter's `++` need not wrap (it cannot reach 2^31 or 2^63 in practice) but an
  unsigned `--` must.
- float (C# float): rounded to single precision after every operation and conversion (`struct` round trip or a
  faster equivalent).
- Python raises where C# and C do not: float division by zero, `math.exp` overflow, `math.log(0)`,
  `math.sqrt(-1)`, int-to-float overflow. Division and the math functions go through helpers that give IEEE
  results (inf, nan) as C# does (the C# runtime calls glibc's libm, so the Python runtime should too, through the
  `math` module with the error cases handled, or ctypes on the system libm — measure speed).
- Python's `%` on floats is floored; C# and C use `fmod`.

## Values, references, generics

- Engine class → Python class with `__slots__`; the raw constructor sets every field to its C# default. Runtime
  struct → an immutable object (share freely), `ZERO` default.
- No overloading: overloaded engine and runtime methods get Go's parameter-type suffixes. Every method call is
  virtual in Python, so a derived method that C# hides rather than overrides must get a distinct name (the Java
  translator's MethodIdent rule).
- `ref T` → a Ref object (`get()`/`set(v)`); a local or parameter whose address is taken lives in a `Cell`
  (read `x.v`); an element `ArrRef(a, i)`; a field a generated helper.
- Generics are erased: pass element makers as the Java translator does (MakerArgs/Maker), numbers included.
- Lambdas with statement bodies become nested `def`s emitted before the statement; assigned captured variables
  need `nonlocal`. C# gives each iteration of a loop a fresh copy of a variable declared inside the loop body
  (and of a foreach variable); Python closures see one variable per function, so a closure that captures such a
  variable must bind it per iteration (a default argument or a factory).
- Exceptions: classes deriving from the runtime's CppException(Exception); C#'s catch-all is `except Exception`.
- goto (seven, all in hydrology/surface/UebReference): a dispatch loop; Python has no labelled continue, so a
  goto from inside a nested loop raises a private exception the dispatch loop catches (or uses flags).
- switch → if/elif (a dict of small functions only if it is measurably faster).

## Checks

`python3 -m py_compile` on the output, then a smoke run once the runtime exists. Speed matters: the SWMM5
Python engine ran the 451 fast gate models in 3,174 s against C#'s 628 s for all 480; aim for the same ratio.

Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
