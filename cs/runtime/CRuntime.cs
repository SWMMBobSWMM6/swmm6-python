// C runtime layer, taken from the C# port of EPA SWMM 5.2.4 (swmm5-csharp) for the SWMM6 ports.
// Reproduces glibc behaviour byte for byte where SWMM's output depends on it:
// printf-family formatting (exact decimal expansion, round-half-even like glibc), strtod/sscanf parsing,
// NUL-terminated byte strings, stdio files, and x86-64 float->int conversion.
// Written by Claude (Anthropic); see AI-USE.md.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Numerics;
using System.Text;

namespace Cpp
{
    /// <summary>C char* : a byte buffer plus an offset. Indexing reads/writes signed chars like x86 C.</summary>
    public struct CPtr : IEquatable<CPtr>
    {
        public CPtr Clone() => this;      // a value: copying it is the copy
        /// <summary>A char* made from a std::vector&lt;char&gt; or std::array&lt;char, N&gt; (sbyte storage): the same
        /// bytes, since the runtime lets an sbyte[] be read as a byte[].</summary>
        public static implicit operator CPtr(Ptr<sbyte> p) => new CPtr((byte[])(object)p.a, (int)p.o);
        public static implicit operator CPtr(Ptr<byte> p) => new CPtr(p.a, (int)p.o);
        public byte[] B;
        public int O;
        public CPtr(byte[] b, int o) { B = b; O = o; }
        public static readonly CPtr Null = default;
        public bool IsNull => B == null;
        public int this[int i]
        {
            get => (sbyte)B[O + i];
            set => B[O + i] = unchecked((byte)value);
        }
        public static CPtr operator +(CPtr p, int n) => new CPtr(p.B, p.O + n);
        public static CPtr operator +(CPtr p, long n) => new CPtr(p.B, p.O + (int)n);
        public static CPtr operator +(CPtr p, ulong n) => new CPtr(p.B, p.O + (int)n);
        public static CPtr operator -(CPtr p, int n) => new CPtr(p.B, p.O - n);
        public static CPtr operator ++(CPtr p) => new CPtr(p.B, p.O + 1);
        public static CPtr operator --(CPtr p) => new CPtr(p.B, p.O - 1);
        public static bool operator ==(CPtr a, CPtr b) => ReferenceEquals(a.B, b.B) && (a.B == null || a.O == b.O);
        public static bool operator !=(CPtr a, CPtr b) => !(a == b);
        public static bool operator <(CPtr a, CPtr b) => a.O < b.O;
        public static bool operator >(CPtr a, CPtr b) => a.O > b.O;
        public static bool operator <=(CPtr a, CPtr b) => a.O <= b.O;
        public static bool operator >=(CPtr a, CPtr b) => a.O >= b.O;
        public static long Diff(CPtr a, CPtr b) => a.O - b.O;
        public bool Equals(CPtr o) => this == o;
        public override bool Equals(object o) => o is CPtr p && this == p;
        public override int GetHashCode() => (B?.GetHashCode() ?? 0) ^ O;
        public override string ToString() => IsNull ? "(null)" : R.Str(this);
    }

    public sealed class RefI { public Action<int> Set; }
    public sealed class RefL { public Action<long> Set; }
    public sealed class RefD { public Action<double> Set; }
    public sealed class RefF { public Action<float> Set; }

    /// <summary>C FILE*</summary>
    public sealed class CFile
    {
        public Stream S;
        public bool Eof;
        public bool IsStd;
        public CFile(Stream s, bool std = false) { S = s; IsStd = std; if (!std) lock (Open) Open.Add(this); }
        /// <summary>The files fopen opened and fclose has not closed: exit() flushes them (R.FlushAll).</summary>
        public static readonly HashSet<CFile> Open = new HashSet<CFile>();
    }

    public static partial class R
    {
        public static double DummyDouble;
        public static int DummyInt;

        // ---------------------------------------------------------------- conversions (x86-64 cvttsd2si)
        public static int D2I(double x)
        {
            if (double.IsNaN(x) || x >= 2147483648.0 || x <= -2147483649.0) return int.MinValue;
            return (int)x;
        }
        public static long D2L(double x)
        {
            if (double.IsNaN(x) || x >= 9223372036854775808.0 || x < -9223372036854775808.0) return long.MinValue;
            return (long)x;
        }
        public static void Discard(object o) { }
        public static T Comma<T>(object a, T b) => b;

