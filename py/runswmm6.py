# runswmm6.py - the SWMM6 command line (src/cli/main.cpp), a hand port of cs/runtime/Program.cs: the same calls into
# the engine's C API (the module-level F__global_swmm_* functions of py/gen/engine.py) in the same order, the same
# messages on stdout and the same exit code; R.FlushAll before exit, as returning from main calls exit(), which
# flushes every open FILE.
# What the C# gets from .NET is made explicit, as in the Java runner (java/src/main/Main.java):
#   - the engine runs on a thread with a large stack and a high recursion limit (Python frames are bigger than .NET's
#     and CPython's default limit is 1000);
#   - an exception that escapes the engine ends the process as .NET's unhandled-exception handler does:
#     "Unhandled exception. ..." on stderr, no flushing of the C files, and exit code 134 (SIGABRT's);
#   - the command-line arguments are the .NET strings (UTF-8, an invalid sequence replaced by U+FFFD), handed to the
#     engine as R.Lit makes them (each UTF-16 unit stored as its low byte) and echoed as Encoding.Latin1 writes them
#     ('?' for a char above U+00FF);
#   - {1:F2} of the elapsed seconds is .NET Core's: the exact value rounded half away from zero to two places.
# python3 -I ignores PYTHONPATH, so this file puts py/ (the runtime package cpp) and py/gen (the engine) on sys.path.
# Usage: python3 -I py/runswmm6.py <input.inp> <report.rpt> [output.out]   (py/runswmm6 is the shell wrapper)
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
import gc
import os
import sys
import threading
import time
from decimal import Decimal, ROUND_HALF_UP

VERSION = '6.0.0-alpha.4'
STACK_BYTES = 256 << 20
RECURSION_LIMIT = 200000

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, 'gen'), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

cpp = None
API = None


def Out(s):
    """Encoding.Latin1.GetBytes(s) written to the C stdout's stream (R.stdout_.S.Write), which writes through."""
    cpp.R_stdout_.S.write(s.encode('latin-1', 'replace'))


def Lit(s):
    """R.Lit(string): a fresh NUL-terminated buffer, each UTF-16 unit of the string stored as its low byte."""
    b = bytearray(s.encode('utf-16-le', 'surrogatepass')[0::2])
    b.append(0)
    return cpp.CPtr(b, 0)


def Str(p):
    """R.Str(CPtr): the Latin-1 text of a C string."""
    B, O = p.B, p.O
    if isinstance(B, (bytearray, bytes)):
        e = B.find(0, O)
        return bytes(B[O:] if e < 0 else B[O:e]).decode('latin-1')
    e = O
    while e < len(B) and B[e] != 0:
        e += 1
    return bytes([x & 255 for x in B[O:e]]).decode('latin-1')


def net_arg(a):
    """A command-line argument as .NET decodes it (UTF-8, an invalid sequence replaced by U+FFFD)."""
    return os.fsencode(a).decode('utf-8', 'replace')


