"""Embedded Typer application for mapped async fsspec sources."""

from __future__ import annotations

import asyncio
import locale
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, TypeAlias

import typer
from fsspec import AbstractFileSystem
from typer.core import TyperCommand

if TYPE_CHECKING:
    from typer._click import Context

AsyncFilesystemSource: TypeAlias = Callable[
    [], AbstractAsyncContextManager[AbstractFileSystem]
]

_RAW_ARGUMENTS = "fsspec_cli.raw_arguments"


class _RawArgumentsCommand(TyperCommand):
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


def _render_diagnostic_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
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
            cls=_RawArgumentsCommand,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        def ls(ctx: typer.Context) -> None:
            raw_arguments = ctx.meta[_RAW_ARGUMENTS]
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                typer.echo("ls: cannot run from an active event loop", err=True)
                raise typer.Exit(1)
            asyncio.run(self._run_ls(raw_arguments))

    async def _run_ls(self, raw_arguments: tuple[str, ...]) -> None:
        options_active = True
        operand_count = 0
        for argument in raw_arguments:
            if options_active and argument == "--":
                options_active = False
                continue
            if options_active and argument.startswith("-") and argument != "-":
                if all(character == "A" for character in argument[1:]):
                    continue
                rendered = _render_diagnostic_value(argument)
                typer.echo(f"ls: {rendered}: unsupported option", err=True)
                raise typer.Exit(2)
            operand_count += 1
            name, separator, path = argument.partition(":")
            if (
                not name
                or not separator
                or not path.startswith("/")
                or "\0" in argument
                or "\n" in argument
            ):
                rendered = _render_diagnostic_value(argument)
                typer.echo(
                    f"ls: {rendered}: invalid mapped filesystem operand",
                    err=True,
                )
                raise typer.Exit(2)
            if name not in self._sources:
                known_names = sorted(
                    self._sources,
                    key=lambda known: (locale.strxfrm(known), known),
                )
                rendered_operand = _render_diagnostic_value(argument)
                rendered_names = ", ".join(
                    _render_diagnostic_value(known) for known in known_names
                )
                typer.echo(
                    f"ls: {rendered_operand}: unknown filesystem "
                    f"(known: {rendered_names})",
                    err=True,
                )
                raise typer.Exit(2)
        if operand_count == 0:
            typer.echo("ls: missing mapped filesystem operand", err=True)
            raise typer.Exit(2)
