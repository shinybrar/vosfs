"""Raw Typer parsing and async execution for ``find``."""

from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeGuard

from ._command import (
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _RawCommand,
    _run_single_operand_text,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _FindRequest:
    maxdepth: int | None
    kind: Literal["f", "d"]
    operand: _MappedOperand


class _FindCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("find", "[--maxdepth N] [--type f|d] [--] name:/path")


def _parse_maxdepth(command: str, value: str) -> int:
    if not value or any(character not in "0123456789" for character in value):
        rendered = _render_diagnostic_value(value)
        _usage_error(command, f"{rendered}: invalid --maxdepth value")
    try:
        return int(value)
    except ValueError:
        rendered = _render_diagnostic_value(value)
        return _usage_error(command, f"{rendered}: invalid --maxdepth value")


def _parse_kind(command: str, value: str) -> Literal["f", "d"]:
    if value == "f":
        return "f"
    if value == "d":
        return "d"
    rendered = _render_diagnostic_value(value)
    return _usage_error(command, f"{rendered}: invalid --type value")


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _FindRequest:
    maxdepth = None
    kind: Literal["f", "d"] = "f"
    operand = None
    options_active = True
    index = 0
    while index < len(raw_arguments):
        argument = raw_arguments[index]
        if options_active and argument == "--":
            options_active = False
            index += 1
            continue
        if options_active and argument in {"--maxdepth", "--type"}:
            index += 1
            if index == len(raw_arguments):
                _usage_error(command, f"{argument}: option requires an argument")
            value = raw_arguments[index]
            if argument == "--maxdepth":
                maxdepth = _parse_maxdepth(command, value)
            else:
                kind = _parse_kind(command, value)
            index += 1
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        if operand is not None:
            _usage_error(command, "extra operand")
        operand = _parse_mapped_operand(command, argument, known_names)
        index += 1
    if operand is None:
        _usage_error(command, "missing mapped filesystem operand")
    return _FindRequest(maxdepth, kind, operand)


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
        result = await filesystem._find(  # noqa: SLF001
            request.operand.path,
            maxdepth=1 if request.maxdepth == 0 else request.maxdepth,
            withdirs=request.kind == "d",
            detail=request.kind == "d",
        )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(request.operand, backend_error=error)
    return _render_result(request, result)


async def _run_find(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    await _run_single_operand_text(
        command,
        request.operand,
        sources,
        lambda filesystem: _search(request, filesystem),
    )
