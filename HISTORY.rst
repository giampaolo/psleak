0.1.7 (IN DEVELOPMENT)
======================

XXXX-XX-XX

**Leak detection**

- b315c85, 223c0de: all memory metrics are now judged by the same fade rule,
  each with its own noise floor instead of a single global one: 16 bytes for
  ``heap``, 1024 for ``uss``/``rss``/``vms``, 4096 for ``mmap``.
- d54fc79: page metrics are now judged on how much memory they retained since
  the first run, instead of on the growth of each single run. Memory reclaimed
  by the trim callback is cycling, not leaking, and no longer fails.
- d54fc79: the two negligible runs needed to pass no longer have to be
  consecutive: periodic noise never produced two in a row.
- 5bd192c: the ``times`` escalation is now capped at 400, so leaky functions
  fail sooner.
- 3a06853: dropped the 20% run-over-run shortcut from the fade heuristic.
- 11_: new ``refcounts`` checker (enabled by default): the refcounts of the
  arguments passed to ``execute()`` are sampled before and after the call. A
  function which permanently gains or loses references to them (e.g. a
  ``Py_INCREF`` / ``Py_DECREF`` imbalance in a C extension) raises the new
  ``RefcountError``. These bugs don't show up as memory growth, so the memory
  checker can't see them. Python >= 3.12 only.

**API**

- f0d9d57: new ``counter_retries`` attribute (default 5). Resource counter
  checks (fds, handles, threads) are now retried against a fresh baseline
  before failing, so a one-time lazy allocation emits a ``ResourceWarning``
  instead of failing.
- ef67aad: ``times`` and ``warmup_times`` are now cast to ``int``, so float
  values are accepted.

**Error messages**

- d8a6dd1: ``Unclosed*Error`` messages now show a ``before=/after=/diff=``
  count summary followed by the list of newly leaked resources.
- 324ca75: ``MemoryLeakError`` messages now also show the initial and final
  memory snapshots.

**Fixes**

- d2897b0: the open FDs baseline is now taken the first time a test runs,
  instead of in ``__init__``, so tests which pytest only collects don't take
  one.
- 9ce5bfe: fixed ``DeprecationWarning`` on psutil 8.0.
- 8d9d6f2: ``open_files()`` is used again on Windows when psutil >= 8.0 is
  installed.

0.1.6
=====

2026-07-23

**Leak detection**

- 9_: rewrote the leak detection heuristic: it now looks at the average memory
  growth per call instead of the absolute growth per run, making it much
  harder for noise to fool it. Detection is now reliable also when tests
  are run in parallel.
- 9_: the number of calls now escalates geometrically (x1.5 per run) instead of
  linearly.
- 9_: tests can now run in parallel via pytest-xdist; the warning previously
  emitted when running inside a worker is gone.

**API**

- 9_: ``execute()`` now rejects ``times`` < 2 and ``retries`` < 1: those values
  silently disabled parts of the detection.

**Compatibility**

- 64386f2: dropped support for Python 3.6 and 3.7 (now requires Python 3.8+).
- 184fa17, f236bff: compatibility with psutil 8.0.

0.1.5
=====

2026-01-07

**Enhancements**

- 3f64fad: automatically skip test if ``PYTHONMALLOC=malloc`` env var is not
  set.
- 99384eb: emit a warning if ``psutil.heap_info()`` appears disabled on this
  platform (``heap_used`` is 0).

**Fixes**

- f4814a7: auto_generate: in case of child class inheriting from another
  MemoryLeakTestCase parent, raise error for duplicate test only if the test
  case if defined in the child class, not the parent.

0.1.4
=====

2026-01-05

**API**

- 7_: add ``MemoryLeakTestCase.auto_generate``, to auto-generate test methods
  from a declarative specification.
- b00462e: set default ``MemoryLeakTestCase.verbosity`` to 0.

**Leak detection**

- 96207d6: warm internal python caches before starting measurements (avoid
  possible false positives on the very first run)

0.1.3
=====

2025-12-29

**Enhancements**

- 4_: emit warning if `psutil.heap_info()` is not available.

**Fixes**

- 5_: can't install on Python 3.8 due to 'license' key in pyproject.toml not
  being compatible across Python versions.

0.1.2
=====

2025-12-24

**Packaging**

- 3_: the source distribution was missing a lot of files due to MANIFEST.in not
  being present.
- 2_: list test dependencies in pyproject.toml so that they can be installed
  via `pip install psleak[test]`.

0.1.1
=====

2025-12-23

**Fixes**

* 77f69ce: fix ``TypeError: dataclass() got an unexpected keyword argument
  'slots'``.

0.1.0
=====

2025-12-21

* initial release

.. _1: https://github.com/giampaolo/psleak/issues/1
.. _2: https://github.com/giampaolo/psleak/issues/2
.. _3: https://github.com/giampaolo/psleak/issues/3
.. _4: https://github.com/giampaolo/psleak/issues/4
.. _5: https://github.com/giampaolo/psleak/issues/5
.. _6: https://github.com/giampaolo/psleak/issues/6
.. _7: https://github.com/giampaolo/psleak/issues/7
.. _8: https://github.com/giampaolo/psleak/issues/8
.. _9: https://github.com/giampaolo/psleak/issues/9
.. _10: https://github.com/giampaolo/psleak/issues/10
.. _11: https://github.com/giampaolo/psleak/issues/11
.. _12: https://github.com/giampaolo/psleak/issues/12
.. _13: https://github.com/giampaolo/psleak/issues/13
.. _14: https://github.com/giampaolo/psleak/issues/14
.. _15: https://github.com/giampaolo/psleak/issues/15
.. _16: https://github.com/giampaolo/psleak/issues/16
.. _17: https://github.com/giampaolo/psleak/issues/17
.. _18: https://github.com/giampaolo/psleak/issues/18
.. _19: https://github.com/giampaolo/psleak/issues/19
.. _20: https://github.com/giampaolo/psleak/issues/20
.. _21: https://github.com/giampaolo/psleak/issues/21
.. _22: https://github.com/giampaolo/psleak/issues/22
.. _23: https://github.com/giampaolo/psleak/issues/23
.. _24: https://github.com/giampaolo/psleak/issues/24
.. _25: https://github.com/giampaolo/psleak/issues/25
.. _26: https://github.com/giampaolo/psleak/issues/26
.. _27: https://github.com/giampaolo/psleak/issues/27
.. _28: https://github.com/giampaolo/psleak/issues/28
.. _29: https://github.com/giampaolo/psleak/issues/29
.. _30: https://github.com/giampaolo/psleak/issues/30