        // ---------------------------------------------------------------- memory
        static readonly Dictionary<string, CPtr> lits = new Dictionary<string, CPtr>();
        public static CPtr Lit(string s)
        {
            var b = new byte[s.Length + 1];
            for (int i = 0; i < s.Length; i++) b[i] = (byte)s[i];
            return new CPtr(b, 0);
        }
        /// <summary>glibc's %a of a non-negative finite double: 0x1.hhhp+e with trailing zero digits dropped
        /// (0x0p+0 for zero, 0x0.hhhp-1022 for a subnormal); with a precision, rounded to that many digits
        /// (round half to even, as glibc does in the default rounding mode).</summary>
        static string HexFloat(double a, int prec, bool alt)
        {
            long bits = BitConverter.DoubleToInt64Bits(a);
            int ex = (int)((bits >> 52) & 0x7FF);
            ulong man = (ulong)bits & 0xFFFFFFFFFFFFFUL;
            int lead = 1;
            int e;
            if (ex == 0) { lead = 0; e = man == 0 ? 0 : -1022; }
            else e = ex - 1023;
            string digits;
            if (prec < 0)
            {
                digits = man.ToString("x13").TrimEnd('0');
            }
            else
            {
                if (prec < 13)
                {
                    int drop = (13 - prec) * 4;
                    ulong keep = man >> drop, rem = man & ((1UL << drop) - 1), half = 1UL << (drop - 1);
                    if (rem > half || (rem == half && (keep & 1) == 1)) keep++;
                    if (keep >> (prec * 4) != 0) { keep &= (1UL << (prec * 4)) - 1; lead++; }
                    digits = prec == 0 ? "" : keep.ToString("x" + prec);
                }
                else digits = man.ToString("x13") + new string('0', prec - 13);
            }
            string mant = lead.ToString(CultureInfo.InvariantCulture) + (digits.Length > 0 || alt ? "." : "") + digits;
            return "0x" + mant + "p" + (e >= 0 ? "+" : "-") + Math.Abs(e).ToString(CultureInfo.InvariantCulture);
        }

        /// <summary>char a[n] = "literal": n bytes, the literal's first, the rest zero.</summary>
        public static CPtr LitN(string s, int n)
        {
            var b = new byte[Math.Max(n, s.Length + 1)];
            for (int i = 0; i < s.Length; i++) b[i] = (byte)s[i];
            return new CPtr(b, 0);
        }
        public static CPtr Buf(int n) => new CPtr(new byte[Math.Max(n, 1)], 0);
        public static CPtr Buf(long n) => Buf((int)n);
        public static CPtr Buf(ulong n) => Buf((int)n);
        public static CPtr BufFrom(CPtr lit, int n)
        {
            var p = Buf(n);
            int i = 0;
            while (i < n && lit.B[lit.O + i] != 0) { p.B[i] = lit.B[lit.O + i]; i++; }
            return p;
        }
        public static CPtr CopyBuf(CPtr p)
        {
            if (p.IsNull) return p;
            return new CPtr((byte[])p.B.Clone(), p.O);
        }
        public static CPtr[] NewBufs(int n, int m)
        {
            var a = new CPtr[n];
            for (int i = 0; i < n; i++) a[i] = Buf(m);
            return a;
        }
        public static T[] NewArray<T>(int n) where T : new()
        {
            if (n < 0) n = 0;
            var a = new T[n];
            for (int i = 0; i < n; i++) a[i] = new T();
            return a;
        }
        public static T[] NewArray<T>(long n) where T : new() => NewArray<T>((int)n);
        public static T[][] Jagged<T>(int n, int m)
        {
            var a = new T[n][];
            for (int i = 0; i < n; i++) a[i] = new T[m];
            return a;
        }
        public static T[] CloneArray<T>(T[] a)
        {
            if (a == null) return null;
            var c = new T[a.Length];
            var mi = typeof(T).GetMethod("Clone");
            for (int i = 0; i < a.Length; i++) c[i] = a[i] == null ? default : (T)mi.Invoke(a[i], null);
            return c;
        }
        public static T[][] CloneJagged<T>(T[][] a)
        {
            var c = new T[a.Length][];
            for (int i = 0; i < a.Length; i++) c[i] = (T[])a[i].Clone();
            return c;
        }
        public static T[] Realloc<T>(T[] a, int n)
        {
            var c = new T[n];
            if (a != null) Array.Copy(a, c, Math.Min(a.Length, n));
            if (typeof(T).IsClass && typeof(T).GetConstructor(Type.EmptyTypes) != null)
                for (int i = a?.Length ?? 0; i < n; i++) c[i] = (T)Activator.CreateInstance(typeof(T));
            return c;
        }
        public static void free(object o) { }
        public static void memset(double[] a, int v, ulong n)
        {
            int cnt = (int)(n / 8);
            for (int i = 0; i < cnt && i < a.Length; i++) a[i] = 0.0;
        }
        public static void memset(double[] a, int v, long n) => memset(a, v, (ulong)n);
        public static void memset(double[] a, int v, int n) => memset(a, v, (ulong)n);