def f2(x):
    """.NET Core's {0:F2} of a double: the exact value rounded half away from zero to two decimals."""
    return str(Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def Main(argv):
    rc = Run(argv)
    cpp.R_FlushAll()          # returning from main calls exit(), which flushes every open FILE
    return rc


def Run(argv):
    if len(argv) < 1:
        Out('\nError: not enough arguments. Use --help for usage.\n\n')
        return 1
    if argv[0] == '--version' or argv[0] == '-v':
        Out('openswmm.engine ' + VERSION + '\n')
        return 0
    if len(argv) < 2:
        Out('\nError: need at least input and report file paths.\n')
        Out('Usage: openswmm <input.inp> <report.rpt> [output.out]\n\n')
        return 1
    inp, rpt = argv[0], argv[1]
    outf = argv[2] if len(argv) > 2 else ''
    _load_engine()
    Out('\n... OpenSWMM Engine ' + VERSION + '\n... Input: ' + inp + '\n... Report: ' + rpt + '\n... Output: ' + outf + '\n')
    cpp.R_fflush(cpp.R_stdout_)
    t0 = time.time()
    engine = API.F__global_swmm_engine_create()
    if engine is None:
        Out('Error: failed to create engine instance.\n')
        return 1
    err = API.F__global_swmm_engine_open(engine, Lit(inp), Lit(rpt), Lit(outf), cpp.CPtr_ZERO)
    if err != 0:
        Out('Error opening input file: ' + Str(API.F__global_swmm_get_last_error_msg(engine)) + '\n')
        API.F__global_swmm_engine_destroy(engine)
        return err
    err = API.F__global_swmm_engine_initialize(engine)
    if err != 0:
        Out('Error initializing: ' + Str(API.F__global_swmm_get_last_error_msg(engine)) + '\n')
        API.F__global_swmm_engine_close(engine)
        API.F__global_swmm_engine_destroy(engine)
        return err
    save = 1 if len(outf) > 0 else 0
    err = API.F__global_swmm_engine_start(engine, save)
    if err != 0:
        Out('Error starting simulation: ' + Str(API.F__global_swmm_get_last_error_msg(engine)) + '\n')
        API.F__global_swmm_engine_close(engine)
        API.F__global_swmm_engine_destroy(engine)
        return err
    elapsed = [0.0]
    ptr = cpp.NewPtrD_TA_l(elapsed, 0)       # new Ptr<double>(elapsed, 0): an immutable value, made once
    step = API.F__global_swmm_engine_step
    steps = 0
    while True:
        err = step(engine, ptr)
        if err != 0:
            Out('\nError at step ' + str(steps) + ': ' + Str(API.F__global_swmm_get_last_error_msg(engine)) + '\n')
            break
        if elapsed[0] <= 0.0:
            break
        steps += 1
    Out('\n... ' + str(steps) + ' steps completed.\n')
    result = [err]

    def Fin(status, phase):
        if status != 0:
            Out('Error ' + phase + ': ' + Str(API.F__global_swmm_get_last_error_msg(engine)) + '\n')
            if result[0] == 0:
                result[0] = status

    Fin(API.F__global_swmm_engine_end(engine), 'ending simulation')
    Fin(API.F__global_swmm_engine_report(engine), 'writing report')
    Fin(API.F__global_swmm_engine_close(engine), 'closing simulation')
    API.F__global_swmm_engine_destroy(engine)
    Out('... OpenSWMM Engine ' + ('completed' if result[0] == 0 else 'failed') + ' in ' + f2(time.time() - t0) + ' seconds.\n\n')
    return result[0]


def _load_engine():
    """Import the engine (its static initialisers run here; .NET runs them on first use, after the argument checks)."""
    global API
    if API is None:
        import engine as _engine
        API = _engine
        gc.freeze()             # the engine's module-level objects are permanent: keep them out of collections


def _crash(e):
    """.NET's unhandled-exception handler: the exception on stderr, then abort (no exit(), so no FILE flushing)."""
    import traceback
    try:
        msg = 'Unhandled exception. ' + type(e).__name__ + ': ' + str(e) + '\n' + ''.join(traceback.format_exception(e))
        b = msg.encode('utf-8', 'replace')
        while b:
            b = b[os.write(2, b):]
    except Exception:
        pass
    os._exit(134)


def main():
    argv = [net_arg(a) for a in sys.argv[1:]]
    box = {}

    def body():
        global cpp
        try:
            import cpp as _cpp
            cpp = _cpp
            box['rc'] = Main(argv)
        except BaseException as e:  # noqa: BLE001 - an exception escaping main ends the process as .NET's does
            box['crash'] = e

    sys.setrecursionlimit(RECURSION_LIMIT)
    threading.stack_size(STACK_BYTES)
    t = threading.Thread(target=body, name='openswmm')
    t.start()
    t.join()
    if 'crash' in box:
        _crash(box['crash'])
    os._exit(box.get('rc', 0) & 0xFF)


if __name__ == '__main__':
    main()
