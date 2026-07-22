# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Test memory leak detection heurisics."""

import unittest

import pytest

from psleak import Checkers
from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase


class DummyMemLeakTest(MemoryLeakTestCase):
    # The scenarios below assume `times` starts at 50 and escalates
    # to 75, 100, 125, 150. Pin it instead of relying on the
    # tests/__init__.py override.
    times = 50
    # These tests are about the memory heuristic only; don't sample
    # live fd / thread counters.
    checkers = Checkers.only("memory")

    def __init__(self, diffs_seq):
        super().__init__("runTest")
        self._diffs_seq = iter(diffs_seq)
        self._printed = []
        self._ncalls = 0

    def _call_ntimes(self, fun, times):
        diffs = next(self._diffs_seq)
        # fail loudly if _get_mem's metric keys ever change
        assert set(diffs) == set(self._get_mem())
        self._ncalls += 1
        return diffs

    def _log(self, msg, level):
        super()._log(msg, level)
        self._printed.append(msg)

    def printed(self):
        return "".join(self._printed)

    def runs_count(self):
        # measurement cycles executed
        return self._ncalls

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

    # ---

    def test_times_escalation(self):
        # times grows linearly by half the initial value: 50 -> 75
        # -> 100 -> 125 -> 150. A steady 96 B/call leak scales with
        # times and never stops growing.
        seen = []

        class Recorder(DummyMemLeakTest):
            def _call_ntimes(self, fun, times):
                seen.append(times)
                return super()._call_ntimes(fun, times)

        diffs = [
            {"heap": 4800, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 7200, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 9600, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 12000, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 14400, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = Recorder(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, times=50, retries=5)
        assert seen == [50, 75, 100, 125, 150]

    def test_tolerance_dict_partial_still_detects(self):
        # heap is missing from the dict so its tolerance defaults
        # to 0: a steady heap leak must still be reported even
        # though uss is excused.
        diffs = [
            {"heap": 2400, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 3600, "uss": 8192, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs), tolerance={"uss": 8192})

    def test_all_clean_first_run(self):
        # Nothing grew: pass on the very first run, nothing printed.
        diffs = [
            {"heap": -100, "uss": -100, "rss": 0, "vms": 0, "mmap": -100},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=1)
        assert t.runs_count() == 1
        assert t.printed() == ""
