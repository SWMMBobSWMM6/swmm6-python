// PyFn.cs - method bodies: C# statements and expressions (Roslyn IOperation trees) to Python.
// Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
//
// Numbers (PY.md): a C# integer is a Python int in its type's value range ("clean"); an expression whose value may
// lie outside it ("raw": the result of + - * << and unary -) is reduced only where it is stored or used by an
// operation whose result depends on the width (comparison, / % >>, conversion, index, argument, return). A C# double
// is a Python float wherever it is stored; an int converted to double ("xint", exact for 8..32-bit sources) is passed
// to Python's mixed arithmetic directly, which converts it exactly, and made a float with float() where it is stored.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Operations;

public static partial class Cs2Py
{
    /// <summary>A translated expression: its text (always atomic or parenthesised), whether an integer value may lie
    /// outside its type's range, and whether a double value is still a Python int.</summary>
    public readonly struct Ex
    {
        public readonly string S;
        public readonly bool Raw;
        public readonly bool XInt;
        public Ex(string s, bool raw = false, bool xint = false) { S = s; Raw = raw; XInt = xint; }
        public static implicit operator Ex(string s) => new Ex(s);
        public static implicit operator string(Ex e) => e.S;
        public override string ToString() => S;
    }

    // references to fields (ref this.x, ref o.x, ref S.x): one small class each
    static string FieldRefHelper(IFieldSymbol f)
    {
        f = f.OriginalDefinition;
        string name, text;
        if (f.IsStatic)
        {
            var g = StaticFieldName(f);
            name = "SR_" + g;
            text = "class " + name + ":\n    __slots__ = ()\n\n    @property\n    def v(self):\n        return " + g + "\n\n    @v.setter\n    def v(self, x):\n        global " + g + "\n        " + g + " = x\n\n";
        }
        else
        {
            var fn = FieldIdent(f);
            name = "FR_" + TypeIdent(f.ContainingType) + "_" + fn;
            text = "class " + name + ":\n    __slots__ = ('o',)\n\n    def __init__(self, o):\n        self.o = o\n\n    @property\n    def v(self):\n        return self.o." + fn + "\n\n    @v.setter\n    def v(self, x):\n        self.o." + fn + " = x\n\n";
        }
        helperClasses[name] = text;
        return name;
    }

    /// <summary>The engine types whose static fields an initialiser reads, directly or through the engine methods and
    /// constructors it calls.</summary>
    static HashSet<INamedTypeSymbol> StaticDeps(IOperation root)
    {
        var res = new HashSet<INamedTypeSymbol>(SymbolEqualityComparer.Default);
        var seen = new HashSet<IMethodSymbol>(SymbolEqualityComparer.Default);
        var work = new Stack<IOperation>();
        work.Push(root);
        while (work.Count > 0)
        {
            var op = work.Pop();
            foreach (var d in op.DescendantsAndSelf())
            {
                IMethodSymbol? callee = null;
                switch (d)
                {
                    case IFieldReferenceOperation fr when fr.Field.IsStatic && !fr.Field.IsConst && IsEngine(fr.Field.ContainingType):
                        res.Add(fr.Field.ContainingType.OriginalDefinition); break;
                    case IInvocationOperation inv: callee = inv.TargetMethod; break;
                    case IObjectCreationOperation oc: callee = oc.Constructor; break;
                    case IMethodReferenceOperation mr: callee = mr.Method; break;
                }
                if (callee == null || !IsEngine(callee.ContainingType)) continue;
                callee = callee.OriginalDefinition;
                if (!seen.Add(callee)) continue;
                foreach (var sr in callee.DeclaringSyntaxReferences)
                {
                    var syn = sr.GetSyntax();
                    IOperation? body = syn switch
                    {
                        BaseMethodDeclarationSyntax bm => bm.Body != null ? M(bm).GetOperation(bm.Body) : bm.ExpressionBody != null ? M(bm).GetOperation(bm.ExpressionBody) : null,
                        _ => null
                    };
                    if (body != null) work.Push(body);
                }
            }
        }
        return res;
    }

public sealed class PyFn
{
    readonly IMethodSymbol? method;
    readonly INamedTypeSymbol cls;
    public List<string> Pre = new();
    readonly Dictionary<ISymbol, string> names;
    readonly HashSet<string> used;
    readonly int[] tmp;
    readonly List<string> prologue = new();
    readonly SortedSet<string> globalsAssigned = new(StringComparer.Ordinal);
    readonly SortedSet<string> nonlocals = new(StringComparer.Ordinal);
    readonly PyFn? parent;
    readonly IMethodSymbol? lambdaSym;
    ITypeSymbol? retType;
    bool retRef;
    readonly int baseInd;
    public HashSet<INamedTypeSymbol> Deps = new(SymbolEqualityComparer.Default);
    /// <summary>Symbols that are value-bound refs: a ref local to a class-typed element never written through.</summary>
    readonly HashSet<ISymbol> refByValue;
    /// <summary>Range-for variables known to be non-negative inside their loop.</summary>
    readonly HashSet<ISymbol> nonNeg;

    public PyFn(IMethodSymbol? m, INamedTypeSymbol c, int ind)
    {
        method = m; cls = c; baseInd = ind;
        names = new(SymbolEqualityComparer.Default); used = new(); tmp = new int[1];
        retType = m?.ReturnType; retRef = m != null && m.RefKind != RefKind.None;
        refByValue = new(SymbolEqualityComparer.Default);
        nonNeg = new(SymbolEqualityComparer.Default);
    }

    PyFn(PyFn par, IMethodSymbol lambda)
    {
        parent = par; lambdaSym = lambda;
        method = lambda; cls = par.cls; baseInd = 1;
        names = par.names; used = par.used; tmp = par.tmp;
        retType = lambda.ReturnType; retRef = lambda.RefKind != RefKind.None;
        gotoLabels = null;
        refByValue = par.refByValue; nonNeg = par.nonNeg;
    }

    public void CollectDeps(IOperation op, INamedTypeSymbol owner) { Deps = StaticDeps(op); }

    string Fresh(string b)
    {
        string n;
        do { tmp[0]++; n = "_" + b + tmp[0]; } while (used.Contains(n));
        used.Add(n);
        return n;
    }

    string Declare(ISymbol s, string name)
    {
        var n = PyIdent(name);
        if (n == "_" || n == "__" || n == "_" + "_") n = "_discard";
        if (n.StartsWith("__")) n = "v" + n;
        if (IsGlobalName(n) || PyKeywords.Contains(n)) n = "v_" + n;
        var b = n; int k = 2;
        while (used.Contains(n) || IsGlobalName(n) || PyKeywords.Contains(n)) n = b + "_" + k++;
        used.Add(n);
        names[s] = n;
        return n;
    }

    static bool Boxed(ISymbol s) => boxed.Contains(s);

    /// <summary>Declare a parameter; a cell parameter is put into its Cell by the prologue.</summary>
    public string DeclareParam(IParameterSymbol p)
    {
        var n = Declare(p, p.Name);
        if (p.RefKind is RefKind.Ref or RefKind.Out) return n;
        if (Boxed(p)) prologue.Add(n + " = " + Use("Cell", "a cell (.v) for a local whose address is taken") + "(" + n + ")");
        return n;
    }

    /// <summary>The global and nonlocal declarations and the cell set-up that go first in the function.</summary>
    public string FuncPrologue(int ind)
    {
        var sb = new StringBuilder();
        if (globalsAssigned.Count > 0) sb.Append(Ind(ind)).Append("global ").Append(string.Join(", ", globalsAssigned)).Append('\n');
        if (nonlocals.Count > 0) sb.Append(Ind(ind)).Append("nonlocal ").Append(string.Join(", ", nonlocals)).Append('\n');
        foreach (var p in prologue) sb.Append(Ind(ind)).Append(p).Append('\n');
        return sb.ToString();
    }

    static string Ind(int n) => new string(' ', 4 * n);

    public void FlushTo(List<string> lines, int ind)
    {
        foreach (var p in Pre) lines.Add(Ind(ind) + p);
        Pre.Clear();
    }

    /// <summary>Add a (possibly multi-line, relatively indented) statement to Pre.</summary>
    void PreAdd(string stmt) { foreach (var l in stmt.Split('\n')) Pre.Add(l); }

    void PreAddLines(List<string> ls, int rel) { foreach (var l in ls) Pre.Add(Ind(rel) + l); }

    /// <summary>A module-level variable the function assigns: a global declaration (or nonlocal in a lambda of a
    /// function... globals are module-level in every case).</summary>
    void AssignGlobal(string g) { globalsAssigned.Add(g); }

    /// <summary>A local of an enclosing function that a lambda assigns: nonlocal in the lambda and every lambda
    /// between.</summary>
    void AssignOuter(ISymbol s)
    {
        if (parent == null) return;
        if (IsOwnLocal(s)) return;
        nonlocals.Add(names[s]);
        parent.AssignOuter(s);
    }

    bool IsOwnLocal(ISymbol s)
    {
        if (lambdaSym == null) return true;
        return SymbolEqualityComparer.Default.Equals(s.ContainingSymbol, lambdaSym) || IsInside(s.ContainingSymbol, lambdaSym) && !IsInsideNestedLambda(s);
    }

    bool IsInsideNestedLambda(ISymbol s) => false;

    // -------------------------------------------------------------------------------------------- statements
    // goto: a method whose labels all sit in its top-level block becomes a dispatch loop over segments
    Dictionary<ILabelSymbol, int>? gotoLabels;
    int dispatchDepth = -1;      // the nesting of loops and switch wrappers inside the dispatch loop

    readonly List<Ctx> ctx = new();

    sealed class Ctx
    {
        public bool IsLoop;
        public ILabelSymbol? Exit, Cont;
        public List<string>? Step;       // a for loop run as while: the step before each continue
        public IOperation? DoCond;       // a do-while: the condition before each continue
        public bool Wrapper;             // a switch run inside while True (a break that is not in tail position)
        public string? ContFlag;         // set by a continue that leaves the wrapper
    }

    public string Body(IOperation op, IMethodSymbol m)
    {
        var lines = new List<string>();
        var ops = op is IBlockOperation b ? b.Operations.ToList() : op is IMethodBodyOperation mb && mb.BlockBody != null ? mb.BlockBody.Operations.ToList() : new List<IOperation> { op };
        if (op.Descendants().Any(d => d is IBranchOperation br && br.BranchKind == BranchKind.GoTo))
            Dispatch(ops, lines, baseInd);
        else
            Block(ops, lines, baseInd);
        return string.Concat(lines.Select(l => l + "\n"));
    }

    void Dispatch(List<IOperation> ops, List<string> lines, int ind)
    {
        gotoLabels = new(SymbolEqualityComparer.Default);
        int k = 0;
        foreach (var o in ops) if (o is ILabeledOperation l) gotoLabels[l.Label] = ++k;
        foreach (var lb in op_labels(ops)) if (!gotoLabels.ContainsKey(lb)) Note("label below the top level of a method");
        var st = Fresh("st");
        stVar = st;
        int start = lines.Count;
        lines.Add(Ind(ind) + st + " = 0");
        lines.Add(Ind(ind) + "while True:");
        var seg = new List<IOperation>();
        int segNo = 0;
        dispatchDepth = 0;
        void FlushSeg()
        {
            lines.Add(Ind(ind + 1) + "if " + st + " <= " + segNo + ":");
            var n0 = lines.Count;
            Block(seg, lines, ind + 2);
            if (lines.Count == n0) lines.Add(Ind(ind + 2) + "pass");
            seg.Clear();
        }
        foreach (var o in ops)
        {
            if (o is ILabeledOperation l)
            {
                FlushSeg();
                segNo = gotoLabels[l.Label];
                if (l.Operation != null) seg.Add(l.Operation);
            }
            else seg.Add(o);
        }
        FlushSeg();
        lines.Add(Ind(ind + 1) + "break");
        dispatchDepth = -1;
        if (gotoRaised)
        {
            // a goto from inside a loop raises GotoSignal(k): the dispatch loop catches it and goes to segment k
            int at = lines.IndexOf(Ind(ind) + "while True:", start) + 1;
            for (int i = at; i < lines.Count; i++) lines[i] = "    " + lines[i];
            lines.Insert(at, Ind(ind + 1) + "try:");
            var g = Fresh("g");
            lines.Add(Ind(ind + 1) + "except " + Use("GotoSignal", "an exception class: raise GotoSignal(k) leaves loops for a goto; .k is the target") + " as " + g + ":");
            lines.Add(Ind(ind + 2) + st + " = " + g + ".k");
            Stat("goto from a loop");
        }
    }

    string? stVar;

    static IEnumerable<ILabelSymbol> op_labels(List<IOperation> ops) => ops.SelectMany(o => o.DescendantsAndSelf()).OfType<ILabeledOperation>().Select(l => l.Label);

    void Block(IEnumerable<IOperation> ops, List<string> lines, int ind)
    {
        var list = ops.ToList();
        for (int i = 0; i < list.Count; i++)
        {
            if (list[i] is IUsingDeclarationOperation ud) { UsingRest(ud, list.Skip(i + 1).ToList(), lines, ind); return; }
            curBlock = list; curIndex = i;
            Stmt(list[i], lines, ind);
        }
    }

    List<IOperation>? curBlock; int curIndex;

    void Sub(IOperation op, List<string> lines, int ind)
    {
        var n0 = lines.Count;
        if (op is IBlockOperation b) Block(b.Operations, lines, ind);
        else Stmt(op, lines, ind);
        if (lines.Count == n0) lines.Add(Ind(ind) + "pass");
    }

    void Stmt(IOperation op, List<string> lines, int ind)
    {
        try { StmtInner(op, lines, ind); }
        catch (global::Todo e)
        {
            Pre.Clear();
            lines.Add(Ind(ind) + "raise NotImplementedError(" + PyStr("TODO " + e.Message) + ")");
        }
    }

    void StmtInner(IOperation op, List<string> lines, int ind)
    {
        var p = Ind(ind);
        switch (op)
        {
            case IBlockOperation b:
                {
                    var n0 = lines.Count;
                    Block(b.Operations, lines, ind);
                    return;
                }
            case IVariableDeclarationGroupOperation g:
                foreach (var d in g.Declarations)
                    foreach (var v in d.Declarators) LocalDecl(v, d.Initializer, lines, ind);
                return;
            case IExpressionStatementOperation es:
                {
                    var s = StmtExpr(es.Operation);
                    FlushTo(lines, ind);
                    if (s.Length > 0) foreach (var l in s.Split('\n')) lines.Add(p + l);
                    return;
                }
            case IReturnOperation r: Return(r, lines, ind); return;
            case IConditionalOperation c: If(c, lines, ind, false); return;
            case IWhileLoopOperation w: While(w, lines, ind); return;
            case IForLoopOperation f: For(f, lines, ind); return;
            case IForEachLoopOperation fe: ForEach(fe, lines, ind); return;
            case IBranchOperation br: Branch(br, lines, ind); return;
            case ILabeledOperation l:
                Note("label outside a dispatch method");
                if (l.Operation != null) Stmt(l.Operation, lines, ind);
                return;
            case ISwitchOperation sw: Switch(sw, lines, ind); return;
            case ITryOperation t: Try(t, lines, ind); return;
            case IUsingOperation u: Using(u, lines, ind); return;
            case IThrowOperation th:
                {
                    var v = th.Exception == null ? null : V(th.Exception);
                    FlushTo(lines, ind);
                    lines.Add(p + (v == null ? "raise" : "raise " + v));
                    return;
                }
            case IEmptyOperation: return;
            case IUsingDeclarationOperation ud: UsingRest(ud, new List<IOperation>(), lines, ind); return;
            case ILocalFunctionOperation: Todo("local function"); return;
            case ILockOperation lk:
                {
                    var o = V(lk.LockedValue);
                    FlushTo(lines, ind);
                    Sub(lk.Body, lines, ind);
                    return;
                }
        }
        Todo("statement " + op.Kind);
    }

    void LocalDecl(IVariableDeclaratorOperation v, IVariableInitializerOperation? outerInit, List<string> lines, int ind)
    {
        var p = Ind(ind);
        var loc = v.Symbol;
        var init = v.Initializer ?? outerInit;
        if (loc.RefKind != RefKind.None)
        {
            if (init != null && RefLocalByValue(loc, init.Value))
            {
                var val = V(init.Value);
                FlushTo(lines, ind);
                var n0 = Declare(loc, loc.Name);
                refByValue.Add(loc);
                lines.Add(p + n0 + " = " + val);
                return;
            }
            var a = init == null ? "None" : RefOf(init.Value);
            FlushTo(lines, ind);
            var n = Declare(loc, loc.Name);
            lines.Add(p + n + " = " + a);
            return;
        }
        string value = init == null ? DefaultOf(loc.Type) : ValueOf(init.Value, loc.Type);
        FlushTo(lines, ind);
        var name = names.TryGetValue(loc, out var existing) ? existing : Declare(loc, loc.Name);
        if (Boxed(loc)) { lines.Add(p + name + " = " + Use("Cell", "a cell (.v) for a local whose address is taken") + "(" + value + ")"); return; }
        lines.Add(p + name + " = " + value);
    }

