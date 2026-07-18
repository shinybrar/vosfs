"""Raw Typer parsing and async execution for ``ls``."""

from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer

from ._command import (
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _render_failure,
    _render_output_failure,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _LsRequest:
    include_almost_all: bool
    operands: tuple[_MappedOperand, ...]


@dataclass(frozen=True)
class _FileResult:
    operand: _MappedOperand


@dataclass(frozen=True)
class _DirectoryResult:
    operand: _MappedOperand
    children: tuple[str, ...]


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _LsRequest:
    include_almost_all = False
    operands = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            if all(character == "A" for character in argument[1:]):
                include_almost_all = True
                continue
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")

        operands.append(_parse_mapped_operand(command, argument, known_names))

    if not operands:
        _usage_error(command, "missing mapped filesystem operand")

    return _LsRequest(
        include_almost_all=include_almost_all,
        operands=tuple(operands),
    )


async def _run_ls(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failures: tuple[_Failure, ...] = ()
    output_error: Exception | None = None
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            successes, failures = await _trace_operands(request, filesystems)
            output = _format_successes(
                successes,
                multiple_operands=len(request.operands) > 1,
            )
            for failure in failures:
                _render_failure(command, failure)
            if output:
                try:
                    typer.echo(output, nl=False, color=True)
                except BrokenPipeError as error:
                    output_error = error
                except Exception as error:  # noqa: BLE001 - output boundary.
                    output_error = error
                    _render_output_failure(command, error)
            succeeded = not failures and output_error is None
    finally:
        backend_error = next(
            (
                failure.backend_error
                for failure in failures
                if failure.backend_error is not None
            ),
            None,
        )
        command_error = backend_error if backend_error is not None else output_error
        cleanup_failed = await invocation.close_with_command_error(command_error)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)


async def _trace_operands(
    request: _LsRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> tuple[
    tuple[_FileResult | _DirectoryResult, ...],
    tuple[_Failure, ...],
]:
    successes: list[_FileResult | _DirectoryResult] = []
    failures = []
    for operand in request.operands:
        result = await _read_operand(
            operand,
            filesystems[operand.name],
            include_almost_all=request.include_almost_all,
        )
        if isinstance(result, _Failure):
            failures.append(result)
        else:
            successes.append(result)
    return tuple(successes), tuple(failures)


async def _read_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    *,
    include_almost_all: bool,
) -> _FileResult | _DirectoryResult | _Failure:
    # fsspec's native async API intentionally exposes underscore coroutines.
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(operand, backend_error=error)

    if not isinstance(info, Mapping) or not isinstance(info.get("type"), str):
        return _Failure(operand)

    result_type = info["type"]
    if result_type == "file":
        return _FileResult(operand)
    if result_type != "directory":
        return _Failure(operand)

    try:
        listing = await filesystem._ls(  # noqa: SLF001
            operand.path,
            detail=False,
        )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(operand, backend_error=error)
    lines = _directory_lines(
        operand.path,
        listing,
        include_almost_all=include_almost_all,
    )
    return _Failure(operand) if lines is None else _DirectoryResult(operand, lines)


def _sort_key(result: _FileResult | _DirectoryResult) -> tuple[str, str]:
    spelling = result.operand.spelling
    return locale.strxfrm(spelling), spelling


def _format_successes(
    successes: tuple[_FileResult | _DirectoryResult, ...],
    *,
    multiple_operands: bool,
) -> str:
    if not multiple_operands:
        if not successes:
            return ""
        result = successes[0]
        lines = (
            (result.operand.spelling,)
            if isinstance(result, _FileResult)
            else result.children
        )
        return "\n".join(lines) + "\n" if lines else ""

    files = sorted(
        (result for result in successes if isinstance(result, _FileResult)),
        key=_sort_key,
    )
    directories = sorted(
        (result for result in successes if isinstance(result, _DirectoryResult)),
        key=_sort_key,
    )
    blocks: list[tuple[str, ...]] = []
    if files:
        blocks.append(tuple(result.operand.spelling for result in files))
    blocks.extend(
        (f"{result.operand.spelling}:", *result.children) for result in directories
    )
    return "\n\n".join("\n".join(block) for block in blocks) + "\n" if blocks else ""


def _directory_lines(
    path: str,
    listing: object,
    *,
    include_almost_all: bool,
) -> tuple[str, ...] | None:
    if not isinstance(listing, list):
        return None

    comparison_path = path.rstrip("/")
    prefix = "/" if not comparison_path else f"{comparison_path}/"
    basenames = []
    for child in listing:
        if not isinstance(child, str) or not child.startswith(prefix):
            return None
        basename = child[len(prefix) :]
        if not basename or "/" in basename or "\0" in basename or "\n" in basename:
            return None
        basenames.append(basename)

    if include_almost_all:
        selected = (name for name in basenames if name not in {".", ".."})
    else:
        selected = (name for name in basenames if not name.startswith("."))
    return tuple(sorted(selected, key=lambda name: (locale.strxfrm(name), name)))
