"""Raw Typer parsing and source-free preflight for ``ls``."""

from __future__ import annotations

import locale
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, cast

import typer
from typer.core import TyperCommand

if TYPE_CHECKING:
    from collections.abc import Collection

_RAW_ARGUMENTS = "fsspec_cli.raw_arguments"


@dataclass(frozen=True)
class _MappedOperand:
    spelling: str
    name: str
    path: str


@dataclass(frozen=True)
class _LsRequest:
    include_almost_all: bool
    operands: tuple[_MappedOperand, ...]


class _LsCommand(TyperCommand):
    def parse_args(self, ctx: object, args: list[str]) -> list[str]:
        context = cast("typer.Context", ctx)
        context.meta[_RAW_ARGUMENTS] = tuple(args)
        return super().parse_args(context, _shield_help_values(args))


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


def _render_diagnostic_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _usage_error(diagnostic: str) -> NoReturn:
    typer.echo(diagnostic, err=True)
    raise typer.Exit(2)


def _preflight(
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _LsRequest:
    include_almost_all = False
    operands = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            if all(character == "A" for character in argument[1:]):
                include_almost_all = True
                continue
            rendered = _render_diagnostic_value(argument)
            _usage_error(f"ls: {rendered}: unsupported option")

        name, separator, path = argument.partition(":")
        if (
            not name
            or not separator
            or not path.startswith("/")
            or "\0" in argument
            or "\n" in argument
        ):
            rendered = _render_diagnostic_value(argument)
            _usage_error(f"ls: {rendered}: invalid mapped filesystem operand")

        if name not in known_names:
            known = sorted(
                known_names,
                key=lambda candidate: (locale.strxfrm(candidate), candidate),
            )
            rendered_operand = _render_diagnostic_value(argument)
            rendered_names = ", ".join(
                _render_diagnostic_value(candidate) for candidate in known
            )
            _usage_error(
                f"ls: {rendered_operand}: unknown filesystem (known: {rendered_names})"
            )

        operands.append(_MappedOperand(spelling=argument, name=name, path=path))

    if not operands:
        _usage_error("ls: missing mapped filesystem operand")

    return _LsRequest(
        include_almost_all=include_almost_all,
        operands=tuple(operands),
    )


async def _run_ls(
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> None:
    _preflight(raw_arguments, known_names)
