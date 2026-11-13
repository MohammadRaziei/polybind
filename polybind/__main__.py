"""``python -m polybind`` and the ``polybind`` console script: see :mod:`polybind.cli`."""

from __future__ import annotations

import sys

from polybind.cli import main

if __name__ == "__main__":
    sys.exit(main())
