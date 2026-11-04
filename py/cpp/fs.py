# fs.py - part D of the Python runtime: a port of the FsPath, Fs and FileStatus classes of cs/runtime/Fs.cs:
# std::filesystem::path with POSIX paths, as libstdc++ on Linux, and the std::filesystem functions the engine calls,
# over .NET's file system calls as the C# port makes them (fsnet.py reproduces those): canonical and weakly_canonical
# are Path.GetFullPath (lexical, links left alone), equivalent compares the two full paths, the error-code overloads
# set ENOENT (2), EACCES (13), EEXIST (17) or EISDIR (21) as the C# port does, and the plain overloads let .NET's
# exceptions through (as SysException). The C library functions of Fs.cs are in rfs.py.
# FsPath.p holds the path's bytes (a Str); the element functions work on the bytes as the C# port's do on its Latin-1
# text (filename is the part after the last '/', "" when the path ends in '/'; operator/ adds a separator unless the
# right side is absolute or empty).
# Names follow py/RUNTIME_HELPERS.md (Join(FsPath) is Join_FsPath, Fs.exists(FsPath, ErrorCode) is
# Fs_exists_FsPath_ErrorCode).
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
from . import fsnet as _net
from . import rfs as _rt

__all__ = ['FsPath', 'NewFsPath', 'NewFsPath_Str', 'NewFsPath_CPtr', 'NewFsPath_str', 'FileStatus_NotFound',
           'FileStatus_Regular', 'FileStatus_Directory', 'FileStatus_Other', 'FileStatus', 'NewFileStatus',
           'FileStatus_Of', 'Fs_exists_FsPath', 'Fs_exists_FsPath_ErrorCode', 'Fs_is_regular_file_FsPath',
           'Fs_is_directory_FsPath', 'Fs_file_size_FsPath', 'Fs_file_size_FsPath_ErrorCode', 'Fs_remove_FsPath',
           'Fs_create_directories_FsPath', 'Fs_current_path', 'Fs_absolute', 'Fs_canonical_FsPath',
           'Fs_weakly_canonical_FsPath', 'Fs_temp_directory_path', 'Fs_rename_FsPath_FsPath',
           'Fs_equivalent_FsPath_FsPath', 'Fs_equivalent_FsPath_FsPath_ErrorCode', 'Fs_remove_FsPath_ErrorCode',
           'Fs_rename_FsPath_FsPath_ErrorCode', 'Fs_create_hard_link', 'Fs_weakly_canonical_FsPath_ErrorCode',
           'Fs_canonical_FsPath_ErrorCode', 'Fs_status_FsPath', 'Fs_status_FsPath_ErrorCode',
           'Fs_symlink_status_FsPath', 'Fs_symlink_status_FsPath_ErrorCode', 'Fs_exists_FileStatus',
           'Fs_is_regular_file_FileStatus', 'Fs_is_directory_FileStatus', 'Fs_is_symlink',
           'Fs_create_directories_FsPath_ErrorCode', 'File_Exists', 'Fs']

try:
    from . import ICopy as _ICopy      # part A's interface class (imported earlier by the package)
except ImportError:                    # only when this module is used on its own
    class _ICopy(object):
        __slots__ = ()


def _b(p):
    """The bytes of a path."""
    s = p.p
    return bytes(s.b[:s.n])


def _mk(data):
    """A path of the given bytes."""
    f = FsPath()
    f.p = _rt.new_str(data)
    return f


