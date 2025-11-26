import pytest
import test_ext as cext

from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase


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
        self.run_test(1)

    def test_1k(self):
        self.run_test(1024)

    def test_16k(self):
        self.run_test(1024 * 16)

    def test_1M(self):
        self.run_test(1024 * 1024, times=30)


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
