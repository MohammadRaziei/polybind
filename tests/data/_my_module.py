"""Pure-Python stand-in for _my_module C extension."""
from __future__ import annotations

class _Box__int32:
    """A box holding a 32-bit integer."""
    def __init__(self, val: int) -> None:
        self._val = int(val)
    def value(self) -> int: return self._val
    @staticmethod
    def zero() -> "_Box__int32": return _Box__int32(0)
    def __add__(self, other): return _Box__int32(self._val + other._val)
    def __eq__(self, other): return isinstance(other, _Box__int32) and self._val == other._val
    def __repr__(self): return f"_Box__int32({self._val!r})"

class _Box__float64:
    """A box holding a 64-bit float."""
    def __init__(self, val: float) -> None:
        self._val = float(val)
    def value(self) -> float: return self._val
    @classmethod
    def from_string(cls, s: str) -> "_Box__float64": return cls(float(s))
    def __mul__(self, other): return _Box__float64(self._val * other._val)
    def __repr__(self): return f"_Box__float64({self._val!r})"

class _Box__str_:
    """A box holding a string."""
    def __init__(self, val: str) -> None:
        self._val = str(val)
    def value(self) -> str: return self._val
    def __len__(self): return len(self._val)
    def __repr__(self): return f"_Box__str_({self._val!r})"

class _Pair__float64__int32:
    """A pair of (float64, int32) values."""
    def __init__(self, first: float, second: int) -> None:
        self._first = float(first); self._second = int(second)
    def first(self) -> float: return self._first
    def second(self) -> int: return self._second
    def __repr__(self): return f"_Pair__float64__int32({self._first!r}, {self._second!r})"

class _Pair__int32__int64:
    """A pair of (int32, int64) values."""
    def __init__(self, first: int, second: int) -> None:
        self._first = int(first); self._second = int(second)
    def first(self) -> int: return self._first
    def second(self) -> int: return self._second
    def __repr__(self): return f"_Pair__int32__int64({self._first!r}, {self._second!r})"

class Renderer:
    def render(self): pass
