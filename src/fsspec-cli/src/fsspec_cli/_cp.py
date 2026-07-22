"""Raw Typer parsing and async execution for verified file ``cp``."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import typer

from ._command import (
    _MappedOperand,
    _parse_mapped_operand,
    _render_backend_failure,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._path import _lexical_basename
from ._recursive_cp import _run_recursive_cp
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource

_MIN_OPERAND_COUNT = 2
_VERIFICATION_TOKEN_ALIASES = (
    ("etag", ("ETag", "etag")),
    ("md5", ("md5",)),
    ("content-md5", ("content-md5", "content_md5")),
    ("checksum", ("checksum",)),
)


@dataclass(frozen=True)
class _CpRequest:
    source: _MappedOperand
    destination: _MappedOperand


@dataclass(frozen=True)
class _CpPlan:
    requests: tuple[_CpRequest, ...]
    require_directory: bool
    recursive: bool


@dataclass(frozen=True)
class _CpFailure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    incompatible: Literal["directory", "result", "same_path"] | None = None
    category: str | None = None
    uncertain: bool = False
    residue: bool = False


@dataclass(frozen=True)
class _TransferProof:
    expected_size: int
    tokens: tuple[tuple[str, frozenset[str | bytes]], ...]


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _CpPlan:
    operands: list[str] = []
    options_active = True
    recursive = False

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument in {"-R", "-r"}:
            if recursive:
                rendered = _render_diagnostic_value(argument)
                _usage_error(command, f"{rendered}: unsupported option")
            recursive = True
            continue
        if options_active and argument.startswith("-") and argument != "-":
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        operands.append(argument)

    if len(operands) < _MIN_OPERAND_COUNT:
        _usage_error(command, "missing mapped filesystem operand")
    if recursive and len(operands) > _MIN_OPERAND_COUNT:
        _usage_error(command, "extra operand")

    mapped = tuple(
        _parse_mapped_operand(command, operand, known_names) for operand in operands
    )
    destination = mapped[-1]
    return _CpPlan(
        requests=tuple(
            _CpRequest(source=source, destination=destination) for source in mapped[:-1]
        ),
        require_directory=len(mapped) > _MIN_OPERAND_COUNT,
        recursive=recursive,
    )


async def _require_directory(
    destination: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> _CpFailure | None:
    try:
        info = await filesystem._info(destination.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _CpFailure(destination, backend_error=error)
    if not isinstance(info, Mapping) or not isinstance(info.get("type"), str):
        return _CpFailure(destination, incompatible="result")
    if info["type"] == "file":
        return _CpFailure(destination, category="not a directory")
    if info["type"] != "directory":
        return _CpFailure(destination, incompatible="result")
    return None


def _parent_path(path: str) -> str:
    normalized = path.rstrip("/") or "/"
    if normalized == "/":
        return "/"
    parent, _separator, _name = normalized.rpartition("/")
    return parent or "/"


def _join_under(directory: str, name: str) -> str:
    if name == "/":
        name = ""
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


def _require_source_file_size(
    source: _MappedOperand,
    info: object,
) -> tuple[int | None, _CpFailure | None]:
    if not isinstance(info, Mapping):
        return None, _CpFailure(source, incompatible="result")
    result_type = info.get("type")
    if not isinstance(result_type, str):
        return None, _CpFailure(source, incompatible="result")
    if result_type == "directory":
        return None, _CpFailure(source, incompatible="directory")
    if result_type != "file":
        return None, _CpFailure(source, incompatible="result")
    size = _require_file_size(info)
    if size is None:
        return None, _CpFailure(source, incompatible="result")
    return size, None


async def _resolve_destination(  # noqa: C901, PLR0911, PLR0912 - explicit target branches.
    destination: _MappedOperand,
    source_path: str,
    filesystem: AsyncFileSystem,
) -> tuple[str, _CpFailure | None]:
    known_directory: str | None = None
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
            known_directory = destination.path
            resolved = _join_under(destination.path, _lexical_basename(source_path))
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
    if parent == known_directory:
        return resolved, None
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
        _remove_temporary(temporary)
        return None, error
    except BaseException:
        _discard_temporary(temporary)
        raise

    try:
        await filesystem._get_file(remote, temporary)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - staging download boundary.
        _remove_temporary(temporary)
        return None, error
    except BaseException:
        _discard_temporary(temporary)
        raise
    return temporary, None


def _remove_temporary(path: str) -> Exception | None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as error:  # noqa: BLE001 - local staging cleanup boundary.
        return error
    return None


def _discard_temporary(path: str) -> None:
    with suppress(BaseException):
        _remove_temporary(path)


def _verification_tokens(info: object) -> dict[str, frozenset[str | bytes]]:
    if not isinstance(info, Mapping):
        return {}
    tokens: dict[str, frozenset[str | bytes]] = {}
    for normalized, aliases in _VERIFICATION_TOKEN_ALIASES:
        values = frozenset(
            value
            for alias in aliases
            if (type(value := info.get(alias)) is str or type(value) is bytes)
        )
        if values:
            tokens[normalized] = values
    return tokens


def _freeze_transfer_proof(
    source_info: object,
    expected_size: int,
) -> _TransferProof:
    return _TransferProof(
        expected_size=expected_size,
        tokens=tuple(_verification_tokens(source_info).items()),
    )


def _shared_verification_tokens_match(
    proof: _TransferProof,
    destination_info: object,
) -> bool:
    destination_tokens = _verification_tokens(destination_info)
    return all(
        destination_tokens[field] == source_values
        for field, source_values in proof.tokens
        if field in destination_tokens
    )


async def _verify_transfer(  # noqa: PLR0913 - one explicit transfer-proof boundary.
    source_filesystem: AsyncFileSystem,
    destination_filesystem: AsyncFileSystem,
    source_path: str,
    destination_path: str,
    proof: _TransferProof,
    destination: _MappedOperand,
    *,
    require_source_absent: bool,
) -> _CpFailure | None:
    try:
        destination_info = await destination_filesystem._info(  # noqa: SLF001
            destination_path
        )
    except Exception as error:  # noqa: BLE001 - post-copy verify is residue-bearing.
        return _CpFailure(
            destination,
            backend_error=error,
            residue=True,
            category="verification failure",
        )

    verified_size = _require_file_size(destination_info)
    if (
        verified_size is None
        or verified_size != proof.expected_size
        or not _shared_verification_tokens_match(proof, destination_info)
    ):
        return _CpFailure(
            destination,
            category="verification failure",
            residue=True,
        )

    if require_source_absent:
        try:
            await source_filesystem._info(source_path)  # noqa: SLF001
        except FileNotFoundError:
            return None
        except Exception as error:  # noqa: BLE001 - post-move absence proof.
            return _CpFailure(
                destination,
                backend_error=error,
                category="verification failure",
                residue=True,
            )
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

    expected_size, source_failure = _require_source_file_size(
        request.source, source_info
    )
    if source_failure is not None:
        return source_failure
    if expected_size is None:
        return _CpFailure(request.source, incompatible="result")
    proof = _freeze_transfer_proof(source_info, expected_size)

    resolved, resolution_failure = await _resolve_destination(
        request.destination,
        request.source.path,
        destination_filesystem,
    )
    if resolution_failure is not None:
        return resolution_failure

    if source_filesystem is destination_filesystem and request.source.path == resolved:
        return _CpFailure(request.source, incompatible="same_path")

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

    primary_failure: _CpFailure | None = None
    mutated = False
    try:
        try:
            staged_size = Path(temporary).stat().st_size  # noqa: ASYNC240
        except Exception as error:  # noqa: BLE001 - local staging boundary.
            primary_failure = _CpFailure(
                request.source,
                backend_error=error,
                category="staging failure",
            )
        else:
            if staged_size != expected_size:
                primary_failure = _CpFailure(
                    request.source,
                    category="verification failure",
                )

        if primary_failure is None:
            mutated = True
            try:
                await destination_filesystem._put_file(  # noqa: SLF001
                    temporary, resolved, mode="overwrite"
                )
            except Exception as error:  # noqa: BLE001 - mutation may leave residue.
                primary_failure = _CpFailure(
                    request.destination,
                    backend_error=error,
                    uncertain=True,
                    residue=True,
                )

        if primary_failure is None:
            primary_failure = await _verify_transfer(
                source_filesystem,
                destination_filesystem,
                request.source.path,
                resolved,
                proof,
                request.destination,
                require_source_absent=False,
            )
    except BaseException:
        _discard_temporary(temporary)
        raise

    cleanup_error = _remove_temporary(temporary)
    if primary_failure is not None:
        return primary_failure
    if cleanup_error is not None:
        return _CpFailure(
            request.destination if mutated else request.source,
            backend_error=cleanup_error,
            category="staging failure",
            residue=mutated,
        )
    return None


async def _confirmed_cp_file(  # noqa: PLR0911 - explicit copy outcomes.
    request: _CpRequest,
    filesystem: AsyncFileSystem,
) -> _CpFailure | None:
    try:
        source_info = await filesystem._info(request.source.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _CpFailure(request.source, backend_error=error)

    expected_size, source_failure = _require_source_file_size(
        request.source, source_info
    )
    if source_failure is not None:
        return source_failure
    if expected_size is None:
        return _CpFailure(request.source, incompatible="result")
    proof = _freeze_transfer_proof(source_info, expected_size)

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

    return await _verify_transfer(
        filesystem,
        filesystem,
        request.source.path,
        resolved,
        proof,
        request.destination,
        require_source_absent=False,
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
    return f"staging failure ({rendered_class})"


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


def _reject_disabled_recursive_copy(
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
        elif options_active and argument in {"-R", "-r"}:
            typer.echo(
                "cp: recursive copy disabled by application",
                err=True,
            )
            raise typer.Exit(2)
        elif options_active and argument.startswith("-") and argument != "-":
            break


async def _run_cp(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
    *,
    recursive_enabled: bool = True,
) -> None:
    _reject_disabled_recursive_copy(
        raw_arguments,
        recursive_enabled=recursive_enabled,
    )
    plan = _preflight(command, raw_arguments, sources)
    if plan.recursive:
        await _run_recursive_cp(
            command,
            plan.requests[0].source,
            plan.requests[0].destination,
            sources,
        )
        return
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure: _CpFailure | None = None
    try:
        names = tuple(
            dict.fromkeys(
                (
                    *(request.source.name for request in plan.requests),
                    plan.requests[0].destination.name,
                )
            )
        )
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            if plan.require_directory:
                failure = await _require_directory(
                    plan.requests[0].destination,
                    filesystems[plan.requests[0].destination.name],
                )
            for request in plan.requests if failure is None else ():
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
                    break
            if failure is not None:
                _render_failure(command, failure)
            succeeded = failure is None
    finally:
        command_error = failure.backend_error if failure is not None else None
        cleanup_failed = await invocation.close_with_command_error(command_error)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)
