import os
import tempfile

import pytest

from psleak import LeakCheckers
from psleak import MemoryLeakTestCase
from psleak import UndeletedTempdirError
from psleak import UndeletedTempfileError


class TestLeakedTempfile(MemoryLeakTestCase):
    checkers = LeakCheckers.exclude("memory")

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
    checkers = LeakCheckers.exclude("memory")

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