    /// <summary>A ref local to an element of class type that is never assigned through and never passed on by
    /// reference: it is bound to the element itself (the object), not to a reference to the slot.</summary>
    static bool RefLocalByValue(ILocalSymbol loc, IOperation init)
    {
        if (loc.Type.IsValueType || loc.Type is ITypeParameterSymbol) return false;
        if (!(init is IArrayElementReferenceOperation || init is IPropertyReferenceOperation pr && pr.Property.IsIndexer)) return false;
        var scope = loc.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax().Ancestors().OfType<BlockSyntax>().FirstOrDefault();
        if (scope == null) return false;
        var model = M(scope);
        foreach (var id in scope.DescendantNodes().OfType<IdentifierNameSyntax>())
        {
            if (id.Identifier.Text != loc.Name) continue;
            if (!SymbolEqualityComparer.Default.Equals(model.GetSymbolInfo(id).Symbol, loc)) continue;
            var parent = id.Parent;
            if (parent is AssignmentExpressionSyntax asg && asg.Left == id) return false;
            if (parent is PrefixUnaryExpressionSyntax || parent is PostfixUnaryExpressionSyntax) return false;
            if (parent is ArgumentSyntax arg && !arg.RefKindKeyword.IsKind(SyntaxKind.None)) return false;
            if (parent is RefExpressionSyntax) return false;
        }
        return true;
    }

    void Return(IReturnOperation r, List<string> lines, int ind)
    {
        string? v = null;
        if (r.ReturnedValue != null)
            v = retRef ? RefOf(r.ReturnedValue) : ValueOf(r.ReturnedValue, retType);
        FlushTo(lines, ind);
        lines.Add(Ind(ind) + "return" + (v != null ? " " + v : ""));
    }

    void If(IConditionalOperation c, List<string> lines, int ind, bool elif)
    {
        var cond = Cond(c.Condition);
        FlushTo(lines, ind);
        IfRest(c, cond, lines, ind, elif);
    }

    void IfRest(IConditionalOperation c, string cond, List<string> lines, int ind, bool elif)
    {
        var p = Ind(ind);
        lines.Add(p + (elif ? "elif " : "if ") + Strip(cond) + ":");
        Sub(c.WhenTrue, lines, ind + 1);
        if (c.WhenFalse == null) return;
        // else if: elif, unless its condition needs statements first
        var wf = c.WhenFalse;
        while (wf is IBlockOperation bb && bb.Operations.Length == 1 && bb.Locals.IsEmpty && bb.Operations[0] is IConditionalOperation) wf = bb.Operations[0];
        if (wf is IConditionalOperation ec && !ec.IsRef && ec.Type == null)
        {
            var saved = Pre; Pre = new List<string>();
            var c2 = Cond(ec.Condition);
            var pre2 = Pre; Pre = saved;
            if (pre2.Count == 0) { IfRest(ec, c2, lines, ind, true); return; }
            lines.Add(p + "else:");
            foreach (var l in pre2) lines.Add(Ind(ind + 1) + l);
            IfRest(ec, c2, lines, ind + 1, false);
            return;
        }
        lines.Add(p + "else:");
        Sub(c.WhenFalse, lines, ind + 1);
    }

    /// <summary>A condition: a bool expression.</summary>
    string Cond(IOperation c) => V(c);

    /// <summary>s without its outer parentheses, for a place any expression may stand (a statement, the right side
    /// of an assignment, an argument, an index, a condition); never an assignment expression, which needs them.</summary>
    static string Strip(string s) => s.Length > 1 && s[0] == '(' && s[^1] == ')' && Balanced(s.Substring(1, s.Length - 2)) && !TopLevelWalrus(s.Substring(1, s.Length - 2)) ? s.Substring(1, s.Length - 2) : s;

    static bool TopLevelWalrus(string s)
    {
        int d = 0; bool inStr = false; char q = '\0';
        for (int i = 0; i < s.Length; i++)
        {
            var ch = s[i];
            if (inStr) { if (ch == '\\') { i++; continue; } if (ch == q) inStr = false; continue; }
            if (ch == '\'' || ch == '"') { inStr = true; q = ch; continue; }
            if (ch == '(' || ch == '[') d++; else if (ch == ')' || ch == ']') d--;
            else if (d == 0 && ch == ':' && i + 1 < s.Length && s[i + 1] == '=') return true;
        }
        return false;
    }

    static bool Balanced(string s)
    {
        int d = 0; bool inStr = false; char q = '\0';
        for (int i = 0; i < s.Length; i++)
        {
            var ch = s[i];
            if (inStr) { if (ch == '\\') { i++; continue; } if (ch == q) inStr = false; continue; }
            if (ch == '\'' || ch == '"') { inStr = true; q = ch; continue; }
            if (ch == '(' || ch == '[') d++; else if (ch == ')' || ch == ']') { d--; if (d < 0) return false; }
        }
        return d == 0;
    }

    static bool IsTrue(IOperation? c) => c == null || c.ConstantValue.HasValue && c.ConstantValue.Value is true;

    void While(IWhileLoopOperation w, List<string> lines, int ind)
    {
        var p = Ind(ind);
        var lc = new Ctx { IsLoop = true, Exit = w.ExitLabel, Cont = w.ContinueLabel };
        if (w.ConditionIsTop)
        {
            var cond = w.Condition == null ? "True" : Cond(w.Condition);
            if (Pre.Count > 0)
            {
                var pre = new List<string>(Pre); Pre.Clear();
                lines.Add(p + "while True:");
                foreach (var s in pre) lines.Add(Ind(ind + 1) + s);
                lines.Add(Ind(ind + 1) + "if not " + Paren(cond) + ":");
                lines.Add(Ind(ind + 2) + "break");
            }
            else lines.Add(p + "while " + Strip(cond) + ":");
            Push(lc);
            Sub(w.Body, lines, ind + 1);
            Pop();
            return;
        }
        // do { body } while (cond): while True: body; if not cond: break
        lines.Add(p + "while True:");
        lc.DoCond = w.Condition;
        Push(lc);
        Sub(w.Body, lines, ind + 1);
        Pop();
        var c = Cond(w.Condition!);
        FlushTo(lines, ind + 1);
        lines.Add(Ind(ind + 1) + "if not " + Paren(c) + ":");
        lines.Add(Ind(ind + 2) + "break");
    }

    void Push(Ctx c) { ctx.Add(c); if (dispatchDepth >= 0 && (c.IsLoop || c.Wrapper)) dispatchDepth++; }
    void Pop() { var c = ctx[^1]; ctx.RemoveAt(ctx.Count - 1); if (dispatchDepth >= 0 && (c.IsLoop || c.Wrapper)) dispatchDepth--; }

    void For(IForLoopOperation f, List<string> lines, int ind)
    {
        var p = Ind(ind);
        if (RangeFor(f, lines, ind)) return;
        foreach (var b in f.Before) Stmt(b, lines, ind);
        var cond = f.Condition == null ? "True" : Cond(f.Condition);
        var condPre = new List<string>(Pre); Pre.Clear();
        var step = new List<string>();
        foreach (var a in f.AtLoopBottom)
        {
            var s = a is IExpressionStatementOperation es ? (es.Operation is IIncrementOrDecrementOperation sinc ? IncDec(sinc, true, true).S : StmtExpr(es.Operation)) : Todo("for-loop step " + a.Kind);
            foreach (var x in Pre) step.Add(x);
            Pre.Clear();
            if (s.Length > 0) foreach (var l in s.Split('\n')) step.Add(l);
        }
        if (condPre.Count > 0)
        {
            lines.Add(p + "while True:");
            foreach (var s in condPre) lines.Add(Ind(ind + 1) + s);
            lines.Add(Ind(ind + 1) + "if not " + Paren(cond) + ":");
            lines.Add(Ind(ind + 2) + "break");
        }
        else lines.Add(p + "while " + Strip(cond) + ":");
        var lc = new Ctx { IsLoop = true, Exit = f.ExitLabel, Cont = f.ContinueLabel, Step = step };
        Push(lc);
        var n0 = lines.Count;
        Sub(f.Body, lines, ind + 1);
        Pop();
        // the step, unless the body cannot reach its end (it ends with continue, break, return or throw)
        if (!EndsInJump(f.Body)) foreach (var s in step) lines.Add(Ind(ind + 1) + s);
        else if (lines.Count == n0) lines.Add(Ind(ind + 1) + "pass");
    }

    static bool EndsInJump(IOperation op)
    {
        while (op is IBlockOperation b) { if (b.Operations.Length == 0) return false; op = b.Operations[^1]; }
        return op is IBranchOperation || op is IReturnOperation || op is IThrowOperation;
    }

    /// <summary>for (i = a; i &lt; n; i++) as for i in range(a, n): when i is the for statement's own variable (or
    /// one used nowhere outside it), the body and the step do not assign it and n is loop-invariant (a constant, or
    /// a local or parameter the loop does not assign, or the length of such an array).</summary>
    bool RangeFor(IForLoopOperation f, List<string> lines, int ind)
    {
        if (f.Condition is not IBinaryOperation cmp) return false;
        if (cmp.OperatorMethod != null) return false;
        var k = cmp.OperatorKind;
        if (k is not (BinaryOperatorKind.LessThan or BinaryOperatorKind.LessThanOrEqual or BinaryOperatorKind.GreaterThan or BinaryOperatorKind.GreaterThanOrEqual)) return false;
        var lhs = StripConv(cmp.LeftOperand);
        if (lhs is not ILocalReferenceOperation lr) return false;
        var iv = lr.Local;
        if (!IsIntegral(iv.Type) || iv.RefKind != RefKind.None || Boxed(iv) || refByValue.Contains(iv)) return false;
        if (!SymbolEqualityComparer.Default.Equals(Under(cmp.LeftOperand.Type!), Under(iv.Type)) && !(cmp.LeftOperand is IConversionOperation)) return false;
        // the comparison must be made in a type at least as wide, with the same signedness, as i
        var ct = Under(cmp.LeftOperand.Type!);
        if (IsUnsigned(ct) != IsUnsigned(iv.Type) || Width(ct) < Width(iv.Type)) return false;
        bool up = k is BinaryOperatorKind.LessThan or BinaryOperatorKind.LessThanOrEqual;
        if (!up && IsUnsigned(iv.Type)) return false;
        if (f.AtLoopBottom.Length != 1 || f.AtLoopBottom[0] is not IExpressionStatementOperation stepSt) return false;
        var step = stepSt.Operation;
        bool stepOk = step switch
        {
            IIncrementOrDecrementOperation inc => StripConv(inc.Target) is ILocalReferenceOperation t1 && SymbolEqualityComparer.Default.Equals(t1.Local, iv) && (inc.Kind == OperationKind.Increment) == up && inc.OperatorMethod == null,
            ICompoundAssignmentOperation ca => StripConv(ca.Target) is ILocalReferenceOperation t2 && SymbolEqualityComparer.Default.Equals(t2.Local, iv) && ca.OperatorMethod == null &&
                (ca.OperatorKind == (up ? BinaryOperatorKind.Add : BinaryOperatorKind.Subtract)) && ca.Value.ConstantValue.HasValue && Convert.ToInt64(ca.Value.ConstantValue.Value) == 1,
            _ => false
        };
        if (!stepOk) return false;
        // the variable: declared by the for statement, or used nowhere outside it but at its declaration
        bool ownVar = f.Locals.Contains(iv);
        IVariableDeclaratorOperation? ownDecl = null;
        if (ownVar)
        {
            foreach (var b in f.Before)
                if (b is IVariableDeclarationGroupOperation g)
                    foreach (var d in g.Declarations) foreach (var v in d.Declarators) if (SymbolEqualityComparer.Default.Equals(v.Symbol, iv)) ownDecl = v;
            if (ownDecl == null) return false;
        }
        else if (!UsedOnlyInside(iv, f)) return false;
        // nothing in the loop assigns i
        foreach (var d in f.Body.DescendantsAndSelf().Concat(cmp.RightOperand.DescendantsAndSelf()))
        {
            if (d is IAssignmentOperation a && StripConv(a.Target) is ILocalReferenceOperation al && SymbolEqualityComparer.Default.Equals(al.Local, iv)) return false;
            if (d is IIncrementOrDecrementOperation ii && StripConv(ii.Target) is ILocalReferenceOperation il && SymbolEqualityComparer.Default.Equals(il.Local, iv)) return false;
            if (d is IAnonymousFunctionOperation af && af.Body.Descendants().Any(x => x is ILocalReferenceOperation xl && SymbolEqualityComparer.Default.Equals(xl.Local, iv))) return false;
        }
        // the bound
        var bound = cmp.RightOperand;
        if (!Invariant(bound, f)) return false;
        // other declarations of the for statement, in order
        foreach (var b in f.Before)
        {
            if (b is IVariableDeclarationGroupOperation g)
            {
                foreach (var d in g.Declarations)
                    foreach (var v in d.Declarators)
                        if (!SymbolEqualityComparer.Default.Equals(v.Symbol, iv)) LocalDecl(v, d.Initializer, lines, ind);
                        else
                        {
                            // i's initial value, unless it is the first thing: then it is range's start
                        }
            }
            else Stmt(b, lines, ind);
        }
        string start;
        if (ownVar)
        {
            var init = ownDecl!.Initializer ?? (ownDecl.Parent as IVariableDeclarationOperation)?.Initializer;
            start = init == null ? "0" : ValueOf(init.Value, iv.Type);
            Declare(iv, iv.Name);
        }
        else
        {
            // used nowhere outside the loop but at its declaration: the declaration's constant is the start
            start = names[iv];
            var decl = iv.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() as VariableDeclaratorSyntax;
            if (decl?.Initializer != null && M(decl).GetOperation(decl.Initializer) is IVariableInitializerOperation vio && vio.Value.ConstantValue.HasValue)
                start = Const(vio.Value.ConstantValue.Value, iv.Type).S;
        }
        var bnd = ValueOf(bound, cmp.RightOperand.Type);
        FlushTo(lines, ind);
        string rng;
        if (up) rng = "range(" + Strip(start) + ", " + (k == BinaryOperatorKind.LessThan ? Strip(bnd) : Paren(bnd) + " + 1") + ")";
        else rng = "range(" + Strip(start) + ", " + (k == BinaryOperatorKind.GreaterThan ? Strip(bnd) : Paren(bnd) + " - 1") + ", -1)";
        lines.Add(Ind(ind) + "for " + names[iv] + " in " + rng + ":");
        bool nn = up && (start == "0" || long.TryParse(start, out var sv) && sv >= 0 || IsUnsigned(iv.Type));
        if (nn) nonNeg.Add(iv);
        var lc = new Ctx { IsLoop = true, Exit = f.ExitLabel, Cont = f.ContinueLabel };
        Push(lc);
        Sub(f.Body, lines, ind + 1);
        Pop();
        if (nn) nonNeg.Remove(iv);
        Stat("range for");
        return true;
    }

    static IOperation StripConv(IOperation op)
    {
        while (op is IConversionOperation cv && cv.OperatorMethod == null && (cv.Conversion.IsIdentity || cv.Conversion.IsNumeric && cv.Conversion.IsImplicit)) op = cv.Operand;
        return op;
    }

    /// <summary>Every reference to local l (but its declaration) lies inside the for statement f, and the
    /// declaration is not inside a loop that also contains f.</summary>
    bool UsedOnlyInside(ILocalSymbol l, IForLoopOperation f)
    {
        var declSyn = l.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax();
        if (declSyn == null) return false;
        var scope = declSyn.Ancestors().FirstOrDefault(a => a is BlockSyntax || a is SwitchSectionSyntax);
        if (scope == null) return false;
        var model = M(scope);
        var fs = f.Syntax.Span;
        foreach (var id in scope.DescendantNodes().OfType<IdentifierNameSyntax>())
        {
            if (id.Identifier.Text != l.Name) continue;
            if (fs.Contains(id.Span)) continue;
            if (SymbolEqualityComparer.Default.Equals(model.GetSymbolInfo(id).Symbol, l)) return false;
        }
        // a loop around f that holds the declaration's scope would run f again with the old i
        return true;
    }

    /// <summary>The bound of a range for: the loop cannot change it.</summary>
    bool Invariant(IOperation b, IForLoopOperation f)
    {
        b = StripConv(b);
        if (b.ConstantValue.HasValue) return true;
        ISymbol? s = b switch { ILocalReferenceOperation l => l.Local, IParameterReferenceOperation p => p.Parameter, _ => null };
        if (s == null)
        {
            if (b is IPropertyReferenceOperation pr && IsSystem(pr.Property) && pr.Property.Name == "Length" && pr.Instance != null && Invariant(pr.Instance, f)) return true;
            if (b is IConversionOperation cv && cv.OperatorMethod == null && IsIntegral(cv.Type) && IsIntegral(cv.Operand.Type)) return Invariant(cv.Operand, f);
            // an engine field the loop cannot write, of an object the loop cannot change
            if (b is IFieldReferenceOperation fr && IsEngine(fr.Field.ContainingType) && !fr.Field.IsConst)
            {
                if (!fr.Field.IsStatic && !(fr.Instance is IInstanceReferenceOperation) && !Invariant(fr.Instance!, f)) return false;
                if (MayWrite(f.Body, fr.Field) || f.AtLoopBottom.Any(x => MayWrite(x, fr.Field)) || MayWrite(f.Condition!, fr.Field)) return false;
                Stat("range for with a field bound");
                return true;
            }
            if (b is IInstanceReferenceOperation) return true;
            return false;
        }
        if (Boxed(s) || IsRefSym(s) || s is ILocalSymbol ls && refByValue.Contains(ls)) return false;
        foreach (var d in f.Body.DescendantsAndSelf().Concat(f.AtLoopBottom.SelectMany(x => x.DescendantsAndSelf())).Concat(f.Condition!.DescendantsAndSelf()))
        {
            if (d is IAssignmentOperation a && Sym(StripConv(a.Target)) is ISymbol t && SymbolEqualityComparer.Default.Equals(t, s)) return false;
            if (d is IIncrementOrDecrementOperation ii && Sym(StripConv(ii.Target)) is ISymbol t2 && SymbolEqualityComparer.Default.Equals(t2, s)) return false;
            if (d is IAnonymousFunctionOperation af && af.Body.Descendants().Any(x => (x is IAssignmentOperation xa && Sym(StripConv(xa.Target)) is ISymbol t3 && SymbolEqualityComparer.Default.Equals(t3, s)))) return false;
        }
        return true;
    }

