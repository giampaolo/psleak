# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Test memory leak detection heurisics."""

import mmap
import unittest

import pytest

from psleak import NOISE_PAGES
from psleak import Checkers
from psleak import MemoryLeakError
from psleak import MemoryLeakTestCase


class DummyMemLeakTest(MemoryLeakTestCase):
    # The scenarios below assume `times` starts at 50 and escalates
    # to 75, 112, 168, 252. Pin it instead of relying on the
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
        mem1 = self._get_mem()
        # fail loudly if _get_mem's metric keys ever change
        assert set(diffs) == set(mem1)
        self._ncalls += 1
        mem2 = {k: mem1[k] + diffs[k] for k in mem1}
        return diffs, mem1, mem2

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


class RetainedMemLeakTest(DummyMemLeakTest):
    """Feeds absolute memory trajectories so the retained logic
    (mem2 - initial) can be exercised. Each run is {metric:
    (start, end)} net bytes above a fixed baseline, so the per-run diff
    is end - start while retained is end. The plain Dummy can't model
    this: it re-samples real memory every run, making mem2 - initial
    meaningless.
    """

    _BASE = dict.fromkeys(("heap", "uss", "rss", "vms", "mmap"), 100 * 1024**2)

    def _call_ntimes(self, fun, times):
        run = next(self._diffs_seq)
        assert set(run) == set(self._BASE)
        self._ncalls += 1
        mem1 = {k: self._BASE[k] + run[k][0] for k in self._BASE}
        mem2 = {k: self._BASE[k] + run[k][1] for k in self._BASE}
        return {k: mem2[k] - mem1[k] for k in mem1}, mem1, mem2


def noop():
    pass


PAGE = mmap.PAGESIZE


