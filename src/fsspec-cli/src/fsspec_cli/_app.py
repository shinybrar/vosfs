"""Embedded Typer application for mapped async fsspec sources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias, TypedDict

import typer
from fsspec import AbstractFileSystem
from typer.core import TyperCommand

from ._basename import _run_basename
from ._cat import _run_cat
from ._command import (
    _parse_mapped_operand,
    _raw_arguments,
    _RawCommand,
    _usage_error,
)
from ._cp import _run_cp
from ._diagnostics import _render_diagnostic_prefix
from ._dirname import _run_dirname
from ._du import _DuCommand, _run_du
from ._find import _FindCommand, _run_find
from ._head_tail import _run_head, _run_tail
from ._info import _run_info
from ._ls import _run_ls
from ._mkdir import _run_mkdir
from ._mv import _run_mv
from ._rm import _run_rm
from ._rmdir import _run_rmdir
from ._size import _run_size
from ._stat import _run_stat
from ._test import _run_test
from ._tree import _run_tree, _TreeCommand
from ._unlink import _run_unlink

AsyncFilesystemSource: TypeAlias = Callable[
    [], AbstractAsyncContextManager[AbstractFileSystem]
]


class RecursionCapabilities(TypedDict, total=False):
    """Application policy for recursive core commands."""

    copy: bool
    remove: bool


class AppCapabilities(TypedDict, total=False):
    """Application-level core command policy."""

    recursion: RecursionCapabilities


CommandCallback: TypeAlias = Callable[..., None]


@dataclass(frozen=True)
class CommandContext:
    """Immutable application context available to command callbacks."""

    sources: Mapping[str, AsyncFilesystemSource]


_AsyncRunner: TypeAlias = Callable[
    [str, tuple[str, ...], Mapping[str, AsyncFilesystemSource]],
    Coroutine[Any, Any, None],
]
_AsyncCommand: TypeAlias = tuple[str, str, _AsyncRunner, type[TyperCommand]]

_COMMAND_CONTEXT = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}


@dataclass(frozen=True)
class _Capabilities:
    recursive_copy: bool = True
    recursive_remove: bool = False


def _snapshot_capabilities(capabilities: AppCapabilities | None) -> _Capabilities:
    if capabilities is None:
        return _Capabilities()
    if not isinstance(capabilities, Mapping):
        msg = "capabilities must be a mapping"
        raise TypeError(msg)
    for name in capabilities:
        if name != "recursion":
            msg = f"capabilities.{name}: unknown capability"
            raise ValueError(msg)

    recursion = capabilities.get("recursion", {})
    if not isinstance(recursion, Mapping):
        msg = "capabilities.recursion must be a mapping"
        raise TypeError(msg)
    for name in recursion:
        if name not in {"copy", "remove"}:
            msg = f"capabilities.recursion.{name}: unknown capability"
            raise ValueError(msg)
    for name in ("copy", "remove"):
        if name in recursion and type(recursion[name]) is not bool:
            msg = f"capabilities.recursion.{name} must be a bool"
            raise TypeError(msg)
    return _Capabilities(
        recursive_copy=recursion.get("copy", True),
        recursive_remove=recursion.get("remove", False),
    )


# Commands that acquire mapped sources and run on the invocation event loop.
_ASYNC_COMMANDS: tuple[_AsyncCommand, ...] = (
    ("ls", "List directory contents", _run_ls, _RawCommand),
    (
        "ll",
        "List directory contents in long form",
        partial(_run_ls, long_by_default=True),
        _RawCommand,
    ),
    ("cat", "Concatenate files to standard output", _run_cat, _RawCommand),
    ("du", "Estimate file space usage", _run_du, _DuCommand),
    ("find", "Find files recursively", _run_find, _FindCommand),
    ("tree", "Display a recursive directory tree", _run_tree, _TreeCommand),
    ("cp", "Copy files or one directory with -R or -r", _run_cp, _RawCommand),
    ("mv", "Move or rename files", _run_mv, _RawCommand),
    ("mkdir", "Create directories", _run_mkdir, _RawCommand),
    ("rmdir", "Remove empty directories", _run_rmdir, _RawCommand),
    ("rm", "Remove files", _run_rm, _RawCommand),
    ("unlink", "Remove a single file", _run_unlink, _RawCommand),
)


def _validate_source_name(name: object) -> None:
    if not isinstance(name, str):
        msg = "async filesystem source names must be strings"
        raise TypeError(msg)
    if not name or any(character in name for character in (":", "\0", "\n")):
        msg = (
            "async filesystem source names must be non-empty and contain no colon, "
            "NUL, or newline"
        )
        raise ValueError(msg)
    if name.startswith("-"):
        msg = "async filesystem source names must not start with '-'"
        raise ValueError(msg)


def _ensure_no_active_event_loop(command: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    prefix = _render_diagnostic_prefix(command)
    typer.echo(
        f"{prefix} cannot run from an active event loop",
        err=True,
        color=True,
    )
    raise typer.Exit(1)


def _register_async_command(
    typer_app: typer.Typer,
    sources: Mapping[str, AsyncFilesystemSource],
    command: _AsyncCommand,
) -> None:
    name, help_text, runner, command_cls = command

    @typer_app.command(
        name,
        cls=command_cls,
        help=help_text,
        context_settings=_COMMAND_CONTEXT,
    )
    def handler(ctx: typer.Context) -> None:
        raw_arguments = _raw_arguments(ctx)
        _ensure_no_active_event_loop(name)
        asyncio.run(runner(name, raw_arguments, sources))


class App:
    """Embedded core commands plus explicitly selected extensions."""

    typer_app: typer.Typer

    def __init__(
        self,
        sources: Mapping[str, AsyncFilesystemSource],
        *,
        capabilities: AppCapabilities | None = None,
        extensions: Sequence[CommandCallback] = (),
    ) -> None:
        """Snapshot sources and register the requested command surface."""
        self._sources = MappingProxyType(dict(sources))
        if not self._sources:
            msg = "at least one async filesystem source is required"
            raise ValueError(msg)
        for name in self._sources:
            _validate_source_name(name)
        self._capabilities = _snapshot_capabilities(capabilities)

        self.typer_app = typer.Typer(add_completion=False)
        self._register_commands()
        for extension in extensions:
            self.typer_app.command()(extension)

    def _register_commands(self) -> None:  # noqa: C901 - central command surface.
        @self.typer_app.callback()
        def root(ctx: typer.Context) -> None:
            ctx.obj = CommandContext(self._sources)

        @self.typer_app.command()
        def head(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
            count: Annotated[int, typer.Option("-c", metavar="N", min=0)],
        ) -> None:
            """Display leading bytes."""
            mapped = _parse_mapped_operand("head", operand, self._sources)
            _ensure_no_active_event_loop("head")
            asyncio.run(_run_head("head", count, mapped, self._sources))

        @self.typer_app.command()
        def tail(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
            count: Annotated[int, typer.Option("-c", metavar="N", min=0)],
        ) -> None:
            """Display trailing bytes."""
            mapped = _parse_mapped_operand("tail", operand, self._sources)
            _ensure_no_active_event_loop("tail")
            asyncio.run(_run_tail("tail", count, mapped, self._sources))

        @self.typer_app.command()
        def basename(
            operand: Annotated[str, typer.Argument(metavar="OPERAND")],
            suffix: Annotated[
                str | None,
                typer.Argument(metavar="SUFFIX"),
            ] = None,
        ) -> None:
            """Strip directory and suffix from a path."""
            _run_basename("basename", operand, suffix)

        @self.typer_app.command()
        def dirname(
            operand: Annotated[str, typer.Argument(metavar="OPERAND")],
        ) -> None:
            """Strip the last component from a path."""
            _run_dirname("dirname", operand)

        @self.typer_app.command()
        def info(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
        ) -> None:
            """Display normalized file information."""
            mapped = _parse_mapped_operand("info", operand, self._sources)
            _ensure_no_active_event_loop("info")
            asyncio.run(_run_info("info", mapped, self._sources))

        @self.typer_app.command()
        def size(
            operands: Annotated[
                list[str],
                typer.Argument(metavar="name:/path"),
            ],
        ) -> None:
            """Display exact file sizes."""
            mapped = tuple(
                _parse_mapped_operand("size", operand, self._sources)
                for operand in operands
            )
            _ensure_no_active_event_loop("size")
            asyncio.run(_run_size("size", mapped, self._sources))

        @self.typer_app.command()
        def test(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
            exists: Annotated[bool, typer.Option("-e")] = False,  # noqa: FBT002
            directory: Annotated[bool, typer.Option("-d")] = False,  # noqa: FBT002
            file: Annotated[bool, typer.Option("-f")] = False,  # noqa: FBT002
        ) -> None:
            """Evaluate a file predicate."""
            mapped = _parse_mapped_operand("test", operand, self._sources)
            selected: list[Literal["e", "d", "f"]] = [
                predicate
                for predicate, enabled in (
                    ("e", exists),
                    ("d", directory),
                    ("f", file),
                )
                if enabled
            ]
            if len(selected) != 1:
                _usage_error("test", "exactly one predicate selector is required")
            _ensure_no_active_event_loop("test")
            asyncio.run(_run_test("test", selected[0], mapped, self._sources))

        @self.typer_app.command()
        def stat(
            operands: Annotated[
                list[str],
                typer.Argument(metavar="name:/path"),
            ],
        ) -> None:
            """Display file status."""
            mapped = tuple(
                _parse_mapped_operand("stat", operand, self._sources)
                for operand in operands
            )
            _ensure_no_active_event_loop("stat")
            asyncio.run(_run_stat("stat", mapped, self._sources))

        for registered_command in _ASYNC_COMMANDS:
            command = registered_command
            if registered_command[0] == "cp":
                help_text = (
                    "Copy files or one directory with -R or -r"
                    if self._capabilities.recursive_copy
                    else "Copy a file (no recursion)"
                )
                command = (
                    registered_command[0],
                    help_text,
                    partial(
                        _run_cp,
                        recursive_enabled=self._capabilities.recursive_copy,
                    ),
                    registered_command[3],
                )
            elif registered_command[0] == "rm":
                help_text = (
                    "Remove files or directories with guarded -R or -r"
                    if self._capabilities.recursive_remove
                    else "Remove files; -d removes empty directories"
                )
                command = (
                    registered_command[0],
                    help_text,
                    partial(
                        _run_rm,
                        recursive_enabled=self._capabilities.recursive_remove,
                    ),
                    registered_command[3],
                )
            _register_async_command(
                self.typer_app,
                self._sources,
                command,
            )
