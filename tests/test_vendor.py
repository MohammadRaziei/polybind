"""tests/test_vendor.py: ``polybind vendor`` copies the C++ dispatch helpers into a project and ``--check`` finds a stale copy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from polybind import vendor

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADERS = ("dispatch.hpp", "nanobind.hpp")


def _polybind(*args: str | Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run([sys.executable, "-m", "polybind", *map(str, args)], capture_output=True, text=True, env=env)


def test_the_package_carries_the_headers_and_the_cmake_config() -> None:
    for name in HEADERS:
        assert (REPO_ROOT / "polybind" / "include" / "polybind" / name).is_file()
    assert (REPO_ROOT / "polybind" / "cmake" / "polybind-config.cmake").is_file()


def test_vendor_copies_the_files_and_writes_a_stamp(tmp_path: Path) -> None:
    out = _polybind("vendor", tmp_path)
    assert out.returncode == 0, out.stderr
    copy = tmp_path / "polybind"
    for name in (*HEADERS, "polybind-config.cmake"):
        assert (copy / name).read_bytes() == dict((n, s) for s, n in vendor.FILES)[name].read_bytes()
    stamp = (copy / "VENDORED").read_text(encoding="utf-8")
    assert stamp.startswith("polybind ")
    for name in (*HEADERS, "polybind-config.cmake"):
        assert f"  {name}\n" in stamp


def test_vendor_creates_the_directory_and_can_be_run_again(tmp_path: Path) -> None:
    target = tmp_path / "third_party" / "deep"
    assert _polybind("vendor", target).returncode == 0
    (target / "polybind" / "dispatch.hpp").write_text("edited", encoding="utf-8")
    assert _polybind("vendor", target).returncode == 0  # overwrites the edit
    assert (target / "polybind" / "dispatch.hpp").read_text(encoding="utf-8") != "edited"
    assert _polybind("vendor", "--check", target).returncode == 0


def test_check_accepts_a_fresh_copy(tmp_path: Path) -> None:
    vendor.vendor(tmp_path)
    assert vendor.check(tmp_path) == []
    out = _polybind("vendor", "--check", tmp_path)
    assert out.returncode == 0 and "up to date" in out.stdout


def test_check_finds_a_file_that_was_changed(tmp_path: Path) -> None:
    vendor.vendor(tmp_path)
    path = tmp_path / "polybind" / "nanobind.hpp"
    path.write_text(path.read_text(encoding="utf-8") + "// local edit\n", encoding="utf-8")
    assert vendor.check(tmp_path) == ["nanobind.hpp: changed since it was copied (the stamp has another hash)"]
    out = _polybind("vendor", "--check", tmp_path)
    assert out.returncode == 1 and "nanobind.hpp: changed since it was copied" in out.stdout


def test_check_finds_a_missing_file(tmp_path: Path) -> None:
    vendor.vendor(tmp_path)
    (tmp_path / "polybind" / "polybind-config.cmake").unlink()
    assert vendor.check(tmp_path) == ["polybind-config.cmake: missing"]


def test_check_finds_a_copy_of_another_polybind(tmp_path: Path) -> None:
    # a copy that is whole and untouched, but of a polybind whose header was different: its stamp matches its files
    vendor.vendor(tmp_path)
    copy = tmp_path / "polybind"
    old = b"// an older dispatch.hpp\n"
    (copy / "dispatch.hpp").write_bytes(old)
    hashes = {name: vendor._sha256(copy / name) for _s, name in vendor.FILES}
    (copy / "VENDORED").write_text(vendor._stamp_text("0.4.0", hashes), encoding="utf-8")
    problems = vendor.check(tmp_path)
    assert len(problems) == 1
    assert problems[0].startswith("dispatch.hpp: from polybind 0.4.0, the installed one differs")


def test_check_of_a_directory_that_is_not_a_copy_is_an_error(tmp_path: Path) -> None:
    out = _polybind("vendor", "--check", tmp_path)
    assert out.returncode == 2
    assert "no VENDORED stamp" in out.stderr


def test_a_damaged_stamp_is_an_error(tmp_path: Path) -> None:
    vendor.vendor(tmp_path)
    (tmp_path / "polybind" / "VENDORED").write_text("not a stamp", encoding="utf-8")
    out = _polybind("vendor", "--check", tmp_path)
    assert out.returncode == 2 and "not a polybind VENDORED stamp" in out.stderr


def test_the_command_is_listed_and_documented() -> None:
    assert "vendor" in _polybind("-h").stdout
    out = _polybind("vendor", "-h")
    assert out.returncode == 0 and "--check" in out.stdout and "polybind::polybind" in out.stdout


@pytest.mark.parametrize("missing", ["dispatch.hpp", "nanobind.hpp"])
def test_vendor_names_a_header_that_is_missing_from_the_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    files = tuple((tmp_path / "nowhere" / n if n == missing else s, n) for s, n in vendor.FILES)
    monkeypatch.setattr(vendor, "FILES", files)
    with pytest.raises(vendor.PolybindError, match="missing from the installed polybind"):
        vendor.vendor(tmp_path / "out")
