"""Current-user Windows DPAPI source for local repositories only.

No passwords are passed in argv, JSON configuration, subprocess diagnostics or
reports. This is not offline escrow or cross-account disaster recovery.
"""
import json
import os
from pathlib import Path
import subprocess

from .backup_sets import safe_path
from .kopia_connectivity import subprocess_environment
from .storage_foundation import StorageError


def windows_dpapi_available():
    return os.name == 'nt'


def _acl_observation(path):
    script = """$ErrorActionPreference='Stop'
$a=Get-Acl -LiteralPath $env:ACTION_STORAGE_DPAPI_PATH
$sid=[System.Security.Principal.SecurityIdentifier]
$current=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$owner=$a.GetOwner($sid).Value
$allowed=@($a.GetAccessRules($true,$true,$sid) | Where-Object {$_.AccessControlType -eq 'Allow'} | ForEach-Object {$_.IdentityReference.Value})
[pscustomobject]@{current=$current;owner=$owner;allowed=$allowed}|ConvertTo-Json -Compress
"""
    env = subprocess_environment()
    env['ACTION_STORAGE_DPAPI_PATH'] = str(path)  # locator only, never secret bytes
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        env=env, capture_output=True, encoding='utf-8', timeout=30)
    if result.returncode:
        raise StorageError('KOPIA_DPAPI_ACL_UNAVAILABLE')
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise StorageError('KOPIA_DPAPI_ACL_UNAVAILABLE') from None


def _unprotect(value):
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    encrypted, plain = Blob(len(value), buffer), Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(encrypted), None, None, None, None, 1, ctypes.byref(plain)):
        raise StorageError('KOPIA_DPAPI_CURRENT_USER_UNAVAILABLE')
    try:
        return ctypes.string_at(plain.data, plain.size).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(plain.data)


def local_password(path: Path, *, provider):
    if provider not in {'filesystem', 'local'}:
        raise StorageError('KOPIA_DPAPI_LOCAL_REPOSITORIES_ONLY')
    if not windows_dpapi_available():
        raise StorageError('KOPIA_DPAPI_WINDOWS_REQUIRED')
    try:
        path = safe_path(path)
        if path.suffix.lower() != '.dpapi' or not 0 < path.stat().st_size <= 65536:
            raise StorageError('KOPIA_DPAPI_FILE_INVALID')
        acl = _acl_observation(path)
        if (not acl.get('current') or acl.get('owner') != acl['current'] or
                not isinstance(acl.get('allowed'), list) or
                set(acl['allowed']) - {acl['current'], 'S-1-5-18'} or
                acl['current'] not in acl['allowed']):
            raise StorageError('KOPIA_DPAPI_PRIVATE_ACL_REQUIRED')
        password = _unprotect(path.read_bytes())
        if not password or '\x00' in password:
            raise StorageError('KOPIA_DPAPI_PASSWORD_INVALID')
        return password
    except StorageError:
        raise
    except Exception:
        raise StorageError('KOPIA_DPAPI_CREDENTIAL_UNAVAILABLE') from None
