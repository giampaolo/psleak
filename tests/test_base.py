import contextlib
import io
import os

import pytest
from psutil import POSIX
from psutil import WINDOWS

from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase
from psleak import UnclosedFdError
from psleak import UnclosedHandleError

from . import retry_on_failure


class TestMisc(MemoryLeakTestCase):
    def test_param_err(self):
        with pytest.raises(ValueError, match="times must be"):
            self.execute(lambda: 0, times=0)
        with pytest.raises(ValueError, match="times must be"):
            self.execute(lambda: 0, times=-1)
        with pytest.raises(ValueError, match="warmup_times"):
            self.execute(lambda: 0, warmup_times=-1)
        with pytest.raises(ValueError, match="tolerance"):
            self.execute(lambda: 0, tolerance=-1)
        with pytest.raises(ValueError, match="retries"):
            self.execute(lambda: 0, retries=-1)

    def test_success(self):
        def foo():
            return 1 + 1

        self.execute(foo)

    @retry_on_failure()
    def test_leak_mem(self):
        ls = []

        def fun(ls=ls):
            ls.append("x" * 248 * 1024)

        try:
            # will consume around 60M in total
            with pytest.raises(MemoryLeakError):
                with contextlib.redirect_stdout(
                    io.StringIO()
                ), contextlib.redirect_stderr(io.StringIO()):
                    self.execute(fun, times=100)
        finally:
            del ls

    def test_unclosed_files(self):
        def fun():
            f = open(__file__)  # noqa: SIM115
            self.addCleanup(f.close)
            box.append(f)  # prevent auto-gc

        box = []
        with pytest.raises(UnclosedFdError if POSIX else UnclosedHandleError):
            self.execute(fun)

    @pytest.mark.skipif(not WINDOWS, reason="WINDOWS only")
    def test_unclosed_handles(self):
        import win32api
        import win32con

        def fun():
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION, win32con.FALSE, os.getpid()
            )
            self.addCleanup(win32api.CloseHandle, handle)

        with pytest.raises(UnclosedHandleError):
            self.execute(fun)

    def test_tolerance(self):
        def fun():
            ls.append("x" * 24 * 1024)

        ls = []
        times = 100
        self.execute(
            fun, times=times, warmup_times=0, tolerance=200 * 1024 * 1024
        )

    def test_execute_w_exc(self):
        def fun_1():
            1 / 0  # noqa: B018

        self.execute_w_exc(ZeroDivisionError, fun_1)

        with pytest.raises(ZeroDivisionError):
            self.execute_w_exc(OSError, fun_1)

        def fun_2():
            pass

        with pytest.raises(AssertionError, match="did not raise"):
            self.execute_w_exc(ZeroDivisionError, fun_2)