class FsPath(_ICopy):
    """std::filesystem::path (Cpp.FsPath). FsPath() is the raw object; the constructors are NewFsPath*."""
    __slots__ = ('p',)

    def __init__(self):
        self.p = None

    def Clone(self):
        f = FsPath()
        f.p = self.p.Clone()
        return f

    def clear(self):
        self.p = _rt.new_str(b'')

    def Assign_FsPath(self, o):
        self.p = o.p.Clone()

    Assign = Assign_FsPath

    def Assign_Str(self, s):
        self.p = s.Clone()

    def MoveFrom(self, o):
        self.p = o.p
        o.p = _rt.new_str(b'')

    def CompareTo(self, o):
        return _compare(self.p, o.p)

    def compare(self, o):
        return _compare(self.p, o.p)

    def ToString(self):
        return _net.latin1_str(_b(self))

    def string(self):
        return self.p.Clone()

    def generic_string(self):
        return self.p.Clone()

    def native(self):
        return self.p.Clone()

    def c_str(self):
        return self.p.c_str()

    def u8string(self):
        return self.p.Clone()

    def empty(self):
        return self.p.n == 0

    def is_absolute(self):
        s = self.p
        return s.n > 0 and s.b[0] == 47

    def is_relative(self):
        return not self.is_absolute()

    def has_filename(self):
        return self.filename().p.n > 0

    def has_parent_path(self):
        return self.parent_path().p.n > 0

    def has_extension(self):
        return self.extension().p.n > 0

    def filename(self):
        """The last element (libstdc++: the part after the last '/'; "" when the path ends in '/')."""
        s = _b(self)
        return _mk(s[s.rfind(b'/') + 1:])

    def parent_path(self):
        s = _b(self)
        i = s.rfind(b'/')
        if i < 0:
            return _mk(b'')
        if i == 0:
            return _mk(b'/')
        j = i
        while j > 0 and s[j - 1] == 47:
            j -= 1
        return _mk(s[:1 if j == 0 else j])

    def stem(self):
        f = _b(self.filename())
        if f == b'.' or f == b'..':
            return _mk(f)
        i = f.rfind(b'.')
        return _mk(f if i <= 0 else f[:i])

    def extension(self):
        f = _b(self.filename())
        if f == b'.' or f == b'..':
            return _mk(b'')
        i = f.rfind(b'.')
        return _mk(b'' if i <= 0 else f[i:])

    def replace_extension_FsPath(self, ext=None):
        """replace_extension(ext): the extension removed, then ext added (with a '.' in front unless it has one); a
        null or empty ext only removes."""
        e = _b(self.extension())
        a = _b(self)
        s = a[:len(a) - len(e)]
        if ext is not None and ext.p.n > 0:
            s += (b'' if ext.p.b[0] == 46 else b'.') + _b(ext)
        self.p = _rt.new_str(s)
        return self

    def replace_extension_Str(self, ext):
        return self.replace_extension_FsPath(NewFsPath_Str(ext))

    def replace_filename(self, f):
        par = self.parent_path()
        self.p = par.Join_FsPath(f).p
        return self

    def remove_filename(self):
        s = _b(self)
        i = s.rfind(b'/')
        self.p = _rt.new_str(b'' if i < 0 else s[:i + 1])
        return self

    def Join_FsPath(self, o):
        """operator/ : an absolute right side replaces the path; otherwise a separator is added."""
        if o.is_absolute():
            return o.Clone()
        s = _b(self)
        if not s:
            return o.Clone()
        if not s.endswith(b'/') and o.p.n > 0:
            s += b'/'
        return _mk(s + _b(o))

    def Join_Str(self, o):
        return self.Join_FsPath(NewFsPath_Str(o))

    def Join_CPtr(self, o):
        return self.Join_FsPath(NewFsPath_CPtr(o))

    def lexically_normal(self):
        """Empty and "." elements dropped, ".." taking the element before it away (dropped at the root), a trailing
        '/' kept, "." for an empty result."""
        s = _b(self)
        if not s:
            return _mk(b'')
        ab = s.startswith(b'/')
        parts = []
        for x in s.split(b'/'):
            if x == b'' or x == b'.':
                continue
            if x == b'..' and parts and parts[-1] != b'..':
                parts.pop()
                continue
            if x == b'..' and ab:
                continue
            parts.append(x)
        r = (b'/' if ab else b'') + b'/'.join(parts)
        if s.endswith(b'/') and parts:
            r += b'/'
        if not r:
            r = b'.'
        return _mk(r)


def _compare(a, b):
    """path::compare as the C# port has it: Str.compare (unsigned bytes, then length)."""
    x = bytes(a.b[:a.n])
    y = bytes(b.b[:b.n])
    m = min(len(x), len(y))
    if x[:m] != y[:m]:
        for i in range(m):
            if x[i] != y[i]:
                return -1 if x[i] < y[i] else 1
    return len(x) - len(y)


def NewFsPath():
    f = FsPath()
    f.p = _rt.new_str(b'')
    return f


def NewFsPath_Str(s):
    f = FsPath()
    f.p = s.Clone()
    return f


def NewFsPath_CPtr(s):
    f = FsPath()
    f.p = _rt.new_str(_rt.cbytes(s))
    return f


def NewFsPath_str(s):
    """new FsPath(string): the Latin-1 bytes of the text."""
    return _mk(_net.latin1_bytes(s))


# ------------------------------------------------------------------------------------------------- FileStatus

FileStatus_NotFound, FileStatus_Regular, FileStatus_Directory, FileStatus_Other = -1, 1, 2, 3


