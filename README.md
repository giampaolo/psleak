# psleak

A testing framework for detecting **memory leaks** and **unclosed resources**
created by Python functions, especially those implemented in **C, Cython, or
other native extensions**.

It was originally developed as part of
[psutil](https://github.com/giampaolo/psutil/pull/2598), and later split out
into a standalone project.

psleak executes a target function multiple times and verifies that it does not
leak memory, file descriptors, handles, threads (Python or native), or
uncollectable garbage. While primarily aimed at **testing C extension
modules**, it also works for pure Python code.

> [!NOTE]
> **Status:** experimental. APIs and heuristics may change.

## Features

### Memory leak detection

The framework measures process memory before and after repeatedly calling a
function, tracking:

- Heap metrics: `heap_used`, `mmap_used` and `heap_count` (Windows) from
  [psutil.heap_info()](https://psutil.readthedocs.io/en/latest/#psutil.heap_info).
- USS, RSS and VMS memory metrics from
  [psutil.Process.memory_full_info()](https://psutil.readthedocs.io/en/latest/#psutil.Process.memory_full_info).

The goal is to catch cases where C native code allocates memory without freeing
it, such as:

- `malloc()` without `free()`
- `mmap()` without `munmap()`
- `HeapAlloc()` without `HeapFree()` (Windows)
- `VirtualAlloc()` without `VirtualFree()` (Windows)
- `HeapCreate()` without `HeapDestroy()` (Windows)

Because memory usage is noisy and influenced by the OS, allocator, and garbage
collector, the function is called repeatedly with an increasing number of
invocations. If memory usage continues to grow across runs, it is marked as
a leak and a `MemoryLeakError` exception is raised.

### Unclosed resource detection

In addition to memory checks, the framework also detects resources that are
created during a single call to the target function but not released afterward.
The following categories are monitored:

- **File descriptors (POSIX)**: e.g. `open()` without `close()`.
- **Windows handles**: kernel objects created via calls such as `CreateFile()`,
  `OpenProcess()` and others that are not released with `CloseHandle()`.
- **Python threads**: `threading.Thread` objects that were `start()`ed but
  never `join()`ed or otherwise stopped.
- **Native system threads**: low-level threads created directly via
  `pthread_create()` or `CreateThread()` (Windows) that remain running or
  unjoined. These are not Python `threading.Thread` objects, but OS threads
  started by C extensions without a matching `pthread_join()` or
  `WaitForSingleObject()` (Windows).
- **Uncollectable GC objects**:  objects that cannot be garbage collected
  because they form cycles and / or define a `__del__` method.

Each category raises a specific assertion error describing what was leaked.

## Install

```bash
pip install psleak
```

## Usage

Subclass `MemoryLeakTestCase` and call `execute()` inside a test:

```python
from psleak import MemoryLeakTestCase

class TestLeaks(MemoryLeakTestCase):

    def test_fun(self):
        self.execute(some_function)
```

If the function leaks memory or resources, the test will fail with a
descriptive exception, e.g.

```
E   psleak.MemoryLeakError: memory kept increasing after 5 runs
E   Run # 1: heap=+928B    (calls=   50, avg/call=+18B)
E   Run # 2: heap=+832B    (calls=  100, avg/call=+8B)
E   Run # 3: heap=+1K      (calls=  150, avg/call=+7B)
E   Run # 4: heap=+2K      (calls=  200, avg/call=+12B)
E   Run # 5: heap=+1K      (calls=  250, avg/call=+7B)
```

## Configuration

`MemoryLeakTestCase` exposes several tunables as class attributes or per-call
overrides:

- `times`: number of times to call the tested function in each iteration.
  (default: 200)
- `retries`: maximum retries if memory grows (default: *10*)
- `warmup_times`: warm-up calls before measuring (default: *10*)
- `tolerance`: allowed memory growth in bytes (int or per-metric dict, default:
  *0*)
- `trim_callback`: optional callable to free caches before measuring (default:
  *None*)
- `verbosity`: diagnostic output level (default: *1*)
- `checkers`: config object which tells which checkers to run (default: *None*)

You can override these either when calling `execute()`:

```python
from psleak import MemoryLeakTestCase, Checkers

class MyTest(MemoryLeakTestCase):

   def test_fun(self):
      self.execute(
          some_function,
          times=500,
          tolerance=1024,
          checkers=Checkers.exclude("gcgarbage"),
      )
```

...or at class level:

```python
from psleak import MemoryLeakTestCase, Checkers

class MyTest(MemoryLeakTestCase):
   times = 500
   tolerance = 1024
   checkers = Checkers.exclude("gcgarbage")

   def test_fun(self):
      self.execute(some_function)
```

## Recommended test environment

For more reliable results, run tests with:

```bash
PYTHONMALLOC=malloc PYTHONUNBUFFERED=1 python3 -m pytest test_memleaks.py
```

Why this matters:

- `PYTHONMALLOC=malloc`: disables the [pymalloc
  allocator](https://docs.python.org/3/c-api/memory.html#the-pymalloc-allocator),
  which caches small objects (<= 512 bytes) and therefore makes leak detection
  less reliable. With pymalloc disabled, all memory allocations go through the
  system `malloc()`, making them easier to detect in heap, USS, RSS, and VMS
  metrics.
- `PYTHONUNBUFFERED=1`: disables stdout/stderr buffering, making memory leak
  detection more reliable.

> [!NOTE]
> memory leak tests should be run separately from other tests, and not in
> parallel (e.g. via pytest-xdist).

## References

- https://github.com/giampaolo/psutil/issues/1275
- https://gmpy.dev/blog/2016/real-process-memory-and-environ-in-python
