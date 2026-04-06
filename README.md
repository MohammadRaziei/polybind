# polybind

> Auto-generate unified Python wrappers for C++ template types exposed via
> **nanobind**, **pybind11**, or **Cython** — from any `.pyi` stub file.

[![PyPI](https://img.shields.io/pypi/v/polybind)](https://pypi.org/project/polybind/)
[![Python](https://img.shields.io/pypi/pyversions/polybind)](https://pypi.org/project/polybind/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## The problem

Every C++ binding tool forces you to expose each template specialisation as a
separate class:

```python
import _mylib

b_int   = _mylib.Box_int32(10)
b_float = _mylib.Box_float64(3.14)

isinstance(b_int,   _mylib.Box_int32)   # True
isinstance(b_float, _mylib.Box_int32)   # False — different class!
```

Python users expect a **single** `Box` that dispatches on input type and
where `isinstance(obj, Box)` works for every specialisation.

## The solution

`polybind` reads the `.pyi` stub produced by your binding tool's own
`stubgen` and auto-generates a clean, fully-typed Python wrapper — with no
changes to your C++ code.

```python
from mylib import Box

b = Box(10)
isinstance(b, Box)              # True ✅
type(b) is Box                  # True ✅
Box(3.14, dtype="float64")      # explicit dtype override ✅
Box[int]                        # returns the raw C++ class ✅
```

Because it works from the `.pyi` stub, **polybind is binding-method agnostic**:
the same tool works for nanobind, pybind11, and Cython.

---

## Installation

```bash
pip install polybind
```

---

## Quick start

### 1 — Generate your stub

```bash
# nanobind
python -m nanobind.stubgen -m _mylib -o _mylib.pyi

# pybind11
pybind11-stubgen _mylib -o .

# Cython
cython --annotate mylib.pyx   # then use stubgen on the .so
```

### 2 — Run polybind

```bash
polybind _mylib.pyi              # writes mylib.py next to the stub
polybind _mylib.pyi -o src/mylib.py
```

### 3 — Use the wrapper

```python
from mylib import Box

box_i = Box(42)
box_f = Box(3.14)
box_s = Box("hello")

isinstance(box_i, Box)   # True
isinstance(box_f, Box)   # True
type(box_i) is Box       # True

len(box_s)               # 5  — delegated transparently
box_i.value()            # 42 — any method from the C++ class works

# dtype override
Box(1, dtype="float64")  # forces float64 variant regardless of input type
```

---

## Naming convention

polybind recognises class names using the **numpy scalar type** convention:

| C++ type        | Suffix        | Python type |
|-----------------|---------------|-------------|
| `int32_t`       | `_int32`      | `int`       |
| `int64_t`       | `_int64`      | `int`       |
| `float`         | `_float32`    | `float`     |
| `double`        | `_float64`    | `float`     |
| `std::string`   | `_str_`       | `str`       |
| `bool`          | `_bool_`      | `bool`      |

A leading underscore on the class name (`_Box_int32`) is stripped to produce
the base name (`Box`).

---

## What gets generated

For a stub containing `_Box_int32`, `_Box_float64`, `_Box_str_`, polybind
emits a file like:

```python
import typing
import _mylib
from abc import ABC as _ABC

_NUMPY_TYPE_MAP: typing.Dict[str, type] = {"int32": int, "float64": float, ...}

class Box(_ABC):
    """Unified wrapper for Box template variants.

    Wraps: ``_Box_float64``, ``_Box_int32``, ``_Box_str_``
    ...
    """

    __slots__ = ('_impl',)

    _type_map_box: typing.ClassVar[typing.Dict[type, type]] = {
        int: _mylib._Box_int32, float: _mylib._Box_float64, str: _mylib._Box_str_
    }

    def __new__(cls, val, dtype=None) -> 'Box': ...
    def value(self): ...          # all public methods from the stub
    def __add__(self, other): ... # all dunders from the stub
    def __repr__(self) -> str: ...

    @classmethod
    def __class_getitem__(cls, item: type) -> type: ...

# isinstance(raw_cpp_obj, Box) → True
for _t in Box._type_map_box.values():
    Box.register(_t)
```

Key properties of the generated wrapper:

| Check | Result |
|---|---|
| `type(obj) is Box` | ✅ True |
| `isinstance(obj, Box)` | ✅ True (also for raw C++ objects) |
| `obj.any_cpp_method()` | ✅ delegated directly |
| `Box(val, dtype="float64")` | ✅ explicit dtype override |
| `Box[int]` | ✅ returns the underlying C++ class |
| Decorators (`@staticmethod`, `@classmethod`, `@property`) | ✅ reproduced |
| Docstrings from stub | ✅ included and rewritten |
| numpy `np.dtype` for dtype arg | ✅ if numpy installed |

---

## CLI reference

```
usage: polybind [-h] [-o OUTPUT] [-m NAME] [--dry-run] [-v] INPUT.pyi

positional arguments:
  INPUT.pyi             Path to the .pyi stub file

options:
  -o, --output OUTPUT   Output .py file (default: INPUT.py, leading _ stripped)
  -m, --module-name NAME  Override the C-extension import name
  --dry-run             Print generated code to stdout, write nothing
  -v, --verbose         Show discovered groups and variants
```

---

## Python API

```python
from polybind.core import PolybindGenerator
from pathlib import Path

gen = PolybindGenerator(Path("_mylib.pyi"))
gen.run(output_path=Path("mylib.py"))

# or just get the source string
source = gen.generate_source()
```

---

## Project layout

```
polybind/
  __init__.py
  __main__.py    ← CLI
  core.py        ← StubParser + CodeGenerator + PolybindGenerator
tests/
  test_core.py
  test_cli.py
  data/
    _my_module.pyi
    _my_module.py
pyproject.toml
README.md
```

---

## Running tests

```bash
pip install -e ".[dev]"
pytest
pytest --cov=polybind --cov-report=term-missing
```

---

## Author

[Mohammad Raziei](https://github.com/mohammadraziei) — MIT License
