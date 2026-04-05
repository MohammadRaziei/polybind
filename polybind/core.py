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
NUMPY_TYPE_MAP: dict[str, str] = {
    "int8":    "int",
    "int16":   "int",
    "int32":   "int",
    "int64":   "int",
    "uint8":   "int",
    "uint16":  "int",
    "uint32":  "int",
    "uint64":  "int",
    "float32": "float",
    "float64": "float",
    "bool_":   "bool",
    "str_":    "str",
    "bytes_":  "bytes",
    "int":     "int",
    "float":   "float",
    "bool":    "bool",
    "str":     "str",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MethodInfo:
    """Signature + docstring for one method parsed from the .pyi."""
    name: str
    args: list[tuple[str, str | None]]   # [(arg_name, annotation_or_None), ...]
    return_annotation: str | None
    docstring: str | None


@dataclass
class CppVariant:
    """One concrete C++ specialisation, e.g. _Box_int32 or Box_float64."""
    raw_name: str
    base_name: str
    type_suffix: str
    py_type: str
    docstring: str | None = None
    methods: list[MethodInfo] = field(default_factory=list)

    @property
    def dunder_methods(self) -> list[str]:
        return [m.name for m in self.methods
                if m.name.startswith("__") and m.name.endswith("__")]


@dataclass
class WrapperGroup:
    """All variants that share the same base_name -> one unified wrapper."""
    base_name: str
    module_name: str
    variants: list[CppVariant] = field(default_factory=list)

    @property
    def by_py_type(self) -> dict[str, CppVariant]:
        result: dict[str, CppVariant] = {}
        for v in self.variants:
            result[v.py_type] = v
        return result

    @property
    def map_attr_name(self) -> str:
        """_type_map_box  (lowercased from base_name)."""
        return f"_type_map_{self.base_name.lower()}"

    @property
    def suffix_map_attr_name(self) -> str:
        """_suffix_map_box  (lowercased from base_name)."""
        return f"_suffix_map_{self.base_name.lower()}"


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
        self.module_name = module_name or pyi_path.stem.lstrip("_")
        self._tree: ast.Module | None = None

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
            base   = m.group("base")
            suffix = m.group("suffix")
            if suffix not in NUMPY_TYPE_MAP:
                continue

            variant = CppVariant(
                raw_name=name,
                base_name=base,
                type_suffix=suffix,
                py_type=NUMPY_TYPE_MAP[suffix],
                docstring=self._extract_docstring(classdef),
                methods=self._extract_methods(classdef),
            )
            if base not in groups:
                groups[base] = WrapperGroup(base_name=base, module_name=self.module_name)
            groups[base].variants.append(variant)

        return list(groups.values())

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

    @classmethod
    def _extract_methods(cls, classdef: ast.ClassDef) -> list[MethodInfo]:
        result: list[MethodInfo] = []
        for node in classdef.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in ("__init__", "__new__", "__class__"):
                continue
            result.append(MethodInfo(
                name=node.name,
                args=cls._parse_args(node),
                return_annotation=cls._ann_to_str(node.returns),
                docstring=cls._extract_docstring(node),
            ))
        return result

    @staticmethod
    def _parse_args(func: ast.FunctionDef) -> list[tuple[str, str | None]]:
        result = []
        for arg in func.args.args:
            ann = StubParser._ann_to_str(arg.annotation)
            result.append((arg.arg, ann))
        if func.args.vararg:
            ann = StubParser._ann_to_str(func.args.vararg.annotation)
            result.append((f"*{func.args.vararg.arg}", ann))
        if func.args.kwarg:
            ann = StubParser._ann_to_str(func.args.kwarg.annotation)
            result.append((f"**{func.args.kwarg.arg}", ann))
        return result

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
            return f"{StubParser._ann_to_str(node.value)}[{StubParser._ann_to_str(node.slice)}]"
        if isinstance(node, ast.Tuple):
            return ", ".join(StubParser._ann_to_str(e) or "object" for e in node.elts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return f"{StubParser._ann_to_str(node.left)} | {StubParser._ann_to_str(node.right)}"
        return ast.unparse(node)


# ---------------------------------------------------------------------------
# Docstring rewriting
# ---------------------------------------------------------------------------

def _rewrite_docstring(raw: str, raw_class_name: str, wrapper_name: str) -> str:
    """Replace occurrences of the C++ variant name with the wrapper name."""
    if not raw or not raw_class_name:
        return raw
    return re.sub(re.escape(raw_class_name), wrapper_name, raw)


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
        g           = self.group
        mod         = g.module_name
        base        = g.base_name
        map_attr    = g.map_attr_name
        suffix_attr = g.suffix_map_attr_name
        py_types    = list(g.by_py_type.keys())
        union_type  = self._union_annotation(py_types)
        all_suffixes = sorted({v.type_suffix for v in g.variants})

        L: list[str] = []

        # ── imports ────────────────────────────────────────────────────
        L += [
            "import typing",
            f"import _{mod} as _{mod}_ext",
            "from abc import ABC as _ABC",
            "",
            "try:",
            "    import numpy as np",
            "    _NP_AVAILABLE = True",
            "except ImportError:",
            "    np = None  # type: ignore[assignment]",
            "    _NP_AVAILABLE = False",
            "",
        ]

        # ── class header + docstring ───────────────────────────────────
        variant_list = ", ".join(
            f"``{v.raw_name}``"
            for v in sorted(g.variants, key=lambda v: v.type_suffix)
        )
        suffixes_repr = ", ".join(f"'{s}'" for s in all_suffixes)

        L += [
            f"class {base}(_ABC):",
            f'    """Unified wrapper for :class:`{base}` template variants.',
            f"",
            f"    Wraps: {variant_list}",
            f"",
            f"    Auto-generated by polybind.",
            f"",
            f"    Args:",
            f"        val: Input value. Accepted Python types: ``{union_type}``.",
            f"        dtype: Optional numpy-style dtype string (e.g. ``'float64'``).",
            f"               Accepted values: {suffixes_repr}.",
            f"               When given, overrides automatic type inference.",
            f"               ``np.dtype`` objects are also accepted if numpy is installed.",
            f'    """',
            f"",
            f"    __slots__ = ('_impl',)",
            f"",
        ]

        # ── _type_map_xxx ──────────────────────────────────────────────
        L.append(f"    #: Maps Python built-in type -> concrete C++ variant class.")
        L.append(f"    {map_attr}: typing.ClassVar[typing.Dict[type, type]] = {{")
        for py_type, variant in g.by_py_type.items():
            L.append(f"        {py_type}: _{mod}_ext.{variant.raw_name},")
        L += ["    }", ""]

        # ── _suffix_map_xxx ────────────────────────────────────────────
        L.append(f"    #: Maps numpy-style dtype string -> concrete C++ variant class.")
        L.append(f"    {suffix_attr}: typing.ClassVar[typing.Dict[str, type]] = {{")
        for variant in sorted(g.variants, key=lambda v: v.type_suffix):
            L.append(f"        '{variant.type_suffix}': _{mod}_ext.{variant.raw_name},")
        L += ["    }", ""]

        # ── __new__ ────────────────────────────────────────────────────
        L += self._new_method(base, map_attr, suffix_attr, union_type, all_suffixes)
        L.append("")

        # ── __init__ ───────────────────────────────────────────────────
        L += [
            f"    def __init__(",
            f"        self,",
            f"        val: {union_type},",
            f"        dtype: typing.Optional[str] = None,",
            f"    ) -> None:",
            f"        pass  # __new__ handles construction",
            f"",
        ]

        # ── explicit public methods ────────────────────────────────────
        public_methods = self._collect_public_methods(g)
        if public_methods:
            L.append("    # -- public methods (from stub) --")
        for minfo in public_methods:
            L += self._public_method_stub(minfo, base, g)
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
            L += self._dunder_stub(dunder, minfo, base)
            L.append("")

        # ── __repr__ fallback ──────────────────────────────────────────
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
            f"    def __class_getitem__(cls, item: type) -> type:",
            f'        """Return the C++ variant class for the given Python type.',
            f"",
            f"        Example::",
            f"",
            f"            {base}[int]   # -> the underlying C++ int variant",
            f'        """',
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
        base: str,
        map_attr: str,
        suffix_attr: str,
        union_type: str,
        all_suffixes: list[str],
    ) -> list[str]:
        suffixes_repr = ", ".join(f"'{s}'" for s in all_suffixes)
        return [
            f"    def __new__(",
            f"        cls,",
            f"        val: {union_type},",
            f"        dtype: typing.Optional[str] = None,",
            f"    ) -> '{base}':",
            f'        """Construct a {base} instance.',
            f"",
            f"        Selects the correct C++ variant based on ``type(val)``",
            f"        unless ``dtype`` is explicitly given.",
            f"        Accepted dtype strings: {suffixes_repr}.",
            f'        """',
            f"        if dtype is not None:",
            f"            if _NP_AVAILABLE and isinstance(dtype, np.dtype):  # type: ignore[union-attr]",
            f"                dtype = dtype.name",
            f"            cpp_cls = cls.{suffix_attr}.get(dtype)",
            f"            if cpp_cls is None:",
            f"                raise TypeError(",
            f"                    f\"{{cls.__name__}}: unknown dtype '{{dtype}}'."
            f" Valid: {suffixes_repr}\"",
            f"                )",
            f"        else:",
            f"            cpp_cls = cls.{map_attr}.get(type(val))",
            f"            if cpp_cls is None:",
            f"                raise TypeError(",
            f"                    f\"{{cls.__name__}} does not support"
            f" type '{{type(val).__name__}}'\"",
            f"                )",
            f"        obj = object.__new__(cls)",
            f"        object.__setattr__(obj, '_impl', cpp_cls(val))",
            f"        return obj",
        ]

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
    def _public_method_stub(
        self,
        minfo: MethodInfo,
        base: str,
        g: WrapperGroup,
    ) -> list[str]:
        sig_parts = self._build_sig_parts(minfo, base)
        ret = self._rewrite_return_ann(minfo.return_annotation, g)
        ret_ann = f" -> {ret}" if ret else ""

        lines = [f"    def {minfo.name}({', '.join(sig_parts)}){ret_ann}:"]
        if minfo.docstring:
            doc = _rewrite_docstring(
                minfo.docstring,
                g.variants[0].raw_name if g.variants else "",
                base,
            )
            lines.append(f'        """{doc}"""')

        non_self = [
            a[0].lstrip("*")
            for a in minfo.args
            if a[0] not in ("self", "cls")
        ]
        lines.append(f"        return self._impl.{minfo.name}({', '.join(non_self)})")
        return lines

    # ------------------------------------------------------------------
    def _dunder_stub(
        self,
        name: str,
        minfo: MethodInfo | None,
        base: str,
    ) -> list[str]:
        g = self.group

        _SIGS: dict[str, tuple[str, str, str]] = {
            "__len__":      ("self",                           "int",              "return len(self._impl)"),
            "__bool__":     ("self",                           "bool",             "return bool(self._impl)"),
            "__hash__":     ("self",                           "int",              "return hash(self._impl)"),
            "__repr__":     ("self",                           "str",              "return repr(self._impl)"),
            "__str__":      ("self",                           "str",              "return str(self._impl)"),
            "__int__":      ("self",                           "int",              "return int(self._impl)"),
            "__float__":    ("self",                           "float",            "return float(self._impl)"),
            "__complex__":  ("self",                           "complex",          "return complex(self._impl)"),
            "__index__":    ("self",                           "int",              "return self._impl.__index__()"),
            "__abs__":      ("self",                           "object",           "return abs(self._impl)"),
            "__neg__":      ("self",                           "object",           "return -self._impl"),
            "__pos__":      ("self",                           "object",           "return +self._impl"),
            "__invert__":   ("self",                           "object",           "return ~self._impl"),
            "__iter__":     ("self",                           "typing.Iterator",  "return iter(self._impl)"),
            "__next__":     ("self",                           "object",           "return next(self._impl)"),
            "__reversed__": ("self",                           "typing.Iterator",  "return reversed(self._impl)"),
            "__contains__": ("self, item: object",             "bool",             "return item in self._impl"),
            "__getitem__":  ("self, key: object",              "object",           "return self._impl[key]"),
            "__setitem__":  ("self, key: object, value: object", "None",           "self._impl[key] = value"),
            "__delitem__":  ("self, key: object",              "None",             "del self._impl[key]"),
            "__call__":     ("self, *args: object, **kwargs: object", "object",    "return self._impl(*args, **kwargs)"),
            "__enter__":    ("self",                           "object",           "return self._impl.__enter__()"),
            "__exit__":     ("self, *args: object",            "object",           "return self._impl.__exit__(*args)"),
            "__reduce__":   ("self",                           "object",           "return self._impl.__reduce__()"),
            "__copy__":     ("self",                           "object",           "return self._impl.__copy__()"),
            "__deepcopy__": ("self, memo: dict",               "object",           "return self._impl.__deepcopy__(memo)"),
        }

        if name in _SIGS:
            params, ret_type, body = _SIGS[name]
        elif name in _BIN_OPS:
            py_types  = list(g.by_py_type.keys())
            other_ann = self._union_annotation([f"'{base}'"] + py_types)
            params    = f"self, other: {other_ann}"
            ret_type  = "object"
            unwrap    = f"other._impl if isinstance(other, {base}) else other"
            body      = f"return self._impl.{name}({unwrap})"
        else:
            params   = "self, *args: object, **kwargs: object"
            ret_type = "object"
            body     = f"return self._impl.{name}(*args, **kwargs)"

        # prefer .pyi signature if richer
        if minfo is not None and len(minfo.args) > 1:
            sig_parts = self._build_sig_parts(minfo, base)
            params    = ", ".join(sig_parts)
            ret_type  = self._rewrite_return_ann(minfo.return_annotation, g) or ret_type

        lines = [f"    def {name}({params}) -> {ret_type}:"]
        if minfo and minfo.docstring:
            doc = _rewrite_docstring(
                minfo.docstring,
                g.variants[0].raw_name if g.variants else "",
                base,
            )
            lines.append(f'        """{doc}"""')
        lines.append(f"        {body}")
        return lines

    # ------------------------------------------------------------------
    @staticmethod
    def _build_sig_parts(minfo: MethodInfo, base: str) -> list[str]:
        """Build parameter list, rewriting raw C++ variant names to base."""
        _variant_ann_re = re.compile(
            r"_?[A-Za-z][A-Za-z0-9]*_(?:int|float|uint|bool|str|bytes)\w*"
        )
        parts = []
        for arg_name, ann in minfo.args:
            if ann is None:
                parts.append(arg_name)
            else:
                clean = _variant_ann_re.sub(base, ann)
                parts.append(f"{arg_name}: {clean}")
        return parts

    # ------------------------------------------------------------------
    @staticmethod
    def _rewrite_return_ann(ann: str | None, g: WrapperGroup) -> str | None:
        if ann is None:
            return None
        for variant in g.variants:
            ann = re.sub(re.escape(variant.raw_name), g.base_name, ann)
        return ann

    # ------------------------------------------------------------------
    @staticmethod
    def _union_annotation(py_types: list[str]) -> str:
        """
        Build typing.Union[...] string (Python 3.9-compatible).
        Uses X | Y only for single pair to stay safe.
        """
        unique = list(dict.fromkeys(py_types))
        if len(unique) == 1:
            return unique[0]
        return "typing.Union[" + ", ".join(unique) + "]"


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
        self.pyi_path    = Path(pyi_path)
        self.module_name = module_name
        self._parser     = StubParser(self.pyi_path, module_name)

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

    def run(self, output_path: Path) -> list[WrapperGroup]:
        output_path = Path(output_path)
        source = self.generate_source()
        output_path.write_text(source, encoding="utf-8")
        return self._parser.parse()