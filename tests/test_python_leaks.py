# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import gc
import threading

import pytest

from psleak import GCDebugger
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


class TestGCDebugger(MemoryLeakTestCase):
    def test_detects_simple_cycle(self):
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

    def test_ignores_exception_objects(self):
        def create_exception():
            try:
                raise ValueError("test")  # noqa: TRY301
            except ValueError as e:
                err = e  # stored in local variable
            return err

        self.execute(create_exception)

    def test_is_transient_ignores(self):
        with GCDebugger() as dbg:
            # scalar objects
            for obj in [1, 2.0, True, "foo", b"bytes", None]:
                assert dbg.is_transient(obj)
            # MainThread
            assert dbg.is_transient(threading.current_thread())
            # exceptions
            assert dbg.is_transient(ValueError())

    def test_nested_containers_with_transient_objects(self):
        t = threading.current_thread()
        nested = [1, (2, [t]), {3, 4}, {"x": t}]

        with GCDebugger() as dbg:
            dbg.after.extend(nested)

        # All objects should be classified as transient because
        # contents are either scalar or transient.
        for obj in nested:
            assert dbg.is_transient(obj)
