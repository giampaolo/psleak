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
verifies that it does not leak memory, file descriptors, handles, or
threads (Python or native). It is primarily aimed at **testing C
extension modules**, but works for pure Python functions as well.

=======================================================================
Memory leak detection
=======================================================================

The framework measures the process's memory usage before and after
repeatedly calling the target function. It monitors the following
memory metrics:

* RSS, VMS, USS from `psutil.Process.memory_full_info()`
* Heap metrics: `heap_used` and `mmap_used` from `psutil.heap_info()`
* Windows native heap count (`HeapCreate` / `HeapDestroy`)

The goal is to catch cases where C native code allocates memory without
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

* **Python threads:** `threading.Thread` objects that were `start()`ed
  but never `join()`ed or otherwise stopped.

* **Native system threads:** low-level threads created directly via
  `pthread_create()` or `CreateThread()` (Windows) that remain running
  or unjoined. These are not Python `threading.Thread` objects but OS
  threads started by C extensions without a matching `pthread_join()`
  or `WaitForSingleObject()`.

* **Temporary files and directories:** those created via the `tempfile`
  module that remain on disk after the function returns. These indicate
  missing cleanup such as `os.remove()` or `shutil.rmtree()`.

* **Subprocesses**: any `subprocess.Popen()` objects that are still
  running or have open stdin/stdout/stderr after the function returns.

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
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass

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
            msg = msg + ": " + ", ".join([repr(x) for x in extras])
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


class UndeletedTempfileError(UnclosedResourceError):
    """Raised when a temporary file created via the tempfile module
    remains on disk after calling function once. Indicates missing
    such as os.remove().
    """

    verb = "undeleted"
    resource_name = "tempfile"


class UndeletedTempdirError(UnclosedResourceError):
    """Raised when a temporary directory created via tempfile remains
    on disk after calling function once. Indicates missing cleanup such
    as shutil.rmtree().
    """

    verb = "undeleted"
    resource_name = "tempdir"


class UnclosedSubprocessError(UnclosedResourceError):
    """Raised when a subprocess.Popen() created during the function
    call is still running or has open stdin/stdout/stderr pipes after
    the function returns. Detects forgotten terminate() or wait().
    """

    resource_name = "subprocess.Popen()"


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


# --- monkey patch tempfile module


class PatchedTempfile:
    """Monkey patch tempfile module to track created temp dirs/files."""

    def __init__(self):
        self._tracked_files = set()
        self._tracked_dirs = set()
        self._orig_mkdtemp = None
        self._orig_mkstemp_inner = None

    def patch(self):
        """Patch tempfile functions to track creations."""

        def _patched_mkdtemp(*args, **kwargs):
            path = self._orig_mkdtemp(*args, **kwargs)
            self._tracked_dirs.add(path)
            return path

        def _patched_mkstemp_inner(*args, **kwargs):
            fd, path = self._orig_mkstemp_inner(*args, **kwargs)
            self._tracked_files.add(path)
            return fd, path

        if self._orig_mkdtemp is not None:
            return  # already patched

        self._orig_mkdtemp = tempfile.mkdtemp
        self._orig_mkstemp_inner = tempfile._mkstemp_inner

        tempfile.mkdtemp = _patched_mkdtemp
        tempfile._mkstemp_inner = _patched_mkstemp_inner

    def unpatch(self):
        """Restore original tempfile functions and clear tracking."""
        if self._orig_mkdtemp is None:
            return  # not patched

        tempfile.mkdtemp = self._orig_mkdtemp
        tempfile._mkstemp_inner = self._orig_mkstemp_inner
        self._orig_mkdtemp = None
        self._orig_mkstemp_inner = None
        self._tracked_dirs.clear()
        self._tracked_files.clear()

    def leaked_files(self):
        return [p for p in self._tracked_files if os.path.isfile(p)]

    def leaked_dirs(self):
        return [p for p in self._tracked_dirs if os.path.isdir(p)]

    def check(self, fun):
        """Check if orphaned files/dirs were left behind and raise
        exception.
        """
        files, dirs = self.leaked_files(), self.leaked_dirs()
        if files:
            raise UndeletedTempfileError(
                len(files), qualname(fun), extras=files
            )
        if dirs:
            raise UndeletedTempdirError(len(dirs), qualname(fun), extras=dirs)


# --- monkey patch subprocess.Popen


class PatchedSubprocess:
    """Monkey patch subprocess.Popen to track created processes."""

    def __init__(self):
        self._tracked_procs = set()
        self._orig_popen = None

    def patch(self):
        """Patch subprocess.Popen to track created processes."""

        def _patched_popen(*args, **kwargs):
            proc = self._orig_popen(*args, **kwargs)
            self._tracked_procs.add(proc)
            return proc

        if self._orig_popen is not None:
            return  # already patched

        self._orig_popen = subprocess.Popen
        subprocess.Popen = _patched_popen

    def unpatch(self):
        """Restore original Popen and clear tracking."""
        if self._orig_popen is None:
            return  # not patched

        subprocess.Popen = self._orig_popen
        self._orig_popen = None
        self._tracked_procs.clear()

    def leaked_procs(self):
        """Return processes that are still running or have open pipes."""
        leaked = []
        for proc in self._tracked_procs:
            running = proc.poll() is None
            open_pipes = (
                (proc.stdin and not proc.stdin.closed)
                or (proc.stdout and not proc.stdout.closed)
                or (proc.stderr and not proc.stderr.closed)
            )
            if running or open_pipes:
                leaked.append(proc)
        return leaked

    def check(self, fun):
        """Check for processes left running or with open pipes."""
        procs = self.leaked_procs()
        if procs:
            raise UnclosedSubprocessError(
                len(procs), qualname(fun), extras=procs
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
    python_threads: bool = True
    tempfiles: bool = True
    subprocesses: bool = True

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
    # Config object which tells which checkers to run.
    checkers = Checkers()

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

    def _get_oneshot(self, checkers):
        # order matters
        d = {}
        if checkers.python_threads:
            d["python_threads"] = (
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

    def _check_oneshot(self, fun, checkers, mpatchers):
        before = self._get_oneshot(checkers)
        self.call(fun)
        after = self._get_oneshot(checkers)

        # run monkey patchers
        for mp in mpatchers:
            mp.check(fun)

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
                    "python_threads": UnclosedPythonThreadError,
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
        checkers=None,
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
        checkers = checkers if checkers is not None else self.checkers

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

        mpatchers = []
        if checkers.tempfiles:
            mpatchers.append(PatchedTempfile())
        if checkers.subprocesses:
            mpatchers.append(PatchedSubprocess())
        for mp in mpatchers:
            mp.patch()

        try:
            self._check_oneshot(fun, checkers, mpatchers)

            if checkers.memory:
                self._warmup(fun, warmup_times)
                self._check_mem(
                    fun, times=times, retries=retries, tolerance=tolerance
                )
        finally:
            for mp in mpatchers:
                mp.unpatch()

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
