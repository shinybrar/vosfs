"""Reduced BSD/macOS ``stat`` execution for typed callbacks.

Owner and group names resolve through the local ``pwd``/``grp`` account
databases on a best-effort basis: they describe the local namespace, not the
remote source's, and fall back to the numeric id when a name is unknown or when
the host lacks these POSIX-only modules.
"""

from __future__ import annotations

import math
import stat as stat_module
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import typer

from ._command import (
    _binary_stdout,
    _CommandFailureError,
    _MappedOperand,
    _render_backend_failure,
    _run_mapped_command,
    _write_binary,
)
from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource

try:
    import grp
    import pwd

    _HAS_ACCOUNT_DB = True
except ImportError:  # pragma: no cover - POSIX-only account databases.
    _HAS_ACCOUNT_DB = False

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True)
class _StatFailure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    incompatible: Literal["result"] | None = None


@dataclass(frozen=True)
class _StatSuccess:
    operand: _MappedOperand
    line: bytes


def _owner_name(uid: int) -> str:
    """Resolve ``uid`` to a local ``pwd`` account name, numeric when unavailable."""
    if not _HAS_ACCOUNT_DB:
        return str(uid)
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OverflowError, OSError):
        return str(uid)


def _group_name(gid: int) -> str:
    """Resolve ``gid`` to a local ``grp`` account name, numeric when unavailable."""
    if not _HAS_ACCOUNT_DB:
        return str(gid)
    try:
        return grp.getgrgid(gid).gr_name
    except (KeyError, OverflowError, OSError):
        return str(gid)


def _format_mtime(mtime: float) -> str:
    local = time.localtime(mtime)
    month = _MONTHS[local.tm_mon - 1]
    return (
        f"{month} {local.tm_mday:2d} "
        f"{local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d} "
        f"{local.tm_year}"
    )


def _validate_info(  # noqa: C901, PLR0911, PLR0912 - locked Local-rich shape checks.
    info: object,
) -> Mapping[str, object] | None:
    if not isinstance(info, Mapping):
        return None
    mapping = cast("Mapping[str, object]", info)
    result_type = mapping.get("type")
    if result_type not in {"file", "directory"} or type(result_type) is not str:
        return None
    if "islink" in mapping:
        islink = mapping["islink"]
        if type(islink) is not bool or islink:
            return None
    name = mapping.get("name")
    if type(name) is not str:
        return None
    size = mapping.get("size")
    if type(size) is not int or size < 0:
        return None
    mode = mapping.get("mode")
    if type(mode) is not int:
        return None
    nlink = mapping.get("nlink")
    if type(nlink) is not int or nlink < 1:
        return None
    uid = mapping.get("uid")
    if type(uid) is not int or uid < 0:
        return None
    gid = mapping.get("gid")
    if type(gid) is not int or gid < 0:
        return None
    mtime = mapping.get("mtime")
    if type(mtime) is int:
        checked_mtime: float = float(mtime)
    elif type(mtime) is float:
        checked_mtime = mtime
    else:
        return None
    if not math.isfinite(checked_mtime):
        return None
    try:
        time.localtime(checked_mtime)
    except (OverflowError, OSError, ValueError):
        return None
    return mapping


def _render_line(operand: _MappedOperand, info: Mapping[str, object]) -> bytes:
    mode = cast("int", info["mode"])
    nlink = cast("int", info["nlink"])
    uid = cast("int", info["uid"])
    gid = cast("int", info["gid"])
    size = cast("int", info["size"])
    mtime = cast("float", info["mtime"])
    line = (
        f"{stat_module.filemode(mode)} {nlink} {_owner_name(uid)} {_group_name(gid)} "
        f'{size} "{_format_mtime(mtime)}" {operand.path}\n'
    )
    return line.encode()


def _write_line(line: bytes) -> None:
    stdout = _binary_stdout()
    _write_binary(stdout, line)
    stdout.flush()


def _render_failure(command: str, failure: _StatFailure) -> None:
    if failure.incompatible == "result" or failure.backend_error is None:
        prefix = _render_diagnostic_prefix(command)
        rendered_operand = _render_diagnostic_value(failure.operand.spelling)
        typer.echo(
            f"{prefix} {rendered_operand}: incompatible result",
            err=True,
            color=True,
        )
        return
    _render_backend_failure(command, failure.operand, failure.backend_error)


async def _read_operand(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> _StatSuccess | _StatFailure:
    try:
        info = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - classify awaited backend failure.
        return _StatFailure(operand, backend_error=error)

    validated = _validate_info(info)
    if validated is None:
        return _StatFailure(operand, incompatible="result")
    return _StatSuccess(operand, _render_line(operand, validated))


async def _trace_operands(
    command: str,
    operands: tuple[_MappedOperand, ...],
    filesystems: Mapping[str, AsyncFileSystem],
) -> None:
    failures: list[_StatFailure] = []
    for operand in operands:
        result = await _read_operand(operand, filesystems[operand.name])
        if isinstance(result, _StatFailure):
            failures.append(result)
            try:
                _render_failure(command, result)
            except Exception as error:
                raise _CommandFailureError(
                    error=result.backend_error,
                    render=False,
                    propagate=error,
                ) from error
            continue
        try:
            _write_line(result.line)
        except Exception as error:
            raise _CommandFailureError(error=error) from error

    if failures:
        first_backend_error = next(
            (
                failure.backend_error
                for failure in failures
                if failure.backend_error is not None
            ),
            None,
        )
        raise _CommandFailureError(
            error=first_backend_error,
            render=False,
        )


async def _run_stat(
    command: str,
    operands: tuple[_MappedOperand, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    async def execute(filesystems: Mapping[str, AsyncFileSystem]) -> None:
        await _trace_operands(command, operands, filesystems)

    await _run_mapped_command(
        command,
        operands,
        sources,
        execute,
        broken_pipe_exit_code=1,
    )
