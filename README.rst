|downloads| |stars| |forks|
|version| |license|
|github-actions| |twitter|

.. |downloads| image:: https://img.shields.io/pypi/dm/psleak.svg
    :target: https://clickpy.clickhouse.com/dashboard/psleak
    :alt: Downloads

.. |stars| image:: https://img.shields.io/github/stars/giampaolo/psleak.svg
    :target: https://github.com/giampaolo/psleak/stargazers
    :alt: Github stars

.. |forks| image:: https://img.shields.io/github/forks/giampaolo/psleak.svg
    :target: https://github.com/giampaolo/psleak/network/members
    :alt: Github forks

.. |github-actions| image:: https://img.shields.io/github/actions/workflow/status/giampaolo/psleak/.github/workflows/tests.yml.svg
    :target: https://github.com/giampaolo/psleak/actions
    :alt: CI status

.. |version| image:: https://img.shields.io/pypi/v/psleak.svg?label=pypi
    :target: https://pypi.org/project/psleak
    :alt: Latest version

.. |license| image:: https://img.shields.io/pypi/l/psleak.svg
    :target: https://github.com/giampaolo/psleak/blob/master/LICENSE
    :alt: License

.. |twitter| image:: https://img.shields.io/twitter/follow/grodola.svg?label=follow&style=flat&logo=twitter&logoColor=4FADFF
    :target: https://twitter.com/grodola
    :alt: Twitter Follow

psleak
======

A testing framework for detecting **memory leaks** and **unclosed resources**
created by Python functions, particularly those **implemented in C or other
native extensions**. It was originally developed as part of `psutil
<https://github.com/giampaolo/psutil>`__ test suite, and later split out into a
standalone project.

**Note**: this project is still experimental. Internal heuristics may change.

Features
========

Memory leak detection
^^^^^^^^^^^^^^^^^^^^^

The framework measures process memory before and after repeatedly calling a
function, tracking:

- Heap metrics from `psutil.heap_info()
  <https://psutil.readthedocs.io/en/latest/#psutil.heap_info>`__
- USS, RSS and VMS from `psutil.Process.memory_full_info()
  <https://psutil.readthedocs.io/en/latest/#psutil.Process.memory_full_info>`__

The goal is to catch cases where C native code allocates memory without
freeing it, such as:

- ``malloc()`` without ``free()``
- ``mmap()`` without ``munmap()``
- ``HeapAlloc()`` without ``HeapFree()`` (Windows)
- ``VirtualAlloc()`` without ``VirtualFree()`` (Windows)
- ``HeapCreate()`` without ``HeapDestroy()`` (Windows)

Because memory usage is noisy and influenced by the OS, allocator, and garbage
collector, the function is called repeatedly with an increasing number of
invocations. If memory usage continues to grow across runs, it is marked as a
leak and a ``MemoryLeakError`` exception is raised.

Unclosed resource detection
^^^^^^^^^^^^^^^^^^^^^^^^^^^

In addition to memory checks, the framework also detects resources that are
created during a single call to the target function, but not released
afterward. The following categories are monitored:

- **File descriptors** (POSIX): e.g. ``open()`` without ``close()``,
  ``shm_open()`` without ``shm_close()``, sockets, pipes, and similar objects.
- **Windows handles**: kernel objects created via calls such as
  ``CreateFile()``, ``OpenProcess()`` and others that are not released with
  ``CloseHandle()``
- **Python threads**: ``threading.Thread`` objects that were started
  but never joined or otherwise stopped.
- **Native system threads**: low-level threads created directly via
  ``pthread_create()`` or ``CreateThread()`` (Windows) that remain running or
  unjoined. These are not Python ``threading.Thread`` objects, but OS threads
  started by C extensions without a matching ``pthread_join()`` or
  ``WaitForSingleObject()`` (Windows).
- **Uncollectable GC objects**: objects that cannot be garbage collected
  because they form cycles and / or define a ``__del__`` method

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
    Run # 1: heap=+379K | uss=+340K | rss=+320K (calls= 200, avg/call=+1K)
    Run # 2: heap=+758K | uss=+732K | rss=+800K (calls= 400, avg/call=+1K)
    Run # 3: heap=+1M   | uss=+1M   | rss=+1M   (calls= 600, avg/call=+1K)
    Run # 4: heap=+1M   | uss=+1M   | rss=+1M   (calls= 800, avg/call=+1K)
    Run # 5: heap=+1M   | uss=+1M   | rss=+1M   (calls=1000, avg/call=+1K)
    Run # 6: heap=+2M   | uss=+2M   | rss=+2M   (calls=1200, avg/call=+1K)
    Run # 7: heap=+2M   | uss=+2M   | rss=+2M   (calls=1400, avg/call=+1K)
    Run # 8: heap=+2M   | uss=+3M   | rss=+3M   (calls=1600, avg/call=+1K)
    Run # 9: heap=+3M   | uss=+3M   | rss=+3M   (calls=1800, avg/call=+1K)
    Run #10: heap=+3M   | uss=+3M   | rss=+3M   (calls=2000, avg/call=+1K)

Configuration
=============

``MemoryLeakTestCase`` exposes several tunables as class attributes or per-call
overrides:

- ``times``: number of times to call the tested function in each iteration.
  (default: *200*)
- ``retries``: maximum retries if memory grows (default: *10*)
- ``warmup_times``: warm-up calls before measuring (default: *10*)
- ``tolerance``: allowed memory growth in bytes (int or per-metric dict,
  default: *0*)
- ``trim_callback``: optional callable to free caches before measuring
  (default: *None*)
- ``verbosity``: diagnostic output level (default: *1*)
- ``checkers``: config object controlling which checkers run (default: *None*)


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

- ``PYTHONMALLOC=malloc``: disables the `pymalloc allocator
  <https://docs.python.org/3/c-api/memory.html#the-pymalloc-allocator>`__,
  which caches small objects (<= 512 bytes) and therefore makes leak detection
  less reliable. With pymalloc disabled, all memory allocations go through the
  system ``malloc()``, making them easier to show up in heap, USS, RSS and VMS
  metrics.
- ``PYTHONUNBUFFERED=1``: disables stdout/stderr buffering, making memory leak
  detection more reliable.

Memory leak tests should be run separately from other tests, and not in
parallel (e.g. via pytest-xdist).

References
==========

- https://github.com/giampaolo/psutil/issues/1275#issuecomment-3572229939
- https://gmpy.dev/blog/2016/real-process-memory-and-environ-in-python
