"""
polybind.version
~~~~~~~~~~~~~~~~
Version and git-tag manager, generalised from httpp's ``version.py``.

One source of truth per project, either

* a C/C++ header with ``#define <PREFIX>VERSION_MAJOR|MINOR|PATCH <N>``, or
* the static ``version = "X.Y.Z"`` of ``[project]`` in ``pyproject.toml``.

Settings (``--file``, ``--prefix``, ``--tag-format``) can live in ``polybind.toml`` or
``[tool.polybind.version]`` of pyproject.toml; see :mod:`polybind.config`.

Usage::

    polybind version                    show X.Y.Z
    polybind version minor              show the minor part
    polybind version major +            major += 1
    polybind version patch +2           patch += 2
    polybind version minor -5           minor -= 5 (never below 0)
    polybind version patch 4            patch = 4
    polybind version tag                show the latest git tag
    polybind version tag set            set the version from the latest tag
    polybind version tag create [what]  commit and tag (major|minor|patch|X.Y.Z)
    polybind version check              exit 1 if the version is not the latest tag
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from polybind.config import ConfigError, load_config

if TYPE_CHECKING:
    Source = "HeaderSource | PyprojectSource"

PARTS = ("major", "minor", "patch")
DEFAULT_TAG_FORMAT = "v%s"

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")
_DEFINE_ANY = re.compile(
    r"^[ \t]*#[ \t]*define[ \t]+(\w*?)VERSION_MAJOR[ \t]+\(?\d+\)?", re.MULTILINE
)


class VersionError(Exception):
    """A problem the user can fix (bad input, missing source, git refused)."""


# ---------------------------------------------------------------------------
# Version value
# ---------------------------------------------------------------------------


@dataclass
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Version:
        m = _SEMVER.match(text.strip())
        if not m:
            raise VersionError(f"invalid version '{text}' (expected X.Y.Z)")
        return cls(*map(int, m.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def get(self, part: str) -> int:
        return int(getattr(self, part))

    def set(self, part: str, value: int) -> None:
        setattr(self, part, max(0, value))

    def bump(self, part: str) -> None:
        """Semantic bump: raising a part resets the parts below it."""
        if part == "major":
            self.major, self.minor, self.patch = self.major + 1, 0, 0
        elif part == "minor":
            self.minor, self.patch = self.minor + 1, 0
        elif part == "patch":
            self.patch += 1
        else:
            raise VersionError(f"unknown part '{part}'")


def apply_value(version: Version, part: str, value: str) -> None:
    """``value`` is a number (set) or an offset: ``+``, ``-``, ``+N``, ``-N``."""
    if value in ("+", "-"):
        value += "1"
    try:
        number = int(value)
    except ValueError:
        raise VersionError(f"invalid value for {part}: '{value}'") from None
    if value[0] in "+-":
        version.set(part, version.get(part) + number)
    elif number < 0:
        raise VersionError(f"invalid value for {part}: '{value}'")
    else:
        version.set(part, number)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class HeaderSource:
    """``#define <prefix>VERSION_MAJOR|MINOR|PATCH <N>`` (``(N)`` is accepted)."""

    def __init__(self, path: Path, prefix: str) -> None:
        self.path = path
        self.prefix = prefix

    def _regex(self, part: str) -> re.Pattern[str]:
        return re.compile(
            rf"^([ \t]*#[ \t]*define[ \t]+{re.escape(self.prefix)}VERSION_{part.upper()}"
            r"[ \t]+\(?)(\d+)(\)?)",
            re.MULTILINE,
        )

    def read(self) -> Version:
        text = self.path.read_text(encoding="utf-8")
        values = []
        for part in PARTS:
            m = self._regex(part).search(text)
            if not m:
                raise VersionError(
                    f"{self.path}: no '#define {self.prefix}VERSION_{part.upper()} <N>'"
                )
            values.append(int(m.group(2)))
        return Version(*values)

    def write(self, version: Version) -> None:
        text = self.path.read_text(encoding="utf-8")
        for part in PARTS:
            text = self._regex(part).sub(
                lambda m, p=part: f"{m.group(1)}{version.get(p)}{m.group(3)}", text, count=1
            )
        self.path.write_text(text, encoding="utf-8")


