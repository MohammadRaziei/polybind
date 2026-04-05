"""
polybind.core
~~~~~~~~~~~~~
Parses .pyi stub files and generates unified Python wrapper classes
for C++ template types exposed via nanobind / pybind11 / Cython.

Naming convention  : ClassName_nptype   (numpy-style: int32, float64, …)
Leading underscore : _ClassName_nptype  → grouped under  ClassName
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Numpy-style scalar type registry
# ---------------------------------------------------------------------------

#: Maps numpy-style suffix → Python built-in type used in __new__ dispatch.
#: Extend this table to support more types.
NUMPY_TYPE_MAP: dict[str, str] = {
    # integers
    "int8":    "int",
    "int16":   "int",
    "int32":   "int",
    "int64":   "int",
    "uint8":   "int",
    "uint16":  "int",
    "uint32":  "int",
    "uint64":  "int",
    # floats
    "float32": "float",
    "float64": "float",
    # misc
    "bool_":   "bool",
    "str_":    "str",
    "bytes_":  "bytes",
    # short aliases kept for convenience
    "int":     "int",
    "float":   "float",
    "bool":    "bool",
    "str":     "str",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CppVariant:
    """One concrete C++ specialisation, e.g. _Box_int32 or Box_float64."""
    raw_name: str          # exactly as found in the .pyi
    base_name: str         # e.g. "Box"
    type_suffix: str       # e.g. "int32"
    py_type: str           # e.g. "int"
    dunder_methods: list[str] = field(default_factory=list)


@dataclass
class WrapperGroup:
    """All variants that share the same base_name → one unified wrapper."""
    base_name: str
    module_name: str                        # import target (leading _ stripped)
    variants: list[CppVariant] = field(default_factory=list)

    # key = py_type string, value = list[CppVariant]
    # (multiple C++ types can map to the same Python type; last one wins in
    #  _TYPE_MAP_VAL but all are registered as virtual subclasses)
    @property
    def by_py_type(self) -> dict[str, CppVariant]:
        result: dict[str, CppVariant] = {}
        for v in self.variants:
            result[v.py_type] = v
        return result


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_VARIANT_RE = re.compile(
    r"^_?(?P<base>[A-Za-z][A-Za-z0-9]*)_(?P<suffix>[A-Za-z0-9_]+)$"
)


class StubParser:
    """Reads a .pyi file and extracts WrapperGroups."""

    def __init__(self, pyi_path: Path, module_name: str | None = None) -> None:
        self.pyi_path = pyi_path
        # derive module name from file stem, strip leading underscore
        self.module_name = module_name or pyi_path.stem.lstrip("_")
        self._tree: ast.Module | None = None

    # ------------------------------------------------------------------
    def parse(self) -> list[WrapperGroup]:
        source = self.pyi_path.read_text(encoding="utf-8")
        self._tree = ast.parse(source)

        raw_classes: dict[str, ast.ClassDef] = {
            node.name: node
            for node in ast.walk(self._tree)
            if isinstance(node, ast.ClassDef)
        }

        groups: dict[str, WrapperGroup] = {}

        for name, classdef in raw_classes.items():
            m = _VARIANT_RE.match(name)
            if m is None:
                continue

            base = m.group("base")
            suffix = m.group("suffix")

            if suffix not in NUMPY_TYPE_MAP:
                continue

            py_type = NUMPY_TYPE_MAP[suffix]
            dunders = self._extract_dunders(classdef)

            variant = CppVariant(
                raw_name=name,
                base_name=base,
                type_suffix=suffix,
                py_type=py_type,
                dunder_methods=dunders,
            )

            if base not in groups:
                groups[base] = WrapperGroup(
                    base_name=base,
                    module_name=self.module_name,
                )
            groups[base].variants.append(variant)

        return list(groups.values())

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_dunders(classdef: ast.ClassDef) -> list[str]:
        """Return dunder method names defined in the class (excluding __init__)."""
        result = []
        for node in ast.walk(classdef):
            if isinstance(node, ast.FunctionDef):
                n = node.name
                if (
                    n.startswith("__")
                    and n.endswith("__")
                    and n not in ("__init__", "__new__", "__class__")
                ):
                    result.append(n)
        return result


# ---------------------------------------------------------------------------
# Code generator
# ---------------------------------------------------------------------------

# Dunder methods that *cannot* be proxied via __getattr__ and need explicit
# delegation stubs in the generated wrapper.
_PROXY_DUNDERS: frozenset[str] = frozenset({
    "__repr__", "__str__", "__bytes__", "__format__",
    "__lt__", "__le__", "__eq__", "__ne__", "__gt__", "__ge__",
    "__hash__", "__bool__",
    "__len__", "__length_hint__",
    "__getitem__", "__setitem__", "__delitem__",
    "__contains__",
    "__iter__", "__next__", "__reversed__",
    "__add__", "__radd__", "__iadd__",
    "__sub__", "__rsub__", "__isub__",
    "__mul__", "__rmul__", "__imul__",
    "__truediv__", "__rtruediv__", "__itruediv__",
    "__floordiv__", "__rfloordiv__", "__ifloordiv__",
    "__mod__", "__rmod__",
    "__pow__", "__rpow__",
    "__neg__", "__pos__", "__abs__", "__invert__",
    "__lshift__", "__rshift__",
    "__and__", "__or__", "__xor__",
    "__int__", "__float__", "__complex__", "__index__",
    "__round__", "__trunc__", "__floor__", "__ceil__",
    "__enter__", "__exit__",
    "__await__", "__aiter__", "__anext__",
    "__aenter__", "__aexit__",
    "__call__",
    "__reduce__", "__reduce_ex__", "__copy__", "__deepcopy__",
})


class CodeGenerator:
    """Turns a WrapperGroup into a Python source string."""

    def __init__(self, group: WrapperGroup) -> None:
        self.group = group

    # ------------------------------------------------------------------
    def generate(self) -> str:
        g = self.group
        mod = g.module_name
        base = g.base_name

        lines: list[str] = []

        # --- import ---
        lines.append(f"import _{mod} as _{mod}_ext")
        lines.append(f"from abc import ABC as _ABC")
        lines.append("")

        # --- class definition ---
        # ABC gives .register() so C++ variants become virtual subclasses.
        lines.append(f"class {base}(_ABC):")
        lines.append(f'    """Unified wrapper for {base} template variants.')
        lines.append(f"")
        lines.append(f"    Auto-generated by polybind.")
        lines.append(f'    """')
        lines.append(f"")
        # __slots__ only covers instance attributes; _TYPE_MAP_VAL is a class
        # variable and lives in the class __dict__ — no conflict.
        lines.append(f"    __slots__ = ('_impl',)")
        lines.append(f"")

        # --- _TYPE_MAP_VAL ---
        # Single leading underscore: private by convention, NOT name-mangled.
        lines.append(f"    #: Maps Python built-in type → concrete C++ variant class")
        lines.append(f"    _TYPE_MAP_VAL: dict = {{")
        for py_type, variant in g.by_py_type.items():
            lines.append(
                f"        {py_type}: _{mod}_ext.{variant.raw_name},"
            )
        lines.append(f"    }}")
        lines.append(f"")

        # --- __new__ ---
        lines += self._new_method()
        lines.append(f"")

        # --- __init__ ---
        lines.append(f"    def __init__(self, val: object) -> None:")
        lines.append(f"        pass  # __new__ handles everything")
        lines.append(f"")

        # --- __getattr__ ---
        lines.append(f"    def __getattr__(self, name: str) -> object:")
        lines.append(f"        return getattr(self._impl, name)")
        lines.append(f"")

        # --- __setattr__ ---
        lines.append(f"    def __setattr__(self, name: str, value: object) -> None:")
        lines.append(f"        if name == '_impl':")
        lines.append(f"            object.__setattr__(self, name, value)")
        lines.append(f"        else:")
        lines.append(f"            setattr(self._impl, name, value)")
        lines.append(f"")

        # --- dunder proxy stubs ---
        all_dunders: set[str] = set()
        for v in g.variants:
            all_dunders.update(v.dunder_methods)

        needed = sorted(all_dunders & _PROXY_DUNDERS)
        if needed:
            lines.append(f"    # -- delegated dunder methods --")
        for dunder in needed:
            lines += self._dunder_stub(dunder)
            lines.append(f"")

        # --- __repr__ fallback if not already added ---
        if "__repr__" not in needed:
            lines.append(f"    def __repr__(self) -> str:")
            lines.append(f"        return repr(self._impl)")
            lines.append(f"")

        # --- __class_getitem__ for Box[int32] syntax ---
        lines.append(f"    def __class_getitem__(cls, item: type) -> type:")
        lines.append(f"        return cls._TYPE_MAP_VAL.get(item, cls)")
        lines.append(f"")

        # --- register C++ variant classes as virtual subclasses ---
        # Box extends ABC → Box.register(Cls) makes isinstance(obj, Box) True.
        lines.append(f"# Register C++ variant classes as virtual subclasses of {base}.")
        lines.append(f"for _t in {base}._TYPE_MAP_VAL.values():")
        lines.append(f"    {base}.register(_t)")

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    def _new_method(self) -> list[str]:
        base = self.group.base_name
        return [
            f"    def __new__(cls, val: object) -> '{base}':",
            f"        cpp_cls = cls._TYPE_MAP_VAL.get(type(val))",
            f"        if cpp_cls is None:",
            f"            raise TypeError(",
            f"                f\"{{cls.__name__}} does not support type "
            f"'{{type(val).__name__}}'\"",
            f"            )",
            f"        obj = object.__new__(cls)",
            f"        object.__setattr__(obj, '_impl', cpp_cls(val))",
            f"        return obj",
        ]

    # ------------------------------------------------------------------
    @staticmethod
    def _dunder_stub(name: str) -> list[str]:
        """Generate a minimal delegation stub for a dunder method."""
        # Special cases that need specific signatures
        _SIGS: dict[str, tuple[str, str]] = {
            "__len__":      ("self",              "return len(self._impl)"),
            "__bool__":     ("self",              "return bool(self._impl)"),
            "__hash__":     ("self",              "return hash(self._impl)"),
            "__repr__":     ("self",              "return repr(self._impl)"),
            "__str__":      ("self",              "return str(self._impl)"),
            "__int__":      ("self",              "return int(self._impl)"),
            "__float__":    ("self",              "return float(self._impl)"),
            "__complex__":  ("self",              "return complex(self._impl)"),
            "__index__":    ("self",              "return self._impl.__index__()"),
            "__abs__":      ("self",              "return abs(self._impl)"),
            "__neg__":      ("self",              "return -self._impl"),
            "__pos__":      ("self",              "return +self._impl"),
            "__invert__":   ("self",              "return ~self._impl"),
            "__iter__":     ("self",              "return iter(self._impl)"),
            "__next__":     ("self",              "return next(self._impl)"),
            "__reversed__": ("self",              "return reversed(self._impl)"),
            "__contains__": ("self, item",        "return item in self._impl"),
            "__getitem__":  ("self, key",         "return self._impl[key]"),
            "__setitem__":  ("self, key, value",  "self._impl[key] = value"),
            "__delitem__":  ("self, key",         "del self._impl[key]"),
            "__call__":     ("self, *a, **kw",    "return self._impl(*a, **kw)"),
            "__enter__":    ("self",              "return self._impl.__enter__()"),
            "__exit__":     ("self, *a",          "return self._impl.__exit__(*a)"),
            "__reduce__":   ("self",              "return self._impl.__reduce__()"),
            "__copy__":     ("self",              "return self._impl.__copy__()"),
            "__deepcopy__": ("self, memo",        "return self._impl.__deepcopy__(memo)"),
        }

        # binary ops: (sig, body template)
        _BIN = [
            "__add__", "__radd__", "__iadd__",
            "__sub__", "__rsub__", "__isub__",
            "__mul__", "__rmul__", "__imul__",
            "__truediv__", "__rtruediv__", "__itruediv__",
            "__floordiv__", "__rfloordiv__", "__ifloordiv__",
            "__mod__", "__rmod__",
            "__pow__", "__rpow__",
            "__lshift__", "__rshift__",
            "__and__", "__or__", "__xor__",
            "__lt__", "__le__", "__eq__", "__ne__", "__gt__", "__ge__",
        ]

        if name in _SIGS:
            sig, body = _SIGS[name]
        elif name in _BIN:
            sig = "self, other"
            other = "other._impl if isinstance(other, type(self)) else other"
            op = f"self._impl.{name}({other})"
            body = f"return {op}"
        else:
            sig = "self, *args, **kwargs"
            body = f"return self._impl.{name}(*args, **kwargs)"

        return [
            f"    def {name}({sig}) -> object:",
            f"        {body}",
        ]


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

