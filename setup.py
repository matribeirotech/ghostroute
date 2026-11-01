"""Build das extensões Cython (opcionais). Se o Cython não estiver
disponível, o pacote funciona com o fallback em Python puro."""
from setuptools import Extension, setup

try:
    from Cython.Build import cythonize

    extensions = cythonize(
        [Extension("ghostroute._speedups._socks5", ["src/ghostroute/_speedups/_socks5.pyx"])],
        compiler_directives={"language_level": "3", "boundscheck": False, "wraparound": False},
    )
except ImportError:
    extensions = []

setup(ext_modules=extensions)

