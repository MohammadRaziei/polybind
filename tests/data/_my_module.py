"""Pure-Python stand-in for a compiled _my_module C extension."""

from __future__ import annotations


class _Box_int32:
    """A box holding a 32-bit integer value."""
    def __init__(self, val: int) -> None:
        if not isinstance(val, int):
            raise TypeError(f"expected int, got {type(val).__name__}")
        self._val = val

    def value(self) -> int:
        """Return the stored integer value."""
        return self._val

    @staticmethod
    def zero() -> "_Box_int32":
        """Return a _Box_int32 initialised to zero."""
        return _Box_int32(0)

    def __add__(self, other: "_Box_int32") -> "_Box_int32":
        return _Box_int32(self._val + other._val)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Box_int32) and self._val == other._val

    def __repr__(self) -> str:
        return f"_Box_int32({self._val!r})"


class _Box_float64:
    """A box holding a 64-bit float value."""
    def __init__(self, val: float) -> None:
        self._val = float(val)

    def value(self) -> float:
        """Return the stored float value."""
        return self._val

    @classmethod
    def from_string(cls, s: str) -> "_Box_float64":
        """Parse a float from string."""
        return cls(float(s))

    def __mul__(self, other: "_Box_float64") -> "_Box_float64":
        return _Box_float64(self._val * other._val)

    def __repr__(self) -> str:
        return f"_Box_float64({self._val!r})"


class _Box_str_:
    """A box holding a string value."""
    def __init__(self, val: str) -> None:
        self._val = val

    def value(self) -> str:
        """Return the stored string value."""
        return self._val

    def __len__(self) -> int:
        return len(self._val)

    def __repr__(self) -> str:
        return f"_Box_str_({self._val!r})"


class Renderer:
    """Non-template class — exists only to verify polybind ignores it."""
    def render(self) -> None:
        pass
