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
from typer.core import TyperCommand

if TYPE_CHECKING:
    from collections.abc import Mapping

    import click
    from fsspec import AbstractFileSystem


_RAW_ARGV_KEY = "fsspec_cli.raw_argv"


def _requests_framework_help(arguments: tuple[str, ...]) -> bool:
    for argument in arguments:
        if argument == "--":
            return False
        if argument == "--help":
            return True
    return False


class _RawArgvCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Preserve raw command arguments before Click parsing."""
        raw_argv = tuple(args)
        ctx.meta[_RAW_ARGV_KEY] = raw_argv
        if _requests_framework_help(raw_argv):
            return super().parse_args(ctx, args)
        return super().parse_args(ctx, ["--", *args])


class App:
    """Expose the smallest Typer surface needed by prototype slice 1."""

    def __init__(  # noqa: C901
        self,
        filesystems: Mapping[str, AbstractFileSystem],
    ) -> None:
        """Build the prototype around host-supplied filesystem instances."""
        self.typer_app = typer.Typer(add_completion=False)

        @self.typer_app.callback()
        def _root() -> None:
            """Create the prototype command group."""

        @self.typer_app.command(
            "ls",
            cls=_RawArgvCommand,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def _ls(ctx: typer.Context) -> None:  # noqa: C901, PLR0912
            """List mapped files and directories through public fsspec methods."""
            raw_argv = ctx.meta[_RAW_ARGV_KEY]
            options_enabled = True
            operands: list[str] = []
            for argument in raw_argv:
                if options_enabled and argument == "--":
                    options_enabled = False
                    continue
                if options_enabled and argument.startswith("-"):
                    typer.echo(f"ls: {argument}: unsupported option", err=True)
                    raise typer.Exit(code=2)
                operands.append(argument)

            if not operands:
                typer.echo("ls: missing mapped filesystem operand", err=True)
                raise typer.Exit(code=2)

            parsed_operands: list[tuple[str, AbstractFileSystem, str]] = []
            for operand in operands:
                name, separator, path = operand.partition(":")
                if not separator or not name or not path.startswith("/"):
                    typer.echo(
                        f"ls: {operand}: invalid mapped filesystem operand",
                        err=True,
                    )
                    raise typer.Exit(code=2)
                if name not in filesystems:
                    known_names = ", ".join(
                        sorted(
                            filesystems,
                            key=lambda known: (locale.strxfrm(known), known),
                        )
                    )
                    typer.echo(
                        f"ls: {operand}: unknown filesystem (known: {known_names})",
                        err=True,
                    )
                    raise typer.Exit(code=2)
                parsed_operands.append((operand, filesystems[name], path))

            file_results: list[str] = []
            directory_results: list[tuple[str, list[str]]] = []

            for operand, filesystem, path in parsed_operands:
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
