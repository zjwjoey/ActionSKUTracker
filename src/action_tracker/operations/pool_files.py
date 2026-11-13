"""Pool file accounting and safe OS locks; no production database connections."""
from contextlib import contextmanager, ExitStack
import ctypes
import hashlib
import os
from pathlib import Path
import time

from . import cleanup_executor as cleanup
from .storage_foundation import StorageError


def allocation(path):
    """Allocated bytes, NOT logical length or GetCompressedFileSize fallback."""
    path = Path(path)
    if os.name != 'nt':
        return path.stat().st_blocks * 512
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    create.restype = w.HANDLE
    class Standard(ctypes.Structure):
        _fields_ = [('allocated', ctypes.c_longlong), ('length', ctypes.c_longlong),
                    ('links', w.DWORD), ('pending', w.BOOLEAN), ('directory', w.BOOLEAN)]
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]; query.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = create(str(path), 0x80, 7, None, 3, 0x00200000, None)
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value = Standard()
        if not query(handle, 1, ctypes.byref(value), ctypes.sizeof(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.allocated
    finally:
        kernel.CloseHandle(handle)


def file_record(path):
    value = cleanup.identity(path)
    value['allocated_bytes'] = allocation(path)
    with Path(path).open('rb') as stream:
        value['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    final = cleanup.identity(path)
    if any(final[k] != value[k] for k in ('file_id', 'size_bytes', 'mtime_ns')):
        raise StorageError('POOL_FILE_CHANGED_DURING_HASH')
    return value


@contextmanager
def os_lock(path, *, wait=0):
    path = Path(path)
    try:
        with path.open('xb') as stream:stream.write(b'0')
    except FileExistsError:
        pass
    deadline = time.monotonic() + wait
    while True:
        ctx = cleanup._held_file(path, writable=False)
        try:
            held = ctx.__enter__()
            break
        except (StorageError, OSError):
            if time.monotonic() >= deadline:raise StorageError('POOL_FILE_IN_USE') from None
            time.sleep(0.05)
    try:
        yield held
    finally:
        ctx.__exit__(None, None, None)


def delete_group(files, *, root, final_check, owned_directory=None):
    """Claim every file before deletion; optional explicitly owned bundle tree."""
    root = Path(root).resolve()
    if owned_directory is not None:
        import re
        owned_directory=cleanup.safe_path(Path(owned_directory))
        if (owned_directory.parent!=root/'staging'
                or not re.fullmatch(r'bundle-[0-9a-f]{32}',owned_directory.name)):
            raise StorageError('POOL_DELETE_DIRECTORY_NOT_OWNED')
    with ExitStack() as stack:
        held = []
        for item in files:
            path = cleanup.safe_path(Path(item['path']))
            allowed=(path.is_relative_to(owned_directory) if owned_directory is not None
                     else path.parent in {root/'active',root/'staging'})
            if not path.is_relative_to(root) or not allowed:
                raise StorageError('POOL_DELETE_OUTSIDE_OWNED_NAMESPACE')
            stream, kernel, handle = stack.enter_context(cleanup._held_file(path))
            st = os.fstat(stream.fileno())
            if (f'{st.st_dev}:{st.st_ino}' != item['file_id'] or st.st_nlink != 1
                    or st.st_size != item['size_bytes']
                    or hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']):
                raise StorageError('POOL_DELETE_IDENTITY_OR_HASH_CHANGED')
            held.append((path, kernel, handle))
        final_check()
        for path, kernel, handle in held:
            if kernel is not None:
                from ctypes import wintypes as w
                value = w.BOOLEAN(True)
                if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(value), ctypes.sizeof(value)):
                    raise StorageError('POOL_DELETE_FAILED')
            else:
                # POSIX advisory locks are cooperative, as in Cleanup V1.
                if cleanup.identity(path)['file_id'] != next(f['file_id'] for f in files if Path(f['path'])==path):
                    raise StorageError('POOL_DELETE_PATH_CHANGED')
                path.unlink()
