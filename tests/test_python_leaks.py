import os
import subprocess
import tempfile
import threading

import pytest

from psleak import Checkers
from psleak import MemoryLeakTestCase
from psleak import UnclosedPythonThreadError
from psleak import UnclosedSubprocessError
from psleak import UncollectableGarbageError
from psleak import UndeletedTempdirError
from psleak import UndeletedTempfileError


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


class TestLeakedTempfile(MemoryLeakTestCase):
    checkers = Checkers.exclude("memory")

    def test_mkstemp(self):
        def fun():
            nonlocal fname
            fd, fname = tempfile.mkstemp()
            self.addCleanup(os.remove, fname)
            os.close(fd)

        fname = None
        with pytest.raises(UndeletedTempfileError, match="tempfile") as cm:
            self.execute(fun)
        assert os.path.isfile(fname)
        assert fname in str(cm)

    def test_NamedTemporaryFile(self):
        def fun():
            nonlocal fname
            with tempfile.NamedTemporaryFile(delete=False) as f:
                pass
            fname = f.name
            self.addCleanup(os.remove, fname)

        fname = None
        with pytest.raises(UndeletedTempfileError, match="tempfile") as cm:
            self.execute(fun)
        assert os.path.isfile(fname)
        assert fname in str(cm)

    def test_TemporaryFile(self):
        def fun():
            with tempfile.TemporaryFile():
                pass

        self.execute(fun)

    def test_SpooledTemporaryFile(self):
        def fun():
            with tempfile.SpooledTemporaryFile():
                pass

        self.execute(fun)


class TestLeakedTempdir(MemoryLeakTestCase):
    checkers = Checkers.exclude("memory")

    def test_mkdtemp(self):
        def fun():
            nonlocal dname
            dname = tempfile.mkdtemp()
            self.addCleanup(os.rmdir, dname)

        dname = None
        with pytest.raises(UndeletedTempdirError, match="tempdir") as cm:
            self.execute(fun)
        assert os.path.isdir(dname)
        assert dname in str(cm)


class TestLeakedSubprocess(MemoryLeakTestCase):
    checkers = Checkers.exclude("memory")

    def test_running_process(self):
        def fun():
            nonlocal proc
            proc = subprocess.Popen(
                ["python3", "-c", "import time; time.sleep(5)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # no terminate/kill/communicate -> should leak
            self.addCleanup(lambda: proc.terminate() and proc.wait())

        proc = None
        with pytest.raises(UnclosedSubprocessError, match="process") as cm:
            self.execute(fun)

        assert proc.poll() is None  # still running
        assert str(proc) in str(cm)

        proc.terminate()
        proc.wait()
        assert proc.poll()

    def test_open_pipes(self):
        def fun():
            nonlocal proc
            proc = subprocess.Popen(
                ["python3", "-c", "print(123)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # process exits immediately, but pipes remain open -> leak
            # no proc.communicate() to close pipes
            self.addCleanup(lambda: proc.terminate() and proc.wait())

        proc = None
        with pytest.raises(UnclosedSubprocessError, match="process") as cm:
            self.execute(fun)

        # The process might have exited, but stdout/stderr is still open.
        assert proc.stdout
        assert proc.stderr
        assert not proc.stdout.closed
        assert not proc.stderr.closed

        assert str(proc) in str(cm)


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

        self.execute(create_cycle)

        with pytest.raises(UncollectableGarbageError):
            self.execute(create_cycle)
