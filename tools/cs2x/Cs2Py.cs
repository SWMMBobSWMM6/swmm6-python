// Cs2Py - translate the generated C# SWMM6 engine (cs/gen and cs/runtime/Hand_*.cs) into one Python module
// (py/gen/engine.py), using Roslyn's semantic model, the way Cs2Go and Cs2Java write the Go and Java engines. The
// design is recorded in PY.md; the runtime contract the module relies on in py/RUNTIME_HELPERS.md.
//
//   dotnet bin/cs2py.dll CS_DIR PY_GEN_DIR
//
// writes PY_GEN_DIR/engine.py, PY_GEN_DIR/../cs2py_runtime.txt (every runtime name the module imports, with the C#
// member it stands for) and PY_GEN_DIR/../cs2py_todo.txt (untranslated constructs; the goal is none).
//
// Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Operations;

public sealed class Todo : Exception { public Todo(string m) : base(m) { } }

public static partial class Cs2Py
{
    static CSharpCompilation comp = null!;
    static readonly Dictionary<SyntaxTree, SemanticModel> models = new();
    static SemanticModel M(SyntaxNode n) => models[n.SyntaxTree];
    static readonly SortedDictionary<string, int> todos = new();
    /// <summary>Python runtime name -> the C# member(s) it stands for (the manifest).</summary>
    static readonly SortedDictionary<string, SortedSet<string>> manifest = new(StringComparer.Ordinal);
    static readonly SortedDictionary<string, int> stats = new();

