"""Opt-in backend-specific command extensions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ._app import _register_async_command
from ._command import (
    _Failure,
    _MappedOperand,
    _preflight_single_mapped_operand,
    _RawCommand,
    _run_single_operand_text,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import typer
    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource


class _SignCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("sign", "[--] name:/path")


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
    operand = _preflight_single_mapped_operand(command, raw_arguments, sources)
    await _run_single_operand_text(
        command, operand, sources, lambda filesystem: _sign(operand, filesystem)
    )


class _SignExtension:
    def register(
        self,
        typer_app: typer.Typer,
        sources: Mapping[str, AsyncFilesystemSource],
    ) -> None:
        _register_async_command(
            typer_app,
            sources,
            (
                "sign",
                "Create a backend-signed URL",
                _run_sign,
                _SignCommand,
            ),
        )


sign: Final = _SignExtension()

__all__ = ["sign"]