    static ISymbol? Sym(IOperation op) => op switch { ILocalReferenceOperation l => l.Local, IParameterReferenceOperation p => p.Parameter, _ => null };

    void ForEach(IForEachLoopOperation fe, List<string> lines, int ind)
    {
        var p = Ind(ind);
        var coll = fe.Collection is IConversionOperation cv && cv.IsImplicit ? cv.Operand : fe.Collection;
        var ct = coll.Type!;
        var src = V(coll);
        FlushTo(lines, ind);
        if (fe.LoopControlVariable is not IVariableDeclaratorOperation vd) { Todo("foreach variable"); return; }
        var loc = vd.Symbol;
        var name = Declare(loc, loc.Name);
        string iter;
        if (ct is IArrayTypeSymbol) iter = src;
        else if (ct is INamedTypeSymbol nt && IsRuntime(nt)) { iter = src; UseM("__iter__", "foreach over " + nt.OriginalDefinition.ToDisplayString() + ": the runtime type is iterable (yields what C#'s GetEnumerator does)"); }
        else if (ct is INamedTypeSymbol ie && IsSystem(ie) && ie.Name == "IEnumerable") iter = src;
        else { Todo("foreach over " + ct.ToDisplayString()); return; }
        var lc = new Ctx { IsLoop = true, Exit = fe.ExitLabel, Cont = fe.ContinueLabel };
        if (Boxed(loc))
        {
            var raw = Fresh("e");
            lines.Add(p + "for " + raw + " in " + iter + ":");
            lines.Add(Ind(ind + 1) + name + " = " + Use("Cell", "a cell (.v) for a local whose address is taken") + "(" + raw + ")");
        }
        else lines.Add(p + "for " + name + " in " + iter + ":");
        Push(lc);
        Sub(fe.Body, lines, ind + 1);
        Pop();
    }

    void Branch(IBranchOperation br, List<string> lines, int ind)
    {
        var p = Ind(ind);
        switch (br.BranchKind)
        {
            case BranchKind.Break:
                {
                    // the innermost loop or switch it leaves
                    for (int i = ctx.Count - 1; i >= 0; i--)
                    {
                        var c = ctx[i];
                        if (SymbolEqualityComparer.Default.Equals(c.Exit, br.Target))
                        {
                            if (c.IsLoop || c.Wrapper) lines.Add(p + "break");
                            // a switch without a wrapper: a break in tail position, nothing to do
                            return;
                        }
                        if (c.Wrapper) Todo("break across a switch wrapper");
                    }
                    lines.Add(p + "break");
                    return;
                }
            case BranchKind.Continue:
                {
                    for (int i = ctx.Count - 1; i >= 0; i--)
                    {
                        var c = ctx[i];
                        if (c.Wrapper)
                        {
                            c.ContFlag ??= Fresh("cont");
                            lines.Add(p + c.ContFlag + " = True");
                            lines.Add(p + "break");
                            return;
                        }
                        if (c.IsLoop && SymbolEqualityComparer.Default.Equals(c.Cont, br.Target))
                        {
                            ContinueOf(c, lines, ind);
                            return;
                        }
                    }
                    lines.Add(p + "continue");
                    return;
                }
            case BranchKind.GoTo:
                if (gotoLabels != null && gotoLabels.TryGetValue(br.Target, out var k))
                {
                    if (dispatchDepth > 0)
                    {
                        lines.Add(p + "raise " + Use("GotoSignal", "an exception class: raise GotoSignal(k) leaves loops for a goto; .k is the target") + "(" + k + ")");
                        gotoRaised = true;
                        return;
                    }
                    lines.Add(p + stVar + " = " + k);
                    lines.Add(p + "continue");
                    return;
                }
                Todo("goto outside a dispatch method");
                return;
        }
        Todo("branch " + br.BranchKind);
    }

    bool gotoRaised;

    /// <summary>What C#'s continue does for loop c: the step of a for loop run as while, the condition of a do-while.</summary>
    void ContinueOf(Ctx c, List<string> lines, int ind)
    {
        var p = Ind(ind);
        if (c.Step != null) foreach (var s in c.Step) lines.Add(p + s);
        if (c.DoCond != null)
        {
            var cond = Cond(c.DoCond);
            FlushTo(lines, ind);
            lines.Add(p + "if " + Strip(cond) + ":");
            lines.Add(p + "    continue");
            lines.Add(p + "break");
            return;
        }
        lines.Add(p + "continue");
    }

    // ---- switch
    void Switch(ISwitchOperation sw, List<string> lines, int ind)
    {
        var p = Ind(ind);
        var vt = sw.Value.Type!;
        var v = V(sw.Value);
        string t = v;
        if (!SimpleName(v)) { t = Fresh("sw"); FlushTo(lines, ind); lines.Add(p + t + " = " + v); }
        else FlushTo(lines, ind);
        bool wrapper = sw.Cases.Any(c => NonTailBreak(c.Body, sw.ExitLabel));
        var sc = new Ctx { Exit = sw.ExitLabel, Wrapper = wrapper };
        int bi = ind;
        int wrapAt = lines.Count;
        if (wrapper) { lines.Add(p + "while True:"); bi = ind + 1; Stat("switch wrapper"); }
        Push(sc);
        bool first = true;
        IOperation[]? defBody = null;
        foreach (var c in sw.Cases)
        {
            var conds = new List<string>();
            foreach (var cl in c.Clauses)
            {
                if (cl is IDefaultCaseClauseOperation) continue;
                if (cl is ISingleValueCaseClauseOperation sv)
                {
                    var cv = ValueOf(sv.Value, vt);
                    conds.Add(vt.SpecialType == SpecialType.System_String || IsStr(vt) ? t + " == " + cv : t + " == " + cv);
                }
                else Todo("case clause " + cl.CaseKind);
            }
            if (c.Clauses.Any(cl => cl is IDefaultCaseClauseOperation)) { defBody = c.Body.ToArray(); if (conds.Count == 0) continue; }
            lines.Add(Ind(bi) + (first ? "if " : "elif ") + string.Join(" or ", conds) + ":");
            first = false;
            SectionBody(c.Body, lines, bi + 1, sw.ExitLabel, wrapper);
        }
        if (defBody != null)
        {
            if (first) { SectionBody(defBody, lines, bi, sw.ExitLabel, wrapper, true); }
            else
            {
                lines.Add(Ind(bi) + "else:");
                SectionBody(defBody, lines, bi + 1, sw.ExitLabel, wrapper);
            }
        }
        Pop();
        if (wrapper)
        {
            lines.Add(Ind(bi) + "break");
            if (sc.ContFlag != null)
            {
                lines.Insert(wrapAt, p + sc.ContFlag + " = False");
                lines.Add(p + "if " + sc.ContFlag + ":");
                // the continue the flag carried, seen from outside the wrapper
                for (int i = ctx.Count - 1; i >= 0; i--)
                {
                    var c = ctx[i];
                    if (c.Wrapper) { c.ContFlag ??= Fresh("cont"); lines.Add(p + "    " + c.ContFlag + " = True"); lines.Add(p + "    break"); goto done; }
                    if (c.IsLoop) { ContinueOf(c, lines, ind + 1); goto done; }
                }
                lines.Add(p + "    continue");
            done:;
            }
        }
    }

    static bool IsStr(ITypeSymbol t) => t.Name == "Str" && IsRuntime(t);

    void SectionBody(IEnumerable<IOperation> body, List<string> lines, int ind, ILabelSymbol exit, bool wrapper, bool topLevel = false)
    {
        var list = body.ToList();
        // a final break leaves the switch: nothing to write unless the switch is a wrapper
        var n0 = lines.Count;
        Block(list, lines, ind);
        if (lines.Count == n0 && !topLevel) lines.Add(Ind(ind) + "pass");
    }

    /// <summary>Whether a break leaving the switch occurs anywhere but in tail position of the section.</summary>
    static bool NonTailBreak(IEnumerable<IOperation> body, ILabelSymbol exit)
    {
        var list = body.ToList();
        for (int i = 0; i < list.Count; i++)
        {
            bool last = i == list.Count - 1;
            if (BreaksIn(list[i], exit, last)) return true;
        }
        return false;
    }

    /// <summary>Whether op contains a break to exit that is not in tail position (tail: op is the section's last
    /// statement and the break is its last statement, through blocks and if branches).</summary>
    static bool BreaksIn(IOperation op, ILabelSymbol exit, bool tail)
    {
        switch (op)
        {
            case IBranchOperation b when b.BranchKind == BranchKind.Break && SymbolEqualityComparer.Default.Equals(b.Target, exit):
                return !tail;
            case IBlockOperation blk:
                {
                    for (int i = 0; i < blk.Operations.Length; i++)
                        if (BreaksIn(blk.Operations[i], exit, tail && i == blk.Operations.Length - 1)) return true;
                    return false;
                }
            case IConditionalOperation c when c.Type == null:
                return BreaksIn(c.WhenTrue, exit, tail) || c.WhenFalse != null && BreaksIn(c.WhenFalse, exit, tail);
            case ILoopOperation:
                return false;           // a break inside a loop leaves the loop
            case ISwitchOperation:
                return false;
            default:
                return op.Descendants().Any(d => d is IBranchOperation b && b.BranchKind == BranchKind.Break && SymbolEqualityComparer.Default.Equals(b.Target, exit));
        }
    }

    // ---- try, using
    void Try(ITryOperation t, List<string> lines, int ind)
    {
        var p = Ind(ind);
        lines.Add(p + "try:");
        Sub(t.Body, lines, ind + 1);
        foreach (var cc in t.Catches)
        {
            var et = cc.ExceptionType;
            string pt;
            if (et.SpecialType == SpecialType.System_Object || et is INamedTypeSymbol en && IsSystem(en) && en.Name == "Exception") pt = "Exception";
            else if (et is INamedTypeSymbol en2 && IsSystem(en2)) pt = Use("SysException", "a .NET exception (SysException(kind, message)); except SysException for a catch of " + en2.Name);
            else if (et is INamedTypeSymbol rt && IsRuntime(rt)) pt = Use(RuntimeTypeName(rt), "type " + rt.OriginalDefinition.ToDisplayString() + " (an exception class)");
            else pt = TypeIdent((INamedTypeSymbol)et);
            if (cc.Filter != null) Todo("exception filter");
            if (cc.ExceptionDeclarationOrExpression is IVariableDeclaratorOperation vd)
            {
                var ev = Declare(vd.Symbol, vd.Symbol.Name);
                lines.Add(p + "except " + pt + " as " + ev + ":");
                if (Boxed(vd.Symbol)) lines.Add(Ind(ind + 1) + ev + " = " + Use("Cell", "a cell (.v) for a local whose address is taken") + "(" + ev + ")");
            }
            else lines.Add(p + "except " + pt + ":");
            Sub(cc.Handler, lines, ind + 1);
        }
        if (t.Finally != null)
        {
            lines.Add(p + "finally:");
            Sub(t.Finally, lines, ind + 1);
        }
    }

    void UsingRest(IUsingDeclarationOperation ud, List<IOperation> rest, List<string> lines, int ind)
    {
        var p = Ind(ind);
        var decls = ud.DeclarationGroup.Declarations.SelectMany(d => d.Declarators).ToList();
        foreach (var d in ud.DeclarationGroup.Declarations)
            foreach (var v in d.Declarators) LocalDecl(v, d.Initializer, lines, ind);
        lines.Add(p + "try:");
        var n0 = lines.Count;
        Block(rest, lines, ind + 1);
        if (lines.Count == n0) lines.Add(Ind(ind + 1) + "pass");
        lines.Add(p + "finally:");
        foreach (var v in decls.AsEnumerable().Reverse())
        {
            var n = Read(v.Symbol).S;
            lines.Add(Ind(ind + 1) + "if " + n + " is not None:");
            lines.Add(Ind(ind + 2) + n + ".Dispose()");
        }
        Use("IDisposable", "interface System.IDisposable (Dispose())");
    }

    void Using(IUsingOperation u, List<string> lines, int ind)
    {
        var p = Ind(ind);
        var names0 = new List<string>();
        if (u.Resources is IVariableDeclarationGroupOperation g)
        {
            foreach (var d in g.Declarations)
                foreach (var v in d.Declarators) { LocalDecl(v, d.Initializer, lines, ind); names0.Add(Read(v.Symbol).S); }
        }
        else
        {
            var r = Fresh("res");
            var val = V(u.Resources);
            FlushTo(lines, ind);
            lines.Add(p + r + " = " + val);
            names0.Add(r);
        }
        lines.Add(p + "try:");
        Sub(u.Body, lines, ind + 1);
        lines.Add(p + "finally:");
        foreach (var n in names0.AsEnumerable().Reverse())
        {
            lines.Add(Ind(ind + 1) + "if " + n + " is not None:");
            lines.Add(Ind(ind + 2) + n + ".Dispose()");
        }
    }


    // -------------------------------------------------------------------------------------------- expression statements
    /// <summary>An expression used as a statement (possibly several lines).</summary>
    string StmtExpr(IOperation op)
    {
        switch (op)
        {
            case ISimpleAssignmentOperation a:
                {
                    if (a.IsRef) return RefRebind(a.Target, RefOf(a.Value));
                    // x = y = v: the inner assignment first
                    var v = ValueOf(a.Value, a.Target.Type);
                    return Assign(a.Target, v);
                }
            case ICompoundAssignmentOperation ca: return Compound(ca, true).S;
            case IIncrementOrDecrementOperation inc: return IncDec(inc, true).S;
            case IDiscardOperation: return "";
            case IConditionalAccessOperation ca:
                {
                    // a?.M();  ->  if a is not None: a.M()
                    var recv = V(ca.Operation);
                    string r = recv;
                    if (!Simple(recv)) { r = Fresh("ca"); Pre.Add(r + " = " + recv); }
                    condAccess.Push(r);
                    var saved = Pre; Pre = new List<string>();
                    var inner = StmtExpr(ca.WhenNotNull);
                    var ip = Pre; Pre = saved;
                    condAccess.Pop();
                    var sb = new StringBuilder("if " + r + " is not None:");
                    foreach (var l in ip) sb.Append("\n    ").Append(l);
                    foreach (var l in inner.Split('\n')) if (l.Length > 0) sb.Append("\n    ").Append(l);
                    if (ip.Count == 0 && inner.Length == 0) sb.Append("\n    pass");
                    return sb.ToString();
                }
            case ILiteralOperation:
                return "";
            default:
                {
                    var s = X(op).S;
                    if (Simple(s)) return "";       // a value read and dropped
                    return Strip(s);
                }
        }
    }

    string RefRebind(IOperation target, string r)
    {
        if (target is ILocalReferenceOperation l && l.Local.RefKind != RefKind.None) return Name(l.Local) + " = " + r;
        return Todo("ref reassignment of " + target.Kind);
    }

    readonly Stack<string> condAccess = new();
    readonly Stack<string> implicitRecv = new();

    // -------------------------------------------------------------------------------------------- places
    /// <summary>A Python target for op, if it has one: a local, a field, a module variable, an array, vector or
    /// string element (the backing list), an element of an array a Ptr was just made over.</summary>
    string? Direct(IOperation op, bool forWrite)
    {
        switch (op)
        {
            case ILocalReferenceOperation l:
                if (l.Local.RefKind != RefKind.None) return refByValue.Contains(l.Local) && !forWrite ? Name(l.Local) : refByValue.Contains(l.Local) ? null : Name(l.Local) + ".v";
                if (Boxed(l.Local)) return Name(l.Local) + ".v";
                if (forWrite) AssignOuter(l.Local);
                return Name(l.Local);
            case IParameterReferenceOperation pr:
                if (pr.Parameter.RefKind is RefKind.Ref or RefKind.Out) return Name(pr.Parameter) + ".v";
                if (Boxed(pr.Parameter)) return Name(pr.Parameter) + ".v";
                if (forWrite) AssignOuter(pr.Parameter);
                return Name(pr.Parameter);
            case IFieldReferenceOperation f:
                {
                    var fs = f.Field;
                    if (IsRuntime(fs)) return fs.IsStatic ? StaticRuntimeField(fs) : Recv(f.Instance!) + "." + RuntimeField(fs);
                    if (fs.IsStatic)
                    {
                        var g = StaticFieldName(fs);
                        if (forWrite) AssignGlobal(g);
                        return g;
                    }
                    return Recv(f.Instance!) + "." + FieldIdent(fs);
                }
            case IArrayElementReferenceOperation ae:
                return Recv(ae.ArrayReference) + "[" + Index(ae.Indices[0]) + "]";
            case IPropertyReferenceOperation p when VecElem(p, out var vs, out var vi):
                return vs + "[" + vi + "]";
            case IPropertyReferenceOperation p2 when PtrOverArray(p2, out var arr, out var ix):
                return arr + "[" + ix + "]";
            case IConversionOperation cv when cv.Conversion.IsIdentity: return Direct(cv.Operand, forWrite);
        }
        return null;
    }

    string Name(ISymbol s) => names.TryGetValue(s, out var n) ? n : Todo("unknown symbol " + s.Name);

    /// <summary>target = v as a statement.</summary>
    string Assign(IOperation target, string v)
    {
        var d = Direct(target, true);
        if (d != null) return d + " = " + Strip(v);
        if (target is IPropertyReferenceOperation cp && IsCPtrIndexer(cp)) return Recv(cp.Instance!) + "." + UseM("set", "method CPtr.this[int] set (set(i, v))") + "(" + Index(cp.Arguments[0].Value) + ", " + Strip(v) + ")";
        if (target is IPropertyReferenceOperation sp && !sp.Property.IsIndexer && sp.Property.SetMethod != null && sp.Property.RefKind == RefKind.None)
            return PropSet(sp, v);
        if (target is IPropertyReferenceOperation rp && rp.Property.RefKind != RefKind.None && IsRuntime(rp.Property))
            return PropRefSet(rp, v);
        return RefOf(target) + ".v = " + Strip(v);
    }