        // ---------------------------------------------------------------- strings
        public static string Str(CPtr p)
        {
            var sb = new StringBuilder();
            for (int i = p.O; i < p.B.Length && p.B[i] != 0; i++) sb.Append((char)p.B[i]);
            return sb.ToString();
        }
        public static byte[] Bytes(CPtr p)
        {
            int n = (int)strlen(p);
            var b = new byte[n];
            Array.Copy(p.B, p.O, b, 0, n);
            return b;
        }
        public static ulong strlen(CPtr p)
        {
            int i = p.O;
            while (p.B[i] != 0) i++;
            return (ulong)(i - p.O);
        }
        public static CPtr strcpy(CPtr d, CPtr s)
        {
            int i = 0;
            byte c;
            if (ReferenceEquals(d.B, s.B) && d.O > s.O)
            {
                var tmp = Bytes(s);
                for (i = 0; i < tmp.Length; i++) d.B[d.O + i] = tmp[i];
                d.B[d.O + tmp.Length] = 0;
                return d;
            }
            do { c = s.B[s.O + i]; d.B[d.O + i] = c; i++; } while (c != 0);
            return d;
        }
        public static CPtr strcat(CPtr d, CPtr s)
        {
            strcpy(d + (int)strlen(d), s);
            return d;
        }
        public static int strcmp(CPtr a, CPtr b)
        {
            int i = 0;
            while (true)
            {
                int x = a.B[a.O + i], y = b.B[b.O + i];
                if (x != y) return x - y;
                if (x == 0) return 0;
                i++;
            }
        }
        public static CPtr strchr(CPtr s, int c)
        {
            byte ch = unchecked((byte)c);
            for (int i = s.O; ; i++)
            {
                if (s.B[i] == ch) return new CPtr(s.B, i);
                if (s.B[i] == 0) return CPtr.Null;
            }
        }
        public static CPtr strrchr(CPtr s, int c)
        {
            byte ch = unchecked((byte)c);
            CPtr r = CPtr.Null;
            for (int i = s.O; ; i++)
            {
                if (s.B[i] == ch) r = new CPtr(s.B, i);
                if (s.B[i] == 0) return r;
            }
        }
        public static CPtr strstr(CPtr h, CPtr n)
        {
            int nl = (int)strlen(n);
            if (nl == 0) return h;
            for (int i = h.O; h.B[i] != 0; i++)
            {
                int j = 0;
                while (j < nl && h.B[i + j] == n.B[n.O + j]) j++;
                if (j == nl) return new CPtr(h.B, i);
            }
            return CPtr.Null;
        }
        public static ulong strcspn(CPtr s, CPtr reject)
        {
            int i = 0;
            while (s.B[s.O + i] != 0)
            {
                int c = s.B[s.O + i];
                for (int j = reject.O; reject.B[j] != 0; j++) if (reject.B[j] == c) return (ulong)i;
                i++;
            }
            return (ulong)i;
        }
        static CPtr tokNext;
        public static CPtr strtok(CPtr s, CPtr delim)
        {
            if (!s.IsNull) tokNext = s;
            if (tokNext.IsNull) return CPtr.Null;
            var p = tokNext;
            // skip leading delimiters
            while (p.B[p.O] != 0 && IsIn(p.B[p.O], delim)) p = p + 1;
            if (p.B[p.O] == 0) { tokNext = CPtr.Null; return CPtr.Null; }
            var start = p;
            while (p.B[p.O] != 0 && !IsIn(p.B[p.O], delim)) p = p + 1;
            if (p.B[p.O] != 0) { p.B[p.O] = 0; tokNext = p + 1; }
            else tokNext = CPtr.Null;
            return start;
        }
        static bool IsIn(byte c, CPtr set)
        {
            for (int j = set.O; set.B[j] != 0; j++) if (set.B[j] == c) return true;
            return false;
        }
        public static int toupper(int c) => (c >= 'a' && c <= 'z') ? c - 32 : c;
        static bool IsSpace(int c) => c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r';
        static bool IsDigit(int c) => c >= '0' && c <= '9';

