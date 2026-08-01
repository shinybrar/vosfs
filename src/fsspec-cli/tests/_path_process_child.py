"""Subprocess fixture for public-seam lexical command output tests.

``argv[1]`` selects the command (``basename`` or ``dirname``); the remaining
arguments are forwarded verbatim.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn

from fsspec_cli import App

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _network_guard import _block_network  # noqa: E402


class _ProcessMonkeyPatch:
    def setattr(self, target: object, name: str, value: object) -> None:
        setattr(target, name, value)


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def main() -> None:
    _block_network(_ProcessMonkeyPatch())
    command = sys.argv[1]
    App({"memory": _source_must_not_run}).typer_app(
        prog_name=f"{command}-process-child",
        args=sys.argv[1:],
        standalone_mode=False,
    )


if __name__ == "__main__":
    main()
