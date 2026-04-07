# polybind

> Auto-generate unified, type-safe Python wrappers for C++ template types —
> from any `.pyi` stub file produced by **nanobind**, **pybind11**, or **Cython**.

[![PyPI](https://img.shields.io/pypi/v/polybind)](https://pypi.org/project/polybind/)
[![Python](https://img.shields.io/pypi/pyversions/polybind)](https://pypi.org/project/polybind/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## The problem

Every C++ binding tool forces you to expose each template specialisation as a
separate class. For `Box<T>` you get:

```python
import _mylib

b_int   = _mylib.Box__int32(10)
b_float = _mylib.Box__float64(3.14)

isinstance(b_int, _mylib.Box__int32)   # True
isinstance(b_int, _mylib.Box__float64) # False — different class!
```

For multi-parametric templates like `Pair<T1, T2>` it gets worse:

```python
p = _mylib.Pair__float64__int32(3.14, 5)
# Which class is this? How do I dispatch on both types at once?
```

Python users expect a **single** `Box` or `Pair` that dispatches on input
types and where `isinstance(obj, Box)` works for all specialisations.

## The solution

`polybind` reads the `.pyi` stub your binding tool already produces and
generates a clean, fully-typed Python wrapper — no C++ changes needed.

```python
from mylib import Box, Pair

# single-type
b = Box(10)
type(b) is Box          # True  ✅
isinstance(b, Box)      # True  ✅
b.value()               # 10    ✅

# multi-type
p = Pair(3.14, 5)
p.first()               # 3.14  ✅
p.second()              # 5     ✅
type(p) is Pair         # True  ✅

# explicit dtype control
Box(1, dtypes=["float64"])               # force float64 variant
Pair(1, 2, dtypes=["int32", "int64"])    # explicit per-type
Pair(1.0, 2, dtypes={"first": "float64"}) # partial — second auto-detected

# subscript access to raw C++ class
Box["int32"]                             # → _mylib.Box__int32
Pair[("float64", "int32")]              # → _mylib.Pair__float64__int32
```

Because polybind works from the `.pyi` stub, it is **binding-method agnostic**:
the same command works for nanobind, pybind11, and Cython.

---

## Naming convention

polybind recognises class names using **double-underscore separators** and
**numpy scalar type** suffixes:

```
[_]BaseName__T1[__T2[__T3...]]
```

| Class name                | Template               | Arity |
|---------------------------|------------------------|-------|
| `_Box__int32`             | `Box<int32>`           | 1     |
| `Box__float64`            | `Box<float64>`         | 1     |
| `_Pair__float64__int32`   | `Pair<float64, int32>` | 2     |
| `Transform__int32__bool_` | `Transform<int32,bool>`| 2     |

Supported numpy-style suffixes: `int8`, `int16`, `int32`, `int64`,
`uint8`–`uint64`, `float32`, `float64`, `bool_`, `str_`, `bytes_`
(and short aliases `int`, `float`, `bool`, `str`).

---

## Installation

```bash
pip install polybind
```

---

## Quick start

### 1 — Expose your C++ templates

```cpp
// nanobind example
nb::class_<Box<int32_t>>(m,  "_Box__int32")
    .def(nb::init<int32_t>())
    .def("value", &Box<int32_t>::value);

nb::class_<Box<double>>(m, "_Box__float64")
    .def(nb::init<double>())
    .def("value", &Box<double>::value);
```

### 2 — Generate the stub

```bash
# nanobind
python -m nanobind.stubgen -m _mylib -o _mylib.pyi

# pybind11
pybind11-stubgen _mylib -o .

# Cython
cython --annotate mylib.pyx   # then use stubgen on the .so
```

### 3 — Run polybind

```bash
polybind _mylib.pyi           # writes mylib.py next to the stub
polybind _mylib.pyi -o src/mylib.py
```

### 4 — Use the wrapper

```python
from mylib import Box

b = Box(42)
b.value()                     # 42
Box["int32"]                  # the raw C++ class
```

---

## dtypes parameter

The `dtypes` argument controls which C++ variant is selected:

| `dtypes` value | Behaviour |
|---|---|
| `None` (default) | Auto-detect from `type(arg)` for each constructor argument |
| `["float64", "int32"]` | Explicit list in template-parameter order |
| `{"first": "float64"}` | Partial dict — unlisted args auto-detected |

When the number of template parameters **exceeds** the number of constructor
arguments (e.g. a tag-dispatch pattern), `dtypes` as a list is **required**.
The generated wrapper will raise a descriptive `TypeError` at runtime if
`dtypes=None` is used in that case.

numpy `np.dtype` objects are also accepted in any position:

```python
import numpy as np
Box(1, dtypes=[np.dtype("float64")])
```

---

## What gets generated

For a stub containing `_Box__int32`, `_Box__float64`, `_Box__str_`:

```python
import typing
import _mylib
from abc import ABC as _ABC

_NUMPY_TYPE_MAP: typing.Dict[str, type] = {"int8": int, ..., "float64": float}

class Box(_ABC):
    """Unified wrapper for Box template variants.
    Wraps: ``_Box__float64``, ``_Box__int32``, ``_Box__str_``
    ...
    """
    __slots__ = ('_impl',)

    _type_map_box: typing.ClassVar[typing.Dict[tuple, type]] = {
        ('float64',): _mylib._Box__float64,
        ('int32',):   _mylib._Box__int32,
        ('str_',):    _mylib._Box__str_,
    }

    def __new__(cls, val, dtypes=None) -> 'Box': ...
    def value(self): ...             # all public methods from the stub
    def __add__(self, other): ...    # all dunders from the stub
    @staticmethod
    def zero() -> 'Box': ...        # @staticmethod reproduced
    @classmethod
    def from_string(cls, s) -> 'Box': ...  # @classmethod reproduced

    @classmethod
    def __class_getitem__(cls, item) -> type: ...

for _t in Box._type_map_box.values():
    Box.register(_t)
```

**Properties of the generated wrapper:**

| Check | Result |
|---|---|
| `type(obj) is Box` | ✅ True |
| `isinstance(obj, Box)` | ✅ True (also for raw C++ objects) |
| `obj.any_cpp_method()` | ✅ delegated directly |
| `Box(val, dtypes=["float64"])` | ✅ explicit dtype override |
| `Box["int32"]` | ✅ returns the underlying C++ class |
| `@staticmethod`, `@classmethod`, `@property` | ✅ reproduced |
| Docstrings from stub | ✅ included and rewritten |
| `np.dtype` for dtypes arg | ✅ if numpy installed |
| Multi-type template `Pair(1.0, 2)` | ✅ auto-detected |

---

## CLI reference

```
usage: polybind [-h] [-o OUTPUT] [-m NAME] [--dry-run] [-v] INPUT.pyi

positional arguments:
  INPUT.pyi             Path to the .pyi stub file

options:
  -o, --output OUTPUT       Output .py file (default: INPUT.py, leading _ stripped)
  -m, --module-name NAME    Override the C-extension import name
  --dry-run                 Print generated code to stdout, write nothing
  -v, --verbose             Show discovered groups and variants
```

---

## Python API

```python
from polybind.core import PolybindGenerator
from pathlib import Path

gen = PolybindGenerator(Path("_mylib.pyi"))
gen.run(output_path=Path("mylib.py"))

# or inspect without writing
source = gen.generate_source()
groups = gen._parser.parse()
for g in groups:
    print(g.base_name, g.arity, [v.suffix_key for v in g.variants])
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
    _my_module.pyi   ← fixture: single and multi-type templates
    _my_module.py    ← pure-Python stand-in for the C extension
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