        // ---------------------------------------------------------------- numbers from text (glibc strtod/strtol)
        public static double strtod(CPtr s) { CPtr e = CPtr.Null; return strtod(s, ref e); }
        public static double strtod(CPtr s, ref CPtr end)
        {
            int n = ScanDouble(s.B, s.O, int.MaxValue, out double v);
            end = n == 0 ? s : new CPtr(s.B, s.O + n);
            return v;
        }
        public static double atof(CPtr s) => strtod(s);
        /// <summary>Parse a C floating number at b[o..] using at most maxw chars (after leading space). Returns chars consumed (0 = none).</summary>
        public static int ScanDouble(byte[] b, int o, int maxw, out double v, bool skipSpace = true)
        {
            v = 0.0;
            int i = o;
            if (skipSpace) while (IsSpace(b[i])) i++;
            int lim = maxw == int.MaxValue ? int.MaxValue : i + maxw;
            int st = i;
            int Get(int k) => k < lim ? b[k] : 0;
            bool neg = false;
            if (Get(i) == '+' || Get(i) == '-') { neg = Get(i) == '-'; i++; }
            // inf / nan
            if (Lower(Get(i)) == 'i' && Lower(Get(i + 1)) == 'n' && Lower(Get(i + 2)) == 'f')
            {
                i += 3;
                if (Lower(Get(i)) == 'i' && Lower(Get(i + 1)) == 'n' && Lower(Get(i + 2)) == 'i' && Lower(Get(i + 3)) == 't' && Lower(Get(i + 4)) == 'y') i += 5;
                v = neg ? double.NegativeInfinity : double.PositiveInfinity;
                return i - o;
            }
            if (Lower(Get(i)) == 'n' && Lower(Get(i + 1)) == 'a' && Lower(Get(i + 2)) == 'n')
            {
                i += 3;
                if (Get(i) == '(')
                {
                    int k = i + 1;
                    while (IsDigit(Get(k)) || (Lower(Get(k)) >= 'a' && Lower(Get(k)) <= 'z') || Get(k) == '_') k++;
                    if (Get(k) == ')') i = k + 1;
                }
                v = neg ? -double.NaN : double.NaN;
                return i - o;
            }
            if (Get(i) == '0' && Lower(Get(i + 1)) == 'x' && (IsHex(Get(i + 2)) || (Get(i + 2) == '.' && IsHex(Get(i + 3)))))
                return HexFloat(b, o, i, lim, neg, out v);
            int ms = i; int nd = 0;
            while (IsDigit(Get(i))) { i++; nd++; }
            if (Get(i) == '.') { i++; while (IsDigit(Get(i))) { i++; nd++; } }
            if (nd == 0) { v = 0.0; return 0; }
            int me = i;
            if (Lower(Get(i)) == 'e')
            {
                int k = i + 1;
                if (Get(k) == '+' || Get(k) == '-') k++;
                if (IsDigit(Get(k))) { while (IsDigit(Get(k))) k++; i = k; }
            }
            var txt = Encoding.ASCII.GetString(b, ms, i - ms);
            v = double.Parse(txt, NumberStyles.Float, CultureInfo.InvariantCulture);
            if (neg) v = -v;
            return i - o;
        }
        static bool IsHex(int c) => IsDigit(c) || (Lower(c) >= 'a' && Lower(c) <= 'f');
        static int Lower(int c) => (c >= 'A' && c <= 'Z') ? c + 32 : c;
        static int HexFloat(byte[] b, int o, int i, int lim, bool neg, out double v)
        {
            int Get(int k) => k < lim ? b[k] : 0;
            i += 2;
            BigInteger m = 0; int exp = 0; bool any = false;
            while (IsHex(Get(i))) { m = m * 16 + HexVal(Get(i)); i++; any = true; }
            if (Get(i) == '.') { i++; while (IsHex(Get(i))) { m = m * 16 + HexVal(Get(i)); exp -= 4; i++; any = true; } }
            if (!any) { v = 0; return 0; }
            if (Lower(Get(i)) == 'p')
            {
                int k = i + 1; bool en = false;
                if (Get(k) == '+' || Get(k) == '-') { en = Get(k) == '-'; k++; }
                if (IsDigit(Get(k))) { int e = 0; while (IsDigit(Get(k))) { e = Math.Min(e * 10 + Get(k) - '0', 100000); k++; } exp += en ? -e : e; i = k; }
            }
            v = BigRatioToDouble(m, exp);
            if (neg) v = -v;
            return i - o;
        }
        static int HexVal(int c) => IsDigit(c) ? c - '0' : Lower(c) - 'a' + 10;
        static double BigRatioToDouble(BigInteger m, int exp2)
        {
            if (m.IsZero) return 0.0;
            // exact: value = m * 2^exp2 ; build via decimal string of sufficient precision is overkill; use scaling
            double d = (double)m; // m may lose bits: hex floats are not used by SWMM inputs in practice
            return d * Math.Pow(2, exp2);
        }
        public static long strtol(CPtr s, ref CPtr end, int b)
        {
            int i = s.O;
            while (IsSpace(s.B[i])) i++;
            bool neg = false;
            if (s.B[i] == '+' || s.B[i] == '-') { neg = s.B[i] == '-'; i++; }
            int st = i; BigInteger v = 0;
            while (IsDigit(s.B[i])) { v = v * 10 + (s.B[i] - '0'); i++; }
            if (i == st) { end = s; return 0; }
            end = new CPtr(s.B, i);
            if (neg) v = -v;
            if (v > long.MaxValue) return long.MaxValue;
            if (v < long.MinValue) return long.MinValue;
            return (long)v;
        }
        public static int atoi(CPtr s) { CPtr e = CPtr.Null; return unchecked((int)strtol(s, ref e, 10)); }

        // ---------------------------------------------------------------- sscanf (the directives SWMM uses)
        public static RefI RefI(Action<int> a) => new RefI { Set = a };
        public static RefL RefL(Action<long> a) => new RefL { Set = a };
        public static RefD RefD(Action<double> a) => new RefD { Set = a };
        public static RefF RefF(Action<float> a) => new RefF { Set = a };

