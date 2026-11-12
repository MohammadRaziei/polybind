"""tests/test_install.py: ``polybind deps install`` and ``requirements --with-pyproject`` (spec 7.3)."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_resolver import make_wheel

from polybind.config import ConfigError, load_config
from polybind.deps import load_dependencies, main
from polybind.install import gather, read_project, requirement_name

REPO_ROOT = Path(__file__).resolve().parent.parent
UV = shutil.which("uv")

PYPROJECT = """\
[build-system]
requires = ["scikit-build-core>=0.10", "nanobind", "polybind"]

[project]
name = "demo"
dependencies = ["numpy>=1.24", "librosa>=0.10; python_version < '3.13'"]

[project.optional-dependencies]
test = ["pytest"]

[dependency-groups]
dev = ["ruff", {include-group = "lint"}]
lint = ["flake8"]
"""

POLYBIND = """\
[deps.liburlparser]
version-spec = ">=2.2.0,<3"
python = {}
runtime = true

[deps.mbedtls]
fetch.url = "https://example.invalid/mbedtls.git"

[optional-deps.test.httpp]
python = {}
runtime = true
buildtime = false
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "polybind.toml").write_text(POLYBIND)
    return tmp_path


def reqs(root: Path, *kinds: str) -> list[str]:
    return gather(read_project(root), load_dependencies(load_config(root)), list(kinds))


def test_build_has_the_python_build_requirements_and_the_native_ones_but_not_polybind(project: Path) -> None:
    assert reqs(project, "build") == ["scikit-build-core>=0.10", "nanobind", "liburlparser>=2.2.0,<3"]


def test_runtime_merges_both_sources_without_duplicates(project: Path) -> None:
    assert reqs(project, "runtime") == ["numpy>=1.24", "librosa>=0.10; python_version < '3.13'", "liburlparser>=2.2.0,<3"]


def test_extras_groups_and_includes(project: Path) -> None:
    assert reqs(project, "extra:test") == ["pytest", "httpp"]
    assert reqs(project, "group:dev") == ["ruff", "flake8"]
    assert reqs(project, "group:DEV") == ["ruff", "flake8"]  # names are normalised


def test_all(project: Path) -> None:
    everything = reqs(project, "all")
    for wanted in ("nanobind", "numpy>=1.24", "pytest", "httpp", "ruff", "flake8", "liburlparser>=2.2.0,<3"):
        assert wanted in everything
    assert len(everything) == len(set(everything))
    assert "polybind" not in everything


def test_errors(project: Path) -> None:
    for kind, message in [("extra:nope", "unknown extra 'nope' .known: test"), ("group:nope", "unknown dependency group"),
                          ("dev", "unknown kind")]:
        with pytest.raises(ConfigError, match=message):
            reqs(project, kind)
    (project / "pyproject.toml").write_text('[dependency-groups]\na = [{include-group = "b"}]\nb = [{include-group = "a"}]\n')
    with pytest.raises(ConfigError, match="include each other"):
        reqs(project, "group:a")
    (project / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = "numpy"\n')
    with pytest.raises(ConfigError, match="must be a list"):
        reqs(project, "runtime")


def test_the_buildtime_entries_of_an_installed_extra_join_build(project: Path) -> None:
    (project / "polybind.toml").write_text(POLYBIND + "\n[optional-deps.test.mbed]\npython = {}\n")
    assert "mbed" in reqs(project, "build", "extra:test")
    assert "mbed" not in reqs(project, "build")


def test_requirement_name() -> None:
    assert requirement_name("Foo_Bar>=1; python_version < '3.9'") == "foo-bar"
    assert requirement_name("polybind") == "polybind"


def test_no_pyproject_no_polybind_is_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install", "--root", str(tmp_path)]) == 0
    assert "nothing to install" in capsys.readouterr().out


def test_dry_run(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install", "--root", str(project), "--installer", "pip", "--dry-run", "--", "--target", "/x"]) == 0
    out = capsys.readouterr().out
    assert "would install 5 requirement(s)" in out
    line = next(ln for ln in out.splitlines() if ln.startswith("command: "))
    assert shlex.split(line[len("command: ") :]) == [
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--target", "/x",
        "scikit-build-core>=0.10", "nanobind", "liburlparser>=2.2.0,<3", "numpy>=1.24",
        "librosa>=0.10; python_version < '3.13'",
    ]  # quoted, so it can be pasted into a shell


def test_requirements_with_pyproject(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["requirements", "--root", str(project), "--kind", "group:dev", "--with-pyproject"]) == 0
    assert capsys.readouterr().out.split() == ["ruff", "flake8"]


@pytest.fixture
def wheelhouse(tmp_path: Path) -> dict[str, str]:
    make_wheel(tmp_path / "wheels", "fakelib", "2.2.0")
    links = str(tmp_path / "wheels")
    return {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": links, "UV_NO_INDEX": "1", "UV_FIND_LINKS": links, "PIP_NO_CACHE_DIR": "1"}


def run(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "polybind", "deps", "install", "--root", str(root), *args],
                          cwd=REPO_ROOT, env={**os.environ, **env, "PYTHONPATH": str(REPO_ROOT)},
                          capture_output=True, text=True, check=False)


@pytest.mark.parametrize("installer", ["pip", pytest.param("uv", marks=pytest.mark.skipif(UV is None, reason="needs uv"))])
def test_really_installs(tmp_path: Path, wheelhouse: dict[str, str], installer: str) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["fakelib>=2"]\n')
    target = tmp_path / "site"
    out = run(root, wheelhouse, "--kind", "runtime", "--installer", installer, "--", "--target", str(target))
    assert out.returncode == 0, out.stdout + out.stderr
    assert (target / "fakelib" / "__init__.py").is_file()


def test_a_failing_installer_returns_its_exit_code(tmp_path: Path, wheelhouse: dict[str, str]) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["does-not-exist-anywhere"]\n')
    out = run(root, wheelhouse, "--kind", "runtime", "--installer", "pip", "--", "--target", str(tmp_path / "s"))
    assert out.returncode != 0
