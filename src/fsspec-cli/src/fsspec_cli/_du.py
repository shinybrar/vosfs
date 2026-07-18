"""Raw Typer parsing and async execution for ``du``."""

from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from ._command import (
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _RawCommand,
    _run_single_operand_text,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value
from ._listing import format_size

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _DuRequest:
    summarize: bool
    human_readable: bool
    operand: _MappedOperand


class _DuCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("du", "[-sh] [--] name:/path")


def _short_options(argument: str) -> str | None:
    characters = argument[1:]
    if not characters or not set(characters) <= {"h", "s"}:
        return None
    return characters


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _DuRequest:
    summarize = False
    human_readable = False
    operand = None
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-"):
            options = _short_options(argument)
            if options is None:
                rendered = _render_diagnostic_value(argument)
                _usage_error(command, f"{rendered}: unsupported option")
            summarize = summarize or "s" in options
            human_readable = human_readable or "h" in options
            continue
        if operand is not None:
            _usage_error(command, "extra operand")
        operand = _parse_mapped_operand(command, argument, known_names)

    if operand is None:
        _usage_error(command, "missing mapped filesystem operand")

    return _DuRequest(
        summarize=summarize,
        human_readable=human_readable,
        operand=operand,
    )


def _valid_size(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def _render_result(request: _DuRequest, result: object) -> str | _Failure:
    operand = request.operand
    if request.summarize:
        if not _valid_size(result):
            return _Failure(operand)
        size = format_size(result, human_readable=request.human_readable)
        return f"{size}\t{operand.path}\n"

    if not isinstance(result, Mapping):
        return _Failure(operand)

    try:
        entries: list[tuple[str, int]] = []
        for path, size in result.items():
            if (
                type(path) is not str
                or "\0" in path
                or "\n" in path
                or not _valid_size(size)
            ):
                return _Failure(operand)
            entries.append((path, size))

        entries.sort(key=lambda entry: (locale.strxfrm(entry[0]), entry[0]))
        return "".join(
            f"{format_size(size, human_readable=request.human_readable)}\t{path}\n"
            for path, size in entries
        )
    except Exception:  # noqa: BLE001 - fail closed on hostile mapping behavior.
        return _Failure(operand)


async def _measure(
    request: _DuRequest,
    filesystem: AsyncFileSystem,
) -> str | _Failure:
    try:
        result = await filesystem._du(  # noqa: SLF001
            request.operand.path,
            total=request.summarize,
        )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(request.operand, backend_error=error)
    return _render_result(request, result)


async def _run_du(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    await _run_single_operand_text(
        command,
        request.operand,
        sources,
        lambda filesystem: _measure(request, filesystem),
    )
