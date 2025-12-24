|github-actions| |version| |license|

.. |github-actions| image:: https://img.shields.io/github/actions/workflow/status/giampaolo/psleak/.github/workflows/tests.yml.svg
    :target: https://github.com/giampaolo/psleak/actions
    :alt: CI status

.. |version| image:: https://img.shields.io/pypi/v/psleak.svg?label=pypi
    :target: https://pypi.org/project/psleak
    :alt: Latest version

.. |license| image:: https://img.shields.io/pypi/l/psleak
    :target: https://github.com/giampaolo/psleak/blob/master/LICENSE
    :alt: License

psleak
======

A testing framework for detecting **memory leaks** and **unclosed resources**
created by Python functions, particularly those **implemented in C or other
native extensions**.

It was originally developed as part of `psutil`_ test suite, and later split
out into a standalone project.

**Note**: this project is still experimental. API and internal heuristics may
change.

Features
========

Memory leak detection
^^^^^^^^^^^^^^^^^^^^^

The framework measures process memory before and after repeatedly calling a
function, tracking:

- Heap metrics from `psutil.heap_info`_
- USS, RSS and VMS from `psutil.Process.memory_full_info`_

The goal is to catch cases where C native code allocates memory without
freeing it, such as:

- ``malloc()`` without ``free()``
- ``mmap()`` without ``munmap()``
- ``HeapAlloc()`` without ``HeapFree()`` (Windows)
- ``VirtualAlloc()`` without ``VirtualFree()`` (Windows)
- ``HeapCreate()`` without ``HeapDestroy()`` (Windows)
- Python C objects for which you forget to call ``Py_DECREF``, ``Py_CLEAR``,
  ``PyMem_Free``, etc.

Because memory usage is noisy and influenced by the OS, allocator and garbage
collector, the function is called repeatedly with an increasing number of
invocations. If memory usage continues to grow across runs, it is marked as a
leak and a ``MemoryLeakError`` exception is raised.

Unclosed resource detection
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Beyond memory, the framework also detects resources that the target function
allocates but fails to release after it's called once. The following categories
are monitored:

- **File descriptors** (POSIX): e.g. ``open()`` without ``close()``,
  ``shm_open()`` without ``shm_close()``, unclosed sockets, pipes, and similar
  objects.
- **Windows handles**: kernel objects created via calls such as ``OpenFile()``,
  ``OpenProcess()``, ``CreatePipe()`` and others that are not released with
  ``CloseHandle()``
- **Python threads**: ``threading.Thread`` objects that were started
  but never joined or otherwise stopped.
- **Native system threads**: low-level threads created directly via
  ``pthread_create()`` or ``CreateThread()`` (Windows) that remain running or
  unjoined. These are not Python ``threading.Thread`` objects, but OS threads
  started by C extensions without a matching ``pthread_join()`` or
  ``WaitForSingleObject()`` (Windows).
- **Uncollectable GC objects**: objects that cannot be garbage collected
  because they form reference cycles and / or define a ``__del__`` method, e.g.:

  .. code-block:: python

      class Leaky:
          def __init__(self):
              self.ref = None

      def create_cycle():
          a = Leaky()
          b = Leaky()
          a.ref = b
          b.ref = a
          return a, b  # cycle preventing GC from collecting

Each category raises a specific assertion error describing what was leaked.

Install
=======

::

    pip install psleak

Usage
=====

Subclass ``MemoryLeakTestCase`` and call ``execute()`` inside a test:

.. code-block:: python

    from psleak import MemoryLeakTestCase

    class TestLeaks(MemoryLeakTestCase):
        def test_fun(self):
            self.execute(some_function)

