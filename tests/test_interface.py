# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections
import contextlib
import io
import os
import socket
import threading
import time
import unittest
import warnings
from unittest import mock

import pytest
from psutil import NETBSD
from psutil import POSIX
from psutil import WINDOWS

import psleak
from psleak import Checkers
from psleak import LeakTest
from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase
from psleak import UnclosedFdError
from psleak import UnclosedHandleError
from psleak import UnclosedHeapCreateError
from psleak import UnclosedNativeThreadError
from psleak import _emit_warnings
from psleak import diff_resources


class TestMisc(MemoryLeakTestCase):
    def test_success(self):
        def foo():
            return 1 + 1

        self.execute(foo)

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
                    self.execute(fun, times=20, retries=5)
        finally:
            del ls

    # TODO: remove when psutil 8.0.0 is out
    @pytest.mark.skipif(
        NETBSD, reason="num_fds() doesn't see new fds on NetBSD"
    )
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
        # On Windows the count is all we get: _FdsBaseline skips
        # open_files() there, so a leaked file never lands in extras.
        if not WINDOWS:
            assert len(cm.value.extras) == 1
            extra = cm.value.extras.pop()
            assert os.path.normpath(extra.path) == os.path.normpath(__file__)

    # TODO: remove when psutil 8.0.0 is out
    @pytest.mark.skipif(
        NETBSD, reason="num_fds() doesn't see new fds on NetBSD"
    )
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
    # TODO: remove when psutil 8.0.0 is out
    @pytest.mark.skipif(
        NETBSD, reason="num_fds() doesn't see new fds on NetBSD"
    )
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

    # TODO: remove when psutil 8.0.0 is out
    @pytest.mark.skipif(
        NETBSD, reason="num_fds() doesn't see new fds on NetBSD"
    )
    def test_counters_retry_on_transient(self):
        # A one-time allocation which stays open (lazy init) is not a
        # leak: the retry re-baselines and the second round passes,
        # but a warning is emitted.
        box = []

        def fun():
            if not box:
                f = open(__file__)  # noqa: SIM115
                self.addCleanup(f.close)
                box.append(f)

        with pytest.warns(ResourceWarning, match="absorbed by re-baselining"):
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

        # None falls back to the class attr (0 by default)
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
            self.execute(fun, times=2, tolerance=-1)
        # invalid dict key
        with pytest.raises(
            ValueError, match="invalid tolerance key 'nonexistent'"
        ):
            self.execute(fun, times=2, tolerance={"nonexistent": 10})

        # invalid tolerance type
        with pytest.raises(TypeError, match="must be instance of"):
            self.execute(fun, times=2, tolerance="invalid")

    def test_execute_args_validation(self):
        def fun():
            pass

        # type errors
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
        with pytest.raises(ValueError, match="times must be"):
            # times=1 would make the escalation a no-op
            self.execute(lambda: 0, times=1)
        with pytest.raises(ValueError, match="retries"):
            # with retries=1 growth can never fade twice in a row
            self.execute(lambda: 0, retries=0)
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

    def test_pythonmalloc_not_set(self):
        class Test(MemoryLeakTestCase):
            def test_it(self):
                self.execute(lambda: None)

        with mock.patch.dict(
            os.environ, {"PYTHONUNBUFFERED": "1"}, clear=True
        ):
            with pytest.raises(unittest.SkipTest, match="PYTHONMALLOC"):
                Test().test_it()


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

    def test_py_threads_disabled(self, monkeypatch):
        checkers = Checkers.exclude("py_threads")

        class MyTest(MemoryLeakTestCase):
            pass

        # _emit_warnings() also calls active_count(); mark warnings
        # as already emitted so the mock only sees checker calls
        monkeypatch.setattr(psleak, "_warnings_emitted", True)

        test = MyTest()
        with mock.patch.object(
            threading, "active_count", wraps=threading.active_count
        ) as m:
            test.execute(lambda: None, checkers=checkers)
            m.assert_not_called()

    def test_execute_kwargs_precedence(self):
        # kwargs must win over class attrs; class attrs must be
        # used when kwargs are omitted
        class MyTest(MemoryLeakTestCase):
            times = 33
            retries = 4
            tolerance = 7

        test = MyTest()
        with mock.patch.object(test, "_check_mem") as m:
            test.execute(lambda: None, times=11, retries=2, tolerance=5)
        m.assert_called_once_with(mock.ANY, times=11, retries=2, tolerance=5)

        with mock.patch.object(test, "_check_mem") as m:
            test.execute(lambda: None)
        m.assert_called_once_with(mock.ANY, times=33, retries=4, tolerance=7)

    def test_times_are_cast_to_int(self):
        class MyTest(MemoryLeakTestCase):
            times = "44"
            warmup_times = 3.9

        test = MyTest()
        with mock.patch.object(test, "_warmup") as w:
            with mock.patch.object(test, "_check_mem") as m:
                test.execute(lambda: None, times=10.7)
        assert m.call_args.kwargs["times"] == 10
        assert w.call_args[0][1] == 3

        with mock.patch.object(test, "_check_mem") as m:
            test.execute(lambda: None)
        assert m.call_args.kwargs["times"] == 44