        public static int sscanf(CPtr s, CPtr fmt, params object[] args)
        {
            byte[] b = s.B; int i = s.O;
            byte[] f = fmt.B; int k = fmt.O;
            int ai = 0, count = 0;
            bool anyInput = false;
            StashReset();
            while (f[k] != 0)
            {
                int fc = f[k];
                if (IsSpace(fc)) { while (IsSpace(f[k])) k++; while (IsSpace(b[i])) i++; continue; }
                if (fc != '%')
                {
                    if (b[i] == 0) return anyInput || count > 0 ? count : -1;
                    if (b[i] != fc) return count;
                    i++; k++; continue;
                }
                k++;
                if (f[k] == '%') { while (IsSpace(b[i])) i++; if (b[i] != '%') return count; i++; k++; continue; }
                bool suppress = false;
                if (f[k] == '*') { suppress = true; k++; }
                int width = 0;
                while (IsDigit(f[k])) { width = width * 10 + f[k] - '0'; k++; }
                int lmod = 0;
                while (f[k] == 'l' || f[k] == 'h' || f[k] == 'L') { if (f[k] == 'l') lmod++; k++; }
                int conv = f[k]; k++;
                if (conv != 'c' && conv != '[' && conv != 'n') while (IsSpace(b[i])) i++;
                if (b[i] == 0 && conv != 'n') return (count == 0 && !anyInput) ? -1 : count;
                int w = width == 0 ? int.MaxValue : width;
                switch (conv)
                {
                    case 'd':
                    case 'i':
                    case 'u':
                        {
                            int j = i, lim = width == 0 ? int.MaxValue : i + width;
                            bool neg = false;
                            if (j < lim && (b[j] == '+' || b[j] == '-')) { neg = b[j] == '-'; j++; }
                            int ds = j; BigInteger v = 0;
                            while (j < lim && IsDigit(b[j])) { v = v * 10 + (b[j] - '0'); j++; }
                            if (j == ds) return count;
                            i = j; anyInput = true;
                            if (neg) v = -v;
                            if (!suppress)
                            {
                                object a = args[ai++];
                                long lv = v > long.MaxValue ? long.MaxValue : v < long.MinValue ? long.MinValue : (long)v;
                                if (a is RefL rl) rl.Set(lv);
                                else if (a is RefI ri) ri.Set(unchecked((int)lv));
                                else if (a is ScanSlot) Stash(lmod > 0 ? (object)lv : (object)unchecked((int)lv));
                                count++;
                            }
                            break;
                        }
                    case 'f':
                    case 'e':
                    case 'g':
                        {
                            int n = ScanDouble(b, i, w, out double v, false);
                            if (n == 0) return count;
                            i += n; anyInput = true;
                            if (!suppress)
                            {
                                object a = args[ai++];
                                if (a is RefD rd) rd.Set(v);
                                else if (a is RefF rf) rf.Set((float)v);
                                else if (a is ScanSlot) Stash(lmod > 0 ? (object)v : (object)(float)v);
                                count++;
                            }
                            break;
                        }
                    case 's':
                        {
                            int j = i;
                            var dst = suppress ? CPtr.Null : (CPtr)args[ai++];
                            int n = 0;
                            while (b[j] != 0 && !IsSpace(b[j]) && n < w) { if (!suppress) dst.B[dst.O + n] = b[j]; j++; n++; }
                            if (!suppress) { dst.B[dst.O + n] = 0; count++; }
                            i = j; anyInput = true;
                            break;
                        }
                    case 'c':
                        {
                            int n = width == 0 ? 1 : width;
                            if (b[i] == 0) return count == 0 && !anyInput ? -1 : count;
                            object a = suppress ? null : args[ai++];
                            for (int t = 0; t < n && b[i] != 0; t++)
                            {
                                if (!suppress)
                                {
                                    if (a is CPtr cp) cp.B[cp.O + t] = b[i];
                                    else if (a is RefI ri) ri.Set((sbyte)b[i]);
                                    else if (a is ScanSlot && t == 0) Stash((sbyte)b[i]);
                                    else if (a is ScanSlot) Stash((int)(sbyte)b[i]);
                                }
                                i++;
                            }
                            anyInput = true;
                            if (!suppress) count++;
                            break;
                        }
                    default:
                        throw new NotSupportedException("sscanf %" + (char)conv);
                }
            }
            return count;
        }

        // ---------------------------------------------------------------- printf family
        public static int fprintf(CFile f, CPtr fmt, params object[] args)
        {
            var bytes = Format(fmt, args);
            if (f == null) return -1;
            f.S.Write(bytes, 0, bytes.Length);
            return bytes.Length;
        }
        public static int snprintf(CPtr buf, ulong n, CPtr fmt, params object[] args)
        {
            var bytes = Format(fmt, args);
            if (n > 0)
            {
                int m = (int)Math.Min((ulong)bytes.Length, n - 1);
                Array.Copy(bytes, 0, buf.B, buf.O, m);
                buf.B[buf.O + m] = 0;
            }
            return bytes.Length;
        }
        public static int snprintf(CPtr buf, long n, CPtr fmt, params object[] args) => snprintf(buf, (ulong)n, fmt, args);
        public static int snprintf(CPtr buf, int n, CPtr fmt, params object[] args) => snprintf(buf, (ulong)n, fmt, args);