    /// <summary>Whether a target can be read and written again without re-running anything.</summary>
    bool StableTarget(IOperation target)
    {
        switch (target)
        {
            case ILocalReferenceOperation: case IParameterReferenceOperation: return true;
            case IFieldReferenceOperation f: return f.Field.IsStatic || f.Instance == null || StableRecv(f.Instance);
            case IArrayElementReferenceOperation ae: return StableRecv(ae.ArrayReference) && StableIndex(ae.Indices[0]);
            case IPropertyReferenceOperation p when (IsVecIndexer(p) || IsCPtrIndexer(p)) && p.Arguments.Length == 1: return StableRecv(p.Instance!) && StableIndex(p.Arguments[0].Value);
            case IConversionOperation cv when cv.Conversion.IsIdentity: return StableTarget(cv.Operand);
        }
        return false;
    }

    static bool StableRecv(IOperation r)
    {
        r = StripConv(r);
        return r switch
        {
            IInstanceReferenceOperation => true,
            ILocalReferenceOperation => true,
            IParameterReferenceOperation => true,
            IFieldReferenceOperation f => f.Field.IsStatic || f.Instance == null || StableRecv(f.Instance),
            _ => false
        };
    }

    static bool StableIndex(IOperation i)
    {
        i = StripConv(i);
        if (i.ConstantValue.HasValue) return true;
        if (i is IConversionOperation cv && cv.OperatorMethod == null && IsIntegral(cv.Type)) return StableIndex(cv.Operand);
        return i is ILocalReferenceOperation || i is IParameterReferenceOperation || i is IFieldReferenceOperation f && (f.Field.IsStatic || StableRecv(f.Instance!));
    }

    /// <summary>A compound assignment. stmt: as a statement; else its value (the stored value) as an expression.</summary>
    Ex Compound(ICompoundAssignmentOperation ca, bool stmt)
    {
        var tt = ca.Target.Type!;
        if (ca.OperatorMethod != null && !IsSystem(ca.OperatorMethod))
        {
            // a runtime struct operator (p += n on a Ptr): target = op(target, value)
            return AssignWith(ca.Target, stmt, cur => UserBinary(ca.OperatorMethod, cur, ValueOf(ca.Value, ca.OperatorMethod.Parameters[1].Type)), tt);
        }
        if (tt.SpecialType == SpecialType.System_String)
            return AssignWith(ca.Target, stmt, cur => "(" + cur + " + " + StrPart(ca.Value) + ")", tt);
        var opT = ca.InConversion.Exists && !ca.InConversion.IsIdentity && ca.InConversion.IsNumeric ? ca.Value.Type! : tt;
        if (ca.OperatorMethod != null && ca.OperatorMethod.ContainingType.SpecialType == SpecialType.System_String) opT = tt;
        // double += - *: Python's own augmented assignment means the same (and keeps the evaluation order)
        if (stmt && IsDouble(tt) && SymbolEqualityComparer.Default.Equals(opT, tt) && ca.OperatorKind is BinaryOperatorKind.Add or BinaryOperatorKind.Subtract or BinaryOperatorKind.Multiply)
        {
            var d = Direct(ca.Target, true);
            if (d != null)
            {
                var rv = X(ca.Value);
                var r = rv.XInt && ca.OperatorKind != BinaryOperatorKind.Multiply ? rv.S : Clean(rv, ca.Value.Type);
                return d + " " + PyOp(ca.OperatorKind) + "= " + Strip(r);
            }
        }
        // int &= |= ^= with clean values: Python's own
        if (stmt && IsIntegral(tt) && SymbolEqualityComparer.Default.Equals(Under(opT), Under(tt)) && ca.OperatorKind is BinaryOperatorKind.And or BinaryOperatorKind.Or or BinaryOperatorKind.ExclusiveOr)
        {
            var d = Direct(ca.Target, true);
            if (d != null) return d + " " + PyOp(ca.OperatorKind) + "= " + Strip(V(ca.Value));
        }
        if (stmt && tt.SpecialType == SpecialType.System_Boolean && ca.OperatorKind is BinaryOperatorKind.And or BinaryOperatorKind.Or or BinaryOperatorKind.ExclusiveOr)
        {
            var d = Direct(ca.Target, true);
            if (d != null) return d + " " + PyOp(ca.OperatorKind) + "= " + Strip(V(ca.Value));
        }
        return AssignWith(ca.Target, stmt, cur =>
        {
            var l = Conv(new Ex(cur), tt, opT);
            var r = X(ca.Value);
            var res = BinOp(ca.OperatorKind, opT, l, r, ca.Value.Type, null);
            return Clean(Conv(res, opT, tt), tt);
        }, tt);
    }

    /// <summary>target = f(current value), as a statement or (stmt false) an expression whose value is the stored one.</summary>
    Ex AssignWith(IOperation target, bool stmt, Func<string, string> f, ITypeSymbol tt)
    {
        var d = Direct(target, false);
        if (d != null && StableTarget(target))
        {
            var dw = Direct(target, true)!;
            var nv = f(d);
            if (stmt) return dw + " = " + Strip(nv);
            if (IsLocalName(dw)) return "(" + dw + " := " + Strip(nv) + ")";
            var t = Fresh("v");
            Pre.Add(t + " = " + Strip(nv));
            Pre.Add(dw + " = " + t);
            return t;
        }
        if (d != null)
        {
            // spill the receiver and index, then read and write the element
            var spilled = Spill(target);
            if (spilled != null)
            {
                var nv = f(spilled);
                if (stmt) return spilled + " = " + Strip(nv);
                var t = Fresh("v");
                Pre.Add(t + " = " + Strip(nv));
                Pre.Add(spilled + " = " + t);
                return t;
            }
        }
        if (target is IPropertyReferenceOperation cp && IsCPtrIndexer(cp))
        {
            var pr = Recv(cp.Instance!); var ix = Index(cp.Arguments[0].Value);
            if (!SimpleName(pr)) { var t0 = Fresh("p"); Pre.Add(t0 + " = " + pr); pr = t0; }
            if (!SimpleName(ix)) { var t1 = Fresh("i"); Pre.Add(t1 + " = " + ix); ix = t1; }
            UseM("get", "method CPtr.this[int] get (get(i))");
            var nv = f(pr + ".get(" + ix + ")");
            UseM("set", "method CPtr.this[int] set (set(i, v))");
            if (stmt) return pr + ".set(" + ix + ", " + Strip(nv) + ")";
            var t = Fresh("v");
            Pre.Add(t + " = " + Strip(nv));
            Pre.Add(pr + ".set(" + ix + ", " + t + ")");
            return t;
        }
        var r0 = RefOf(target);
        var rv = r0;
        if (!SimpleName(r0)) { rv = Fresh("r"); Pre.Add(rv + " = " + r0); }
        var val = f(rv + ".v");
        if (stmt) return rv + ".v = " + Strip(val);
        var tv = Fresh("v");
        Pre.Add(tv + " = " + Strip(val));
        Pre.Add(rv + ".v = " + tv);
        return tv;
    }

    /// <summary>An element or field target with its receiver and index evaluated once into temporaries.</summary>
    string? Spill(IOperation target)
    {
        switch (target)
        {
            case IFieldReferenceOperation f when !f.Field.IsStatic && f.Instance != null && !IsRuntime(f.Field):
                {
                    var r = V(f.Instance);
                    var t = Fresh("o"); Pre.Add(t + " = " + r);
                    return t + "." + FieldIdent(f.Field);
                }
            case IArrayElementReferenceOperation ae:
                {
                    var a = V(ae.ArrayReference); var i = Index(ae.Indices[0]);
                    var ta = Fresh("a"); Pre.Add(ta + " = " + a);
                    var ti = Fresh("i"); Pre.Add(ti + " = " + i);
                    return ta + "[" + ti + "]";
                }
            case IPropertyReferenceOperation p when VecElem(p, out var vs, out var vi):
                {
                    var ta = Fresh("a"); Pre.Add(ta + " = " + vs);
                    var ti = Fresh("i"); Pre.Add(ti + " = " + vi);
                    return ta + "[" + ti + "]";
                }
            case IPropertyReferenceOperation p2 when PtrOverArray(p2, out var arr, out var ix):
                {
                    var ta = Fresh("a"); Pre.Add(ta + " = " + arr);
                    var ti = Fresh("i"); Pre.Add(ti + " = " + ix);
                    return ta + "[" + ti + "]";
                }
        }
        return null;
    }

    static bool IsLocalName(string s) => s.Length > 0 && (char.IsLetter(s[0]) || s[0] == '_') && s.All(ch => char.IsLetterOrDigit(ch) || ch == '_');

    Ex IncDec(IIncrementOrDecrementOperation inc, bool stmt, bool loopStep = false)
    {
        var up = inc.Kind == OperationKind.Increment;
        var post = inc.IsPostfix;
        var tt = inc.Target.Type!;
        if (inc.OperatorMethod != null)
        {
            var m = up ? "op_Increment" : "op_Decrement";
            var cn = RuntimeTypeName((INamedTypeSymbol)tt);
            var fnm = Use(cn + "_" + m, "operator " + inc.OperatorMethod.ToDisplayString());
            var d0 = Direct(inc.Target, false);
            if (d0 != null && StableTarget(inc.Target))
            {
                var dw = Direct(inc.Target, true)!;
                if (stmt) return dw + " = " + fnm + "(" + d0 + ")";
                if (!post && IsLocalName(dw)) return "(" + dw + " := " + fnm + "(" + d0 + "))";
                var t0 = Fresh("old");
                Pre.Add(t0 + " = " + d0);
                Pre.Add(dw + " = " + fnm + "(" + t0 + ")");
                return post ? t0 : dw;
            }
            var r1 = RefOf(inc.Target);
            var rt = Fresh("r"); Pre.Add(rt + " = " + r1);
            if (stmt) return rt + ".v = " + fnm + "(" + rt + ".v)";
            var t1 = Fresh("old");
            Pre.Add(t1 + " = " + rt + ".v");
            Pre.Add(rt + ".v = " + fnm + "(" + t1 + ")");
            return post ? t1 : rt + ".v";
        }
        // the new value from the old one
        string Next(string cur)
        {
            if (IsDouble(tt)) return "(" + cur + (up ? " + 1.0)" : " - 1.0)");
            if (IsSingle(tt)) return Use("f32", "round to IEEE single precision (C# float)") + "(" + cur + (up ? " + 1.0)" : " - 1.0)");
            var e = "(" + cur + (up ? " + 1)" : " - 1)");
            // a counter's ++ cannot reach 2^31 or 2^63 (PY.md): ++ of an int or long, a for loop's ++ of any 32- or
            // 64-bit counter and a for loop's -- of an int or long are not wrapped; every other -- is
            if (Width(tt) >= 32 && !IsUnsigned(tt) && (up || loopStep)) return e;
            if (Width(tt) >= 32 && loopStep && up) return e;
            return WrapInt(e, tt);
        }
        if (inc.Target is IPropertyReferenceOperation cp && IsCPtrIndexer(cp))
        {
            var pr = Recv(cp.Instance!); var ix = Index(cp.Arguments[0].Value);
            var mn = (post ? "post" : "pre") + (up ? "Inc" : "Dec");
            UseM(mn, "method CPtr element " + (post ? "post" : "pre") + (up ? "increment" : "decrement") + " (" + mn + "(i), returns the old/new sbyte value)");
            return pr + "." + mn + "(" + ix + ")";
        }
        var d = Direct(inc.Target, false);
        if (d != null && StableTarget(inc.Target))
        {
            var dw = Direct(inc.Target, true)!;
            if (stmt)
            {
                if (IsDouble(tt)) return dw + (up ? " += 1.0" : " -= 1.0");
                if (Width(tt) >= 32 && !IsUnsigned(tt) && (up || loopStep)) return dw + (up ? " += 1" : " -= 1");
                if (Width(tt) >= 32 && loopStep && up) return dw + " += 1";
                return dw + " = " + Next(d);
            }
            if (IsLocalName(dw))
            {
                if (!post) return new Ex("(" + dw + " := " + Strip(Next(d)) + ")");
                if (IsIntegral(tt) && Width(tt) >= 32 && !IsUnsigned(tt) && up) return new Ex("((" + dw + " := " + dw + " + 1) - 1)");
                return new Ex("(" + dw + ", " + dw + " := " + Strip(Next(d)) + ")[0]");
            }
            var t0 = Fresh("old");
            Pre.Add(t0 + " = " + d);
            Pre.Add(dw + " = " + Strip(Next(t0)));
            return post ? t0 : new Ex(dw);
        }
        if (d != null)
        {
            var sp = Spill(inc.Target);
            if (sp != null)
            {
                if (stmt) return sp + " = " + Strip(Next(sp));
                var t0 = Fresh("old");
                Pre.Add(t0 + " = " + sp);
                Pre.Add(sp + " = " + Strip(Next(t0)));
                return post ? t0 : new Ex(sp);
            }
        }
        var r = RefOf(inc.Target);
        var rv = r;
        if (!SimpleName(r)) { rv = Fresh("r"); Pre.Add(rv + " = " + r); }
        if (stmt) return rv + ".v = " + Strip(Next(rv + ".v"));
        var t2 = Fresh("old");
        Pre.Add(t2 + " = " + rv + ".v");
        Pre.Add(rv + ".v = " + Strip(Next(t2)));
        return post ? t2 : new Ex(rv + ".v");
    }

    // -------------------------------------------------------------------------------------------- references
    /// <summary>A Ref object (.v) for the location op names (a ref argument, a ref local, a ref return).</summary>
    public string RefOf(IOperation op)
    {
        switch (op)
        {
            case ILocalReferenceOperation l:
                if (l.Local.RefKind != RefKind.None && !refByValue.Contains(l.Local) || Boxed(l.Local)) return Name(l.Local);
                return Todo("reference to a local that is not a cell: " + l.Local.Name);
            case IParameterReferenceOperation p:
                if (p.Parameter.RefKind is RefKind.Ref or RefKind.Out || Boxed(p.Parameter)) return Name(p.Parameter);
                return Todo("reference to a parameter that is not a cell: " + p.Parameter.Name);
            case IFieldReferenceOperation f:
                {
                    var fs = f.Field;
                    if (IsRuntime(fs))
                    {
                        if (fs.IsStatic) return Todo("reference to a runtime static field");
                        var n = "ref_" + fs.Name;
                        UseM(n, "field ref " + fs.OriginalDefinition.ToDisplayString() + " (a Ref to the field)");
                        return Recv(f.Instance!) + "." + n + "()";
                    }
                    var h = FieldRefHelper(fs);
                    return fs.IsStatic ? h + "()" : h + "(" + V(f.Instance!) + ")";
                }
            case IArrayElementReferenceOperation ae:
                return Use("ArrRef", "a Ref (.v) to element i of a Python list: ArrRef(list, i)") + "(" + V(ae.ArrayReference) + ", " + Index(ae.Indices[0]) + ")";
            case IPropertyReferenceOperation pr:
                {
                    if (VecElem(pr, out var vs, out var vi)) return Use("ArrRef", "a Ref (.v) to element i of a Python list: ArrRef(list, i)") + "(" + vs + ", " + vi + ")";
                    if (PtrOverArray(pr, out var arr, out var ix)) return Use("ArrRef", "a Ref (.v) to element i of a Python list: ArrRef(list, i)") + "(" + arr + ", " + ix + ")";
                    if (IsCPtrIndexer(pr)) return Recv(pr.Instance!) + "." + UseM("ref", "method CPtr element reference (ref(i): a Ref to the sbyte at i)") + "(" + Index(pr.Arguments[0].Value) + ")";
                    if (pr.Property.RefKind != RefKind.None) return Prop(pr, true);
                    return Todo("reference to a property that returns no reference: " + pr.Property.Name);
                }
            case IInvocationOperation inv when inv.TargetMethod.RefKind != RefKind.None:
                return Invoke(inv, true).S;
            case IConditionalOperation c when c.IsRef:
                return "(" + RefOf(c.WhenTrue) + " if " + Cond(c.Condition) + " else " + RefOf(c.WhenFalse!) + ")";
            case IDiscardOperation d:
                return Use("Cell", "a cell (.v) for a local whose address is taken") + "(" + DefaultOf(d.Type!) + ")";
            case IConversionOperation cv when cv.Conversion.IsIdentity:
                return RefOf(cv.Operand);
            case IParenthesizedOperation pp: return RefOf(pp.Operand);
        }
        return Todo("reference to " + op.Kind);
    }

    // -------------------------------------------------------------------------------------------- expressions
    /// <summary>The value of op converted (as C# does at this point) to target, clean (in range, a float for double).</summary>
    public string ValueOf(IOperation op, ITypeSymbol? target)
    {
        var e = X(op);
        if (target != null && op.Type != null && !SymbolEqualityComparer.Default.Equals(op.Type, target))
        {
            e = Conv(e, op.Type, target);
            return Clean(e, target);
        }
        return Clean(e, target ?? op.Type);
    }

    /// <summary>The clean value of op in its own type.</summary>
    string V(IOperation op) => Clean(X(op), op.Type);

    /// <summary>A raw integer wrapped to t's range, an xint made a float.</summary>
    static string Clean(Ex e, ITypeSymbol? t)
    {
        if (t == null) return e.S;
        if (e.Raw && IsIntegral(t)) return WrapInt(e.S, t);
        if (e.XInt && IsFloat(t)) return "float(" + Strip(e.S) + ")";
        return e.S;
    }

