"""Opt-in backend-specific command extensions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

import typer  # noqa: TC002 - Typer evaluates callback annotations at runtime.

from ._app import _COMMAND_CONTEXT, _ensure_no_active_event_loop
from ._command import (
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _raw_arguments,
    _RawCommand,
    _run_single_operand_text,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource


class _SignCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("sign", "[--] name:/path")


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _MappedOperand:
    operand = None
    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        if operand is not None:
            _usage_error(command, "extra operand")
        operand = _parse_mapped_operand(command, argument, known_names)
    if operand is None:
        _usage_error(command, "missing mapped filesystem operand")
    return operand


async def _sign(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> str | _Failure:
    try:
        result = filesystem.sign(operand.path)
    except NotImplementedError as error:
        return _Failure(operand, backend_error=error)
    except Exception as error:  # noqa: BLE001 - backend capability boundary.
        return _Failure(operand, backend_error=error)
    if type(result) is not str or not result:
        return _Failure(operand)
    return f"{result}\n"


async def _run_sign(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    operand = _preflight(command, raw_arguments, sources)

    async def operation(filesystem: AsyncFileSystem) -> str | _Failure:
        return await _sign(operand, filesystem)

    await _run_single_operand_text(command, operand, sources, operation)


class _SignExtension:
    def register(
        self,
        typer_app: typer.Typer,
        sources: Mapping[str, AsyncFilesystemSource],
    ) -> None:
        @typer_app.command(
            "sign",
            cls=_SignCommand,
            help="Create a backend-signed URL",
            context_settings=_COMMAND_CONTEXT,
        )
        def handler(ctx: typer.Context) -> None:
            raw_arguments = _raw_arguments(ctx)
            _ensure_no_active_event_loop("sign")
            asyncio.run(_run_sign("sign", raw_arguments, sources))


sign: Final = _SignExtension()

__all__ = ["sign"]
