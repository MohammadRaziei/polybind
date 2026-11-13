"""
polybind.errors
~~~~~~~~~~~~~~~
The one exception every command raises for a problem the user can fix: bad input, a missing
file, an invalid setting, git refusing. The command line prints it as ``polybind: error: ...``
and exits with 2. Anything else is a bug and keeps its traceback.
"""

from __future__ import annotations


class PolybindError(Exception):
    """A problem the user can fix; the message names the file and the key."""