    public static int Main(string[] args)
    {
        var cs = args[0];
        var outDir = args[1];
        var files = Directory.GetFiles(Path.Combine(cs, "gen"), "*.cs").Concat(Directory.GetFiles(Path.Combine(cs, "runtime"), "*.cs"))
            .Where(f => !f.EndsWith("Program.cs")).OrderBy(f => f, StringComparer.Ordinal).ToArray();
        var opts = new CSharpParseOptions(LanguageVersion.Latest);
        var trees = files.Select(f => CSharpSyntaxTree.ParseText(File.ReadAllText(f), opts, f)).ToList();
        var fw = Path.GetDirectoryName(typeof(object).Assembly.Location)!;
        var refs = new List<MetadataReference>();
        foreach (var f in Directory.GetFiles(fw, "*.dll"))
        {
            var b = Path.GetFileName(f);
            if (b.StartsWith("Microsoft.VisualBasic") || b == "mscorlib.dll") continue;
            try { refs.Add(MetadataReference.CreateFromFile(f)); } catch { }
        }
        comp = CSharpCompilation.Create("engine", trees, refs, new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true));
        var errs = comp.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error).ToList();
        if (errs.Count > 0) { foreach (var e in errs.Take(10)) Console.Error.WriteLine(e); return 1; }
        foreach (var t in trees) models[t] = comp.GetSemanticModel(t);
        // the engine: the generated C#, and the hand-written engine parts kept with the runtime (Hand_*.cs)
        var gen = trees.Where(t => t.FilePath.Contains("/gen/") || Path.GetFileName(t.FilePath).StartsWith("Hand_")).ToList();

        Directory.CreateDirectory(outDir);
        CollectTypes(gen);
        CollectRuntimeNames();
        Analyze(gen);
        var text = Module(gen);
        File.WriteAllText(Path.Combine(outDir, "engine.py"), text);
        using (var w = new StreamWriter(Path.Combine(outDir, "..", "cs2py_todo.txt")))
            foreach (var kv in todos.OrderByDescending(kv => kv.Value).ThenBy(kv => kv.Key, StringComparer.Ordinal)) w.WriteLine("{0,6}  {1}", kv.Value, kv.Key);
        using (var w = new StreamWriter(Path.Combine(outDir, "..", "cs2py_runtime.txt")))
        {
            w.WriteLine("# The runtime names py/gen/engine.py uses: Python name, tab, the C# member(s) it stands for (a .name is a member of runtime objects). See py/RUNTIME_HELPERS.md.");
            w.WriteLine("# Written by tools/cs2x/Cs2Py.cs. Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.");
            foreach (var kv in manifest) w.WriteLine(kv.Key + "\t" + string.Join(" | ", kv.Value));
        }
        if (Environment.GetEnvironmentVariable("CS2PY_STATS") != null)
            foreach (var kv in stats.OrderByDescending(kv => kv.Value)) Console.Error.WriteLine("{0,7}  {1}", kv.Value, kv.Key);
        Console.WriteLine("wrote engine.py (" + text.Count(c => c == '\n') + " lines), " + manifest.Count + " runtime names, " + todos.Count + " TODO kinds, " + todos.Values.Sum() + " TODO sites");
        return 0;
    }

    static void Note(string what) { todos[what] = todos.TryGetValue(what, out var c) ? c + 1 : 1; }
    static void Stat(string what) { stats[what] = stats.TryGetValue(what, out var c) ? c + 1 : 1; }

    static string Todo(string what)
    {
        todos[what] = todos.TryGetValue(what, out var c) ? c + 1 : 1;
        throw new Todo(what);
    }

    /// <summary>Record that the module imports runtime name py (a module-level function, class or constant) for
    /// the C# member cs; returns py.</summary>
    static string Use(string py, string cs)
    {
        if (!manifest.TryGetValue(py, out var set)) manifest[py] = set = new SortedSet<string>(StringComparer.Ordinal);
        set.Add(cs);
        return py;
    }

    /// <summary>Record that the module uses member py (a method, field or property of runtime objects) for the C#
    /// member cs; returns py. The manifest lists these as .py.</summary>
    static string UseM(string py, string cs)
    {
        var k = "." + py;
        if (!manifest.TryGetValue(k, out var set)) manifest[k] = set = new SortedSet<string>(StringComparer.Ordinal);
        set.Add(cs);
        return py;
    }

    /// <summary>The module-level helpers the translator writes calls to (the runtime defines them; RUNTIME_HELPERS.md).</summary>
    static readonly HashSet<string> HelperNames = new()
    {
        "Cell", "ArrRef", "GotoSignal", "SysException", "throw_", "f64_from_bits", "f64_to_bits_signed", "signbit",
        "cs_tostring", "cs_equals", "exc_message", "idiv", "irem", "fmod", "fdiv0", "f32", "f2i32", "f2u32", "f2i64",
        "f2u64", "f2i16", "f2u16", "f2i8", "f2u8", "U32", "U64", "I64", "U8", "I8", "I16", "U16", "Ch", "F32",
        "MK_Z", "MK_D", "MK_I", "MK_N", "Array_Fill", "File_Exists", "Math", "Array", "File", "IDisposable"
    };

    // ------------------------------------------------------------------------------------------------ names
    static readonly HashSet<string> PyKeywords = new()
    {
        "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del",
        "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda",
        "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield", "match", "case", "type", "_",
        "print", "exec"
    };

    /// <summary>Names a local may not take: builtins the module calls, and module-level names it may use.</summary>
    static readonly HashSet<string> PyBuiltins = new()
    {
        "int", "float", "len", "range", "isinstance", "abs", "str", "bool", "list", "tuple", "object", "Exception",
        "BaseException", "type", "super", "min", "max", "id", "iter", "next", "divmod", "ord", "chr", "hash", "repr",
        "NotImplementedError", "ZeroDivisionError", "ValueError", "OverflowError", "IndexError", "KeyError",
        "AttributeError", "TypeError", "RuntimeError", "StopIteration", "ArithmeticError", "dict", "set", "bytes",
        "bytearray", "slice", "map", "filter", "zip", "any", "all", "sum", "round", "pow", "format", "open", "getattr",
        "setattr", "hasattr", "callable", "self", "math", "cpp", "sorted", "reversed", "enumerate", "property",
        "staticmethod", "classmethod", "memoryview", "frozenset", "vars", "locals", "globals", "input", "compile", "eval",
        "help", "dir", "delattr", "issubclass", "complex", "oct", "hex", "bin", "ascii", "breakpoint", "copyright",
        "credits", "license", "exit", "quit", "Ellipsis", "NotImplemented", "__import__", "__name__", "__file__",
        "__doc__", "__builtins__", "__spec__", "__loader__", "__package__", "__debug__", "__build_class__"
    };

    /// <summary>A Python identifier for a C# one: no keyword, no character Python rejects.</summary>
    static string PyIdent(string n)
    {
        n = n.TrimStart('@');
        var sb = new StringBuilder();
        foreach (var ch in n) sb.Append(char.IsLetterOrDigit(ch) || ch == '_' ? ch : '_');
        n = sb.ToString();
        if (n.Length == 0 || char.IsDigit(n[0])) n = "_" + n;
        return PyKeywords.Contains(n) ? n + "_" : n;
    }

    /// <summary>An attribute name (field or method): no keyword, and no leading double underscore, which Python
    /// would mangle inside a class body.</summary>
    static string AttrIdent(string n)
    {
        n = PyIdent(n);
        if (n.StartsWith("__") && !n.EndsWith("__")) n = "m" + n;
        if (n.StartsWith("__") && n.EndsWith("__")) n = "m" + n;
        return n;
    }

    static bool IsRuntime(ISymbol s)
    {
        var ns = s is INamespaceSymbol nss ? nss : s.ContainingNamespace;
        while (ns != null && !ns.IsGlobalNamespace)
        {
            if (ns.Name == "Cpp" && ns.ContainingNamespace.IsGlobalNamespace) return true;
            ns = ns.ContainingNamespace;
        }
        return false;
    }

    static bool IsSystem(ISymbol s)
    {
        var ns = s.ContainingNamespace;
        while (ns != null && !ns.IsGlobalNamespace) { if (ns.Name == "System" && ns.ContainingNamespace.IsGlobalNamespace) return true; ns = ns.ContainingNamespace; }
        return false;
    }

    // every module-level name the engine defines, and the runtime's type names: a local must not hide one
    static readonly HashSet<string> engineGlobals = new();
    static readonly HashSet<string> runtimeTypeNames = new();

    static void CollectRuntimeNames()
    {
        void Walk(INamespaceOrTypeSymbol ns)
        {
            foreach (var m in ns.GetMembers())
            {
                if (m is INamedTypeSymbol t)
                {
                    var parts = new List<string>();
                    for (var c = t; c != null; c = c.ContainingType) parts.Insert(0, StripArity(c.Name));
                    var b = string.Join("_", parts);
                    runtimeTypeNames.Add(b);
                    foreach (var code in new[] { "D", "F", "I", "L", "B", "Z", "S", "C" }) runtimeTypeNames.Add(b + code);
                    runtimeTypeNames.Add("New" + b);
                    Walk(t);
                }
                else if (m is INamespaceSymbol n) Walk(n);
            }
        }
        foreach (var ns in comp.GlobalNamespace.GetNamespaceMembers().Where(n => n.Name == "Cpp")) Walk(ns);
    }

    /// <summary>Whether n could be a module-level name: one the engine defines, a runtime name (a runtime type name,
    /// optionally followed by _member), or one of the translator's own prefixes.</summary>
    static bool IsGlobalName(string n)
    {
        if (PyBuiltins.Contains(n) || engineGlobals.Contains(n) || runtimeTypeNames.Contains(n) || HelperNames.Contains(n)) return true;
        var i = n.IndexOf('_');
        if (i > 0 && runtimeTypeNames.Contains(n.Substring(0, i))) return true;
        foreach (var p in ReservedPrefixes) if (n.StartsWith(p)) return true;
        return false;
    }

    static readonly string[] ReservedPrefixes = { "FR_", "SR_", "_K", "_si_", "mk_", "MK_", "cs_", "_m_", "_G", "_INF", "_NAN" };

    static readonly Dictionary<ISymbol, string> typeNames = new(SymbolEqualityComparer.Default);

    /// <summary>The Python name of an engine type: its namespace below openswmm and its class nesting, joined by _
    /// (the Java translator's rule; a namespace's free functions class _F is F_, F_ns).</summary>
    static string TypeIdent(INamedTypeSymbol t)
    {
        t = t.OriginalDefinition;
        if (typeNames.TryGetValue(t, out var n)) return n;
        var parts = new List<string>();
        for (var c = t; c != null; c = c.ContainingType) parts.Insert(0, c.Name);
        var ns = new List<string>();
        for (var x = t.ContainingNamespace; x != null && !x.IsGlobalNamespace; x = x.ContainingNamespace) ns.Insert(0, x.Name);
        if (ns.Count > 0 && (ns[0] == "openswmm" || ns[0] == "_global" || ns[0] == "Gen")) ns.RemoveAt(0);
        var s = PyIdent(string.Join("_", ns.Concat(parts)));
        if (t.Name == "_F" && t.ContainingType == null)
        {
            var full = new List<string>();
            for (var x = t.ContainingNamespace; x != null && !x.IsGlobalNamespace; x = x.ContainingNamespace) full.Insert(0, x.Name);
            s = full.Count == 1 && full[0] == "openswmm" ? "F" : full.Count > 0 && full[0] == "openswmm" ? "F_" + string.Join("_", full.Skip(1)) : "F_" + string.Join("_", full);
        }
        while (PyKeywords.Contains(s) || PyBuiltins.Contains(s) || runtimeTypeNames.Contains(s) || s.StartsWith("__")) s += "_";
        typeNames[t] = s;
        return s;
    }

    /// <summary>A static method's module-level name: its type's name and the method's (as Go names them).</summary>
    static string StaticFuncName(IMethodSymbol m) => TypeIdent(m.ContainingType) + "_" + MethodIdent(m);

    static string StaticFieldName(IFieldSymbol f) => TypeIdent(f.ContainingType) + "_" + PyIdent(f.Name);

    static readonly Dictionary<IMethodSymbol, string> methodNames = new(SymbolEqualityComparer.Default);

    /// <summary>An engine method's Python name. Every Python method call is virtual, so overloads in one class, and a
    /// method that overloads or hides (rather than overrides) one of a base class, get a suffix of their parameter
    /// types (Go's rule); a hiding method with the same parameters also gets its class's name; an override keeps
    /// the name of the method it overrides. A method that would share
    /// its name with a field anywhere in its hierarchy gets M_. A method implementing a runtime interface member
    /// keeps that member's name.</summary>
    static string MethodIdent(IMethodSymbol m)
    {
        m = m.OriginalDefinition;
        if (m.OverriddenMethod != null) return MethodIdent(m.OverriddenMethod);
        if (methodNames.TryGetValue(m, out var n)) return n;
        var t = m.ContainingType;
        var nm = AttrIdent(m.Name);
        var impl = !m.IsStatic ? RuntimeInterfaceMember(m) : null;
        if (impl != null) { methodNames[m] = impl; return impl; }
        var same = t.GetMembers(m.Name).OfType<IMethodSymbol>().Where(x => x.MethodKind == MethodKind.Ordinary).ToList();
        bool overloaded = same.Count > 1;
        bool hidesSame = false;
        if (!m.IsStatic)
        {
            // a method of a base class with this name that m does not override: m overloads or hides it (a new
            // virtual method starts its own chain of overrides), so it needs a name of its own
            for (var b = t.BaseType; b != null && !IsSystem(b) && !IsRuntime(b); b = b.BaseType)
                foreach (var x in b.GetMembers(m.Name).OfType<IMethodSymbol>())
                    if (x.MethodKind == MethodKind.Ordinary && !x.IsStatic && !IsOverrideOf(m, x))
                    { overloaded = true; if (SameParams(x, m)) hidesSame = true; }
        }
        if (overloaded)
            nm += "__" + string.Join("_", m.Parameters.Select(p => ShortType(p.Type) + (p.RefKind != RefKind.None ? "r" : "")));
        if (hidesSame) nm += "__" + TypeIdent(t);
        if (!m.IsStatic && HierarchyHasField(t, nm)) nm = "M_" + nm;
        if (nm == "__init__" || nm == "__slots__") nm = "m" + nm;
        methodNames[m] = nm;
        return nm;
    }

    static bool SameParams(IMethodSymbol a, IMethodSymbol b) =>
        a.Parameters.Length == b.Parameters.Length && a.Parameters.Zip(b.Parameters).All(p => SymbolEqualityComparer.Default.Equals(p.First.Type, p.Second.Type) && p.First.RefKind == p.Second.RefKind);

    /// <summary>The runtime interface member (ICopy.Clone, IHashFn.HashOf, IBinFn.Call2, IDisposable.Dispose, ...)
    /// m implements, by name: the runtime calls it by that name.</summary>
    static string? RuntimeInterfaceMember(IMethodSymbol m)
    {
        foreach (var i in m.ContainingType.AllInterfaces)
        {
            if (!IsRuntime(i) && !IsSystem(i)) continue;
            foreach (var im in i.GetMembers().OfType<IMethodSymbol>())
            {
                var impl = m.ContainingType.FindImplementationForInterfaceMember(im);
                if (SymbolEqualityComparer.Default.Equals(impl?.OriginalDefinition, m)) return im.Name;
            }
        }
        return null;
    }

    static bool IsOverrideOf(IMethodSymbol m, IMethodSymbol b)
    {
        for (var o = m.OverriddenMethod; o != null; o = o.OverriddenMethod) if (SymbolEqualityComparer.Default.Equals(o.OriginalDefinition, b.OriginalDefinition)) return true;
        return false;
    }

    static bool Derives(INamedTypeSymbol d, INamedTypeSymbol b)
    {
        for (var x = d.BaseType; x != null; x = x.BaseType) if (SymbolEqualityComparer.Default.Equals(x.OriginalDefinition, b.OriginalDefinition)) return true;
        return false;
    }

    /// <summary>The root of t's engine class hierarchy.</summary>
    static INamedTypeSymbol RootOf(INamedTypeSymbol t)
    {
        var r = t.OriginalDefinition;
        while (r.BaseType != null && IsEngine(r.BaseType)) r = r.BaseType.OriginalDefinition;
        return r;
    }

    /// <summary>Whether any class of t's hierarchy (ancestors and descendants) has an instance field named nm.</summary>
    static bool HierarchyHasField(INamedTypeSymbol t, string nm)
    {
        var root = RootOf(t);
        foreach (var c in engineTypes)
        {
            if (!SymbolEqualityComparer.Default.Equals(RootOf(c), root)) continue;
            if (c.GetMembers().OfType<IFieldSymbol>().Any(f => !f.IsStatic && !f.IsConst && FieldIdent(f) == nm)) return true;
        }
        return false;
    }

    static string ShortType(ITypeSymbol t)
    {
        switch (t.SpecialType)
        {
            case SpecialType.System_Int32: return "i";
            case SpecialType.System_Int64: return "l";
            case SpecialType.System_UInt64: return "ul";
            case SpecialType.System_UInt32: return "u";
            case SpecialType.System_Double: return "d";
            case SpecialType.System_Single: return "f";
            case SpecialType.System_Boolean: return "b";
            case SpecialType.System_SByte: return "c";
            case SpecialType.System_Byte: return "uc";
            case SpecialType.System_Int16: return "s";
            case SpecialType.System_UInt16: return "us";
            case SpecialType.System_Char: return "ch";
            case SpecialType.System_Object: return "o";
            case SpecialType.System_String: return "str";
        }
        if (t is IArrayTypeSymbol at) return ShortType(at.ElementType) + "A";
        if (t is ITypeParameterSymbol tp) return tp.Name;
        if (t is INamedTypeSymbol nt)
        {
            var s = nt.Name;
            if (nt.TypeArguments.Length > 0) s += string.Join("", nt.TypeArguments.Select(ShortType));
            return s;
        }
        return "x";
    }

    static readonly Dictionary<IFieldSymbol, string> fieldNames = new(SymbolEqualityComparer.Default);

    /// <summary>An instance field's attribute name. C# lets a derived class hide a base field with its own; Python
    /// attributes would merge them, so the hiding field gets its class's name.</summary>
    static string FieldIdent(IFieldSymbol f)
    {
        f = f.OriginalDefinition;
        if (fieldNames.TryGetValue(f, out var n)) return n;
        n = AttrIdent(f.Name);
        if (!f.IsStatic && IsEngine(f.ContainingType))
            for (var b = f.ContainingType.BaseType; b != null && b.SpecialType != SpecialType.System_Object && !IsSystem(b); b = b.BaseType)
                if (b.GetMembers(f.Name).OfType<IFieldSymbol>().Any(x => !x.IsStatic)) { n += "_" + TypeIdent(f.ContainingType); Stat("hidden field (renamed) " + f.Name); break; }
        fieldNames[f] = n;
        return n;
    }

    static string StripArity(string s) { var i = s.IndexOf('`'); return i < 0 ? s : s.Substring(0, i); }
}
