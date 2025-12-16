# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import functools
import sys

import pytest

import psleak

# speed things up
psleak.MemoryLeakTestCase.times = 50
psleak.MemoryLeakTestCase.retries = 5
psleak.MemoryLeakTestCase.warmup_times = 2

# how many times retry_on_failure() decorator will retry
NO_RETRIES = 10


def retry_on_failure(retries=NO_RETRIES):
    """Decorator which runs a test function and retries N times before
    giving up and failing.
    """

    def decorator(test_method):
        @functools.wraps(test_method)
        def wrapper(self, *args, **kwargs):
            err = None
            for attempt in range(retries):
                try:
                    return test_method(self, *args, **kwargs)
                except (  # noqa: PERF203
                    AssertionError,
                    pytest.fail.Exception,
                ) as _:
                    err = _
                    prefix = "\n" if attempt == 0 else ""
                    short_err = str(err).split("\n")[0]
                    print(  # noqa: T201
                        f"{prefix}{short_err}, retrying"
                        f" {attempt + 1}/{retries} ...",
                        file=sys.stderr,
                    )
                    if hasattr(self, "tearDown"):
                        self.tearDown()
                    if hasattr(self, "teardown_method"):
                        self.teardown_method()
                    if hasattr(self, "setUp"):
                        self.setUp()
                    if hasattr(self, "setup_method"):
                        self.setup_method()

            raise err

        return wrapper

    assert retries > 1, retries
    return decorator
