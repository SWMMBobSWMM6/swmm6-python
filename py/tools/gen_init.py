#!/usr/bin/env python3
# gen_init.py - writes py/cpp/__init__.py, the runtime package's import list, from what is there now: rerun it
# whenever a part module is added, renamed or changes what it defines.
#
#   python3 py/tools/gen_init.py            write py/cpp/__init__.py and report
#   python3 py/tools/gen_init.py --check    only report (missing names, duplicates), exit 1 if a name is missing
#
# What it does:
#   1. reads the names py/gen/engine.py imports (its `from cpp import (...)` statement);
#   2. finds each module of py/cpp and its part from its header comment ("part X of the Python runtime", else the
#      first "part X", else the C# file it says it ports: Std.cs / Str.cs A, CRuntime.cs / Misc.cs / Stash.cs /
#      SysMath C, Containers.cs / StdAlgo.cs / PrimeList.cs B, Streams.cs / Fs.cs D);
#   3. imports the modules in the package's order (part A's, then C's, B's, D's; alphabetical within a part, except
#      the part D modules in EARLY, which come right after part A's because part C's class R extends rfs.RFs), binding
#      each module's names into a stand-in package as __init__ will, so a module that imports a name from the package
#      at its top level (`from cpp import ICopy`) finds it;
#   4. gives every name to the first module that defines it in part order A, C, B, D (a module's names are its
#      __all__, or else what its source defines at the top level without a leading underscore);
#   5. writes __init__.py: the engine's names first, module by module, then every other public name (so that a
#      runtime module can reach another part's internals lazily through the package), then the calls of each
#      module's _package_init() (after all modules are imported) and a re-import of the names that function makes.
# A module that fails to import is reported, and its names are taken from its source.
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
import ast
import importlib
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.dirname(HERE)
CPP = os.path.join(PY, 'cpp')
ENGINE = os.path.join(PY, 'gen', 'engine.py')
OUT = os.path.join(CPP, '__init__.py')

PART_ORDER = 'ACBD'                 # the order the package imports and resolves the parts in
EARLY = ('fsnet', 'rfs')            # part D modules imported right after part A's (rfs.RFs is part C's R's base)
LATE_NAMES = {'rfs': ('R_stderr_', 'R_stdin_')}   # names a module's _package_init() makes
CS_FILE_PART = [('Streams.cs', 'D'), ('Fs.cs', 'D'), ('Containers.cs', 'B'), ('StdAlgo.cs', 'B'),
                ('PrimeList.cs', 'B'), ('CRuntime.cs', 'C'), ('Misc.cs', 'C'), ('Stash.cs', 'C'),
                ('SysMath', 'C'), ('Std.cs', 'A'), ('Str.cs', 'A')]


def engine_names():
    src = open(ENGINE, encoding='utf-8').read()
    m = re.search(r'^from cpp import \((.*?)\)', src, re.S | re.M)
    if not m:
        sys.exit('gen_init: no "from cpp import (...)" in ' + ENGINE)
    return [n.strip() for n in m.group(1).replace('\n', ' ').split(',') if n.strip()]


def header(path):
    lines = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            if not line.startswith('#'):
                if lines and line.strip() == '':
                    break
                if line.strip():
                    break
                continue
            lines.append(line[1:].strip())
            if len(lines) > 80:
                break
    return ' '.join(lines)


def part_of(path):
    h = header(path)
    m = re.search(r'\bpart\s+([A-D])\s+of\s+the\s+python\s+runtime', h, re.I)
    if not m:
        m = re.search(r'\bpart\s+([A-D])\b', h, re.I)
    if m:
        return m.group(1).upper(), 'header says part ' + m.group(1).upper()
    best = None
    for cs, part in CS_FILE_PART:
        i = h.find(cs)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, part, cs)
    if best:
        return best[1], 'header names ' + best[2]
    return None, 'no part in the header'


