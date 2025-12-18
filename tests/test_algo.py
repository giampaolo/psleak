# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Test memory leak detection heurisics."""

import unittest

import pytest

from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase


class DummyMemLeakTest(MemoryLeakTestCase):
    def __init__(self, diffs_seq):
        super().__init__("runTest")
        self._diffs_seq = iter(diffs_seq)
        self._printed = []

    def _call_ntimes(self, fun, times):
        return next(self._diffs_seq)

    def _log(self, msg, level):
        super()._log(msg, level)
        self._printed.append(msg)

    def printed(self):
        return "".join(self._printed)

    def runs_count(self):
        return self.printed().count("Run # ")

    def call(self, fun):
        return None


def noop():
    pass


class TestMemleakDetectionAlgo(unittest.TestCase):

    def test_increase(self):
        diffs = [
            {"heap": 1024, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 2048, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs))

    def test_decrease(self):
        diffs = [
            {"heap": 2048, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "no further growth" in t.printed()

    def test_same(self):
        diffs = [
            {"heap": 1024, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "no further growth" in t.printed()
        assert t.runs_count() == 2

    # ---

    def test_partial_decrease(self):
        # scenario: heap the same, uss decreased
        diffs = [
            {"heap": 1024, "uss": 20480, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 4096, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "no further growth" in t.printed()
        assert t.runs_count() == 2

    def test_new_metric_appears(self):
        diffs = [
            {"heap": 1024, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 8192, "rss": 4096, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs))

    def test_metric_disappears(self):
        diffs = [
            {"heap": 1024, "uss": 8192, "rss": 4096, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "no further growth" in t.printed()
