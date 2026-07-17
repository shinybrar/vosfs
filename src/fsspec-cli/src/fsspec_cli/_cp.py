"""Raw Typer parsing and async execution for same-source two-operand ``cp``."""

from __future__ import annotations

import hashlib
import locale
import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
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

_COMPARE_CHUNK = 1 << 16
_OPERAND_COUNT = 2


@dataclass(frozen=True)
class _CpRequest:
    source: _MappedOperand
    destination: _MappedOperand


@dataclass(frozen=True)
class _CpFailure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    incompatible: Literal["directory", "result", "same_path"] | None = None
    category: str | None = None
    uncertain: bool = False
    residue: bool = False


class _CpCommand(TyperCommand):
    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        ctx.meta[_RAW_ARGUMENTS] = tuple(args)
        return super().parse_args(ctx, _shield_help_values(args))


def _raw_arguments(ctx: typer.Context) -> tuple[str, ...]:
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _validate_mapped_operand(
    command: str,
    argument: str,
    known_names: Collection[str],
) -> _MappedOperand:
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

    return _MappedOperand(spelling=argument, name=name, path=path)


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _CpRequest:
    operands: list[str] = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        operands.append(argument)

    if len(operands) < _OPERAND_COUNT:
        _usage_error(command, "missing mapped filesystem operand")
    if len(operands) > _OPERAND_COUNT:
        _usage_error(command, "extra operand")

    source = _validate_mapped_operand(command, operands[0], known_names)
    destination = _validate_mapped_operand(command, operands[1], known_names)

    return _CpRequest(source=source, destination=destination)


def _basename(path: str) -> str:
    normalized = path.rstrip("/") or "/"
    return normalized.rsplit("/", 1)[-1]


def _parent_path(path: str) -> str:
    normalized = path.rstrip("/") or "/"
    if normalized == "/":
        return "/"
    parent, _separator, _name = normalized.rpartition("/")
    return parent or "/"


def _join_under(directory: str, name: str) -> str:
    if directory in {"/", ""}:
        return f"/{name}"
    return f"{directory.rstrip('/')}/{name}"


def _require_file_size(info: object) -> int | None:
    if not isinstance(info, Mapping):
        return None
    result_type = info.get("type")
    if not isinstance(result_type, str) or result_type != "file":
        return None
    size = info.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return None
    return size


async def _resolve_destination(  # noqa: C901, PLR0911, PLR0912 - explicit target branches.
    destination: _MappedOperand,
    source_path: str,
    filesystem: AsyncFileSystem,
) -> tuple[str, _CpFailure | None]:
    try:
        dest_info = await filesystem._info(destination.path)  # noqa: SLF001
    except FileNotFoundError:
        resolved = destination.path
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return destination.path, _CpFailure(destination, backend_error=error)
    else:
        if not isinstance(dest_info, Mapping) or not isinstance(
            dest_info.get("type"), str
        ):
            return destination.path, _CpFailure(destination, incompatible="result")
        dest_type = dest_info["type"]
        if dest_type == "directory":
            resolved = _join_under(destination.path, _basename(source_path))
        elif dest_type == "file":
            resolved = destination.path
        else:
            return destination.path, _CpFailure(destination, incompatible="result")

    if resolved != destination.path:
        try:
            collision = await filesystem._info(resolved)  # noqa: SLF001
        except FileNotFoundError:
            collision = None
        except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
            return resolved, _CpFailure(destination, backend_error=error)
        if collision is not None:
            if not isinstance(collision, Mapping) or not isinstance(
                collision.get("type"), str
            ):
                return resolved, _CpFailure(destination, incompatible="result")
            if collision["type"] == "directory":
                return resolved, _CpFailure(destination, incompatible="result")
            if collision["type"] != "file":
                return resolved, _CpFailure(destination, incompatible="result")

    parent = _parent_path(resolved)
    try:
        parent_info = await filesystem._info(parent)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return resolved, _CpFailure(destination, backend_error=error)

    if not isinstance(parent_info, Mapping) or not isinstance(
        parent_info.get("type"), str
    ):
        return resolved, _CpFailure(destination, incompatible="result")
    if parent_info["type"] != "directory":
        category = (
            "not a directory"
            if parent_info["type"] == "file"
            else "incompatible result"
        )
        return resolved, _CpFailure(destination, category=category)

    return resolved, None


