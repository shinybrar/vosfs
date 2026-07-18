"""Raw Typer parsing and async execution for ``unlink``."""

from __future__ import annotations

import locale
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import typer

from ._command import _MappedOperand, _render_backend_failure, _usage_error
from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _UnlinkRequest:
    operand: _MappedOperand


@dataclass(frozen=True)
class _UnlinkFailure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    incompatible: Literal["directory", "result"] | None = None
    uncertain: bool = False


def _is_rejected_path(path: str) -> bool:
    normalized = path.rstrip("/")
    if not normalized:
        return True
    final = normalized.rsplit("/", 1)[-1]
    return final in {".", ".."}


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

    if _is_rejected_path(path):
        rendered = _render_diagnostic_value(argument)
        _usage_error(command, f"{rendered}: rejected path")

    return _MappedOperand(spelling=argument, name=name, path=path)


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _UnlinkRequest:
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

    if not operands:
        _usage_error(command, "missing mapped filesystem operand")
    if len(operands) > 1:
        _usage_error(command, "extra operand")

    return _UnlinkRequest(
        operand=_validate_mapped_operand(command, operands[0], known_names)
    )


async def _confirmed_rm_file(  # noqa: PLR0911 - explicit outcome branches.
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> _UnlinkFailure | None:
    """Remove one source-reported file and confirm absence."""
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _UnlinkFailure(operand, backend_error=error)

    if not isinstance(info, Mapping) or not isinstance(info.get("type"), str):
        return _UnlinkFailure(operand, incompatible="result")

    result_type = info["type"]
    if result_type == "directory":
        return _UnlinkFailure(operand, incompatible="directory")
    if result_type != "file":
        return _UnlinkFailure(operand, incompatible="result")

    try:
        await filesystem._rm_file(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - mutation may have partially applied.
        return _UnlinkFailure(operand, backend_error=error, uncertain=True)

    try:
        await filesystem._info(operand.path)  # noqa: SLF001
    except FileNotFoundError:
        return None
    except Exception as error:  # noqa: BLE001 - never hide non-not-found errors.
        return _UnlinkFailure(operand, backend_error=error, uncertain=True)
    else:
        return _UnlinkFailure(operand, uncertain=True)


def _render_operand_diagnostic(
    command: str,
    operand: _MappedOperand,
    category: str,
) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered_operand = _render_diagnostic_value(operand.spelling)
    typer.echo(f"{prefix} {rendered_operand}: {category}", err=True, color=True)


def _render_failure(command: str, failure: _UnlinkFailure) -> None:
    if failure.uncertain:
        _render_operand_diagnostic(command, failure.operand, "uncertain mutation state")
    elif failure.incompatible == "directory":
        _render_operand_diagnostic(command, failure.operand, "is a directory")
    elif failure.incompatible == "result":
        _render_operand_diagnostic(command, failure.operand, "incompatible result")
    elif isinstance(failure.backend_error, IsADirectoryError):
        _render_operand_diagnostic(command, failure.operand, "is a directory")
    elif failure.backend_error is None:
        _render_operand_diagnostic(command, failure.operand, "incompatible result")
    else:
        _render_backend_failure(command, failure.operand, failure.backend_error)


async def _run_unlink(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure: _UnlinkFailure | None = None
    try:
        filesystems = await invocation.acquire((request.operand.name,))
        if filesystems is not None:
            failure = await _confirmed_rm_file(
                request.operand,
                filesystems[request.operand.name],
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
