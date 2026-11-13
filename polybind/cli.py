"""
polybind.cli
~~~~~~~~~~~~
The command line: one parser, one command per tool. Every tool lives in its own module and
registers itself with ``add_parser``; this module only builds the root parser and runs.

    polybind gen       :mod:`polybind.gen`      wrapper classes from C++ .pyi stubs
    polybind version   :mod:`polybind.version`  show, change and tag the project version
    polybind deps      :mod:`polybind.deps`     native dependencies
    polybind config    :mod:`polybind.config`   the effective settings
    polybind cmake-dir                          the directory of PolybindDeps.cmake

Exit codes: 0 success; 1 a check that did not pass (``version check``); 2 a problem the user
can fix (bad input, missing file, invalid setting), printed as ``polybind: error: ...``;
3 a bug in the generator.
"""

from __future__ import annotations

import argparse
import difflib
import functools
import sys

from polybind.errors import PolybindError

_EPILOG = """\
run `polybind COMMAND -h` for the options of a command

exit codes:
  0  success
  1  a check did not pass (polybind version check)
  2  a problem you can fix: bad input, missing file, invalid setting
  3  a bug in the generator
"""


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("polybind")
    except Exception:  # noqa: BLE001 -- running from a source tree that is not installed
        return "0.0.0"


def _cmake_dir(_args: argparse.Namespace) -> int:
    from polybind.deps import CMAKE_DIR

    print(CMAKE_DIR)
    return 0


def build_parser() -> argparse.ArgumentParser:
    from polybind import config, deps, gen, version

    parser = argparse.ArgumentParser(
        prog="polybind",
        description="A toolbox for hybrid C++/Python projects: wrappers for nanobind, pybind11\n"
        "and Cython, one version for the whole project, native dependencies.",
        formatter_class=functools.partial(
            argparse.RawDescriptionHelpFormatter, max_help_position=24
        ),
        epilog=_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    subs = parser.add_subparsers(title="commands", dest="command", required=True)
    gen.add_parser(subs)
    version.add_parser(subs)
    deps.add_parser(subs)
    config.add_parser(subs)
    subs.add_parser(
        "cmake-dir",
        help="print the directory of PolybindDeps.cmake",
        description="Print the directory of PolybindDeps.cmake, for include() in CMakeLists.txt.",
    ).set_defaults(func=_cmake_dir)
    return parser


def _unknown_command(parser: argparse.ArgumentParser, argv: list[str]) -> str | None:
    """A hint for a first word that is not a command (``polybind x.pyi`` was the old form of ``gen``)."""
    first = argv[0] if argv else ""
    if not first or first.startswith("-"):
        return None
    subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    commands = list(subs.choices)
    if first in commands:
        return None
    if first.endswith(".pyi"):
        return f"unknown command '{first}'; to generate wrappers run: polybind gen {first}"
    close = difflib.get_close_matches(first, commands, n=1)
    hint = f"; did you mean '{close[0]}'?" if close else ""
    return f"unknown command '{first}'{hint} (choose from {', '.join(commands)})"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    problem = _unknown_command(parser, argv)
    if problem:
        parser.print_usage(sys.stderr)
        print(f"polybind: error: {problem}", file=sys.stderr)
        return 2
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PolybindError as exc:
        print(f"polybind: error: {exc}", file=sys.stderr)
        return 2
