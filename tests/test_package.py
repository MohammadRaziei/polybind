"""tests/test_package.py: the Python API of the installed package (``import polybind``)."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import polybind

REPO_ROOT = Path(__file__).resolve().parent.parent


def _python(code: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=REPO_ROOT.parent)


@pytest.mark.parametrize("name", [n for n in polybind.__all__ if n != "__version__"])
def test_the_generator_api_is_importable(name: str) -> None:
    assert getattr(polybind, name) is getattr(__import__("polybind.core", fromlist=[name]), name)


def test_version_is_a_string() -> None:
    assert isinstance(polybind.__version__, str) and polybind.__version__


def test_import_does_not_load_the_generator() -> None:
    # `polybind version` and the build-time providers import the package; they must not pay for the stub parser
    out = _python("import sys, polybind; print('polybind.core' in sys.modules)")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_the_first_use_loads_it() -> None:
    out = _python("import sys, polybind; polybind.PolybindGenerator; print('polybind.core' in sys.modules)")
    assert out.stdout.strip() == "True", out.stderr


def test_an_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        polybind.nope  # noqa: B018


def test_dir_lists_the_api() -> None:
    assert set(polybind.__all__) <= set(dir(polybind))


def test_import_works_from_the_parent_directory() -> None:
    # regression: a __init__.py in the repository root shadowed the package when run from the directory above it
    out = _python("import polybind.core, polybind.cli; print('ok')")
    assert out.stdout.strip() == "ok", out.stderr


@pytest.mark.parametrize("name", ["cli", "config", "deps", "errors", "gen", "install", "lock", "metadata", "version"])
def test_no_name_in_the_package_hides_a_submodule(name: str) -> None:
    # `from polybind import version` must give the module, not something `__init__` imported under that name
    module = importlib.import_module("polybind")
    assert not hasattr(module, name) or type(getattr(module, name)).__name__ == "module"
