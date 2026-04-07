"""
tests/test_core.py
~~~~~~~~~~~~~~~~~~
Unit tests for polybind.core — no C extension required.
Naming convention: BaseName__T1[__T2...]  (double underscore separator)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from polybind.core import (
    CodeGenerator,
    MethodInfo,
    PolybindGenerator,
    StubParser,
    WrapperGroup,
    CppVariant,
    NUMPY_TYPE_MAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_pyi(tmp_path: Path, content: str, name: str = "_mymod.pyi") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def make_variant(raw: str, base: str, suffixes: list[str]) -> CppVariant:
    """Convenience: build a CppVariant with minimal fields."""
    return CppVariant(
        raw_name=raw,
        base_name=base,
        suffixes=suffixes,
        py_types=[NUMPY_TYPE_MAP[s] for s in suffixes],
    )


def make_group(base: str = "Box", mod: str = "mymod",
               variants: list[CppVariant] | None = None) -> WrapperGroup:
    g = WrapperGroup(base_name=base, module_name=mod, import_name=mod)
    g.variants = variants or [
        make_variant("Box__int32",   "Box", ["int32"]),
        make_variant("Box__float64", "Box", ["float64"]),
    ]
    return g


# ---------------------------------------------------------------------------
# StubParser
# ---------------------------------------------------------------------------

class TestStubParser:

    def test_detects_basic_variants(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box__int32:
                def __init__(self, val: int) -> None: ...
            class Box__float64:
                def __init__(self, val: float) -> None: ...
        """)
        groups = StubParser(pyi).parse()

        assert len(groups) == 1
        group = groups[0]
        assert group.base_name == "Box"
        all_suffixes = {s for v in group.variants for s in v.suffixes}
        assert all_suffixes == {"int32", "float64"}

    def test_strips_leading_underscore(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class _Box__int32:
                def __init__(self, val: int) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        assert len(groups) == 1
        assert groups[0].base_name == "Box"
        assert groups[0].variants[0].raw_name == "_Box__int32"

    def test_ignores_non_template_classes(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Renderer:
                pass
            class Box__int32:
                def __init__(self, val: int) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        names = {g.base_name for g in groups}
        assert "Renderer" not in names
        assert "Box" in names

    def test_ignores_unknown_suffix(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box__foobar:
                pass
        """)
        groups = StubParser(pyi).parse()
        assert groups == []

    def test_multiple_base_names(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box__int32:
                def __init__(self, val: int) -> None: ...
            class Matrix__float64:
                def __init__(self, val: float) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        base_names = {g.base_name for g in groups}
        assert base_names == {"Box", "Matrix"}

    def test_multi_type_variant(self, tmp_path):
        """Two-type variant Box__float64__int32."""
        pyi = make_pyi(tmp_path, """\
            class _Pair__float64__int32:
                def __init__(self, first: float, second: int) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        assert len(groups) == 1
        v = groups[0].variants[0]
        assert v.suffixes == ["float64", "int32"]
        assert v.py_types == ["float", "int"]
        assert v.arity == 2

    def test_extracts_dunders(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box__int32:
                def __init__(self, val: int) -> None: ...
                def __add__(self, other: Box__int32) -> Box__int32: ...
                def __repr__(self) -> str: ...
        """)
        groups = StubParser(pyi).parse()
        dunders = set(groups[0].variants[0].dunder_methods)
        assert "__add__" in dunders
        assert "__repr__" in dunders
        assert "__init__" not in dunders

    def test_infers_ctor_arg_map(self, tmp_path):
        """Parser infers which ctor arg maps to which template position."""
        pyi = make_pyi(tmp_path, """\
            class _Pair__float64__int32:
                def __init__(self, first: float, second: int) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        m = groups[0].variants[0].ctor_arg_to_suffix
        assert m.get("first") == 0   # float → T1
        assert m.get("second") == 1  # int   → T2

    def test_module_name_strips_underscore(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box__int32:
                def __init__(self, val: int) -> None: ...
        """, name="_novasvg.pyi")
        groups = StubParser(pyi).parse()
        assert groups[0].module_name == "novasvg"

    def test_module_name_override(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box__int32:
                def __init__(self, val: int) -> None: ...
        """)
        groups = StubParser(pyi, module_name="custom").parse()
        assert groups[0].module_name == "custom"


# ---------------------------------------------------------------------------
# WrapperGroup
# ---------------------------------------------------------------------------

class TestWrapperGroup:

    def test_suffix_key_to_variant(self):
        g = make_group()
        assert ("int32",)   in g.suffix_key_to_variant
        assert ("float64",) in g.suffix_key_to_variant

    def test_map_attr_name(self):
        g = make_group(base="Box")
        assert g.map_attr_name == "_type_map_box"

    def test_arity_single(self):
        g = make_group()
        assert g.arity == 1

    def test_arity_multi(self):
        g = make_group(base="Pair")
        g.variants = [
            make_variant("Pair__float64__int32", "Pair", ["float64", "int32"]),
        ]
        assert g.arity == 2

    def test_all_ctor_arg_names(self):
        g = make_group(base="Pair")
        v = make_variant("Pair__float64__int32", "Pair", ["float64", "int32"])
        v.ctor_arg_to_suffix = {"first": 0, "second": 1}
        g.variants = [v]
        assert set(g.all_ctor_arg_names) == {"first", "second"}


# ---------------------------------------------------------------------------
# CodeGenerator (source-level checks, no import)
# ---------------------------------------------------------------------------

class TestCodeGenerator:

    def test_class_definition_present(self):
        src = CodeGenerator(make_group()).generate()
        assert "class Box(_ABC):" in src

    def test_type_map_present(self):
        src = CodeGenerator(make_group()).generate()
        assert "_type_map_" in src

    def test_suffix_key_in_map(self):
        src = CodeGenerator(make_group()).generate()
        assert "('int32',)" in src
        assert "('float64',)" in src

    def test_import_uses_module_name(self):
        src = CodeGenerator(make_group(mod="mymod")).generate()
        assert "import mymod" in src

    def test_slots_present(self):
        src = CodeGenerator(make_group()).generate()
        assert "__slots__" in src

    def test_dtypes_param_present(self):
        src = CodeGenerator(make_group()).generate()
        assert "dtypes" in src

    def test_dunder_stubs_generated(self):
        g = make_group()
        g.variants[0].methods = [
            MethodInfo(name="__add__",  args=[("self", None), ("other", None)],
                       return_annotation=None, docstring=None),
            MethodInfo(name="__repr__", args=[("self", None)],
                       return_annotation="str", docstring=None),
        ]
        src = CodeGenerator(g).generate()
        assert "def __add__" in src
        assert "def __repr__" in src

    def test_class_getitem_present(self):
        src = CodeGenerator(make_group()).generate()
        assert "__class_getitem__" in src

    def test_multi_type_map_keys(self):
        """Multi-type variant produces tuple keys in the map."""
        g = make_group(base="Pair")
        g.variants = [
            make_variant("Pair__float64__int32", "Pair", ["float64", "int32"]),
            make_variant("Pair__int32__int64",   "Pair", ["int32",   "int64"]),
        ]
        src = CodeGenerator(g).generate()
        assert "('float64', 'int32')" in src
        assert "('int32', 'int64')" in src


# ---------------------------------------------------------------------------
# PolybindGenerator integration (no real C extension)
# ---------------------------------------------------------------------------

class TestPolybindGenerator:

    def test_raises_on_empty_stub(self, tmp_path):
        pyi = make_pyi(tmp_path, "class Renderer: pass\n")
        gen = PolybindGenerator(pyi)
        with pytest.raises(ValueError, match="No template variants"):
            gen.generate_source()

    def test_generates_and_writes_file(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box__int32:
                def __init__(self, val: int) -> None: ...
        """)
        out = tmp_path / "box.py"
        gen = PolybindGenerator(pyi)
        gen.run(output_path=out)
        assert out.exists()
        content = out.read_text()
        assert "class Box(_ABC):" in content
        assert "auto-generated by polybind" in content.lower()

    def test_header_contains_source_filename(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class _Box__int32:
                def __init__(self, val: int) -> None: ...
        """, name="_novasvg.pyi")
        gen = PolybindGenerator(pyi)
        src = gen.generate_source()
        assert "_novasvg.pyi" in src


# ---------------------------------------------------------------------------
# CLI  (subprocess)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(_REPO_ROOT),
    }
    return subprocess.run(
        [sys.executable, "-m", "polybind", *[str(a) for a in args]],
        capture_output=True,
        text=True,
        env=env,
    )


# Fixture uses new __ naming convention
_FIXTURE_PYI = """\
class _Tensor__int32:
    def __init__(self, val: int) -> None: ...
    def __add__(self, other: _Tensor__int32) -> _Tensor__int32: ...
    def __repr__(self) -> str: ...

class _Tensor__float64:
    def __init__(self, val: float) -> None: ...
    def __mul__(self, other: _Tensor__float64) -> _Tensor__float64: ...

class _Tensor__bool_:
    def __init__(self, val: bool) -> None: ...
    def __bool__(self) -> bool: ...

class Renderer:
    \"\"\"Should be ignored — no numpy-type suffix.\"\"\"
    pass
"""


class TestCLI:

    @pytest.fixture()
    def stub_pyi(self, tmp_path: Path) -> Path:
        pyi = tmp_path / "_myengine.pyi"
        pyi.write_text(textwrap.dedent(_FIXTURE_PYI), encoding="utf-8")
        return pyi.resolve()

    def test_basic_run_exit_zero(self, stub_pyi, tmp_path):
        out = (tmp_path / "myengine.py").resolve()
        result = _run_cli(stub_pyi, "-o", out)
        assert result.returncode == 0, (
            f"CLI exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert out.exists()

    def test_default_output_path(self, stub_pyi, tmp_path):
        result = _run_cli(stub_pyi)
        assert result.returncode == 0, result.stderr
        expected = stub_pyi.parent / "myengine.py"
        assert expected.exists(), f"Expected default output at {expected}"

    def test_output_contains_class_definitions(self, stub_pyi, tmp_path):
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)
        content = out.read_text(encoding="utf-8")
        assert "class Tensor(_ABC):" in content

    def test_output_contains_type_map(self, stub_pyi, tmp_path):
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)
        content = out.read_text(encoding="utf-8")
        assert "_type_map_" in content
        assert "_Tensor__int32"   in content
        assert "_Tensor__float64" in content
        assert "_Tensor__bool_"   in content

    def test_output_header_mentions_source_file(self, stub_pyi, tmp_path):
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)
        content = out.read_text(encoding="utf-8")
        assert "_myengine.pyi" in content

    def test_non_template_class_not_in_output(self, stub_pyi, tmp_path):
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)
        content = out.read_text(encoding="utf-8")
        assert "class Renderer:" not in content

    def test_dunder_stubs_in_output(self, stub_pyi, tmp_path):
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)
        content = out.read_text(encoding="utf-8")
        assert "def __add__" in content
        assert "def __mul__" in content
        assert "def __bool__" in content

    def test_dry_run_prints_to_stdout(self, stub_pyi, tmp_path):
        out = tmp_path / "should_not_exist.py"
        result = _run_cli(stub_pyi, "-o", out, "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "class Tensor(_ABC):" in result.stdout
        assert not out.exists()

    def test_dry_run_stdout_is_valid_python(self, stub_pyi):
        import ast as _ast
        result = _run_cli(stub_pyi, "--dry-run")
        assert result.returncode == 0, result.stderr
        try:
            _ast.parse(result.stdout)
        except SyntaxError as exc:
            pytest.fail(f"dry-run output is not valid Python: {exc}\n{result.stdout}")

    def test_verbose_mentions_variants(self, stub_pyi, tmp_path):
        out = tmp_path / "myengine.py"
        result = _run_cli(stub_pyi, "-o", out, "--verbose")
        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert "Tensor" in combined

    def test_module_name_override_in_output(self, stub_pyi, tmp_path):
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out, "--module-name", "custom_engine")
        content = out.read_text(encoding="utf-8")
        assert "import custom_engine" in content
        assert "import _myengine" not in content

    def test_missing_input_file_exits_nonzero(self, tmp_path):
        missing = (tmp_path / "ghost.pyi").resolve()
        result = _run_cli(missing)
        assert result.returncode != 0
        assert "error" in result.stderr.lower()

    def test_stub_with_no_variants_exits_nonzero(self, tmp_path):
        pyi = tmp_path / "_empty.pyi"
        pyi.write_text("class Renderer: pass\n", encoding="utf-8")
        result = _run_cli(pyi.resolve())
        assert result.returncode != 0
        assert "error" in result.stderr.lower()
