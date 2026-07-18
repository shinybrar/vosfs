"""Shared scaffolding for mapped-source command modules.

Every mapped-operand command (``ls``, ``cat``, ``cp``, ``mv``, ``mkdir``,
``rmdir``, ``rm``, ``unlink``, ``stat``, ``basename``, ``dirname``) parses its
own raw ``argv`` and renders stable diagnostics. This module is the single home
for the pieces they all share: raw-argument capture, the malformed-help shield,
the mapped-operand record, usage-error reporting, and binary stdout access.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, Protocol, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_prefix

if TYPE_CHECKING:
    from typer._click import Context

_RAW_ARGUMENTS = "fsspec_cli.raw_arguments"


class _RawCommand(TyperCommand):
    """A Typer command that captures raw ``argv`` before framework parsing.

    Command preflight needs the exact tokens the user supplied, so the raw
    arguments are stashed on ``ctx.meta`` and malformed ``--help=`` tokens are
    shielded from Click's eager help option.
    """

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
    """Return the raw ``argv`` captured by :class:`_RawCommand`."""
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _usage_error(command: str, diagnostic: str) -> NoReturn:
    """Emit one stable usage diagnostic on stderr and exit ``2``."""
    prefix = _render_diagnostic_prefix(command)
    typer.echo(f"{prefix} {diagnostic}", err=True, color=True)
    raise typer.Exit(2)


@dataclass(frozen=True)
class _MappedOperand:
    """A parsed ``name:/path`` operand selecting one mapped source."""

    spelling: str
    name: str
    path: str


class _BinaryWriter(Protocol):
    """The write/flush surface of a binary stdout stream."""

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...


def _binary_stdout() -> _BinaryWriter:
    """Return the process binary stdout buffer, or raise if unavailable."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        message = "stdout has no binary buffer"
        raise OSError(message)
    return buffer
