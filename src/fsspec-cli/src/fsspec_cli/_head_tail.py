"""Typed ``head`` and ``tail`` runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from ._command import (
    _binary_stdout,
    _CommandFailureError,
    _MappedOperand,
    _run_mapped_command,
    _write_binary,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _ByteRangeRequest:
    count: int
    operand: _MappedOperand


def _valid_size(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


async def _read_head(
    request: _ByteRangeRequest,
    filesystem: AsyncFileSystem,
) -> bytes:
    try:
        result = await filesystem._cat_file(  # noqa: SLF001
            request.operand.path,
            start=0,
            end=request.count,
        )
    except Exception as error:
        raise _CommandFailureError(request.operand, error) from error
    if type(result) is not bytes or len(result) > request.count:
        raise _CommandFailureError(request.operand)
    return result


def _size_from_info(operand: _MappedOperand, info: object) -> int:
    if not isinstance(info, Mapping):
        raise _CommandFailureError(operand)
    try:
        size = info.get("size")
    except Exception:  # noqa: BLE001 - hostile metadata mapping boundary.
        raise _CommandFailureError(operand) from None
    if not _valid_size(size):
        raise _CommandFailureError(operand)
    return size


async def _read_tail(
    request: _ByteRangeRequest,
    filesystem: AsyncFileSystem,
) -> bytes:
    try:
        info = await filesystem._info(request.operand.path)  # noqa: SLF001
    except Exception as error:
        raise _CommandFailureError(request.operand, error) from error
    size = _size_from_info(request.operand, info)
    try:
        result = await filesystem._cat_file(  # noqa: SLF001
            request.operand.path,
            start=size - request.count,
            end=None,
        )
    except Exception as error:
        raise _CommandFailureError(request.operand, error) from error
    if type(result) is not bytes or len(result) > request.count:
        raise _CommandFailureError(request.operand)
    return result


def _emit(payload: bytes) -> None:
    if not payload:
        return
    stdout = _binary_stdout()
    _write_binary(stdout, payload)
    stdout.flush()


async def _run_byte_range(
    command: str,
    count: int,
    operand: _MappedOperand,
    sources: Mapping[str, AsyncFilesystemSource],
    operation: Callable[
        [_ByteRangeRequest, AsyncFileSystem],
        Awaitable[bytes],
    ],
) -> None:
    request = _ByteRangeRequest(count, operand)

    async def execute(filesystems: Mapping[str, AsyncFileSystem]) -> None:
        payload = await operation(request, filesystems[operand.name])
        try:
            _emit(payload)
        except Exception as error:
            raise _CommandFailureError(error=error) from error

    await _run_mapped_command(command, (operand,), sources, execute)


async def _run_head(
    command: str,
    count: int,
    operand: _MappedOperand,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_byte_range(command, count, operand, sources, _read_head)


async def _run_tail(
    command: str,
    count: int,
    operand: _MappedOperand,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_byte_range(command, count, operand, sources, _read_tail)
