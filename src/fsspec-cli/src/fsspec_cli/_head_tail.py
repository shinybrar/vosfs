"""Raw parsing and bounded async reads for ``head`` and ``tail``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

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
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource

_ASCII_ZERO = ord("0")


@dataclass(frozen=True)
class _ByteRangeRequest:
    count: int
    operand: _MappedOperand


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
    # The grammar has no digit ceiling, unlike Python's string-to-int guard.
    value = 0
    for digit in argument:
        value = (value * 10) + ord(digit) - _ASCII_ZERO
    return value


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
            if index == len(raw_arguments):
                _usage_error(command, "-c: option requires an argument")
            count = _parse_count(command, raw_arguments[index])
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


async def _read_tail(
    request: _ByteRangeRequest,
    filesystem: AsyncFileSystem,
) -> bytes | _Failure:
    try:
        info = await filesystem._info(request.operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - awaited backend boundary.
        return _Failure(request.operand, backend_error=error)
    if not isinstance(info, Mapping):
        return _Failure(request.operand)
    size = info.get("size")
    if not _valid_size(size):
        return _Failure(request.operand)
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
            if command == "head":
                result = await _read_head(request, filesystem)
            else:
                result = await _read_tail(request, filesystem)
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
    await _run_byte_range(command, raw_arguments, sources)


async def _run_tail(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_byte_range(command, raw_arguments, sources)
