# fsnet.py - part D of the Python runtime: the parts of .NET's System.IO that cs/runtime/Streams.cs and Fs.cs rely
# on, reproduced for the Python port of those files (the Java port's FsNet.java is the model):
#   Path.GetFullPath on Unix (lexical: "." and ".." and doubled separators removed against the current directory,
#   symbolic links left alone; ArgumentException for an empty path or a NUL), File.Exists / Directory.Exists (stat,
#   then lstat for a broken link; a full path ending in '/' is never a file), File.Move (rename(2), or copy and delete
#   across file systems) / File.Copy / File.Delete / Directory.Delete / Directory.CreateDirectory, FileInfo.Length,
#   the FileStream constructor's open (a directory refused), a FileStream's write buffer (BufferedFileStreamStrategy:
#   fill the buffer, flush it, write a request of a whole buffer or more directly) and its position, which .NET keeps
#   itself and reads and writes at with pread / pwrite (OSFileStreamStrategy), and a MemoryStream.
# Every path is made full first, as .NET does, so "a/../b" names "b" even when "a" does not exist. Paths reach the
# OS as the UTF-8 of the .NET string (the Latin-1 text of the C++ bytes), as .NET marshals them.
# A .NET exception that the C# lets through is raised as the runtime's SysException (kind, message), resolved when
# first needed (no other part's name is used while this module imports).
# Written by Claude (Anthropic) for the SWMM6 ports; see AI-USE.md.
import os
import stat as _stat

__all__ = []      # internal to part D

SEEK_BEFORE_BEGIN = "An attempt was made to move the position before the beginning of the stream."
SEEK_APPEND = "Unable seek backward to overwrite data that previously existed in a file opened in Append mode."

_SysException = None


def sys_exc(kind, msg):
    """A .NET exception as the runtime's SysException (part A's), or a plain stand-in before it exists."""
    global _SysException
    if _SysException is None:
        try:
            from . import SysException as e    # the package exports every runtime name
            _SysException = e
        except ImportError:
            class _Stand(Exception):
                def __init__(self, kind, message):
                    Exception.__init__(self, message)
                    self.kind = kind
                    self.message = message
            _SysException = _Stand
    return _SysException(kind, msg)


def io_exc(e):
    """An OSError as .NET's IOException."""
    if isinstance(e, OSError):
        return sys_exc('IO.IOException', e.strerror or str(e))
    return sys_exc('IO.IOException', str(e))


# ------------------------------------------------------------------------------------------- text and bytes

def latin1_str(b):
    """The .NET string of C++ bytes (Encoding.Latin1.GetString, or C#'s Str.ToString())."""
    return bytes(b).decode('latin-1')


def latin1_bytes(s):
    """Encoding.Latin1.GetBytes: a char above U+00FF becomes '?' (the encoder's replacement fallback)."""
    try:
        return s.encode('latin-1')
    except UnicodeEncodeError:
        return s.encode('latin-1', 'replace')


def os_path(full):
    """A .NET path string as the bytes the OS sees (UTF-8, as .NET marshals it)."""
    return full.encode('utf-8', 'surrogatepass')


def net_str(b):
    """A string .NET reads from the OS (getcwd, environ): UTF-8, an invalid sequence replaced by U+FFFD."""
    return b.decode('utf-8', 'replace')


# ------------------------------------------------------------------------------------------- Path.GetFullPath

def cwd():
    """Directory.GetCurrentDirectory: getcwd(3) as a .NET string."""
    return net_str(os.getcwdb())


def full_path(path):
    """Path.GetFullPath on Unix: ArgumentException for an empty path or a NUL; a relative path is put under the
    current directory (Path.Combine), then PathInternal.RemoveRelativeSegments collapses it."""
    if not path:
        raise sys_exc('ArgumentException', "The value cannot be an empty string. (Parameter 'path')")
    if '\0' in path:
        raise sys_exc('ArgumentException', "Null character in path. (Parameter 'path')")
    if path[0] != '/':
        c = cwd()
        path = c + path if c.endswith('/') else c + '/' + path
    if '//' not in path and '/.' not in path:
        return path                     # nothing to collapse (the common case)
    r = remove_relative_segments(path, 1)
    return r if r else '/'


