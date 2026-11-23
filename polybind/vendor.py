"""
polybind.vendor
~~~~~~~~~~~~~~~
``polybind vendor DIR``: copy the C++ dispatch helpers (the headers and their CMake config) into a project, for a build
that must not need polybind installed. ``polybind vendor --check DIR`` says whether that copy is still the one of the
installed polybind.

The copy is ``DIR/polybind/`` (``dispatch.hpp``, ``nanobind.hpp``, ``polybind-config.cmake``) and a stamp file
``VENDORED`` with the polybind version and the sha256 of every file. The CMake config is the same file as in the
installed package: it finds the headers next to itself, so ``include(DIR/polybind/polybind-config.cmake)`` gives the
target ``polybind::polybind`` with the include path ``DIR``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path

from polybind.errors import PolybindError

PACKAGE = Path(__file__).resolve().parent
STAMP = "VENDORED"

# (path in the installed package, name in the copy)
FILES: tuple[tuple[Path, str], ...] = (
    (PACKAGE / "include" / "polybind" / "dispatch.hpp", "dispatch.hpp"),
    (PACKAGE / "include" / "polybind" / "nanobind.hpp", "nanobind.hpp"),
    (PACKAGE / "cmake" / "polybind-config.cmake", "polybind-config.cmake"),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _own_version() -> str:
    from polybind.cli import _get_version

    return _get_version()


def _stamp_text(version: str, hashes: dict[str, str]) -> str:
    lines = [f"polybind {version}", ""]
    lines += [f"{digest}  {name}" for name, digest in sorted(hashes.items())]
    return "\n".join(lines) + "\n"


_STAMP_VERSION = re.compile(r"^polybind (\S+)$")
_STAMP_HASH = re.compile(r"^([0-9a-f]{64})  (\S+)$")


def _read_stamp(path: Path) -> tuple[str, dict[str, str]]:
    """The version and the hashes written by :func:`vendor`; a stamp that is not that is an error."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PolybindError(f"{path}: cannot be read ({exc})") from exc
    head = _STAMP_VERSION.match(lines[0]) if lines else None
    hashes = {}
    for line in lines[2:]:
        match = _STAMP_HASH.match(line)
        if not match:
            head = None
            break
        hashes[match.group(2)] = match.group(1)
    if head is None or len(lines) < 3 or lines[1] != "" or not hashes:
        raise PolybindError(f"{path}: not a polybind VENDORED stamp")
    return head.group(1), hashes


def vendor(directory: Path) -> list[str]:
    """Copy the helpers to ``directory/polybind``; return the names written."""
    for source, _name in FILES:
        if not source.is_file():
            raise PolybindError(f"{source}: missing from the installed polybind (reinstall it)")
    target = directory / "polybind"
    target.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for source, name in FILES:
        shutil.copyfile(source, target / name)
        hashes[name] = _sha256(source)
    (target / STAMP).write_text(_stamp_text(_own_version(), hashes), encoding="utf-8")
    return [name for _source, name in FILES]


def check(directory: Path) -> list[str]:
    """What is wrong with the copy in ``directory/polybind``: empty when it is the installed polybind's, byte for byte."""
    target = directory / "polybind"
    stamp = target / STAMP
    if not stamp.is_file():
        raise PolybindError(f"{target}: no {STAMP} stamp, so this is not a copy made by `polybind vendor`")
    version, stamped = _read_stamp(stamp)
    problems: list[str] = []
    for source, name in FILES:
        path = target / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif _sha256(path) != stamped.get(name):
            problems.append(f"{name}: changed since it was copied (the stamp has another hash)")
        elif _sha256(path) != _sha256(source):
            problems.append(f"{name}: from polybind {version}, the installed one differs (run `polybind vendor` again)")
    return problems


def cmd_vendor(args: argparse.Namespace) -> int:
    if args.check:
        problems = check(args.directory)
        if problems:
            for problem in problems:
                print(f"polybind vendor: {problem}")
            return 1
        print(f"{args.directory / 'polybind'}: up to date with polybind {_own_version()}")
        return 0
    names = vendor(args.directory)
    print(f"polybind {_own_version()}: wrote {', '.join(names)} to {args.directory / 'polybind'}")
    return 0


def add_parser(subs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``polybind vendor``."""
    p = subs.add_parser(
        "vendor",
        help="copy the C++ dispatch helpers (headers and CMake config) into a project",
        description="Copy the C++ dispatch helpers into DIR/polybind (dispatch.hpp, nanobind.hpp, polybind-config.cmake)\n"
        "and a VENDORED stamp, for a project that must build without polybind installed. In CMake:\n"
        "  include(DIR/polybind/polybind-config.cmake)\n"
        "  target_link_libraries(my_module PRIVATE polybind::polybind)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("directory", metavar="DIR", type=Path, help="where to put the polybind/ directory (the include path)")
    p.add_argument(
        "--check", action="store_true",
        help="write nothing: exit 1 if the copy in DIR is missing a file, was changed, or is not the installed polybind's",
    )
    p.set_defaults(func=cmd_vendor)
