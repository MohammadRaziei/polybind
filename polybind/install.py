"""
polybind.install
~~~~~~~~~~~~~~~~
``polybind deps install``: put the Python requirements of a project into an environment, without installing the project.

Two sources, merged and de-duplicated:

* ``pyproject.toml``, read only: ``[build-system].requires`` (nanobind, scikit-build-core, ...), ``[project].dependencies``,
  ``[project.optional-dependencies]`` and ``[dependency-groups]`` (PEP 735, ``include-group`` followed);
* the native dependencies of polybind itself that are pip packages (``[deps]``, ``[optional-deps]``), see :mod:`polybind.deps`.

Kinds (spec.md, section 7.3): ``build``, ``runtime``, ``extra:<name>``, ``group:<name>``, ``all`` (every one of them).
polybind itself is never installed by this: it is the program that is running.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polybind.config import ConfigError, read_toml
from polybind.deps import Dependencies, requirements

SELF = "polybind"
DEFAULT_KINDS = ("build", "runtime")

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize(name: str) -> str:
    """PEP 503: ``Foo_Bar.baz`` -> ``foo-bar-baz``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str | None:
    match = _NAME.match(requirement)
    return normalize(match.group(1)) if match else None


@dataclass
class ProjectRequirements:
    """What ``pyproject.toml`` declares, as lists of PEP 508 strings."""

    build: list[str] = field(default_factory=list)
    runtime: list[str] = field(default_factory=list)
    extras: dict[str, list[str]] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)


def _strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"pyproject.toml: {where} must be a list of requirement strings")
    return list(value)


def _resolve_group(name: str, raw: dict[str, Any], stack: tuple[str, ...]) -> list[str]:
    lookup = {normalize(k): k for k in raw}
    key = normalize(name)
    if key not in lookup:
        raise ConfigError(f"pyproject.toml: dependency group '{name}' is not defined")
    if key in stack:
        raise ConfigError("pyproject.toml: dependency groups include each other: " + " -> ".join((*stack, key)))
    out: list[str] = []
    for item in raw[lookup[key]]:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and set(item) == {"include-group"} and isinstance(item["include-group"], str):
            out += _resolve_group(item["include-group"], raw, (*stack, key))
        else:
            raise ConfigError(f"pyproject.toml: [dependency-groups].{lookup[key]} has an item that is neither a string nor an include-group")
    return out


def read_project(root: Path) -> ProjectRequirements:
    path = root / "pyproject.toml"
    if not path.is_file():
        return ProjectRequirements()
    data = read_toml(path)
    project = data.get("project", {})
    raw_groups = data.get("dependency-groups", {})
    return ProjectRequirements(
        build=_strings(data.get("build-system", {}).get("requires", []), "[build-system].requires"),
        runtime=_strings(project.get("dependencies", []), "[project].dependencies"),
        extras={
            group: _strings(items, f"[project.optional-dependencies].{group}")
            for group, items in project.get("optional-dependencies", {}).items()
        },
        groups={name: _resolve_group(name, raw_groups, ()) for name in raw_groups},
    )


def expand_kinds(kinds: list[str], project: ProjectRequirements, deps: Dependencies) -> list[str]:
    out: list[str] = []
    for kind in kinds:
        if kind == "all":
            out += ["build", "runtime"]
            out += [f"extra:{g}" for g in sorted(set(project.extras) | set(deps.groups))]
            out += [f"group:{g}" for g in project.groups]
        else:
            out.append(kind)
    return list(dict.fromkeys(out))


def gather(project: ProjectRequirements, deps: Dependencies, kinds: list[str]) -> list[str]:
    """The requirements of ``kinds``, ``pyproject.toml`` first, without duplicates and without polybind itself."""
    kinds = expand_kinds(kinds, project, deps)
    # a group that is installed is a group that is selected: its buildtime entries join `build`
    selected = tuple(k[len("extra:") :] for k in kinds if k.startswith("extra:") and k[len("extra:") :] in deps.groups)
    out: list[str] = []

    def add(items: list[str]) -> None:
        for item in items:
            if requirement_name(item) != SELF and item not in out:
                out.append(item)

    for kind in kinds:
        if kind == "build":
            add(project.build)
            add(requirements(deps, "build", selected))
        elif kind == "runtime":
            add(project.runtime)
            add(requirements(deps, "runtime"))
        elif kind.startswith("extra:"):
            group = kind[len("extra:") :]
            if group not in project.extras and group not in deps.groups:
                known = sorted(set(project.extras) | set(deps.groups))
                raise ConfigError(f"unknown extra '{group}' (known: {', '.join(known) or 'none'})")
            add(project.extras.get(group, []))
            if group in deps.groups:
                add(requirements(deps, kind))
        elif kind.startswith("group:"):
            group = kind[len("group:") :]
            names = {normalize(g): g for g in project.groups}
            if normalize(group) not in names:
                raise ConfigError(f"unknown dependency group '{group}' (known: {', '.join(project.groups) or 'none'})")
            add(project.groups[names[normalize(group)]])
        else:
            raise ConfigError(f"unknown kind '{kind}' (build, runtime, extra:<name>, group:<name> or all)")
    return out


def installer_command(installer: str, python: str, reqs: list[str], extra: list[str]) -> list[str]:
    """The command line that installs ``reqs`` into the environment of ``python``."""
    if installer == "auto":
        installer = "uv" if shutil.which("uv") else "pip"
    if installer == "uv":
        uv = shutil.which("uv")
        if uv is None:
            raise ConfigError("uv was not found (use --installer pip)")
        return [uv, "pip", "install", "--python", python, *extra, *reqs]
    return [python, "-m", "pip", "install", "--disable-pip-version-check", *extra, *reqs]


def install(
    project: ProjectRequirements,
    deps: Dependencies,
    kinds: list[str],
    *,
    installer: str = "auto",
    python: str = sys.executable,
    extra: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    reqs = gather(project, deps, kinds)
    if not reqs:
        print("nothing to install")
        return 0
    command = installer_command(installer, python, reqs, extra or [])
    print(f"{'would install' if dry_run else 'installing'} {len(reqs)} requirement(s):")
    for req in reqs:
        print(f"  {req}")
    if dry_run:
        print("command: " + shlex.join(command))
        return 0
    return subprocess.run(command, check=False).returncode
