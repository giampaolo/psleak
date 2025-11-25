import glob

from setuptools import Extension
from setuptools import setup

libraries = []
ext = Extension(
    "_psleak",
    sources=glob.glob("psleak/*.c"),
    libraries=libraries,
)
setup(
    name="psleak",
    version="0.1.0",
    ext_modules=[ext],
)