def remove_relative_segments(path, root_length):
    """PathInternal.RemoveRelativeSegments (dotnet/runtime) for '/' separators."""
    skip = root_length
    if path[skip - 1] == '/':
        skip -= 1
    sb = list(path[:skip]) if skip > 0 else []
    n = len(path)
    i = skip
    while i < n:
        c = path[i]
        if c == '/' and i + 1 < n:
            if path[i + 1] == '/':
                i += 1
                continue
            if (i + 2 == n or path[i + 2] == '/') and path[i + 1] == '.':
                i += 2
                continue
            if i + 2 < n and (i + 3 == n or path[i + 3] == '/') and path[i + 1] == '.' and path[i + 2] == '.':
                s = len(sb) - 1
                while s >= skip:
                    if sb[s] == '/':
                        del sb[(s + 1 if (i + 3 >= n and s == skip) else s):]
                        break
                    s -= 1
                if s < skip:
                    del sb[skip:]
                i += 3
                continue
        sb.append(c)
        i += 1
    if skip != root_length and len(sb) < root_length:
        sb.append(path[root_length - 1])
    return ''.join(sb)


# ------------------------------------------------------------------------------------------- existence

def stat(full):
    """stat(2), or lstat(2) when stat fails (a broken link): None when nothing is there."""
    p = os_path(full)
    try:
        return os.stat(p)
    except (OSError, ValueError):
        try:
            return os.lstat(p)
        except (OSError, ValueError):
            return None


def fs_file_exists(full):
    """FileSystem.FileExists of a full path: something that is not a directory."""
    if len(full) > 1 and full.endswith('/'):
        return False
    st = stat(full)
    return st is not None and not _stat.S_ISDIR(st.st_mode)


def fs_dir_exists(full):
    st = stat(full)
    return st is not None and _stat.S_ISDIR(st.st_mode)


def file_exists(path):
    """System.IO.File.Exists."""
    if not path:
        return False
    try:
        f = full_path(path)
    except Exception:
        return False
    if f.endswith('/'):
        return False
    return fs_file_exists(f)


def dir_exists(path):
    """System.IO.Directory.Exists."""
    if not path:
        return False
    try:
        f = full_path(path)
    except Exception:
        return False
    return fs_dir_exists(f)


# ------------------------------------------------------------------------------------------- file operations

def parent_exists(full):
    """Whether the directory of a full path exists (.NET tells a missing directory from a missing file)."""
    k = full.rfind('/')
    return fs_dir_exists('/' if k <= 0 else full[:k])


def move_full(fa, fb):
    """FileSystem.MoveFile(fa, fb, overwrite: true) of two full paths: a directory destination is refused
    (IOException); rename(2), or copy and delete across file systems (EXDEV). Raises OSError."""
    if fs_dir_exists(fb):
        raise IsADirectoryError(21, 'Is a directory')
    try:
        os.rename(os_path(fa), os_path(fb))
    except OSError as e:
        if e.errno != 18:     # EXDEV
            raise
        copy_full(fa, fb, True)
        os.unlink(os_path(fa))


def move_file(a, b):
    """File.Move(a, b, overwrite: true): only a file moves. Raises SysException: FileNotFoundException for a missing
    source (or a missing name in an existing directory), DirectoryNotFoundException for a missing destination
    directory, IOException otherwise."""
    fa, fb = full_path(a), full_path(b)
    if not fs_file_exists(fa):
        raise sys_exc('IO.FileNotFoundException', "Could not find file '" + fa + "'.")
    try:
        move_full(fa, fb)
    except FileNotFoundError:
        if not parent_exists(fb):
            raise sys_exc('IO.DirectoryNotFoundException', "Could not find a part of the path '" + fb + "'.")
        raise sys_exc('IO.FileNotFoundException', "Could not find file '" + fb + "'.")
    except OSError as e:
        raise io_exc(e)