def source_names(path):
    """The public names a module's source defines at its top level (its __all__ when it has a literal one)."""
    tree = ast.parse(open(path, encoding='utf-8').read(), path)
    names = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == '__all__':
                    try:
                        return list(ast.literal_eval(node.value)), True
                    except ValueError:
                        pass
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for e in (t.elts if isinstance(t, ast.Tuple) else [t]):
                    if isinstance(e, ast.Name):
                        names.append(e.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    seen = set()
    out = []
    for n in names:
        if not n.startswith('_') and n not in seen:
            seen.add(n)
            out.append(n)
    return out, False


def main():
    check = '--check' in sys.argv
    want = engine_names()
    mods = sorted(f[:-3] for f in os.listdir(CPP) if f.endswith('.py') and f != '__init__.py')
    parts = {}
    notes = []
    for m in mods:
        part, why = part_of(os.path.join(CPP, m + '.py'))
        if part is None:
            notes.append('module %s: %s; imported last' % (m, why))
            part = 'Z'
        parts[m] = part

    def key_logical(m):
        p = parts[m]
        return (PART_ORDER.index(p) if p in PART_ORDER else 9, m)

    logical = sorted(mods, key=key_logical)
    early = [m for m in EARLY if m in parts]
    physical = [m for m in logical if parts[m] == 'A'] + early + \
               [m for m in logical if parts[m] != 'A' and m not in early]

    # import the modules in the package's order into a stand-in package, to learn their names
    sys.path.insert(0, PY)
    for k in [k for k in sys.modules if k == 'cpp' or k.startswith('cpp.')]:
        del sys.modules[k]
    pkg = types.ModuleType('cpp')
    pkg.__path__ = [CPP]
    pkg.__package__ = 'cpp'
    pkg.__file__ = OUT
    sys.modules['cpp'] = pkg
    names = {}
    failed = {}
    for m in physical:
        src, has_all = source_names(os.path.join(CPP, m + '.py'))
        try:
            mod = importlib.import_module('cpp.' + m)
        except Exception as e:      # noqa: BLE001 - report and go on with the source's names
            failed[m] = '%s: %s' % (type(e).__name__, e)
            names[m] = src
            continue
        if hasattr(mod, '__all__'):
            ns = [n for n in mod.__all__ if hasattr(mod, n)]
            gone = [n for n in mod.__all__ if not hasattr(mod, n)]
            if gone:
                notes.append('module %s: __all__ names it does not define: %s' % (m, ', '.join(gone)))
        else:
            ns = [n for n in src if hasattr(mod, n)]
        names[m] = ns
        for n in ns:
            if not hasattr(pkg, n):
                setattr(pkg, n, getattr(mod, n))
        if hasattr(mod, '_package_init'):
            pass        # run by the real __init__ after every module is imported

    # every name to the first module in logical order that defines it
    owner = {}
    dups = {}
    for m in logical:
        for n in names.get(m, ()):
            if n in owner:
                if owner[n] != m:
                    dups.setdefault(n, [owner[n]]).append(m)
            else:
                owner[n] = m
    for m, late in LATE_NAMES.items():
        for n in late:
            if n not in owner and m in names:
                owner[n] = m
    missing = [n for n in want if n not in owner]
    want_set = set(want)

    # report
    print('gen_init: %d engine names, %d modules' % (len(want), len(mods)))
    for m in physical:
        k = sum(1 for n in want if owner.get(n) == m)
        print('  part %s  %-14s %4d engine names, %4d names%s' % (parts[m], m, k, len(names.get(m, ())),
              '  IMPORT FAILED: ' + failed[m] if m in failed else ''))
    for n in notes:
        print('  note: ' + n)
    if dups:
        print('  defined in more than one module (the first wins): ' +
              '; '.join('%s (%s)' % (n, ', '.join(v)) for n, v in sorted(dups.items())))
    if missing:
        print('  MISSING (%d): %s' % (len(missing), ' '.join(missing)))
    if check:
        return 1 if missing or failed else 0

    # write __init__.py
    out = []
    out.append('# __init__.py - the runtime package of the Python SWMM6 engine (py/gen/engine.py imports its names).')
    out.append('# GENERATED by py/tools/gen_init.py from the part modules; do not edit, rerun it instead.')
    out.append('# Part A\'s modules first, then part C\'s, B\'s and D\'s (fsnet and rfs of part D right after part A\'s,')
    out.append('# since part C\'s R extends rfs.RFs); a name defined twice comes from the first module in part order')
    out.append('# A, C, B, D. Every other public name follows, for lazy use across parts; then each module\'s')
    out.append('# _package_init() runs, once every module is imported.')
    out.append('# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.')
    if missing:
        line = '# MISSING engine names (no module defines them):'
        for n in missing:
            if len(line) + len(n) + 1 > 118:
                out.append(line)
                line = '#  '
            line += ' ' + n
        out.append(line)
    if dups:
        line = '# defined twice (the first wins):'
        for n, v in sorted(dups.items()):
            piece = ' %s (%s);' % (n, ', '.join(v))
            if len(line) + len(piece) > 118:
                out.append(line)
                line = '#  '
            line += piece
        out.append(line)
    out.append('')

    def emit(m, ns, tag):
        if not ns:
            return
        out.append('from .%s import (  # part %s%s' % (m, parts[m], tag))
        line = '   '
        for n in ns:
            piece = ' ' + n + ','
            if len(line) + len(piece) > 118:
                out.append(line)
                line = '   '
            line += piece
        out.append(line)
        out.append(')')

    late_all = {n for v in LATE_NAMES.values() for n in v}
    for m in physical:
        ns = [n for n in want if owner.get(n) == m and n not in late_all]
        emit(m, ns, ', engine names')
    out.append('')
    out.append('# every other public name of the runtime')
    for m in physical:
        ns = [n for n in names.get(m, ()) if owner.get(n) == m and n not in want_set and n not in late_all]
        emit(m, ns, '')
    out.append('')
    for m in physical:
        if m in failed:
            continue
        mod = sys.modules.get('cpp.' + m)
        if mod is not None and hasattr(mod, '_package_init'):
            out.append('from .%s import _package_init as _pi_%s' % (m, m))
            out.append('_pi_%s()' % m)
            late = [n for n in LATE_NAMES.get(m, ()) if owner.get(n) == m]
            if late:
                out.append('from .%s import %s' % (m, ', '.join(late)))
    out.append('')
    text = '\n'.join(out)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(text)
    print('gen_init: wrote %s (%d lines)' % (OUT, text.count('\n')))
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
