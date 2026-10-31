"""Read the directory of our own shell process without sending terminal input."""
import os


def process_directory(pid):
    if os.name!='nt':return os.readlink(f'/proc/{pid}/cwd')
    return windows_directory(pid)


def windows_directory(pid):
    """Read only the child process's current-directory field (native or WOW64).

    Windows has no public GetCurrentDirectory(other_pid) API. The native process
    parameter layout is inspected defensively; failure leaves the browser at its
    last directory. See NtQueryInformationProcess and phnt/ntrtl.h CURDIR layout.
    """
    import ctypes as c
    from ctypes import wintypes as w
    kernel=c.WinDLL('kernel32',use_last_error=True);nt=c.WinDLL('ntdll')
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.ReadProcessMemory.argtypes=[w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)];kernel.ReadProcessMemory.restype=w.BOOL
    nt.NtQueryInformationProcess.argtypes=[w.HANDLE,w.ULONG,c.c_void_p,w.ULONG,c.POINTER(w.ULONG)];nt.NtQueryInformationProcess.restype=c.c_long
    handle=kernel.OpenProcess(0x410,False,pid)
    if not handle:raise OSError(c.get_last_error(),'Cannot inspect the local shell directory.')
    try:
        def read(address,length):
            if not address or not 0<length<=65536:raise OSError('Invalid process directory data.')
            buffer=c.create_string_buffer(length);actual=c.c_size_t()
            if not kernel.ReadProcessMemory(handle,address,buffer,length,c.byref(actual)) or actual.value!=length:raise OSError('Shell directory is temporarily unavailable.')
            return buffer.raw
        class Basic(c.Structure):_fields_=[('reserved',c.c_void_p),('peb',c.c_void_p),('other',c.c_void_p*4)]
        basic=Basic();length=w.ULONG();wow=c.c_size_t()
        if nt.NtQueryInformationProcess(handle,0,c.byref(basic),c.sizeof(basic),c.byref(length))<0:raise OSError('Cannot query local shell.')
        nt.NtQueryInformationProcess(handle,26,c.byref(wow),c.sizeof(wow),c.byref(length))
        width=4 if wow.value else c.sizeof(c.c_void_p);peb=wow.value or basic.peb
        params=int.from_bytes(read(peb+(0x10 if width==4 else 0x20),width),'little')
        offset=0x24 if width==4 else 0x38;size=8 if width==4 else 16
        value=read(params+offset,size);count=int.from_bytes(value[:2],'little');pointer=int.from_bytes(value[4 if width==4 else 8:],'little')
        if count%2 or not count:raise OSError('Invalid shell directory length.')
        path=read(pointer,count).decode('utf-16-le')
        if '\0' in path or not os.path.isabs(path):raise OSError('Invalid shell directory.')
        return path
    finally:kernel.CloseHandle(handle)
