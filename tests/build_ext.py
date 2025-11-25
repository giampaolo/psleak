from setuptools import Extension
from setuptools import setup

setup(
    name="test_ext",
    ext_modules=[Extension("test_ext", ["tests/test_ext.c"])],
)
