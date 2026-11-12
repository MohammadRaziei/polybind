"""A scikit-build-core dynamic-metadata provider: the Python requirements come from ``polybind.toml`` (spec 7.2).

A project turns it on with ``polybind`` in ``[build-system].requires`` and::

    [project]
    dynamic = ["dependencies", "optional-dependencies"]   # the ones it should fill

    [[tool.dynamic-metadata]]
    provider = "polybind.metadata"

scikit-build-core then asks the provider twice:

* ``get_requires_for_dynamic_metadata``: the *build* requirements, which pip installs into the isolated build environment
  before the build starts;
* ``dynamic_metadata``: the *runtime* requirements (``dependencies``), the extras (``optional-dependencies``) and the
  ``version`` (read from the header, see ``polybind.version``) that go into the wheel metadata.

Why this cannot loop (the rules the code keeps):

* The provider only reads files in the project directory. It never runs pip, uv, cmake or the build itself, so nothing it
  does can start another build.
* ``polybind`` is not listed by the provider: it has to be in ``[build-system].requires`` to be imported at all, and listing
  it again would only be a second route to the same package.
* Neither answer depends on the other or on the environment: both are computed from the same files, so asking twice (or in
  another build state, or from an sdist) gives the same lists.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polybind.config import load_config
from polybind.deps import load_dependencies, requirements
from polybind.version import project_version

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _is_polybind(requirement: str) -> bool:
    match = _NAME.match(requirement)
    return bool(match) and re.sub(r"[-_.]+", "-", match.group(1)).lower() == "polybind"  # type: ignore[union-attr]


def _without_polybind(items: list[str]) -> list[str]:
    return [item for item in items if not _is_polybind(item)]


def _check_settings(settings: Mapping[str, Any]) -> None:
    if settings:
        names = ", ".join(sorted(settings))
        raise RuntimeError(f"polybind: the [[tool.dynamic-metadata]] entry takes no settings besides 'provider' (got: {names})")


class Provider:
    """Entry point ``dynamic_metadata.provider`` = ``polybind``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def _dependencies(self) -> Any:
        # The build runs with the source directory as the current directory (PEP 517).
        return load_dependencies(load_config(self.root or Path.cwd()))

    def get_requires_for_dynamic_metadata(self, settings: Mapping[str, Any]) -> list[str]:
        _check_settings(settings)
        return _without_polybind(requirements(self._dependencies(), "build"))

    def dynamic_metadata(self, settings: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any]:
        _check_settings(settings)
        deps = self._dependencies()
        declared = set(project.get("dynamic", []))

        runtime = _without_polybind(requirements(deps, "runtime"))
        extras = {
            group: found
            for group in deps.groups
            if (found := _without_polybind(requirements(deps, f"extra:{group}")))
        }

        # A requirement that has nowhere to go is an error: silently dropping it is the failure this provider exists to prevent.
        if runtime and "dependencies" not in declared:
            raise RuntimeError("polybind: the settings have runtime dependencies, so [project].dynamic must list 'dependencies'")
        if extras and "optional-dependencies" not in declared:
            raise RuntimeError(
                "polybind: the settings have optional dependencies, so [project].dynamic must list 'optional-dependencies'"
            )

        fragment: dict[str, Any] = {}
        if "version" in declared:
            fragment["version"] = str(project_version(self.root or Path.cwd()))
        if "dependencies" in declared:
            fragment["dependencies"] = runtime
        if "optional-dependencies" in declared:
            fragment["optional-dependencies"] = extras
        return fragment
