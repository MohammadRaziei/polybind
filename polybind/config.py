"""
polybind.config
~~~~~~~~~~~~~~~
Project settings for polybind, read from TOML.

Two places, same keys, merged in this order (later wins)::

    pyproject.toml   [tool.polybind] ...
    .polybind        top level, no "tool.polybind" prefix

The command line wins over both. Example ``.polybind``::

    [version]
    file = "include/httpp.h"
    prefix = "HTTPP_"
    tag_format = "v%s"

Sections: ``version`` (checked key by key), ``gen`` and ``deps`` (reserved, passed through).

TOML is read with ``tomllib`` (Python 3.11+) or its backport ``tomli`` (``pip install tomli``,
installed automatically on older Pythons). A project that has no polybind settings never
needs either.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE = ".polybind"
PYPROJECT_FILE = "pyproject.toml"

# section -> allowed keys (None: not checked yet)
SCHEMA: dict[str, set[str] | None] = {
    "version": {"file", "prefix", "tag_format"},
    "gen": None,
    "deps": None,
}


class ConfigError(Exception):
    """Unreadable or invalid settings."""


def _toml() -> Any:
    try:
        import tomllib

        return tomllib
    except ImportError:
        pass
    try:
        import tomli

        return tomli
    except ImportError:
        raise ConfigError(
            "reading TOML needs Python 3.11+ or the 'tomli' package (pip install tomli)"
        ) from None


def read_toml(path: Path) -> dict[str, Any]:
    toml = _toml()
    try:
        with path.open("rb") as handle:
            return dict(toml.load(handle))
    except toml.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _unknown(kind: str, name: str, allowed: set[str], origin: str) -> ConfigError:
    hint = difflib.get_close_matches(name, sorted(allowed), n=1)
    more = f" (did you mean '{hint[0]}'?)" if hint else f" (known: {', '.join(sorted(allowed))})"
    return ConfigError(f"{origin}: unknown {kind} '{name}'{more}")


def validate(data: dict[str, Any], origin: str) -> None:
    for section, body in data.items():
        if section not in SCHEMA:
            raise _unknown("section", section, set(SCHEMA), origin)
        if not isinstance(body, dict):
            raise ConfigError(f"{origin}: [{section}] must be a table")
        allowed = SCHEMA[section]
        if allowed is None:
            continue
        for key, value in body.items():
            if key not in allowed:
                raise _unknown("key", f"{section}.{key}", {f"{section}.{k}" for k in allowed}, origin)
            if not isinstance(value, str):
                raise ConfigError(f"{origin}: {section}.{key} must be a string")


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)


def load_config(root: Path) -> Config:
    """Merged settings of ``root``; empty when the project has none."""
    config = Config()

    pyproject = root / PYPROJECT_FILE
    # Do not need a TOML reader for projects that never mention polybind.
    if pyproject.is_file() and "tool.polybind" in pyproject.read_text(encoding="utf-8"):
        table = read_toml(pyproject).get("tool", {}).get("polybind")
        if table is not None:
            if not isinstance(table, dict):
                raise ConfigError(f"{pyproject}: [tool.polybind] must be a table")
            validate(table, f"{pyproject} [tool.polybind]")
            config.data = deep_merge(config.data, table)
            config.sources.append(pyproject)

    dotfile = root / CONFIG_FILE
    if dotfile.is_file():
        table = read_toml(dotfile)
        validate(table, str(dotfile))
        config.data = deep_merge(config.data, table)
        config.sources.append(dotfile)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="polybind config", description="Show the effective polybind settings."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project directory")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.root.resolve())
    except ConfigError as exc:
        print(f"polybind: error: {exc}", file=sys.stderr)
        return 2
    if not config.sources:
        print("no polybind settings found")
        return 0
    for source in config.sources:
        print(f"# {source}")
    print(json.dumps(config.data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
