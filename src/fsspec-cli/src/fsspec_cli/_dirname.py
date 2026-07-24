"""Lexical execution for typed ``dirname``."""

from __future__ import annotations

import typer

from ._command import _usage_error
from ._diagnostics import _render_diagnostic_value


def _posix_dirname_string(string: str) -> str:
    if "/" not in string:
        return "."

    if string and all(character == "/" for character in string):
        return "/"

    while string.endswith("/"):
        string = string[:-1]

    if "/" not in string:
        return "."

    prefix = string.rsplit("/", 1)[0]
    if prefix == "":
        return "/"
    return prefix


def _run_dirname(command: str, operand: str) -> None:
    if "\0" in operand:
        rendered = _render_diagnostic_value(operand)
        _usage_error(command, f"{rendered}: invalid operand")
    typer.echo(_posix_dirname_string(operand), nl=True, color=True)
