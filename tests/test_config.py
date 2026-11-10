"""tests/test_config.py: ``.polybind`` / ``[tool.polybind]`` settings and their use by ``version``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from polybind import config as cfg
from polybind.config import ConfigError, load_config
from polybind.version import main

REPO_ROOT = Path(__file__).resolve().parent.parent

HEADER = "#define ACME_VERSION_MAJOR 1\n#define ACME_VERSION_MINOR 2\n#define ACME_VERSION_PATCH 3\n"
OTHER = "#define OTHER_VERSION_MAJOR 7\n#define OTHER_VERSION_MINOR 8\n#define OTHER_VERSION_PATCH 9\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "acme.h").write_text(HEADER)
    (tmp_path / "include" / "other.h").write_text(OTHER)  # two headers: needs a setting
    return tmp_path


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def test_empty_project(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.data == {}
    assert config.sources == []


def test_pyproject_only(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "pyproject.toml").write_text('[tool.polybind.version]\nfile = "include/acme.h"\n')
    assert run(project) == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


def test_dotfile_overrides_pyproject(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "pyproject.toml").write_text('[tool.polybind.version]\nfile = "include/acme.h"\n')
    (project / ".polybind").write_text('[version]\nfile = "include/other.h"\n')
    assert load_config(project).sources == [project / "pyproject.toml", project / ".polybind"]
    assert run(project) == 0
    assert capsys.readouterr().out.strip() == "7.8.9"


def test_merge_is_per_key(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        '[tool.polybind.version]\nfile = "include/acme.h"\ntag_format = "a-%s"\n'
    )
    (project / ".polybind").write_text('[version]\ntag_format = "b-%s"\n')
    config = load_config(project)
    assert config.get("version", "file") == "include/acme.h"
    assert config.get("version", "tag_format") == "b-%s"


def test_cli_overrides_settings(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / ".polybind").write_text('[version]\nfile = "include/other.h"\n')
    assert run(project, "--file", "include/acme.h") == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


def test_prefix_and_tag_format_from_settings(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / ".polybind").write_text(
        '[version]\nfile = "include/acme.h"\nprefix = "ACME_"\ntag_format = "rel-%s"\n'
    )
    assert run(project, "tag", "create", "--dry-run") == 0
    assert "tag rel-1.2.3" in capsys.readouterr().out
    assert run(project, "tag", "create", "--dry-run", "--tag-format", "x%s") == 0
    assert "tag x1.2.3" in capsys.readouterr().out


def test_unknown_key_suggests(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / ".polybind").write_text('[version]\nfil = "include/acme.h"\n')
    assert run(project) == 2
    assert "did you mean 'version.file'" in capsys.readouterr().err


def test_unknown_section_and_types(tmp_path: Path) -> None:
    (tmp_path / ".polybind").write_text("[versoin]\nfile = 'x'\n")
    with pytest.raises(ConfigError, match="did you mean 'version'"):
        load_config(tmp_path)
    (tmp_path / ".polybind").write_text("[version]\nfile = 3\n")
    with pytest.raises(ConfigError, match="must be a string"):
        load_config(tmp_path)
    (tmp_path / ".polybind").write_text("version = 'x'\n")
    with pytest.raises(ConfigError, match="must be a table"):
        load_config(tmp_path)


def test_reserved_sections_pass_through(tmp_path: Path) -> None:
    (tmp_path / ".polybind").write_text("[deps.liburlparser]\nversion_spec = '>=2'\n[gen]\nx = 1\n")
    assert load_config(tmp_path).data["deps"]["liburlparser"]["version_spec"] == ">=2"


def test_bad_toml(tmp_path: Path) -> None:
    (tmp_path / ".polybind").write_text("[version\n")
    with pytest.raises(ConfigError, match=r"\.polybind"):
        load_config(tmp_path)


def test_tomli_fallback_and_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".polybind").write_text("[version]\nprefix = 'A_'\n")
    monkeypatch.setitem(sys.modules, "tomllib", None)  # as on Python 3.10
    pytest.importorskip("tomli")
    assert load_config(tmp_path).get("version", "prefix") == "A_"
    monkeypatch.setitem(sys.modules, "tomli", None)  # neither is installed
    with pytest.raises(ConfigError, match="pip install tomli"):
        load_config(tmp_path)


def test_no_toml_reader_needed_without_settings(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "3.2.1"\n')
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)
    assert run(project, "--file", "pyproject.toml") == 0
    assert capsys.readouterr().out.strip() == "3.2.1"


def test_config_command(project: Path) -> None:
    (project / ".polybind").write_text('[version]\nfile = "include/acme.h"\n')
    out = subprocess.run(
        [sys.executable, "-m", "polybind", "config", "--root", str(project)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0
    assert '"file": "include/acme.h"' in out.stdout
    assert cfg.CONFIG_FILE in out.stdout