If the function leaks memory or resources, the test will fail with a
descriptive exception, e.g.::

    psleak.MemoryLeakError: memory kept increasing after 10 runs
    Run # 1: heap=+388160  | uss=+356352  | rss=+327680  | (calls= 200, avg/call=+1940)
    Run # 2: heap=+584848  | uss=+614400  | rss=+491520  | (calls= 300, avg/call=+1949)
    Run # 3: heap=+778320  | uss=+782336  | rss=+819200  | (calls= 400, avg/call=+1945)
    Run # 4: heap=+970512  | uss=+1032192 | rss=+1146880 | (calls= 500, avg/call=+1941)
    Run # 5: heap=+1169024 | uss=+1171456 | rss=+1146880 | (calls= 600, avg/call=+1948)
    Run # 6: heap=+1357360 | uss=+1413120 | rss=+1310720 | (calls= 700, avg/call=+1939)
    Run # 7: heap=+1552336 | uss=+1634304 | rss=+1638400 | (calls= 800, avg/call=+1940)
    Run # 8: heap=+1752032 | uss=+1781760 | rss=+1802240 | (calls= 900, avg/call=+1946)
    Run # 9: heap=+1945056 | uss=+2031616 | rss=+2129920 | (calls=1000, avg/call=+1945)
    Run #10: heap=+2140624 | uss=+2179072 | rss=+2293760 | (calls=1100, avg/call=+1946)

Configuration
=============

``MemoryLeakTestCase`` exposes several tunables as class attributes or per-call
overrides:

- ``warmup_times``: warm-up calls before starting measurement (default: *10*)
- ``times``: number of times to call the tested function in each iteration.
  (default: *200*)
- ``retries``: maximum retries if memory keeps growing (default: *10*)
- ``tolerance``: allowed memory growth (in bytes or per-metric) before
  it is considered a leak. (default: *0*)
- ``trim_callback``: optional callable to free caches before starting
  measurement (default: *None*)
- ``checkers``: config object controlling which checkers to run (default:
  *None*)
- ``verbosity``: diagnostic output level (default: *1*)

You can override these either when calling ``execute()``:

.. code-block:: python

    from psleak import MemoryLeakTestCase, Checkers

    class MyTest(MemoryLeakTestCase):
        def test_fun(self):
            self.execute(
                some_function,
                times=500,
                tolerance=1024,
                checkers=Checkers.exclude("gcgarbage")
             )

...or at class level:

.. code-block:: python

    from psleak import MemoryLeakTestCase, Checkers

    class MyTest(MemoryLeakTestCase):
        times = 500
        tolerance = {"rss": 1024}
        checkers = Checkers.only("memory")

        def test_fun(self):
            self.execute(some_function)

Recommended test environment
============================

For more reliable results, it is important to run tests with:

.. code-block:: bash

    PYTHONMALLOC=malloc PYTHONUNBUFFERED=1 python3 -m pytest test_memleaks.py

Why this matters:

- ``PYTHONMALLOC=malloc``: disables the `pymalloc allocator`_, which caches
  small objects (<= 512 bytes) and therefore makes leak detection less
  reliable. With pymalloc disabled, all memory allocations go through the
  system ``malloc()``, making them visible in heap, USS, RSS, and VMS metrics.
- ``PYTHONUNBUFFERED=1``: disables stdout/stderr buffering, making memory leak
  detection more reliable.

Memory leak tests should be run separately from other tests, and not in
parallel (e.g. via pytest-xdist).

References
==========

- \(2025\) Blog post about `psutil heap APIs <https://gmpy.dev/blog/2025/psutil-heap-introspection-apis>`__
- \(2018\) History of psutil heap APIs: `psutil issue #1275 <https://github.com/giampaolo/psutil/issues/1275>`__
- \(2016\) Blog post about `USS and PSS memory <https://gmpy.dev/blog/2016/real-process-memory-and-environ-in-python>`__
- Usage of psleak in psutil tests: `test_memleaks.py <https://github.com/giampaolo/psutil/blob/master/tests/test_memleaks.py>`__

.. _psutil.heap_info: https://psutil.readthedocs.io/en/latest/#psutil.heap_info
.. _psutil.Process.memory_full_info: https://psutil.readthedocs.io/en/latest/#psutil.Process.memory_full_info
.. _psutil: https://github.com/giampaolo/psutil
.. _pymalloc allocator: https://docs.python.org/3/c-api/memory.html#the-pymalloc-allocator
