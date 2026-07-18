"""Raw Typer parsing and async execution for ``ls``."""

from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

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
from ._listing import ListingRow, render_listing, to_listing
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _LsRequest:
    include_almost_all: bool
    long_listing: bool
    human_readable: bool
    operands: tuple[_MappedOperand, ...]


@dataclass(frozen=True)
class _FileResult:
    operand: _MappedOperand
    row: ListingRow | None = None


@dataclass(frozen=True)
class _DirectoryResult:
    operand: _MappedOperand
    children: tuple[str, ...] | None = None
    rows: tuple[ListingRow, ...] | None = None


def _long_requested(
    raw_arguments: tuple[str, ...],
    *,
    long_by_default: bool,
) -> bool:
    if long_by_default:
        return True

    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if not options_active or not argument.startswith("-") or argument == "-":
            continue
        characters = argument[1:]
        if characters and set(characters) <= {"A", "h", "l"} and "l" in characters:
            return True
    return False


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
    *,
    long_by_default: bool = False,
) -> _LsRequest:
    include_almost_all = False
    long_listing = _long_requested(
        raw_arguments,
        long_by_default=long_by_default,
    )
    human_readable = False
    operands = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            characters = argument[1:]
            if not characters or not set(characters) <= {"A", "h", "l"}:
                rendered = _render_diagnostic_value(argument)
                _usage_error(command, f"{rendered}: unsupported option")
            if "h" in characters and not long_listing:
                rendered = _render_diagnostic_value(argument)
                _usage_error(command, f"{rendered}: unsupported option")
            include_almost_all = include_almost_all or "A" in characters
            human_readable = human_readable or "h" in characters
            continue

        operands.append(_parse_mapped_operand(command, argument, known_names))

    if not operands:
        _usage_error(command, "missing mapped filesystem operand")

    return _LsRequest(
        include_almost_all=include_almost_all,
        long_listing=long_listing,
        human_readable=human_readable,
        operands=tuple(operands),
    )


async def _run_ls(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
    *,
    long_by_default: bool = False,
) -> None:
    request = _preflight(
        command,
        raw_arguments,
        sources,
        long_by_default=long_by_default,
    )
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
                request=request,
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
            long_listing=request.long_listing,
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
    long_listing: bool,
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
        return _read_file_operand(
            operand,
            cast("Mapping[str, object]", info),
            long_listing=long_listing,
        )
    if result_type == "directory":
        return await _read_directory_operand(
            operand,
            filesystem,
            include_almost_all=include_almost_all,
            long_listing=long_listing,
        )
    return _Failure(operand)


def _read_file_operand(
    operand: _MappedOperand,
    info: Mapping[str, object],
    *,
    long_listing: bool,
) -> _FileResult | _Failure:
    if not long_listing:
        return _FileResult(operand)
    try:
        row = to_listing(info)
    except (TypeError, ValueError):
        return _Failure(operand)
    if not row.name or "\0" in row.name or "\n" in row.name:
        return _Failure(operand)
    return _FileResult(operand, row)


async def _read_directory_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    *,
    include_almost_all: bool,
    long_listing: bool,
) -> _DirectoryResult | _Failure:
    try:
        listing = await filesystem._ls(  # noqa: SLF001
            operand.path,
            detail=long_listing,
        )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(operand, backend_error=error)
    if long_listing:
        rows = _directory_rows(
            operand.path,
            listing,
            include_almost_all=include_almost_all,
        )
        return (
            _Failure(operand) if rows is None else _DirectoryResult(operand, rows=rows)
        )

    lines = _directory_lines(
        operand.path,
        listing,
        include_almost_all=include_almost_all,
    )
    return (
        _Failure(operand)
        if lines is None
        else _DirectoryResult(operand, children=lines)
    )


def _sort_key(result: _FileResult | _DirectoryResult) -> tuple[str, str]:
    spelling = result.operand.spelling
    return locale.strxfrm(spelling), spelling


def _format_successes(
    successes: tuple[_FileResult | _DirectoryResult, ...],
    *,
    request: _LsRequest,
    multiple_operands: bool,
) -> str:
    if request.long_listing:
        return _format_long_successes(
            successes,
            human_readable=request.human_readable,
            multiple_operands=multiple_operands,
        )

    if not multiple_operands:
        if not successes:
            return ""
        result = successes[0]
        lines = (
            (result.operand.spelling,)
            if isinstance(result, _FileResult)
            else result.children or ()
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
        (f"{result.operand.spelling}:", *(result.children or ()))
        for result in directories
    )
    return "\n\n".join("\n".join(block) for block in blocks) + "\n" if blocks else ""


def _format_long_successes(
    successes: tuple[_FileResult | _DirectoryResult, ...],
    *,
    human_readable: bool,
    multiple_operands: bool,
) -> str:
    if not successes:
        return ""
    if not multiple_operands:
        result = successes[0]
        rows = (result.row,) if isinstance(result, _FileResult) else result.rows or ()
        return render_listing(
            tuple(row for row in rows if row is not None),
            human_readable=human_readable,
        )

    files = sorted(
        (result for result in successes if isinstance(result, _FileResult)),
        key=_sort_key,
    )
    directories = sorted(
        (result for result in successes if isinstance(result, _DirectoryResult)),
        key=_sort_key,
    )
    blocks = []
    file_rows = tuple(result.row for result in files if result.row is not None)
    if file_rows:
        blocks.append(
            render_listing(file_rows, human_readable=human_readable).removesuffix("\n")
        )
    for result in directories:
        rendered = render_listing(
            result.rows or (),
            human_readable=human_readable,
        ).removesuffix("\n")
        header = f"{result.operand.spelling}:"
        blocks.append(f"{header}\n{rendered}" if rendered else header)
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _directory_basename(path: str, name: object) -> str | None:
    if not isinstance(name, str):
        return None

    comparison_path = path.rstrip("/")
    prefix = "/" if not comparison_path else f"{comparison_path}/"
    if not name.startswith(prefix):
        return None
    basename = name[len(prefix) :]
    if not basename or "/" in basename or "\0" in basename or "\n" in basename:
        return None
    return basename


def _directory_lines(
    path: str,
    listing: object,
    *,
    include_almost_all: bool,
) -> tuple[str, ...] | None:
    if not isinstance(listing, list):
        return None

    basenames = []
    for child in listing:
        basename = _directory_basename(path, child)
        if basename is None:
            return None
        basenames.append(basename)

    if include_almost_all:
        selected = (name for name in basenames if name not in {".", ".."})
    else:
        selected = (name for name in basenames if not name.startswith("."))
    return tuple(sorted(selected, key=lambda name: (locale.strxfrm(name), name)))


def _directory_rows(
    path: str,
    listing: object,
    *,
    include_almost_all: bool,
) -> tuple[ListingRow, ...] | None:
    if not isinstance(listing, list):
        return None

    entries = []
    for info in listing:
        if not isinstance(info, Mapping):
            return None
        basename = _directory_basename(path, info.get("name"))
        if basename is None:
            return None
        try:
            row = to_listing(cast("Mapping[str, object]", info))
        except (TypeError, ValueError):
            return None
        entries.append((basename, row))

    if include_almost_all:
        selected = (entry for entry in entries if entry[0] not in {".", ".."})
    else:
        selected = (entry for entry in entries if not entry[0].startswith("."))
    return tuple(
        row
        for _basename, row in sorted(
            selected,
            key=lambda entry: (locale.strxfrm(entry[0]), entry[0]),
        )
    )
