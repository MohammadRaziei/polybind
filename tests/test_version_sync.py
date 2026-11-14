"""tests/test_version_sync.py: ``[[version.sync]]``, the files that repeat the version (a file and a regex)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polybind.cli import main as cli_main
from polybind.config import ConfigError, load_config

HEADER = """\
#ifndef CTOON_H
#define CTOON_VERSION_MAJOR 0
#define CTOON_VERSION_MINOR 9
#define CTOON_VERSION_PATCH 0
#endif
"""
CARGO = """\
[package]
name = "ctoon"
version = "0.9.0"
rust-version = "1.70"

[dependencies]
serde_json = { version = "1", features = ["preserve_order"] }
"""
ZON = """\
.{
    .name = .ctoon,
    .version = "0.9.0",
    .minimum_zig_version = "0.16.0",
}
"""
README = """\
# ctoon

```cmake
FetchContent_Declare(ctoon
    GIT_REPOSITORY https://github.com/mohammadraziei/ctoon
    GIT_TAG v0.9.0
)
```

```toml
ctoon = "0.9.0"
```
"""
SETTINGS = r"""
[[tool.polybind.version.sync]]
file = "Cargo.toml"
regex = '(?m)^version = "(?P<version>[^"]*)"'

[[tool.polybind.version.sync]]
file = "build.zig.zon"
regex = '\.version = "(?P<version>[^"]*)",'

[[tool.polybind.version.sync]]
file = "README.md"
regex = '(?m)^[ \t]*GIT_TAG[ \t]+v(?P<version>\d+\.\d+\.\d+)[ \t]*$'

