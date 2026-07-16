"""Raw Typer parsing and async execution for ``ls``."""

from __future__ import annotations

import locale
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context

    from ._app import AsyncFilesystemSource

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
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(raw_arguments, sources)
    invocation = _SourceInvocation(sources)
    succeeded = False
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            succeeded = await _trace_files(request, filesystems)
    finally:
        cleanup_failed = await invocation.close(sys.exc_info())
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)


async def _trace_files(
    request: _LsRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> bool:
    for operand in request.operands:
        # fsspec's native async API intentionally exposes underscore coroutines.
        info = await filesystems[operand.name]._info(operand.path)  # noqa: SLF001
        if not (
            isinstance(info, Mapping)
            and isinstance(info.get("type"), str)
            and info["type"] == "file"
        ):
            rendered_operand = _render_diagnostic_value(operand.spelling)
            typer.echo(f"ls: {rendered_operand}: incompatible result", err=True)
            return False
        typer.echo(operand.spelling)
    return True