class TestEmitWarnings:
    def setup_method(self):
        psleak._warnings_emitted = False

    def assert_warn_msg(self, msg):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _emit_warnings()
        assert len(w) == 1
        assert msg in str(w[0].message)

    def test_pythonunbuffered_not_set(self, monkeypatch):
        monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
        self.assert_warn_msg(
            "PYTHONUNBUFFERED=1 environment variable was not set"
        )

    def test_no_heap_info(self, monkeypatch):
        monkeypatch.setenv("PYTHONUNBUFFERED", "1")
        with mock.patch.object(psleak.psutil, "heap_info", new=mock.DEFAULT):
            del psleak.psutil.heap_info
            self.assert_warn_msg("heap_info() not available")

    def test_active_threads_warning(self, monkeypatch):
        monkeypatch.setenv("PYTHONUNBUFFERED", "1")

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

    def test_heap_used_zero(self, monkeypatch):
        monkeypatch.setenv("PYTHONUNBUFFERED", "1")

        class FakeHeapInfo:
            heap_used = 0
            mmap_used = 100

        with mock.patch.object(
            psleak.psutil,
            "heap_info",
            return_value=FakeHeapInfo(),
        ):
            self.assert_warn_msg("psutil.heap_info() appears disabled")


class TestAutoGenerate(unittest.TestCase):

    def test_simple_leaktest(self):
        calls = []

        class Test(MemoryLeakTestCase):
            @classmethod
            def auto_generate(cls):
                return {"foo": LeakTest(lambda: None)}

            def execute(self, fun, **kwargs):
                calls.append((fun, kwargs))

        test = Test("test_leak_foo")
        test.test_leak_foo()

        assert len(calls) == 1
        fun, kwargs = calls[0]
        assert callable(fun)
        assert kwargs == {}

    def test_leaktest_with_args(self):
        calls = []
        called = []

        def f(a, b):
            called.append((a, b))

        class Test(MemoryLeakTestCase):
            @classmethod
            def auto_generate(cls):
                return {"foo": LeakTest(f, 1, 2)}

            def execute(self, fun, **kwargs):
                fun()
                calls.append((fun, kwargs))

        Test("test_leak_foo").test_leak_foo()
        assert called == [(1, 2)]
        assert calls[0][1] == {}

    def test_execute_kwargs_override(self):
        calls = []

        class Test(MemoryLeakTestCase):
            @classmethod
            def auto_generate(cls):
                return {"foo": LeakTest(lambda: None, times=10, tolerance=123)}

            def execute(self, fun, **kwargs):
                calls.append((fun, kwargs))

        Test("test_leak_foo").test_leak_foo()
        assert calls[0][1] == {"times": 10, "tolerance": 123}

    def test_execute_kwargs_do_not_leak_between_tests(self):
        calls = []

        class Test(MemoryLeakTestCase):
            @classmethod
            def auto_generate(cls):
                return {
                    "a": LeakTest(lambda: None, times=1),
                    "b": LeakTest(lambda: None, times=2),
                }

            def execute(self, fun, **kwargs):
                calls.append((fun, kwargs))

        Test("test_leak_a").test_leak_a()
        Test("test_leak_b").test_leak_b()
        assert calls[0][1] == {"times": 1}
        assert calls[1][1] == {"times": 2}

    def test_name_collision(self):
        with pytest.raises(RuntimeError):

            class Test(MemoryLeakTestCase):
                @classmethod
                def auto_generate(cls):
                    return {"foo": LeakTest(lambda: None)}

                def test_leak_foo(self):
                    pass

    def test_inherited_no_collision(self):
        # a subclass re-runs auto_generate: the test methods it
        # inherits from the parent must not count as duplicates
        class Parent(MemoryLeakTestCase):
            @classmethod
            def auto_generate(cls):
                return {"foo": LeakTest(lambda: None)}

        class Child(Parent):
            pass

        assert "test_leak_foo" in Parent.__dict__
        assert "test_leak_foo" in Child.__dict__


pthread = collections.namedtuple("pthread", ["id", "user_time", "system_time"])


class TestDiffResources(unittest.TestCase):
    def test_new_vs_changed(self):
        # Same id with advanced CPU time is "changed"; a new id is "new".
        before = [pthread(1, 0.0, 0.0)]
        after = [pthread(1, 5.0, 3.0), pthread(2, 0.0, 0.0)]
        new, changed = diff_resources(before, after)
        assert new == [pthread(2, 0.0, 0.0)]
        assert changed == [pthread(1, 5.0, 3.0)]

    def test_empty_before(self):
        # GC path: everything is new and unhashable objects are fine.
        new, changed = diff_resources([], [{"a": 1}, [2]])
        assert new == [{"a": 1}, [2]]
        assert changed == []


class TestUnclosedResourceError(unittest.TestCase):
    def test_message_with_items(self):
        before = (8, [pthread(1, 0.0, 0.0)])
        after = (9, [pthread(2, 0.0, 0.0), pthread(1, 5.0, 3.0)])
        exc = UnclosedNativeThreadError("foo", before, after)
        assert exc.count == 1
        assert (
            str(exc)
            == "detected 1 unclosed native C thread after calling 'foo' 1"
            " time: before=8, after=9, diff=1"
            "\n* + pthread(id=1, user_time=5.0, system_time=3.0)"
            "\n* pthread(id=2, user_time=0.0, system_time=0.0)"
        )

    def test_message_counts_only(self):
        exc = UnclosedHeapCreateError("foo", (3, []), (5, []))
        assert exc.count == 2
        assert (
            str(exc)
            == "detected 2 unclosed HeapCreate() calls after calling 'foo' 1"
            " time: before=3, after=5, diff=2"
        )
