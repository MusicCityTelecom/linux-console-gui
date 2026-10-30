"""Small OS adapters shared by the Linux and Windows builds."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import sys


def config_directory():
    override=os.environ.get('XDG_CONFIG_HOME')
    if override: return Path(override)/'serial-desk'
    if os.name=='nt': return Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))/'SerialDesk'
    return Path.home()/'.config/serial-desk'


def application_directory():
    return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent


@contextmanager
def exclusive_file_lock(path):
    fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
    with os.fdopen(fd,'r+b') as f:
        if os.name=='nt':
            import msvcrt
            if os.fstat(f.fileno()).st_size==0: f.write(b'0');f.flush()
            f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
            try: yield
            finally: f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(f,fcntl.LOCK_UN)


def _dpapi(data,encrypt):
    """Protect a local vault key for the current Windows user with DPAPI."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buf=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
    source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if encrypt:
        fn=crypt.CryptProtectData
        fn.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        args=(ctypes.byref(source),'Serial Desk local vault key',None,None,None,1,ctypes.byref(target))
    else:
        fn=crypt.CryptUnprotectData
        fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        args=(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    fn.restype=wintypes.BOOL
    if not fn(*args): raise ctypes.WinError(ctypes.get_last_error())
    try: return ctypes.string_at(target.pbData,target.cbData)
    finally: kernel.LocalFree(target.pbData)


def protect_local_key(key): return b'DPAPI1'+_dpapi(key,True) if os.name=='nt' else key

def unprotect_local_key(data):
    if data.startswith(b'DPAPI1'):
        if os.name!='nt': raise OSError('This local vault key is tied to a Windows account. Use master-password mode to move a vault between systems.')
        return _dpapi(data[6:],False)
    return data
