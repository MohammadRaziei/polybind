"""
polybind.deps
~~~~~~~~~~~~~
Native dependencies of a hybrid C++/Python project: ``[deps.<name>]`` and
``[optional-deps.<group>.<name>]`` of the settings, checked against ``polybind.lock``.

Usage::

    polybind deps validate                    check settings, dependencies and the lock
    polybind deps list [--json]               every dependency with all defaults filled in
    polybind deps requirements --kind build   PEP 508 requirements, one per line
                               --kind runtime
                               --kind extra:<group>

The rules are in spec.md, sections 5 to 7 and 9.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from polybind.config import Config, ConfigError, load_config, unknown_key
from polybind.lock import (
    DEFAULT_TAG_FORMAT,
    LOCK_FILE,
    Lock,
    read_lock,
    tag_to_version,
)

PROVIDERS = ("python", "uv", "pip", "fetch", "system")

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SPEC_ITEM = re.compile(r"^(~=|==|!=|<=|>=|<|>)\s*[0-9][0-9A-Za-z.*+!_-]*$")

DEP_KEYS = {
    "description", "version-spec", "targets", "find-package", "optional", "prefer-provider",
    "runtime", "buildtime", "bundle", "python", "fetch",
}
PYTHON_KEYS = {"package", "module", "module-cli", "cmake-flag", "version-flag", "cmake-function"}
FETCH_KEYS = {"url", "tag-format", "build-options"}


@dataclass
class PythonProvider:
    package: str
    module: str
    module_cli: bool
    cmake_flag: list[str]
    version_flag: str  # "" = read the version from the package metadata
    cmake_function: str  # "" unless module_cli is false


@dataclass
class FetchProvider:
    url: str
    tag_format: str
    build_options: dict[str, str]


@dataclass
class Dependency:
    name: str
    group: str | None
    description: str
    version_spec: str
    targets: list[str]
    find_package: str
    optional: bool
    prefer_provider: str
    runtime: bool
    buildtime: bool
    bundle: bool
    python: PythonProvider | None
    fetch: FetchProvider | None

    @property
    def providers(self) -> list[str]:
        """The providers that are on (spec 5.2)."""
        return (["python"] if self.python else []) + (["fetch"] if self.fetch else [])

    @property
    def tag_format(self) -> str:
        return self.fetch.tag_format if self.fetch else DEFAULT_TAG_FORMAT


@dataclass
class Dependencies:
    deps: dict[str, Dependency] = field(default_factory=dict)
    groups: dict[str, dict[str, Dependency]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class _Table:
    """A TOML table whose keys are checked and whose values are read with a type."""

    def __init__(self, data: dict[str, Any], where: str, origin: str, allowed: set[str]) -> None:
        self.data, self.where, self.origin = data, where, origin
        for key in data:
            if key not in allowed:
                raise unknown_key("key", f"{where}.{key}", {f"{where}.{k}" for k in allowed}, origin)

    def has(self, key: str) -> bool:
        return key in self.data

    def error(self, key: str, message: str) -> ConfigError:
        return ConfigError(f"{self.origin}: {self.where}.{key} {message}")

    def string(self, key: str, default: str, *, empty: bool = True) -> str:
        value = self.data.get(key, default)
        if not isinstance(value, str):
            raise self.error(key, "must be a string")
        if not empty and not value:
            raise self.error(key, "must not be empty")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self.data.get(key, default)
        if not isinstance(value, bool):
            raise self.error(key, "must be true or false")
        return value

    def strings(self, key: str, default: list[str], *, single: bool = False) -> list[str]:
        value = self.data.get(key, default)
        if single and isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
            raise self.error(key, "must be a list of non-empty strings")
        return list(value)


def _check_spec(spec: str, table: _Table) -> None:
    if not spec.strip():
        return
    for item in spec.split(","):
        if not _SPEC_ITEM.match(item.strip()):
            raise table.error("version-spec", f"has an invalid item '{item.strip()}' (e.g. '>=2.2.0,<3')")


def _parse_python(name: str, data: object, where: str, origin: str) -> PythonProvider:
    if not isinstance(data, dict):
        raise ConfigError(f"{origin}: {where}.python must be a table (leave it out to turn the provider off)")
    t = _Table(data, f"{where}.python", origin, PYTHON_KEYS)
    package = t.string("package", name, empty=False)
    module = t.string("module", package.replace("-", "_"), empty=False)
    if t.boolean("module-cli", True):
        if t.has("cmake-function"):
            raise t.error("cmake-function", "needs module-cli = false")
        return PythonProvider(
            package, module, True,
            t.strings("cmake-flag", ["--cmake-dir"], single=True),
            t.string("version-flag", "--version"), "",
        )
    for key in ("cmake-flag", "version-flag"):
        if t.has(key):
            raise t.error(key, "needs module-cli = true (without a CLI there is nothing to pass it to)")
    return PythonProvider(package, module, False, [], "", t.string("cmake-function", "get_cmake_dir", empty=False))


def _parse_fetch(data: object, where: str, origin: str) -> FetchProvider:
    if not isinstance(data, dict):
        raise ConfigError(f"{origin}: {where}.fetch must be a table (leave it out to turn the provider off)")
    t = _Table(data, f"{where}.fetch", origin, FETCH_KEYS)
    if not t.has("url"):
        raise t.error("url", "is required: it is what turns the fetch provider on")
    url = t.string("url", "", empty=False)
    tag_format = t.string("tag-format", DEFAULT_TAG_FORMAT, empty=False)
    if "%s" not in tag_format:
        raise t.error("tag-format", "must contain %s")
    options = t.data.get("build-options", {})
    if not isinstance(options, dict) or not all(isinstance(v, str) for v in options.values()):
        raise t.error("build-options", "must be a table of strings")
    return FetchProvider(url, tag_format, dict(options))


def parse_dependency(
    name: str, data: object, origin: str, warnings: list[str], group: str | None = None
) -> Dependency:
    where = f"deps.{name}" if group is None else f"optional-deps.{group}.{name}"
    if not _NAME.match(name):
        raise ConfigError(f"{origin}: invalid dependency name '{name}'")
    if not isinstance(data, dict):
        raise ConfigError(f"{origin}: [{where}] must be a table")
    t = _Table(data, where, origin, DEP_KEYS)

    spec = t.string("version-spec", "")
    _check_spec(spec, t)
    prefer = t.string("prefer-provider", "")
    if prefer and prefer not in PROVIDERS:
        raise t.error("prefer-provider", f"is '{prefer}', expected one of {', '.join(PROVIDERS)} or empty")
    runtime = t.boolean("runtime", False)
    buildtime = t.boolean("buildtime", True)
    if not runtime and not buildtime:
        raise ConfigError(f"{origin}: {where}: runtime and buildtime are both false, it would go nowhere")
    bundle = t.boolean("bundle", False)

    python = _parse_python(name, data["python"], where, origin) if "python" in data else None
    fetch = _parse_fetch(data["fetch"], where, origin) if "fetch" in data else None

    if runtime and python is None:
        raise t.error("runtime", "needs the python provider ([...python] table): only a pip package can be a requirement")
    if bundle and runtime:
        warnings.append(f"{origin}: {where}: bundle and runtime are both true; the library is inside the wheel, "
                        "keep runtime only if the Python import is really needed")
    if prefer in ("python", "uv", "pip") and python is None:
        warnings.append(f"{origin}: {where}: prefer-provider = {prefer}, but [{where}.python] is not set")
    if prefer == "fetch" and fetch is None:
        warnings.append(f"{origin}: {where}: prefer-provider = fetch, but {where}.fetch.url is not set")

    return Dependency(
        name=name, group=group,
        description=t.string("description", ""),
        version_spec=spec.replace(" ", ""),
        targets=t.strings("targets", [f"{name}::{name}"]),
        find_package=t.string("find-package", name, empty=False),
        optional=t.boolean("optional", False),
        prefer_provider=prefer, runtime=runtime, buildtime=buildtime, bundle=bundle,
        python=python, fetch=fetch,
    )


def load_dependencies(config: Config) -> Dependencies:
    origin = config.origin
    out = Dependencies()
    deps = config.data.get("deps", {})
    for name, body in deps.items():
        out.deps[name] = parse_dependency(name, body, origin, out.warnings)
    for group, members in config.data.get("optional-deps", {}).items():
        if not _NAME.match(group):
            raise ConfigError(f"{origin}: invalid group name '{group}'")
        if not isinstance(members, dict):
            raise ConfigError(f"{origin}: [optional-deps.{group}] must be a table of dependencies")
        out.groups[group] = {
            name: parse_dependency(name, body, origin, out.warnings, group) for name, body in members.items()
        }
    return out


# --------------------------------------------------------------------------- requirements


def requirement(dep: Dependency) -> str | None:
    """``liburlparser>=2.2.0,<3``; ``None`` for a dependency that is not a pip package."""
    return f"{dep.python.package}{dep.version_spec}" if dep.python else None


def requirements(deps: Dependencies, kind: str, groups: tuple[str, ...] = ()) -> list[str]:
    """PEP 508 requirements: ``build``, ``runtime`` or ``extra:<group>`` (spec 7.1).

    ``groups`` are the groups selected at build time; their buildtime entries join ``build`` (spec 6, option a).
    """
    out: list[str] = []

    def add(items: Any, flag: str) -> None:
        for dep in items:
            req = requirement(dep)
            if req and getattr(dep, flag) and req not in out:
                out.append(req)

    def group(name: str) -> dict[str, Dependency]:
        if name not in deps.groups:
            raise ConfigError(f"unknown group '{name}' (known: {', '.join(deps.groups) or 'none'})")
        return deps.groups[name]

    if kind == "build":
        add(deps.deps.values(), "buildtime")
        for name in groups:
            add(group(name).values(), "buildtime")
    elif kind == "runtime":
        add(deps.deps.values(), "runtime")
    elif kind.startswith("extra:"):
        add(group(kind[len("extra:") :]).values(), "runtime")
    else:
        raise ConfigError(f"unknown kind '{kind}' (build, runtime or extra:<group>)")
    return out


# --------------------------------------------------------------------------- lock


def check_lock(deps: Dependencies, lock: Lock) -> tuple[list[str], list[str]]:
    """``(warnings, errors)`` of the lock against the declared dependencies (spec 9)."""
    warnings: list[str] = []
    errors: list[str] = []
    for name, entry in lock.entries.items():
        dep = deps.deps.get(name)
        if dep is None:
            warnings.append(f"{LOCK_FILE}: [deps.{name}] is not declared in the settings (stale entry)")
        elif entry.provider == "fetch":
            if dep.fetch is None:
                warnings.append(f"{LOCK_FILE}: [deps.{name}] records provider fetch, but {name} has no fetch.url")
        else:
            if dep.python is None:
                warnings.append(f"{LOCK_FILE}: [deps.{name}] records provider {entry.provider}, but {name} has no python provider")
            if tag_to_version(entry.tag, dep.tag_format) is None:
                errors.append(
                    f"{LOCK_FILE}: [deps.{name}] tag '{entry.tag}' cannot name a pip version "
                    f"(format {dep.tag_format}); a commit id works with fetch only"
                )
    return warnings, errors


# --------------------------------------------------------------------------- command line


def _describe(dep: Dependency) -> str:
    flags = [f for f, on in (("build", dep.buildtime), ("runtime", dep.runtime), ("bundle", dep.bundle),
                              ("optional", dep.optional)) if on]
    name = dep.name if dep.group is None else f"{dep.group}/{dep.name}"
    spec = dep.version_spec or "any"
    return f"{name:<24} {spec:<16} providers: {', '.join(dep.providers) or 'none':<14} {' '.join(flags)}"


def _load(root: Path) -> tuple[Config, Dependencies]:
    config = load_config(root)
    return config, load_dependencies(config)


def cmd_validate(args: argparse.Namespace) -> int:
    config, deps = _load(args.root)
    warnings = list(deps.warnings)
    errors: list[str] = []
    lock_path = args.root / LOCK_FILE
    if lock_path.is_file():
        lock_warnings, errors = check_lock(deps, read_lock(lock_path))
        warnings += lock_warnings
    for message in warnings:
        print(f"warning: {message}", file=sys.stderr)
    for message in errors:
        print(f"error: {message}", file=sys.stderr)
    if errors:
        return 2
    count = len(deps.deps) + sum(len(g) for g in deps.groups.values())
    print(f"ok: {count} dependenc{'y' if count == 1 else 'ies'}, {len(warnings)} warning(s)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    _, deps = _load(args.root)
    everything = list(deps.deps.values()) + [d for g in deps.groups.values() for d in g.values()]
    if args.json:
        print(json.dumps([asdict(d) for d in everything], indent=2))
    else:
        for dep in everything:
            print(_describe(dep))
    return 0


def cmd_requirements(args: argparse.Namespace) -> int:
    _, deps = _load(args.root)
    for line in requirements(deps, args.kind, tuple(args.group or ())):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polybind deps",
        description="Native dependencies: validate, list, requirements.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__.split("Usage::")[1]),
    )
    subs = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        p = subs.add_parser(name, help=help_)
        p.add_argument("--root", type=Path, default=Path.cwd(), help="project directory (default: cwd)")
        return p

    add("validate", "check settings, dependencies and the lock").set_defaults(func=cmd_validate)
    p = add("list", "the dependencies with every default filled in")
    p.add_argument("--json", action="store_true", help="machine readable")
    p.set_defaults(func=cmd_list)
    p = add("requirements", "PEP 508 requirements, one per line")
    p.add_argument("--kind", required=True, help="build | runtime | extra:<group>")
    p.add_argument("--group", action="append", help="a group selected at build time (kind build); repeatable")
    p.set_defaults(func=cmd_requirements)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.root = args.root.resolve()
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"polybind: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