[[tool.polybind.version.sync]]
file = "README.md"
regex = '(?m)^ctoon = "(?P<version>[^"]*)"[ \t]*$'
"""
PYPROJECT = '[project]\nname = "ctoon"\ndynamic = ["version"]\n' + SETTINGS


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "ctoon.h").write_text(HEADER)
    (tmp_path / "Cargo.toml").write_text(CARGO)
    (tmp_path / "build.zig.zon").write_text(ZON)
    (tmp_path / "README.md").write_text(README)
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    return tmp_path


def run(root: Path, *args: str) -> int:
    return cli_main(["version", "--root", str(root), *args])


def text(root: Path, name: str) -> str:
    return (root / name).read_bytes().decode()


def test_sync_writes_only_the_version_text(project: Path) -> None:
    (project / "include" / "ctoon.h").write_text(HEADER.replace("MINOR 9", "MINOR 10"))
    assert run(project, "sync") == 0
    assert text(project, "Cargo.toml") == CARGO.replace('version = "0.9.0"', 'version = "0.10.0"', 1)
    assert text(project, "build.zig.zon") == ZON.replace("0.9.0", "0.10.0", 1)
    assert text(project, "README.md") == README.replace("0.9.0", "0.10.0")  # two regexes, one file


def test_bump_writes_source_and_every_file(project: Path) -> None:
    assert run(project, "minor", "+") == 0
    assert "MINOR 10" in text(project, "include/ctoon.h")
    assert 'version = "0.10.0"' in text(project, "Cargo.toml")
    assert 'serde_json = { version = "1"' in text(project, "Cargo.toml")  # the other version = is untouched
    assert 'ctoon = "0.10.0"' in text(project, "README.md")


def test_dry_run_writes_nothing_and_lists_files(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "minor", "+", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Would update Cargo.toml" in out and "Would update README.md" in out
    assert text(project, "Cargo.toml") == CARGO and text(project, "include/ctoon.h") == HEADER


def test_line_endings_survive(project: Path) -> None:
    (project / "Cargo.toml").write_bytes(CARGO.replace("\n", "\r\n").encode())
    assert run(project, "patch", "+") == 0
    assert text(project, "Cargo.toml") == CARGO.replace("\n", "\r\n").replace("0.9.0", "0.9.1", 1)


def test_a_regex_that_does_not_match_changes_no_file(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "README.md").write_text(README.replace("GIT_TAG v0.9.0", "GIT_TAG main"))
    assert run(project, "minor", "+") == 2
    assert "README.md" in capsys.readouterr().err
    assert text(project, "include/ctoon.h") == HEADER  # the source too: planned first
    assert text(project, "Cargo.toml") == CARGO


def test_missing_file_is_an_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "build.zig.zon").unlink()
    assert run(project, "sync") == 2
    assert "build.zig.zon" in capsys.readouterr().err


def test_expect_counts_matches(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        PYPROJECT + '\n[[tool.polybind.version.sync]]\nfile = "README.md"\n'
        "regex = '(?P<version>0\\.9\\.0)'\nexpect = 3\n"
    )
    assert run(project, "sync") == 2  # README.md holds 0.9.0 twice, 3 are expected
    (project / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndynamic = ["version"]\n[[tool.polybind.version.sync]]\n'
        'file = "README.md"\nregex = \'(?P<version>0\\.9\\.0)\'\nexpect = 2\n'
    )
    assert run(project, "minor", "+") == 0
    assert text(project, "README.md") == README.replace("0.9.0", "0.10.0")


def test_check_reports_each_file_that_differs(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "Cargo.toml").write_text(CARGO.replace("0.9.0", "0.8.0", 1))
    assert run(project, "check") == 1
    err = capsys.readouterr().err
    assert "Cargo.toml: 0.8.0 (expected 0.9.0)" in err and "build.zig.zon" not in err


def test_sync_without_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "ctoon.h").write_text(HEADER)
    assert run(tmp_path, "sync") == 0
    assert "no [[version.sync]]" in capsys.readouterr().out


def test_settings_in_polybind_toml(project: Path) -> None:
    (project / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (project / "polybind.toml").write_text(
        '[[version.sync]]\nfile = "Cargo.toml"\nregex = \'(?m)^version = "(?P<version>[^"]*)"\'\n'
    )
    assert run(project, "patch", "+") == 0
    assert 'version = "0.9.1"' in text(project, "Cargo.toml")
    assert text(project, "build.zig.zon") == ZON  # not listed in this project


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('file = "a"', "'regex' is required"),
        ('regex = "(?P<version>x)"', "'file' is required"),
        ('file = "a"\nregex = "(x)"', "named group"),
        ('file = "a"\nregex = "(?P<version>"', "does not compile"),
        ('file = "a"\nregex = "(?P<version>x)"\nexpect = 0', "'expect' must be"),
        ('file = "a"\nregex = "(?P<version>x)"\nkind = "cargo"', "unknown key 'version.sync.kind'"),
        ('file = "a"\nregex = "(?P<version>x)"\nexpct = 1', "did you mean 'version.sync.expect'"),
    ],
)
def test_bad_tables(tmp_path: Path, table: str, message: str) -> None:
    (tmp_path / "polybind.toml").write_text(f"[[version.sync]]\n{table}\n")
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_sync_must_be_an_array_of_tables(tmp_path: Path) -> None:
    (tmp_path / "polybind.toml").write_text('[version]\nsync = "Cargo.toml"\n')
    with pytest.raises(ConfigError, match="array of tables"):
        load_config(tmp_path)


# --------------------------------------------------------------------------- git


@pytest.fixture
def git_project(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }.items():
        monkeypatch.setenv(key, value)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=project, check=True, capture_output=True)
    return project


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def test_tag_create_commits_the_sync_files_and_nothing_else(git_project: Path) -> None:
    (git_project / "other.txt").write_text("x")
    git(git_project, "add", "other.txt")
    assert run(git_project, "tag", "create", "minor") == 0
    changed = git(git_project, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(changed) == ["Cargo.toml", "README.md", "build.zig.zon", "include/ctoon.h"]
    assert git(git_project, "describe", "--tags", "--abbrev=0") == "v0.10.0"
    assert run(git_project, "check") == 0


def test_tag_set_writes_the_sync_files(git_project: Path) -> None:
    git(git_project, "tag", "v0.20.1")
    assert run(git_project, "tag", "set") == 0
    assert 'version = "0.20.1"' in text(git_project, "Cargo.toml")
    assert run(git_project, "check") == 0


def test_check_fails_when_a_file_drifted_even_if_the_tag_matches(git_project: Path) -> None:
    assert run(git_project, "tag", "create") == 0
    (git_project / "Cargo.toml").write_text(CARGO.replace("0.9.0", "0.8.0", 1))
    assert run(git_project, "check") == 1