    public Ex X(IOperation op)
    {
        if (op.ConstantValue.HasValue) return Const(op.ConstantValue.Value, op.Type);
        switch (op)
        {
            case IConversionOperation c: return Conversion(c);
            case ILocalReferenceOperation l: return Read(l.Local);
            case IParameterReferenceOperation pr:
                {
                    var n = Name(pr.Parameter);
                    if (pr.Parameter.RefKind is RefKind.Ref or RefKind.Out) return n + ".v";
                    return Boxed(pr.Parameter) ? n + ".v" : n;
                }
            case IInstanceReferenceOperation ir:
                if (ir.ReferenceKind == InstanceReferenceKind.ImplicitReceiver) return implicitRecv.Count > 0 ? implicitRecv.Peek() : Todo("implicit receiver");
                return "self";
            case IFieldReferenceOperation f: return Field(f);
            case IPropertyReferenceOperation p: return Prop(p, false);
            case IArrayElementReferenceOperation ae: return Recv(ae.ArrayReference) + "[" + Index(ae.Indices[0]) + "]";
            case IInvocationOperation inv: return Invoke(inv, false);
            case IObjectCreationOperation oc: return New(oc);
            case IArrayCreationOperation ac: return NewArray(ac);
            case IBinaryOperation b: return Binary(b);
            case IUnaryOperation u: return Unary(u);
            case IConditionalOperation c: return Ternary(c);
            case ISimpleAssignmentOperation a:
                {
                    if (a.IsRef) return Todo("ref assignment as a value");
                    var v = ValueOf(a.Value, a.Target.Type);
                    var d = Direct(a.Target, true);
                    if (d != null && IsLocalName(d)) return "(" + d + " := " + Strip(v) + ")";
                    var t = IsConstText(v) ? v : Fresh("v");
                    if (t != v) Pre.Add(t + " = " + Strip(v));
                    Pre.Add(Assign(a.Target, t));
                    Stat("assignment as a value (hoisted)");
                    return t;
                }
            case ICompoundAssignmentOperation ca: return Compound(ca, false);
            case IIncrementOrDecrementOperation inc: return IncDec(inc, false);
            case IConditionalAccessOperation ca: return CondAccess(ca);
            case IConditionalAccessInstanceOperation: return condAccess.Count > 0 ? condAccess.Peek() : Todo("conditional access instance");
            case IAnonymousFunctionOperation af: return Lambda(af, null);
            case IDelegateCreationOperation dc: return DelegateCreation(dc);
            case IDefaultValueOperation dv: return DefaultOf(dv.Type!);
            case ILiteralOperation lit: return Const(lit.ConstantValue.Value, lit.Type);
            case IIsTypeOperation it: return "isinstance(" + V(it.ValueOperand) + ", " + PyTypeRef(it.TypeOperand) + ")";
            case IIsPatternOperation ip when ip.Pattern is IConstantPatternOperation cpat && cpat.Value.ConstantValue is { HasValue: true, Value: null }:
                return "(" + V(ip.Value) + " is None)";
            case IThrowOperation th:
                return Use("throw_", "raise its argument (a throw expression)") + "(" + V(th.Exception!) + ")";
            case ICoalesceOperation co:
                {
                    // a ?? b: b only when a is null
                    var a = V(co.Value);
                    var saved = Pre; Pre = new List<string>();
                    var b = ValueOf(co.WhenNull, co.Type);
                    var pb = Pre; Pre = saved;
                    var t = Fresh("co");
                    if (pb.Count == 0) return "(" + t + " if (" + t + " := " + Strip(a) + ") is not None else " + b + ")";
                    Pre.Add(t + " = " + Strip(a));
                    Pre.Add("if " + t + " is None:");
                    foreach (var l in pb) Pre.Add("    " + l);
                    Pre.Add("    " + t + " = " + Strip(b));
                    return t;
                }
            case IParenthesizedOperation pp: return X(pp.Operand);
            case IDiscardOperation: return Todo("discard as a value");
            case ISizeOfOperation so: return Todo("sizeof");
            case ITypeOfOperation: return Todo("typeof");
            case IDeclarationExpressionOperation de:
                return Todo("declaration expression");
        }
        return Todo("expression " + op.Kind);
    }

    Ex Read(ILocalSymbol l)
    {
        if (refByValue.Contains(l)) return Name(l);
        if (Boxed(l) || l.RefKind != RefKind.None) return Name(l) + ".v";
        return Name(l);
    }

    /// <summary>A type for isinstance.</summary>
    static string PyTypeRef(ITypeSymbol t)
    {
        if (t is INamedTypeSymbol n && IsEngine(n)) return TypeIdent(n);
        if (t is INamedTypeSymbol r && IsRuntime(r)) return Use(RuntimeTypeName(r), "type " + r.OriginalDefinition.ToDisplayString() + " (isinstance)");
        if (t.SpecialType == SpecialType.System_String) return "str";
        if (t is INamedTypeSymbol sx && IsSystem(sx) && sx.Name == "Exception") return "Exception";
        return Todo("type test against " + t.ToDisplayString());
    }

    /// <summary>A list index: a clean int.</summary>
    string Index(IOperation i)
    {
        var s = V(i);
        return Strip(s);
    }

    static string Paren(string s) => Atomic(s) ? s : "(" + s + ")";

    /// <summary>A name, an attribute path, a call or subscript of one, a literal, or a fully parenthesised expression.</summary>
    static bool Atomic(string s)
    {
        if (s.Length == 0) return false;
        if (s[0] == '(' && s[^1] == ')' && Balanced(s.Substring(1, s.Length - 2))) return true;
        int depth = 0; bool inStr = false; char q = '\0';
        for (int i = 0; i < s.Length; i++)
        {
            var ch = s[i];
            if (inStr) { if (ch == '\\') { i++; continue; } if (ch == q) inStr = false; continue; }
            if (ch == '\'' || ch == '"') { inStr = true; q = ch; continue; }
            if (ch == '(' || ch == '[') depth++;
            else if (ch == ')' || ch == ']') depth--;
            else if (depth == 0 && !(char.IsLetterOrDigit(ch) || ch == '_' || ch == '.')) return false;
        }
        return depth == 0;
    }

    // ---- constants
    public static Ex Const(object? v, ITypeSymbol? t)
    {
        if (v == null) return t != null && t.IsValueType && !(t is INamedTypeSymbol nn && nn.OriginalDefinition.SpecialType == SpecialType.System_Nullable_T) ? DefaultOf(t) : "None";
        if (t != null && t.TypeKind == TypeKind.Enum) t = Under(t);
        switch (v)
        {
            case bool b: return b ? "True" : "False";
            case string s: return PyStr(s);
            case char ch: return ((int)ch).ToString(CultureInfo.InvariantCulture);
            case double d: return t != null && t.SpecialType == SpecialType.System_Single ? DoubleLit((float)d) : DoubleLit(d);
            case float f: return DoubleLit(f);
        }
        var st = t?.SpecialType ?? SpecialType.System_Int32;
        if (st == SpecialType.System_Double || st == SpecialType.System_Single)
        {
            double dv = v switch { int i => i, long l => l, uint u => u, ulong ul => ul, short sh => sh, ushort us => us, byte by => by, sbyte sb => sb, _ => 0 };
            return DoubleLit(st == SpecialType.System_Single ? (float)dv : dv);
        }
        if (st == SpecialType.System_Char) st = SpecialType.System_UInt16;
        System.Numerics.BigInteger val = v switch { int i => i, long l => l, uint u => u, ulong ul => ul, short sh => sh, ushort us => us, byte by => by, sbyte sb => sb, _ => 0 };
        var s0 = val.ToString(CultureInfo.InvariantCulture);
        return val < 0 ? "(" + s0 + ")" : s0;
    }

    static string DoubleLit(double d)
    {
        if (double.IsNaN(d))
        {
            var bits = (ulong)BitConverter.DoubleToInt64Bits(d);
            var n = "_NAN_" + bits.ToString("X16");
            moduleConsts[n] = Use("f64_from_bits", "the double with the given IEEE bits (a NaN constant keeps its sign and payload)") + "(0x" + bits.ToString("X16") + ")";
            return n;
        }
        if (double.IsPositiveInfinity(d)) return "_INF";
        if (double.IsNegativeInfinity(d)) return "(-_INF)";
        if (d == 0 && double.IsNegative(d)) return "(-0.0)";
        var s = d.ToString("R", CultureInfo.InvariantCulture);
        if (!s.Contains('.') && !s.Contains('E')) s += ".0";
        return d < 0 ? "(" + s + ")" : s;
    }

    public static string PyStr(string s)
    {
        var sb = new StringBuilder("'");
        foreach (var ch in s)
        {
            int c = ch;
            if (c == '\'') sb.Append("\\'");
            else if (c == '\\') sb.Append("\\\\");
            else if (c >= 32 && c < 127) sb.Append(ch);
            else if (c == '\n') sb.Append("\\n");
            else if (c == '\t') sb.Append("\\t");
            else if (c == '\r') sb.Append("\\r");
            else if (c < 256) sb.Append("\\x").Append(c.ToString("x2"));
            else sb.Append("\\u").Append(c.ToString("x4"));
        }
        return sb.Append('\'').ToString();
    }

    // ---- conversions
    Ex Conversion(IConversionOperation c)
    {
        var from = c.Operand.Type;
        var to = c.Type!;
        if (c.OperatorMethod != null)
        {
            if (IsSystem(c.OperatorMethod)) return Todo("system conversion operator " + c.OperatorMethod.ToDisplayString());
            return RuntimeConvOp(c.OperatorMethod, c.Operand);
        }
        if (c.Operand is IAnonymousFunctionOperation af) return Lambda(af, to);
        if (c.Operand is IDelegateCreationOperation dc0 && to.TypeKind == TypeKind.Delegate) return DelegateCreation(dc0);
        if (from == null)
        {
            if (c.Operand is ILiteralOperation || c.Operand is IDefaultValueOperation) return DefaultOf(to);
            return X(c.Operand);
        }
        if (c.IsTryCast)
        {
            var v = V(c.Operand);
            var t = Fresh("as");
            return "(" + t + " if isinstance((" + t + " := " + Strip(v) + "), " + PyTypeRef(to) + ") else None)";
        }
        return Conv(X(c.Operand), from, to);
    }

    /// <summary>C#'s conversion of a value of type from to type to.</summary>
    public Ex Conv(Ex e, ITypeSymbol from, ITypeSymbol to)
    {
        if (SymbolEqualityComparer.Default.Equals(from, to)) return e;
        var f = Under(from); var t = Under(to);
        if (SymbolEqualityComparer.Default.Equals(f, t)) return e;
        if (IsNumeric(t) && IsNumeric(f))
        {
            if (IsFloat(f) && IsIntegral(t))
            {
                // C# on x86-64 (cvttsd2si): NaN and out-of-range values give the integer indefinite
                var k = t.SpecialType switch
                {
                    SpecialType.System_Int32 => "i32", SpecialType.System_UInt32 => "u32", SpecialType.System_Int64 => "i64", SpecialType.System_UInt64 => "u64",
                    SpecialType.System_Int16 => "i16", SpecialType.System_UInt16 => "u16", SpecialType.System_SByte => "i8", SpecialType.System_Byte => "u8", SpecialType.System_Char => "u16",
                    _ => "x"
                };
                Use("f2" + k, "C# (" + t.ToDisplayString() + ") of a double or float, unchecked, on x86-64 (the Java runtime's F2." + k + ")");
                return "f2" + k + "(" + Strip(e.S) + ")";
            }
            if (IsDouble(t))
            {
                if (IsSingle(f)) return e;
                // an integer: exact for 8..32-bit sources, so Python's mixed arithmetic converts it as C# does
                var ce = Clean(e, f);
                if (Width(f) <= 32) return new Ex(ce, xint: true);
                return "float(" + Strip(ce) + ")";
            }
            if (IsSingle(t))
            {
                var ce = IsIntegral(f) ? Clean(e, f) : e.S;
                return Use("f32", "round to IEEE single precision (C# float)") + "(" + Strip(ce) + ")";
            }
            // integral to integral
            int wf = Width(f), wt = Width(t);
            bool uf = IsUnsigned(f), ut = IsUnsigned(t);
            if (wt > wf)
            {
                var ce = Clean(e, f);
                if (!uf && ut)
                {
                    if (e.S.Length > 0 && IsNonNeg(e)) return ce;
                    return WrapInt(Paren(ce), t);
                }
                return ce;       // sign- or zero-extension keeps the value
            }
            if (wt == wf && uf == ut) return e;
            if (!e.Raw && wt == wf && !uf && ut && IsNonNeg(e)) return e;
            if (!e.Raw && wt == wf && uf && !ut && e.S.Length > 0 && char.IsDigit(e.S[0]) && System.Numerics.BigInteger.TryParse(e.S, out var cvv) && cvv < System.Numerics.BigInteger.Pow(2, wt - 1)) return e;
            return new Ex(WrapInt(Paren(e.S), t));
        }
        if (t.SpecialType == SpecialType.System_Boolean && f.SpecialType == SpecialType.System_Boolean) return e;
        // boxing, unboxing, reference conversions: the Python value is the same object
        if (to.SpecialType == SpecialType.System_Object || to.TypeKind == TypeKind.Interface) return new Ex(Clean(e, from));
        if (from.SpecialType == SpecialType.System_Object || from.TypeKind == TypeKind.Interface || from is ITypeParameterSymbol) return e;
        if (to is INamedTypeSymbol tex && IsSystem(tex) && tex.Name == "Exception") return e;
        if (from is INamedTypeSymbol fn && to is INamedTypeSymbol tn && (IsEngine(fn) || IsRuntime(fn)) && (IsEngine(tn) || IsRuntime(tn)))
        {
            if (Derives(fn, tn) || tn.TypeKind == TypeKind.Interface || Derives(tn, fn)) return e;
        }
        if (from.TypeKind == TypeKind.Delegate && to.TypeKind == TypeKind.Delegate) return e;
        if (to is INamedTypeSymbol nl && nl.OriginalDefinition.SpecialType == SpecialType.System_Nullable_T) return new Ex(Clean(e, from));
        if (from is INamedTypeSymbol nf && nf.OriginalDefinition.SpecialType == SpecialType.System_Nullable_T) return e;
        if (SymbolEqualityComparer.Default.Equals(from.OriginalDefinition, to.OriginalDefinition)) return e;
        if (from.TypeKind == TypeKind.Array && to.TypeKind == TypeKind.Array) return e;
        if (from.TypeKind == TypeKind.Array && to is INamedTypeSymbol sa && IsSystem(sa)) return e;
        if (from is INamedTypeSymbol fs && IsSystem(fs) && fs.Name == "Exception") return e;
        return Todo("conversion " + from.ToDisplayString() + " -> " + to.ToDisplayString());
    }

    /// <summary>A clean integer known not to be negative: a non-negative literal, a range-for variable counting up
    /// from a non-negative start, a value masked with a non-negative constant.</summary>
    bool IsNonNeg(Ex e)
    {
        if (e.Raw) return false;
        var s = e.S;
        if (s.Length > 0 && char.IsDigit(s[0]) && s.All(char.IsLetterOrDigit)) return true;
        foreach (var sym in nonNeg) if (names.TryGetValue(sym, out var n) && n == s) return true;
        return false;
    }

    Ex RuntimeConvOp(IMethodSymbol op, IOperation operand)
    {
        var t = op.ContainingType;
        var p0 = op.OriginalDefinition.Parameters[0].Type;
        var nm = RuntimeTypeName(t) + "_from_" + ShortType(p0);
        Use(nm, "conversion " + op.ToDisplayString());
        return nm + "(" + Strip(ValueOf(operand, op.Parameters[0].Type)) + ")";
    }

    /// <summary>The specialisation suffix of a generic runtime member used with primitive type arguments:
    /// __ and one code per type argument (the class's, then the method's), O for a reference type (Java's rule).</summary>
    static string Spec(INamedTypeSymbol? t, IMethodSymbol? m)
    {
        var args = new List<ITypeSymbol>();
        if (t != null && t.IsGenericType && !SpecializedClass(t)) args.AddRange(t.TypeArguments);
        if (m != null && m.IsGenericMethod) args.AddRange(m.TypeArguments);
        if (args.Count == 0 || args.All(a => PrimCode(a) == null)) return "";
        // Mv.Back<R, T>(r, ref v, box) copies the box back into v. A Python int cannot say whether the runtime read
        // memory into an int or a uint (memcpy, IStream.read decode signed), so for a uint or ulong T the copy-back
        // masks: T's code is U (uint) or Q (ulong) there, and Mv_Back__XU / __XQ store box[0] & 0xFFFFFFFF[FFFFFFFF].
        if (m != null && m.Name == "Back" && m.ContainingType?.Name == "Mv" && m.TypeArguments.Length == 2)
            return "__" + (PrimCode(m.TypeArguments[0]) ?? "O") + m.TypeArguments[1].SpecialType switch
            {
                SpecialType.System_UInt32 => "U", SpecialType.System_UInt64 => "Q", _ => PrimCode(m.TypeArguments[1]) ?? "O"
            };
        return "__" + string.Concat(args.Select(a => PrimCode(a) ?? "O"));
    }

    // ---- fields and properties
    Ex Field(IFieldReferenceOperation f)
    {
        var fs = f.Field;
        if (fs.ContainingType.TypeKind == TypeKind.Enum) return Const(fs.ConstantValue, fs.Type);
        if (IsRuntime(fs))
        {
            if (fs.IsStatic || fs.IsConst) return StaticRuntimeField(fs);
            return Recv(f.Instance!) + "." + RuntimeField(fs);
        }
        if (fs.IsStatic || fs.IsConst) return StaticFieldName(fs);
        return Recv(f.Instance!) + "." + FieldIdent(fs);
    }

    static string StaticRuntimeField(IFieldSymbol fs)
    {
        var n = RuntimeTypeName(fs.ContainingType) + "_" + fs.Name;
        return Use(n, "static field " + fs.OriginalDefinition.ToDisplayString());
    }

