"""Embedded Typer application for mapped async fsspec sources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import TypeAlias

import typer
from fsspec import AbstractFileSystem

from ._ls import _LsCommand, _raw_arguments, _run_ls

AsyncFilesystemSource: TypeAlias = Callable[
    [], AbstractAsyncContextManager[AbstractFileSystem]
]


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

        @self.typer_app.callback()
        def root() -> None:
            pass

        @self.typer_app.command(
            "ls",
            cls=_LsCommand,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def ls(ctx: typer.Context) -> None:
            raw_arguments = _raw_arguments(ctx)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                typer.echo(
                    "ls: cannot run from an active event loop",
                    err=True,
                    color=True,
                )
                raise typer.Exit(1)
            asyncio.run(_run_ls(raw_arguments, self._sources))
