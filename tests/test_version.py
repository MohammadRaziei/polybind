"""tests/test_version.py: ``polybind version`` (header and pyproject sources, git tags)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from polybind.config import read_toml
from polybind.version import (
    HeaderSource,
    Version,
    VersionError,
    apply_value,
    detect_source,
    main,
    parse_tag,
    tag_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

HEADER = """\
#ifndef HTTPP_H
#define HTTPP_VERSION_MAJOR 1  // keep this comment
#define HTTPP_VERSION_MINOR (2)
#define HTTPP_VERSION_PATCH 3
#endif
"""

PYPROJECT = """\
[build-system]
requires = ["hatchling"]

[project]
name = "demo"
version = "0.4.1"  # static

[tool.other]
version = "9.9.9"
"""


@pytest.fixture
def header_project(tmp_path: Path) -> Path:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "httpp.h").write_text(HEADER)
    return tmp_path


@pytest.fixture
def py_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    return tmp_path


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


# --------------------------------------------------------------------------- Version


def test_parse_and_str() -> None:
    assert str(Version.parse("1.20.3")) == "1.20.3"
    for bad in ("1.2", "v1.2.3", "1.2.3.4", "a.b.c"):
        with pytest.raises(VersionError):
            Version.parse(bad)


def test_bump_resets_lower_parts() -> None:
    v = Version(1, 2, 3)
    v.bump("minor")
    assert str(v) == "1.3.0"
    v.bump("major")
    assert str(v) == "2.0.0"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("+", 3), ("+2", 4), ("-", 1), ("-5", 0), ("7", 7), ("0", 0)],
)
def test_apply_value(value: str, expected: int) -> None:
    v = Version(0, 0, 2)
    apply_value(v, "patch", value)
    assert v.patch == expected


@pytest.mark.parametrize("value", ["x", "+x", ""])
def test_apply_value_rejects(value: str) -> None:
    with pytest.raises(VersionError):
        apply_value(Version(0, 0, 0), "patch", value)


def test_tag_format_roundtrip() -> None:
    v = Version(1, 2, 3)
    assert tag_name(v, "v%s") == "v1.2.3"
    assert tag_name(v, "release-%s") == "release-1.2.3"
    assert parse_tag("release-1.2.3", "release-%s") == v
    assert parse_tag("1.2.3", "v%s") == v  # bare tag is accepted
    with pytest.raises(VersionError):
        parse_tag("foo", "v%s")
    with pytest.raises(VersionError):
        tag_name(v, "nope")


# --------------------------------------------------------------------------- header


def test_header_show_and_parts(header_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(header_project) == 0
    assert capsys.readouterr().out.strip() == "1.2.3"
    assert run(header_project, "minor") == 0
    assert capsys.readouterr().out.strip() == "2"


def test_header_write_keeps_format(header_project: Path) -> None:
    assert run(header_project, "major", "+") == 0
    assert run(header_project, "patch", "9") == 0
    text = (header_project / "include" / "httpp.h").read_text()
    assert "#define HTTPP_VERSION_MAJOR 2  // keep this comment" in text
    assert "#define HTTPP_VERSION_MINOR (2)" in text
    assert "#define HTTPP_VERSION_PATCH 9" in text


def test_dry_run_writes_nothing(header_project: Path) -> None:
    assert run(header_project, "major", "+", "--dry-run") == 0
    assert (header_project / "include" / "httpp.h").read_text() == HEADER


def test_header_prefix_detected_and_explicit(tmp_path: Path) -> None:
    (tmp_path / "v.h").write_text("#define VERSION_MAJOR 4\n#define VERSION_MINOR 5\n#define VERSION_PATCH 6\n")
    src = detect_source(tmp_path)
    assert isinstance(src, HeaderSource)
    assert src.prefix == ""
    assert str(src.read()) == "4.5.6"
    with pytest.raises(VersionError):
        HeaderSource(tmp_path / "v.h", "NOPE_").read()


def test_header_missing_part(tmp_path: Path) -> None:
    (tmp_path / "v.h").write_text("#define X_VERSION_MAJOR 1\n#define X_VERSION_MINOR 2\n")
    assert run(tmp_path, "--file", "v.h") == 2


def test_several_headers_need_file(header_project: Path) -> None:
    (header_project / "include" / "other.h").write_text(HEADER.replace("HTTPP", "OTHER"))
    assert run(header_project) == 2
    assert run(header_project, "--file", "include/other.h") == 0


# --------------------------------------------------------------------------- pyproject


def test_pyproject_read_write(py_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(py_project) == 0
    assert capsys.readouterr().out.strip() == "0.4.1"
    assert run(py_project, "minor", "+") == 0
    text = (py_project / "pyproject.toml").read_text()
    assert 'version = "0.5.1"  # static' in text
    assert 'version = "9.9.9"' in text  # [tool.other] untouched


def test_pyproject_dynamic_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndynamic = ["version"]\n')
    assert run(tmp_path) == 2


def test_no_source(tmp_path: Path) -> None:
    assert run(tmp_path) == 2


# --------------------------------------------------------------------------- git


@pytest.fixture
def git_project(header_project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }.items():
        monkeypatch.setenv(key, value)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=header_project, check=True, capture_output=True)
    return header_project


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_tag_none(git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(git_project, "tag") == 0
    assert "No git tags found" in capsys.readouterr().out
    assert run(git_project, "check") == 1
    assert run(git_project, "tag", "set") == 2


def test_tag_create_current_and_bump(git_project: Path) -> None:
    assert run(git_project, "tag", "create") == 0
    assert git(git_project, "describe", "--tags", "--abbrev=0") == "v1.2.3"
    assert run(git_project, "check") == 0
    assert run(git_project, "tag", "create", "minor") == 0
    assert git(git_project, "describe", "--tags", "--abbrev=0") == "v1.3.0"
    assert "1.3.0" in git(git_project, "log", "-1", "--format=%s")
    assert run(git_project, "tag", "create", "v2.0.0") == 0
    assert run(git_project, "major") == 0


def test_tag_create_refuses_existing_and_commits_only_the_file(git_project: Path) -> None:
    assert run(git_project, "tag", "create") == 0
    assert run(git_project, "tag", "create", "1.2.3") == 2  # v1.2.3 exists
    (git_project / "other.txt").write_text("x")
    git(git_project, "add", "other.txt")
    assert run(git_project, "tag", "create", "patch") == 0
    assert "other.txt" not in git(git_project, "show", "--name-only", "--format=", "HEAD")


def test_tag_set_and_check(git_project: Path) -> None:
    assert run(git_project, "tag", "create", "5.6.7") == 0
    assert run(git_project, "patch", "0") == 0
    assert run(git_project, "check") == 1
    assert run(git_project, "tag", "set") == 0
    assert run(git_project, "check") == 0
    assert run(git_project, "tag", "create", "9.9.9", "--dry-run") == 0
    assert git(git_project, "tag", "-l", "v9.9.9") == ""


def test_custom_tag_format(git_project: Path) -> None:
    assert run(git_project, "tag", "create", "--tag-format", "rel-%s") == 0
    assert git(git_project, "tag", "-l") == "rel-1.2.3"
    assert run(git_project, "check", "--tag-format", "rel-%s") == 0


# --------------------------------------------------------------------------- CLI


def test_cli_subprocess(header_project: Path) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "polybind", "version", "--root", str(header_project)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == "1.2.3"


def test_cli_works_in_own_repo() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "polybind", "version"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == read_toml(REPO_ROOT / "pyproject.toml")["project"]["version"]  # whatever the release is
