"""Embedded Typer application for mapped async fsspec sources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, TypeAlias

import typer
from fsspec import AbstractFileSystem

if TYPE_CHECKING:
    from typer.core import TyperCommand

from ._basename import (
    _BasenameCommand,
    _run_basename,
)
from ._basename import (
    _raw_arguments as _basename_raw_arguments,
)
from ._cat import _CatCommand, _run_cat
from ._cp import _CpCommand, _run_cp
from ._cp import _raw_arguments as _cp_raw_arguments
from ._diagnostics import _render_diagnostic_prefix
from ._dirname import (
    _DirnameCommand,
    _run_dirname,
)
from ._dirname import (
    _raw_arguments as _dirname_raw_arguments,
)
from ._ls import _LsCommand, _raw_arguments, _run_ls
from ._mkdir import _MkdirCommand, _run_mkdir
from ._rm import _raw_arguments as _rm_raw_arguments
from ._rm import _RmCommand, _run_rm
from ._rmdir import _raw_arguments as _rmdir_raw_arguments
from ._rmdir import _RmdirCommand, _run_rmdir
from ._unlink import _raw_arguments as _unlink_raw_arguments
from ._unlink import _run_unlink, _UnlinkCommand

AsyncFilesystemSource: TypeAlias = Callable[
    [], AbstractAsyncContextManager[AbstractFileSystem]
]
_BASENAME_COMMAND = "basename"
_DIRNAME_COMMAND = "dirname"
_LS_COMMAND = "ls"
_CAT_COMMAND = "cat"
_CP_COMMAND = "cp"
_CP_HELP = (
    "Copy one same-source mapped file with byte verification. A passing result "
    "proves target resolution, replacement, bytes, diagnostics, cleanup, and "
    "partial-state reporting only — not POSIX mode, ownership, link identity, "
    "or timestamps."
)
_MKDIR_COMMAND = "mkdir"
_MKDIR_HELP = (
    "Create directories with optional parent creation (-p). A passing result "
    "claims only source-default creation semantics, not POSIX mode or umask "
    "behavior."
)
_RMDIR_COMMAND = "rmdir"
_RM_COMMAND = "rm"
_UNLINK_COMMAND = "unlink"
_RM_HELP = (
    "Remove source-reported files. rm -f ignores files already missing before "
    "removal, including with zero operands. Directories, recursion, and "
    "interactive permission prompts are unavailable."
)
_SOURCE_FREE_CONTEXT = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}


def _register_source_free_command(
    app: typer.Typer,
    name: str,
    command_cls: type[TyperCommand],
    runner: Callable[[str, tuple[str, ...]], None],
    raw_arguments: Callable[[typer.Context], tuple[str, ...]],
) -> None:
    @app.command(name, cls=command_cls, context_settings=_SOURCE_FREE_CONTEXT)
    def handler(ctx: typer.Context) -> None:
        runner(name, raw_arguments(ctx))


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

        _register_source_free_command(
            self.typer_app,
            _BASENAME_COMMAND,
            _BasenameCommand,
            _run_basename,
            _basename_raw_arguments,
        )
        _register_source_free_command(
            self.typer_app,
            _DIRNAME_COMMAND,
            _DirnameCommand,
            _run_dirname,
            _dirname_raw_arguments,
        )

        @self.typer_app.command(
            _LS_COMMAND,
            cls=_LsCommand,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def ls(ctx: typer.Context) -> None:
            raw_arguments = _raw_arguments(ctx)
            _ensure_no_active_event_loop(_LS_COMMAND)
            asyncio.run(_run_ls(_LS_COMMAND, raw_arguments, self._sources))

        @self.typer_app.command(
            _CAT_COMMAND,
            cls=_CatCommand,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def cat(ctx: typer.Context) -> None:
            raw_arguments = _raw_arguments(ctx)
            _ensure_no_active_event_loop(_CAT_COMMAND)
            asyncio.run(_run_cat(_CAT_COMMAND, raw_arguments, self._sources))

        @self.typer_app.command(
            _CP_COMMAND,
            cls=_CpCommand,
            help=_CP_HELP,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def cp(ctx: typer.Context) -> None:
            raw_arguments = _cp_raw_arguments(ctx)
            _ensure_no_active_event_loop(_CP_COMMAND)
            asyncio.run(_run_cp(_CP_COMMAND, raw_arguments, self._sources))

        @self.typer_app.command(
            _MKDIR_COMMAND,
            cls=_MkdirCommand,
            help=_MKDIR_HELP,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def mkdir(ctx: typer.Context) -> None:
            raw_arguments = _raw_arguments(ctx)
            _ensure_no_active_event_loop(_MKDIR_COMMAND)
            asyncio.run(_run_mkdir(_MKDIR_COMMAND, raw_arguments, self._sources))

        @self.typer_app.command(
            _RMDIR_COMMAND,
            cls=_RmdirCommand,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def rmdir(ctx: typer.Context) -> None:
            raw_arguments = _rmdir_raw_arguments(ctx)
            _ensure_no_active_event_loop(_RMDIR_COMMAND)
            asyncio.run(_run_rmdir(_RMDIR_COMMAND, raw_arguments, self._sources))

        @self.typer_app.command(
            _RM_COMMAND,
            cls=_RmCommand,
            help=_RM_HELP,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def rm(ctx: typer.Context) -> None:
            raw_arguments = _rm_raw_arguments(ctx)
            _ensure_no_active_event_loop(_RM_COMMAND)
            asyncio.run(_run_rm(_RM_COMMAND, raw_arguments, self._sources))

        @self.typer_app.command(
            _UNLINK_COMMAND,
            cls=_UnlinkCommand,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def unlink(ctx: typer.Context) -> None:
            raw_arguments = _unlink_raw_arguments(ctx)
            _ensure_no_active_event_loop(_UNLINK_COMMAND)
            asyncio.run(_run_unlink(_UNLINK_COMMAND, raw_arguments, self._sources))