    static string RuntimeField(IFieldSymbol fs)
    {
        UseM(fs.Name, "field " + fs.OriginalDefinition.ToDisplayString());
        return fs.Name;
    }

    string Recv(IOperation inst)
    {
        var s = V(inst);
        return Paren(s);
    }

    /// <summary>A dotted name: safe to evaluate twice.</summary>
    static bool Simple(string s) => s.Length > 0 && (char.IsLetter(s[0]) || s[0] == '_') && s.All(ch => char.IsLetterOrDigit(ch) || ch == '_' || ch == '.');
    static bool SimpleName(string s) => IsLocalName(s);

    static bool IsCPtrIndexer(IPropertyReferenceOperation p) => p.Property.IsIndexer && p.Property.ContainingType.Name == "CPtr" && IsRuntime(p.Property);
    static bool IsVecIndexer(IPropertyReferenceOperation p) => p.Property.IsIndexer && IsRuntime(p.Property) && StripArity(p.Property.ContainingType.Name) is "Vec" or "Str" && p.Property.RefKind != RefKind.None;

    /// <summary>A vector or string element: the backing list and the index.</summary>
    bool VecElem(IPropertyReferenceOperation p, out string arr, out string idx)
    {
        arr = idx = "";
        var tn = StripArity(p.Property.ContainingType.Name);
        if (!p.Property.IsIndexer || !IsRuntime(p.Property) || p.Property.RefKind == RefKind.None) return false;
        if (tn == "Vec") { arr = Recv(p.Instance!) + "." + UseM("a", "field Cpp.Vec<T>.a (the backing list; elements 0..n-1)"); idx = Index(p.Arguments[0].Value); return true; }
        if (tn == "Str") { arr = Recv(p.Instance!) + "." + UseM("b", "field Cpp.Str.b (the backing bytearray; bytes 0..n-1)"); idx = Index(p.Arguments[0].Value); return true; }
        return false;
    }

    /// <summary>new Ptr&lt;T&gt;(array, offset)[i] (or v.data()[i]): the array element itself.</summary>
    bool PtrOverArray(IPropertyReferenceOperation p, out string arr, out string idx)
    {
        arr = idx = "";
        if (!p.Property.IsIndexer || !IsRuntime(p.Property) || StripArity(p.Property.ContainingType.Name) != "Ptr") return false;
        var inst = p.Instance;
        while (inst is IConversionOperation cv && cv.Conversion.IsIdentity) inst = cv.Operand;
        if (inst is IObjectCreationOperation oc && oc.Constructor!.Parameters.Length == 2 && oc.Constructor.Parameters[0].Type is IArrayTypeSymbol)
        {
            var a = Paren(V(oc.Arguments.First(x => x.Parameter!.Ordinal == 0).Value));
            var off = oc.Arguments.First(x => x.Parameter!.Ordinal == 1).Value;
            var i = Index(p.Arguments[0].Value);
            arr = a;
            if (off.ConstantValue.HasValue && Convert.ToInt64(off.ConstantValue.Value) == 0) idx = i;
            else idx = Paren(V(off)) + " + " + Paren(i);
            Stat("ptr over array element");
            return true;
        }
        if (inst is IInvocationOperation inv && inv.TargetMethod.Name == "data" && IsRuntime(inv.TargetMethod) && StripArity(inv.TargetMethod.ContainingType.Name) == "Vec" && inv.Arguments.Length == 0)
        {
            arr = Recv(inv.Instance!) + "." + UseM("a", "field Cpp.Vec<T>.a (the backing list; elements 0..n-1)");
            idx = Index(p.Arguments[0].Value);
            Stat("vec data element");
            return true;
        }
        return false;
    }

    /// <summary>A runtime property or indexer: get_X(args) for its value, ref_X(args) for a Ref (Java's names).</summary>
    Ex Prop(IPropertyReferenceOperation p, bool asRef)
    {
        var ps = p.Property;
        if (IsSystem(ps) && ps.Name == "Length") return "len(" + V(p.Instance!) + ")";
        if (IsSystem(ps) && ps.Name == "HasValue" && ps.ContainingType.OriginalDefinition.SpecialType == SpecialType.System_Nullable_T) return "(" + V(p.Instance!) + " is not None)";
        if (IsSystem(ps) && ps.Name == "Value" && ps.ContainingType.OriginalDefinition.SpecialType == SpecialType.System_Nullable_T) return V(p.Instance!);
        if (IsSystem(ps) && ps.Name == "Message" && ps.ContainingType.Name == "Exception") return Use("exc_message", "System.Exception.Message of a caught exception (a Python str)") + "(" + V(p.Instance!) + ")";
        if (!IsRuntime(ps)) return Todo((IsSystem(ps) ? "system" : "engine") + " property " + ps.ContainingType.Name + "." + ps.Name);
        if (!asRef && VecElem(p, out var arr, out var idx)) return arr + "[" + idx + "]";
        if (!asRef && PtrOverArray(p, out var arr2, out var idx2)) return arr2 + "[" + idx2 + "]";
        if (IsCPtrIndexer(p) && !asRef) return Recv(p.Instance!) + "." + UseM("get", "method CPtr.this[int] get (get(i))") + "(" + Index(p.Arguments[0].Value) + ")";
        // Vec.size() and friends are methods, not properties
        var cn = RuntimeTypeName(ps.ContainingType);
        var nm = ps.IsIndexer ? "Item" : ps.Name;
        var sfx = ps.IsIndexer && ps.ContainingType.GetMembers().OfType<IPropertySymbol>().Count(x => x.IsIndexer) > 1 ? "_" + ShortType(ps.OriginalDefinition.Parameters[0].Type) : "";
        var args = string.Join(", ", p.Arguments.OrderBy(a => a.Parameter!.Ordinal).Select(a => Strip(ValueOf(a.Value, a.Parameter!.Type))));
        var key = "property " + ps.ContainingType.OriginalDefinition.ToDisplayString() + "." + ps.Name + (ps.IsIndexer ? "[" + string.Join(",", ps.Parameters.Select(x => x.Type.ToDisplayString())) + "]" : "");
        var recv = ps.IsStatic ? null : Recv(p.Instance!);
        if (asRef)
        {
            var rn = "ref_" + nm + sfx;
            if (ps.IsStatic) return Use(cn + "_" + rn, key + " (static, a Ref)") + "(" + args + ")";
            UseM(rn, key + " (a Ref)");
            return recv + "." + rn + "(" + args + ")";
        }
        var gn = "get_" + nm + sfx;
        if (ps.IsStatic) return Use(cn + "_" + gn, key + " (static)") + "(" + args + ")";
        UseM(gn, key);
        return recv + "." + gn + "(" + args + ")";
    }

    string PropSet(IPropertyReferenceOperation p, string v)
    {
        var ps = p.Property;
        if (!IsRuntime(ps)) return Todo("set engine property " + ps.Name);
        var sn = "set_" + ps.Name;
        if (ps.IsStatic) return Use(RuntimeTypeName(ps.ContainingType) + "_" + sn, "property set " + ps.ContainingType.OriginalDefinition.ToDisplayString() + "." + ps.Name + " (static)") + "(" + Strip(v) + ")";
        UseM(sn, "property set " + ps.ContainingType.OriginalDefinition.ToDisplayString() + "." + ps.Name);
        return Recv(p.Instance!) + "." + sn + "(" + Strip(v) + ")";
    }

    /// <summary>An assignment through a ref property or indexer: set_X(args, v).</summary>
    string PropRefSet(IPropertyReferenceOperation p, string v)
    {
        var ps = p.Property;
        var nm = ps.IsIndexer ? "Item" : ps.Name;
        var sfx = ps.IsIndexer && ps.ContainingType.GetMembers().OfType<IPropertySymbol>().Count(x => x.IsIndexer) > 1 ? "_" + ShortType(ps.OriginalDefinition.Parameters[0].Type) : "";
        var args = p.Arguments.OrderBy(a => a.Parameter!.Ordinal).Select(a => Strip(ValueOf(a.Value, a.Parameter!.Type))).ToList();
        args.Add(Strip(v));
        var key = "property " + ps.ContainingType.OriginalDefinition.ToDisplayString() + "." + ps.Name + (ps.IsIndexer ? "[" + string.Join(",", ps.Parameters.Select(x => x.Type.ToDisplayString())) + "]" : "") + " (assignment through the reference: set_" + nm + sfx + "(args..., v))";
        if (ps.IsStatic) return Use(RuntimeTypeName(ps.ContainingType) + "_set_" + nm + sfx, key) + "(" + string.Join(", ", args) + ")";
        UseM("set_" + nm + sfx, key);
        return Recv(p.Instance!) + ".set_" + nm + sfx + "(" + string.Join(", ", args) + ")";
    }

    // ---- calls
    public string ArgList(IMethodSymbol m, IEnumerable<IArgumentOperation> args) => Args(m, args);

    string Args(IMethodSymbol m, IEnumerable<IArgumentOperation> args, bool runtime = false)
    {
        var outv = new List<string>();
        var list = args.OrderBy(a => a.Parameter?.Ordinal ?? 0).ToList();
        // the arguments are evaluated in order: one that needs statements first moves the earlier ones with it
        var texts = new List<string>();
        foreach (var a in list)
        {
            var prm = a.Parameter!;
            int pre0 = Pre.Count;
            string text;
            if (a.ArgumentKind == ArgumentKind.ParamArray)
            {
                if (a.Value is IArrayCreationOperation ac && ac.Initializer != null)
                {
                    var et = ((IArrayTypeSymbol)prm.Type).ElementType;
                    var parts = ac.Initializer.ElementValues.Select(e => runtime ? VarArg(e, et) : Strip(ValueOf(e, et))).ToList();
                    text = runtime ? string.Join(", ", parts) : "[" + string.Join(", ", parts) + "]";
                    if (parts.Count == 0 && runtime) text = "";
                }
                else text = runtime ? "*" + Paren(V(a.Value)) : Strip(V(a.Value));
            }
            else if (prm.RefKind is RefKind.Ref or RefKind.Out) text = RefOf(a.Value);
            else text = Strip(ValueOf(a.Value, prm.Type));
            if (Pre.Count > pre0 && texts.Count > 0) SpillEarlier(texts, pre0);
            texts.Add(text);
        }
        return string.Join(", ", texts.Where(t => t.Length > 0));
    }

    /// <summary>Earlier operands that are not stable names are evaluated into temporaries before the statements a
    /// later operand needs (inserted at pre0), so C#'s left-to-right order holds.</summary>
    void SpillEarlier(List<string> texts, int pre0)
    {
        var ins = new List<string>();
        for (int i = 0; i < texts.Count; i++)
        {
            var t = texts[i];
            if (t.Length == 0 || IsConstText(t)) continue;
            if (t.StartsWith("*")) continue;
            var tv = Fresh("arg");
            ins.Add(tv + " = " + t);
            texts[i] = tv;
        }
        Pre.InsertRange(pre0, ins);
        Stat("arguments spilled for order");
    }

    static readonly System.Text.RegularExpressions.Regex ConstRx = new(@"^(\(-)?[0-9][0-9A-Za-z_.+\-]*\)?$|^'([^'\\]|\\.)*'$|^None$|^True$|^False$|^_INF$|^\(-_INF\)$|^_NAN_[0-9A-F]+$");

    public static bool IsLiteral(string t) => IsConstText(t) || IsConstText(Strip(t));
    public static string StripPub(string t) => Strip(t);

    /// <summary>A literal: safe to evaluate twice or out of order.</summary>
    static bool IsConstText(string t) => ConstRx.IsMatch(t) && !(t.Contains('(') && !t.StartsWith("(-"));

    /// <summary>A params object[] element: a value whose C# type the runtime's printf must know is wrapped in a
    /// typed box (Python ints have no width): I64, U32, U64, I16, U16, I8, U8, F32, Ch; int, double, bool, strings,
    /// objects as they are.</summary>
    string VarArg(IOperation e, ITypeSymbol et)
    {
        var src = e is IConversionOperation cv && cv.IsImplicit && et.SpecialType == SpecialType.System_Object ? cv.Operand : e;
        if (et.SpecialType == SpecialType.System_Object && src.Type != null)
        {
            var st = Under(src.Type);
            var v = Strip(V(src));
            string? box = st.SpecialType switch
            {
                SpecialType.System_UInt32 => "U32", SpecialType.System_UInt64 => "U64", SpecialType.System_Int64 => "I64",
                SpecialType.System_Byte => "U8", SpecialType.System_SByte => "I8", SpecialType.System_Int16 => "I16", SpecialType.System_UInt16 => "U16",
                SpecialType.System_Char => "Ch", SpecialType.System_Single => "F32", _ => null
            };
            if (box != null) return Use(box, "a typed box for a params object[] argument of C# type " + st.ToDisplayString() + " (.v holds the value)") + "(" + v + ")";
            return v;
        }
        return Strip(ValueOf(e, et));
    }

    Ex Invoke(IInvocationOperation inv, bool asRef)
    {
        var m = inv.TargetMethod;
        string call;
        if (m.MethodKind == MethodKind.DelegateInvoke)
        {
            call = Recv(inv.Instance!) + "(" + Args(m, inv.Arguments) + ")";
            if (m.RefKind != RefKind.None) return asRef ? call : call + ".v";
            return call;
        }
        if (IsRuntime(m) || IsSystem(m)) return RuntimeCall(m, inv.Instance, inv.Arguments, asRef);
        if (m.IsStatic) call = StaticFuncName(m) + "(" + Args(m, inv.Arguments) + ")";
        else
        {
            var mn = MethodIdent(m);
            if (inv.Instance is IInstanceReferenceOperation ir && ir.ReferenceKind == InstanceReferenceKind.ContainingTypeInstance && !SymbolEqualityComparer.Default.Equals(m.ContainingType.OriginalDefinition, cls.OriginalDefinition)
                && ((!inv.IsVirtual && (m.IsVirtual || m.IsOverride || m.IsAbstract)) || ir.Syntax is Microsoft.CodeAnalysis.CSharp.Syntax.BaseExpressionSyntax))
            {
                // base.M(): the implementation in that class, not the override. That includes a method that keeps
                // its name because it implements a runtime interface (ICopy's Assign): the derived class's Assign
                // replaces the base one under the same Python name, so self.Assign(s) would call itself (lessons/11)
                var a = Args(m, inv.Arguments);
                call = TypeIdent(m.ContainingType) + "." + mn + "(self" + (a.Length > 0 ? ", " + a : "") + ")";
            }
            else
            {
                var r = Recv(inv.Instance!);
                call = r + "." + mn + "(" + Args(m, inv.Arguments) + ")";
            }
        }
        if (m.RefKind != RefKind.None) return asRef ? call : call + ".v";
        return call;
    }

    Ex RuntimeCall(IMethodSymbol m, IOperation? inst, System.Collections.Immutable.ImmutableArray<IArgumentOperation> argOps, bool asRef)
    {
        var t = m.ContainingType;
        if (IsSystem(m)) return SystemCall(m, inst, argOps);
        // inlined: M.min, M.max, M.clamp of numbers (the C# runtime's definitions)
        if (t.Name == "M" && m.IsStatic && !m.IsGenericMethod && m.Parameters.All(p => IsNumeric(p.Type)) && m.Name is "min" or "max" or "clamp")
            return InlineMinMax(m, argOps);
        // Vec.size(), Vec.empty(), Str.size(), Str.length(), Str.empty(): the count field
        if (!m.IsStatic && inst != null && m.Parameters.Length == 0 && StripArity(t.Name) is "Vec" or "Str" && m.Name is "size" or "empty" or "length")
        {
            var r = Recv(inst);
            UseM("n", "field " + (StripArity(t.Name) == "Vec" ? "Cpp.Vec<T>.n" : "Cpp.Str.n") + " (the element count; size(), length(), empty() read it)");
            return m.Name == "empty" ? "(" + r + ".n == 0)" : r + ".n";
        }
        var args = Args(m, argOps, true);
        if (m.Name == "ToString" && !m.IsStatic) return Recv(inst!) + "." + UseM("ToString", "method ToString() of a runtime value (a Python str)") + "()";
        var overloads = t.GetMembers(m.Name).OfType<IMethodSymbol>().Count();
        var sfx = overloads > 1 ? "_" + string.Join("_", m.OriginalDefinition.Parameters.Select(p => ShortType(p.Type) + (p.RefKind != RefKind.None ? "r" : ""))) : "";
        var spec = Spec(m.IsStatic ? t : null, m);
        var nm = m.Name + sfx + spec;
        var refm = m.RefKind != RefKind.None;
        if (refm && asRef) nm = "ref_" + nm;
        string call;
        bool maker = false;
        if (m.IsStatic && m.IsGenericMethod && spec.Length == 0 && (t.Name == "Mv" && m.Name == "Take" || t.Name == "Arr" && (m.Name == "Make" && m.Parameters.Length == 1 || m.Name == "Make2")))
        {
            // these make a T with Elem<T>.Make: the maker goes last
            args = (args.Length > 0 ? args + ", " : "") + Maker(m.TypeArguments[0]);
            maker = true;
        }
        if (m.IsStatic)
        {
            var cn = RuntimeTypeName(t);
            var full = cn + "_" + nm;
            Use(full, "static " + m.OriginalDefinition.ToDisplayString() + (spec.Length > 0 ? " " + spec : "") + (refm && asRef ? " (a Ref)" : "") + (maker ? " + maker" : ""));
            call = full + "(" + args + ")";
        }
        else
        {
            UseM(nm, "method " + m.OriginalDefinition.ToDisplayString() + (spec.Length > 0 ? " " + spec : "") + (refm && asRef ? " (a Ref)" : ""));
            call = Recv(inst!) + "." + nm + "(" + args + ")";
        }
        return call;
    }

