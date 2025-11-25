import pytest

from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase

from .cutils import malloc


class TestMallocWithoutFree(MemoryLeakTestCase):
    """Allocate memory via malloc() and deliberately never call free().
    This must trigger a MemoryLeakError because `heap_used` grows for
    small allocations, and `mmap_used` grows for bigger ones.
    """

    def test_1b(self):
        with pytest.raises(MemoryLeakError):
            self.execute(malloc, 1, times=20000)

    def test_1k(self):
        with pytest.raises(MemoryLeakError):
            self.execute(malloc, 1024)

    def test_16k(self):
        with pytest.raises(MemoryLeakError):
            self.execute(malloc, 1024 * 16)

    def test_1M(self):
        with pytest.raises(MemoryLeakError):
            self.execute(malloc, 1024 * 1024)
