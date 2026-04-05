"""
polybind
~~~~~~~~
Auto-generate unified Python wrappers for C++ template types
exposed via nanobind, pybind11, or Cython.

Quickstart::

    from polybind.core import PolybindGenerator
    from pathlib import Path

    gen = PolybindGenerator(Path("_mymodule.pyi"))
    gen.run(output_path=Path("mymodule.py"))
"""

from polybind.core import (
    CodeGenerator,
    CppVariant,
    NUMPY_TYPE_MAP,
    PolybindGenerator,
    StubParser,
    WrapperGroup,
)

__all__ = [
    "CodeGenerator",
    "CppVariant",
    "NUMPY_TYPE_MAP",
    "PolybindGenerator",
    "StubParser",
    "WrapperGroup",
]

try:
    from importlib.metadata import version
    __version__: str = version("polybind")
except Exception:
    __version__ = "0.0.0"
