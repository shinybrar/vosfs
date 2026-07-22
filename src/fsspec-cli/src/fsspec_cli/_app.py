"""Embedded Typer application for mapped async fsspec sources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, TypedDict

import typer
from fsspec import AbstractFileSystem
from typer.core import TyperCommand

from ._basename import _run_basename
from ._cat import _run_cat
from ._command import _raw_arguments, _RawCommand
from ._cp import _run_cp
from ._diagnostics import _render_diagnostic_prefix
from ._dirname import _run_dirname
from ._du import _DuCommand, _run_du
from ._find import _FindCommand, _run_find
from ._head_tail import _HeadCommand, _run_head, _run_tail, _TailCommand
from ._info import _InfoCommand, _run_info
from ._ls import _run_ls
from ._mkdir import _run_mkdir
from ._mv import _run_mv
from ._rm import _run_rm
from ._rmdir import _run_rmdir
from ._size import _run_size, _SizeCommand
from ._stat import _run_stat, _StatCommand
from ._test import _run_test, _TestCommand
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


class CommandExtension(Protocol):
    """One opt-in command registration against snapshotted sources."""

    def register(
        self,
        typer_app: typer.Typer,
        sources: Mapping[str, AsyncFilesystemSource],
    ) -> None: ...


_SourceFreeRunner: TypeAlias = Callable[[str, tuple[str, ...]], None]
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


# Commands that render their raw arguments without acquiring a source.
_SOURCE_FREE_COMMANDS: tuple[tuple[str, str, _SourceFreeRunner], ...] = (
    ("basename", "Strip directory and suffix from a path", _run_basename),
    ("dirname", "Strip the last component from a path", _run_dirname),
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
    ("size", "Display exact file sizes", _run_size, _SizeCommand),
    ("test", "Evaluate a file predicate", _run_test, _TestCommand),
    ("head", "Display leading bytes", _run_head, _HeadCommand),
    ("tail", "Display trailing bytes", _run_tail, _TailCommand),
    ("tree", "Display a recursive directory tree", _run_tree, _TreeCommand),
    ("info", "Display normalized file information", _run_info, _InfoCommand),
    ("cp", "Copy files or one directory with -R or -r", _run_cp, _RawCommand),
    ("mv", "Move or rename files", _run_mv, _RawCommand),
    ("mkdir", "Create directories", _run_mkdir, _RawCommand),
    ("rmdir", "Remove empty directories", _run_rmdir, _RawCommand),
    ("rm", "Remove files", _run_rm, _RawCommand),
    ("unlink", "Remove a single file", _run_unlink, _RawCommand),
    ("stat", "Display file status", _run_stat, _StatCommand),
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
        extensions: Sequence[CommandExtension] = (),
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
            extension.register(self.typer_app, self._sources)

    def _register_commands(self) -> None:
        @self.typer_app.callback()
        def root() -> None:
            pass

        for name, help_text, source_free_runner in _SOURCE_FREE_COMMANDS:
            self._register_source_free(name, help_text, source_free_runner)
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

    def _register_source_free(
        self,
        name: str,
        help_text: str,
        runner: _SourceFreeRunner,
    ) -> None:
        @self.typer_app.command(
            name,
            cls=_RawCommand,
            help=help_text,
            context_settings=_COMMAND_CONTEXT,
        )
        def handler(ctx: typer.Context) -> None:
            runner(name, _raw_arguments(ctx))