class FileStatus(object):
    """std::filesystem::file_status as far as the engine asks: not found, a regular file or a directory
    (File.Exists, then Directory.Exists, as .NET sees them: links followed)."""
    __slots__ = ('kind',)
    NotFound, Regular, Directory, Other = -1, 1, 2, 3

    def __init__(self):
        self.kind = 0

    def type(self):
        return self.kind

    def Clone(self):
        r = FileStatus()
        r.kind = self.kind
        return r


NewFileStatus = FileStatus


def FileStatus_Of(s):
    r = FileStatus()
    r.kind = 1 if _net.file_exists(s) else (2 if _net.dir_exists(s) else -1)
    return r


# ------------------------------------------------------------------------------------------------- Cpp.Fs

def _s(p):
    return _net.latin1_str(_b(p))


def Fs_exists_FsPath(p):
    s = _s(p)
    return _net.file_exists(s) or _net.dir_exists(s)


def Fs_exists_FsPath_ErrorCode(p, ec):
    return Fs_exists_FsPath(p)


def Fs_is_regular_file_FsPath(p):
    return _net.file_exists(_s(p))


def Fs_is_directory_FsPath(p):
    return _net.dir_exists(_s(p))


def Fs_file_size_FsPath(p):
    """new FileInfo(p).Length: a missing file or a directory throws (FileNotFoundException)."""
    return _net.file_length(_s(p))


def Fs_file_size_FsPath_ErrorCode(p, ec):
    """file_size(p, ec): libstdc++ sets ENOENT for a missing file, EISDIR for a directory and returns
    static_cast<uintmax_t>(-1) on error."""
    s = _s(p)
    if _net.file_exists(s):
        ec.v = 0
        return _net.file_length(s)
    ec.v = 21 if _net.dir_exists(s) else 2
    return 0xFFFFFFFFFFFFFFFF


def Fs_remove_FsPath(p):
    """Removes a file (not a directory); reports whether one was removed."""
    s = _s(p)
    try:
        if _net.file_exists(s):
            _net.delete_file(_net.full_path(s))
            return True
    except Exception:
        pass
    return False


def Fs_create_directories_FsPath(p):
    """Directory.CreateDirectory: true unless it fails (an existing directory is fine)."""
    try:
        _net.create_directories(_s(p))
        return True
    except Exception:
        return False


def Fs_current_path():
    return NewFsPath_str(_net.cwd())


def Fs_absolute(p):
    return p.Clone() if p.is_absolute() else Fs_current_path().Join_FsPath(p)


def Fs_canonical_FsPath(p):
    return NewFsPath_str(_net.full_path(_s(p)))


def Fs_weakly_canonical_FsPath(p):
    return NewFsPath_str(_net.full_path(_s(p)))


def Fs_temp_directory_path():
    """$TMPDIR, or /tmp when it is not set."""
    t = _rt.net_environ().get('TMPDIR')
    return NewFsPath_str(t if t is not None else '/tmp')


def Fs_rename_FsPath_FsPath(a, b):
    """File.Move(a, b, true), which throws when it fails."""
    _net.move_file(_s(a), _s(b))


def Fs_equivalent_FsPath_FsPath(a, b):
    return _net.full_path(_s(a)) == _net.full_path(_s(b))


def Fs_equivalent_FsPath_FsPath_ErrorCode(a, b, ec):
    if not Fs_exists_FsPath(a) or not Fs_exists_FsPath(b):
        ec.v = 2
        return False
    ec.v = 0
    return Fs_equivalent_FsPath_FsPath(a, b)


def Fs_remove_FsPath_ErrorCode(p, ec):
    """remove(p, ec): a file or an empty directory (EACCES = 13 when that fails)."""
    ec.v = 0
    s = _s(p)
    try:
        if _net.file_exists(s):
            _net.delete_file(_net.full_path(s))
            return True
        if _net.dir_exists(s):
            _net.delete_dir(_net.full_path(s))
            return True
    except Exception:
        ec.v = 13
    return False


def Fs_rename_FsPath_FsPath_ErrorCode(a, b, ec):
    """rename(a, b, ec): File.Move(a, b, true); FileNotFoundException gives ENOENT (2), anything else EACCES (13)."""
    try:
        fa = _net.full_path(_s(a))
        fb = _net.full_path(_s(b))
    except Exception:
        ec.v = 13
        return
    if not _net.fs_file_exists(fa):
        ec.v = 2
        return
    try:
        _net.move_full(fa, fb)
        ec.v = 0
    except FileNotFoundError:
        ec.v = 2 if _net.parent_exists(fb) else 13
    except Exception:
        ec.v = 13