    /// <summary>M.min(a, b) = b &lt; a ? b : a, M.max(a, b) = a &lt; b ? b : a, M.clamp(v, lo, hi) = v &lt; lo ? lo :
    /// hi &lt; v ? hi : v, written inline with temporaries for operands that are not simple.</summary>
    Ex InlineMinMax(IMethodSymbol m, System.Collections.Immutable.ImmutableArray<IArgumentOperation> argOps)
    {
        var vals = argOps.OrderBy(a => a.Parameter!.Ordinal).Select(a => Strip(ValueOf(a.Value, a.Parameter!.Type))).ToList();
        // operands evaluated once, in order: the first is bound where it is first used
        var uses = vals.Select(v => Simple(v) || IsConstText(v) ? v : null).ToList();
        string[] bind = new string[vals.Count], use = new string[vals.Count];
        for (int i = 0; i < vals.Count; i++)
        {
            if (uses[i] != null) { bind[i] = use[i] = uses[i]!; continue; }
            var t = Fresh("m");
            bind[i] = "(" + t + " := " + vals[i] + ")";
            use[i] = t;
        }
        Stat("inline " + m.Name);
        if (m.Name == "min") return "(" + use[1] + " if " + bind[1] + " < " + bind[0] + " else " + use[0] + ")";
        if (m.Name == "max") return "(" + use[1] + " if " + bind[0] + " < " + bind[1] + " else " + use[0] + ")";
        // clamp(v, lo, hi): v first, then lo; hi is bound when it is compared
        return "(" + use[1] + " if " + bind[0] + " < " + bind[1] + " else (" + use[2] + " if " + bind[2] + " < " + use[0] + " else " + use[0] + "))";
    }

    Ex SystemCall(IMethodSymbol m, IOperation? inst, System.Collections.Immutable.ImmutableArray<IArgumentOperation> argOps)
    {
        var t = m.ContainingType.Name;
        string args() => Args(m, argOps);
        if (t == "Math" || t == "MathF")
        {
            if (m.Name == "Abs" && m.Parameters.Length == 1)
            {
                var a = X(argOps[0].Value);
                if (IsFloat(m.Parameters[0].Type)) return "abs(" + Strip(Clean(a, m.Parameters[0].Type)) + ")";
                return "abs(" + Strip(Clean(a, m.Parameters[0].Type)) + ")";
            }
            var n = "Math_" + m.Name + (m.Parameters.Length > 0 ? "_" + ShortType(m.Parameters[0].Type) : "");
            if (m.Name == "Sqrt" && m.Parameters.Length == 1 && IsDouble(m.Parameters[0].Type))
            {
                // the fast path inline: math.sqrt of a non-negative number (or -0.0) is IEEE's; the runtime gives the
                // NaN of a negative or NaN argument
                var a = Strip(ValueOf(argOps[0].Value, m.Parameters[0].Type));
                Use(n, "System.Math.Sqrt(double): IEEE sqrt, with x86's NaN for a negative argument");
                if (SimpleName(a)) return "(_m_sqrt(" + a + ") if " + a + " >= 0.0 else " + n + "(" + a + "))";
                var tv = Fresh("q");
                return "(_m_sqrt(" + tv + ") if (" + tv + " := " + a + ") >= 0.0 else " + n + "(" + tv + "))";
            }
            Use(n, "System.Math." + m.Name + "(" + string.Join(",", m.Parameters.Select(p => p.Type.Name)) + "): IEEE results (inf, nan) where Python's math raises");
            return n + "(" + args() + ")";
        }
        if (t == "Object" && m.Name == "ReferenceEquals") { var a = argOps.OrderBy(x => x.Parameter!.Ordinal).Select(x => V(x.Value)).ToList(); return "(" + a[0] + " is " + a[1] + ")"; }
        if (t == "Double" && m.Name == "IsFinite") return "_m_isfinite(" + args() + ")";
        if (t == "Double" && m.Name == "IsNaN") return "_m_isnan(" + args() + ")";
        if (t == "Double" && m.Name == "IsInfinity") return "_m_isinf(" + args() + ")";
        if (t == "Double" && m.Name == "IsPositiveInfinity") return "(" + args() + " == _INF)";
        if (t == "Double" && m.Name == "IsNegativeInfinity") return "(" + args() + " == -_INF)";
        if (t == "Double" && m.Name == "IsNegative") return Use("signbit", "Double.IsNegative: the sign bit of a double") + "(" + args() + ")";
        if (t == "BitConverter" && m.Name == "DoubleToInt64Bits") return Use("f64_to_bits_signed", "BitConverter.DoubleToInt64Bits: the IEEE bits of a double as a signed 64-bit int") + "(" + args() + ")";
        if (t == "BitConverter" && m.Name == "Int64BitsToDouble") return Use("f64_from_bits", "the double with the given IEEE bits (a NaN constant keeps its sign and payload)") + "(" + args() + ")";
        if (t == "Array" && m.Name == "Fill") return Use("Array_Fill", "System.Array.Fill(T[], T): every element the same value") + "(" + args() + ")";
        if (t == "File" && m.Name == "Exists") return Use("File_Exists", "System.IO.File.Exists(string)") + "(" + args() + ")";
        if (m.Name == "Dispose" && inst != null) return Recv(inst) + ".Dispose()";
        if (m.Name == "GetHashCode" && inst != null) return Recv(inst) + "." + UseM("GetHashCode", "method GetHashCode() of a runtime value") + "()";
        if (m.Name == "Equals" && inst != null) return Use("cs_equals", "object.Equals(a, b) as C# would call it (a.Equals(b) for runtime values, == for numbers and strings)") + "(" + V(inst) + ", " + args() + ")";
        if (m.Name == "ToString" && inst != null)
        {
            var it = inst.Type!;
            if (IsIntegral(it)) return "str(" + V(inst) + ")";
            return Use("cs_tostring", "C#'s ToString() of a value (double: shortest round-trip, \"R\")") + "(" + V(inst) + ")";
        }
        return Todo("system call " + m.ContainingType.ToDisplayString() + "." + m.Name);
    }

    Ex New(IObjectCreationOperation oc)
    {
        var t = (INamedTypeSymbol)oc.Type!;
        var c = oc.Constructor!;
        string e;
        if (t.TypeKind == TypeKind.Delegate) return Todo("delegate construction");
        if (IsEngine(t))
        {
            if (IsRawCtor(c) || IsTrivialCtor(c)) e = TypeIdent(t) + "()";
            else e = CtorCallable(c) + "(" + Args(c, oc.Arguments) + ")";
        }
        else if (IsRuntime(t))
        {
            var fname = RuntimeCtorName(c, t);
            var args = MakerArgs(c, oc.Arguments, t);
            Use(fname, "new " + c.OriginalDefinition.ToDisplayString() + (SpecializedClass(t) ? " (" + t.TypeArguments[0].ToDisplayString() + ")" : "") + (args.appended ? " + makers" : ""));
            e = fname + "(" + args.text + ")";
        }
        else if (IsSystem(t))
        {
            if (t.Name.EndsWith("Exception"))
            {
                Use("SysException", "a .NET exception (SysException(kind, message)); except SysException for a catch of a .NET exception type");
                var a0 = oc.Arguments.Length > 0 ? oc.Arguments[0].Value : null;
                var msg = a0 == null ? "''" : a0.Type?.SpecialType == SpecialType.System_String && a0.ConstantValue.HasValue ? V(a0)
                    : Use("cs_tostring", "C#'s ToString() of a value (double: shortest round-trip, \"R\")") + "(" + V(a0) + ")";
                e = "SysException(" + PyStr(t.Name) + ", " + msg + ")";
            }
            else return Todo("new " + t.ToDisplayString());
        }
        else return Todo("new " + t.ToDisplayString());
        if (oc.Initializer == null || oc.Initializer.Initializers.Length == 0) return e;
        // new X() { a = 1, b = 2 }: a temporary, set member by member before the statement
        var o = Fresh("o");
        Pre.Add(o + " = " + e);
        implicitRecv.Push(o);
        foreach (var init in oc.Initializer.Initializers)
        {
            var st = StmtExpr(init);
            if (st.Length > 0) foreach (var l in st.Split('\n')) Pre.Add(l);
        }
        implicitRecv.Pop();
        Stat("object initializer");
        return o;
    }

    // ---- element makers (as the Java translator passes them: a maker of T, of V for the maps, is appended to a
    // generic runtime container's constructor that has no Func parameter, or stands in for a null one)
    (string text, bool appended) MakerArgs(IMethodSymbol c, System.Collections.Immutable.ImmutableArray<IArgumentOperation> argOps, INamedTypeSymbol t)
    {
        var name = StripArity(t.Name);
        var specialized = SpecializedClass(t);
        var outv = new List<string>();
        bool hasFunc = false;
        foreach (var a in argOps.OrderBy(a => a.Parameter?.Ordinal ?? 0))
        {
            var prm = a.Parameter!;
            if (prm.Type is INamedTypeSymbol ft && IsSystem(ft) && ft.Name == "Func" && ft.TypeArguments.Length == 1 && !specialized)
            {
                hasFunc = true;
                if (a.Value.ConstantValue is { HasValue: true, Value: null } || a.Value is IDefaultValueOperation || a.ArgumentKind == ArgumentKind.DefaultValue && a.Value.ConstantValue.HasValue)
                { outv.Add(Maker(ft.TypeArguments[0])); continue; }
            }
            if (a.ArgumentKind == ArgumentKind.ParamArray)
            {
                if (a.Value is IArrayCreationOperation ac && ac.Initializer != null)
                {
                    var et = ((IArrayTypeSymbol)prm.Type).ElementType;
                    foreach (var e in ac.Initializer.ElementValues) outv.Add(VarArg(e, et));
                }
                else outv.Add("*" + Paren(V(a.Value)));
                continue;
            }
            outv.Add(prm.RefKind is RefKind.Ref or RefKind.Out ? RefOf(a.Value) : Strip(ValueOf(a.Value, prm.Type)));
        }
        bool appended = false;
        if (!specialized && !hasFunc)
        {
            if (makerSlots.TryGetValue(name, out var slots))
                foreach (var k in slots) { outv.Add(Maker(t.TypeArguments[k])); appended = true; }
            else if ((name == "Pair" || name == "Tup") && c.Parameters.Length == 0)
                foreach (var ta in t.TypeArguments) { outv.Add(Maker(ta)); appended = true; }
        }
        return (string.Join(", ", outv), appended);
    }

    Ex NewArray(IArrayCreationOperation ac)
    {
        var at = (IArrayTypeSymbol)ac.Type!;
        var et = at.ElementType;
        if (ac.Initializer != null)
        {
            var elems = ac.Initializer.ElementValues.Select(v => Strip(ValueOf(v, et))).ToList();
            return "[" + string.Join(", ", elems) + "]";
        }
        var n = Index(ac.DimensionSizes[0]);
        return "([" + DefaultOf(et) + "] * " + Paren(n) + ")";
    }

    // ---- operators
    static string PyOp(BinaryOperatorKind k) => k switch
    {
        BinaryOperatorKind.Add => "+", BinaryOperatorKind.Subtract => "-", BinaryOperatorKind.Multiply => "*",
        BinaryOperatorKind.Divide => "/", BinaryOperatorKind.Remainder => "%", BinaryOperatorKind.LeftShift => "<<",
        BinaryOperatorKind.RightShift => ">>", BinaryOperatorKind.And => "&", BinaryOperatorKind.Or => "|",
        BinaryOperatorKind.ExclusiveOr => "^", BinaryOperatorKind.ConditionalAnd => "and", BinaryOperatorKind.ConditionalOr => "or",
        BinaryOperatorKind.Equals => "==", BinaryOperatorKind.NotEquals => "!=", BinaryOperatorKind.LessThan => "<",
        BinaryOperatorKind.LessThanOrEqual => "<=", BinaryOperatorKind.GreaterThan => ">", BinaryOperatorKind.GreaterThanOrEqual => ">=",
        BinaryOperatorKind.UnsignedRightShift => ">>",
        _ => Todo("operator " + k)
    };

    static bool IsCompare(BinaryOperatorKind k) => k is BinaryOperatorKind.Equals or BinaryOperatorKind.NotEquals or BinaryOperatorKind.LessThan or BinaryOperatorKind.LessThanOrEqual or BinaryOperatorKind.GreaterThan or BinaryOperatorKind.GreaterThanOrEqual;

    /// <summary>l op r for operands of C# type t (after C#'s promotions).</summary>
    Ex BinOp(BinaryOperatorKind k, ITypeSymbol t, Ex l, Ex r, ITypeSymbol? rt, IOperation? rop)
    {
        var u = Under(t);
        if (IsIntegral(u))
        {
            switch (k)
            {
                case BinaryOperatorKind.Add: case BinaryOperatorKind.Subtract: case BinaryOperatorKind.Multiply:
                    return new Ex("(" + l.S + " " + PyOp(k) + " " + r.S + ")", raw: true);
                case BinaryOperatorKind.And:
                    {
                        bool clean = !l.Raw && !r.Raw || IsUnsigned(u) && (!l.Raw || !r.Raw) || IsNonNegConst(l) || IsNonNegConst(r);
                        return new Ex("(" + l.S + " & " + r.S + ")", raw: !clean);
                    }
                case BinaryOperatorKind.Or: case BinaryOperatorKind.ExclusiveOr:
                    return new Ex("(" + l.S + " " + PyOp(k) + " " + r.S + ")", raw: l.Raw || r.Raw);
                case BinaryOperatorKind.LeftShift:
                    return new Ex("(" + l.S + " << " + ShiftCount(r, rt, u) + ")", raw: true);
                case BinaryOperatorKind.RightShift:
                    return new Ex("(" + Clean(l, u) + " >> " + ShiftCount(r, rt, u) + ")");
                case BinaryOperatorKind.UnsignedRightShift:
                    {
                        var cnt = ShiftCount(r, rt, u);
                        var lv = IsUnsigned(u) ? Clean(l, u) : "(" + Clean(l, u) + " & " + MaskOf(u) + ")";
                        bool cleanRes = IsUnsigned(u) || cnt.Length > 0 && char.IsDigit(cnt[0]) && cnt != "0";
                        return new Ex("(" + lv + " >> " + cnt + ")", raw: !cleanRes);
                    }
                case BinaryOperatorKind.Divide:
                    {
                        var a = Clean(l, u); var b = Clean(r, u);
                        if (IsUnsigned(u)) return "(" + a + " // " + b + ")";
                        if (Width(u) <= 32) return "int(" + a + " / " + b + ")";
                        return Use("idiv", "C# signed integer division (truncates toward zero)") + "(" + Strip(a) + ", " + Strip(b) + ")";
                    }
                case BinaryOperatorKind.Remainder:
                    {
                        var a = Clean(l, u); var b = Clean(r, u);
                        if (IsUnsigned(u)) return "(" + a + " % " + b + ")";
                        // by a positive constant: inline, the remainder taking the sign of the dividend
                        if (Simple(a) && long.TryParse(b, out var bc) && bc > 0) return "(" + a + " % " + b + " if " + a + " >= 0 else -(-" + a + " % " + b + "))";
                        return Use("irem", "C# signed integer remainder (the sign of the dividend)") + "(" + Strip(a) + ", " + Strip(b) + ")";
                    }
            }
            if (IsCompare(k)) return "(" + Clean(l, u) + " " + PyOp(k) + " " + Clean(r, u) + ")";
            return Todo("integer operator " + k);
        }
        if (IsDouble(u))
        {
            switch (k)
            {
                case BinaryOperatorKind.Add: case BinaryOperatorKind.Subtract: case BinaryOperatorKind.Multiply:
                    {
                        // two ints converted to double: the left one becomes a float, so the operation is IEEE's
                        var a = l.XInt && r.XInt ? "float(" + Strip(l.S) + ")" : l.S;
                        return "(" + a + " " + PyOp(k) + " " + r.S + ")";
                    }
                case BinaryOperatorKind.Divide: return FDiv(l, r, rop);
                case BinaryOperatorKind.Remainder:
                    return Use("fmod", "C fmod (C#'s % on doubles): IEEE results where math.fmod raises; accepts ints") + "(" + Strip(l.S) + ", " + Strip(r.S) + ")";
            }
            if (IsCompare(k)) return "(" + l.S + " " + PyOp(k) + " " + r.S + ")";
            return Todo("double operator " + k);
        }
        if (IsSingle(u))
        {
            var f32 = Use("f32", "round to IEEE single precision (C# float)");
            switch (k)
            {
                case BinaryOperatorKind.Add: case BinaryOperatorKind.Subtract: case BinaryOperatorKind.Multiply:
                    return f32 + "(" + l.S + " " + PyOp(k) + " " + r.S + ")";
                case BinaryOperatorKind.Divide: return f32 + "(" + Strip(FDiv(l, r, rop).S) + ")";
                case BinaryOperatorKind.Remainder:
                    return f32 + "(" + Use("fmod", "C fmod (C#'s % on doubles): IEEE results where math.fmod raises; accepts ints") + "(" + Strip(l.S) + ", " + Strip(r.S) + "))";
            }
            if (IsCompare(k)) return "(" + l.S + " " + PyOp(k) + " " + r.S + ")";
            return Todo("float operator " + k);
        }
        if (u.SpecialType == SpecialType.System_Boolean)
        {
            switch (k)
            {
                case BinaryOperatorKind.And: return "(" + l.S + " & " + r.S + ")";
                case BinaryOperatorKind.Or: return "(" + l.S + " | " + r.S + ")";
                case BinaryOperatorKind.ExclusiveOr: return "(" + l.S + " ^ " + r.S + ")";
                case BinaryOperatorKind.Equals: return "(" + l.S + " == " + r.S + ")";
                case BinaryOperatorKind.NotEquals: return "(" + l.S + " != " + r.S + ")";
            }
        }
        if (k is BinaryOperatorKind.Equals or BinaryOperatorKind.NotEquals)
        {
            // references: identity (null is None)
            var eq = k == BinaryOperatorKind.Equals;
            if (t.SpecialType == SpecialType.System_String) return "(" + l.S + (eq ? " == " : " != ") + r.S + ")";
            return "(" + l.S + (eq ? " is " : " is not ") + r.S + ")";
        }
        return Todo("operator " + k + " on " + t.ToDisplayString());
    }

