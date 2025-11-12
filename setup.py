from setuptools import setup, Extension
import glob

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
