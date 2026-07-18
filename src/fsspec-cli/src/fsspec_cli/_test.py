"""Raw Typer parsing and async execution for ``test`` predicates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import typer

from ._command import (
    _MappedOperand,
    _parse_mapped_operand,
    _RawCommand,
    _render_backend_failure,
    _render_operand_diagnostic,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource

_Predicate = Literal["e", "d", "f"]
_PREDICATES: dict[str, _Predicate] = {"-e": "e", "-d": "d", "-f": "f"}


@dataclass(frozen=True)
class _TestRequest:
    predicate: _Predicate
    operand: _MappedOperand


class _TestCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("test", "-e|-d|-f [--] name:/path")


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _TestRequest:
    predicate: _Predicate | None = None
    operand = None
    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        selected = _PREDICATES.get(argument) if options_active else None
        if selected is not None:
            if predicate is not None:
                _usage_error(command, "exactly one predicate selector is required")
            predicate = selected
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        if operand is not None:
            _usage_error(command, "extra operand")
        operand = _parse_mapped_operand(command, argument, known_names)

    if predicate is None:
        _usage_error(command, "exactly one predicate selector is required")
    if operand is None:
        _usage_error(command, "missing mapped filesystem operand")
    return _TestRequest(predicate, operand)


async def _evaluate(
    request: _TestRequest,
    filesystem: AsyncFileSystem,
) -> object:
    path = request.operand.path
    if request.predicate == "e":
        result = await filesystem._exists(path)  # noqa: SLF001
    elif request.predicate == "d":
        result = await filesystem._isdir(path)  # noqa: SLF001
    else:
        result = await filesystem._isfile(path)  # noqa: SLF001
    return result


async def _run_test(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    backend_error: Exception | None = None
    matched = False
    try:
        filesystems = await invocation.acquire((request.operand.name,))
        if filesystems is not None:
            try:
                result = await _evaluate(
                    request,
                    filesystems[request.operand.name],
                )
            except Exception as error:  # noqa: BLE001 - awaited backend boundary.
                backend_error = error
                _render_backend_failure(command, request.operand, error)
            else:
                if type(result) is bool:
                    matched = result
                else:
                    _render_operand_diagnostic(
                        command,
                        request.operand,
                        "incompatible result",
                    )
    finally:
        cleanup_failed = await invocation.close_with_command_error(backend_error)
    if not matched or cleanup_failed:
        raise typer.Exit(1)