class PyprojectSource:
    """The static ``version = "X.Y.Z"`` line of the ``[project]`` table."""

    _LINE = re.compile(r"""^(\s*version\s*=\s*)(["'])([^"']*)(["'])(.*)$""")

    def __init__(self, path: Path) -> None:
        self.path = path

    def _locate(self, lines: list[str]) -> int:
        table = ""
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("["):
                table = stripped.split("#")[0].strip()
            elif table == "[project]" and self._LINE.match(line):
                return i
        raise VersionError(
            f"{self.path}: no static 'version = \"X.Y.Z\"' in [project] "
            "(is it dynamic? then use --file with the header that defines it)"
        )

    def read(self) -> Version:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        m = self._LINE.match(lines[self._locate(lines)])
        assert m is not None
        return Version.parse(m.group(3))

    def write(self, version: Version) -> None:
        text = self.path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        i = self._locate([ln.rstrip("\r\n") for ln in lines])
        m = self._LINE.match(lines[i].rstrip("\r\n"))
        assert m is not None
        ending = lines[i][len(lines[i].rstrip("\r\n")) :]
        lines[i] = f"{m.group(1)}{m.group(2)}{version}{m.group(4)}{m.group(5)}{ending}"
        self.path.write_text("".join(lines), encoding="utf-8")



def _header_candidates(root: Path) -> list[Path]:
    found: list[Path] = []
    for base, pattern in ((root / "include", "**/*"), (root, "*")):
        if base.is_dir():
            found += [
                p
                for p in sorted(base.glob(pattern))
                if p.is_file()
                and p.suffix in _HEADER_SUFFIXES
                and _DEFINE_ANY.search(p.read_text(encoding="utf-8", errors="ignore"))
            ]
    return sorted(set(found))


def detect_source(root: Path, file: Path | None = None, prefix: str | None = None) -> Source:
    """Pick the version source: ``--file``, else a header under include/, else pyproject."""
    if file is not None:
        path = file if file.is_absolute() else root / file
        if not path.is_file():
            raise VersionError(f"file not found: {path}")
        if path.suffix == ".toml":
            return PyprojectSource(path)
        if prefix is None:
            m = _DEFINE_ANY.search(path.read_text(encoding="utf-8", errors="ignore"))
            if not m:
                raise VersionError(f"{path}: no '#define ...VERSION_MAJOR <N>' found")
            prefix = m.group(1)
        return HeaderSource(path, prefix)

    headers = _header_candidates(root)
    if len(headers) > 1:
        names = ", ".join(str(h.relative_to(root)) for h in headers)
        raise VersionError(f"several headers define a version ({names}); choose with --file")
    if headers:
        return detect_source(root, headers[0], prefix)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        return PyprojectSource(pyproject)
    raise VersionError(f"no version source found in {root}; use --file")


def project_source(root: Path) -> HeaderSource:
    """The version source for a build backend that asks for a *dynamic* version (``[project].dynamic = ["version"]``).

    The settings (``[version]``) are the same as for the command line. ``pyproject.toml`` cannot be the source here: a
    dynamic version is by definition not written in it.
    """
    config = load_config(root)
    name = config.get("version", "file")
    source = detect_source(root, Path(name) if name else None, config.get("version", "prefix"))
    if isinstance(source, PyprojectSource):
        raise VersionError(
            "the version is dynamic, so it cannot come from a pyproject.toml; "
            "put it in a header (#define <PREFIX>VERSION_MAJOR/MINOR/PATCH) and set version.file if it is not found"
        )
    return source


def project_version(root: Path) -> Version:
    return project_source(root).read()


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise VersionError("git is not installed") from None
    if check and result.returncode != 0:
        raise VersionError(f"git {' '.join(args)}: {result.stderr.strip() or 'failed'}")
    return result


def git_last_tag(root: Path) -> str | None:
    result = _git(root, "describe", "--tags", "--abbrev=0", check=False)
    return result.stdout.strip() or None if result.returncode == 0 else None


def tag_name(version: Version, fmt: str) -> str:
    if "%s" not in fmt:
        raise VersionError(f"invalid tag format '{fmt}' (needs %s)")
    return fmt.replace("%s", str(version), 1)


def parse_tag(tag: str, fmt: str) -> Version:
    """``v1.2.3`` for the default format. A bare ``1.2.3`` is accepted too."""
    pre, _, post = fmt.partition("%s")
    if tag.startswith(pre) and tag.endswith(post) and len(tag) >= len(pre) + len(post):
        body = tag[len(pre) : len(tag) - len(post)]
        if _SEMVER.match(body):
            return Version.parse(body)
    if _SEMVER.match(tag):
        return Version.parse(tag)
    raise VersionError(f"tag '{tag}' does not match format '{fmt}'")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _resolve_target(args: argparse.Namespace, current: Version) -> Version:
    value = args.value
    target = Version(current.major, current.minor, current.patch)
    if value is None:
        return target
    if value in PARTS:
        target.bump(value)
        return target
    return Version.parse(value.lstrip("v"))


