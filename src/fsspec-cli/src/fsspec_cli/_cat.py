"""Raw Typer parsing and async execution for mapped-file ``cat``."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import typer

from ._command import (
    _binary_stdout,
    _BinaryWriter,
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _render_failure,
    _render_output_failure,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource

_OUTPUT_CHUNK = 1 << 16
# 128 + SIGPIPE (13): lets pipeline consumers tell "reader went away" from a
# real error when the sole failure is a broken pipe on stdout.
_BROKEN_PIPE_EXIT_CODE = 141


@dataclass(frozen=True)
class _StdinOperand:
    spelling: str = "-"


_CatOperand = _MappedOperand | _StdinOperand


@dataclass(frozen=True)
class _CatRequest:
    operands: tuple[_CatOperand, ...]


@dataclass(frozen=True)
class _StagingFailure:
    operand: _CatOperand
    error: Exception


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _CatRequest:
    operands: list[_CatOperand] = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if argument == "-":
            operands.append(_StdinOperand())
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")

        operands.append(_parse_mapped_operand(command, argument, known_names))

    if not operands:
        operands.append(_StdinOperand())

    return _CatRequest(operands=tuple(operands))


def _binary_stdin() -> BinaryIO:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        message = "stdin has no binary buffer"
        raise OSError(message)
    return buffer


def _write_stdout(stdout: _BinaryWriter, chunk: bytes) -> None:
    written = stdout.write(chunk)
    if written != len(chunk):
        message = "short write"
        raise OSError(message)


def _render_staging_failure(
    command: str,
    operand: _CatOperand,
    error: Exception,
) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered_operand = _render_diagnostic_value(operand.spelling)
    rendered_class = _render_diagnostic_value(type(error).__name__)
    rendered_message = _render_diagnostic_value(str(error))
    typer.echo(
        f"{prefix} {rendered_operand}: staging failure "
        f"({rendered_class}): {rendered_message}",
        err=True,
        color=True,
    )


def _render_operand_failure(command: str, failure: _Failure | _StagingFailure) -> None:
    if isinstance(failure, _StagingFailure):
        _render_staging_failure(command, failure.operand, failure.error)
        return
    if isinstance(failure.backend_error, IsADirectoryError):
        prefix = _render_diagnostic_prefix(command)
        rendered_operand = _render_diagnostic_value(failure.operand.spelling)
        typer.echo(
            f"{prefix} {rendered_operand}: is a directory",
            err=True,
            color=True,
        )
        return
    _render_failure(command, failure)


def _remove_temporary(path: str) -> Exception | None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return None
    except Exception as error:  # noqa: BLE001 - staging cleanup boundary.
        return error
    return None


@dataclass
class _CatOwnership:
    pending_temporaries: set[str]
    temporary_operands: dict[str, _MappedOperand]
    pending_descriptors: dict[int, str]

    def add(self, descriptor: int, temporary: str, operand: _MappedOperand) -> None:
        self.pending_temporaries.add(temporary)
        self.temporary_operands[temporary] = operand
        self.pending_descriptors[descriptor] = temporary


def _close_descriptor(
    ownership: _CatOwnership,
    descriptor: int,
) -> Exception | None:
    try:
        os.close(descriptor)
    except Exception as error:  # noqa: BLE001 - descriptor ownership boundary.
        return error
    ownership.pending_descriptors.pop(descriptor)
    return None


def _remove_owned_temporary(
    ownership: _CatOwnership,
    temporary: str,
) -> Exception | None:
    if temporary not in ownership.pending_temporaries:
        return None
    if temporary in ownership.pending_descriptors.values():
        message = "temporary descriptor remains open"
        return OSError(message)
    cleanup_error = _remove_temporary(temporary)
    if cleanup_error is None:
        ownership.pending_temporaries.remove(temporary)
        ownership.temporary_operands.pop(temporary)
    return cleanup_error


def _sweep_ownership(ownership: _CatOwnership) -> dict[str, Exception]:
    errors: dict[str, Exception] = {}
    for descriptor, temporary in tuple(ownership.pending_descriptors.items()):
        close_error = _close_descriptor(ownership, descriptor)
        if close_error is not None:
            errors[temporary] = close_error
    for temporary in tuple(ownership.pending_temporaries):
        cleanup_error = _remove_owned_temporary(ownership, temporary)
        if cleanup_error is not None:
            errors.setdefault(temporary, cleanup_error)
        else:
            errors.pop(temporary, None)
    return errors


def _failure_after_temporary(
    ownership: _CatOwnership,
    operand: _MappedOperand,
    temporary: str,
    error: Exception,
    *,
    staging: bool,
) -> _StagingFailure | _Failure:
    cleanup_error = _remove_owned_temporary(ownership, temporary)
    if cleanup_error is not None:
        if staging and temporary in ownership.pending_descriptors.values():
            return _StagingFailure(operand, error)
        return _StagingFailure(operand, cleanup_error)
    if staging:
        return _StagingFailure(operand, error)
    return _Failure(operand, backend_error=error)


async def _stage_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    ownership: _CatOwnership,
) -> tuple[str | None, _Failure | _StagingFailure | None]:
    # fsspec's native async API intentionally exposes underscore coroutines.
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return None, _Failure(operand, backend_error=error)

    if not isinstance(info, Mapping) or info.get("type") != "file":
        return None, _Failure(operand)

    try:
        descriptor, temporary = tempfile.mkstemp(prefix="fsspec-cli-cat-")
    except Exception as error:  # noqa: BLE001 - local staging creation boundary.
        return None, _StagingFailure(operand, error)

    ownership.add(descriptor, temporary, operand)
    close_error = _close_descriptor(ownership, descriptor)
    if close_error is not None:
        _sweep_ownership(ownership)
        return None, _failure_after_temporary(
            ownership,
            operand,
            temporary,
            close_error,
            staging=True,
        )

    try:
        await filesystem._get_file(operand.path, temporary)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return None, _failure_after_temporary(
            ownership,
            operand,
            temporary,
            error,
            staging=False,
        )
    except BaseException:
        _remove_owned_temporary(ownership, temporary)
        raise

    return temporary, None


@dataclass(frozen=True)
class _ForwardResult:
    staging_error: Exception | None = None
    output_error: Exception | None = None
    wrote_output: bool = False


def _emit_handle(handle: BinaryIO, *, read_is_output: bool) -> _ForwardResult:
    emitted = False
    stdout: _BinaryWriter | None = None
    while True:
        try:
            chunk = handle.read(_OUTPUT_CHUNK)
        except Exception as error:  # noqa: BLE001 - local staging/stdin read boundary.
            if read_is_output and emitted:
                return _ForwardResult(output_error=error, wrote_output=True)
            return _ForwardResult(staging_error=error, wrote_output=emitted)
        if not chunk:
            break
        if stdout is None:
            try:
                stdout = _binary_stdout()
            except Exception as error:  # noqa: BLE001 - stdout boundary.
                return _ForwardResult(output_error=error, wrote_output=emitted)
        try:
            _write_stdout(stdout, chunk)
        except Exception as error:  # noqa: BLE001 - stdout boundary.
            return _ForwardResult(output_error=error, wrote_output=emitted)
        emitted = True
    if stdout is not None:
        try:
            stdout.flush()
        except Exception as error:  # noqa: BLE001 - stdout boundary.
            return _ForwardResult(output_error=error, wrote_output=emitted)
    return _ForwardResult(wrote_output=emitted)


def _forward_stdin() -> _ForwardResult:
    try:
        handle = _binary_stdin()
    except Exception as error:  # noqa: BLE001 - stdin open boundary.
        return _ForwardResult(staging_error=error)
    return _emit_handle(handle, read_is_output=False)


def _forward_temporary(temporary: str) -> _ForwardResult:
    """Stream one staging handle straight to stdout."""
    try:
        handle = Path(temporary).open("rb")  # noqa: SIM115
    except Exception as error:  # noqa: BLE001 - local staging open boundary.
        return _ForwardResult(staging_error=error)

    forwarded = _ForwardResult()
    try:
        forwarded = _emit_handle(handle, read_is_output=True)
    finally:
        try:
            handle.close()
        except Exception as error:  # noqa: BLE001 - local staging close boundary.
            if forwarded.staging_error is None and forwarded.output_error is None:
                if forwarded.wrote_output:
                    forwarded = _ForwardResult(
                        output_error=error,
                        wrote_output=True,
                    )
                else:
                    forwarded = _ForwardResult(staging_error=error)

    return forwarded


@dataclass
class _CatProgress:
    failures: list[_Failure | _StagingFailure]
    output_error: Exception | None = None
    staging_cleanup_error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return (
            not self.failures
            and self.output_error is None
            and self.staging_cleanup_error is None
        )

    def command_error(self) -> Exception | None:
        for failure in self.failures:
            if isinstance(failure, _StagingFailure):
                return failure.error
            if failure.backend_error is not None:
                return failure.backend_error
        if self.output_error is not None:
            return self.output_error
        return self.staging_cleanup_error


def _apply_forward_result(
    command: str,
    operand: _CatOperand,
    forwarded: _ForwardResult,
    progress: _CatProgress,
) -> None:
    if forwarded.staging_error is not None:
        failure = _StagingFailure(operand, forwarded.staging_error)
        _render_operand_failure(command, failure)
        progress.failures.append(failure)
        return
    if forwarded.output_error is not None:
        progress.output_error = forwarded.output_error
        if not isinstance(forwarded.output_error, BrokenPipeError):
            _render_output_failure(command, forwarded.output_error)


async def _emit_mapped_operand(
    command: str,
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    progress: _CatProgress,
    ownership: _CatOwnership,
) -> None:
    temporary, failure = await _stage_operand(operand, filesystem, ownership)
    if failure is not None:
        _render_operand_failure(command, failure)
        progress.failures.append(failure)
        return
    if temporary is None:
        progress.failures.append(_Failure(operand))
        _render_operand_failure(command, progress.failures[-1])
        return
    try:
        _apply_forward_result(
            command,
            operand,
            _forward_temporary(temporary),
            progress,
        )
    finally:
        cleanup_error = _remove_owned_temporary(ownership, temporary)
        if cleanup_error is not None:
            progress.staging_cleanup_error = cleanup_error
            _render_staging_failure(command, operand, cleanup_error)


async def _emit_operands(
    command: str,
    request: _CatRequest,
    filesystems: Mapping[str, AsyncFileSystem],
    progress: _CatProgress,
    ownership: _CatOwnership,
) -> None:
    for operand in request.operands:
        if progress.output_error is not None:
            return
        if isinstance(operand, _StdinOperand):
            _apply_forward_result(command, operand, _forward_stdin(), progress)
            continue
        await _emit_mapped_operand(
            command,
            operand,
            filesystems[operand.name],
            progress,
            ownership,
        )


async def _run_cat(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    progress = _CatProgress(failures=[])
    ownership = _CatOwnership(set(), {}, {})
    succeeded = False
    try:
        names = dict.fromkeys(
            operand.name
            for operand in request.operands
            if isinstance(operand, _MappedOperand)
        )
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            await _emit_operands(command, request, filesystems, progress, ownership)
            succeeded = progress.succeeded
    finally:
        ownership_errors = _sweep_ownership(ownership)
        if ownership_errors:
            succeeded = False
        for temporary, error in ownership_errors.items():
            operand = ownership.temporary_operands[temporary]
            _render_staging_failure(command, operand, error)
            progress.staging_cleanup_error = error
        cleanup_failed = await invocation.close_with_command_error(
            progress.command_error()
        )
    if not succeeded or cleanup_failed:
        if (
            not cleanup_failed
            and not progress.failures
            and progress.staging_cleanup_error is None
            and isinstance(progress.output_error, BrokenPipeError)
        ):
            raise typer.Exit(_BROKEN_PIPE_EXIT_CODE)
        raise typer.Exit(1)
