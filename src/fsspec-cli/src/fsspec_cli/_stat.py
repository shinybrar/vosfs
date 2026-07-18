"""Raw Typer parsing and async execution for reduced BSD/macOS ``stat``.

Owner and group names resolve through the local ``pwd``/``grp`` account
databases on a best-effort basis: they describe the local namespace, not the
remote source's, and fall back to the numeric id when a name is unknown or when
the host lacks these POSIX-only modules.
"""

from __future__ import annotations

import locale
import math
import stat as stat_module
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._ls import (
    _RAW_ARGUMENTS,
    _MappedOperand,
    _render_backend_failure,
    _render_output_failure,
    _shield_help_values,
    _usage_error,
)
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

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


class _BinaryWriter(Protocol):
    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...


@dataclass(frozen=True)
class _StatRequest:
    operands: tuple[_MappedOperand, ...]


@dataclass(frozen=True)
class _StatFailure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    incompatible: Literal["result"] | None = None


@dataclass(frozen=True)
class _StatSuccess:
    operand: _MappedOperand
    line: bytes


class _StatCommand(TyperCommand):
    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        ctx.meta[_RAW_ARGUMENTS] = tuple(args)
        return super().parse_args(ctx, _shield_help_values(args))

    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("stat", "[--] name:/path...")


def _raw_arguments(ctx: typer.Context) -> tuple[str, ...]:
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _StatRequest:
    operands: list[_MappedOperand] = []
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

        operands.append(_MappedOperand(spelling=argument, name=name, path=path))

    if not operands:
        _usage_error(command, "missing mapped filesystem operand")

    return _StatRequest(operands=tuple(operands))


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


def _binary_stdout() -> _BinaryWriter:
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        message = "stdout has no binary buffer"
        raise OSError(message)
    return buffer


def _write_line(line: bytes) -> None:
    stdout = _binary_stdout()
    written = stdout.write(line)
    if written != len(line):
        message = "short write"
        raise OSError(message)
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
    request: _StatRequest,
    filesystems: Mapping[str, AsyncFileSystem],
    failures: list[_StatFailure],
) -> Exception | None:
    for operand in request.operands:
        result = await _read_operand(operand, filesystems[operand.name])
        if isinstance(result, _StatFailure):
            failures.append(result)
            _render_failure(command, result)
            continue
        try:
            _write_line(result.line)
        except BrokenPipeError as error:
            return error
        except Exception as error:  # noqa: BLE001 - stdout boundary.
            _render_output_failure(command, error)
            return error
    return None


async def _run_stat(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failures: list[_StatFailure] = []
    output_error: Exception | None = None
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            output_error = await _trace_operands(
                command, request, filesystems, failures
            )
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
