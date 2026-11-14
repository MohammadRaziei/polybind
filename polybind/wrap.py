"""
polybind.wrap
~~~~~~~~~~~~~
``polybind wrap``: one Python class per C++ template from a ``.pyi`` stub file. Classes named
``Box__int32``, ``Box__float64`` become a single ``Box`` that dispatches on the argument types or on
``dtypes=[...]``. The stub can come from nanobind, pybind11 or Cython. The generator itself is
:mod:`polybind.core`.

Usage::

    polybind wrap _novasvg.pyi
    polybind wrap _novasvg.pyi -o novasvg.py
    polybind wrap _novasvg.pyi --module-name novasvg --verbose
    polybind wrap _novasvg.pyi --dry-run
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path
from typing import Any

from polybind.errors import PolybindError


def default_output(pyi_path: Path) -> Path:
    """The ``.py`` file next to the stub, with the leading underscores of the stem stripped."""
    return pyi_path.with_name(pyi_path.stem.lstrip("_") + ".py")


def _print_summary(groups: list[Any], output_path: Path) -> None:
    print(f"\n{'─' * 52}")
    print(f"  polybind  →  {output_path}")
    print(f"{'─' * 52}")
    for g in sorted(groups, key=lambda x: x.base_name):
        print(f"\n  class {g.base_name}")
        for v in sorted(g.variants, key=lambda x: x.suffix_key):
            dunders = f"  [{', '.join(v.dunder_methods)}]" if v.dunder_methods else ""
            py = "/".join(v.py_types)
            print(f"    {v.raw_name:<34} →  {py}{dunders}")
    print(f"\n{'─' * 52}\n")


def cmd_wrap(args: argparse.Namespace) -> int:
    from polybind.core import PolybindGenerator

    pyi_path: Path = args.pyi
    if not pyi_path.exists():
        raise PolybindError(f"{pyi_path}: file not found")
    if pyi_path.suffix != ".pyi":
        print(
            f"polybind: warning: expected a .pyi file, got '{pyi_path.suffix}'. Continuing anyway.",
            file=sys.stderr,
        )
    output_path: Path = args.output or default_output(pyi_path)

    try:
        generator = PolybindGenerator(pyi_path, module_name=args.module_name)
        if args.dry_run:
            print(generator.generate_source())
            return 0
        groups = generator.run(output_path)
    except ValueError as exc:  # the stub is not one the generator can handle
        raise PolybindError(f"{pyi_path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 -- a bug: say so, keep the traceback for -v
        print(f"polybind: error: unexpected error: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 3

    if args.verbose:
        _print_summary(groups, output_path)
    else:
        print(f"✓ Written → {output_path}")
    return 0


def add_parser(subs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``polybind wrap``."""
    p = subs.add_parser(
        "wrap",
        help="one Python class for the C++ template variants in a .pyi stub",
        description="Write one Python class for each group of C++ template variants in a .pyi stub\n"
        "(Box__int32, Box__float64 -> Box). Works on stubs from nanobind, pybind11 and Cython.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__.split("Usage::")[1]),
    )
    p.add_argument("pyi", metavar="INPUT.pyi", type=Path, help="the .pyi stub file to process")
    p.add_argument(
        "-o", "--output", metavar="OUTPUT.py", type=Path, default=None,
        help="output .py file (default: INPUT.py next to the stub, leading underscores of the name stripped)",
    )
    p.add_argument(
        "-m", "--module-name", metavar="NAME", default=None,
        help="the C-extension module name used in the import statements "
        "(default: the stem of INPUT.pyi, leading underscores stripped)",
    )
    p.add_argument("--dry-run", action="store_true", help="print the generated code; write no file")
    p.add_argument("-v", "--verbose", action="store_true", help="print the groups and variants that were found")
    p.set_defaults(func=cmd_wrap)