def copy_full(fa, fb, overwrite):
    """FileSystem.CopyFile: the bytes and the permission bits; a directory source is refused. Raises OSError."""
    sfd = os.open(os_path(fa), os.O_RDONLY | os.O_CLOEXEC)
    try:
        st = os.fstat(sfd)
        if _stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(21, 'Is a directory')
        flags = os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | (os.O_TRUNC if overwrite else os.O_EXCL)
        dfd = os.open(os_path(fb), flags, st.st_mode & 0o7777)
        try:
            while True:
                chunk = os.read(sfd, 1 << 20)
                if not chunk:
                    break
                mv = memoryview(chunk)
                while mv:
                    k = os.write(dfd, mv)
                    mv = mv[k:]
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def copy_file(a, b):
    """File.Copy(a, b) without overwrite."""
    fa, fb = full_path(a), full_path(b)
    try:
        copy_full(fa, fb, False)
    except IsADirectoryError:
        raise sys_exc('UnauthorizedAccessException', "Access to the path '" + fa + "' is denied.")
    except OSError as e:
        raise io_exc(e)


def delete_file(full):
    """File.Delete of a full path (a missing file is not an error)."""
    try:
        os.unlink(os_path(full))
    except FileNotFoundError:
        pass
    except OSError as e:
        raise io_exc(e)


def delete_dir(full):
    """Directory.Delete(path) (not recursive): rmdir(2)."""
    try:
        os.rmdir(os_path(full))
    except OSError as e:
        raise io_exc(e)


def create_directories(path):
    """Directory.CreateDirectory: the directory and its missing parents; an existing directory is fine."""
    f = full_path(path)
    try:
        os.makedirs(os_path(f), exist_ok=True)
    except OSError as e:
        raise io_exc(e)


def file_length(path):
    """new FileInfo(path).Length: FileNotFoundException for a missing path or a directory."""
    f = full_path(path)
    st = None if (len(f) > 1 and f.endswith('/')) else stat(f)
    if st is None or _stat.S_ISDIR(st.st_mode):
        raise sys_exc('IO.FileNotFoundException', "Could not find file '" + f + "'.")
    return st.st_size


# FileMode / FileAccess combinations the streams open with
READ = os.O_RDONLY
CREATE = os.O_WRONLY | os.O_CREAT | os.O_TRUNC           # FileMode.Create, FileAccess.Write
APPEND = os.O_WRONLY | os.O_CREAT                        # FileMode.Append (.NET seeks to the end itself)
OPEN_OR_CREATE_RW = os.O_RDWR | os.O_CREAT               # FileMode.OpenOrCreate, FileAccess.ReadWrite


def open_fd(path, flags):
    """The FileStream constructor's open: the full path (exceptions as .NET's), a directory refused; files are
    created with mode 0666 less the umask, as .NET creates them. Returns the descriptor; raises on failure."""
    full = full_path(path)
    if len(full) > 1 and full.endswith('/'):
        raise IsADirectoryError(21, 'Is a directory')
    fd = os.open(os_path(full), flags | os.O_CLOEXEC, 0o666)
    try:
        if _stat.S_ISDIR(os.fstat(fd).st_mode):
            raise IsADirectoryError(21, 'Is a directory')
    except BaseException:
        os.close(fd)
        raise
    return fd


# ------------------------------------------------------------------------------------------- FileStream (read)

class FileIn(object):
    """A FileStream opened for reading as IStream uses it: every read asks for a whole buffer (64 KiB), which
    BufferedFileStreamStrategy passes straight to pread at the position .NET keeps (FileStream.Position)."""
    __slots__ = ('fd', 'pos')

    def __init__(self, fd):
        self.fd = fd
        self.pos = 0

    def read(self, n):
        try:
            b = os.pread(self.fd, n, self.pos)
        except OSError as e:
            raise io_exc(e)
        self.pos += len(b)
        return b

    def length(self):
        try:
            return os.fstat(self.fd).st_size
        except OSError as e:
            raise io_exc(e)

    def seek(self, np):
        self.pos = np

    def close(self):
        fd = self.fd
        if fd >= 0:
            self.fd = -1
            try:
                os.close(fd)
            except OSError:
                pass


