# Copyright (c) 2009, Giampaolo Rodola'. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""\
=======================================================================
About
=======================================================================

A testing framework for detecting **memory leaks** and **unclosed
resources** created by Python functions, typically those implemented in
C, Cython, or other native extensions.

The framework runs a target function under controlled conditions and
verifies that it does not leak memory, file descriptors, handles, heap
allocations, or threads (Python or native). It is primarily aimed at
**testing C extension modules**, but works for pure Python functions as
well.

=======================================================================
Memory leak detection
=======================================================================

The framework measures the process's memory usage before and after
repeatedly calling the target function. It monitors the following
memory metrics:

* RSS, VMS, USS from `psutil.Process.memory_full_info()`
* Heap metrics: `heap_used` and `mmap_used` from `psutil.heap_info()`
* Windows native heap count (`HeapCreate` / `HeapDestroy`)

The goal is to catch cases where native code allocates memory without
freeing it, such as:

* `malloc()` without `free()`
* `mmap()` without `munmap()`
* `HeapAlloc()` without `HeapFree()` (Windows)
* `VirtualAlloc()` without `VirtualFree()` (Windows)
* `HeapCreate()` without `HeapDestroy()` (Windows)

Memory usage is noisy and influenced by the OS, allocator, and garbage
collector. Therefore, a detected memory increase triggers repeated
retests with an increasing number of calls. If memory continues to
grow, a `MemoryLeakError` is raised.

This mechanism is not perfect and cannot guarantee correctness, but it
greatly helps catch deterministic leaks in native C extensions modules.

=======================================================================
Unclosed resources detection
=======================================================================

In addition to memory checks, the framework also detects resources that
are created during a single call to the target function but not
released afterward. The following categories are monitored:

* **File descriptors (UNIX):** cases like `open()` without `close()`.

* **Windows handles:** kernel objects created via calls such as
  `CreateFile()`, `CreateProcess()`, or `CreateEvent()` that are not
  released with `CloseHandle()`.

* **Windows heap objects:** `HeapCreate()` without the corresponding
  `HeapDestroy()`.

* **Python threads:** `threading.Thread` objects that were `start()`ed
  but never `join()`ed or otherwise stopped.

* **Native system threads:** low-level threads created directly via
  `pthread_create()` or `CreateThread()` (Windows) that remain running
  or unjoined. These are not Python `threading.Thread` objects but OS
  threads started by C extensions without a matching `pthread_join()`
  or `WaitForSingleObject()`.

=======================================================================
Usage example
=======================================================================

from psleak import MemoryLeakTestCase

class TestLeaks(MemoryLeakTestCase):
    def test_fun(self):
        self.execute(some_function)

-----------------------------------------------------------------------

NOTE: This class is **experimental**. Its API and detection heuristics
may change in future versions.

