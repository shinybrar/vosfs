"""Raw Typer parsing and async execution for ``ls``."""

from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar, cast

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


_PayloadT = TypeVar("_PayloadT")


@dataclass(frozen=True)
class _FileResult(Generic[_PayloadT]):
    operand: _MappedOperand
    value: _PayloadT


@dataclass(frozen=True)
class _DirectoryResult(Generic[_PayloadT]):
    operand: _MappedOperand
    values: tuple[_PayloadT, ...]


_PlainFileResult: TypeAlias = _FileResult[str]
_PlainDirectoryResult: TypeAlias = _DirectoryResult[str]
_PlainResult: TypeAlias = _PlainFileResult | _PlainDirectoryResult
_LongFileResult: TypeAlias = _FileResult[ListingRow]
_LongDirectoryResult: TypeAlias = _DirectoryResult[ListingRow]
_LongResult: TypeAlias = _LongFileResult | _LongDirectoryResult


def _short_options(argument: str) -> str | None:
    characters = argument[1:]
    if not characters or not set(characters) <= {"A", "h", "l"}:
        return None
    return characters


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
        options = _short_options(argument)
        if options is not None and "l" in options:
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
            options = _short_options(argument)
            if options is None:
                rendered = _render_diagnostic_value(argument)
                _usage_error(command, f"{rendered}: unsupported option")
            if "h" in options and not long_listing:
                rendered = _render_diagnostic_value(argument)
                _usage_error(command, f"{rendered}: unsupported option")
            include_almost_all = include_almost_all or "A" in options
            human_readable = human_readable or "h" in options
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
            if request.long_listing:
                long_successes, failures = await _trace_long_operands(
                    request,
                    filesystems,
                )
                output = _format_long_successes(
                    long_successes,
                    human_readable=request.human_readable,
                    multiple_operands=len(request.operands) > 1,
                )
            else:
                plain_successes, failures = await _trace_plain_operands(
                    request,
                    filesystems,
                )
                output = _format_plain_successes(
                    plain_successes,
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


async def _trace_plain_operands(
    request: _LsRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> tuple[tuple[_PlainResult, ...], tuple[_Failure, ...]]:
    successes: list[_PlainResult] = []
    failures = []
    for operand in request.operands:
        result = await _read_plain_operand(
            operand,
            filesystems[operand.name],
            include_almost_all=request.include_almost_all,
        )
        if isinstance(result, _Failure):
            failures.append(result)
        else:
            successes.append(result)
    return tuple(successes), tuple(failures)


async def _trace_long_operands(
    request: _LsRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> tuple[tuple[_LongResult, ...], tuple[_Failure, ...]]:
    successes: list[_LongResult] = []
    failures = []
    for operand in request.operands:
        result = await _read_long_operand(
            operand,
            filesystems[operand.name],
            include_almost_all=request.include_almost_all,
        )
        if isinstance(result, _Failure):
            failures.append(result)
        else:
            successes.append(result)
    return tuple(successes), tuple(failures)


async def _classify_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> Mapping[str, object] | _Failure:
    # fsspec's native async API intentionally exposes underscore coroutines.
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(operand, backend_error=error)

    if not isinstance(info, Mapping) or info.get("type") not in {"file", "directory"}:
        return _Failure(operand)
    return cast("Mapping[str, object]", info)


async def _read_plain_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    *,
    include_almost_all: bool,
) -> _PlainResult | _Failure:
    info = await _classify_operand(operand, filesystem)
    if isinstance(info, _Failure):
        return info
    if info["type"] == "file":
        return _FileResult(operand=operand, value=operand.spelling)

    listing = await _list_directory(operand, filesystem, detail=False)
    if isinstance(listing, _Failure):
        return listing
    children = _directory_lines(
        operand.path,
        listing,
        include_almost_all=include_almost_all,
    )
    if children is None:
        return _Failure(operand)
    return _DirectoryResult(operand=operand, values=children)


async def _read_long_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    *,
    include_almost_all: bool,
) -> _LongResult | _Failure:
    info = await _classify_operand(operand, filesystem)
    if isinstance(info, _Failure):
        return info
    if info["type"] == "file":
        row = _listing_row(info)
        if row is None or not row.name or "\0" in row.name or "\n" in row.name:
            return _Failure(operand)
        return _FileResult(operand=operand, value=row)

    listing = await _list_directory(operand, filesystem, detail=True)
    if isinstance(listing, _Failure):
        return listing
    rows = _directory_rows(
        operand.path,
        listing,
        include_almost_all=include_almost_all,
    )
    if rows is None:
        return _Failure(operand)
    return _DirectoryResult(operand=operand, values=rows)


def _listing_row(info: Mapping[str, object]) -> ListingRow | None:
    try:
        return to_listing(info)
    except (TypeError, ValueError):
        return None


async def _list_directory(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    *,
    detail: bool,
) -> object | _Failure:
    try:
        return await filesystem._ls(  # noqa: SLF001
            operand.path,
            detail=detail,
        )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(operand, backend_error=error)


def _sort_key(
    result: _FileResult[_PayloadT] | _DirectoryResult[_PayloadT],
) -> tuple[str, str]:
    spelling = result.operand.spelling
    return locale.strxfrm(spelling), spelling


def _format_plain_successes(
    successes: tuple[_PlainResult, ...],
    *,
    multiple_operands: bool,
) -> str:
    if not multiple_operands:
        if not successes:
            return ""
        result = successes[0]
        if isinstance(result, _FileResult):
            lines = (cast("_PlainFileResult", result).value,)
        else:
            lines = cast("_PlainDirectoryResult", result).values
        return "\n".join(lines) + "\n" if lines else ""

    files = sorted(
        (
            cast("_PlainFileResult", result)
            for result in successes
            if isinstance(result, _FileResult)
        ),
        key=_sort_key,
    )
    directories = sorted(
        (
            cast("_PlainDirectoryResult", result)
            for result in successes
            if isinstance(result, _DirectoryResult)
        ),
        key=_sort_key,
    )
    blocks: list[str] = []
    if files:
        blocks.append("\n".join(result.value for result in files))
    for result in directories:
        header = f"{result.operand.spelling}:"
        children = "\n".join(result.values)
        blocks.append(f"{header}\n{children}" if children else header)
    return _join_blocks(blocks)


def _format_long_successes(
    successes: tuple[_LongResult, ...],
    *,
    human_readable: bool,
    multiple_operands: bool,
) -> str:
    if not successes:
        return ""
    if not multiple_operands:
        result = successes[0]
        if isinstance(result, _FileResult):
            rows = (cast("_LongFileResult", result).value,)
        else:
            rows = cast("_LongDirectoryResult", result).values
        return render_listing(
            rows,
            human_readable=human_readable,
        )

    files = sorted(
        (
            cast("_LongFileResult", result)
            for result in successes
            if isinstance(result, _FileResult)
        ),
        key=_sort_key,
    )
    directories = sorted(
        (
            cast("_LongDirectoryResult", result)
            for result in successes
            if isinstance(result, _DirectoryResult)
        ),
        key=_sort_key,
    )
    blocks = []
    file_rows = tuple(result.value for result in files)
    if file_rows:
        blocks.append(
            render_listing(file_rows, human_readable=human_readable).removesuffix("\n")
        )
    for result in directories:
        rendered = render_listing(
            result.values,
            human_readable=human_readable,
        ).removesuffix("\n")
        header = f"{result.operand.spelling}:"
        blocks.append(f"{header}\n{rendered}" if rendered else header)
    return _join_blocks(blocks)


def _join_blocks(blocks: list[str]) -> str:
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

    entries: list[tuple[str, Mapping[str, object]]] = []
    for info in listing:
        if not isinstance(info, Mapping):
            return None
        typed_info = cast("Mapping[str, object]", info)
        basename = _directory_basename(path, typed_info.get("name"))
        if basename is None:
            return None
        entries.append((basename, typed_info))

    if include_almost_all:
        selected = (entry for entry in entries if entry[0] not in {".", ".."})
    else:
        selected = (entry for entry in entries if not entry[0].startswith("."))
    sorted_entries = sorted(
        selected,
        key=lambda entry: (locale.strxfrm(entry[0]), entry[0]),
    )
    rows = []
    for _basename, info in sorted_entries:
        row = _listing_row(info)
        if row is None:
            return None
        rows.append(row)
    return tuple(rows)