        public static byte[] Format(CPtr fmt, object[] args)
        {
            var o = new List<byte>(128);
            byte[] f = fmt.B; int k = fmt.O; int ai = 0;
            while (f[k] != 0)
            {
                if (f[k] != '%') { o.Add(f[k]); k++; continue; }
                k++;
                if (f[k] == '%') { o.Add((byte)'%'); k++; continue; }
                bool left = false, plus = false, space = false, alt = false, zero = false;
                for (; ; k++)
                {
                    if (f[k] == '-') left = true;
                    else if (f[k] == '+') plus = true;
                    else if (f[k] == ' ') space = true;
                    else if (f[k] == '#') alt = true;
                    else if (f[k] == '0') zero = true;
                    else break;
                }
                int width = 0, prec = -1;
                if (f[k] == '*') { width = Convert.ToInt32(args[ai++]); if (width < 0) { left = true; width = -width; } k++; }
                else while (IsDigit(f[k])) { width = width * 10 + f[k] - '0'; k++; }
                if (f[k] == '.')
                {
                    k++; prec = 0;
                    if (f[k] == '*') { prec = Convert.ToInt32(args[ai++]); if (prec < 0) prec = -1; k++; }
                    else while (IsDigit(f[k])) { prec = prec * 10 + f[k] - '0'; k++; }
                }
                while (f[k] == 'l' || f[k] == 'h' || f[k] == 'L' || f[k] == 'z' || f[k] == 'j' || f[k] == 't' || f[k] == 'q') k++;
                int conv = f[k]; k++;
                string body; string sign = "";
                bool numeric = true;
                switch (conv)
                {
                    case 'd':
                    case 'i':
                        {
                            long v = ToLong(args[ai++]);
                            BigInteger bv = v;
                            if (v < 0) { sign = "-"; bv = -bv; } else if (plus) sign = "+"; else if (space) sign = " ";
                            body = bv.ToString(CultureInfo.InvariantCulture);
                            if (prec >= 0) { zero = false; if (prec == 0 && v == 0) body = ""; body = body.PadLeft(prec, '0'); }
                            break;
                        }
                    case 'u':
                    case 'x':
                    case 'X':
                    case 'o':
                        {
                            ulong v = ToULong(args[ai++]);
                            body = conv == 'u' ? v.ToString(CultureInfo.InvariantCulture) : conv == 'o' ? Convert.ToString((long)v, 8) : v.ToString(conv == 'x' ? "x" : "X");
                            if (prec >= 0) { zero = false; if (prec == 0 && v == 0) body = ""; body = body.PadLeft(prec, '0'); }
                            break;
                        }
                    case 'c':
                        {
                            numeric = false;
                            int c = Convert.ToInt32(args[ai++]);
                            body = ((char)(byte)c).ToString();
                            break;
                        }
                    case 's':
                        {
                            numeric = false;
                            var p = (CPtr)args[ai++];
                            string s = p.IsNull ? "(null)" : Str(p);
                            if (prec >= 0 && s.Length > prec) s = s.Substring(0, prec);
                            body = s;
                            break;
                        }
                    case 'f':
                    case 'F':
                    case 'e':
                    case 'E':
                    case 'g':
                    case 'G':
                        {
                            double v = ToDouble(args[ai++]);
                            if (prec < 0) prec = 6;
                            bool negv = double.IsNegative(v);
                            if (negv) sign = "-"; else if (plus) sign = "+"; else if (space) sign = " ";
                            double a = Math.Abs(v);
                            if (double.IsNaN(v) || double.IsInfinity(v))
                            {
                                body = double.IsNaN(v) ? "nan" : "inf";
                                if (conv == 'F' || conv == 'E' || conv == 'G') body = body.ToUpperInvariant();
                                zero = false;
                            }
                            else body = FormatFloat(a, conv, prec, alt);
                            break;
                        }
                    case 'a':
                    case 'A':
                        {
                            double v = ToDouble(args[ai++]);
                            bool negv = double.IsNegative(v);
                            if (negv) sign = "-"; else if (plus) sign = "+"; else if (space) sign = " ";
                            if (double.IsNaN(v) || double.IsInfinity(v)) { body = double.IsNaN(v) ? "nan" : "inf"; zero = false; }
                            else body = HexFloat(Math.Abs(v), prec, alt);
                            if (conv == 'A') body = body.ToUpperInvariant();
                            break;
                        }
                    default:
                        throw new NotSupportedException("printf %" + (char)conv);
                }
                string full;
                int len = sign.Length + body.Length;
                if (len >= width) full = sign + body;
                else if (left) full = sign + body + new string(' ', width - len);
                else if (zero && numeric) full = sign + new string('0', width - len) + body;
                else full = new string(' ', width - len) + sign + body;
                foreach (char ch in full) o.Add((byte)ch);
            }
            return o.ToArray();
        }
        static long ToLong(object a) => a switch { int i => i, long l => l, uint u => u, ulong ul => unchecked((long)ul), double d => (long)d, _ => Convert.ToInt64(a) };
        static ulong ToULong(object a) => a switch { int i => unchecked((ulong)(long)i), long l => unchecked((ulong)l), uint u => u, ulong ul => ul, _ => Convert.ToUInt64(a) };
        static double ToDouble(object a) => a switch { double d => d, float f => f, int i => i, long l => l, _ => Convert.ToDouble(a) };

