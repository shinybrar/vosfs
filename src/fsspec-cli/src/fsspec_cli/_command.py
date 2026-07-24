"""Shared scaffolding for mapped-source command modules.

Every mapped-operand command (``ls``, ``du``, ``find``, ``size``, ``test``,
``head``, ``tail``, ``tree``, ``info``, ``cat``, ``cp``, ``mv``, ``mkdir``,
``rmdir``, ``rm``, ``unlink``, and ``stat``) parses its own raw ``argv`` and
renders stable diagnostics. This module is the single home for the pieces they
share: raw-argument capture, the malformed-help shield, mapped operands, usage
errors, binary stdout, and the single-operand buffered-text lifecycle.
"""

from __future__ import annotations

import locale
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, Protocol, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context

    from ._app import AsyncFilesystemSource

_RAW_ARGUMENTS = "fsspec_cli.raw_arguments"
# 128 + SIGPIPE (13): lets pipeline consumers distinguish a closed reader from
# an ordinary command failure when the broken pipe is the sole failure.
_BROKEN_PIPE_EXIT_CODE = 141


class _RawCommand(TyperCommand):
    """A Typer command that captures raw ``argv`` before framework parsing.

    Command preflight needs the exact tokens the user supplied, so the raw
    arguments are stashed on ``ctx.meta`` and malformed ``--help=`` tokens are
    shielded from Click's eager help option.
    """

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        ctx.meta[_RAW_ARGUMENTS] = tuple(args)
        return super().parse_args(ctx, _shield_help_values(args))


def _shield_help_values(arguments: list[str]) -> list[str]:
    """Keep malformed help tokens available to command preflight."""
    shielded = []
    options_active = True
    for argument in arguments:
        if argument == "--":
            options_active = False
        if options_active and argument.startswith("--help="):
            shielded.append("--fsspec-cli-unsupported-help-value")
        else:
            shielded.append(argument)
    return shielded


def _raw_arguments(ctx: typer.Context) -> tuple[str, ...]:
    """Return the raw ``argv`` captured by :class:`_RawCommand`."""
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _usage_error(command: str, diagnostic: str) -> NoReturn:
    """Emit one stable usage diagnostic on stderr and exit ``2``."""
    prefix = _render_diagnostic_prefix(command)
    typer.echo(f"{prefix} {diagnostic}", err=True, color=True)
    raise typer.Exit(2)


@dataclass(frozen=True)
class _MappedOperand:
    """A parsed ``name:/path`` operand selecting one mapped source."""

    spelling: str
    name: str
    path: str


class _BinaryWriter(Protocol):
    """The write/flush surface of a binary stdout stream."""

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...


