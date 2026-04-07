"""
polybind.core
~~~~~~~~~~~~~
Parses .pyi stub files and generates unified Python wrapper classes
for C++ template types exposed via nanobind / pybind11 / Cython.

Naming convention:
    [_]BaseName__T1[__T2[__T3...]]

Examples:
    _Box__float32          →  Box<float32>
    _Box__float32__int32   →  Box<float32, int32>
    Transform__int32__bool_→  Transform<int32, bool_>

The number of __ -separated type suffixes determines the template arity.
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

#: Maps numpy-style suffix → Python built-in type name (string).
#: Emitted verbatim as _NUMPY_TYPE_MAP in generated files.
NUMPY_TYPE_MAP: dict[str, str] = {
    "int8":    "int",   "int16":  "int",   "int32":  "int",   "int64":  "int",
    "uint8":   "int",   "uint16": "int",   "uint32": "int",   "uint64": "int",
    "float32": "float", "float64": "float",
    "bool_":   "bool",  "str_":   "str",   "bytes_": "bytes",
    "int":     "int",   "float":  "float", "bool":   "bool",  "str":    "str",
}

_NUMPY_TYPE_MAP_REPR = (
    "{" + ", ".join(f'"{k}": {v}' for k, v in NUMPY_TYPE_MAP.items()) + "}"
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MethodInfo:
    """Signature + metadata for one method parsed from the .pyi."""
    name: str
    args: list[tuple[str, str | None]]   # [(arg_name, annotation_or_None), …]
    return_annotation: str | None
    docstring: str | None
    decorators: list[str] = field(default_factory=list)


@dataclass
class CppVariant:
    """One concrete C++ specialisation.

    e.g.  _Box__float32__int32  →  base="Box", suffixes=["float32","int32"]
    """
    raw_name: str               # exactly as found in the .pyi
    base_name: str              # e.g. "Box"
    suffixes: list[str]         # e.g. ["float32", "int32"]  (ordered)
    py_types: list[str]         # e.g. ["float", "int"]       (parallel)
    # arg_name → suffix index:  which constructor arg corresponds to which T
    # e.g. {"val": 0, "idx": 1}  — may have fewer entries than suffixes
    ctor_arg_to_suffix: dict[str, int] = field(default_factory=dict)
    docstring: str | None = None
    methods: list[MethodInfo] = field(default_factory=list)

    @property
    def arity(self) -> int:
        return len(self.suffixes)

    @property
    def suffix_key(self) -> tuple[str, ...]:
        """The map key for this variant: tuple of suffixes."""
        return tuple(self.suffixes)

    @property
    def dunder_methods(self) -> list[str]:
        return [m.name for m in self.methods
                if m.name.startswith("__") and m.name.endswith("__")]


@dataclass
class WrapperGroup:
    """All variants that share the same base_name → one unified wrapper."""
    base_name: str
    module_name: str        # stem with leading _ stripped  (attr names)
    import_name: str = ""   # exact stem as-is from file    (import stmt)
    variants: list[CppVariant] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.import_name:
            self.import_name = self.module_name

    @property
    def arity(self) -> int:
        """Template arity (number of type params). All variants share this."""
        return self.variants[0].arity if self.variants else 0

    @property
    def map_attr_name(self) -> str:
        """e.g.  Box → _type_map_box"""
        return f"_type_map_{self.base_name.lower()}"

    @property
    def all_ctor_arg_names(self) -> list[str]:
        """Union of constructor arg names across all variants (minus 'self')."""
        seen: dict[str, None] = {}
        for v in self.variants:
            for name, _ in v.ctor_arg_to_suffix.items():
                seen[name] = None
        return list(seen)

    @property
    def suffix_key_to_variant(self) -> dict[tuple[str, ...], CppVariant]:
        return {v.suffix_key: v for v in self.variants}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# New pattern: optional leading _, base name, then one or more __suffix groups
_VARIANT_RE = re.compile(
    r"^_?(?P<base>[A-Za-z][A-Za-z0-9]*)(?P<parts>(?:__[A-Za-z0-9_]+)+)$"
)

_KNOWN_DECORATORS = {"staticmethod", "classmethod", "property",
                     "abstractmethod", "overload"}


class StubParser:
    """Reads a .pyi / .py file and extracts WrapperGroups."""

    def __init__(self, pyi_path: Path, module_name: str | None = None) -> None:
        self.pyi_path    = pyi_path
        self.import_name = module_name or pyi_path.stem
        self.module_name = module_name or pyi_path.stem.lstrip("_")
        self._tree: ast.Module | None = None

    # ------------------------------------------------------------------
    def parse(self) -> list[WrapperGroup]:
        source     = self.pyi_path.read_text(encoding="utf-8")
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

            base     = m.group("base")
            # split __float32__int32 → ["float32", "int32"]
            suffixes = [s for s in m.group("parts").split("__") if s]

            # all suffixes must be in NUMPY_TYPE_MAP
            if not all(s in NUMPY_TYPE_MAP for s in suffixes):
                continue

            py_types = [NUMPY_TYPE_MAP[s] for s in suffixes]
            methods  = self._extract_methods(classdef)
            ctor_map = self._infer_ctor_arg_map(classdef, suffixes, py_types)

            variant = CppVariant(
                raw_name           = name,
                base_name          = base,
                suffixes           = suffixes,
                py_types           = py_types,
                ctor_arg_to_suffix = ctor_map,
                docstring          = self._extract_docstring(classdef),
                methods            = methods,
            )

            if base not in groups:
                groups[base] = WrapperGroup(
                    base_name   = base,
                    module_name = self.module_name,
                    import_name = self.import_name,
                )
            groups[base].variants.append(variant)

        return list(groups.values())

    # ------------------------------------------------------------------
    @staticmethod
    def _infer_ctor_arg_map(
        classdef: ast.ClassDef,
        suffixes: list[str],
        py_types: list[str],
    ) -> dict[str, int]:
        """
        Infer which constructor argument corresponds to which template type.

        Strategy: walk the __init__ args (skip 'self'), match each argument's
        Python type annotation against py_types in order.

        Returns {arg_name: suffix_index}.
        """
        init_node = next(
            (n for n in classdef.body
             if isinstance(n, ast.FunctionDef) and n.name == "__init__"),
            None,
        )
        if init_node is None:
            return {}

        result: dict[str, int] = {}
        suffix_used = [False] * len(suffixes)

        for arg in init_node.args.args:
            if arg.arg == "self":
                continue
            ann = StubParser._ann_to_str(arg.annotation)
            if ann is None:
                continue
            # try to match annotation to an unused py_type
            for i, (pt, used) in enumerate(zip(py_types, suffix_used)):
                if not used and ann == pt:
                    result[arg.arg] = i
                    suffix_used[i] = True
                    break

        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_docstring(node: ast.ClassDef | ast.FunctionDef) -> str | None:
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            return node.body[0].value.value
        return None

    # ------------------------------------------------------------------
    @classmethod
    def _extract_methods(cls, classdef: ast.ClassDef) -> list[MethodInfo]:
        result: list[MethodInfo] = []
        for node in classdef.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in ("__init__", "__new__", "__class__"):
                continue
            result.append(MethodInfo(
                name               = node.name,
                args               = cls._parse_args(node),
                return_annotation  = cls._ann_to_str(node.returns),
                docstring          = cls._extract_docstring(node),
                decorators         = cls._parse_decorators(node),
            ))
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_decorators(func: ast.FunctionDef) -> list[str]:
        return [ast.unparse(d) for d in func.decorator_list
                if ast.unparse(d) in _KNOWN_DECORATORS]

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_args(func: ast.FunctionDef) -> list[tuple[str, str | None]]:
        result = []
        for arg in func.args.args:
            result.append((arg.arg, StubParser._ann_to_str(arg.annotation)))
        if func.args.vararg:
            result.append((f"*{func.args.vararg.arg}",
                            StubParser._ann_to_str(func.args.vararg.annotation)))
        if func.args.kwarg:
            result.append((f"**{func.args.kwarg.arg}",
                            StubParser._ann_to_str(func.args.kwarg.annotation)))
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _ann_to_str(node: ast.expr | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{StubParser._ann_to_str(node.value)}.{node.attr}"
        if isinstance(node, ast.Subscript):
            return (f"{StubParser._ann_to_str(node.value)}"
                    f"[{StubParser._ann_to_str(node.slice)}]")
        if isinstance(node, ast.Tuple):
            return ", ".join(StubParser._ann_to_str(e) or "object"
                             for e in node.elts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return (f"{StubParser._ann_to_str(node.left)}"
                    f" | {StubParser._ann_to_str(node.right)}")
        return ast.unparse(node)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VARIANT_NAME_RE = re.compile(
    r"\b_?[A-Za-z][A-Za-z0-9]*(?:__[A-Za-z0-9_]+)+\b"
)


def _rewrite_names(text: str, wrapper_name: str) -> str:
    """Replace every variant-looking name in text with wrapper_name."""
    if not text:
        return text
    return _VARIANT_NAME_RE.sub(wrapper_name, text)


# ---------------------------------------------------------------------------
# Code generator
# ---------------------------------------------------------------------------

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

_BIN_OPS: frozenset[str] = frozenset({
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
})


class CodeGenerator:
    """Turns a WrapperGroup into a Python source string."""

    def __init__(self, group: WrapperGroup) -> None:
        self.group = group

    # ------------------------------------------------------------------
    def generate(self) -> str:
        g        = self.group
        mod      = g.module_name
        base     = g.base_name
        map_attr = g.map_attr_name
        arity    = g.arity

        # Collect all unique suffix-tuples
        all_keys     = sorted(g.suffix_key_to_variant.keys())
        all_suffixes = sorted({s for v in g.variants for s in v.suffixes})

        # ctor arg names that map to template positions
        ctor_args = g.all_ctor_arg_names   # e.g. ["val", "idx"]

        # Build union annotation for each template position
        # pos_unions[i] = Union of all py_types at position i
        pos_unions: list[str] = []
        for i in range(arity):
            pts = list(dict.fromkeys(
                v.py_types[i] for v in g.variants if i < len(v.py_types)
            ))
            pos_unions.append(self._union_annotation(pts))

        # Full signature union: all distinct py_type combos
        full_union = self._union_annotation(
            list(dict.fromkeys(pt for v in g.variants for pt in v.py_types))
        )

        L: list[str] = []

        # ── imports ────────────────────────────────────────────────────
        L += [
            "import typing",
            f"import {g.import_name}",
            "from abc import ABC as _ABC",
            "",
            "try:",
            "    import numpy as np",
            "    _NP_AVAILABLE = True",
            "except ImportError:",
            "    np = None  # type: ignore[assignment]",
            "    _NP_AVAILABLE = False",
            "",
            f"_NUMPY_TYPE_MAP: typing.Dict[str, type] = {_NUMPY_TYPE_MAP_REPR}",
            "",
        ]

        # ── class docstring ────────────────────────────────────────────
        variant_list = ", ".join(
            f"``{v.raw_name}``"
            for v in sorted(g.variants, key=lambda v: v.suffix_key)
        )
        variant_docs = self._collect_variant_docs(g, base)
        keys_repr = ", ".join(str(k) for k in all_keys)
        suffixes_repr = ", ".join(f"'{s}'" for s in all_suffixes)

        dtypes_doc = self._dtypes_doc(g, ctor_args)

        L += [f"class {base}(_ABC):"]
        L.append(f'    """Unified wrapper for :class:`{base}` template variants.')
        L.append(f"")
        L.append(f"    Wraps: {variant_list}")
        if variant_docs:
            L.append(f"")
            for line in variant_docs.splitlines():
                L.append(f"    {line}" if line.strip() else "")
        L += [
            f"",
            f"    Auto-generated by polybind.",
            f"",
            f"    Template arity: {arity}",
            f"    Valid dtype key-tuples: {keys_repr}",
            f"",
            f"    Args:",
        ]
        for line in dtypes_doc.splitlines():
            L.append(f"        {line}")
        L += [f'    """', f"", f"    __slots__ = ('_impl',)", f""]

        # ── _type_map_xxx  key=(suffix_tuple) → C++ class ──────────────
        map_items = ", ".join(
            f"{v.suffix_key!r}: {g.import_name}.{v.raw_name}"
            for v in sorted(g.variants, key=lambda v: v.suffix_key)
        )
        L += [
            f"    #: Maps tuple-of-suffixes → concrete C++ variant class.",
            f"    {map_attr}: typing.ClassVar[typing.Dict[tuple, type]]"
            f" = {{{map_items}}}",
            f"",
        ]

        # ── __new__ ────────────────────────────────────────────────────
        L += self._new_method(g, ctor_args, pos_unions, all_keys, full_union)
        L.append("")

        # ── __init__ ───────────────────────────────────────────────────
        L += self._init_signature(g, ctor_args, pos_unions, full_union)
        L.append("")

        # ── public methods ─────────────────────────────────────────────
        public_methods = self._collect_public_methods(g)
        if public_methods:
            L.append("    # -- public methods (from stub) --")
        for minfo in public_methods:
            L += self._method_stub(minfo, base, g)
            L.append("")

        # ── dunder stubs ───────────────────────────────────────────────
        all_dunders: set[str] = set()
        for v in g.variants:
            all_dunders.update(v.dunder_methods)
        needed_dunders = sorted(all_dunders & _PROXY_DUNDERS)
        if needed_dunders:
            L.append("    # -- delegated dunder methods --")
        for dunder in needed_dunders:
            minfo = self._find_method_info(g, dunder)
            L += self._dunder_stub(dunder, minfo, base, g)
            L.append("")

        if "__repr__" not in needed_dunders:
            L += [
                "    def __repr__(self) -> str:",
                '        """Return repr of the underlying C++ object."""',
                "        return repr(self._impl)",
                "",
            ]

        # ── __class_getitem__ ──────────────────────────────────────────
        L += [
            "    @classmethod",
            f"    def __class_getitem__(cls, item: typing.Union[str, tuple]) -> type:",
            f'        """Return the C++ variant class for a suffix or suffix-tuple.',
            f"",
            f"        Examples::",
            f"",
            f"            {base}['float32']             # single-type variant",
            f"            {base}[('float32', 'int32')]  # multi-type variant",
            f'        """',
            f"        if isinstance(item, str):",
            f"            item = (item,)",
            f"        return cls.{map_attr}.get(item, cls)",
            "",
        ]

        # ── register virtual subclasses ────────────────────────────────
        L += [
            f"# Register C++ variant classes as virtual subclasses of {base}.",
            f"for _t in {base}.{map_attr}.values():",
            f"    {base}.register(_t)",
        ]

        return "\n".join(L) + "\n"

    # ------------------------------------------------------------------
    def _new_method(
        self,
        g: WrapperGroup,
        ctor_args: list[str],
        pos_unions: list[str],
        all_keys: list[tuple[str, ...]],
        full_union: str,
    ) -> list[str]:
        base        = g.base_name
        map_attr    = g.map_attr_name
        arity       = g.arity
        keys_repr   = ", ".join(str(k) for k in all_keys)
        n_ctor_args = len(ctor_args)
        args_joined = ", ".join(ctor_args)

        params = ["        cls,"]
        for i, arg in enumerate(ctor_args):
            ann = pos_unions[i] if i < len(pos_unions) else "object"
            params.append(f"        {arg}: {ann},")
        extra_needed = arity - n_ctor_args
        for i in range(extra_needed):
            params.append(f"        _t{i}: object = None,  # extra template arg (no ctor mapping)")
        params.append(
            "        dtypes: typing.Optional["
            "typing.Union[typing.List[str], typing.Dict[str, str]]] = None,"
        )

        L: list[str] = []
        L.append(f"    def __new__(")
        L.extend(params)
        L += [
            f"    ) -> '{base}':",
            f'        """Construct a {base} instance.',
            f"",
            f"        Resolves the correct C++ variant from *dtypes* or by",
            f"        inferring types from the supplied arguments.",
            f"",
            f"        Valid dtype key-tuples: {keys_repr}",
            f'        """',
            f"        # ── resolve suffix key ────────────────────────────────",
            f"        _key: typing.Optional[tuple] = None",
            f"        if dtypes is None or (isinstance(dtypes, dict) and not dtypes):",
        ]

        if n_ctor_args >= arity:
            # Can auto-detect from argument types
            L += [
                f"            # auto-detect: find variant whose py_types match argument types",
                f"            _arg_pytypes = tuple(type(a).__name__ for a in [{args_joined}][:{arity}])",
                f"            for _k in cls.{map_attr}:",
                f"                _k_pytypes = tuple(getattr(_NUMPY_TYPE_MAP.get(s, s), '__name__', str(_NUMPY_TYPE_MAP.get(s, s))) for s in _k)",
                f"                if _k_pytypes == _arg_pytypes:",
                f"                    _key = _k",
                f"                    break",
                f"            if _key is None:",
                f"                raise TypeError(",
                f"                    f\"{{cls.__name__}}: cannot auto-detect variant for \"",
                f"                    f\"argument types {{_arg_pytypes}}. \"",
                f"                    f\"Valid: {keys_repr}\"",
                f"                )",
            ]
        else:
            # Not enough ctor args — dtypes list required
            L += [
                f"            raise TypeError(",
                f"                f\"{{cls.__name__}} has {arity} template type(s) but only "
                f"{n_ctor_args} constructor arg(s). \"",
                f"                \"Pass dtypes as a list: dtypes=['T1', ...']\"",
                f"            )",
            ]

        # Build representative ctor_arg_to_suffix across all variants
        all_maps: dict[str, int] = {}
        for v in g.variants:
            for arg, idx in v.ctor_arg_to_suffix.items():
                if arg not in all_maps:
                    all_maps[arg] = idx

        L += [
            f"        elif isinstance(dtypes, list):",
            f"            _norm: list[str] = []",
            f"            for _d in dtypes:",
            f"                if _NP_AVAILABLE and isinstance(_d, np.dtype):  # type: ignore",
            f"                    _d = _d.name",
            f"                _norm.append(str(_d))",
            f"            _key = tuple(_norm)",
            f"        elif isinstance(dtypes, dict):",
            f"            _parts: list[str] = [''] * {arity}",
        ]
        for arg, idx in all_maps.items():
            if idx < arity:
                L.append(
                    f"            if '{arg}' in dtypes: _parts[{idx}] = dtypes['{arg}']"
                )
        for arg, idx in all_maps.items():
            if idx < arity:
                L.append(
                    f"            if not _parts[{idx}]: _parts[{idx}] = next("
                    f"(s for s, t in _NUMPY_TYPE_MAP.items() if t == type({arg}).__name__), "
                    f"type({arg}).__name__)"
                )
        L += [
            f"            _key = tuple(_parts)",
            f"        else:",
            f"            raise TypeError(f\"dtypes must be None, list, or dict — got {{type(dtypes).__name__}}\")",
            f"",
            f"        cpp_cls = cls.{map_attr}.get(_key)",
            f"        if cpp_cls is None:",
            f"            raise TypeError(",
            f"                f\"{{cls.__name__}}: no variant for key {{_key}}. \"",
            f"                f\"Valid: {keys_repr}\"",
            f"            )",
            f"        obj = object.__new__(cls)",
            f"        object.__setattr__(obj, '_impl', cpp_cls(*[{args_joined}]))",
            f"        return obj",
        ]
        return L


    # ------------------------------------------------------------------
    def _init_signature(
        self,
        g: WrapperGroup,
        ctor_args: list[str],
        pos_unions: list[str],
        full_union: str,
    ) -> list[str]:
        L = ["    def __init__(", "        self,"]
        for i, arg in enumerate(ctor_args):
            ann = pos_unions[i] if i < len(pos_unions) else "object"
            L.append(f"        {arg}: {ann},")
        L += [
            "        dtypes: typing.Optional["
            "typing.Union[typing.List[str], typing.Dict[str, str]]] = None,",
            "    ) -> None:",
            "        pass  # __new__ handles construction",
        ]
        return L

    # ------------------------------------------------------------------
    @staticmethod
    def _dtypes_doc(g: WrapperGroup, ctor_args: list[str]) -> str:
        arity       = g.arity
        n_ctor_args = len(ctor_args)
        lines = []
        for i, arg in enumerate(ctor_args):
            union = CodeGenerator._union_annotation(
                list(dict.fromkeys(
                    v.py_types[i] for v in g.variants if i < len(v.py_types)
                ))
            )
            lines.append(f"{arg}: {union}")
        lines.append(
            "dtypes: None | list[str] | dict[str,str] — controls variant selection."
        )
        lines.append(
            "  None / {} → auto-detect from argument types"
            + (" (all positions inferrable)" if n_ctor_args >= arity
               else " — NOT possible here, dtypes list required")
        )
        lines.append("  list  → ['float32','int32'] in suffix order")
        lines.append("  dict  → {'val':'float32'} partial, rest auto-detected")
        if n_ctor_args < arity:
            lines.append(
                f"  NOTE: {arity} template types but only {n_ctor_args} ctor arg(s) "
                "→ dtypes list is REQUIRED."
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    @staticmethod
    def _collect_variant_docs(g: WrapperGroup, base: str) -> str:
        seen: set[str] = set()
        parts: list[str] = []
        for v in sorted(g.variants, key=lambda v: v.suffix_key):
            if v.docstring:
                cleaned = _rewrite_names(v.docstring.strip(), base)
                if cleaned not in seen:
                    parts.append(cleaned)
                    seen.add(cleaned)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    @staticmethod
    def _collect_public_methods(g: WrapperGroup) -> list[MethodInfo]:
        best: dict[str, MethodInfo] = {}
        for variant in g.variants:
            for minfo in variant.methods:
                if minfo.name.startswith("__"):
                    continue
                existing = best.get(minfo.name)
                if existing is None or len(minfo.args) > len(existing.args):
                    best[minfo.name] = minfo
        return list(best.values())

    # ------------------------------------------------------------------
    @staticmethod
    def _find_method_info(g: WrapperGroup, name: str) -> MethodInfo | None:
        for variant in g.variants:
            for minfo in variant.methods:
                if minfo.name == name:
                    return minfo
        return None

    # ------------------------------------------------------------------
    def _method_stub(
        self,
        minfo: MethodInfo,
        base: str,
        g: WrapperGroup,
    ) -> list[str]:
        lines: list[str] = []
        for dec in minfo.decorators:
            lines.append(f"    @{dec}")
        sig_parts = self._build_sig_parts(minfo, base)
        ret       = self._rewrite_return_ann(minfo.return_annotation, g)
        ret_ann   = f" -> {ret}" if ret else ""
        lines.append(f"    def {minfo.name}({', '.join(sig_parts)}){ret_ann}:")
        if minfo.docstring:
            lines.append(f'        """{_rewrite_names(minfo.docstring, base)}"""')

        skip = {"self", "cls"}
        call_args = ", ".join(
            a[0].lstrip("*") for a in minfo.args if a[0] not in skip
        )
        if "staticmethod" in minfo.decorators:
            # staticmethod has no cls — use the class name directly
            lines.append(f"        for _scls in {base}.{g.map_attr_name}.values():")
            lines.append(f"            if hasattr(_scls, '{minfo.name}'):")
            lines.append(f"                _raw = _scls.{minfo.name}({call_args})")
            lines.append(f"                _obj = object.__new__({base})")
            lines.append(f"                object.__setattr__(_obj, '_impl', _raw)")
            lines.append(f"                return _obj")
            lines.append(f"        raise AttributeError('{base}' + ' has no staticmethod {minfo.name}')")
        elif "classmethod" in minfo.decorators:
            lines.append(f"        for _cls in cls.{g.map_attr_name}.values():")
            lines.append(f"            if hasattr(_cls, '{minfo.name}'):")
            lines.append(f"                _raw = _cls.{minfo.name}({call_args})")
            lines.append(f"                _obj = object.__new__(cls)")
            lines.append(f"                object.__setattr__(_obj, '_impl', _raw)")
            lines.append(f"                return _obj")
            lines.append(f"        raise AttributeError(cls.__name__ + ' has no classmethod {minfo.name}')")
        elif "property" in minfo.decorators:
            lines.append(f"        return self._impl.{minfo.name}")
        else:
            lines.append(f"        return self._impl.{minfo.name}({call_args})")
        return lines

    # ------------------------------------------------------------------
    def _dunder_stub(
        self,
        name: str,
        minfo: MethodInfo | None,
        base: str,
        g: WrapperGroup,
    ) -> list[str]:
        _SIGS: dict[str, tuple[str, str, str]] = {
            "__len__":      ("self",                              "int",             "return len(self._impl)"),
            "__bool__":     ("self",                              "bool",            "return bool(self._impl)"),
            "__hash__":     ("self",                              "int",             "return hash(self._impl)"),
            "__repr__":     ("self",                              "str",             "return repr(self._impl)"),
            "__str__":      ("self",                              "str",             "return str(self._impl)"),
            "__int__":      ("self",                              "int",             "return int(self._impl)"),
            "__float__":    ("self",                              "float",           "return float(self._impl)"),
            "__complex__":  ("self",                              "complex",         "return complex(self._impl)"),
            "__index__":    ("self",                              "int",             "return self._impl.__index__()"),
            "__abs__":      ("self",                              "object",          "return abs(self._impl)"),
            "__neg__":      ("self",                              "object",          "return -self._impl"),
            "__pos__":      ("self",                              "object",          "return +self._impl"),
            "__invert__":   ("self",                              "object",          "return ~self._impl"),
            "__iter__":     ("self",                              "typing.Iterator", "return iter(self._impl)"),
            "__next__":     ("self",                              "object",          "return next(self._impl)"),
            "__reversed__": ("self",                              "typing.Iterator", "return reversed(self._impl)"),
            "__contains__": ("self, item: object",                "bool",            "return item in self._impl"),
            "__getitem__":  ("self, key: object",                 "object",          "return self._impl[key]"),
            "__setitem__":  ("self, key: object, value: object",  "None",            "self._impl[key] = value"),
            "__delitem__":  ("self, key: object",                 "None",            "del self._impl[key]"),
            "__call__":     ("self, *args: object, **kwargs: object", "object",      "return self._impl(*args, **kwargs)"),
            "__enter__":    ("self",                              "object",          "return self._impl.__enter__()"),
            "__exit__":     ("self, *args: object",               "object",          "return self._impl.__exit__(*args)"),
            "__reduce__":   ("self",                              "object",          "return self._impl.__reduce__()"),
            "__copy__":     ("self",                              "object",          "return self._impl.__copy__()"),
            "__deepcopy__": ("self, memo: dict",                  "object",          "return self._impl.__deepcopy__(memo)"),
        }
        if name in _SIGS:
            params, ret_type, body = _SIGS[name]
        elif name in _BIN_OPS:
            all_py = list(dict.fromkeys(pt for v in g.variants for pt in v.py_types))
            other_ann = self._union_annotation([f"'{base}'"] + all_py)
            params    = f"self, other: {other_ann}"
            ret_type  = "object"
            body      = (f"return self._impl.{name}"
                         f"(other._impl if isinstance(other, {base}) else other)")
        else:
            params, ret_type = "self, *args: object, **kwargs: object", "object"
            body = f"return self._impl.{name}(*args, **kwargs)"

        if minfo and len(minfo.args) > 1:
            params   = ", ".join(self._build_sig_parts(minfo, base))
            ret_type = self._rewrite_return_ann(minfo.return_annotation, g) or ret_type

        lines: list[str] = []
        if minfo:
            for dec in minfo.decorators:
                lines.append(f"    @{dec}")
        lines.append(f"    def {name}({params}) -> {ret_type}:")
        if minfo and minfo.docstring:
            lines.append(f'        """{_rewrite_names(minfo.docstring, base)}"""')
        lines.append(f"        {body}")
        return lines

    # ------------------------------------------------------------------
    @staticmethod
    def _build_sig_parts(minfo: MethodInfo, base: str) -> list[str]:
        _re = re.compile(
            r"_?[A-Za-z][A-Za-z0-9]*(?:__[A-Za-z0-9_]+)+"
        )
        parts = []
        for arg_name, ann in minfo.args:
            if ann is None:
                parts.append(arg_name)
            else:
                parts.append(f"{arg_name}: {_re.sub(base, ann)}")
        return parts

    # ------------------------------------------------------------------
    @staticmethod
    def _rewrite_return_ann(ann: str | None, g: WrapperGroup) -> str | None:
        if ann is None:
            return None
        return _rewrite_names(ann, g.base_name)

    # ------------------------------------------------------------------
    @staticmethod
    def _union_annotation(py_types: list[str]) -> str:
        unique = list(dict.fromkeys(py_types))
        if len(unique) == 1:
            return unique[0]
        return "typing.Union[" + ", ".join(unique) + "]"


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

class PolybindGenerator:
    """
    Orchestrates parsing and code generation for a single .pyi/.py stub.

    Usage::

        gen = PolybindGenerator(Path("_novasvg.pyi"))
        gen.run(output_path=Path("novasvg.py"))
    """

    def __init__(
        self,
        pyi_path: Path,
        module_name: str | None = None,
    ) -> None:
        self.pyi_path    = Path(pyi_path)
        self.module_name = module_name
        self._parser     = StubParser(self.pyi_path, module_name)

    def generate_source(self) -> str:
        groups = self._parser.parse()
        if not groups:
            raise ValueError(
                f"No template variants found in '{self.pyi_path}'. "
                "Make sure class names follow the BaseName__T1[__T2] convention "
                "(e.g. Box__int32, Matrix__float64__int32)."
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

    def run(self, output_path: Path) -> list[WrapperGroup]:
        output_path = Path(output_path)
        source = self.generate_source()
        output_path.write_text(source, encoding="utf-8")
        return self._parser.parse()
