# ruff: noqa: INP001
"""PROTOTYPE/THROWAWAY: answer the Issue #80 plain-``ls`` matrix question.

Delete this code after its interoperability evidence is captured. It is not
production ``fsspec-cli`` implementation.
"""

from __future__ import annotations

import locale
from collections.abc import Mapping
from typing import TYPE_CHECKING

import typer
from typer.core import TyperCommand

if TYPE_CHECKING:
    import click
    from fsspec import AbstractFileSystem


_RAW_ARGV_KEY = "fsspec_cli.raw_argv"


class _IncompatibleResultError(Exception):
    """Mark a backend value that cannot satisfy the command contract."""


def _render_diagnostic_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _collation_key(value: str) -> tuple[str, str]:
    try:
        transformed = locale.strxfrm(value)
    except ValueError:
        transformed = value
    return transformed, value


def _runtime_failure_category(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "not found"
    if isinstance(error, PermissionError):
        return "permission denied"
    if isinstance(error, NotADirectoryError):
        return "not a directory"
    if isinstance(error, NotImplementedError):
        return "unsupported operation"
    error_class = _render_diagnostic_value(type(error).__name__)
    message = _render_diagnostic_value(str(error))
    return f"backend failure ({error_class}): {message}"


def _entry_type(entry: object) -> str:
    if not isinstance(entry, Mapping):
        raise _IncompatibleResultError
    entry_type = entry.get("type")
    if not isinstance(entry_type, str) or entry_type not in {"file", "directory"}:
        raise _IncompatibleResultError
    return entry_type


def _directory_basenames(path: str, children: object) -> list[str]:
    if not isinstance(children, list):
        raise _IncompatibleResultError

    comparison_path = path.rstrip("/")
    prefix = f"{comparison_path}/" if comparison_path else "/"
    basenames: list[str] = []
    for child in children:
        if not isinstance(child, str) or not child.startswith(prefix):
            raise _IncompatibleResultError
        basename = child[len(prefix) :]
        if not basename or "/" in basename or "\0" in basename or "\n" in basename:
            raise _IncompatibleResultError
        basenames.append(basename)
    return basenames


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

    def __init__(  # noqa: C901, PLR0915
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
        def _ls(ctx: typer.Context) -> None:  # noqa: C901, PLR0912, PLR0915
            """List mapped files and directories through public fsspec methods."""
            raw_argv = ctx.meta[_RAW_ARGV_KEY]
            options_enabled = True
            almost_all = False
            operands: list[str] = []
            for argument in raw_argv:
                if options_enabled and argument == "--":
                    options_enabled = False
                    continue
                if options_enabled and argument != "-" and argument.startswith("-"):
                    if argument[1:] and set(argument[1:]) == {"A"}:
                        almost_all = True
                        continue
                    rendered_argument = _render_diagnostic_value(argument)
                    typer.echo(
                        f"ls: {rendered_argument}: unsupported option",
                        err=True,
                    )
                    raise typer.Exit(code=2)
                operands.append(argument)

            if not operands:
                typer.echo("ls: missing mapped filesystem operand", err=True)
                raise typer.Exit(code=2)

            parsed_operands: list[tuple[str, AbstractFileSystem, str]] = []
            for operand in operands:
                rendered_operand = _render_diagnostic_value(operand)
                if "\0" in operand or "\n" in operand:
                    typer.echo(
                        f"ls: {rendered_operand}: invalid mapped filesystem operand",
                        err=True,
                    )
                    raise typer.Exit(code=2)
                name, separator, path = operand.partition(":")
                if not separator or not name or not path.startswith("/"):
                    typer.echo(
                        f"ls: {rendered_operand}: invalid mapped filesystem operand",
                        err=True,
                    )
                    raise typer.Exit(code=2)
                if name not in filesystems:
                    known_names = ", ".join(
                        _render_diagnostic_value(known)
                        for known in sorted(
                            filesystems,
                            key=_collation_key,
                        )
                    )
                    typer.echo(
                        f"ls: {rendered_operand}: unknown filesystem "
                        f"(known: {known_names})",
                        err=True,
                    )
                    raise typer.Exit(code=2)
                parsed_operands.append((operand, filesystems[name], path))

            file_results: list[str] = []
            directory_results: list[tuple[str, list[str]]] = []
            runtime_diagnostics: list[str] = []

            for operand, filesystem, path in parsed_operands:
                try:
                    entry = filesystem.info(path)
                    if _entry_type(entry) == "file":
                        file_results.append(operand)
                        continue
                    listed_basenames = _directory_basenames(
                        path,
                        filesystem.ls(path, detail=False),
                    )
                    basenames = []
                    for basename in listed_basenames:
                        if basename in {".", ".."}:
                            continue
                        if almost_all or not basename.startswith("."):
                            basenames.append(basename)
                    directory_results.append(
                        (
                            operand,
                            sorted(
                                basenames,
                                key=_collation_key,
                            ),
                        )
                    )
                except _IncompatibleResultError:
                    rendered_operand = _render_diagnostic_value(operand)
                    runtime_diagnostics.append(
                        f"ls: {rendered_operand}: incompatible result"
                    )
                    continue
                except Exception as error:  # noqa: BLE001 - required fallback
                    rendered_operand = _render_diagnostic_value(operand)
                    category = _runtime_failure_category(error)
                    runtime_diagnostics.append(f"ls: {rendered_operand}: {category}")
                    continue

            file_results.sort(key=_collation_key)
            directory_results.sort(key=lambda result: _collation_key(result[0]))

            if len(operands) == 1 and not runtime_diagnostics:
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
            if blocks:
                typer.echo("\n\n".join(blocks))
            for diagnostic in runtime_diagnostics:
                typer.echo(diagnostic, err=True)
            if runtime_diagnostics:
                raise typer.Exit(code=1)