        /// <summary>exact decimal formatting of a finite non-negative double, glibc rounding (half-even on the exact value)</summary>
        public static string FormatFloat(double a, int conv, int prec, bool alt)
        {
            char lc = (char)(conv | 0x20);
            if (lc == 'f') return FixedDigits(a, prec, alt);
            if (lc == 'e') return ExpDigits(a, prec, alt, conv == 'E');
            // %g
            int P = prec == 0 ? 1 : prec;
            int X;
            if (a == 0.0) X = 0;
            else { ExpDigitsRaw(a, P - 1, out X); }
            string s;
            if (P > X && X >= -4) s = FixedDigits(a, P - 1 - X, alt);
            else s = ExpDigits(a, P - 1, alt, conv == 'G');
            if (!alt)
            {
                int epos = s.IndexOfAny(new[] { 'e', 'E' });
                string mant = epos >= 0 ? s.Substring(0, epos) : s;
                string ex = epos >= 0 ? s.Substring(epos) : "";
                if (mant.Contains('.')) { mant = mant.TrimEnd('0'); if (mant.EndsWith(".")) mant = mant.Substring(0, mant.Length - 1); }
                s = mant + ex;
            }
            return s;
        }
        static void Decompose(double a, out BigInteger m, out int e)
        {
            long bits = BitConverter.DoubleToInt64Bits(a);
            int ex = (int)((bits >> 52) & 0x7FF);
            long frac = bits & 0xFFFFFFFFFFFFFL;
            if (ex == 0) { m = frac; e = -1074; }
            else { m = frac | (1L << 52); e = ex - 1075; }
        }
        /// <summary>round_half_even(a * 10^p) for p possibly negative</summary>
        static BigInteger ScaledRound(double a, int p)
        {
            Decompose(a, out BigInteger m, out int e);
            BigInteger num = m, den = BigInteger.One;
            if (e >= 0) num <<= e; else den <<= -e;
            if (p >= 0) num *= BigInteger.Pow(10, p); else den *= BigInteger.Pow(10, -p);
            BigInteger q = BigInteger.DivRem(num, den, out BigInteger r);
            BigInteger twice = r * 2;
            int c = twice.CompareTo(den);
            if (c > 0 || (c == 0 && !q.IsEven)) q += 1;
            return q;
        }
        static string FixedDigits(double a, int prec, bool alt)
        {
            BigInteger q = ScaledRound(a, prec);
            string d = q.ToString(CultureInfo.InvariantCulture);
            if (prec == 0) return alt ? d + "." : d;
            if (d.Length <= prec) d = new string('0', prec - d.Length + 1) + d;
            return d.Substring(0, d.Length - prec) + "." + d.Substring(d.Length - prec);
        }
        static string ExpDigitsRaw(double a, int prec, out int X)
        {
            if (a == 0.0) { X = 0; return new string('0', prec + 1); }
            // estimate exponent
            int E = (int)Math.Floor(Math.Log10(a));
            for (int iter = 0; iter < 4; iter++)
            {
                BigInteger q = ScaledRound(a, prec - E);
                string d = q.ToString(CultureInfo.InvariantCulture);
                if (d.Length == prec + 1) { X = E; return d; }
                if (d.Length > prec + 1) E++; else E--;
            }
            // fallback (should not happen)
            BigInteger qq = ScaledRound(a, prec - E);
            X = E;
            return qq.ToString(CultureInfo.InvariantCulture);
        }
        static string ExpDigits(double a, int prec, bool alt, bool upper)
        {
            string d = ExpDigitsRaw(a, prec, out int X);
            string mant = d.Substring(0, 1) + (prec > 0 ? "." + d.Substring(1) : (alt ? "." : ""));
            string ex = (X < 0 ? "-" : "+") + Math.Abs(X).ToString("00", CultureInfo.InvariantCulture);
            return mant + (upper ? "E" : "e") + ex;
        }

        // ---------------------------------------------------------------- files
        public static CFile stdout_ = new CFile(Console.OpenStandardOutput(), true);
        public static CFile fopen(CPtr name, CPtr mode)
        {
            string fn = Str(name), m = Str(mode);
            try
            {
                FileStream fs;
                bool plus = m.Contains('+');
                if (m[0] == 'r') fs = new FileStream(fn, FileMode.Open, plus ? FileAccess.ReadWrite : FileAccess.Read, FileShare.ReadWrite);
                else if (m[0] == 'w') fs = new FileStream(fn, FileMode.Create, plus ? FileAccess.ReadWrite : FileAccess.Write, FileShare.ReadWrite);
                else if (m[0] == 'a') { fs = new FileStream(fn, FileMode.Append, FileAccess.Write, FileShare.ReadWrite); }
                else return null;
                return new CFile(new BufferedStream(fs, 1 << 16));
            }
            catch (Exception) { return null; }
        }
        public static int fclose(CFile f)
        {
            if (f == null) return -1;
            if (!f.IsStd) { f.S.Dispose(); lock (CFile.Open) CFile.Open.Remove(f); } else f.S.Flush();
            return 0;
        }
        /// <summary>What C's exit() does to open streams: flush every one (stdout and stderr too).</summary>
        public static void FlushAll()
        {
            CFile[] open;
            lock (CFile.Open) open = new List<CFile>(CFile.Open).ToArray();
            foreach (var f in open) { try { f.S.Flush(); } catch (Exception) { } }
            try { stdout_.S.Flush(); } catch (Exception) { }
            try { stderr_.S.Flush(); } catch (Exception) { }
        }
        public static int fflush(CFile f) { f?.S.Flush(); return 0; }
        public static int feof(CFile f) => f.Eof ? 1 : 0;
        public static void rewind(CFile f) { f.S.Seek(0, SeekOrigin.Begin); f.Eof = false; }
        public static int fseek(CFile f, long off, int whence)
        {
            try
            {
                f.S.Seek(off, whence == 0 ? SeekOrigin.Begin : whence == 1 ? SeekOrigin.Current : SeekOrigin.End);
                f.Eof = false;
                return 0;
            }
            catch (Exception) { return -1; }
        }
        public static int fseek(CFile f, int off, int whence) => fseek(f, (long)off, whence);
        public static long ftell(CFile f) => f.S.Position;
        public static CPtr fgets(CPtr buf, int n, CFile f)
        {
            int i = 0;
            while (i < n - 1)
            {
                int c = f.S.ReadByte();
                if (c < 0) { f.Eof = true; break; }
                buf.B[buf.O + i++] = (byte)c;
                if (c == '\n') break;
            }
            if (i == 0) return CPtr.Null;
            buf.B[buf.O + i] = 0;
            return buf;
        }
        public static int remove(CPtr name)
        {
            try { File.Delete(Str(name)); return 0; } catch (Exception) { return -1; }
        }
        static int tmpCounter;
        public static int mkstemp(CPtr template)
        {
            int n = (int)strlen(template);
            var rnd = new Random(Environment.ProcessId ^ Environment.TickCount ^ (tmpCounter++ * 7919));
            const string chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
            for (int attempt = 0; attempt < 1000; attempt++)
            {
                for (int i = n - 6; i < n; i++) template.B[template.O + i] = (byte)chars[rnd.Next(chars.Length)];
                string fn = Str(template);
                try { using (new FileStream(fn, FileMode.CreateNew)) { } return 3; } catch (IOException) { }
            }
            return -1;
        }

