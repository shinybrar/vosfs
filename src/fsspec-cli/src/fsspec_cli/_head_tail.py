"""Raw parsing and bounded async reads for ``head`` and ``tail``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeGuard

import typer

from ._command import (
    _BROKEN_PIPE_EXIT_CODE,
    _binary_stdout,
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _RawCommand,
    _render_failure,
    _render_output_failure,
    _usage_error,
    _write_binary,
)
from ._diagnostics import _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Awaitable, Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _ByteRangeRequest:
    count: int
    operand: _MappedOperand


class _ReadOperation(Protocol):
    def __call__(
        self,
        request: _ByteRangeRequest,
        filesystem: AsyncFileSystem,
    ) -> Awaitable[bytes | _Failure]: ...


class _HeadCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("head", "-c N [--] name:/path")


class _TailCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("tail", "-c N [--] name:/path")


def _parse_count(command: str, argument: str) -> int:
    if not argument or not argument.isascii() or not argument.isdecimal():
        rendered = _render_diagnostic_value(argument)
        _usage_error(command, f"{rendered}: invalid byte count")
    try:
        return int(argument)
    except ValueError:
        rendered = _render_diagnostic_value(argument)
        _usage_error(command, f"{rendered}: invalid byte count")


def _selector_argument(
    command: str,
    raw_arguments: tuple[str, ...],
    index: int,
) -> str:
    if index == len(raw_arguments):
        _usage_error(command, "-c: option requires an argument")
    argument = raw_arguments[index]
    if argument == "-c":
        _usage_error(command, "exactly one byte-count selector is required")
    return argument


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _ByteRangeRequest:
    count: int | None = None
    operand = None
    options_active = True
    index = 0
    while index < len(raw_arguments):
        argument = raw_arguments[index]
        if options_active and argument == "--":
            options_active = False
            index += 1
            continue
        if options_active and argument == "-c":
            if count is not None:
                _usage_error(command, "exactly one byte-count selector is required")
            index += 1
            count = _parse_count(
                command,
                _selector_argument(command, raw_arguments, index),
            )
            index += 1
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        if operand is not None:
            _usage_error(command, "extra operand")
        operand = _parse_mapped_operand(command, argument, known_names)
        index += 1

    if count is None:
        _usage_error(command, "exactly one byte-count selector is required")
    if operand is None:
        _usage_error(command, "missing mapped filesystem operand")
    return _ByteRangeRequest(count, operand)


def _valid_size(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


async def _read_head(
    request: _ByteRangeRequest,
    filesystem: AsyncFileSystem,
) -> bytes | _Failure:
    try:
        result = await filesystem._cat_file(  # noqa: SLF001
            request.operand.path,
            start=0,
            end=request.count,
        )
    except Exception as error:  # noqa: BLE001 - awaited backend boundary.
        return _Failure(request.operand, backend_error=error)
    if type(result) is not bytes or len(result) > request.count:
        return _Failure(request.operand)
    return result


def _size_from_info(operand: _MappedOperand, info: object) -> int | _Failure:
    if not isinstance(info, Mapping):
        return _Failure(operand)
    try:
        size = info.get("size")
    except Exception:  # noqa: BLE001 - hostile metadata mapping boundary.
        return _Failure(operand)
    if not _valid_size(size):
        return _Failure(operand)
    return size


async def _read_tail(
    request: _ByteRangeRequest,
    filesystem: AsyncFileSystem,
) -> bytes | _Failure:
    try:
        info = await filesystem._info(request.operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - awaited backend boundary.
        return _Failure(request.operand, backend_error=error)
    size = _size_from_info(request.operand, info)
    if isinstance(size, _Failure):
        return size
    try:
        result = await filesystem._cat_file(  # noqa: SLF001
            request.operand.path,
            start=size - request.count,
            end=None,
        )
    except Exception as error:  # noqa: BLE001 - awaited backend boundary.
        return _Failure(request.operand, backend_error=error)
    if type(result) is not bytes or len(result) > request.count:
        return _Failure(request.operand)
    return result


def _emit(payload: bytes) -> None:
    if not payload:
        return
    stdout = _binary_stdout()
    _write_binary(stdout, payload)
    stdout.flush()


async def _run_byte_range(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
    operation: _ReadOperation,
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    failure: _Failure | None = None
    output_error: Exception | None = None
    succeeded = False
    try:
        filesystems = await invocation.acquire((request.operand.name,))
        if filesystems is not None:
            filesystem = filesystems[request.operand.name]
            result = await operation(request, filesystem)
            if isinstance(result, _Failure):
                failure = result
                _render_failure(command, result)
            else:
                try:
                    _emit(result)
                except BrokenPipeError as error:
                    output_error = error
                except Exception as error:  # noqa: BLE001 - output boundary.
                    output_error = error
                    _render_output_failure(command, error)
            succeeded = failure is None and output_error is None
    finally:
        command_error = failure.backend_error if failure is not None else output_error
        cleanup_failed = await invocation.close_with_command_error(command_error)
    if not succeeded or cleanup_failed:
        if (
            not cleanup_failed
            and failure is None
            and isinstance(output_error, BrokenPipeError)
        ):
            raise typer.Exit(_BROKEN_PIPE_EXIT_CODE)
        raise typer.Exit(1)


async def _run_head(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_byte_range(command, raw_arguments, sources, _read_head)


async def _run_tail(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_byte_range(command, raw_arguments, sources, _read_tail)
