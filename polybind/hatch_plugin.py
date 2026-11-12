"""A hatch version source: ``[tool.hatch.version] source = "polybind"`` reads the version from the project's header.

Imported only by hatch (entry point group ``hatch``), so polybind itself does not depend on hatchling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.plugin import hookimpl
from hatchling.version.source.plugin.interface import VersionSourceInterface

from polybind.version import Version, VersionError, project_source


class PolybindVersionSource(VersionSourceInterface):
    PLUGIN_NAME = "polybind"

    def get_version_data(self) -> dict[str, Any]:
        try:
            return {"version": str(project_source(Path(self.root)).read())}
        except VersionError as exc:
            raise ValueError(f"polybind: {exc}") from exc

    def set_version(self, version: str, version_data: dict[str, Any]) -> None:  # `hatch version 1.2.3`
        try:
            project_source(Path(self.root)).write(Version.parse(version))
        except VersionError as exc:
            raise ValueError(f"polybind: {exc}") from exc


@hookimpl
def hatch_register_version_source() -> type[VersionSourceInterface]:
    return PolybindVersionSource
