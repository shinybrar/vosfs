"""Raw Typer parsing and async execution for ``ls``."""

from __future__ import annotations

import locale
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context

    from ._app import AsyncFilesystemSource

_RAW_ARGUMENTS = "fsspec_cli.raw_arguments"


@dataclass(frozen=True)
class _MappedOperand:
    spelling: str
    name: str
    path: str


@dataclass(frozen=True)
class _LsRequest:
    include_almost_all: bool
    operands: tuple[_MappedOperand, ...]


@dataclass(frozen=True)
class _Failure:
    operand: _MappedOperand
    backend_error: Exception | None = None


class _LsCommand(TyperCommand):
    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        ctx.meta[_RAW_ARGUMENTS] = tuple(args)
        return super().parse_args(ctx, _shield_help_values(args))


def _shield_help_values(arguments: list[str]) -> list[str]:
    """Keep malformed help tokens available to command preflight."""
    shielded = []
    options_active = True
    for argument in arguments:
        if argument == "--":
            options_active = False
        if options_active and argument.startswith("--help="):
            shielded.append("--fsspec-cli-unsupported-help-value")
        else:
            shielded.append(argument)
    return shielded


def _raw_arguments(ctx: typer.Context) -> tuple[str, ...]:
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _usage_error(diagnostic: str) -> NoReturn:
    typer.echo(diagnostic, err=True)
    raise typer.Exit(2)


def _preflight(
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
            _usage_error(f"ls: {rendered}: unsupported option")

        name, separator, path = argument.partition(":")
        if (
            not name
            or not separator
            or not path.startswith("/")
            or "\0" in argument
            or "\n" in argument
        ):
            rendered = _render_diagnostic_value(argument)
            _usage_error(f"ls: {rendered}: invalid mapped filesystem operand")

        if name not in known_names:
            known = sorted(
                known_names,
                key=lambda candidate: (locale.strxfrm(candidate), candidate),
            )
            rendered_operand = _render_diagnostic_value(argument)
            rendered_names = ", ".join(
                _render_diagnostic_value(candidate) for candidate in known
            )
            _usage_error(
                f"ls: {rendered_operand}: unknown filesystem (known: {rendered_names})"
            )

        operands.append(_MappedOperand(spelling=argument, name=name, path=path))

    if not operands:
        _usage_error("ls: missing mapped filesystem operand")

    return _LsRequest(
        include_almost_all=include_almost_all,
        operands=tuple(operands),
    )


async def _run_ls(
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(raw_arguments, sources)
    invocation = _SourceInvocation(sources)
    succeeded = False
    failure = None
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            failure = await _trace_operands(request, filesystems)
            if failure is None:
                succeeded = True
            else:
                _render_failure(failure)
    finally:
        active_exc_info = sys.exc_info()
        backend_error = failure.backend_error if failure is not None else None
        if backend_error is not None and (
            active_exc_info[1] is None or isinstance(active_exc_info[1], Exception)
        ):
            active_exc_info = (
                type(backend_error),
                backend_error,
                backend_error.__traceback__,
            )
        cleanup_failed = await invocation.close(active_exc_info)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)


async def _trace_operands(
    request: _LsRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> _Failure | None:
    for operand in request.operands:
        result = await _read_operand(
            operand,
            filesystems[operand.name],
            include_almost_all=request.include_almost_all,
        )
        if isinstance(result, _Failure):
            return result

        for line in result:
            typer.echo(line)
    return None


async def _read_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    *,
    include_almost_all: bool,
) -> tuple[str, ...] | _Failure:
    # fsspec's native async API intentionally exposes underscore coroutines.
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(operand, backend_error=error)

    if not isinstance(info, Mapping) or not isinstance(info.get("type"), str):
        return _Failure(operand)

    result_type = info["type"]
    if result_type == "file":
        return (operand.spelling,)
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
    return _Failure(operand) if lines is None else lines


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


def _render_operand_diagnostic(operand: _MappedOperand, category: str) -> None:
    rendered_operand = _render_diagnostic_value(operand.spelling)
    typer.echo(f"ls: {rendered_operand}: {category}", err=True)


def _render_failure(failure: _Failure) -> None:
    if failure.backend_error is None:
        _render_operand_diagnostic(failure.operand, "incompatible result")
    else:
        _render_backend_failure(failure.operand, failure.backend_error)


def _render_backend_failure(
    operand: _MappedOperand,
    error: Exception,
) -> None:
    if isinstance(error, FileNotFoundError):
        category = "not found"
    elif isinstance(error, PermissionError):
        category = "permission denied"
    elif isinstance(error, NotADirectoryError):
        category = "not a directory"
    elif isinstance(error, NotImplementedError):
        category = "unsupported operation"
    else:
        rendered_class = _render_diagnostic_value(type(error).__name__)
        rendered_message = _render_diagnostic_value(str(error))
        category = f"backend failure ({rendered_class}): {rendered_message}"
    _render_operand_diagnostic(operand, category)
