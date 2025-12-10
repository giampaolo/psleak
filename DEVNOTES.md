# Implementaitons

## Leak detection

* [cPython's Lib/test/libregrtest/refleak.py](https://github.com/giampaolo/cpython/blob/2e27da18952/Lib/test/libregrtest/refleak.py)
* [Google's testing_refleaks.py](https://pigweed.googlesource.com/third_party/github/protocolbuffers/protobuf/+/refs/heads/upstream/main-tmp-2/python/google/protobuf/internal/testing_refleaks.py)

## Cache clearing

* [cPython's Lib/test/libregrtest/utils.py::clear_caches()](https://github.com/giampaolo/cpython/blob/2e27da18952/Lib/test/libregrtest/utils.py#L116 )

# Resources / URLs

XXX

# Random notes

* `sys.getallocatedblocks()` detects small pymalloc allocations like
  `PyMem_Malloc(512)` which don't end up in OS memory metrics (including heap),
  but it's too noisy.

# Abandoned ideas

* There's no point in monitoring `sys.getunicodeinternedsize()`: the user
  cannot create immortal unicode objects, unless they use private cPython APIs.
