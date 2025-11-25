import pytest

from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase

from .cutils import free
from .cutils import malloc


class TestMallocWithoutFree(MemoryLeakTestCase):
    def malloc(self, size):
        ptr = malloc(size)
        self.addCleanup(free, ptr)

    def test_malloc_1b(self):
        def fun():
            self.malloc(1)

        with pytest.raises(MemoryLeakError):
            self.execute(fun)

    def test_malloc_1k(self):
        def fun():
            self.malloc(1024)

        with pytest.raises(MemoryLeakError):
            self.execute(fun)

    def test_malloc_16k(self):
        def fun():
            self.malloc(1024 * 16)

        with pytest.raises(MemoryLeakError):
            self.execute(fun)

    def test_malloc_1M(self):
        def fun():
            self.malloc(1024 * 1024)

        with pytest.raises(MemoryLeakError):
            self.execute(fun)