        static byte[] tmp8 = new byte[8];
        static bool ReadExact(CFile f, byte[] b, int n)
        {
            int got = 0;
            while (got < n)
            {
                int r = f.S.Read(b, got, n - got);
                if (r <= 0) { f.Eof = true; return false; }
                got += r;
            }
            return true;
        }
        // fwrite/fread of typed values (little-endian like x86)
        public static ulong fwriteF1(float v, CFile f) { f.S.Write(BitConverter.GetBytes(v), 0, 4); return 1; }
        public static ulong fwriteD1(double v, CFile f) { f.S.Write(BitConverter.GetBytes(v), 0, 8); return 1; }
        public static ulong fwriteI1(int v, CFile f) { f.S.Write(BitConverter.GetBytes(v), 0, 4); return 1; }
        public static ulong fwriteL1(long v, CFile f) { f.S.Write(BitConverter.GetBytes(v), 0, 8); return 1; }
        public static ulong fwriteF(float[] a, int off, int n, CFile f) { for (int i = 0; i < n; i++) f.S.Write(BitConverter.GetBytes(a[off + i]), 0, 4); return (ulong)n; }
        public static ulong fwriteD(double[] a, int off, int n, CFile f) { for (int i = 0; i < n; i++) f.S.Write(BitConverter.GetBytes(a[off + i]), 0, 8); return (ulong)n; }
        public static ulong fwriteI(int[] a, int off, int n, CFile f) { for (int i = 0; i < n; i++) f.S.Write(BitConverter.GetBytes(a[off + i]), 0, 4); return (ulong)n; }
        public static ulong fwriteC(CPtr p, int off, int n, CFile f) { f.S.Write(p.B, p.O + off, n); return (ulong)n; }
        public static ulong freadF1(ref float v, CFile f) { if (!ReadExact(f, tmp8, 4)) return 0; v = BitConverter.ToSingle(tmp8, 0); return 1; }
        public static ulong freadD1(ref double v, CFile f) { if (!ReadExact(f, tmp8, 8)) return 0; v = BitConverter.ToDouble(tmp8, 0); return 1; }
        public static ulong freadI1(ref int v, CFile f) { if (!ReadExact(f, tmp8, 4)) return 0; v = BitConverter.ToInt32(tmp8, 0); return 1; }
        public static ulong freadL1(ref long v, CFile f) { if (!ReadExact(f, tmp8, 8)) return 0; v = BitConverter.ToInt64(tmp8, 0); return 1; }
        public static ulong freadF(float[] a, int off, int n, CFile f) { for (int i = 0; i < n; i++) { if (!ReadExact(f, tmp8, 4)) return (ulong)i; a[off + i] = BitConverter.ToSingle(tmp8, 0); } return (ulong)n; }
        public static ulong freadD(double[] a, int off, int n, CFile f) { for (int i = 0; i < n; i++) { if (!ReadExact(f, tmp8, 8)) return (ulong)i; a[off + i] = BitConverter.ToDouble(tmp8, 0); } return (ulong)n; }
        public static ulong freadI(int[] a, int off, int n, CFile f) { for (int i = 0; i < n; i++) { if (!ReadExact(f, tmp8, 4)) return (ulong)i; a[off + i] = BitConverter.ToInt32(tmp8, 0); } return (ulong)n; }
        public static ulong freadC(CPtr p, int off, int n, CFile f)
        {
            int got = 0;
            while (got < n) { int r = f.S.Read(p.B, p.O + off + got, n - got); if (r <= 0) { f.Eof = true; break; } got += r; }
            return (ulong)got;
        }

        // ---------------------------------------------------------------- time
        public static long time() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        public static long time(Ptr<long> p) { long t = time(); if (!p.IsNull) p[0] = t; return t; }
        public static CPtr ctime(Ptr<long> p) => ctime(p[0]);
        public static double difftime(long end, long start) => (double)(end - start);
        /// <summary>char a[n][m]: n zeroed rows of m bytes.</summary>
        public static CPtr[] Bufs(int n, int m) { var r = new CPtr[n]; for (int i = 0; i < n; i++) r[i] = Buf(m); return r; }
        public static CPtr ctime(long t)
        {
            var d = DateTimeOffset.FromUnixTimeSeconds(t).ToLocalTime();
            string s = d.ToString("ddd MMM ", CultureInfo.InvariantCulture) + d.Day.ToString().PadLeft(2) + d.ToString(" HH:mm:ss yyyy", CultureInfo.InvariantCulture) + "\n";
            return Lit(s);
        }
    }
}
