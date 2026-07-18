"""Raw Typer parsing and lexical execution for ``dirname``."""

from __future__ import annotations

from dataclasses import dataclass

import typer

from ._command import _usage_error
from ._diagnostics import _render_diagnostic_value


@dataclass(frozen=True)
class _DirnameRequest:
    operand: str


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
) -> _DirnameRequest:
    operands = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")

        if len(operands) >= 1:
            _usage_error(command, "extra operand")

        if "\0" in argument:
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: invalid operand")

        operands.append(argument)

    if not operands:
        _usage_error(command, "missing operand")

    return _DirnameRequest(operand=operands[0])


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


def _run_dirname(command: str, raw_arguments: tuple[str, ...]) -> None:
    request = _preflight(command, raw_arguments)
    typer.echo(_posix_dirname_string(request.operand), nl=True, color=True)
