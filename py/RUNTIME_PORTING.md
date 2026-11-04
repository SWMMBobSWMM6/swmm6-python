# Porting the C# runtime to Python (package cpp, py/cpp)

`py/gen/engine.py` (written by `tools/cs2x/Cs2Py.cs` from the verified C# engine) imports every runtime name it
uses explicitly from the runtime package. **`py/RUNTIME_HELPERS.md` is the contract**: value representation,
names, the Ref protocol, element makers, the runtime internals the module reads directly (`Vec.a`/`.n`,
`Str.b`/`.n`), what the runtime may call on engine objects, and the exact semantics of every helper with no C#
member. `py/cs2py_runtime.txt` lists every name (a leading `.` marks a member of runtime objects). The C# runtime
(`cs/runtime/*.cs`) is the specification of behaviour.

References that passed all 480 gate models: Go (`go/cpp`, whose overload-suffix names the Python names follow),
Java (`java/src/cpp`, which takes element makers the same way and has the same split into parts). The SWMM5 Python
port's runtime (`/home/claude/swmm5-ports/py/swmm5/crt.py`, 465/465 against EPA) has glibc-exact printf, strtod
and sscanf for SWMM5's use and shows what fast exact Python looks like. CPython's `math` module calls the system
glibc libm, as the C++ and C# do, so no glibc translation is needed; only its error cases (it raises where C
returns inf or nan) need handling.

## Layout

- The package is `py/cpp/` with an `__init__.py` that **part D writes**, importing every name the engine imports
  from the part modules in a fixed order (part A's first, then C, B, D). Other parts write their own modules only.
- No module may use another part's names while modules import (at the top level), except inheriting from a class
  in a module `__init__` imports earlier. Inside functions anything goes.
- Speed matters: the gate runs 480 models in CPython 3.13. Prefer `__slots__`, local-variable caching in loops,
  plain lists for Vec storage, and inline arithmetic over helper calls on hot paths.

## Rules

- Add your own files to `py/cpp`; do not edit another part's files (say what you need in your report).
- Do not commit or touch git, do not edit `py/gen` (generated) or the translator.
- Do not run any program built from downloaded third-party source code. The system's own libraries and compilers
  are fine.
- Written by Claude (Anthropic) for the SWMM6 ports; credit AI-USE.md in each file header.
