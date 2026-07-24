"""Opt-in backend-specific command extensions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, cast

import typer

from ._app import CommandContext, _ensure_no_active_event_loop
from ._command import (
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _run_single_operand_text,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


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
    operand: _MappedOperand,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_single_operand_text(
        command,
        operand,
        sources,
        lambda filesystem: _sign(operand, filesystem),
    )


def sign(
    ctx: typer.Context,
    operand: Annotated[str, typer.Argument(metavar="name:/path")],
) -> None:
    """Create a backend-signed URL."""
    sources = cast("CommandContext", ctx.find_object(CommandContext)).sources
    mapped = _parse_mapped_operand("sign", operand, sources)
    _ensure_no_active_event_loop("sign")
    asyncio.run(_run_sign("sign", mapped, sources))


__all__ = ["sign"]
