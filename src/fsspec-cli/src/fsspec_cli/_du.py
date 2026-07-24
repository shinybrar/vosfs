"""Raw Typer parsing and async execution for ``du``."""

from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from ._command import (
    _drain_current_operation,
    _Failure,
    _MappedOperand,
    _run_single_operand_text,
)
from ._listing import format_size

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _DuRequest:
    summarize: bool
    human_readable: bool
    operand: _MappedOperand


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
        result = await _drain_current_operation(
            filesystem._du(  # noqa: SLF001
                request.operand.path,
                total=request.summarize,
            )
        )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(request.operand, backend_error=error)
    return _render_result(request, result)


async def _run_du(
    command: str,
    request: _DuRequest,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_single_operand_text(
        command,
        request.operand,
        sources,
        lambda filesystem: _measure(request, filesystem),
    )