async def _stage_remote(
    filesystem: AsyncFileSystem,
    remote: str,
    prefix: str,
) -> tuple[str | None, Exception | None]:
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=prefix)
    except Exception as error:  # noqa: BLE001 - local staging creation boundary.
        return None, error
    try:
        os.close(descriptor)
    except Exception as error:  # noqa: BLE001 - local staging descriptor boundary.
        cleanup = _remove_temporary(temporary)
        return None, cleanup if cleanup is not None else error

    try:
        await filesystem._get_file(remote, temporary)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - staging download boundary.
        cleanup = _remove_temporary(temporary)
        return None, cleanup if cleanup is not None else error
    except BaseException:
        _remove_temporary(temporary)
        raise
    return temporary, None


def _remove_temporary(path: str) -> Exception | None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as error:  # noqa: BLE001 - local staging cleanup boundary.
        return error
    return None


def _files_match(left: str, right: str) -> tuple[bool, Exception | None]:
    try:
        with (
            Path(left).open("rb") as left_handle,
            Path(right).open("rb") as right_handle,
        ):
            while True:
                left_chunk = left_handle.read(_COMPARE_CHUNK)
                right_chunk = right_handle.read(_COMPARE_CHUNK)
                if left_chunk != right_chunk:
                    return False, None
                if not left_chunk:
                    return True, None
    except Exception as error:  # noqa: BLE001 - local comparison boundary.
        return False, error


def _file_digest(path: str) -> tuple[bytes | None, Exception | None]:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(_COMPARE_CHUNK), b""):
                digest.update(chunk)
    except Exception as error:  # noqa: BLE001 - local verification boundary.
        return None, error
    return digest.digest(), None