def _binary_stdout() -> _BinaryWriter:
    """Return the process binary stdout buffer, or raise if unavailable."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        message = "stdout has no binary buffer"
        raise OSError(message)
    return buffer


def _write_binary(stdout: _BinaryWriter, payload: bytes) -> None:
    """Write one complete byte payload or reject a short write."""
    written = stdout.write(payload)
    if written != len(payload):
        message = "short write"
        raise OSError(message)


@dataclass(frozen=True)
class _Failure:
    operand: _MappedOperand
    backend_error: Exception | None = None


class _CommandFailureError(Exception):
    """One expected command or output failure crossing the invocation boundary."""

    def __init__(
        self,
        operand: _MappedOperand | None = None,
        error: Exception | None = None,
    ) -> None:
        self.operand = operand
        self.error = error


def _render_operand_diagnostic(
    command: str,
    operand: _MappedOperand,
    category: str,
) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered_operand = _render_diagnostic_value(operand.spelling)
    typer.echo(f"{prefix} {rendered_operand}: {category}", err=True, color=True)


def _render_failure(command: str, failure: _Failure) -> None:
    if failure.backend_error is None:
        _render_operand_diagnostic(command, failure.operand, "incompatible result")
    else:
        _render_backend_failure(command, failure.operand, failure.backend_error)


def _render_backend_failure(
    command: str,
    operand: _MappedOperand,
    error: Exception,
) -> None:
    if isinstance(error, FileNotFoundError):
        category = "not found"
    elif isinstance(error, PermissionError):
        category = "permission denied"
    elif isinstance(error, NotADirectoryError):
        category = "not a directory"
    elif isinstance(error, NotImplementedError):
        category = "unsupported operation"
    else:
        rendered_class = _render_diagnostic_value(type(error).__name__)
        rendered_message = _render_diagnostic_value(str(error))
        category = f"backend failure ({rendered_class}): {rendered_message}"
    _render_operand_diagnostic(command, operand, category)


def _render_output_failure(command: str, error: Exception) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered_class = _render_diagnostic_value(type(error).__name__)
    rendered_message = _render_diagnostic_value(str(error))
    typer.echo(
        f"{prefix} output: output failure ({rendered_class}): {rendered_message}",
        err=True,
        color=True,
    )


async def _run_mapped_command(
    command: str,
    operands: tuple[_MappedOperand, ...],
    sources: Mapping[str, AsyncFilesystemSource],
    operation: Callable[[Mapping[str, AsyncFileSystem]], Awaitable[None]],
) -> None:
    """Acquire referenced sources, run one command, and own final status."""
    invocation = _SourceInvocation(command, sources)
    acquired = False
    failure: _CommandFailureError | None = None
    try:
        filesystems = await invocation.acquire(
            tuple(dict.fromkeys(operand.name for operand in operands))
        )
        acquired = filesystems is not None
        if filesystems is not None:
            await operation(filesystems)
    except _CommandFailureError as error:
        failure = error
        if error.operand is not None:
            _render_failure(command, _Failure(error.operand, error.error))
        elif error.error is not None and not isinstance(
            error.error,
            BrokenPipeError,
        ):
            _render_output_failure(command, error.error)
    finally:
        cleanup_failed = await invocation.close_with_command_error(
            failure.error if failure is not None else None
        )

    if not acquired or failure is not None or cleanup_failed:
        if (
            failure is not None
            and isinstance(failure.error, BrokenPipeError)
            and not cleanup_failed
        ):
            raise typer.Exit(_BROKEN_PIPE_EXIT_CODE)
        raise typer.Exit(1)


async def _run_single_operand_text(
    command: str,
    operand: _MappedOperand,
    sources: Mapping[str, AsyncFilesystemSource],
    operation: Callable[[AsyncFileSystem], Awaitable[str | _Failure]],
) -> None:
    """Run one mapped async operation with buffered text output and cleanup."""
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure: _Failure | None = None
    output_error: Exception | None = None
    try:
        filesystems = await invocation.acquire((operand.name,))
        if filesystems is not None:
            result = await operation(filesystems[operand.name])
            if isinstance(result, _Failure):
                failure = result
                _render_failure(command, failure)
            elif result:
                try:
                    typer.echo(result, nl=False, color=True)
                except BrokenPipeError as error:
                    output_error = error
                except Exception as error:  # noqa: BLE001 - output boundary.
                    output_error = error
                    _render_output_failure(command, error)
            succeeded = failure is None and output_error is None
    finally:
        command_error = failure.backend_error if failure is not None else output_error
        cleanup_failed = await invocation.close_with_command_error(command_error)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)


def _sorted_known(known_names: Collection[str]) -> list[str]:
    """Return the configured source names in locale order for diagnostics."""
    return sorted(
        known_names, key=lambda candidate: (locale.strxfrm(candidate), candidate)
    )


def _parse_mapped_operand(
    command: str,
    argument: str,
    known_names: Collection[str],
) -> _MappedOperand:
    """Parse and validate one ``name:/path`` operand against the known sources."""
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
        rendered_operand = _render_diagnostic_value(argument)
        rendered_names = ", ".join(
            _render_diagnostic_value(candidate)
            for candidate in _sorted_known(known_names)
        )
        _usage_error(
            command,
            f"{rendered_operand}: unknown filesystem (known: {rendered_names})",
        )

    return _MappedOperand(spelling=argument, name=name, path=path)


def _preflight_single_mapped_operand(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _MappedOperand:
    """Parse one mapped operand for a command with no options."""
    operand = None
    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        if operand is not None:
            _usage_error(command, "extra operand")
        operand = _parse_mapped_operand(command, argument, known_names)
    if operand is None:
        _usage_error(command, "missing mapped filesystem operand")
    return operand
