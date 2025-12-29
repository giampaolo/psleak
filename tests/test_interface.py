# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import contextlib
import io
import os
import socket
import threading
import time
import warnings
from unittest import mock

import pytest
from psutil import NETBSD
from psutil import POSIX
from psutil import WINDOWS

import psleak
from psleak import Checkers
from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase
from psleak import UnclosedFdError
from psleak import UnclosedHandleError
from psleak import _emit_warnings

from . import retry_on_failure


class TestMisc(MemoryLeakTestCase):
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

    def test_unclosed_file(self):
        def fun():
            f = open(__file__)  # noqa: SIM115
            self.addCleanup(f.close)
            box.append(f)  # prevent auto-gc

        box = []
        with pytest.raises(
            UnclosedFdError if POSIX else UnclosedHandleError
        ) as cm:
            self.execute(fun)
        if not NETBSD:
            assert len(cm.value.extras) == 1
            extra = cm.value.extras.pop()
            assert os.path.normpath(extra.path) == os.path.normpath(__file__)

    def test_unclosed_socket(self):
        def fun():
            sock = socket.socket()
            sock.bind(("", 0))
            sock.listen(5)
            self.addCleanup(sock.close)
            box.append(sock)  # prevent auto-gc

        box = []
        with pytest.raises(
            UnclosedFdError if POSIX else UnclosedHandleError
        ) as cm:
            self.execute(fun)
        assert len(cm.value.extras) == 1
        assert "SOCK_STREAM" in str(cm)

    @pytest.mark.skipif(not POSIX, reason="POSIX only")
    def test_unclosed_fd(self):
        def fun():
            fd = os.open("/dev/null", os.O_RDONLY)
            self.addCleanup(os.close, fd)
            box.append(fd)  # prevent auto-gc

        box = []

        with pytest.raises(UnclosedFdError) as cm:
            self.execute(fun)
        assert cm.value.count == 1
        assert "1 unclosed file descriptor" in str(cm)

    @pytest.mark.skipif(not WINDOWS, reason="WINDOWS only")
    def test_unclosed_handles(self):
        import _winapi  # noqa: PLC0415

        def fun():
            handle = _winapi.OpenProcess(
                _winapi.PROCESS_ALL_ACCESS, False, os.getpid()
            )
            self.addCleanup(_winapi.CloseHandle, handle)

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

    def test_tolerance_dict(self):
        ls = []

        def fun():
            ls.append("x" * 24 * 1024)

        n = 200 * 1024 * 1024

        # integer tolerance large enough
        self.execute(fun, times=100, warmup_times=0, tolerance=n)

        # None tolerance (same as 0)
        ls.clear()
        with pytest.raises(MemoryLeakError):
            self.execute(fun, warmup_times=0, tolerance=None)

        # dict full tolerance
        ls.clear()
        tol = {"rss": n, "heap": n, "mmap": n, "uss": n, "vms": n}
        self.execute(fun, warmup_times=0, tolerance=tol)

        # dict full tolerance except some
        ls.clear()
        tol = {"rss": 0, "heap": 0, "mmap": n, "uss": 0, "vms": 0}
        with pytest.raises(MemoryLeakError):
            self.execute(fun, warmup_times=0, tolerance=tol)

    def test_tolerance_errors(self):
        def fun():
            pass

        # negative integer
        with pytest.raises(ValueError, match="tolerance must be >= 0"):
            self.execute(fun, times=1, tolerance=-1)
        # invalid dict key
        with pytest.raises(
            ValueError, match="invalid tolerance key 'nonexistent'"
        ):
            self.execute(fun, times=1, tolerance={"nonexistent": 10})

        # invalid tolerance type
        with pytest.raises(TypeError, match="must be instance of"):
            self.execute(fun, times=1, tolerance="invalid")

    def test_execute_args_validation(self):
        def fun():
            pass

        # type errors
        with pytest.raises(TypeError):
            self.execute(fun, warmup_times="10")
        with pytest.raises(TypeError):
            self.execute(fun, times="100")
        with pytest.raises(TypeError):
            self.execute(fun, retries="100")
        with pytest.raises(TypeError):
            self.execute(fun, tolerance="bad")
        with pytest.raises(TypeError):
            self.execute(fun, trim_callback=123)

        # value errors
        with pytest.raises(ValueError):  # noqa: PT011
            self.execute(fun, warmup_times=-1)
        with pytest.raises(ValueError):  # noqa: PT011
            self.execute(fun, times=0)
        with pytest.raises(ValueError):  # noqa: PT011
            self.execute(fun, retries=-1)
        with pytest.raises(ValueError):  # noqa: PT011
            self.execute(fun, tolerance=-1)
        with pytest.raises(ValueError):  # noqa: PT011
            self.execute(fun, tolerance={"invalid": 1})
        with pytest.raises(ValueError):  # noqa: PT011
            self.execute(fun, tolerance={"rss": -1})

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

    def test_execute_w_exc(self):
        def fun_1():
            1 / 0  # noqa: B018

        self.execute_w_exc(ZeroDivisionError, fun_1)

        with pytest.raises(ZeroDivisionError):
            self.execute_w_exc(OSError, fun_1)

        def fun_2(a):
            pass

        with pytest.raises(AssertionError, match="did not raise"):
            self.execute_w_exc(ZeroDivisionError, fun_2, 1)

    def test_trim_callback(self):
        called = []

        def cleanup():
            called.append(True)

        def fun():
            pass

        class MyTest(MemoryLeakTestCase):
            pass

        tc = MyTest()
        tc.execute(fun, trim_callback=cleanup)
        assert called


