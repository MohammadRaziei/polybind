"""
tests/test_core.py
~~~~~~~~~~~~~~~~~~
Unit tests for polybind.core — no C extension required.
We test the parser and code generator against synthetic .pyi content.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from polybind.core import (
    CodeGenerator,
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


# ---------------------------------------------------------------------------
# StubParser
# ---------------------------------------------------------------------------

class TestStubParser:

    def test_detects_basic_variants(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box_int32:
                def __init__(self, val: int) -> None: ...
            class Box_float64:
                def __init__(self, val: float) -> None: ...
        """)
        groups = StubParser(pyi).parse()

        assert len(groups) == 1
        group = groups[0]
        assert group.base_name == "Box"
        suffixes = {v.type_suffix for v in group.variants}
        assert suffixes == {"int32", "float64"}

    def test_strips_leading_underscore(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class _Box_int32:
                def __init__(self, val: int) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        assert len(groups) == 1
        assert groups[0].base_name == "Box"
        assert groups[0].variants[0].raw_name == "_Box_int32"

    def test_ignores_non_template_classes(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Renderer:
                pass
            class Box_int32:
                def __init__(self, val: int) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        names = {g.base_name for g in groups}
        assert "Renderer" not in names
        assert "Box" in names

    def test_ignores_unknown_suffix(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box_foobar:
                pass
        """)
        groups = StubParser(pyi).parse()
        assert groups == []

    def test_multiple_base_names(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box_int32:
                def __init__(self, val: int) -> None: ...
            class Matrix_float64:
                def __init__(self, val: float) -> None: ...
        """)
        groups = StubParser(pyi).parse()
        base_names = {g.base_name for g in groups}
        assert base_names == {"Box", "Matrix"}

    def test_extracts_dunders(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box_int32:
                def __init__(self, val: int) -> None: ...
                def __add__(self, other: Box_int32) -> Box_int32: ...
                def __repr__(self) -> str: ...
        """)
        groups = StubParser(pyi).parse()
        dunders = set(groups[0].variants[0].dunder_methods)
        assert "__add__" in dunders
        assert "__repr__" in dunders
        assert "__init__" not in dunders  # excluded

    def test_module_name_strips_underscore(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box_int32:
                def __init__(self, val: int) -> None: ...
        """, name="_novasvg.pyi")
        groups = StubParser(pyi).parse()
        assert groups[0].module_name == "novasvg"

    def test_module_name_override(self, tmp_path):
        pyi = make_pyi(tmp_path, """\
            class Box_int32:
                def __init__(self, val: int) -> None: ...
        """)
        groups = StubParser(pyi, module_name="custom").parse()
        assert groups[0].module_name == "custom"


# ---------------------------------------------------------------------------
# WrapperGroup.by_py_type
# ---------------------------------------------------------------------------

class TestWrapperGroup:

    def test_by_py_type_last_wins(self):
        g = WrapperGroup(base_name="Box", module_name="mod")
        g.variants = [
            CppVariant("Box_int32", "Box", "int32", "int"),
            CppVariant("Box_int64", "Box", "int64", "int"),  # both map to int
        ]
        # last one wins
        assert g.by_py_type["int"].type_suffix == "int64"

    def test_by_py_type_multiple_types(self):
        g = WrapperGroup(base_name="Box", module_name="mod")
        g.variants = [
            CppVariant("Box_int32",   "Box", "int32",   "int"),
            CppVariant("Box_float64", "Box", "float64", "float"),
            CppVariant("Box_str_",    "Box", "str_",    "str"),
        ]
        assert set(g.by_py_type.keys()) == {"int", "float", "str"}


# ---------------------------------------------------------------------------
# CodeGenerator (source-level checks, no import)
# ---------------------------------------------------------------------------

class TestCodeGenerator:

    def _make_group(self, base="Box", mod="mymod", variants=None) -> WrapperGroup:
        g = WrapperGroup(base_name=base, module_name=mod)
        g.variants = variants or [
            CppVariant("Box_int32",   "Box", "int32",   "int"),
            CppVariant("Box_float64", "Box", "float64", "float"),
        ]
        return g

    def test_class_definition_present(self):
        src = CodeGenerator(self._make_group()).generate()
        assert "class Box(_ABC):" in src

    def test_type_map_present(self):
        src = CodeGenerator(self._make_group()).generate()
        assert "_type_map_" in src

    def test_import_uses_private_module(self):
        # import_name == module_name when constructed directly (no pyi stem)
        # so import_name="mymod" → "import mymod" (no underscore added)
        src = CodeGenerator(self._make_group(mod="mymod")).generate()
        assert "import mymod" in src

    def test_slots_present(self):
        src = CodeGenerator(self._make_group()).generate()
        assert "__slots__" in src

    def test_dunder_stubs_generated(self):
        from polybind.core import MethodInfo
        g = self._make_group()
        # dunder_methods is a computed property — inject via methods list
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
        src = CodeGenerator(self._make_group()).generate()
        assert "__class_getitem__" in src


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
            class Box_int32:
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
            class _Box_int32:
                def __init__(self, val: int) -> None: ...
        """, name="_novasvg.pyi")
        gen = PolybindGenerator(pyi)
        src = gen.generate_source()
        assert "_novasvg.pyi" in src


# ---------------------------------------------------------------------------
# CLI  (subprocess — uses the installed entry-point or python -m polybind)
# ---------------------------------------------------------------------------

# Absolute path to the polybind package so the subprocess finds it even when
# the package is not installed into the active environment.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """
    Invoke  python -m polybind <args>  as a real subprocess.
    PYTHONPATH is set to the repo root so polybind is importable by absolute
    path regardless of whether it has been pip-installed.
    """
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


# Realistic stub that a nanobind/pybind11 stubgen might produce.
_FIXTURE_PYI = """\
class _Tensor_int32:
    def __init__(self, val: int) -> None: ...
    def __add__(self, other: _Tensor_int32) -> _Tensor_int32: ...
    def __repr__(self) -> str: ...

class _Tensor_float64:
    def __init__(self, val: float) -> None: ...
    def __mul__(self, other: _Tensor_float64) -> _Tensor_float64: ...

class _Tensor_bool_:
    def __init__(self, val: bool) -> None: ...
    def __bool__(self) -> bool: ...

class Renderer:
    \"\"\"Should be ignored — no numpy-type suffix.\"\"\"
    pass
"""


class TestCLI:

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture()
    def stub_pyi(self, tmp_path: Path) -> Path:
        """Write the fixture .pyi to a real temp file and return its absolute path."""
        pyi = tmp_path / "_myengine.pyi"
        pyi.write_text(textwrap.dedent(_FIXTURE_PYI), encoding="utf-8")
        return pyi.resolve()   # ← absolute path

    # ------------------------------------------------------------------
    # Happy-path
    # ------------------------------------------------------------------

    def test_basic_run_exit_zero(self, stub_pyi, tmp_path):
        """CLI exits 0 and creates the output file."""
        out = (tmp_path / "myengine.py").resolve()
        result = _run_cli(stub_pyi, "-o", out)

        assert result.returncode == 0, (
            f"CLI exited {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert out.exists(), "Output file was not created"

    def test_default_output_path(self, stub_pyi, tmp_path):
        """Without -o, output is written as <stem-without-underscore>.py next to input."""
        result = _run_cli(stub_pyi)

        assert result.returncode == 0, result.stderr
        expected = stub_pyi.parent / "myengine.py"
        assert expected.exists(), f"Expected default output at {expected}"

    def test_output_contains_class_definitions(self, stub_pyi, tmp_path):
        """Generated file must contain a class for every discovered base name."""
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)

        content = out.read_text(encoding="utf-8")
        assert "class Tensor(_ABC):" in content

    def test_output_contains_type_map(self, stub_pyi, tmp_path):
        """_TYPE_MAP_VAL must be present and reference the C++ classes."""
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)

        content = out.read_text(encoding="utf-8")
        assert "_type_map_" in content   # attr name is _type_map_<classname_lower>
        # all three raw variant names must appear
        assert "_Tensor_int32" in content
        assert "_Tensor_float64" in content
        assert "_Tensor_bool_" in content

    def test_output_header_mentions_source_file(self, stub_pyi, tmp_path):
        """The auto-generated header must name the source .pyi."""
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)

        content = out.read_text(encoding="utf-8")
        assert "_myengine.pyi" in content

    def test_non_template_class_not_in_output(self, stub_pyi, tmp_path):
        """Renderer has no numpy suffix — must not appear as a wrapper class."""
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)

        content = out.read_text(encoding="utf-8")
        # 'Renderer' may appear in a comment but not as a class definition
        assert "class Renderer:" not in content

    def test_dunder_stubs_in_output(self, stub_pyi, tmp_path):
        """Dunder methods present in the stub must be delegated in the wrapper."""
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out)

        content = out.read_text(encoding="utf-8")
        assert "def __add__" in content
        assert "def __mul__" in content
        assert "def __bool__" in content

    # ------------------------------------------------------------------
    # --dry-run
    # ------------------------------------------------------------------

    def test_dry_run_prints_to_stdout(self, stub_pyi, tmp_path):
        """--dry-run must print source to stdout and not write any file."""
        out = tmp_path / "should_not_exist.py"
        result = _run_cli(stub_pyi, "-o", out, "--dry-run")

        assert result.returncode == 0, result.stderr
        assert "class Tensor(_ABC):" in result.stdout
        assert not out.exists(), "--dry-run must not write a file"

    def test_dry_run_stdout_is_valid_python(self, stub_pyi):
        """The dry-run output must be parseable Python source."""
        import ast as _ast
        result = _run_cli(stub_pyi, "--dry-run")

        assert result.returncode == 0, result.stderr
        try:
            _ast.parse(result.stdout)
        except SyntaxError as exc:
            pytest.fail(f"dry-run output is not valid Python: {exc}\n{result.stdout}")

    # ------------------------------------------------------------------
    # --verbose
    # ------------------------------------------------------------------

    def test_verbose_mentions_variants(self, stub_pyi, tmp_path):
        """--verbose output must list the discovered variant names."""
        out = tmp_path / "myengine.py"
        result = _run_cli(stub_pyi, "-o", out, "--verbose")

        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert "Tensor" in combined

    # ------------------------------------------------------------------
    # --module-name override
    # ------------------------------------------------------------------

    def test_module_name_override_in_output(self, stub_pyi, tmp_path):
        """--module-name must replace the import target in generated code."""
        out = tmp_path / "myengine.py"
        _run_cli(stub_pyi, "-o", out, "--module-name", "custom_engine")

        content = out.read_text(encoding="utf-8")
        assert "import custom_engine" in content
        assert "import _myengine" not in content

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_missing_input_file_exits_nonzero(self, tmp_path):
        """Passing a nonexistent .pyi must exit with a non-zero code."""
        missing = (tmp_path / "ghost.pyi").resolve()
        result = _run_cli(missing)

        assert result.returncode != 0
        assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_stub_with_no_variants_exits_nonzero(self, tmp_path):
        """A .pyi with no recognised template classes must exit non-zero."""
        pyi = tmp_path / "_empty.pyi"
        pyi.write_text("class Renderer: pass\n", encoding="utf-8")
        result = _run_cli(pyi.resolve())

        assert result.returncode != 0
        assert "error" in result.stderr.lower()
