"""Raw Typer parsing and async execution for ``rm`` profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer

from ._command import (
    _binary_stdout,
    _MappedOperand,
    _parse_mapped_operand,
    _render_output_failure,
    _usage_error,
    _write_binary,
)
from ._diagnostics import _render_diagnostic_value
from ._recursive_rm import (
    _RecursiveRmFailure,
    _remove_recursive,
    _render_recursive_failure,
)
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
    recursive: bool
    verbose: bool
    operands: tuple[_MappedOperand, ...]


def _is_rejected_path(path: str) -> bool:
    normalized = path.rstrip("/")
    if not normalized:
        return True
    final = normalized.rsplit("/", 1)[-1]
    return final in {".", ".."}


def _is_recursive_rejected_path(path: str) -> bool:
    normalized = path.rstrip("/")
    return not normalized or any(
        component in {".", ".."} for component in path.split("/")
    )


def _reject_disabled_recursive_rm(
    raw_arguments: tuple[str, ...],
    *,
    recursive_enabled: bool,
) -> None:
    if recursive_enabled:
        return
    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if not options_active or not argument.startswith("-") or argument == "-":
            break
        characters = argument[1:]
        if (
            characters
            and set(characters) <= {"R", "r", "f", "v"}
            and ("R" in characters or "r" in characters)
            and characters.count("v") <= 1
        ):
            typer.echo("rm: recursive removal disabled by application", err=True)
            raise typer.Exit(2)
        if characters and set(characters) <= {"f", "v"} and characters.count("v") <= 1:
            continue
        break


def _recursive_profile_requested(raw_arguments: tuple[str, ...]) -> bool:
    for argument in raw_arguments:
        if argument == "--" or not argument.startswith("-") or argument == "-":
            return False
        characters = argument[1:]
        if (
            characters
            and set(characters) <= {"R", "r", "f", "v"}
            and ("R" in characters or "r" in characters)
        ):
            return True
    return False


def _preflight(  # noqa: C901, PLR0912, PLR0915 - locked argv diagnostics.
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _RmRequest:
    force = False
    directory = False
    recursive = False
    verbose = False
    operands = []
    options_active = True
    seen_operand = False
    after_double_dash = False
    recursive_profile = _recursive_profile_requested(raw_arguments)

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            after_double_dash = True
            continue
        is_option_like = argument.startswith("-") and argument != "-"
        if options_active and is_option_like:
            characters = argument[1:]
            if (
                recursive_profile
                and characters
                and set(characters) <= {"R", "r", "f", "v"}
                and not directory
            ):
                if (verbose and "v" in characters) or characters.count("v") > 1:
                    rendered = _render_diagnostic_value(argument)
                    _usage_error(command, f"{rendered}: unsupported option")
                recursive = recursive or "R" in characters or "r" in characters
                force = force or "f" in characters
                verbose = verbose or "v" in characters
                continue
            if (
                argument == "-d"
                and not force
                and not directory
                and not recursive
                and not verbose
            ):
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

        operand = _parse_mapped_operand(command, argument, known_names)
        if (
            _is_recursive_rejected_path(operand.path)
            if recursive
            else _is_rejected_path(operand.path)
        ):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: rejected path")

        operands.append(operand)
        seen_operand = True
        if options_active:
            options_active = False

    if not operands and not force:
        _usage_error(command, "missing mapped filesystem operand")

    return _RmRequest(
        force=force,
        directory=directory,
        recursive=recursive,
        verbose=verbose,
        operands=tuple(operands),
    )


def _write_verbose_line(spelling: str) -> None:
    chunk = f"{spelling}\n".encode()
    stdout = _binary_stdout()
    _write_binary(stdout, chunk)
    stdout.flush()


def _render_rm_failure(
    command: str,
    failure: _UnlinkFailure | _RmdirFailure | _RecursiveRmFailure,
) -> None:
    if isinstance(failure, _RecursiveRmFailure):
        _render_recursive_failure(command, failure)
    elif isinstance(failure, _RmdirFailure):
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
    failures: list[_UnlinkFailure | _RmdirFailure | _RecursiveRmFailure],
) -> Exception | None:
    for operand in request.operands:
        filesystem = filesystems[operand.name]
        if request.recursive:
            result = await _remove_recursive(operand, filesystem)
        elif request.directory:
            result = await _remove_directory_entry(operand, filesystem)
        else:
            result = await _confirmed_rm_file(operand, filesystem)
        force_missing = request.force and (
            (
                isinstance(result, _UnlinkFailure)
                and not result.uncertain
                and isinstance(result.backend_error, FileNotFoundError)
            )
            or (isinstance(result, _RecursiveRmFailure) and result.root_missing)
        )
        if result is not None and not force_missing:
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
    *,
    recursive_enabled: bool = False,
) -> None:
    _reject_disabled_recursive_rm(
        raw_arguments,
        recursive_enabled=recursive_enabled,
    )
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failures: list[_UnlinkFailure | _RmdirFailure | _RecursiveRmFailure] = []
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
