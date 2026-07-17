"""Subprocess fixture for public-seam ``basename`` output tests."""

from __future__ import annotations

import sys
from typing import NoReturn

from fsspec_cli import App


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def main() -> None:
    operand = sys.argv.pop(1)
    App({"memory": _source_must_not_run}).typer_app(
        prog_name="basename-process-child",
        args=["basename", operand],
        standalone_mode=False,
    )


if __name__ == "__main__":
    main()
