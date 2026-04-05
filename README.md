# polybind

> Auto-generate unified Python wrapper classes for C++ template types
> exposed via **nanobind**, **pybind11**, or **Cython**.

---

## The problem

When you bind a C++ template class like `Box<T>`, every binding tool forces
you to expose each specialisation under a separate name:

```python
# what the binding layer gives you
import _novasvg

b = _novasvg.Box_int32(10)
isinstance(b, _novasvg.Box_int32)   # True
isinstance(b, _novasvg.Box_float64) # False — different class!
```

Python users expect a **single** `Box` class that dispatches on input type,
and they expect `isinstance(b, Box)` to work for every specialisation.

## The solution

`polybind` reads the `.pyi` stub produced by your binding tool's own
`stubgen` and auto-generates a clean Python wrapper:

```python
# what polybind generates for you
from novasvg import Box   # generated wrapper

b = Box(10)
isinstance(b, Box)   # True ✅
type(b) is Box       # True ✅
Box[int]             # returns Box_int32 directly ✅
```

Because it works from the `.pyi` stub it is **binding-method agnostic** —
the same tool works for nanobind, pybind11, and Cython.

---

## Naming convention

polybind recognises class names that follow the **numpy scalar type**
convention:

| C++ type        | Suffix expected | Python type |
|-----------------|-----------------|-------------|
| `int32_t`       | `_int32`        | `int`       |
| `int64_t`       | `_int64`        | `int`       |
| `float`         | `_float32`      | `float`     |
| `double`        | `_float64`      | `float`     |
| `std::string`   | `_str_`         | `str`       |
| `bool`          | `_bool_`        | `bool`      |

Classes with a leading underscore (`_Box_int32`) are also recognised —
the leading `_` is stripped from the base name.

---

## Installation

```bash
pip install polybind
```

Or from source:

```bash
git clone https://github.com/your-username/polybind
cd polybind
pip install -e .
```

---

## Usage

### CLI

```bash
# basic — writes novasvg.py next to the stub
polybind _novasvg.pyi

# specify output path
polybind _novasvg.pyi -o src/novasvg.py

# dry-run: print to stdout without writing
polybind _novasvg.pyi --dry-run

# verbose: print discovered groups and variants
polybind _novasvg.pyi -v
```

### Python API

```python
from polybind.core import PolybindGenerator
from pathlib import Path

gen = PolybindGenerator(Path("_novasvg.pyi"))
gen.run(output_path=Path("novasvg.py"))
```

---

## Project layout

```
polybind/           # flat layout — package == repo root
  __init__.py
  __main__.py       # CLI entry point
  core.py           # parser + code generator
tests/
  test_core.py
pyproject.toml
README.md
```

---

## How the generated wrapper works

```
Box(10)
  └─ __new__
       └─ looks up int in Box.__TYPE_MAP_VAL
            └─ returns Box_int32(10) stored in ._impl
                  └─ Box instance wraps it via delegation
```

| Check | Result |
|---|---|
| `type(b) is Box` | ✅ True |
| `isinstance(b, Box)` | ✅ True |
| `b.any_cpp_method()` | ✅ delegated via `__getattr__` |
| `pickle / deepcopy` | ✅ via `__reduce__` delegation |

---

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

---

## License

MIT