def run(**offsets):
    # {metric: (start, end)} offsets from baseline; unset metrics flat
    keys = ("heap", "uss", "rss", "vms", "mmap")
    return {k: offsets.get(k, (0, 0)) for k in keys}


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
            {"heap": 1024, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "growth per call faded" in t.printed()

    def test_same(self):
        diffs = [
            {"heap": 1024, "uss": 1000, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 1000, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 1000, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "growth per call faded" in t.printed()
        assert t.runs_count() == 3

    # ---

    def test_partial_decrease(self):
        # scenario: heap the same, uss decreased
        diffs = [
            {"heap": 1024, "uss": 2000, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 1000, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 500, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "growth per call faded" in t.printed()
        assert t.runs_count() == 3

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
            {"heap": 1024, "uss": 1000, "rss": 4096, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 1000, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1024, "uss": 1000, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "growth per call faded" in t.printed()

    # --- per-call average heuristics

    def test_spike_then_plateau(self):
        # A noise spike inflating one run must not mask a real leak:
        # the per-call average stays flat afterwards, meaning growth
        # never actually stopped.
        diffs = [
            {"heap": 4400, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 3600, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 5400, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 8100, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs))

    def test_steady_leak(self):
        # Constant per-call leak: absolute growth scales with `times`
        # and the per-call average never fades.
        diffs = [
            {"heap": 2400, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 3600, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 5400, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs))

    def test_alternating_noise_passes(self):
        # Real trace from a parallel run: clean code, but every other
        # cycle catches a ~1.8KB noise burst. The two negligible runs
        # it needs are runs 1 and 3, which are not adjacent: requiring
        # them back to back would never pass a signal like this.
        diffs = [
            {"heap": 176, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1840, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 176, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1840, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 176, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "growth per call faded" in t.printed()
        assert t.runs_count() == 3

    def test_bouncy_noise_passes(self):
        # Clean but noisy readings bounce in absolute terms; the
        # `times` escalation still dilutes their per-call average, so
        # they must pass.
        diffs = [
            {"heap": 416, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 380, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 450, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert "growth per call faded" in t.printed()

    # --- retained page-metric logic

    def test_cycling_page_metric_passes(self):
        # A real net_connections trace: uss posts big per-run growth
        # yet what it holds above baseline stays a few dozen pages
        # because _trim_mem() reclaims it. The 140-page diff would trip
        # a per-run tolerance, so this pins the retained rule.
        traj = [
            run(uss=(0, 50 * PAGE)),  # retained 50 pages
            run(uss=(-100 * PAGE, 40 * PAGE)),  # diff 140 pages, retained 40
        ]
        t = RetainedMemLeakTest(traj)
        t.execute(noop, retries=len(traj))
        assert "growth per call faded" in t.printed()
        assert t.runs_count() == 2

    def test_accumulating_page_metric_fails(self):
        # uss retains more every run and never gives it back (one page
        # per call, like a real rss-only leak): retained climbs without
        # bound, so it must be flagged.
        traj = [run(uss=(0, 200 * (i + 1) * PAGE)) for i in range(4)]
        t = RetainedMemLeakTest(traj)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(traj))

    # --- fade rule details

    def test_single_negligible_run_is_not_enough(self):
        # Only run 1 is negligible (8 B/call); the rest stay well over
        # the floor. One clean reading must not clear a leaky test.
        diffs = [
            {"heap": 400, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 3000, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 3360, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 6720, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 7560, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs))
        assert t.runs_count() == 5

    def test_all_clean_fast_path(self):
        # Nothing grew: pass on the very first run via the tolerance
        # fast path, no fade streak needed and nothing printed.
        diffs = [
            {"heap": -100, "uss": -100, "rss": 0, "vms": 0, "mmap": -100},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=1)
        assert t.runs_count() == 1
        assert t.printed() == ""

    def test_clean_run_after_leak_is_quiet(self):
        # A fully clean run passes via the fast path right after a
        # leaky one, but with no growth there is no stabilized msg.
        diffs = [
            {"heap": 1024, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 0, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert t.runs_count() == 2
        assert "growth per call faded" not in t.printed()

    def test_just_above_floor_leak_raises(self):
        # 17 B/call flat (850/50, 1275/75, 1904/112) sits over the
        # floor and never drops 20%, so it must be flagged.
        diffs = [
            {"heap": 850, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1275, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1904, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs))

    def test_times_escalation(self):
        # times grows 50 -> 75 -> 112 -> 168 -> 252 (int truncation:
        # int(112.5) == 112). A steady 96 B/call leak scales with
        # times and never fades.
        seen = []

        class Recorder(DummyMemLeakTest):
            def _call_ntimes(self, fun, times):
                seen.append(times)
                return super()._call_ntimes(fun, times)

        diffs = [
            {"heap": 4800, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 7200, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 10752, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 16128, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 24192, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = Recorder(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, times=50, retries=5)
        assert seen == [50, 75, 112, 168, 252]

    def test_avg_exactly_at_noise_floor(self):
        # 800/50 and 1200/75 are both exactly 16.0 B/call: the noise
        # floor is inclusive, so both runs fade and the test passes.
        diffs = [
            {"heap": 800, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 1200, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs))
        assert t.runs_count() == 2
        assert "growth per call faded" in t.printed()

    def test_tolerance_dict_excuses_metric(self):
        # uss is flat and well past what the page tolerance forgives,
        # but excused by its own tolerance while heap fades under the
        # floor, so two runs are enough. The same trace without the
        # dict must fail in two runs.
        big = 2 * NOISE_PAGES * mmap.PAGESIZE
        diffs = [
            {"heap": 400, "uss": big, "rss": 0, "vms": 0, "mmap": 0},
            {"heap": 400, "uss": big, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        t.execute(noop, retries=len(diffs), tolerance={"uss": big})
        assert t.runs_count() == 2
        assert "growth per call faded" in t.printed()

        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=len(diffs))

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

    def test_retries_one_needs_fast_path(self):
        # With retries=1 the fade streak can never reach 2, so any
        # growth at all fails, even sub-floor noise.
        diffs = [
            {"heap": 400, "uss": 0, "rss": 0, "vms": 0, "mmap": 0},
        ]
        t = DummyMemLeakTest(diffs)
        with pytest.raises(MemoryLeakError):
            t.execute(noop, retries=1)
