"""Utilities for tests based on `psutil.heap_info()`.

The tests deliberately create **controlled memory leaks** by calling
low-level C allocation functions (`malloc()`, `HeapAlloc()`,
`VirtualAllocEx()`, etc.) **without** freeing them - exactly how
real-world memory leaks occur in native C extensions code.

By bypassing Python's memory manager entirely (via `ctypes`), we
directly exercise the underlying system allocator:

UNIX

- Small `malloc()` allocations (≤128KB on glibc) without `free()`
  increase `heap_used`.
- Large `malloc()` allocations  without `free()` trigger `mmap()` and
  increase `mmap_used`.
    - Note: direct `mmap()` / `munmap()` via `ctypes` was attempted but
      proved unreliable.

Windows

- `HeapAlloc()` without `HeapFree()` increases `heap_used`.
- `VirtualAllocEx()` without `VirtualFreeEx()` increases `mmap_used`.
- `HeapCreate()` without `HeapDestroy()` increases `heap_count`.

These tests ensure that `psutil.heap_info()` detects unreleased native
memory across different allocators (glibc on Linux, jemalloc on
BSD/macOS, Windows CRT) and that pyleak test suite raises an exception.
"""

import ctypes

from psutil import POSIX
from psutil import WINDOWS

if POSIX:  # noqa: SIM108
    libc = ctypes.CDLL(None)
else:
    libc = ctypes.CDLL("msvcrt.dll")


def malloc(size):
    """Same as C malloc() (allocate memory). If passed a small size,
    usually affects heap_used, else mmap_used (not on Windows).
    """
    fun = libc.malloc
    fun.argtypes = [ctypes.c_size_t]
    fun.restype = ctypes.c_void_p
    ptr = fun(size)
    assert ptr, f"malloc({size}) failed"
    return ptr


def free(ptr):
    """Free malloc() memory. Same as C free()."""
    fun = libc.free
    fun.argtypes = [ctypes.c_void_p]
    fun.restype = None
    fun(ptr)


if WINDOWS:
    from ctypes import wintypes

    import win32api
    import win32con
    import win32process

    kernel32 = ctypes.windll.kernel32
    HEAP_NO_SERIALIZE = 0x00000001

    # --- for `heap_used`

    def GetProcessHeap():
        fun = kernel32.GetProcessHeap
        fun.argtypes = []
        fun.restype = wintypes.HANDLE
        heap = fun()
        assert heap != 0, "GetProcessHeap failed"
        return heap

    def HeapAlloc(heap, size):
        fun = kernel32.HeapAlloc
        fun.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_size_t]
        fun.restype = ctypes.c_void_p
        addr = fun(heap, 0, size)
        assert addr, f"HeapAlloc {size} failed"
        return addr

    def HeapFree(heap, addr):
        fun = kernel32.HeapFree
        fun.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
        fun.restype = wintypes.BOOL
        assert fun(heap, 0, addr) != 0, "HeapFree failed"

    # --- for `mmap_used`

    def VirtualAllocEx(size):
        return win32process.VirtualAllocEx(
            win32api.GetCurrentProcess(),
            0,
            size,
            win32con.MEM_COMMIT | win32con.MEM_RESERVE,
            win32con.PAGE_READWRITE,
        )

    def VirtualFreeEx(addr):
        win32process.VirtualFreeEx(
            win32api.GetCurrentProcess(), addr, 0, win32con.MEM_RELEASE
        )

    # --- for `heap_count`

    def HeapCreate(initial_size, max_size):
        fun = kernel32.HeapCreate
        fun.argtypes = [
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]
        fun.restype = wintypes.HANDLE
        heap = fun(HEAP_NO_SERIALIZE, initial_size, max_size)
        assert heap != 0, "HeapCreate failed"
        return heap

    def HeapDestroy(heap):
        fun = kernel32.HeapDestroy
        fun.argtypes = [wintypes.HANDLE]
        fun.restype = wintypes.BOOL
        assert fun(heap) != 0, "HeapDestroy failed"
