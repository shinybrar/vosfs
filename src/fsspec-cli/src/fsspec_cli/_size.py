"""Raw Typer parsing and async execution for ``size``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

import typer

from ._command import (
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _RawCommand,
    _render_failure,
    _render_output_failure,
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


@dataclass(frozen=True)
class _SizeRequest:
    operands: tuple[_MappedOperand, ...]


class _SizeCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("size", "[--] name:/path...")


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _SizeRequest:
    operands: list[_MappedOperand] = []
    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        operands.append(_parse_mapped_operand(command, argument, known_names))
    if not operands:
        _usage_error(command, "missing mapped filesystem operand")
    return _SizeRequest(tuple(operands))


def _valid_size(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def _group_operands(
    operands: tuple[_MappedOperand, ...],
) -> dict[str, list[tuple[int, _MappedOperand]]]:
    groups: dict[str, list[tuple[int, _MappedOperand]]] = {}
    for index, operand in enumerate(operands):
        groups.setdefault(operand.name, []).append((index, operand))
    return groups


async def _measure(
    request: _SizeRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> str | _Failure:
    if len(request.operands) == 1:
        return await _measure_one(request.operands[0], filesystems)
    return await _measure_many(request.operands, filesystems)


async def _measure_one(
    operand: _MappedOperand,
    filesystems: Mapping[str, AsyncFileSystem],
) -> str | _Failure:
    try:
        result = await filesystems[operand.name]._size(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - awaited backend boundary.
        return _Failure(operand, backend_error=error)
    if not _valid_size(result):
        return _Failure(operand)
    return f"{result}\t{operand.spelling}\n"


async def _measure_many(
    operands: tuple[_MappedOperand, ...],
    filesystems: Mapping[str, AsyncFileSystem],
) -> str | _Failure:
    sizes: dict[int, int] = {}
    for name, group in _group_operands(operands).items():
        paths = [operand.path for _, operand in group]
        try:
            result = await filesystems[name]._sizes(paths)  # noqa: SLF001
        except Exception as error:  # noqa: BLE001 - awaited backend boundary.
            return _Failure(group[0][1], backend_error=error)
        if type(result) is not list or len(result) != len(group):
            return _Failure(group[0][1])
        for (index, operand), size in zip(group, result, strict=True):
            if not _valid_size(size):
                return _Failure(operand)
            sizes[index] = size

    return "".join(
        f"{sizes[index]}\t{operand.spelling}\n"
        for index, operand in enumerate(operands)
    )


async def _run_size(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    groups = _group_operands(request.operands)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure: _Failure | None = None
    output_error: Exception | None = None
    try:
        filesystems = await invocation.acquire(groups)
        if filesystems is not None:
            result = await _measure(request, filesystems)
            if isinstance(result, _Failure):
                failure = result
                _render_failure(command, result)
            else:
                try:
                    typer.echo(result, nl=False, color=True)
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
        raise typer.Exit(1)