class PolybindGenerator:
    """
    Orchestrates parsing and code generation for a single .pyi stub.

    Usage::

        gen = PolybindGenerator(Path("_novasvg.pyi"))
        gen.run(output_path=Path("novasvg.py"))
    """

    def __init__(
        self,
        pyi_path: Path,
        module_name: str | None = None,
    ) -> None:
        self.pyi_path = Path(pyi_path)
        self.module_name = module_name
        self._parser = StubParser(self.pyi_path, module_name)

    # ------------------------------------------------------------------
    def generate_source(self) -> str:
        groups = self._parser.parse()
        if not groups:
            raise ValueError(
                f"No template variants found in '{self.pyi_path}'. "
                "Make sure class names follow the ClassName_nptype convention "
                "(e.g. Box_int32, Matrix_float64)."
            )

        header = textwrap.dedent(f"""\
            # This file was auto-generated by polybind.
            # Source: {self.pyi_path.name}
            # Do not edit manually.

            from __future__ import annotations

        """)

        body_parts = [
            CodeGenerator(group).generate()
            for group in sorted(groups, key=lambda g: g.base_name)
        ]

        return header + "\n\n".join(body_parts)

    # ------------------------------------------------------------------
    def run(self, output_path: Path) -> list[WrapperGroup]:
        output_path = Path(output_path)
        source = self.generate_source()
        output_path.write_text(source, encoding="utf-8")
        groups = self._parser.parse()
        return groups
