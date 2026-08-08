# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Test framework to detect memory and resource leaks in Python C
extensions.
"""

import collections
import functools
import gc
import linecache
import logging
import os
import sys
import threading
import types
import unittest
import warnings
from dataclasses import dataclass

import psutil
from psutil import POSIX
from psutil import WINDOWS
from psutil._common import bytes2human
from psutil._common import print_color

thisproc = psutil.Process()

# Per-call growth at or below this many bytes is considered noise.
# `heap` is byte-granular (glibc), so a real once-per-call malloc leak
# can't stay under 16: the smallest chunk is 32 bytes. The others move
# in whole pages and bounce by a few pages a run, which spread over
# `times` calls is a few hundred bytes each. A real page-only leak (a
# raw mmap() or a dirtied page) is a full 4096 per call, so 1024 sits
# well clear of the noise and well under the leak.
#
# `mmap` gets a higher floor. It's the allocator's own view of what
# it got from the OS, and it's noisy: FreeBSD's jemalloc reports the
# whole address space its arenas hold, which drifts by hundreds of
# pages a run. Being generous costs nothing: a leak big enough to
# land here is far above any floor, and `heap` and rss/vms catch the
# rest.
NOISE_FLOOR = 16
PAGE_NOISE_FLOOR = 1024
MMAP_NOISE_FLOOR = 4096
FLOORS = {
    "heap": NOISE_FLOOR,
    "mmap": MMAP_NOISE_FLOOR,
    "uss": PAGE_NOISE_FLOOR,
    "rss": PAGE_NOISE_FLOOR,
    "vms": PAGE_NOISE_FLOOR,
}


# --- exceptions


class Error(AssertionError):
    """Base class for all psleak exceptions."""


class UnclosedResourceError(Error):
    """Base class for errors raised when some resource created during a
    function call is left unclosed or unfreed afterward.
    """

    resource_name = "resource"
    verb = "unclosed"

    def __init__(self, fun_name, before, after):
        # `before`/`after` are (count, items) snapshots. `count` is
        # authoritative (from an exact counter); `items` may be empty
        # (e.g. heap) or a subset that can't explain the whole count.
        count_before, items_before = before
        count_after, items_after = after
        self.count = count_after - count_before
        self.fun_name = fun_name
        self.extras, self.changed = diff_resources(items_before, items_after)
        name = self.resource_name + ("s" if self.count > 1 else "")
        msg = (
            f"detected {self.count} {self.verb} {name} after calling "
            f"{fun_name!r} 1 time: "
            f"before={count_before}, after={count_after}, diff={self.count}"
        )
        # `changed` (value moved, marked "+ ") first, then new/leaked.
        lines = [f"\n* + {c!r}" for c in self.changed]
        lines += [f"\n* {e!r}" for e in self.extras]
        super().__init__(msg + "".join(lines))


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

    resource_name = "GC object"
    verb = "uncollectable"


class RefcountError(Error):
    """Raised when calling the function changes the reference count of
    one of its arguments. Typically a C extension calling Py_INCREF
    with no matching Py_DECREF, which keeps the object alive forever,
    or an extra Py_DECREF, which frees an object still in use and
    crashes the interpreter later on, usually from unrelated code.
    """

    def __init__(self, fun_name, diffs):
        self.fun_name = fun_name
        self.diffs = diffs
        self.count = sum(abs(n) for _, n in diffs)
        noun = "change" + ("s" if self.count > 1 else "")
        msg = (
            f"detected {self.count} refcount {noun} after calling"
            f" {fun_name!r} 1 time:"
        )
        lines = []
        for obj, n in diffs:
            verb = "gained" if n > 0 else "lost"
            refs = "reference" + ("s" if abs(n) > 1 else "")
            lines.append(f"\n* {verb} {abs(n)} {refs} to {obj!r}")
        super().__init__(msg + "".join(lines))


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
    parts = [f"{k}={'+' + str(v):<6}" for k, v in diffs.items() if v > 0]
    metrics = " | ".join(parts)
    avg = "0B"
    if parts:
        first_key = next(k for k, v in diffs.items() if v > 0)
        avg = str(diffs[first_key] // times)
    s = f"Run #{idx:>2}: {metrics:<50} (calls={times:>4}, avg/call=+{avg})"
    if idx == 1:
        s = "\n" + s
    return s


def format_mem(label, mem):
    """Format a memory snapshot as a human-readable line, e.g.
    'Initial: heap=1.2K, mmap=0.0B, ...'.
    """
    parts = ", ".join(f"{k}={bytes2human(v)}" for k, v in mem.items())
    return f"{label}{parts}"


def qualname(obj):
    """Return a human-readable qualified name for a function, method or
    class.
    """
    return getattr(obj, "__qualname__", getattr(obj, "__name__", str(obj)))


def warm_caches():
    """Avoid potential false positives due to various caches filling
    slowly with random data, usually happening on the very first run.
    Taken from cPython's refleak.py.
    """
    # char cache
    s = bytes(range(256))
    for i in range(256):
        s[i : i + 1]
    # unicode cache
    [chr(i) for i in range(256)]
    # int cache
    list(range(-5, 257))


def assert_isinstance(name, obj, types):
    if not isinstance(obj, types):
        if isinstance(types, tuple):
            exp = " or ".join(t.__name__ for t in types)
        else:
            exp = types.__name__
        msg = f"{name!r} must be instance of {exp} (got {obj!r})"
        raise TypeError(msg)


def diff_resources(before, after):
    """Split `after` into (new, changed) against `before`. A resource
    is keyed by its `.id` when it has one (threads), else by value, so
    a thread whose CPU time merely advanced is `changed`, not `new`.
    `new` was absent from `before` (the leak); `changed` is shown only
    for context.
    """
    if not before:
        # Empty `before` (e.g. GC's pre-filtered, possibly unhashable
        # leaks): everything is new, and we skip hashing.
        return list(after), []
    seen = {getattr(x, "id", x): x for x in before}
    new, changed = [], []
    for x in after:
        old = seen.get(getattr(x, "id", x))
        if old is None:
            new.append(x)
        elif old != x:
            changed.append(x)
    return new, changed


# --- GC debugger


class GCDebugger:
    """Detects objects that cannot be automatically garbage collected
    because they form reference cycles or define a finalizer method
    (__del__).

    Detection is performed using a context manager that temporarily
    enables gc.DEBUG_SAVEALL and tracks objects remaining in
    gc.garbage.
    """

    # Objects that are temporarily part of a cycle but are expected to
    # disappear once the cycle is broken.
    TRANSIENT_TYPES = (
        types.FrameType,
        types.TracebackType,
        type(threading.current_thread()),
        BaseException,  # ignore all exception instances
    )

    # Value-like objects that do not hold references to other Python
    # objects tracked by the GC and therefore cannot participate in
    # reference cycles.
    SCALAR_TYPES = (
        int,
        float,
        bool,
        str,
        bytes,
        bytearray,
        complex,
        type(None),
    )

    def __enter__(self):
        self._old_debug = gc.get_debug()
        gc.set_debug(gc.DEBUG_SAVEALL)
        gc.collect()
        self.before = list(gc.garbage)
        self.after = []
        gc.garbage.clear()
        return self

    def __exit__(self, *a, **k):
        gc.collect()
        self.after = list(gc.garbage)
        gc.garbage.clear()
        gc.set_debug(self._old_debug)

    def is_transient(self, obj, _seen=None):
        if _seen is None:
            _seen = set()

        oid = id(obj)
        if oid in _seen:
            return True

        _seen.add(oid)

        if isinstance(obj, self.TRANSIENT_TYPES):
            return True

        if isinstance(obj, self.SCALAR_TYPES):
            return True

        if isinstance(obj, (list, tuple, set, frozenset)):
            for o in obj:  # noqa: SIM110
                if not self.is_transient(o, _seen):
                    return False
            return True

        if isinstance(obj, dict):
            for k, v in obj.items():
                if not self.is_transient(k, _seen):
                    return False
                if not self.is_transient(v, _seen):
                    return False
            return True

        return False

    def leaked_objects(self):
        leaked = []
        for obj in self.after:
            if obj in self.before:
                continue
            if self.is_transient(obj):
                continue
            leaked.append(obj)
        return leaked

    def check(self, fun):
        leaked = self.leaked_objects()
        if leaked:
            raise UncollectableGarbageError(
                qualname(fun), (0, []), (len(leaked), leaked)
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
    refcounts: bool = True

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

    def warn(msg, suffix="memory leak detection may be less reliable"):
        if suffix:
            msg += "; " + suffix
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

    if _warnings_emitted:
        return

    if not hasattr(psutil, "heap_info"):  # SunOS, OpenBSD
        warn("psutil.heap_info() not available on this platform")
    elif psutil.heap_info().heap_used == 0:
        warn("psutil.heap_info() appears disabled on this platform")

    if os.environ.get("PYTHONUNBUFFERED") != "1":
        warn("PYTHONUNBUFFERED=1 environment variable was not set")

    if threading.active_count() > 1:
        warn(
            "active Python threads exist before test; memory/thread counts may"
            f" be unreliable: {threading.enumerate()}",
            suffix="",
        )

    _warnings_emitted = True


class _FdsBaseline:
    """The list of open FDs a leak is reported against. It's shared by
    all tests, and taken the first time one of them runs: doing it at
    import or in __init__ would also hit the tests pytest merely
    collects and never executes.
    """

    _cached = None

    @staticmethod
    def _get():
        ls = []
        # open_files() on Windows in psutil < 8.0 is too slow
        if not WINDOWS or psutil.version_info >= (8, 0):
            try:
                ls.extend(thisproc.open_files())
            except psutil.Error:
                pass
        try:
            ls.extend(thisproc.net_connections(kind="all"))
        except psutil.Error:
            pass
        return ls

    @classmethod
    def get(cls):
        # Take the baseline the first time it's asked for.
        if cls._cached is None:
            cls._cached = cls._get()
        return cls._cached

    @classmethod
    def refresh(cls):
        # Re-take it, done after a leak is detected.
        cls._cached = cls._get()
        return cls._cached


class LeakTest:
    """Small helper object to use in conjunction with
    ``MemoryLeakTestCase.auto_generate``.
    """

    __slots__ = ("args", "execute_kwargs", "fun")

    def __init__(self, fun, *args, **execute_kwargs):
        assert_isinstance("fun", fun, collections.abc.Callable)
        self.fun = fun
        self.args = args
        self.execute_kwargs = dict(execute_kwargs)

    def _make_callable(self):
        if self.args:
            return functools.partial(self.fun, *self.args)
        return self.fun


_TIMES = 200


class MemoryLeakTestCase(unittest.TestCase):
    # Warm-up calls before starting measurement.
    warmup_times = 10
    # Number of times to call the tested function in each iteration.
    times = _TIMES
    # Maximum retries if memory keeps growing.
    retries = 10
    # Maximum retries for the resource counter checks (fds, handles,
    # C threads).
    counter_retries = 5
    # Allowed memory growth (in bytes or per-metric) before it is
    # considered a leak.
    tolerance = 0
    # Optional callable to free caches before starting measurement.
    trim_callback = None
    # Config object which tells which checkers to run.
    checkers = Checkers()
    # 0 = no messages; 1 = print diagnostics when memory increases.
    verbosity = 0

    __doc__ = __doc__

    @classmethod
    def auto_generate(cls):
        """Return a dict {name: LeakTest}. Override in subclasses."""
        return {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        calls = cls.auto_generate()
        if not isinstance(calls, dict):
            msg = f"{cls.__name__}.auto_generate must return a dict"
            raise TypeError(msg)

        for name, entry in calls.items():
            if not isinstance(entry, LeakTest):
                msg = (
                    f"{cls.__name__}.auto_generate()[{name!r}] must be a"
                    " LeakTest"
                )
                raise TypeError(msg)

            test_name = f"test_leak_{name}"
            if test_name in cls.__dict__:
                msg = f"{cls.__name__} already defines {test_name}"
                raise RuntimeError(msg)

            fun = entry._make_callable()
            execute_kwargs = dict(entry.execute_kwargs)

            def make_test(fun, execute_kwargs, test_name=test_name, name=name):
                def test(self):
                    self.execute(fun, **execute_kwargs)

                test.__name__ = test_name
                test.__qualname__ = test_name
                test.__doc__ = f"Auto-generated leak test for {name}"
                return test

            setattr(cls, test_name, make_test(fun, execute_kwargs))

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

        sys.path_importer_cache.clear()
        linecache.cache.clear()

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
        # Only on 3.12+, where shared objects like small ints are
        # immortal (PEP 683). On older versions an argument like `1`
        # has a refcount that moves on its own: false positives.
        if checkers.refcounts and sys.version_info >= (3, 12):
            d["refcounts"] = (
                [sys.getrefcount(x) for x in self._watched],
                self._watched,
            )
        if checkers.py_threads:
            d["py_threads"] = (
                threading.active_count(),
                threading.enumerate(),
            )
        if POSIX and checkers.fds:
            d["num_fds"] = (thisproc.num_fds(), _FdsBaseline.get())
        if WINDOWS and checkers.handles:
            d["num_handles"] = (thisproc.num_handles(), _FdsBaseline.get())
        if checkers.c_threads:
            d["c_threads"] = (thisproc.num_threads(), thisproc.threads())
        if WINDOWS and checkers.memory:
            d["heap_count"] = (psutil.heap_info().heap_count, [])
        return d

    def _get_mem(self):
        # `heap` and `mmap` come from the allocator's own accounting.
        # `uss`/`rss`/`vms` are OS-level: page-granular and noisier, but
        # the only ones that catch a raw mmap() or page-dirtying leak,
        # which never touches the allocator. _check_mem judges them all
        # the same way, just with a higher noise floor for the coarse
        # ones (see FLOORS).
        if hasattr(psutil, "heap_info"):
            heap = psutil.heap_info()
            heap_used = heap.heap_used
            mmap_used = heap.mmap_used
        else:
            heap_used = mmap_used = 0

        if hasattr(thisproc, "memory_footprint"):  # psutil 8+
            m = thisproc.memory_footprint()
        elif psutil.version_info < (8, 0):
            m = thisproc.memory_full_info()
        else:
            m = None
        uss = getattr(m, "uss", 0)

        rss, vms = thisproc.memory_info()[:2]

        return {
            "heap": heap_used,
            "mmap": mmap_used,
            "uss": uss,
            "rss": rss,
            "vms": vms,
        }

    # --- checkers

    def _check_counters(self, fun, checkers):
        # A one-time lazy allocation made by another thread can land in
        # the measurement window and look like a leak. A real leak
        # shows up on every call, so retry N times before failing.
        last_err = None
        for attempt in range(1, self.counter_retries + 1):
            # `_check_counters_once` returns the error instead of
            # raising it: catching it here would leave an exception ->
            # frame cycle in gc.garbage, tripping the GC checker.
            err = self._check_counters_once(fun, checkers)
            if err is None:
                if last_err is not None:
                    msg = (
                        "one-time resource allocation absorbed by"
                        f" re-baselining, not treated as a leak: {last_err}"
                    )
                    warnings.warn(msg, ResourceWarning, stacklevel=2)
                return
            if attempt == self.counter_retries:
                raise err
            last_err = err
            msg = (
                f"{type(err).__name__} (diff={err.count}), retrying"
                " with a fresh baseline"
            )
            self._log(msg, 1)

    def _check_counters_once(self, fun, checkers):
        before = self._get_counters(checkers)
        self.call(fun)
        after = self._get_counters(checkers)

        for what, (count_before, extras_before) in before.items():
            count_after = after[what][0]
            extras_after = after[what][1]

            if what == "refcounts":
                diffs = [
                    (obj, a - b)
                    for obj, b, a in zip(
                        extras_before, count_before, count_after
                    )
                    if a != b
                ]
                if diffs:
                    return RefcountError(qualname(fun), diffs)
                continue

            diff = count_after - count_before

            if diff < 0:
                msg = (
                    f"WARNING: {what!r} decreased by {abs(diff)} after calling"
                    f" {qualname(fun)!r} once"
                )
                self._log(msg, 0)

            elif diff > 0:
                if what in {"num_fds", "num_handles"}:
                    # fetch fds and update cache only in case of failure
                    extras_after = _FdsBaseline.refresh()

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
                return exc(
                    qualname(fun),
                    (count_before, extras_before),
                    (count_after, extras_after),
                )
        return None

    def _call_ntimes(self, fun, times):
        """Get memory samples before and after calling fun repeatedly.
        Return (diffs, before, after) where diffs is the per-metric
        growth and before/after are the absolute snapshots.
        """
        self._trim_mem()
        mem1 = self._get_mem()

        for _ in range(times):
            self.call(fun)

        self._trim_mem()
        mem2 = self._get_mem()

        diffs = {k: mem2[k] - mem1[k] for k in mem1}
        return diffs, mem1, mem2

    def _check_mem(self, fun, times, retries, tolerance):
        negligible_runs = 0
        messages = []
        initial = final = None
        if isinstance(tolerance, dict):
            tolerances = tolerance
        else:
            tolerances = dict.fromkeys(self._get_mem(), tolerance)

        for idx in range(1, retries + 1):
            diffs, mem1, mem2 = self._call_ntimes(fun, times)
            if initial is None:
                initial = mem1
            final = mem2
            leaks = {k: v for k, v in diffs.items() if v > 0}

            if leaks:
                line = format_run_line(idx, leaks, times)
                messages.append(line)
                self._log(line, 1)

            avg = {k: diffs[k] / times for k in diffs}

            # A real leak wastes memory on every call, so its per-call
            # average holds no matter how big `times` gets. Noise
            # doesn't: it's a fixed burst per run, so spread over more
            # and more calls it sinks below the floor. So we escalate
            # `times` and pass once growth is negligible. Page-granular
            # metrics bounce by whole pages, so they get a higher floor
            # than byte-granular `heap`.
            clean = all(diffs[k] <= tolerances.get(k, 0) for k in diffs)
            if not clean:
                negligible = all(
                    diffs[k] <= tolerances.get(k, 0) or avg[k] <= FLOORS[k]
                    for k in diffs
                )
                # Two negligible runs are enough that one lucky reading
                # can't clear a leaky test. They needn't be consecutive:
                # a strictly periodic signal never gives two in a row,
                # which would fail a clean function forever.
                if negligible:
                    negligible_runs += 1

            if clean or negligible_runs >= 2:
                if idx > 1 and leaks:
                    self._log("Memory stabilized (growth per call faded)", 1)
                return

            # Escalating `times` dilutes noise; a real leak wastes the
            # same bytes per call however long we run, so growing
            # forever only slows the test. So we cap it. For fast
            # functions (default `times` 200) the cap bites quickly:
            #     200, 300, 400, 400, 400, ...
            # Slow functions start lower (e.g. 20) and are the noisiest,
            # so they need more room; a flat cap, not a multiple of
            # `times`, gives it to them:
            #     20, 30, 45, 67, 100, 150, 225, 337, 400, 400, ...
            times = min(int(times * 1.5), _TIMES * 2)

        msg = (
            f"memory kept increasing after {retries} runs"
            + "\n".join(messages)
            + "\n"
            + format_mem("Initial: ", initial)
            + "\n"
            + format_mem("Final  : ", final)
        )
        raise MemoryLeakError(msg)

    def _parse_opts(
        self, warmup_times, times, retries, tolerance, trim_callback, checkers
    ):
        """Fall back to the class attributes for the options left to
        None, then validate and normalize them. Return them as a tuple.
        """
        if warmup_times is None:
            warmup_times = self.warmup_times
        if times is None:
            times = self.times
        if retries is None:
            retries = self.retries
        if tolerance is None:
            tolerance = self.tolerance
        if trim_callback is None:
            trim_callback = self.trim_callback
        if checkers is None:
            checkers = self.checkers

        warmup_times = int(warmup_times)
        times = int(times)

        assert_isinstance("retries", retries, int)
        assert_isinstance("tolerance", tolerance, (int, dict))
        if trim_callback is not None:
            assert_isinstance(
                "trim_callback", trim_callback, collections.abc.Callable
            )

        if warmup_times < 0:
            msg = f"warmup_times must be >= 0 (got {warmup_times})"
            raise ValueError(msg)
        if times < 2:
            # int(1 * 1.5) == 1: with times=1 the escalation would
            # be a no-op
            msg = f"times must be >= 2 (got {times})"
            raise ValueError(msg)
        if retries < 1:
            msg = f"retries must be >= 1 (got {retries})"
            raise ValueError(msg)
        if tolerance is not None:
            if isinstance(tolerance, int):
                if tolerance < 0:
                    msg = f"tolerance must be >= 0 (got {tolerance!r})"
                    raise ValueError(msg)
            else:
                mem_keys = self._get_mem().keys()
                for k, v in tolerance.items():
                    if k not in mem_keys:
                        msg = f"invalid tolerance key {k!r}"
                        raise ValueError(msg)
                    if v < 0:
                        msg = f"{k!r} tolerance must be >= 0 (got {v})"
                        raise ValueError(msg)

        return warmup_times, times, retries, tolerance, trim_callback, checkers

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
        (
            warmup_times,
            times,
            retries,
            tolerance,
            trim_callback,
            checkers,
        ) = self._parse_opts(
            warmup_times, times, retries, tolerance, trim_callback, checkers
        )

        if checkers.memory and os.environ.get("PYTHONMALLOC", "") != "malloc":
            msg = "PYTHONMALLOC=malloc was not set"
            raise unittest.SkipTest(msg)

        _emit_warnings()
        warm_caches()
        _FdsBaseline.get()

        self._watched = args
        if args:
            fun = functools.partial(fun, *args)

        self._trim_callback = trim_callback

        # Resource counters (fds, handles, threads, heap) are checked
        # with NO warm-up, unlike memory below: they're exact, so
        # nothing needs settling. A failure is retried with a fresh
        # baseline though (see _check_counters), so a resource
        # allocated once and kept alive on purpose (lazy init) is
        # only logged/warned, not reported as a leak.
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

    def execute_w_exc(self, exc, fun, *args, **kwargs):
        """Run MemoryLeakTestCase.execute() expecting fun() to raise
        exc on every call.

        The exception is caught so resource and memory checks can run
        normally. If `fun()` does not raise `exc` on any call, the
        test fails.
        """

        def call(*args):  # noqa: ARG001
            try:
                self.call(fun)
            except exc:
                pass
            else:
                return self.fail(f"{qualname(fun)!r} did not raise {exc}")

        if args:
            fun = functools.partial(fun, *args)

        self.execute(call, *args, **kwargs)
