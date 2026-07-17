"""Raw Typer parsing and async execution for ``rmdir``."""

from __future__ import annotations

import errno
import locale
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._ls import (
    _RAW_ARGUMENTS,
    _MappedOperand,
    _render_backend_failure,
    _shield_help_values,
    _usage_error,
)
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _RmdirRequest:
    operands: tuple[_MappedOperand, ...]


@dataclass(frozen=True)
class _RmdirFailure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    incompatible: Literal["directory", "result"] | None = None


class _RmdirCommand(TyperCommand):
    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        ctx.meta[_RAW_ARGUMENTS] = tuple(args)
        return super().parse_args(ctx, _shield_help_values(args))


def _raw_arguments(ctx: typer.Context) -> tuple[str, ...]:
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _is_rejected_path(path: str) -> bool:
    normalized = path.rstrip("/")
    if not normalized:
        return True
    final = normalized.rsplit("/", 1)[-1]
    return final in {".", ".."}


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _RmdirRequest:
    operands = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")

        name, separator, path = argument.partition(":")
        if (
            not name
            or not separator
            or not path.startswith("/")
            or "\0" in argument
            or "\n" in argument
        ):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: invalid mapped filesystem operand")

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
                command,
                f"{rendered_operand}: unknown filesystem (known: {rendered_names})",
            )

        if _is_rejected_path(path):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: rejected path")

        operands.append(_MappedOperand(spelling=argument, name=name, path=path))

    if not operands:
        _usage_error(command, "missing mapped filesystem operand")

    return _RmdirRequest(operands=tuple(operands))


async def _remove_empty_directory(  # noqa: PLR0911 - explicit outcome branches.
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> _RmdirFailure | None:
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _RmdirFailure(operand, backend_error=error)

    if not isinstance(info, Mapping) or not isinstance(info.get("type"), str):
        return _RmdirFailure(operand, incompatible="result")

    result_type = info["type"]
    if result_type == "file":
        return _RmdirFailure(operand, incompatible="directory")
    if result_type != "directory":
        return _RmdirFailure(operand, incompatible="result")

    rmdir = getattr(filesystem, "_rmdir", None)
    if not callable(rmdir):
        return _RmdirFailure(
            operand,
            backend_error=NotImplementedError(
                f"{type(filesystem).__name__} lacks async _rmdir"
            ),
        )

    try:
        await rmdir(operand.path)
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _RmdirFailure(operand, backend_error=error)

    try:
        await filesystem._info(operand.path)  # noqa: SLF001
    except FileNotFoundError:
        return None
    except Exception as error:  # noqa: BLE001 - never hide non-not-found errors.
        return _RmdirFailure(operand, backend_error=error)
    else:
        return _RmdirFailure(operand, incompatible="result")


def _render_operand_diagnostic(
    command: str,
    operand: _MappedOperand,
    category: str,
) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered_operand = _render_diagnostic_value(operand.spelling)
    typer.echo(f"{prefix} {rendered_operand}: {category}", err=True, color=True)


def _render_rmdir_backend_failure(
    command: str,
    operand: _MappedOperand,
    error: Exception,
) -> None:
    if isinstance(error, OSError) and error.errno == errno.ENOTEMPTY:
        _render_operand_diagnostic(command, operand, "directory not empty")
        return
    _render_backend_failure(command, operand, error)


def _render_failure(command: str, failure: _RmdirFailure) -> None:
    if failure.incompatible == "directory":
        _render_operand_diagnostic(command, failure.operand, "not a directory")
    elif failure.incompatible == "result":
        _render_operand_diagnostic(command, failure.operand, "incompatible result")
    elif isinstance(failure.backend_error, NotADirectoryError):
        _render_operand_diagnostic(command, failure.operand, "not a directory")
    elif failure.backend_error is None:
        _render_operand_diagnostic(command, failure.operand, "incompatible result")
    else:
        _render_rmdir_backend_failure(command, failure.operand, failure.backend_error)


async def _trace_operands(
    request: _RmdirRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> tuple[_RmdirFailure, ...]:
    failures = []
    for operand in request.operands:
        result = await _remove_empty_directory(operand, filesystems[operand.name])
        if isinstance(result, _RmdirFailure):
            failures.append(result)
    return tuple(failures)


async def _run_rmdir(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failures: tuple[_RmdirFailure, ...] = ()
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            failures = await _trace_operands(request, filesystems)
            for failure in failures:
                _render_failure(command, failure)
            succeeded = not failures
    finally:
        active_exc_info = sys.exc_info()
        backend_error = next(
            (
                failure.backend_error
                for failure in failures
                if failure.backend_error is not None
            ),
            None,
        )
        command_error = backend_error
        if command_error is not None and (
            active_exc_info[1] is None or isinstance(active_exc_info[1], Exception)
        ):
            active_exc_info = (
                type(command_error),
                command_error,
                command_error.__traceback__,
            )
        cleanup_failed = await invocation.close(active_exc_info)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)
