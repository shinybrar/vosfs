"""Raw Typer parsing and async execution for same-source file ``mv``."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import typer

from ._command import _parse_mapped_operand, _usage_error
from ._cp import (
    _CpFailure,
    _CpRequest,
    _freeze_transfer_proof,
    _render_failure,
    _require_directory,
    _require_source_file_size,
    _resolve_destination,
    _verify_transfer,
)
from ._diagnostics import _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection
    from collections.abc import Mapping as MappingType

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource

_OPERAND_COUNT = 2


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> tuple[tuple[_CpRequest, ...], bool]:
    operands: list[str] = []
    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
        elif options_active and argument.startswith("-") and argument != "-":
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        else:
            operands.append(argument)
    if len(operands) < _OPERAND_COUNT:
        _usage_error(command, "missing mapped filesystem operand")
    sources = tuple(
        _parse_mapped_operand(command, operand, known_names)
        for operand in operands[:-1]
    )
    destination = _parse_mapped_operand(command, operands[-1], known_names)
    if any(source.name != destination.name for source in sources):
        _usage_error(command, "cross-source move unsupported")
    return (
        tuple(_CpRequest(source, destination) for source in sources),
        len(sources) > 1,
    )


async def _confirmed_mv_file(  # noqa: PLR0911
    request: _CpRequest, filesystem: AsyncFileSystem
) -> _CpFailure | None:
    try:
        source_info = await filesystem._info(request.source.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001
        return _CpFailure(request.source, backend_error=error)
    expected_size, source_failure = _require_source_file_size(
        request.source,
        source_info,
    )
    if source_failure is not None:
        return source_failure
    if expected_size is None:
        return _CpFailure(request.source, incompatible="result")
    proof = _freeze_transfer_proof(source_info, expected_size)
    resolved, failure = await _resolve_destination(
        request.destination, request.source.path, filesystem
    )
    if failure is not None:
        return failure
    if request.source.path == resolved:
        return None
    declared_operation = type(filesystem).__dict__.get("_mv")
    if not inspect.iscoroutinefunction(declared_operation):
        return _CpFailure(
            request.destination,
            backend_error=NotImplementedError("_mv must be configured by source form"),
        )

    try:
        await declared_operation(filesystem, request.source.path, resolved)
    except Exception as operation_error:  # noqa: BLE001
        return _CpFailure(
            request.destination,
            backend_error=operation_error,
            uncertain=True,
            residue=True,
        )

    return await _verify_transfer(
        filesystem,
        filesystem,
        request.source.path,
        resolved,
        proof,
        request.destination,
        require_source_absent=True,
    )


async def _run_mv(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: MappingType[str, AsyncFilesystemSource],
) -> None:
    requests, requires_directory = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure: _CpFailure | None = None
    try:
        filesystems = await invocation.acquire((requests[0].source.name,))
        if filesystems is not None:
            filesystem = filesystems[requests[0].source.name]
            if requires_directory:
                failure = await _require_directory(requests[0].destination, filesystem)
            if failure is None:
                for request in requests:
                    failure = await _confirmed_mv_file(request, filesystem)
                    if failure is not None:
                        break
            if failure is not None:
                _render_failure(command, failure)
            succeeded = failure is None
    finally:
        command_error = failure.backend_error if failure is not None else None
        cleanup_failed = await invocation.close_with_command_error(command_error)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)
