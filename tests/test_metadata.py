"""tests/test_metadata.py: polybind.metadata, the scikit-build-core provider (spec 7.2)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from polybind.config import ConfigError, read_toml
from polybind.metadata import Provider

SETTINGS = """\
schema = 1

[deps.liburlparser]
version-spec = ">=2.2.0,<3"
python = {}

[deps.mbedtls]
version-spec = ">=0.3,<0.4"
runtime = true
python = {}

[deps.cpponly]
fetch.url = "https://example.invalid/cpponly.git"

[optional-deps.test.tinylib]
runtime = true
python = {}
"""


def provider(tmp_path: Path, toml: str = SETTINGS) -> Provider:
    (tmp_path / "polybind.toml").write_text(toml)
    return Provider(tmp_path)


def test_build_requirements(tmp_path: Path) -> None:
    got = provider(tmp_path).get_requires_for_dynamic_metadata({})
    assert got == ["liburlparser>=2.2.0,<3", "mbedtls>=0.3,<0.4"]  # buildtime is on by default; no fetch-only dependency


def test_runtime_requirements_and_extras(tmp_path: Path) -> None:
    fragment = provider(tmp_path).dynamic_metadata({}, {"dynamic": ["dependencies", "optional-dependencies"]})
    assert fragment == {"dependencies": ["mbedtls>=0.3,<0.4"], "optional-dependencies": {"test": ["tinylib"]}}


def test_only_the_declared_fields_are_returned(tmp_path: Path) -> None:
    toml = '[deps.mbedtls]\nruntime = true\npython = {}\n'
    assert provider(tmp_path, toml).dynamic_metadata({}, {"dynamic": ["dependencies"]}) == {"dependencies": ["mbedtls"]}


def test_a_declared_field_without_content_is_empty_not_missing(tmp_path: Path) -> None:
    fragment = provider(tmp_path, "schema = 1\n").dynamic_metadata({}, {"dynamic": ["dependencies", "optional-dependencies"]})
    assert fragment == {"dependencies": [], "optional-dependencies": {}}


def test_runtime_requirements_without_a_dynamic_field_are_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="must list 'dependencies'"):
        provider(tmp_path).dynamic_metadata({}, {"dynamic": []})


def test_extras_without_a_dynamic_field_are_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="must list 'optional-dependencies'"):
        provider(tmp_path).dynamic_metadata({}, {"dynamic": ["dependencies"]})


def test_polybind_never_lists_itself(tmp_path: Path) -> None:
    toml = '[deps.polybind]\nruntime = true\npython = {}\n[deps.other]\nruntime = true\npython.package = "POLYBIND"\n'
    p = provider(tmp_path, toml)
    assert p.get_requires_for_dynamic_metadata({}) == []
    assert p.dynamic_metadata({}, {"dynamic": ["dependencies"]}) == {"dependencies": []}


def test_settings_are_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="takes no settings"):
        provider(tmp_path).get_requires_for_dynamic_metadata({"groups": ["test"]})


def test_bad_settings_are_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        provider(tmp_path, '[deps.x]\nversion_spec = ">=1"\n').get_requires_for_dynamic_metadata({})


def test_the_current_directory_is_the_default_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "polybind.toml").write_text(SETTINGS)
    monkeypatch.chdir(tmp_path)
    assert Provider().get_requires_for_dynamic_metadata({}) == ["liburlparser>=2.2.0,<3", "mbedtls>=0.3,<0.4"]


def test_settings_in_pyproject_work_too(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.polybind.deps.mbedtls]\npython = {}\n')
    assert Provider(tmp_path).get_requires_for_dynamic_metadata({}) == ["mbedtls"]


# --------------------------------------------------------------------------- with scikit-build-core itself

SKBUILD = importlib.util.find_spec("scikit_build_core") is not None
needs_skbuild = pytest.mark.skipif(not SKBUILD, reason="needs scikit-build-core")

PYPROJECT = """\
[build-system]
requires = ["scikit-build-core", "polybind"]
build-backend = "scikit_build_core.build"

[project]
name = "demo"
version = "0.1.0"
dynamic = ["dependencies", "optional-dependencies"]

[[tool.dynamic-metadata]]
provider = "polybind"

[tool.scikit-build]
wheel.packages = ["demo"]
cmake.build-type = "Release"
"""


def make_project(root: Path) -> Path:
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.21)\nproject(demo LANGUAGES NONE)\n")
    (root / "polybind.toml").write_text(SETTINGS)
    return root


REPO_ROOT = Path(__file__).resolve().parent.parent


def entry_points_dir(dest: Path) -> Path:
    """A dist-info with the entry point from our own pyproject.toml: polybind is on sys.path here, but not installed."""
    table = read_toml(REPO_ROOT / "pyproject.toml")["project"]["entry-points"]["dynamic_metadata.provider"]
    info = dest / "ep" / "polybind-0.0.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: polybind\nVersion: 0.0.0\n")
    lines = "".join(f"{name} = {target}\n" for name, target in table.items())
    (info / "entry_points.txt").write_text(f"[dynamic_metadata.provider]\n{lines}")
    return info.parent


def in_project(root: Path, code: str, entry_points: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(entry_points), str(REPO_ROOT)])}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)], cwd=root, env=env, capture_output=True, text=True, check=False
    )


@needs_skbuild
def test_scikit_build_core_asks_for_the_build_requirements(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    out = in_project(
        root, "import json, scikit_build_core.build as b; print(json.dumps(b.get_requires_for_build_wheel()))", entry_points_dir(tmp_path)
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.splitlines()[-1])
    assert "liburlparser>=2.2.0,<3" in got
    assert "mbedtls>=0.3,<0.4" in got
    assert not any(item.startswith("polybind") for item in got)


@needs_skbuild
def test_the_wheel_metadata_has_the_runtime_requirements(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    dist = tmp_path / "dist"
    out = in_project(
        root,
        f"""
        import scikit_build_core.build as b
        print(b.build_wheel({str(dist)!r}))
        """,
        entry_points_dir(tmp_path),
    )
    assert out.returncode == 0, out.stdout + out.stderr
    wheel = dist / out.stdout.splitlines()[-1]
    with zipfile.ZipFile(wheel) as archive:
        meta = next(archive.read(n).decode() for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
    assert "Requires-Dist: mbedtls<0.4,>=0.3" in meta
    assert "Provides-Extra: test" in meta
    assert 'Requires-Dist: tinylib; extra == "test"' in meta
    assert "liburlparser" not in meta  # buildtime only: it is not a runtime requirement
