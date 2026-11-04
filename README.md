# swmm6-python: the OpenSWMM 6 1D engine in Python, byte for byte

A translation of the 1D engine of OpenSWMM 6 (HydroCouple/openswmm.engine, branch `swmm6_rel`, commit 88329d6)
into Python, held to the same output as the C++ engine: the same binary `.out` file and the same `.rpt`
report, apart from the three clock lines. It is not SWMM6 and not a HydroCouple or EPA release; it is a
translation checked against SWMM6.

## Result

**474 of 474 models run so far are byte-identical; 6 of the 480 are still running** against the C++ reference engine built from the same commit and run on one thread
(`OMP_NUM_THREADS=1`), on the 480-model gate set (the one the SWMM5 ports were checked on), drawn from
[1729-SWMM5-Models-2030](https://github.com/SWMMBobSWMM6/1729-SWMM5-Models-2030). Gate runs
`py1`, `py1h`; every result, with the SHA-256 of each `.out` and `.rpt`, is in `gate/evidence/`.

A model passes when its `.out` is byte-identical to the reference's (when the reference writes one), its `.rpt` is
identical after removing the lines "Analysis begun", "Analysis ended" and "Total elapsed time", and its exit code is
the same (the command line exits nonzero when a model stops on an input error).

The six translations, all from the same C# and gated the same way: swmm6-csharp, swmm6-go, swmm6-java,
swmm6-typescript, swmm6-fortran and swmm6-python, under [SWMMBobSWMM6](https://github.com/SWMMBobSWMM6).

## How it was made

The C++ engine was translated to C# first, and the C# gated to 480 of 480. The other five languages are
translated from that C#, not from the C++. `tools/cs2x/Cs2Py.cs` translates the verified C# engine (`cs/gen`) through Roslyn (`py/gen/engine.py`; the mapping is in `tools/cs2x/PY.md`); `py/cpp` is the runtime library in Python. CPython's math module calls the system's glibc, as the C++ does, so its results are glibc's.

Nothing in the generated engine is edited by hand. Where the output depends on the C library (printf's rounding,
strtod, sscanf, the order of a hash table's nodes, introsort's swaps), the runtime reproduces glibc and libstdc++
exactly; the checks in `tests/` compare it with them case by case.

## Build and run

Needs CPython 3.13 (nothing else). No build step. Then run a model the way the C++ command line runs it:

    py/runswmm6 model.inp model.rpt model.out

To regenerate the engine: tools/cs2x/build_py.sh, then dotnet tools/cs2x/bin/cs2py.dll cs py/gen, then python3 py/tools/gen_init.py. The C# engine it starts from is regenerated from the C++ source with
`tools/cpp2x` (see the header of `tools/cpp2x/emit_cs.py` and `cs/README.md`), which needs the OpenSWMM source at
commit 88329d6 and libclang.

## The gate

`gate/make_ref.sh` makes the reference outputs with the C++ engine (`ref/runswmm6`: build openswmm.engine at
88329d6 and run it single-threaded); `gate/gate.py NAME "py/runswmm6" --resume` runs this engine on every model in
`gate/models_all.tsv` (paths relative to the corpus; set `SWMM_CORPUS`) and compares; `gate/cmp1.sh` checks one
model. The median time per model in the gate runs was 3.55 s (the C# engine's: 1.38 s, which includes .NET's start-up and JIT compilation). The times were measured on a shared two-core cloud machine while other runs were going, so they are a
rough guide, not a benchmark.

## What this does not show

- Agreement on models outside the 480. The gate set is broad (EPA's examples, the OWA EXTRAN set, hydrology,
  hydraulics, water quality, LID, RDII, controls, hot start, input errors), but a feature no gate model uses is
  untested.
- The reference's parallel runs. Every parallel loop runs on one thread, in order, which is what the C++ engine
  does when it is held to one thread; results with OpenMP threads can differ in the last bits.
- The 2D and other modules of OpenSWMM 6 beyond the 1D engine the command line runs.

## License and attribution

This repository is a translation of the OpenSWMM 6 engine.

OpenSWMM Engine — Copyright 2026 HydroCouple Developers, licensed under the Apache License, Version 2.0. See
[LICENSE](LICENSE) and [NOTICE](NOTICE). Source: https://github.com/HydroCouple/openswmm.engine (branch
`swmm6_rel`, commit `88329d6`).

Every generated engine file here is a modified, mechanically translated form of the OpenSWMM sources: its header
names what it was generated from, and each C# file in `cs/gen` names the OpenSWMM source file it came from.

Portions derive from the USEPA Storm Water Management Model, which is in the public domain under 17 USC § 105. The
USEPA does not endorse this product, and neither does HydroCouple.

The translators, the runtime, the gate and the notes: Copyright 2026 Robert Dickinson, licensed under the Apache
License, Version 2.0.

## AI use

Made with Claude (Anthropic): the translators, the runtime, the gate and the notes are Claude's work, directed by
Robert Dickinson; the engine itself is OpenSWMM's, translated mechanically. See [AI-USE.md](AI-USE.md).
