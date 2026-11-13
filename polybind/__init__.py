"""
polybind
~~~~~~~~
A toolbox for hybrid C++/Python projects: wrappers for nanobind, pybind11 and Cython
stubs, one version for the whole project, native dependencies. The command line is
``polybind -h``; this package is the Python API of the stub generator.

Quickstart::

    from pathlib import Path
    from polybind import PolybindGenerator

    PolybindGenerator(Path("_mymodule.pyi")).run(output_path=Path("mymodule.py"))

The generator is imported on first use, so ``import polybind`` stays cheap for the tools
that never need it (``polybind version`` and the build-time providers).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polybind.core import (
        NUMPY_TYPE_MAP,
        CodeGenerator,
        CppVariant,
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
    "__version__",
]

_LAZY = frozenset(__all__) - {"__version__"}

try:
    # `_pkg_version`, not `version`: that name would hide the submodule polybind.version
    from importlib.metadata import version as _pkg_version

    __version__: str = _pkg_version("polybind")
except Exception:  # noqa: BLE001 -- running from a source tree that is not installed
    __version__ = "0.0.0"


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        return getattr(importlib.import_module("polybind.core"), name)
    raise AttributeError(f"module 'polybind' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), *_LAZY])
