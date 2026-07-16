# ruff: noqa: INP001
"""PROTOTYPE/THROWAWAY: answer the Issue #80 plain-``ls`` matrix question.

Delete this code after its interoperability evidence is captured. It is not
production ``fsspec-cli`` implementation.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fsspec import AbstractFileSystem


class App:
    """Expose the smallest Typer surface needed by prototype slice 1."""

    def __init__(self, filesystems: Mapping[str, AbstractFileSystem]) -> None:
        """Build the prototype around host-supplied filesystem instances."""
        self.typer_app = typer.Typer(add_completion=False)

        @self.typer_app.callback()
        def _root() -> None:
            """Create the prototype command group."""

        @self.typer_app.command("ls")
        def _ls(operand: str) -> None:
            """List one mapped directory through public fsspec methods."""
            name, path = operand.split(":", 1)
            filesystem = filesystems[name]
            entry = filesystem.info(path)
            if entry["type"] == "file":
                typer.echo(operand)
                return
            for child in filesystem.ls(path, detail=False):
                typer.echo(posixpath.basename(child))
