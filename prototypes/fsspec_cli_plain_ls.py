# ruff: noqa: INP001
"""PROTOTYPE/THROWAWAY: answer the Issue #80 plain-``ls`` matrix question.

Delete this code after its interoperability evidence is captured. It is not
production ``fsspec-cli`` implementation.
"""

from __future__ import annotations

import locale
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
        def _ls(operands: list[str]) -> None:
            """List mapped files and directories through public fsspec methods."""
            file_results: list[str] = []
            directory_results: list[tuple[str, list[str]]] = []

            for operand in operands:
                name, path = operand.split(":", 1)
                filesystem = filesystems[name]
                entry = filesystem.info(path)
                if entry["type"] == "file":
                    file_results.append(operand)
                    continue
                basenames = [
                    posixpath.basename(child)
                    for child in filesystem.ls(path, detail=False)
                    if not posixpath.basename(child).startswith(".")
                ]
                directory_results.append(
                    (
                        operand,
                        sorted(
                            basenames,
                            key=lambda child: (locale.strxfrm(child), child),
                        ),
                    )
                )

            if len(operands) == 1:
                if file_results:
                    typer.echo(file_results[0])
                    return
                for basename in directory_results[0][1]:
                    typer.echo(basename)
                return

            blocks: list[str] = []
            if file_results:
                blocks.append("\n".join(file_results))
            blocks.extend(
                "\n".join((f"{operand}:", *basenames))
                for operand, basenames in directory_results
            )
            typer.echo("\n\n".join(blocks))
