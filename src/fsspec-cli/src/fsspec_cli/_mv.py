"""Raw Typer parsing and async execution for same-source file ``mv``."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING

import typer

from ._command import _usage_error
from ._cp import (
    _CpFailure,
    _CpRequest,
    _files_match,
    _remove_temporary,
    _render_failure,
    _require_directory,
    _require_file_size,
    _resolve_destination,
    _stage_remote,
    _validate_mapped_operand,
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
        _validate_mapped_operand(command, operand, known_names)
        for operand in operands[:-1]
    )
    destination = _validate_mapped_operand(command, operands[-1], known_names)
    if any(source.name != destination.name for source in sources):
        _usage_error(command, "cross-source move unsupported")
    return (
        tuple(_CpRequest(source, destination) for source in sources),
        len(sources) > 1,
    )


async def _confirmed_mv_file(  # noqa: C901, PLR0911, PLR0912
    request: _CpRequest, filesystem: AsyncFileSystem
) -> _CpFailure | None:
    try:
        source_info = await filesystem._info(request.source.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001
        return _CpFailure(request.source, backend_error=error)
    expected_size = _require_file_size(source_info)
    if expected_size is None:
        return _CpFailure(
            request.source,
            incompatible="directory"
            if (
                isinstance(source_info, Mapping)
                and source_info.get("type") == "directory"
            )
            else "result",
        )
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

    source_temp, error = await _stage_remote(
        filesystem, request.source.path, "fsspec-cli-mv-src-"
    )
    if error is not None or source_temp is None:
        return _CpFailure(
            request.destination, backend_error=error, category="staging failure"
        )
    dest_temp: str | None = None
    try:
        try:
            await declared_operation(filesystem, request.source.path, resolved)
        except Exception as operation_error:  # noqa: BLE001
            return _CpFailure(
                request.destination,
                backend_error=operation_error,
                uncertain=True,
                residue=True,
            )
        try:
            destination_info = await filesystem._info(resolved)  # noqa: SLF001
            if _require_file_size(destination_info) != expected_size:
                message = "destination verification failed"
                raise ValueError(message)  # noqa: TRY301
            dest_temp, error = await _stage_remote(
                filesystem, resolved, "fsspec-cli-mv-dst-"
            )
            if error is not None or dest_temp is None:
                message = "destination staging failed"
                raise error or ValueError(message)  # noqa: TRY301
            matched, error = _files_match(source_temp, dest_temp)
            if error is not None or not matched:
                message = "destination verification failed"
                raise error or ValueError(message)  # noqa: TRY301
            try:
                await filesystem._info(request.source.path)  # noqa: SLF001
            except FileNotFoundError:
                return None
            message = "source remains after move"
            raise ValueError(message)  # noqa: TRY301
        except Exception as verify_error:  # noqa: BLE001
            return _CpFailure(
                request.destination,
                backend_error=verify_error,
                category="verification failure",
                residue=True,
            )
    finally:
        _remove_temporary(source_temp)
        if dest_temp is not None:
            _remove_temporary(dest_temp)


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
        active_exc_info = sys.exc_info()
        if (
            failure is not None
            and failure.backend_error is not None
            and (
                active_exc_info[1] is None or isinstance(active_exc_info[1], Exception)
            )
        ):
            error = failure.backend_error
            active_exc_info = (type(error), error, error.__traceback__)
        cleanup_failed = await invocation.close(active_exc_info)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)
