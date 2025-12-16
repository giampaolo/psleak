# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Test framework to detect memory and resource leaks in Python C
extensions.
"""

import functools
import gc
import logging
import os
import sys
import threading
import unittest
import warnings
from dataclasses import dataclass

import psutil
from psutil._common import POSIX
from psutil._common import WINDOWS
from psutil._common import bytes2human
from psutil._common import print_color

thisproc = psutil.Process()
b2h = functools.partial(bytes2human, format="%(value)i%(symbol)s")


# --- exceptions


class Error(AssertionError):
    """Base class for all psleak exceptions."""


class UnclosedResourceError(Error):
    """Base class for errors raised when some resource created during a
    function call is left unclosed or unfreed afterward.
    """

    resource_name = "resource"
    verb = "unclosed"

    def __init__(self, count, fun_name, extras=None):
        self.count = count
        self.fun_name = fun_name
        self.extras = extras
        name = self.resource_name
        name += "s" if count > 1 else ""  # pluralize
        msg = (
            f"detected {count} {self.verb} {name} after calling {fun_name!r} 1"
            " time"
        )
        if extras:
            msg += ":" + "".join(f"\n  {extra}" for extra in extras)
        super().__init__(msg)


class UnclosedFdError(UnclosedResourceError):
    """Raised when an unclosed file descriptor is detected after
    calling function once. Used to detect forgotten close(). UNIX only.
    """

    resource_name = "file descriptor"


class UnclosedHandleError(UnclosedResourceError):
    """Raised when an unclosed handle is detected after calling
    function once. Used to detect forgotten CloseHandle().
    Windows only.
    """

    resource_name = "handle"


class UnclosedHeapCreateError(UnclosedResourceError):
    """Raised when test detects HeapCreate() without a corresponding
    HeapDestroy() after calling function once. Windows only.
    """

    resource_name = "HeapCreate() call"


class UnclosedNativeThreadError(UnclosedResourceError):
    """Raised when a native C thread created outside Python is running
    after calling function once. Detects pthread_create() without
    a corresponding pthread_join().
    """

    resource_name = "native C thread"


class UnclosedPythonThreadError(UnclosedResourceError):
    """Raised when a Python thread is running after calling function
    once. This indicates that a `threading.Thread` was start()ed but not
    properly join()ed or stopped.
    """

    resource_name = "Python thread"


class UncollectableGarbageError(UnclosedResourceError):
    """Raised when objects with __del__ are left in gc.garbage after a call."""

    resource_name = "uncollectable GC object"
    verb = "leaked"


class MemoryLeakError(Error):
    """Raised when a memory leak is detected after calling function
    many times. Aims to detect:

    - `malloc()` without a corresponding `free()`
    - `mmap()` without `munmap()`
    - `HeapAlloc()` without `HeapFree()` (Windows)
    - `VirtualAlloc()` without `VirtualFree()` (Windows)
    """


# --- utils


def format_run_line(idx, diffs, times):
    parts = [f"{k}={'+' + b2h(v):<6}" for k, v in diffs.items() if v > 0]
    metrics = " | ".join(parts)
    avg = "0B"
    if parts:
        first_key = next(k for k, v in diffs.items() if v > 0)
        avg = b2h(diffs[first_key] // times)
    s = f"Run #{idx:>2}: {metrics:<50} (calls={times:>4}, avg/call=+{avg})"
    if idx == 1:
        s = "\n" + s
    return s


def qualname(obj):
    """Return a human-readable qualified name for a function, method or
    class.
    """
    return getattr(obj, "__qualname__", getattr(obj, "__name__", str(obj)))


# --- GC debugger


class GCDebugger:
    """Context manager that enables DEBUG_SAVEALL and tracks gc.garbage."""

    def __enter__(self):
        self._old_debug = gc.get_debug()
        gc.set_debug(gc.DEBUG_SAVEALL)
        gc.collect()
        self.before = list(gc.garbage)
        gc.garbage.clear()
        return self

    def __exit__(self, *a, **k):
        gc.collect()
        self.after = list(gc.garbage)
        gc.garbage.clear()
        gc.set_debug(self._old_debug)

    def leaked_objects(self):
        return [obj for obj in self.after if obj not in self.before]

    def check(self, fun):
        leaked = self.leaked_objects()
        if leaked:
            type_summary = {}
            for obj in leaked:
                typename = type(obj).__name__
                type_summary[typename] = type_summary.get(typename, 0) + 1

            extras = [
                f"{typename!r} x{count}"
                for typename, count in type_summary.items()
            ]

            raise UncollectableGarbageError(
                len(leaked), qualname(fun), extras=extras
            )


# --- checkers config


@dataclass(frozen=True)
class Checkers:
    """Configuration object controlling which leak checkers are enabled."""

    # C stuff
    memory: bool = True
    fds: bool = True
    handles: bool = True
    c_threads: bool = True
    # Python stuff
    py_threads: bool = True
    gcgarbage: bool = True

    @classmethod
    def _validate(cls, check_names):
        """Validate checker names and return set of all fields."""
        all_fields = set(cls.__annotations__.keys())
        invalid = set(check_names) - all_fields
        if invalid:
            msg = f"invalid checker names: {', '.join(invalid)}"
            raise ValueError(msg)
        return all_fields

    @classmethod
    def only(cls, *checks):
        """Return a config object with only the specified checkers enabled."""
        all_fields = cls._validate(checks)
        kwargs = {f: f in checks for f in all_fields}
        return cls(**kwargs)

    @classmethod
    def exclude(cls, *checks):
        """Return a config object with the specified checkers disabled."""
        all_fields = cls._validate(checks)
        kwargs = {f: f not in checks for f in all_fields}
        return cls(**kwargs)


# ---

_warnings_emitted = False


def _emit_warnings():
    global _warnings_emitted  # noqa: PLW0603

    if _warnings_emitted:
        return

    if not os.environ.get("PYTHONMALLOC", "").startswith(
        ("malloc", "mimalloc")
    ):
        msg = (
            "PYTHONMALLOC=malloc environment variable was not set; memory leak"
            " detection may be less reliable"
        )
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

    if os.environ.get("PYTHONUNBUFFERED") != "1":
        msg = (
            "PYTHONUNBUFFERED=1 environment variable was not set; memory leak"
            " detection may be less reliable"
        )
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

    if "PYTEST_XDIST_WORKER" in os.environ:
        msg = (
            "memory leak detection is unreliable when running tests in"
            " parallel via pytest-xdist"
        )
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

    if threading.active_count() > 1:
        msg = (
            "active Python threads exist before test; memory/thread counts may"
            " be unreliable"
        )
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

    _warnings_emitted = True


class MemoryLeakTestCase(unittest.TestCase):
    # Warm-up calls before starting measurement.
    warmup_times = 10
    # Number of times to call the tested function in each iteration.
    times = 200
    # Maximum retries if memory keeps growing.
    retries = 10
    # Allowed memory growth (in bytes or per-metric) before it is
    # considered a leak.
    tolerance = 0
    # Optional callable to free caches before starting measurement.
    trim_callback = None
    # Config object which tells which checkers to run.
    checkers = Checkers()
    # 0 = no messages; 1 = print diagnostics when memory increases.
    verbosity = 1

    __doc__ = __doc__

    @classmethod
    def setUpClass(cls):
        cls._psutil_debug_orig = bool(os.getenv("PSUTIL_DEBUG"))
        psutil._set_debug(False)  # avoid spamming to stderr

    @classmethod
    def tearDownClass(cls):
        psutil._set_debug(cls._psutil_debug_orig)

    def _log(self, msg, level):
        if level <= self.verbosity:
            if WINDOWS:
                # On Windows we use ctypes to add colors. Avoid that to
                # not interfere with memory observations.
                print(msg)  # noqa: T201
            else:
                print_color(msg, color="yellow")
            # Force flush to not interfere with memory observations.
            sys.stdout.flush()

    def _trim_mem(self):
        """Release unused memory. Aims to stabilize memory measurements."""
        if self._trim_callback is not None:
            self._trim_callback()

        # flush standard streams
        for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
            stream.flush()

        # flush logging handlers
        for handler in logging.root.handlers:
            handler.flush()

        # Full garbage collection. Note: cPython does it 3 times, but
        # it seems more historical churn.
        # https://github.com/giampaolo/cpython/blob/2e27da18952/Lib/test/support/__init__.py
        gc.collect()
        if gc.garbage:
            msg = f"GC garbage is not empty: {gc.garbage}"
            raise AssertionError(msg)

        if hasattr(sys, "_clear_internal_caches"):  # python 3.13
            sys._clear_internal_caches()
        elif hasattr(sys, "_clear_type_cache"):
            sys._clear_type_cache()

        # release free heap memory back to the OS
        if hasattr(psutil, "heap_trim"):
            psutil.heap_trim()

    def _warmup(self, fun, warmup_times):
        for _ in range(warmup_times):
            self.call(fun)

    # --- getters

    def _get_counters(self, checkers):
        # order matters
        d = {}
        if checkers.py_threads:
            d["py_threads"] = (
                threading.active_count(),
                threading.enumerate(),
            )
        if POSIX and checkers.fds:
            # Slows down too much.
            # ls = []
            # try:
            #     ls.extend(thisproc.open_files())
            # except psutil.Error:
            #     pass
            # try:
            #     ls.extend(thisproc.net_connections())
            # except psutil.Error:
            #     pass
            d["num_fds"] = (thisproc.num_fds(), [])
        if WINDOWS and checkers.handles:
            d["num_handles"] = (thisproc.num_handles(), [])
        if checkers.c_threads:
            d["c_threads"] = (thisproc.num_threads(), thisproc.threads())
        if WINDOWS and checkers.memory:
            d["heap_count"] = (psutil.heap_info().heap_count, [])
        return d

    def _get_mem(self):
        mem = thisproc.memory_full_info()
        heap_used = mmap_used = 0
        if hasattr(psutil, "heap_info"):
            heap = psutil.heap_info()
            heap_used = heap.heap_used
            mmap_used = heap.mmap_used
        return {
            "heap": heap_used,
            "mmap": mmap_used,
            "uss": getattr(mem, "uss", 0),
            "rss": mem.rss,
            "vms": mem.vms,
        }

    # --- checkers

    def _check_counters(self, fun, checkers):
        before = self._get_counters(checkers)
        self.call(fun)
        after = self._get_counters(checkers)

        for what, (count_before, extras_before) in before.items():
            count_after = after[what][0]
            extras_after = after[what][1]
            diff = count_after - count_before

            if diff < 0:
                msg = (
                    f"WARNING: {what!r} decreased by {abs(diff)} after calling"
                    f" {qualname(fun)!r} 1 time"
                )
                self._log(msg, 0)

            elif diff > 0:
                extras = set(extras_after) - set(extras_before)
                mapping = {
                    "num_fds": UnclosedFdError,
                    "num_handles": UnclosedHandleError,
                    "heap_count": UnclosedHeapCreateError,
                    "py_threads": UnclosedPythonThreadError,
                    "c_threads": UnclosedNativeThreadError,
                }
                exc = mapping.get(what)
                if exc is None:
                    raise ValueError(what)
                raise exc(diff, qualname(fun), extras=extras)

    def _call_ntimes(self, fun, times):
        """Get memory samples before and after calling fun repeatedly,
        and return the diffs as a dict.
        """
        self._trim_mem()
        mem1 = self._get_mem()

        for _ in range(times):
            self.call(fun)

        self._trim_mem()
        mem2 = self._get_mem()

        diffs = {k: mem2[k] - mem1[k] for k in mem1}
        return diffs

    def _check_mem(self, fun, times, retries, tolerance):
        prev = {}
        messages = []
        if isinstance(tolerance, dict):
            tolerances = tolerance
        else:
            t = 0 if tolerance is None else tolerance
            tolerances = dict.fromkeys(self._get_mem(), t)

        base_times = times
        for idx in range(1, retries + 1):
            diffs = self._call_ntimes(fun, times)
            leaks = {k: v for k, v in diffs.items() if v > 0}

            if leaks:
                line = format_run_line(idx, leaks, times)
                messages.append(line)
                self._log(line, 1)

            # stable means:
            # * any growth is within tolerance, OR
            # * growth has stopped (no increase vs prev)
            stable = all(
                diffs[k] <= tolerances.get(k, 0) or diffs[k] <= prev.get(k, 0)
                for k in diffs
            )

            if stable:
                if idx > 1 and leaks:
                    self._log(
                        "Memory stabilized (no further growth detected)", 1
                    )
                return

            prev = diffs
            times += base_times  # double calls each retry

        msg = f"memory kept increasing after {retries} runs" + "\n".join(
            messages
        )
        raise MemoryLeakError(msg)

    def _validate_opts(
        self, warmup_times, times, retries, tolerance, trim_callback
    ):
        if warmup_times < 0:
            msg = f"warmup_times must be >= 0 (got {warmup_times})"
            raise ValueError(msg)
        if times < 1:
            msg = f"times must be >= 1 (got {times})"
            raise ValueError(msg)
        if retries < 0:
            msg = f"retries must be >= 0 (got {retries})"
            raise ValueError(msg)
        if tolerance is not None:
            if isinstance(tolerance, int):
                if tolerance < 0:
                    msg = f"tolerance must be >= 0 (got {tolerance!r})"
                    raise ValueError(msg)
            elif isinstance(tolerance, dict):
                mem_keys = self._get_mem().keys()
                for k, v in tolerance.items():
                    if k not in mem_keys:
                        msg = f"invalid tolerance key {k!r}"
                        raise ValueError(msg)
                    if v < 0:
                        msg = f"{k!r} tolerance must be >= 0 (got {v})"
                        raise ValueError(msg)
            else:
                msg = (
                    f"invalid tolerance type {type(tolerance)} (expected int "
                    "or dict)"
                )
                raise TypeError(msg)
        if trim_callback is not None and not callable(trim_callback):
            msg = f"trim_callback {trim_callback} is not callable"
            raise TypeError(msg)

    # ---

    def call(self, fun):
        return fun()

    def execute(
        self,
        fun,
        *args,
        warmup_times=None,
        times=None,
        retries=None,
        tolerance=None,
        trim_callback=None,
        checkers=None,
    ):
        """Run a full leak test on a callable. If specified, the
        optional arguments override the class attributes with the same
        name.
        """
        warmup_times = (
            warmup_times if warmup_times is not None else self.warmup_times
        )
        times = times if times is not None else self.times
        retries = retries if retries is not None else self.retries
        tolerance = tolerance if tolerance is not None else self.tolerance
        checkers = checkers if checkers is not None else self.checkers
        trim_callback = (
            trim_callback if trim_callback is not None else self.trim_callback
        )

        self._validate_opts(
            warmup_times, times, retries, tolerance, trim_callback
        )

        _emit_warnings()

        if args:
            fun = functools.partial(fun, *args)

        self._trim_callback = trim_callback

        # run check counters
        if checkers.gcgarbage:
            with GCDebugger() as gcdbg:
                self._check_counters(fun, checkers)
            gcdbg.check(fun)
        else:
            self._check_counters(fun, checkers)

        # run memory checks
        if checkers.memory:
            self._warmup(fun, warmup_times)
            self._check_mem(
                fun, times=times, retries=retries, tolerance=tolerance
            )

    def execute_w_exc(self, exc, fun, **kwargs):
        """Run MemoryLeakTestCase.execute() expecting fun() to raise
        exc on every call.

        The exception is caught so resource and memory checks can run
        normally. If `fun()` does not raise `exc` on any call, the
        test fails.
        """

        def call():
            try:
                self.call(fun)
            except exc:
                pass
            else:
                return self.fail(f"{qualname(fun)!r} did not raise {exc}")

        self.execute(call, **kwargs)