def Fs_create_hard_link(target, link, ec):
    """The C# port's create_hard_link(target, link, ec): EEXIST (17) when link exists, otherwise a copy of target at
    link (ENOENT when that fails)."""
    if _net.file_exists(_s(link)):
        ec.v = 17
        return
    try:
        _net.copy_file(_s(target), _s(link))
        ec.v = 0
    except Exception:
        ec.v = 2


def Fs_weakly_canonical_FsPath_ErrorCode(p, ec):
    ec.v = 0
    return Fs_weakly_canonical_FsPath(p)


def Fs_canonical_FsPath_ErrorCode(p, ec):
    if not Fs_exists_FsPath(p):
        ec.v = 2
        return NewFsPath()
    ec.v = 0
    return Fs_canonical_FsPath(p)


def Fs_status_FsPath(p):
    return FileStatus_Of(_s(p))


def Fs_status_FsPath_ErrorCode(p, ec):
    ec.v = 0
    return FileStatus_Of(_s(p))


def Fs_symlink_status_FsPath(p):
    return FileStatus_Of(_s(p))


def Fs_symlink_status_FsPath_ErrorCode(p, ec):
    ec.v = 0
    return FileStatus_Of(_s(p))


def Fs_exists_FileStatus(s):
    return s.kind != -1


def Fs_is_regular_file_FileStatus(s):
    return s.kind == 1


def Fs_is_directory_FileStatus(s):
    return s.kind == 2


def Fs_is_symlink(s):
    return False


def Fs_create_directories_FsPath_ErrorCode(p, ec):
    ec.v = 0
    return Fs_create_directories_FsPath(p)


def File_Exists(path):
    """System.IO.File.Exists(string)."""
    return _net.file_exists(path)


class Fs(object):
    """Cpp.Fs as a namespace."""
    __slots__ = ()
    exists_FsPath = staticmethod(Fs_exists_FsPath)
    exists_FsPath_ErrorCode = staticmethod(Fs_exists_FsPath_ErrorCode)
    exists_FileStatus = staticmethod(Fs_exists_FileStatus)
    is_regular_file_FsPath = staticmethod(Fs_is_regular_file_FsPath)
    is_regular_file_FileStatus = staticmethod(Fs_is_regular_file_FileStatus)
    is_directory_FsPath = staticmethod(Fs_is_directory_FsPath)
    is_directory_FileStatus = staticmethod(Fs_is_directory_FileStatus)
    is_symlink = staticmethod(Fs_is_symlink)
    file_size_FsPath = staticmethod(Fs_file_size_FsPath)
    file_size_FsPath_ErrorCode = staticmethod(Fs_file_size_FsPath_ErrorCode)
    remove_FsPath = staticmethod(Fs_remove_FsPath)
    remove_FsPath_ErrorCode = staticmethod(Fs_remove_FsPath_ErrorCode)
    create_directories_FsPath = staticmethod(Fs_create_directories_FsPath)
    create_directories_FsPath_ErrorCode = staticmethod(Fs_create_directories_FsPath_ErrorCode)
    current_path = staticmethod(Fs_current_path)
    absolute = staticmethod(Fs_absolute)
    canonical_FsPath = staticmethod(Fs_canonical_FsPath)
    canonical_FsPath_ErrorCode = staticmethod(Fs_canonical_FsPath_ErrorCode)
    weakly_canonical_FsPath = staticmethod(Fs_weakly_canonical_FsPath)
    weakly_canonical_FsPath_ErrorCode = staticmethod(Fs_weakly_canonical_FsPath_ErrorCode)
    temp_directory_path = staticmethod(Fs_temp_directory_path)
    rename_FsPath_FsPath = staticmethod(Fs_rename_FsPath_FsPath)
    rename_FsPath_FsPath_ErrorCode = staticmethod(Fs_rename_FsPath_FsPath_ErrorCode)
    equivalent_FsPath_FsPath = staticmethod(Fs_equivalent_FsPath_FsPath)
    equivalent_FsPath_FsPath_ErrorCode = staticmethod(Fs_equivalent_FsPath_FsPath_ErrorCode)
    create_hard_link = staticmethod(Fs_create_hard_link)
    status_FsPath = staticmethod(Fs_status_FsPath)
    status_FsPath_ErrorCode = staticmethod(Fs_status_FsPath_ErrorCode)
    symlink_status_FsPath = staticmethod(Fs_symlink_status_FsPath)
    symlink_status_FsPath_ErrorCode = staticmethod(Fs_symlink_status_FsPath_ErrorCode)
