"""Raw Typer parsing and async execution for ``rm`` profiles."""

from __future__ import annotations

import locale
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer

from ._command import (
    _binary_stdout,
    _MappedOperand,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value
from ._ls import _render_output_failure
from ._rmdir import _remove_empty_directory, _RmdirFailure
from ._rmdir import _render_failure as _render_rmdir_failure
from ._sources import _SourceInvocation
from ._unlink import _confirmed_rm_file, _render_failure, _UnlinkFailure

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _RmRequest:
    force: bool
    directory: bool
    verbose: bool
    operands: tuple[_MappedOperand, ...]


def _is_rejected_path(path: str) -> bool:
    normalized = path.rstrip("/")
    if not normalized:
        return True
    final = normalized.rsplit("/", 1)[-1]
    return final in {".", ".."}


def _preflight(  # noqa: C901, PLR0912 - locked option and operand diagnostics.
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _RmRequest:
    force = False
    directory = False
    verbose = False
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
            if argument == "-d" and not force and not directory and not verbose:
                directory = True
                continue
            if argument == "-v" and not force and not directory and not verbose:
                verbose = True
                continue
            if all(character == "f" for character in argument[1:]):
                if directory or verbose:
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

    return _RmRequest(
        force=force,
        directory=directory,
        verbose=verbose,
        operands=tuple(operands),
    )


def _write_verbose_line(spelling: str) -> None:
    chunk = f"{spelling}\n".encode()
    stdout = _binary_stdout()
    written = stdout.write(chunk)
    if written != len(chunk):
        message = "short write"
        raise OSError(message)
    stdout.flush()


def _render_rm_failure(
    command: str,
    failure: _UnlinkFailure | _RmdirFailure,
) -> None:
    if isinstance(failure, _RmdirFailure):
        _render_rmdir_failure(command, failure)
    else:
        _render_failure(command, failure)


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
    command: str,
    request: _RmRequest,
    filesystems: Mapping[str, AsyncFileSystem],
    failures: list[_UnlinkFailure | _RmdirFailure],
) -> Exception | None:
    for operand in request.operands:
        filesystem = filesystems[operand.name]
        if request.directory:
            result = await _remove_directory_entry(operand, filesystem)
        else:
            result = await _confirmed_rm_file(operand, filesystem)
        force_missing = (
            request.force
            and isinstance(result, _UnlinkFailure)
            and not result.uncertain
            and isinstance(result.backend_error, FileNotFoundError)
        )
        if (isinstance(result, _UnlinkFailure) and not force_missing) or isinstance(
            result, _RmdirFailure
        ):
            failures.append(result)
            if request.verbose:
                _render_rm_failure(command, result)
            continue
        if request.verbose and result is None:
            try:
                _write_verbose_line(operand.spelling)
            except BrokenPipeError as error:
                return error
            except Exception as error:  # noqa: BLE001 - stdout boundary.
                _render_output_failure(command, error)
                return error
    return None


async def _run_rm(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failures: list[_UnlinkFailure | _RmdirFailure] = []
    output_error: Exception | None = None
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            output_error = await _trace_operands(
                command, request, filesystems, failures
            )
            if not request.verbose:
                for failure in failures:
                    _render_rm_failure(command, failure)
            succeeded = not failures and output_error is None
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
        command_error = backend_error if backend_error is not None else output_error
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
