# Copyright (c) 2025, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from psutil import POSIX
from psutil import WINDOWS
from setuptools import Extension
from setuptools import setup

macros = []

if POSIX:
    macros.append(("PSLEAK_POSIX", 1))
if WINDOWS:
    macros.append(("PSLEAK_WINDOWS", 1))

setup(
    name="test_ext",
    ext_modules=[
        Extension(
            "test_ext",
            ["tests/test_ext.c"],
            define_macros=macros,
        )
    ],
)
