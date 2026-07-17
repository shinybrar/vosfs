"""Raw Typer parsing and async execution for file-only ``rm`` and its force profile."""

from __future__ import annotations

import locale
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_value
from ._ls import (
    _RAW_ARGUMENTS,
    _MappedOperand,
    _shield_help_values,
    _usage_error,
)
from ._rmdir import _remove_empty_directory, _RmdirFailure
from ._rmdir import _render_failure as _render_rmdir_failure
from ._sources import _SourceInvocation
from ._unlink import _confirmed_rm_file, _render_failure, _UnlinkFailure

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _RmRequest:
    force: bool
    directory: bool
    operands: tuple[_MappedOperand, ...]


class _RmCommand(TyperCommand):
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


def _preflight(  # noqa: C901 - locked option and operand diagnostics.
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _RmRequest:
    force = False
    directory = False
    operands = []
    options_active = True
    seen_operand = False
    after_double_dash = False

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            after_double_dash = True
            continue
        is_option_like = argument.startswith("-") and argument != "-"
        if options_active and is_option_like:
            if argument == "-d" and not force and not directory:
                directory = True
                continue
            if all(character == "f" for character in argument[1:]):
                if directory:
                    rendered = _render_diagnostic_value(argument)
                    _usage_error(command, f"{rendered}: unsupported option")
                force = True
                continue
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        if seen_operand and is_option_like and not after_double_dash:
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
        seen_operand = True
        if options_active:
            options_active = False

    if not operands and not force:
        _usage_error(command, "missing mapped filesystem operand")

    return _RmRequest(force=force, directory=directory, operands=tuple(operands))


async def _remove_directory_entry(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> _UnlinkFailure | _RmdirFailure | None:
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _UnlinkFailure(operand, backend_error=error)

    if not isinstance(info, Mapping) or not isinstance(info.get("type"), str):
        return _UnlinkFailure(operand, incompatible="result")
    if info["type"] == "file":
        return await _confirmed_rm_file(operand, filesystem)
    if info["type"] == "directory":
        return await _remove_empty_directory(operand, filesystem)
    return _UnlinkFailure(operand, incompatible="result")


async def _trace_operands(
    request: _RmRequest,
    filesystems: Mapping[str, AsyncFileSystem],
) -> tuple[_UnlinkFailure | _RmdirFailure, ...]:
    failures = []
    for operand in request.operands:
        filesystem = filesystems[operand.name]
        if request.directory:
            result = await _remove_directory_entry(operand, filesystem)
        else:
            result = await _confirmed_rm_file(operand, filesystem)
        if (
            isinstance(result, _UnlinkFailure)
            and not (
                request.force
                and not result.uncertain
                and isinstance(result.backend_error, FileNotFoundError)
            )
        ) or isinstance(result, _RmdirFailure):
            failures.append(result)
    return tuple(failures)


async def _run_rm(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failures: tuple[_UnlinkFailure | _RmdirFailure, ...] = ()
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            failures = await _trace_operands(request, filesystems)
            for failure in failures:
                if isinstance(failure, _RmdirFailure):
                    _render_rmdir_failure(command, failure)
                else:
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
