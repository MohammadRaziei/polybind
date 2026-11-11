"""tests/test_lock.py: ``polybind.lock`` (spec section 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from polybind.lock import (
    Lock,
    LockEntry,
    LockError,
    read_lock,
    render_lock,
    tag_to_version,
    write_lock,
)

COMMIT = "1cc8355a90720d282e5b66fdb06063396d9194f4"


def lock_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "polybind.lock"
    path.write_text(text)
    return path


def test_roundtrip(tmp_path: Path) -> None:
    lock = Lock(
        {
            "liburlparser": LockEntry("v2.2.0", "fetch", COMMIT),
            "mbedtls": LockEntry("v3.6.1", "uv"),
            "a.b": LockEntry("v1.0.0", "pip"),
        }
    )
    path = tmp_path / "polybind.lock"
    write_lock(path, lock)
    assert read_lock(path) == lock


def test_render_is_sorted_and_stable() -> None:
    lock = Lock({"zeta": LockEntry("v1.0.0", "pip"), "alpha": LockEntry("v2.0.0", "fetch", COMMIT)})
    text = render_lock(lock)
    assert text.index("[deps.alpha]") < text.index("[deps.zeta]")
    assert text == render_lock(lock)
    assert text.startswith("# polybind.lock")
    assert f'commit   = "{COMMIT}"' in text


def test_tag_only_lock_is_valid(tmp_path: Path) -> None:
    path = lock_file(tmp_path, 'schema = 1\n[deps.x]\ntag = "v1.2.3"\nprovider = "fetch"\n')
    assert read_lock(path).entries["x"] == LockEntry("v1.2.3", "fetch", None)


def test_empty_lock(tmp_path: Path) -> None:
    assert read_lock(lock_file(tmp_path, "schema = 1\n")).entries == {}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[deps.x]\ntag = "v1"\nprovider = "fetch"\n', "schema must be 1"),
        ("schema = 2\n", "schema must be 1"),
        ("schema = 1\n[dep.x]\n", "unknown section 'dep'"),
        ("schema = 1\ndeps = 3\n", r"\[deps\] must be a table"),
        ("schema = 1\n[deps]\nx = 3\n", r"\[deps.x\] must be a table"),
        ('schema = 1\n[deps.x]\nprovider = "fetch"\n', "deps.x.tag is required"),
        ('schema = 1\n[deps.x]\ntag = "v1"\n', "deps.x.provider is required"),
        ('schema = 1\n[deps.x]\ntag = "v1"\nprovider = "system"\n', "expected one of fetch, uv, pip"),
        ('schema = 1\n[deps.x]\ntag = ""\nprovider = "fetch"\n', "non-empty string"),
        ('schema = 1\n[deps.x]\ntag = "v1"\nprovider = "fetch"\nversion = "1"\n', "unknown key 'deps.x.version'"),
        ('schema = 1\n[deps.x]\ntag = "v1"\nprovider = "uv"\ncommit = "' + COMMIT + '"\n', "only recorded for provider 'fetch'"),
        ('schema = 1\n[deps.x]\ntag = "v1"\nprovider = "fetch"\ncommit = "nothex"\n', "not a commit id"),
    ],
)
def test_invalid(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(LockError, match=message):
        read_lock(lock_file(tmp_path, text))


def test_write_refuses_an_invalid_entry(tmp_path: Path) -> None:
    with pytest.raises(LockError):
        write_lock(tmp_path / "polybind.lock", Lock({"x": LockEntry("v1", "uv", COMMIT)}))


def test_tag_to_version() -> None:
    assert tag_to_version("v2.2.0") == "2.2.0"
    assert tag_to_version("rel-1.0.0-x", "rel-%s-x") == "1.0.0"
    assert tag_to_version("2.2.0", "%s") == "2.2.0"
    assert tag_to_version("1cc8355a90720d282e5b66fdb06063396d9194f4") is None  # a commit id
    assert tag_to_version("v") is None
    assert tag_to_version("vfoo") is None
