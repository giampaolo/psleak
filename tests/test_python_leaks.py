import threading

import pytest

from psleak import MemoryLeakTestCase
from psleak import UnclosedPythonThreadError
from psleak import UncollectableGarbageError


class TestPythonThreads(MemoryLeakTestCase):

    def test_it(self):
        """Create a Python thread and leave it running (no join()).
        Expect UnclosedPythonThreadError to be raised.
        """

        def worker():
            done.wait()  # block until signaled

        def fun():
            thread = threading.Thread(target=worker)
            thread.start()
            self.addCleanup(done.set)

        done = threading.Event()
        with pytest.raises(UnclosedPythonThreadError):
            self.execute(fun)


class TestGarbageLeak(MemoryLeakTestCase):
    def test_uncollectable_garbage(self):
        class Leaky:
            def __init__(self):
                self.ref = None

        def create_cycle():
            a = Leaky()
            b = Leaky()
            a.ref = b
            b.ref = a
            return a, b  # cycle preventing GC from collecting

        with pytest.raises(UncollectableGarbageError):
            self.execute(create_cycle)