def cmd_show(args: argparse.Namespace, source: Source) -> int:
    version = source.read()
    print(version.get(args.part) if args.part else version)
    return 0


def cmd_part(args: argparse.Namespace, source: Source) -> int:
    version = source.read()
    if args.value is None:
        print(version.get(args.part))
        return 0
    apply_value(version, args.part, args.value)
    if args.dry_run:
        print(f"Would update version to {version}")
        return 0
    source.write(version)
    print(f"Version updated to {version}")
    return 0


def cmd_tag(args: argparse.Namespace, source: Source) -> int:
    root: Path = args.root
    action = args.action

    if action is None:
        tag = git_last_tag(root)
        print(tag if tag else "No git tags found")
        return 0

    if action == "set":
        tag = git_last_tag(root)
        if not tag:
            raise VersionError("No git tags found")
        version = parse_tag(tag, args.tag_format)
        if args.dry_run:
            print(f"Would set version from tag to {version}")
            return 0
        source.write(version)
        print(f"Version set from tag -> {version}")
        return 0

    # create
    version = _resolve_target(args, source.read())
    name = tag_name(version, args.tag_format)
    if _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{name}", check=False).returncode == 0:
        raise VersionError(f"tag {name} already exists")
    if args.dry_run:
        print(f"Would write {version} to {source.path}, commit it and tag {name}")
        return 0
    source.write(version)
    path = str(source.path)
    _git(root, "add", "--", path)
    _git(root, "commit", "--allow-empty", "-m", f"Release {version}", "--", path)
    _git(root, "tag", name)
    print(f"Committed and tagged {name}")
    return 0


def cmd_check(args: argparse.Namespace, source: Source) -> int:
    version = source.read()
    tag = git_last_tag(args.root)
    if not tag:
        print("No git tags found", file=sys.stderr)
        return 1
    tagged = parse_tag(tag, args.tag_format)
    if tagged != version:
        print(f"version {version} does not match latest tag {tag}", file=sys.stderr)
        return 1
    print(f"version {version} matches tag {tag}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser, *, sub: bool) -> None:
    """Options valid before *and* after the subcommand (subparsers must not reset them)."""
    d = argparse.SUPPRESS

    def default(value: object) -> object:
        return d if sub else value

    parser.add_argument(
        "--root", type=Path, default=default(Path.cwd()), help="project directory (default: cwd)"
    )
    parser.add_argument(
        "--file", type=Path, default=default(None),
        help="version source: a header with VERSION_MAJOR/MINOR/PATCH defines, or pyproject.toml",
    )
    parser.add_argument(
        "--prefix", default=default(None),
        help="define prefix before VERSION_MAJOR, e.g. HTTPP_ (default: detected)",
    )
    parser.add_argument(
        "--tag-format", default=default(None),
        help="git tag format, %%s is the version (default: v%%s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=default(False),
        help="say what would change; write nothing",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polybind version",
        description="Show, change and tag the project version.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__.split("Usage::")[1]),
    )
    _add_common(parser, sub=False)
    parser.set_defaults(func=cmd_show, part=None)
    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    for part in PARTS:
        p = subs.add_parser(part, help=f"show or change the {part} version")
        _add_common(p, sub=True)
        p.add_argument("value", nargs="?", help="a number (set) or an offset: + - +N -N")
        p.set_defaults(func=cmd_part, part=part)

    tag = subs.add_parser("tag", help="show the latest git tag; set / create")
    _add_common(tag, sub=True)
    tag.set_defaults(func=cmd_tag, action=None, value=None)
    tag_subs = tag.add_subparsers(dest="action", metavar="ACTION")
    tag_subs.add_parser("set", help="set the version from the latest tag").set_defaults(
        func=cmd_tag
    )
    create = tag_subs.add_parser("create", help="write the version, commit it, tag it")
    create.add_argument("value", nargs="?", help="major | minor | patch | X.Y.Z")
    create.set_defaults(func=cmd_tag)
    for p in tag_subs.choices.values():
        _add_common(p, sub=True)

    check = subs.add_parser("check", help="exit 1 when the version is not the latest tag")
    _add_common(check, sub=True)
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    try:
        # command line > polybind.toml > [tool.polybind] in pyproject.toml > built-in default
        config = load_config(args.root)
        file = args.file
        if file is None and config.get("version", "file"):
            file = Path(config.get("version", "file"))
        prefix = args.prefix if args.prefix is not None else config.get("version", "prefix")
        args.tag_format = (
            args.tag_format or config.get("version", "tag-format") or DEFAULT_TAG_FORMAT
        )
        source = detect_source(args.root, file, prefix)
        return int(args.func(args, source))
    except (VersionError, ConfigError) as exc:
        print(f"polybind: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
