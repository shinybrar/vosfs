"""Embedded Typer application for mapped async fsspec sources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager
from functools import partial
from typing import TYPE_CHECKING, Any, TypeAlias

import typer
from fsspec import AbstractFileSystem

from ._basename import _run_basename
from ._cat import _run_cat
from ._command import _raw_arguments, _RawCommand
from ._cp import _run_cp
from ._diagnostics import _render_diagnostic_prefix
from ._dirname import _run_dirname
from ._du import _DuCommand, _run_du
from ._find import _FindCommand, _run_find
from ._ls import _run_ls
from ._mkdir import _run_mkdir
from ._mv import _run_mv
from ._rm import _run_rm
from ._rmdir import _run_rmdir
from ._size import _run_size, _SizeCommand
from ._stat import _run_stat, _StatCommand
from ._test import _run_test, _TestCommand
from ._unlink import _run_unlink

if TYPE_CHECKING:
    from typer.core import TyperCommand

AsyncFilesystemSource: TypeAlias = Callable[
    [], AbstractAsyncContextManager[AbstractFileSystem]
]
_SourceFreeRunner: TypeAlias = Callable[[str, tuple[str, ...]], None]
_AsyncRunner: TypeAlias = Callable[
    [str, tuple[str, ...], Mapping[str, AsyncFilesystemSource]],
    Coroutine[Any, Any, None],
]

_COMMAND_CONTEXT = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}

# Commands that render their raw arguments without acquiring a source.
_SOURCE_FREE_COMMANDS: tuple[tuple[str, str, _SourceFreeRunner], ...] = (
    ("basename", "Strip directory and suffix from a path", _run_basename),
    ("dirname", "Strip the last component from a path", _run_dirname),
)
# Commands that acquire mapped sources and run on the invocation event loop.
_ASYNC_COMMANDS: tuple[tuple[str, str, _AsyncRunner, type[TyperCommand]], ...] = (
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
    ("cp", "Copy a file (no recursion)", _run_cp, _RawCommand),
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


class App:
    """An embedded command application backed by named filesystem sources."""

    typer_app: typer.Typer

    def __init__(self, sources: Mapping[str, AsyncFilesystemSource]) -> None:
        """Snapshot configured sources for this application."""
        self._sources = dict(sources)
        if not self._sources:
            msg = "at least one async filesystem source is required"
            raise ValueError(msg)
        for name in self._sources:
            _validate_source_name(name)

        self.typer_app = typer.Typer(add_completion=False)
        self._register_commands()

    def _register_commands(self) -> None:
        @self.typer_app.callback()
        def root() -> None:
            pass

        for name, help_text, source_free_runner in _SOURCE_FREE_COMMANDS:
            self._register_source_free(name, help_text, source_free_runner)
        for name, help_text, async_runner, command_cls in _ASYNC_COMMANDS:
            self._register_async(name, help_text, async_runner, command_cls)

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

    def _register_async(
        self,
        name: str,
        help_text: str,
        runner: _AsyncRunner,
        command_cls: type[TyperCommand],
    ) -> None:
        @self.typer_app.command(
            name,
            cls=command_cls,
            help=help_text,
            context_settings=_COMMAND_CONTEXT,
        )
        def handler(ctx: typer.Context) -> None:
            raw_arguments = _raw_arguments(ctx)
            _ensure_no_active_event_loop(name)
            asyncio.run(runner(name, raw_arguments, self._sources))
