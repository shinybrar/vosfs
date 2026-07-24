"""Raw Typer parsing and async execution for ``find``."""

from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeGuard

from ._command import (
    _drain_current_operation,
    _Failure,
    _MappedOperand,
    _run_single_operand_text,
)

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _FindRequest:
    maxdepth: int | None
    kind: Literal["f", "d"]
    operand: _MappedOperand


def _render_result(request: _FindRequest, result: object) -> str | _Failure:
    paths = _directory_paths(result) if request.kind == "d" else _file_paths(result)
    if paths is None:
        return _Failure(request.operand)
    if request.maxdepth == 0:
        root = request.operand.path.rstrip("/")
        paths = [path for path in paths if path.rstrip("/") == root]
    paths.sort(key=lambda path: (locale.strxfrm(path), path))
    return "".join(f"{path}\n" for path in paths)


def _file_paths(result: object) -> list[str] | None:
    if type(result) is not list:
        return None
    paths: list[str] = []
    for path in result:
        if not _valid_path(path):
            return None
        paths.append(path)
    return paths


def _directory_paths(result: object) -> list[str] | None:
    if not isinstance(result, Mapping):
        return None
    paths: list[str] = []
    try:
        for path, info in result.items():
            if not _valid_path(path) or not isinstance(info, Mapping):
                return None
            kind = info.get("type")
            if type(kind) is not str:
                return None
            if kind == "directory":
                paths.append(path)
    except Exception:  # noqa: BLE001 - fail closed on hostile mapping consumption.
        return None
    return paths


def _valid_path(path: object) -> TypeGuard[str]:
    return type(path) is str and "\0" not in path and "\n" not in path


async def _search(
    request: _FindRequest,
    filesystem: AsyncFileSystem,
) -> str | _Failure:
    try:
        result = await _drain_current_operation(
            filesystem._find(  # noqa: SLF001
                request.operand.path,
                maxdepth=1 if request.maxdepth == 0 else request.maxdepth,
                withdirs=request.kind == "d",
                detail=request.kind == "d",
            )
        )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(request.operand, backend_error=error)
    return _render_result(request, result)


async def _run_find(
    command: str,
    request: _FindRequest,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_single_operand_text(
        command,
        request.operand,
        sources,
        lambda filesystem: _search(request, filesystem),
    )
