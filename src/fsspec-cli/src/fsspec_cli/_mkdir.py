"""Raw Typer parsing and async execution for ``mkdir``."""

from __future__ import annotations

import locale
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, cast

import typer
from typer.core import TyperCommand

from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context

    from ._app import AsyncFilesystemSource

_RAW_ARGUMENTS = "fsspec_cli.raw_arguments"


@dataclass(frozen=True)
class _MappedOperand:
    spelling: str
    name: str
    path: str


@dataclass(frozen=True)
class _MkdirRequest:
    create_parents: bool
    operands: tuple[_MappedOperand, ...]


@dataclass(frozen=True)
class _Failure:
    operand: _MappedOperand
    backend_error: Exception | None = None
    uncertain: bool = False


class _MkdirCommand(TyperCommand):
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
    return cast("tuple[str, ...]", ctx.meta[_RAW_ARGUMENTS])


def _usage_error(command: str, diagnostic: str) -> NoReturn:
    prefix = _render_diagnostic_prefix(command)
    typer.echo(f"{prefix} {diagnostic}", err=True, color=True)
    raise typer.Exit(2)


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _MkdirRequest:
    create_parents = False
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
            if all(character == "p" for character in argument[1:]):
                create_parents = True
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

        operands.append(_MappedOperand(spelling=argument, name=name, path=path))
        seen_operand = True
        if options_active:
            options_active = False

    if not operands:
        _usage_error(command, "missing mapped filesystem operand")

    return _MkdirRequest(create_parents=create_parents, operands=tuple(operands))


async def _run_mkdir(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    request = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failures: tuple[_Failure, ...] = ()
    try:
        names = dict.fromkeys(operand.name for operand in request.operands)
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            failures = await _trace_operands(
                request,
                filesystems,
                create_parents=request.create_parents,
            )
            for failure in failures:
                _render_failure(command, failure)
            succeeded = not failures
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
        command_error = backend_error
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


def _render_operand_diagnostic(
    command: str,
    operand: _MappedOperand,
    category: str,
) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered_operand = _render_diagnostic_value(operand.spelling)
    typer.echo(f"{prefix} {rendered_operand}: {category}", err=True, color=True)


def _backend_category(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "not found"
    if isinstance(error, FileExistsError):
        return "file exists"
    if isinstance(error, PermissionError):
        return "permission denied"
    if isinstance(error, NotADirectoryError):
        return "not a directory"
    if isinstance(error, NotImplementedError):
        return "unsupported operation"
    rendered_class = _render_diagnostic_value(type(error).__name__)
    rendered_message = _render_diagnostic_value(str(error))
    return f"backend failure ({rendered_class}): {rendered_message}"


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
