"""
polybind.lock
~~~~~~~~~~~~~
``polybind.lock``: the tag and the provider that delivered each dependency.

A separate file at the project root, written only by ``polybind deps record``::

    schema = 1

    [deps.liburlparser]
    tag      = "v2.2.0"
    provider = "fetch"
    commit   = "1cc8355a90720d282e5b66fdb06063396d9194f4"   # optional, fetch only

See spec.md, section 9.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from polybind.config import SCHEMA_VERSION, ConfigError, read_toml, unknown_key

LOCK_FILE = "polybind.lock"
LOCK_PROVIDERS = ("fetch", "uv", "pip")
DEFAULT_TAG_FORMAT = "v%s"

_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
_VERSION = re.compile(r"^[0-9]+(\.[0-9A-Za-z]+)*$")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ENTRY_KEYS = {"tag", "provider", "commit"}

HEADER = "# polybind.lock: written by `polybind deps record`. Do not edit by hand.\n"


class LockError(ConfigError):
    """An invalid lock file."""


@dataclass
class LockEntry:
    tag: str
    provider: str
    commit: str | None = None


@dataclass
class Lock:
    entries: dict[str, LockEntry] = field(default_factory=dict)


def _unknown(kind: str, name: str, allowed: set[str], origin: str) -> LockError:
    return LockError(str(unknown_key(kind, name, allowed, origin)))


def tag_to_version(tag: str, tag_format: str = DEFAULT_TAG_FORMAT) -> str | None:
    """``v2.2.0`` -> ``2.2.0``. ``None`` when the tag does not follow the format (a commit id)."""
    pre, _, post = tag_format.partition("%s")
    if tag.startswith(pre) and tag.endswith(post) and len(tag) > len(pre) + len(post):
        body = tag[len(pre) : len(tag) - len(post)]
        if _VERSION.match(body):
            return body
    return None


def validate_entry(name: str, data: object, origin: str) -> LockEntry:
    where = f"deps.{name}"
    if not isinstance(data, dict):
        raise LockError(f"{origin}: [{where}] must be a table")
    for key in data:
        if key not in _ENTRY_KEYS:
            raise _unknown("key", f"{where}.{key}", {f"{where}.{k}" for k in _ENTRY_KEYS}, origin)
    for key in ("tag", "provider"):
        if key not in data:
            raise LockError(f"{origin}: {where}.{key} is required")
    for key, value in data.items():
        if not isinstance(value, str) or not value:
            raise LockError(f"{origin}: {where}.{key} must be a non-empty string")
    provider = data["provider"]
    if provider not in LOCK_PROVIDERS:
        raise LockError(
            f"{origin}: {where}.provider is '{provider}', expected one of {', '.join(LOCK_PROVIDERS)}"
        )
    commit = data.get("commit")
    if commit is not None:
        if provider != "fetch":
            raise LockError(f"{origin}: {where}.commit is only recorded for provider 'fetch'")
        if not _COMMIT.match(commit):
            raise LockError(f"{origin}: {where}.commit is not a commit id")
    return LockEntry(data["tag"], provider, commit)


def read_lock(path: Path) -> Lock:
    data = read_toml(path)
    origin = str(path)
    for key in data:
        if key not in ("schema", "deps"):
            raise _unknown("section", key, {"schema", "deps"}, origin)
    schema = data.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA_VERSION:
        raise LockError(f"{origin}: schema must be {SCHEMA_VERSION}")
    deps = data.get("deps", {})
    if not isinstance(deps, dict):
        raise LockError(f"{origin}: [deps] must be a table")
    return Lock({name: validate_entry(name, body, origin) for name, body in deps.items()})


def _key(name: str) -> str:
    return name if _BARE_KEY.match(name) else json.dumps(name)


def render_lock(lock: Lock) -> str:
    lines = [HEADER.rstrip("\n"), "", f"schema = {SCHEMA_VERSION}"]
    for name in sorted(lock.entries):
        entry = lock.entries[name]
        validate_entry(
            name,
            {k: v for k, v in (("tag", entry.tag), ("provider", entry.provider), ("commit", entry.commit)) if v},
            LOCK_FILE,
        )
        lines += ["", f"[deps.{_key(name)}]", f"tag      = {json.dumps(entry.tag)}"]
        lines.append(f"provider = {json.dumps(entry.provider)}")
        if entry.commit:
            lines.append(f"commit   = {json.dumps(entry.commit)}")
    return "\n".join(lines) + "\n"


def write_lock(path: Path, lock: Lock) -> None:
    path.write_text(render_lock(lock), encoding="utf-8")
