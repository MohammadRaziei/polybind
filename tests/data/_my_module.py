"""
tests/data/_my_module.py
~~~~~~~~~~~~~~~~~~~~~~~~
Pure-Python stand-in for a compiled C extension (_my_module.so).

Used exclusively in tests so that the generated wrapper can be imported
and its runtime behaviour verified — without needing a real C++ build.

Class names intentionally match my_module.pyi so the generated code
references them correctly.
"""

from __future__ import annotations


class _Box_int32:
    def __init__(self, val: int) -> None:
        if not isinstance(val, int):
            raise TypeError(f"expected int, got {type(val).__name__}")
        self._val = val

    def value(self) -> int:
        return self._val

    def __add__(self, other: "_Box_int32") -> "_Box_int32":
        return _Box_int32(self._val + other._val)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _Box_int32):
            return self._val == other._val
        return NotImplemented

    def __repr__(self) -> str:
        return f"_Box_int32({self._val!r})"


class _Box_float64:
    def __init__(self, val: float) -> None:
        if not isinstance(val, (int, float)):
            raise TypeError(f"expected float, got {type(val).__name__}")
        self._val = float(val)

    def value(self) -> float:
        return self._val

    def __mul__(self, other: "_Box_float64") -> "_Box_float64":
        return _Box_float64(self._val * other._val)

    def __repr__(self) -> str:
        return f"_Box_float64({self._val!r})"


class _Box_str_:
    def __init__(self, val: str) -> None:
        if not isinstance(val, str):
            raise TypeError(f"expected str, got {type(val).__name__}")
        self._val = val

    def value(self) -> str:
        return self._val

    def __len__(self) -> int:
        return len(self._val)

    def __repr__(self) -> str:
        return f"_Box_str_({self._val!r})"


class Renderer:
    """Non-template class — exists only to verify polybind ignores it."""

    def render(self) -> None:
        pass
