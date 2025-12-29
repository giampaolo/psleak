# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import threading

import pytest
import test_ext as cext
from psutil import POSIX
from psutil import WINDOWS

from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase
from psleak import UnclosedHeapCreateError
from psleak import UnclosedNativeThreadError

from . import retry_on_failure


class TestMallocWithoutFree(MemoryLeakTestCase):
    """Allocate memory via malloc() and deliberately never call free().
    This must trigger a MemoryLeakError because `heap_used` grows for
    small allocations, and `mmap_used` grows for bigger ones.
    """

    def run_test(self, size, times=None):
        # just malloc(); expect failure
        with pytest.raises(MemoryLeakError):
            self.execute(cext.malloc, size, times=times)

        # malloc() + free(); expect success
        def fun():
            ptr = cext.malloc(size)
            cext.free(ptr)

        self.execute(fun, times=times)

    def test_1b(self):
        self.run_test(1, times=200)

    def test_1k(self):
        self.run_test(1024)

    def test_16k(self):
        self.run_test(1024 * 16)

    @retry_on_failure()
    def test_1M(self):  # noqa: N802
        self.run_test(1024 * 1024, times=30)


# --- posix


@pytest.mark.skipif(not POSIX, reason="POSIX only")
class TestMmapWithoutMunmap(TestMallocWithoutFree):
    """Allocate memory via mmap() and deliberately never call munmap().
    Funnily enough it's not `mmap_used` that grows but VMS.
    """

    def run_test(self, size, times=None):
        # just mmap(); expect failure
        with pytest.raises(MemoryLeakError):
            self.execute(cext.mmap, size, times=times)

        # mmap() + munmap(); expect success
        def fun():
            ptr = cext.mmap(size)
            cext.munmap(ptr, size)

        self.execute(fun, times=times)


# --- windows


@pytest.mark.skipif(not WINDOWS, reason="WINDOWS only")
class TestHeapAllocWithoutHeapFree(TestMallocWithoutFree):
    """Allocate memory via HeapAlloc() and deliberately never call
    HeapFree().
    """

    def run_test(self, size, times=None):
        # just HeapAlloc(); expect failure
        with pytest.raises(MemoryLeakError):
            self.execute(cext.HeapAlloc, size, times=times)

        # HeapAlloc() + HeapFree(); expect success
        def fun():
            ptr = cext.HeapAlloc(size)
            cext.HeapFree(ptr)

        self.execute(fun, times=times)


@pytest.mark.skipif(not WINDOWS, reason="WINDOWS only")
class TestVirtualAllocWithoutVirtualFree(TestMallocWithoutFree):
    """Allocate memory via VirtualAlloc() and deliberately never call
    VirtualFree().
    """

    def run_test(self, size, times=None):
        # just VirtualAlloc(); expect failure
        with pytest.raises(MemoryLeakError):
            self.execute(cext.VirtualAlloc, size, times=times)

        # VirtualAlloc() + VirtualFree(); expect success
        def fun():
            ptr = cext.VirtualAlloc(size)
            cext.VirtualFree(ptr)

        self.execute(fun, times=times)


@pytest.mark.skipif(not WINDOWS, reason="WINDOWS only")
class TestHeapCreateWithoutHeapDestroy(TestMallocWithoutFree):
    """Allocate memory via HeapCreate() and deliberately never call
    HeapDestroy(). Expect UnclosedHeapCreateError to be raised.
    """

    def run_test(self, size, times=None):
        # just HeapCreate(); expect failure
        with pytest.raises(UnclosedHeapCreateError):
            self.execute(cext.HeapCreate, size, 0, times=times)

        # HeapCreate() + HeapDestroy(); expect success
        def fun():
            ptr = cext.HeapCreate(size, 0)
            cext.HeapDestroy(ptr)

        self.execute(fun, times=times)


# --- threads


class TestUnclosedThreads(MemoryLeakTestCase):

    def test_c_thread(self):
        """Create a native C thread and leave it running. Expect
        UnclosedNativeThreadError to be raised.
        """

        def fun():
            nonlocal ptr
            ptr = cext.start_native_thread()
            # Native C threads are supposed to be "hidden" to Python.
            # Make sure they doesn't show up.
            assert threading.active_count() == init_pythread_count
            self.addCleanup(cext.stop_native_thread, ptr)

        init_pythread_count = threading.active_count()
        ptr = None
        with pytest.raises(UnclosedNativeThreadError):
            self.execute(fun)


# --- python idioms


class TestPythonExtensionLeaks(MemoryLeakTestCase):
    """Test typical patterns that lead to a memory leak in C
    extensions, like forgetting Py_DECREF, etc.
    """

    def test_leak_list_small(self):
        # fails without PYTHONMALLOC=malloc
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_list, 1)

    def test_leak_list_big(self):
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_list, 100)

    def test_leak_long_small(self):
        # fails without PYTHONMALLOC=malloc
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_long, 512)

    def test_leak_long_big(self):
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_long, 1024)

    def test_leak_tuple_small(self):
        # fails without PYTHONMALLOC=malloc
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_tuple, 1)

    def test_leak_tuple_big(self):
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_tuple, 100)

    def test_leak_dict(self):
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_dict)

    def test_leak_cycle(self):
        with pytest.raises(MemoryLeakError):
            self.execute(cext.leak_cycle)


class TestPymalloc(MemoryLeakTestCase):

    def run_test(self, alloc_fun, free_fun, size, times=None):
        # just allocate; expect failure
        with pytest.raises(MemoryLeakError):
            self.execute(alloc_fun, size, times=times)

        # allocate + free; expect success
        def fun():
            ptr = alloc_fun(size)
            free_fun(ptr)

        self.execute(fun, times=times)

    def test_pymem_malloc_small(self):
        self.run_test(cext.pymem_malloc, cext.pymem_free, 1)

    def test_pymem_malloc_big(self):
        self.run_test(cext.pymem_malloc, cext.pymem_free, 1024)

    def test_pyobject_malloc_small(self):
        self.run_test(cext.pyobject_malloc, cext.pyobject_free, 1)

    def test_pyobject_malloc_big(self):
        self.run_test(cext.pyobject_malloc, cext.pyobject_free, 1024)
