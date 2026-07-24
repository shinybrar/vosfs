"""Typed async execution for ``mkdir``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._command import (
    _backend_category,
    _CommandFailureError,
    _MappedOperand,
    _render_operand_diagnostic,
    _run_mapped_command,
)

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource


@dataclass(frozen=True)
class _MkdirRequest:
    create_parents: bool
    operands: tuple[_MappedOperand, ...]


@dataclass(frozen=True)
class _Failure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    uncertain: bool = False


async def _run_mkdir(
    command: str,
    request: _MkdirRequest,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    async def operation(filesystems: Mapping[str, AsyncFileSystem]) -> None:
        failures = await _trace_operands(
            request,
            filesystems,
            create_parents=request.create_parents,
        )
        if not failures:
            return
        backend_error = next(
            (
                failure.backend_error
                for failure in failures
                if failure.backend_error is not None
            ),
            None,
        )
        try:
            for failure in failures:
                _render_failure(command, failure)
        except BaseException as error:
            raise _CommandFailureError(
                error=backend_error,
                render=False,
                propagate=error,
            ) from error
        raise _CommandFailureError(error=backend_error, render=False)

    await _run_mapped_command(command, request.operands, sources, operation)


async def _trace_operands(
    request: _MkdirRequest,
    filesystems: Mapping[str, AsyncFileSystem],
    *,
    create_parents: bool,
) -> tuple[_Failure, ...]:
    failures = []
    for operand in request.operands:
        result = await _create_operand(
            operand,
            filesystems[operand.name],
            create_parents=create_parents,
        )
        if isinstance(result, _Failure):
            failures.append(result)
    return tuple(failures)


async def _create_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    *,
    create_parents: bool,
) -> _Failure | None:
    try:
        if create_parents:
            await filesystem._makedirs(  # noqa: SLF001
                operand.path,
                exist_ok=True,
            )
        else:
            await filesystem._mkdir(  # noqa: SLF001
                operand.path,
                create_parents=False,
            )
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _Failure(operand, backend_error=error)

    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - post-mutation verify is uncertain.
        return _Failure(operand, backend_error=error, uncertain=True)

    if not isinstance(info, Mapping) or not isinstance(info.get("type"), str):
        return _Failure(operand, uncertain=True)

    if info["type"] != "directory":
        return _Failure(operand, uncertain=True)

    return None


def _render_failure(command: str, failure: _Failure) -> None:
    if failure.uncertain:
        if failure.backend_error is None:
            category = "uncertain state (incompatible result)"
        else:
            category = f"uncertain state ({_backend_category(failure.backend_error)})"
        _render_operand_diagnostic(command, failure.operand, category)
        return
    if failure.backend_error is None:
        _render_operand_diagnostic(command, failure.operand, "incompatible result")
        return
    _render_operand_diagnostic(
        command,
        failure.operand,
        _backend_category(failure.backend_error),
    )
