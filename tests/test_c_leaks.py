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
        with pytest.raises(MemoryLeakError):
            self.execute(self.malloc, 1)

    def test_malloc_1k(self):
        with pytest.raises(MemoryLeakError):
            self.execute(self.malloc, 1024)

    def test_malloc_16k(self):
        with pytest.raises(MemoryLeakError):
            self.execute(self.malloc, 1024 * 16)

    def test_malloc_1M(self):
        with pytest.raises(MemoryLeakError):
            self.execute(self.malloc, 1024 * 1024)