[1] https://gmpy.dev/blog/2016/real-process-memory-and-environ-in-python
[2] https://github.com/giampaolo/psutil/issues/1275
"""

import functools
import gc
import logging
import os
import sys
import threading
import unittest

import psutil
from psutil._common import POSIX
from psutil._common import WINDOWS
from psutil._common import bytes2human
from psutil._common import print_color

thisproc = psutil.Process()
b2h = functools.partial(bytes2human, format="%(value)i%(symbol)s")


# --- exceptions


class UnclosedResourceError(AssertionError):
    """Base class for errors raised when some resource created during a
    function call is left unclosed or unfreed afterward.
    """

    resource_name = "resource"  # override in subclasses

    def __init__(self, count, fun_name):
        self.count = count
        self.fun_name = fun_name
        name = self.resource_name
        name += "s" if count > 1 else ""  # pluralize
        msg = (
            f"detected {count} unclosed {name} after calling {fun_name!r} 1"
            " time"
        )
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


class MemoryLeakError(AssertionError):
    """Raised when a memory leak is detected after calling function
    many times. Aims to detect:

    - `malloc()` without a corresponding `free()`
    - `mmap()` without `munmap()`
    - `HeapAlloc()` without `HeapFree()` (Windows)
    - `VirtualAlloc()` without `VirtualFree()` (Windows)
    """


def format_run_line(idx, diffs, times):
    parts = [f"{k}={'+' + b2h(v):<6}" for k, v in diffs.items() if v > 0]
    metrics = " | ".join(parts)
    avg = "0B"
    if parts:
        first_key = next(k for k, v in diffs.items() if v > 0)
        avg = b2h(diffs[first_key] // times)
    s = f"Run #{idx:>2}: {metrics:<50} (calls={times:>5}, avg/call=+{avg})"
    if idx == 1:
        s = "\n" + s
    return s


def qualname(obj):
    """Return a human-readable qualified name for a function, method or
    class.
    """
    return getattr(obj, "__qualname__", getattr(obj, "__name__", str(obj)))


# ---


class MemoryLeakTestCase(unittest.TestCase):
    # Number of times to call the tested function in each iteration.
    times = 200
    # Maximum number of retries if memory growth is detected.
    retries = 5
    # Number of warm-up calls before measurements begin.
    warmup_times = 10
    # Allowed memory difference (in bytes) before considering it a leak.
    tolerance = 0
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
        # flush standard streams
        sys.stdout.flush()
        sys.stderr.flush()

        # flush logging handlers
        for handler in logging.root.handlers:
            handler.flush()

        # full garbage collection
        gc.collect()
        assert gc.garbage == []

        # release free heap memory back to the OS
        if hasattr(psutil, "heap_trim"):
            psutil.heap_trim()

    def _warmup(self, fun, warmup_times):
        for _ in range(warmup_times):
            self.call(fun)

    # --- getters

    def _get_oneshot(self):
        return {
            "num_fds": thisproc.num_fds() if POSIX else 0,
            "num_handles": thisproc.num_handles() if WINDOWS else 0,
            "py_threads": threading.active_count(),  # order matters
            "c_threads": thisproc.num_threads(),
            "heap_count": psutil.heap_info().heap_count if WINDOWS else 0,
        }

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

    def _check_oneshot(self, fun):
        before = self._get_oneshot()
        self.call(fun)
        after = self._get_oneshot()

        for what, value_before in before.items():
            value_after = after[what]
            diff = value_after - value_before

            if diff < 0:
                msg = (
                    f"WARNING: {what!r} decreased by {abs(diff)} after calling"
                    f" {qualname(fun)!r} 1 time"
                )
                self._log(msg, 0)

            elif diff > 0:
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
                raise exc(diff, qualname(fun))

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

        for idx in range(1, retries + 1):
            diffs = self._call_ntimes(fun, times)
            leaks = {k: v for k, v in diffs.items() if v > 0}

            if leaks:
                line = format_run_line(idx, leaks, times)
                messages.append(line)
                self._log(line, 1)

            stable = all(
                diffs.get(k, 0) <= tolerance
                or diffs.get(k, 0) <= prev.get(k, 0)
                for k in diffs
            )
            if stable:
                if idx > 1 and leaks:
                    self._log(
                        "Memory stabilized (no further growth detected)", 1
                    )
                return

            prev = diffs
            times *= 2  # double calls each retry

        msg = f"memory kept increasing after {retries} runs" + "\n".join(
            messages
        )
        raise MemoryLeakError(msg)

    # ---

    def call(self, fun):
        return fun()

    def execute(
        self,
        fun,
        *args,
        times=None,
        warmup_times=None,
        retries=None,
        tolerance=None,
    ):
        """Run a full leak test on a callable. If specified, the
        optional arguments override the class attributes with the same
        name.
        """
        times = times if times is not None else self.times
        warmup_times = (
            warmup_times if warmup_times is not None else self.warmup_times
        )
        retries = retries if retries is not None else self.retries
        tolerance = tolerance if tolerance is not None else self.tolerance

        if times < 1:
            msg = f"times must be >= 1 (got {times})"
            raise ValueError(msg)
        if warmup_times < 0:
            msg = f"warmup_times must be >= 0 (got {warmup_times})"
            raise ValueError(msg)
        if retries < 0:
            msg = f"retries must be >= 0 (got {retries})"
            raise ValueError(msg)
        if tolerance < 0:
            msg = f"tolerance must be >= 0 (got {tolerance})"
            raise ValueError(msg)

        if args:
            fun = functools.partial(fun, *args)

        self._check_oneshot(fun)
        self._warmup(fun, warmup_times)
        self._check_mem(fun, times=times, retries=retries, tolerance=tolerance)
