# psleak

**Detect C-level memory leaks and inspect malloc usage — a psutil companion library.**

`psleak` provides a **MemoryLeakTestCase** class for unit testing, which helps detect memory leaks in C extensions or native functions by comparing memory usage before and after repeated calls.

Additionally, it provides two low-level utility functions for monitoring memory allocations in Python:

1. **malloc_info()**
   Returns detailed statistics about the process memory allocator:
   - `heap_used` – memory allocated via `malloc()`
   - `mmap_used` – memory allocated via `mmap()` (large blocks)
   - `heap_total` – total main heap size (`sbrk`/arenas)

2. **malloc_trim()**
   Releases unused memory held by the allocator back to the operating system.

---

## What psleak detects

`psleak` is specifically designed to catch cases where native code allocates
memory or system resources but fails to release them. It monitors both **heap
allocations** and **large memory mappings**, as well as unclosed handles or
file descriptors.

The types of allocations it can detect include:

- `malloc()` without a corresponding `free()`
- `mmap()` without `munmap()`
- `HeapAlloc()` without `HeapFree()` (Windows)
- `VirtualAlloc()` without `VirtualFree()` (Windows)
- `HeapCreate()` without `HeapDestroy()` (Windows)

In other words, any memory allocated via these system calls that is not
properly released can be caught by `psleak`.

---

## Intended Audience

`psleak` is primarily designed for developers who work with Python extensions,
C libraries, or other native code that is embedded in Python programs.

It is especially useful for:

- **C extension developers** who want to ensure their functions correctly allocate and free memory.
- **QA testers** who want automated memory leak checks in unit tests.
- **Python developers** wrapping native libraries and needing to detect memory leaks.
- **Open source maintainers** looking to validate memory safety in cross-platform Python modules.

In short, `psleak` is aimed at anyone who needs **low-level insight into memory
usage and leak detection** within Python applications that interact with native C code.

---

## Features

- Track heap, mmap, and total memory usage in Python processes.
- Detect memory leaks in C extensions, Python wrappers, or other native code.
- Detect unclosed file descriptors (POSIX) or handles (Windows).
- Works on Linux, macOS, and Windows.
- Integrates seamlessly with `unittest` frameworks.

---

## Installation

```bash
pip install psleak
```

---

## Usage

### Detect memory leaks in functions

```python
from psleak import MemoryLeakTestCase

class TestLeaks(MemoryLeakTestCase):
    def test_my_function(self):
        self.execute(my_c_function)
```

- Automatically checks for memory growth across repeated calls.
- Detects unclosed file descriptors or handles.
- Reports memory usage per call and stabilizes after retries.

### Inspect memory allocations

```python
import psleak

info = psleak.malloc_info()
print(info)  # {'heap_used': ..., 'mmap_used': ..., 'heap_total': ...}

psleak.malloc_trim()  # release unused memory
```

---

## References

- [Real Process Memory and Environ in Python](https://gmpy.dev/blog/2016/real-process-memory-and-environ-in-python)
- [psutil Issue #1275](https://github.com/giampaolo/psutil/issues/1275)