# ------------------------------------------------------------------------------------------- FileStream (write)

class FileOut(object):
    """A FileStream opened for writing, with its write buffer (BufferedFileStreamStrategy over the file)."""
    __slots__ = ('fd', 'size', 'buf', 'fpos', 'append_start')

    def __init__(self, fd, size, append):
        self.fd = fd
        self.size = size
        self.buf = bytearray()          # the buffered bytes (_writePos == len(buf))
        if append:
            try:
                self.fpos = os.fstat(fd).st_size
            except OSError as e:
                raise io_exc(e)
            self.append_start = self.fpos
        else:
            self.fpos = 0               # OSFileStreamStrategy._filePosition: where buf starts
            self.append_start = -1

    def raw(self, data):
        mv = memoryview(data)
        pos = self.fpos
        fd = self.fd
        try:
            while mv:
                k = os.pwrite(fd, mv, pos)
                pos += k
                mv = mv[k:]
        except OSError as e:
            raise io_exc(e)
        finally:
            self.fpos = pos

    def flush_write(self):
        if self.buf:
            b = self.buf
            self.buf = bytearray()
            self.raw(b)

    def write(self, data):
        """BufferedFileStreamStrategy.WriteSpan of a bytes-like object."""
        n = len(data)
        size = self.size
        buf = self.buf
        w = len(buf)
        if w > 0:
            room = size - w
            if room > 0:
                if room >= n:
                    buf += data
                    return
                buf += data[:room]
                data = data[room:]
                n -= room
            self.flush_write()
        if n >= size:
            self.raw(data)
            return
        if n == 0:
            return
        self.buf = bytearray(data)

    def position(self):
        return self.fpos + len(self.buf)

    def length(self):
        try:
            ln = os.fstat(self.fd).st_size
        except OSError as e:
            raise io_exc(e)
        e = self.fpos + len(self.buf)
        return e if e > ln else ln

    def seek(self, off, d):
        """FileStream.Seek: the buffer flushed, then the new position (IOException before 0 or, in append mode,
        before the end the file had when it was opened)."""
        self.flush_write()
        np = off if d == 0 else (self.length() + off if d == 2 else self.fpos + off)
        if np < 0:
            raise sys_exc('IO.IOException', SEEK_BEFORE_BEGIN)
        if self.append_start != -1 and np < self.append_start:
            raise sys_exc('IO.IOException', SEEK_APPEND)
        self.fpos = np
        return np

    def flush(self):
        self.flush_write()

    def close(self):
        try:
            self.flush_write()
        finally:
            fd = self.fd
            if fd >= 0:
                self.fd = -1
                try:
                    os.close(fd)
                except OSError:
                    pass


# ------------------------------------------------------------------------------------------- MemoryStream

class MemStream(object):
    """A MemoryStream: the bytes and the position (writes overwrite, extend, and zero-fill a gap)."""
    __slots__ = ('b', 'pos')

    def __init__(self):
        self.b = bytearray()
        self.pos = 0

    def write(self, data):
        n = len(data)
        if n <= 0:
            return
        b = self.b
        p = self.pos
        ln = len(b)
        if p == ln:
            b += data
        else:
            if p > ln:
                b += bytes(p - ln)
            b[p:p + n] = data
        self.pos = p + n

    def to_array(self):
        return bytes(self.b)

    def seek(self, off, d):
        np = off if d == 0 else (self.pos + off if d == 1 else len(self.b) + off)
        if np < 0:
            raise sys_exc('IO.IOException', SEEK_BEFORE_BEGIN)
        if np > 0x7FFFFFFF:
            raise sys_exc('ArgumentOutOfRangeException',
                          "Stream length must be non-negative and less than 2^31 - 1 - origin. (Parameter 'offset')")
        self.pos = np
        return np