class TestCheckers:

    def test_default_values(self):
        checkers = Checkers()
        assert checkers.fds
        assert checkers.handles
        assert checkers.py_threads
        assert checkers.c_threads
        assert checkers.memory
        assert checkers.gcgarbage

    def test_only(self):
        checkers = Checkers.only("fds", "py_threads")
        assert checkers.fds
        assert checkers.py_threads
        assert not checkers.handles
        assert not checkers.c_threads
        assert not checkers.memory
        assert not checkers.gcgarbage

        with pytest.raises(ValueError, match="invalid_checker"):
            Checkers.only("fds", "invalid_checker")

    def test_only_with_all_fields(self):
        # should enable all
        all_fields = Checkers.__annotations__.keys()
        checkers = Checkers.only(*all_fields)
        for f in all_fields:
            assert getattr(checkers, f)

    def test_exclude(self):
        checkers = Checkers.exclude("memory", "fds")
        assert not checkers.memory
        assert not checkers.fds
        assert checkers.handles
        assert checkers.py_threads
        assert checkers.c_threads
        assert checkers.gcgarbage

        with pytest.raises(ValueError, match="not_a_checker"):
            Checkers.exclude("fds", "not_a_checker")

    def test_exclude_with_no_fields(self):
        # should disable nothing, i.e., default True
        checkers = Checkers.exclude()
        for f in Checkers.__annotations__:
            assert getattr(checkers, f)


class TestMemoryLeakTestCaseConfig:

    def test_memory_disabled(self):
        checkers = Checkers.exclude("memory")

        class MyTest(MemoryLeakTestCase):
            pass

        test = MyTest()
        with mock.patch.object(test, "_check_mem", wraps=test._check_mem) as m:
            test.execute(lambda: None, checkers=checkers)
            m.assert_not_called()

    def test_py_threads_disabled(self):
        checkers = Checkers.exclude("py_threads")

        class MyTest(MemoryLeakTestCase):
            pass

        test = MyTest()
        with mock.patch.object(
            threading, "active_count", wraps=threading.active_count
        ) as m:
            test.execute(lambda: None, checkers=checkers)
            m.assert_not_called()


class TestEmitWarnings:
    def setup_method(self):
        psleak._warnings_emitted = False

    def assert_warn_msg(self, msg):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _emit_warnings()
        assert len(w) == 1
        assert msg in str(w[0].message)

    def test_pythonmalloc_not_set(self, monkeypatch):
        monkeypatch.delenv("PYTHONMALLOC", raising=False)
        self.assert_warn_msg(
            "PYTHONMALLOC=malloc environment variable was not set"
        )

    def test_pythonunbuffered_not_set(self, monkeypatch):
        monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
        self.assert_warn_msg(
            "PYTHONUNBUFFERED=1 environment variable was not set"
        )

    def test_pytest_xdist_worker(self, monkeypatch):
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        self.assert_warn_msg("pytest-xdist")

    def test_no_heap_info(self):
        with mock.patch.object(psleak.psutil, "heap_info", new=mock.DEFAULT):
            del psleak.psutil.heap_info
            self.assert_warn_msg("heap_info() not available")

    def test_active_threads_warning(self):
        def fun():
            while not stop:
                time.sleep(0.001)

        stop = False
        thread = threading.Thread(target=fun)
        thread.start()
        try:
            self.assert_warn_msg("active Python threads exist")
        finally:
            stop = True
            thread.join()