async def _verify_copy(  # noqa: PLR0911 - explicit verify outcomes.
    filesystem: AsyncFileSystem,
    source_path: str,
    destination_path: str,
    expected_size: int,
    destination: _MappedOperand,
) -> _CpFailure | None:
    try:
        info = await filesystem._info(destination_path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - post-copy verify is residue-bearing.
        return _CpFailure(
            destination,
            backend_error=error,
            residue=True,
            category="verification failure",
        )

    verified_size = _require_file_size(info)
    if verified_size is None or verified_size != expected_size:
        return _CpFailure(
            destination,
            category="verification failure",
            residue=True,
        )

    source_temp, source_error = await _stage_remote(
        filesystem,
        source_path,
        "fsspec-cli-cp-src-",
    )
    if source_error is not None or source_temp is None:
        return _CpFailure(
            destination,
            backend_error=source_error,
            category="staging failure",
            residue=True,
        )

    try:
        dest_temp, dest_error = await _stage_remote(
            filesystem,
            destination_path,
            "fsspec-cli-cp-dst-",
        )
    except BaseException:
        _remove_temporary(source_temp)
        raise
    if dest_error is not None or dest_temp is None:
        cleanup = _remove_temporary(source_temp)
        return _CpFailure(
            destination,
            backend_error=dest_error or cleanup,
            category="staging failure",
            residue=True,
        )

    matched, compare_error = _files_match(source_temp, dest_temp)
    source_cleanup = _remove_temporary(source_temp)
    dest_cleanup = _remove_temporary(dest_temp)
    cleanup_error = source_cleanup or dest_cleanup
    if compare_error is not None:
        return _CpFailure(
            destination,
            backend_error=compare_error,
            category="staging failure",
            residue=True,
        )
    if cleanup_error is not None:
        return _CpFailure(
            destination,
            backend_error=cleanup_error,
            category="staging failure",
            residue=True,
        )
    if not matched:
        return _CpFailure(
            destination,
            category="verification failure",
            residue=True,
        )
    return None


async def _confirmed_cross_source_cp_file(  # noqa: C901, PLR0911, PLR0912 - explicit copy outcomes.
    request: _CpRequest,
    source_filesystem: AsyncFileSystem,
    destination_filesystem: AsyncFileSystem,
) -> _CpFailure | None:
    try:
        source_info = await source_filesystem._info(request.source.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _CpFailure(request.source, backend_error=error)

    if not isinstance(source_info, Mapping) or not isinstance(
        source_info.get("type"), str
    ):
        return _CpFailure(request.source, incompatible="result")
    if source_info["type"] == "directory":
        return _CpFailure(request.source, incompatible="directory")
    expected_size = _require_file_size(source_info)
    if expected_size is None:
        return _CpFailure(request.source, incompatible="result")

    resolved, resolution_failure = await _resolve_destination(
        request.destination,
        request.source.path,
        destination_filesystem,
    )
    if resolution_failure is not None:
        return resolution_failure

    temporary, staging_error = await _stage_remote(
        source_filesystem,
        request.source.path,
        "fsspec-cli-cp-",
    )
    if staging_error is not None or temporary is None:
        return _CpFailure(
            request.source,
            backend_error=staging_error,
            category="staging failure",
        )

    mutated = False
    try:
        source_digest, digest_error = _file_digest(temporary)
        if digest_error is not None or source_digest is None:
            return _CpFailure(
                request.source,
                backend_error=digest_error,
                category="staging failure",
            )
        try:
            staged_size = Path(temporary).stat().st_size  # noqa: ASYNC240
        except Exception as error:  # noqa: BLE001 - local staging boundary.
            return _CpFailure(
                request.source,
                backend_error=error,
                category="staging failure",
            )
        if staged_size != expected_size:
            return _CpFailure(request.source, category="verification failure")

        try:
            await destination_filesystem._put_file(  # noqa: SLF001
                temporary, resolved, mode="overwrite"
            )
            mutated = True
        except Exception as error:  # noqa: BLE001 - mutation may leave residue.
            mutated = True
            return _CpFailure(
                request.destination,
                backend_error=error,
                uncertain=True,
                residue=True,
            )

        try:
            info = await destination_filesystem._info(resolved)  # noqa: SLF001
        except Exception as error:  # noqa: BLE001 - post-copy verify boundary.
            return _CpFailure(
                request.destination,
                backend_error=error,
                category="verification failure",
                residue=True,
            )
        if _require_file_size(info) != expected_size:
            return _CpFailure(
                request.destination,
                category="verification failure",
                residue=True,
            )

        try:
            await destination_filesystem._get_file(resolved, temporary)  # noqa: SLF001
        except Exception as error:  # noqa: BLE001 - post-copy staging boundary.
            return _CpFailure(
                request.destination,
                backend_error=error,
                category="staging failure",
                residue=True,
            )
        destination_digest, digest_error = _file_digest(temporary)
        if digest_error is not None:
            return _CpFailure(
                request.destination,
                backend_error=digest_error,
                category="staging failure",
                residue=True,
            )
        if destination_digest != source_digest:
            return _CpFailure(
                request.destination,
                category="verification failure",
                residue=True,
            )
    finally:
        cleanup_error = _remove_temporary(temporary)
        if cleanup_error is not None and sys.exc_info()[0] is None:
            return _CpFailure(  # noqa: B012 - cleanup failure replaces return only.
                request.destination if mutated else request.source,
                backend_error=cleanup_error,
                category="staging failure",
                residue=mutated,
            )


async def _confirmed_cp_file(  # noqa: PLR0911 - explicit copy outcomes.
    request: _CpRequest,
    filesystem: AsyncFileSystem,
) -> _CpFailure | None:
    try:
        source_info = await filesystem._info(request.source.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _CpFailure(request.source, backend_error=error)

    if not isinstance(source_info, Mapping) or not isinstance(
        source_info.get("type"), str
    ):
        return _CpFailure(request.source, incompatible="result")
    if source_info["type"] == "directory":
        return _CpFailure(request.source, incompatible="directory")
    expected_size = _require_file_size(source_info)
    if expected_size is None:
        return _CpFailure(request.source, incompatible="result")

    resolved, resolution_failure = await _resolve_destination(
        request.destination,
        request.source.path,
        filesystem,
    )
    if resolution_failure is not None:
        return resolution_failure

    if request.source.path == resolved:
        return _CpFailure(request.source, incompatible="same_path")

    try:
        await filesystem._cp_file(request.source.path, resolved)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - mutation may leave destination residue.
        return _CpFailure(
            request.destination,
            backend_error=error,
            uncertain=True,
            residue=True,
        )

    return await _verify_copy(
        filesystem,
        request.source.path,
        resolved,
        expected_size,
        request.destination,
    )


def _render_operand_diagnostic(
    command: str,
    operand: _MappedOperand,
    category: str,
) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered_operand = _render_diagnostic_value(operand.spelling)
    typer.echo(f"{prefix} {rendered_operand}: {category}", err=True, color=True)


def _render_staging_category(error: Exception) -> str:
    rendered_class = _render_diagnostic_value(type(error).__name__)
    rendered_message = _render_diagnostic_value(str(error))
    return f"staging failure ({rendered_class}): {rendered_message}"


def _render_failure(  # noqa: C901 - stable diagnostic categories.
    command: str, failure: _CpFailure
) -> None:
    suffix = "; destination residue may remain" if failure.residue else ""
    if failure.uncertain:
        _render_operand_diagnostic(
            command,
            failure.operand,
            f"uncertain mutation state{suffix}",
        )
    elif failure.incompatible == "directory":
        _render_operand_diagnostic(command, failure.operand, "is a directory")
    elif failure.incompatible == "same_path":
        _render_operand_diagnostic(command, failure.operand, "same path")
    elif failure.incompatible == "result":
        _render_operand_diagnostic(command, failure.operand, "incompatible result")
    elif failure.category == "verification failure":
        _render_operand_diagnostic(
            command,
            failure.operand,
            f"verification failure{suffix}",
        )
    elif failure.category == "not a directory":
        _render_operand_diagnostic(command, failure.operand, "not a directory")
    elif failure.category == "staging failure" and failure.backend_error is not None:
        _render_operand_diagnostic(
            command,
            failure.operand,
            f"{_render_staging_category(failure.backend_error)}{suffix}",
        )
    elif isinstance(failure.backend_error, IsADirectoryError):
        _render_operand_diagnostic(command, failure.operand, "is a directory")
    elif isinstance(failure.backend_error, NotADirectoryError):
        _render_operand_diagnostic(command, failure.operand, "not a directory")
    elif failure.backend_error is None:
        _render_operand_diagnostic(
            command,
            failure.operand,
            failure.category or "incompatible result",
        )
    else:
        _render_backend_failure(command, failure.operand, failure.backend_error)


async def _run_cp(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure: _CpFailure | None = None
    try:
        names = (request.source.name,)
        if request.source.name != request.destination.name:
            names += (request.destination.name,)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            if request.source.name == request.destination.name:
                failure = await _confirmed_cp_file(
                    request,
                    filesystems[request.source.name],
                )
            else:
                failure = await _confirmed_cross_source_cp_file(
                    request,
                    filesystems[request.source.name],
                    filesystems[request.destination.name],
                )
            if failure is not None:
                _render_failure(command, failure)
            succeeded = failure is None
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
