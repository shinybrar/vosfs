"""Raw Typer parsing and lexical execution for ``basename``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value

if TYPE_CHECKING:
    from typer._click import Context

_RAW_ARGUMENTS = "fsspec_cli.raw_arguments"


@dataclass(frozen=True)
class _BasenameRequest:
    operand: str


class _BasenameCommand(TyperCommand):
    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        ctx.meta[_RAW_ARGUMENTS] = tuple(args)
        return super().parse_args(ctx, _shield_help_values(args))


def _shield_help_values(arguments: list[str]) -> list[str]:
    """Keep malformed help tokens available to command preflight."""
    shielded = []
    options_active = True
    for argument in arguments:
        if argument == "--":
            options_active = False
        if options_active and argument.startswith("--help="):
            shielded.append("--fsspec-cli-unsupported-help-value")
        else:
            shielded.append(argument)
    return shielded


def _raw_arguments(ctx: typer.Context) -> tuple[str, ...]:
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _usage_error(command: str, diagnostic: str) -> NoReturn:
    prefix = _render_diagnostic_prefix(command)
    typer.echo(f"{prefix} {diagnostic}", err=True, color=True)
    raise typer.Exit(2)


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
) -> _BasenameRequest:
    operands = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")

        if "\0" in argument:
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: invalid operand")

        operands.append(argument)

    if not operands:
        _usage_error(command, "missing operand")
    if len(operands) > 1:
        _usage_error(command, "extra operand")

    return _BasenameRequest(operand=operands[0])


def _posix_basename_string(string: str) -> str:
    if string and all(character == "/" for character in string):
        return "/"

    while string.endswith("/"):
        string = string[:-1]

    if "/" in string:
        return string.rsplit("/", 1)[-1]
    return string


def _run_basename(command: str, raw_arguments: tuple[str, ...]) -> None:
    request = _preflight(command, raw_arguments)
    typer.echo(_posix_basename_string(request.operand), nl=True, color=True)
