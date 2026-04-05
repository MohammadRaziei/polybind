"""
polybind.__main__
~~~~~~~~~~~~~~~~~
CLI for generating unified Python wrappers from C++ stub files.

Usage:
    python -m polybind input.pyi [options]
    polybind input.pyi [options]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polybind",
        description=(
            "Generate unified Python wrapper classes from C++ .pyi stub files.\n"
            "Supports nanobind, pybind11, and Cython stubs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              polybind _novasvg.pyi
              polybind _novasvg.pyi -o novasvg.py
              polybind _novasvg.pyi --module-name novasvg --verbose
        """),
    )

    parser.add_argument(
        "pyi",
        metavar="INPUT.pyi",
        type=Path,
        help="Path to the .pyi stub file to process.",
    )

    parser.add_argument(
        "-o", "--output",
        metavar="OUTPUT.py",
        type=Path,
        default=None,
        help=(
            "Output .py file path. "
            "Defaults to INPUT.py (leading underscore stripped)."
        ),
    )

    parser.add_argument(
        "-m", "--module-name",
        metavar="NAME",
        default=None,
        help=(
            "Override the C-extension module name used in import statements. "
            "Defaults to the stem of INPUT.pyi with leading underscore stripped."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated code to stdout without writing any file.",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print details about discovered groups and variants.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )

    return parser


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("polybind")
    except Exception:
        return "0.0.0"


def _default_output(pyi_path: Path) -> Path:
    """Strip leading underscore from stem and return .py path next to input."""
    stem = pyi_path.stem.lstrip("_")
    return pyi_path.with_name(stem + ".py")


class CLI:
    """Encapsulates the command-line workflow."""

    def __init__(self, args: list[str] | None = None) -> None:
        import textwrap  # local to avoid module-level side-effects
        self._textwrap = textwrap
        self.parser = _build_parser()
        self.ns = self.parser.parse_args(args)

    # ------------------------------------------------------------------
    def run(self) -> int:
        from polybind.core import PolybindGenerator

        pyi_path: Path = self.ns.pyi

        if not pyi_path.exists():
            self._error(f"File not found: {pyi_path}")
            return 1

        if pyi_path.suffix != ".pyi":
            self._warn(f"Expected a .pyi file, got '{pyi_path.suffix}'. Continuing anyway.")

        output_path: Path = self.ns.output or _default_output(pyi_path)

        try:
            gen = PolybindGenerator(pyi_path, module_name=self.ns.module_name)

            if self.ns.dry_run:
                source = gen.generate_source()
                print(source)
                return 0

            groups = gen.run(output_path)

            if self.ns.verbose:
                self._print_summary(groups, output_path)
            else:
                print(f"✓ Written → {output_path}")

            return 0

        except ValueError as exc:
            self._error(str(exc))
            return 2
        except Exception as exc:  # noqa: BLE001
            self._error(f"Unexpected error: {exc}")
            if self.ns.verbose:
                import traceback
                traceback.print_exc()
            return 3

    # ------------------------------------------------------------------
    def _print_summary(self, groups: list, output_path: Path) -> None:
        from polybind.core import WrapperGroup
        print(f"\n{'─' * 52}")
        print(f"  polybind  →  {output_path}")
        print(f"{'─' * 52}")
        for g in sorted(groups, key=lambda x: x.base_name):
            print(f"\n  class {g.base_name}")
            for v in sorted(g.variants, key=lambda x: x.type_suffix):
                dunders = f"  [{', '.join(v.dunder_methods)}]" if v.dunder_methods else ""
                print(f"    {v.raw_name:<30} →  {v.py_type}{dunders}")
        print(f"\n{'─' * 52}\n")

    # ------------------------------------------------------------------
    @staticmethod
    def _error(msg: str) -> None:
        print(f"polybind: error: {msg}", file=sys.stderr)

    @staticmethod
    def _warn(msg: str) -> None:
        print(f"polybind: warning: {msg}", file=sys.stderr)


import textwrap  # needed by _build_parser epilog


def main(args: list[str] | None = None) -> None:
    sys.exit(CLI(args).run())


if __name__ == "__main__":
    main()