    static bool IsNonNegConst(Ex e) => !e.Raw && e.S.Length > 0 && char.IsDigit(e.S[0]) && e.S.All(char.IsLetterOrDigit);

    /// <summary>C# masks a shift count to the width of the shifted type.</summary>
    static string ShiftCount(Ex r, ITypeSymbol? rt, ITypeSymbol lt)
    {
        int mask = Width(lt) == 64 ? 63 : 31;
        if (long.TryParse(r.S, out var c)) return (c & mask).ToString(CultureInfo.InvariantCulture);
        var cr = rt != null ? Clean(r, rt) : r.S;
        return "(" + cr + " & " + mask + ")";
    }

    /// <summary>a / b for doubles: Python raises on a zero divisor where IEEE gives inf or nan.</summary>
    Ex FDiv(Ex l, Ex r, IOperation? rop)
    {
        var a = l.XInt && r.XInt ? "float(" + Strip(l.S) + ")" : l.S;
        if (rop != null && rop.ConstantValue.HasValue && rop.ConstantValue.Value is double dv && dv != 0 && !double.IsNaN(dv)) return "(" + a + " / " + r.S + ")";
        if (rop != null && rop.ConstantValue.HasValue && rop.ConstantValue.Value is float fv && fv != 0 && !float.IsNaN(fv)) return "(" + a + " / " + r.S + ")";
        if (rop is IConversionOperation cv && cv.Operand.ConstantValue.HasValue && cv.Operand.ConstantValue.Value is int iv && iv != 0) return "(" + a + " / " + r.S + ")";
        var fd = Use("fdiv0", "a / b for b == 0 (or -0.0): IEEE's inf or nan with C#'s signs; a and b may be ints");
        if (Simple(r.S)) return "(" + a + " / " + r.S + " if " + r.S + " else " + fd + "(" + Strip(a) + ", " + r.S + "))";
        var tv = Fresh("d");
        // the dividend is evaluated first, as in C#
        if (Simple(a) || IsConstText(a)) return "(" + a + " / " + tv + " if (" + tv + " := " + Strip(r.S) + ") else " + fd + "(" + Strip(a) + ", " + tv + "))";
        var ta = Fresh("n");
        return "(" + ta + " / " + tv + " if ((" + ta + " := " + Strip(a) + "), (" + tv + " := " + Strip(r.S) + "))[1] else " + fd + "(" + ta + ", " + tv + "))";
    }

    Ex Binary(IBinaryOperation b)
    {
        if (b.OperatorMethod != null)
        {
            if (IsSystem(b.OperatorMethod) && b.OperatorMethod.ContainingType.SpecialType == SpecialType.System_String)
            {
                if (b.OperatorKind == BinaryOperatorKind.Add) return StrConcat(b.LeftOperand, b.RightOperand);
                var a0 = V(b.LeftOperand); var b0 = V(b.RightOperand);
                if (b.OperatorKind == BinaryOperatorKind.Equals) return "(" + a0 + " == " + b0 + ")";
                if (b.OperatorKind == BinaryOperatorKind.NotEquals) return "(" + a0 + " != " + b0 + ")";
            }
            if (IsSystem(b.OperatorMethod) && b.OperatorMethod.ContainingType.TypeKind == TypeKind.Delegate)
            {
                var a0 = V(b.LeftOperand); var b0 = V(b.RightOperand);
                if (b.OperatorKind == BinaryOperatorKind.Equals) return "(" + a0 + " is " + b0 + ")";
                if (b.OperatorKind == BinaryOperatorKind.NotEquals) return "(" + a0 + " is not " + b0 + ")";
            }
            var lv = ValueOf(b.LeftOperand, b.OperatorMethod.Parameters[0].Type);
            int pre0 = Pre.Count;
            var rv = ValueOf(b.RightOperand, b.OperatorMethod.Parameters[1].Type);
            if (Pre.Count > pre0 && !Simple(lv) && !IsConstText(lv)) { var l2 = new List<string> { lv }; SpillEarlier(l2, pre0); lv = l2[0]; }
            return UserBinary(b.OperatorMethod, lv, rv);
        }
        var k = b.OperatorKind;
        if (k is BinaryOperatorKind.ConditionalAnd or BinaryOperatorKind.ConditionalOr) return Logical(b);
        var lt = b.LeftOperand.Type;
        var l = X(b.LeftOperand);
        int p0 = Pre.Count;
        var r = X(b.RightOperand);
        if (Pre.Count > p0 && !Simple(l.S) && !IsConstText(l.S))
        {
            var tv = Fresh("l");
            Pre.Insert(p0, tv + " = " + Strip(Clean(l, lt)));
            l = new Ex(tv);
            Stat("binary operand spilled for order");
        }
        if ((lt != null && lt.SpecialType == SpecialType.System_String || b.Type?.SpecialType == SpecialType.System_String) && k == BinaryOperatorKind.Add) return StrConcat(b.LeftOperand, b.RightOperand);
        // null tests
        if (k is BinaryOperatorKind.Equals or BinaryOperatorKind.NotEquals && (IsNullConst(b.LeftOperand) || IsNullConst(b.RightOperand)))
            return "(" + l.S + (k == BinaryOperatorKind.Equals ? " is " : " is not ") + r.S + ")";
        var opT = lt ?? b.Type!;
        // a shift's count has its own type
        return BinOp(k, opT, l, r, b.RightOperand.Type, b.RightOperand);
    }

    static bool IsNullConst(IOperation op)
    {
        while (op is IConversionOperation cv && cv.OperatorMethod == null) op = cv.Operand;
        return op.ConstantValue is { HasValue: true, Value: null } || op is IDefaultValueOperation dv && dv.Type != null && !dv.Type.IsValueType;
    }

    /// <summary>a &amp;&amp; b, a || b: Python's and/or; when b needs statements, an if before the statement.</summary>
    Ex Logical(IBinaryOperation b)
    {
        bool and = b.OperatorKind == BinaryOperatorKind.ConditionalAnd;
        var l = V(b.LeftOperand);
        var saved = Pre; Pre = new List<string>();
        var r = V(b.RightOperand);
        var pr = Pre; Pre = saved;
        if (pr.Count == 0) return "(" + l + (and ? " and " : " or ") + r + ")";
        var t = Fresh("c");
        Pre.Add(t + " = " + Strip(l));
        Pre.Add((and ? "if " : "if not ") + t + ":");
        foreach (var s in pr) Pre.Add("    " + s);
        Pre.Add("    " + t + " = " + Strip(r));
        Stat("logical with statements");
        return t;
    }

    /// <summary>C# string concatenation: each operand as C# turns it into text.</summary>
    Ex StrConcat(IOperation a, IOperation b) => "(" + StrPart(a) + " + " + StrPart(b) + ")";

    string StrPart(IOperation o0)
    {
        return Part(o0);
        string Part(IOperation o)
        {
            while (o is IConversionOperation cv && cv.OperatorMethod == null && cv.Type?.SpecialType == SpecialType.System_Object) o = cv.Operand;
            var t = o.Type;
            var v = V(o);
            if (t == null) return "''";
            if (t.SpecialType == SpecialType.System_String) return IsNullConst(o) ? "''" : o is ILiteralOperation || o.ConstantValue.HasValue || o is IBinaryOperation || o is IInvocationOperation ? v : "(" + v + " or '')";
            if (t.SpecialType == SpecialType.System_Char) return "chr(" + v + ")";
            if (IsIntegral(t)) return "str(" + v + ")";
            if (t.SpecialType == SpecialType.System_Boolean) return "str(" + v + ")";
            return Use("cs_tostring", "C#'s ToString() of a value (double: shortest round-trip, \"R\")") + "(" + v + ")";
        }
    }

    Ex UserBinary(IMethodSymbol op, string l, string r)
    {
        var t = op.ContainingType;
        var opn = op.Name switch
        {
            "op_Equality" => "op_Eq", "op_Inequality" => "op_Ne", "op_Addition" => "op_Add", "op_Subtraction" => "op_Sub",
            "op_LessThan" => "op_Lt", "op_GreaterThan" => "op_Gt", "op_LessThanOrEqual" => "op_Le", "op_GreaterThanOrEqual" => "op_Ge",
            "op_Multiply" => "op_Mul", "op_Division" => "op_Div",
            _ => Todo("user operator " + op.Name)
        };
        if (!IsRuntime(op)) return Todo("engine operator");
        var overloads = t.GetMembers(op.Name).OfType<IMethodSymbol>().Count();
        var sfx = overloads > 1 ? "_" + string.Join("_", op.OriginalDefinition.Parameters.Select(p => ShortType(p.Type))) : "";
        var spec = Spec(t, null);
        var n = RuntimeTypeName(t) + "_" + opn + sfx + spec;
        Use(n, "operator " + op.ToDisplayString());
        return n + "(" + Strip(l) + ", " + Strip(r) + ")";
    }

    Ex Unary(IUnaryOperation u)
    {
        if (u.OperatorMethod != null) return Todo("user unary operator");
        var o = X(u.Operand);
        var t = u.Operand.Type!;
        switch (u.OperatorKind)
        {
            case UnaryOperatorKind.Minus:
                if (IsIntegral(t)) return new Ex("(-" + Paren(o.S) + ")", raw: true);
                if (o.XInt) return "(-float(" + Strip(o.S) + "))";
                if (IsSingle(t)) return "(-" + Paren(o.S) + ")";
                return "(-" + Paren(o.S) + ")";
            case UnaryOperatorKind.Plus: return o;
            case UnaryOperatorKind.BitwiseNegation:
                if (IsUnsigned(t)) return new Ex("(" + Paren(o.S) + " ^ " + MaskOf(t) + ")", raw: o.Raw);
                return new Ex("(~" + Paren(o.S) + ")", raw: o.Raw);
            case UnaryOperatorKind.Not: return "(not " + Paren(o.S) + ")";
        }
        return Todo("unary " + u.OperatorKind);
    }

    Ex Ternary(IConditionalOperation c)
    {
        var cond = Cond(c.Condition);
        var t = c.Type!;
        if (c.IsRef) return "(" + RefOf(c.WhenTrue) + " if " + cond + " else " + RefOf(c.WhenFalse!) + ").v";
        var saved = Pre; Pre = new List<string>();
        var ae = X(c.WhenTrue);
        var a = Conv(ae, c.WhenTrue.Type ?? t, t); var pa = Pre; Pre = new List<string>();
        var be = X(c.WhenFalse!);
        var bb = Conv(be, c.WhenFalse!.Type ?? t, t); var pb = Pre; Pre = saved;
        if (pa.Count > 0 || pb.Count > 0)
        {
            // a branch needs statements: an if-else before the statement fills a temporary
            var tv = Fresh("t");
            Pre.Add("if " + Strip(cond) + ":");
            foreach (var s in pa) Pre.Add("    " + s);
            Pre.Add("    " + tv + " = " + Strip(Clean(a, t)));
            Pre.Add("else:");
            foreach (var s in pb) Pre.Add("    " + s);
            Pre.Add("    " + tv + " = " + Strip(Clean(bb, t)));
            Stat("conditional with statements");
            return tv;
        }
        bool raw = a.Raw || bb.Raw;
        bool xint = a.XInt && bb.XInt;
        string sa = xint ? a.S : IsFloat(t) ? Clean(new Ex(a.S, a.Raw, a.XInt), t) : a.S;
        string sb = xint ? bb.S : IsFloat(t) ? Clean(new Ex(bb.S, bb.Raw, bb.XInt), t) : bb.S;
        return new Ex("(" + sa + " if " + cond + " else " + sb + ")", raw: raw, xint: xint);
    }

    Ex CondAccess(IConditionalAccessOperation ca)
    {
        var recv = V(ca.Operation);
        var r = recv;
        string test;
        if (Simple(recv)) test = recv;
        else { r = Fresh("ca"); test = "(" + r + " := " + Strip(recv) + ")"; }
        condAccess.Push(r);
        var saved = Pre; Pre = new List<string>();
        var inner = V(ca.WhenNotNull);
        var ip = Pre; Pre = saved;
        condAccess.Pop();
        var t = ca.Type!;
        var none = IsStructType(t) || IsNumeric(t) || t.SpecialType == SpecialType.System_Boolean ? DefaultOf(t) : "None";
        if (t is INamedTypeSymbol nt && nt.OriginalDefinition.SpecialType == SpecialType.System_Nullable_T) none = "None";
        if (ip.Count > 0)
        {
            var tv = Fresh("t");
            if (!Simple(recv)) { Pre.Add(r + " = " + Strip(recv)); }
            Pre.Add(tv + " = " + none);
            Pre.Add("if " + r + " is not None:");
            foreach (var s in ip) Pre.Add("    " + s);
            Pre.Add("    " + tv + " = " + Strip(inner));
            return tv;
        }
        return "(" + none + " if " + test + " is None else " + inner + ")";
    }

    // ---- lambdas: an expression lambda is a Python lambda; one with statements is a def written before the
    // statement. A captured variable declared in a loop body is bound per iteration by a default argument.
    Ex Lambda(IAnonymousFunctionOperation af, ITypeSymbol? target)
    {
        var m = af.Symbol;
        var sub = new PyFn(this, m);
        var ps = m.Parameters.Select(p => sub.DeclareParam(p)).ToList();
        // the loop-body variables of enclosing scopes it captures
        var binds = new List<string>();
        foreach (var r in af.Body.Descendants())
        {
            ISymbol? s = r is ILocalReferenceOperation lr ? lr.Local : null;
            if (s == null || !loopScoped.Contains(s)) continue;
            if (SymbolEqualityComparer.Default.Equals(s.ContainingSymbol, m) || IsInside(s.ContainingSymbol, m)) continue;
            if (!names.ContainsKey(s)) continue;
            var nm = names[s];
            if (!binds.Contains(nm)) binds.Add(nm);
        }
        var bindText = binds.Count > 0 ? (ps.Count > 0 ? ", " : "") + "*, " + string.Join(", ", binds.Select(b => b + "=" + b)) : "";
        if (binds.Count > 0) Stat("lambda binding loop variables");
        var ops = af.Body.Operations;
        var body = new List<string>();
        // a single return of a value (an expression lambda): a Python lambda if it needs no statements
        if (ops.Length >= 1 && ops[0] is IReturnOperation ret && ret.ReturnedValue != null)
        {
            var e = sub.retRef ? sub.RefOf(ret.ReturnedValue) : sub.ValueOf(ret.ReturnedValue, sub.retType);
            if (sub.Pre.Count == 0 && sub.globalsAssigned.Count == 0 && sub.nonlocals.Count == 0 && sub.prologue.Count == 0)
                return "(lambda" + (ps.Count > 0 || binds.Count > 0 ? " " : "") + string.Join(", ", ps) + bindText + ": " + Strip(e) + ")";
            sub.FlushTo(body, 1);
            body.Add(Ind(1) + "return " + Strip(e));
        }
        else if (m.ReturnsVoid && ops.Length >= 1 && ops[0] is IExpressionStatementOperation es0 && ops.Skip(1).All(o => o is IReturnOperation r0 && r0.ReturnedValue == null)
            && es0.Operation is IInvocationOperation or IObjectCreationOperation)
        {
            var e = sub.X(es0.Operation).S;
            if (sub.Pre.Count == 0 && sub.globalsAssigned.Count == 0 && sub.nonlocals.Count == 0 && sub.prologue.Count == 0)
            {
                Stat("void expression lambda");
                return "(lambda" + (ps.Count > 0 || binds.Count > 0 ? " " : "") + string.Join(", ", ps) + bindText + ": " + Strip(e) + ")";
            }
            sub.FlushTo(body, 1);
            body.Add(Ind(1) + Strip(e));
        }
        else sub.Block(ops, body, 1);
        var fname = Fresh("lam");
        var text = new List<string> { "def " + fname + "(" + string.Join(", ", ps) + bindText + "):" };
        var pro = sub.FuncPrologue(1);
        foreach (var l in pro.Split('\n')) if (l.Length > 0) text.Add(l);
        if (body.Count == 0) text.Add("    pass");
        text.AddRange(body);
        foreach (var l in text) Pre.Add(l);
        Stat("lambda as def");
        return fname;
    }

    Ex DelegateCreation(IDelegateCreationOperation dc)
    {
        var t = dc.Type!;
        if (dc.Target is IAnonymousFunctionOperation af) return Lambda(af, t);
        if (dc.Target is IMethodReferenceOperation mr)
        {
            var m = mr.Method;
            if (IsRuntime(m))
            {
                if (m.IsStatic)
                {
                    var overloads = m.ContainingType.GetMembers(m.Name).OfType<IMethodSymbol>().Count();
                    var sfx = overloads > 1 ? "_" + string.Join("_", m.OriginalDefinition.Parameters.Select(p => ShortType(p.Type) + (p.RefKind != RefKind.None ? "r" : ""))) : "";
                    var n = RuntimeTypeName(m.ContainingType) + "_" + m.Name + sfx + Spec(m.ContainingType, m);
                    return Use(n, "static " + m.OriginalDefinition.ToDisplayString() + " (as a function value)");
                }
                return Recv(mr.Instance!) + "." + UseM(m.Name, "method " + m.OriginalDefinition.ToDisplayString() + " (as a bound method)");
            }
            if (m.IsStatic) return StaticFuncName(m);
            return Recv(mr.Instance!) + "." + MethodIdent(m);
        }
        return Todo("delegate creation " + dc.Target.Kind);
    }
}
}
