"""Embedded Typer application for mapped async fsspec sources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias, TypedDict

import typer
from fsspec import AbstractFileSystem

from ._basename import _run_basename
from ._cat import _run_cat, _StdinOperand
from ._command import (
    _MappedOperand,
    _parse_mapped_operand,
    _usage_error,
)
from ._cp import _cp_plan, _run_cp
from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._dirname import _run_dirname
from ._du import _DuRequest, _run_du
from ._find import _FindRequest, _run_find
from ._head_tail import _run_head, _run_tail
from ._info import _run_info
from ._ls import _LsRequest, _run_ls
from ._mkdir import _MkdirRequest, _run_mkdir
from ._mv import _plan_mv, _run_mv
from ._path import _has_dot_segment, _has_final_dot_segment, _is_root
from ._rm import _RmRequest, _run_rm
from ._rmdir import _run_rmdir
from ._size import _run_size
from ._stat import _run_stat
from ._test import _run_test
from ._tree import _run_tree, _TreeRequest
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


def _run_async_command(
    command: str,
    operation: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """Run one preflighted command coroutine from a synchronous callback."""
    _ensure_no_active_event_loop(command)
    asyncio.run(operation())


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

    def _register_commands(  # noqa: C901, PLR0915 - central command surface.
        self,
    ) -> None:
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
            _run_async_command(
                "head",
                lambda: _run_head("head", count, mapped, self._sources),
            )

        @self.typer_app.command()
        def tail(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
            count: Annotated[int, typer.Option("-c", metavar="N", min=0)],
        ) -> None:
            """Display trailing bytes."""
            mapped = _parse_mapped_operand("tail", operand, self._sources)
            _run_async_command(
                "tail",
                lambda: _run_tail("tail", count, mapped, self._sources),
            )

        @self.typer_app.command()
        def cat(
            operands: Annotated[
                list[str] | None,
                typer.Argument(metavar="name:/path|-"),
            ] = None,
        ) -> None:
            """Concatenate files to standard output."""
            parsed = tuple(
                _StdinOperand()
                if operand == "-"
                else _parse_mapped_operand("cat", operand, self._sources)
                for operand in operands or ("-",)
            )
            _run_async_command(
                "cat",
                lambda: _run_cat("cat", parsed, self._sources),
            )

        def run_cp(operands: list[str], *, recursive: bool) -> None:
            plan = _cp_plan("cp", tuple(operands), self._sources, recursive=recursive)
            _run_async_command(
                "cp",
                lambda: _run_cp("cp", plan, self._sources),
            )

        if self._capabilities.recursive_copy:

            @self.typer_app.command()
            def cp(
                operands: Annotated[
                    list[str],
                    typer.Argument(metavar="SOURCE... DESTINATION"),
                ],
                *,
                recursive: Annotated[bool, typer.Option("-R", "-r")] = False,
            ) -> None:
                """Copy files or one directory."""
                run_cp(operands, recursive=recursive)

        else:

            @self.typer_app.command()
            def cp(
                operands: Annotated[
                    list[str],
                    typer.Argument(metavar="SOURCE... DESTINATION"),
                ],
            ) -> None:
                """Copy one or more files."""
                run_cp(operands, recursive=False)

        @self.typer_app.command()
        def mkdir(
            operands: Annotated[
                list[str],
                typer.Argument(metavar="name:/path"),
            ],
            *,
            parents: Annotated[bool, typer.Option("-p")] = False,
        ) -> None:
            """Create directories."""
            mapped = tuple(
                _parse_mapped_operand("mkdir", operand, self._sources)
                for operand in operands
            )
            _run_async_command(
                "mkdir",
                lambda: _run_mkdir(
                    "mkdir",
                    _MkdirRequest(create_parents=parents, operands=mapped),
                    self._sources,
                ),
            )

        def destructive_operand(command: str, spelling: str) -> _MappedOperand:
            operand = _parse_mapped_operand(command, spelling, self._sources)
            if _is_root(operand.path) or _has_final_dot_segment(operand.path):
                rendered = _render_diagnostic_value(spelling)
                _usage_error(command, f"{rendered}: rejected path")
            return operand

        @self.typer_app.command()
        def rmdir(
            operands: Annotated[
                list[str],
                typer.Argument(metavar="name:/path"),
            ],
        ) -> None:
            """Remove empty directories."""
            mapped = tuple(
                destructive_operand("rmdir", operand) for operand in operands
            )
            _run_async_command(
                "rmdir",
                lambda: _run_rmdir("rmdir", mapped, self._sources),
            )

        @self.typer_app.command()
        def unlink(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
        ) -> None:
            """Remove a single file."""
            mapped = destructive_operand("unlink", operand)
            _run_async_command(
                "unlink",
                lambda: _run_unlink("unlink", mapped, self._sources),
            )

        def run_rm(
            operands: list[str] | None,
            *,
            directory: bool,
            force: bool,
            verbose: int,
            recursive: bool = False,
        ) -> None:
            if verbose > 1:
                _usage_error("rm", "-v: may be supplied once")
            if directory and (force or verbose or recursive):
                _usage_error("rm", "-d: cannot combine with other options")
            if not recursive and force and verbose:
                _usage_error("rm", "-f: cannot combine with -v")
            spellings = operands or []
            if not spellings and not force:
                _usage_error("rm", "missing mapped filesystem operand")
            mapped = tuple(
                _parse_mapped_operand("rm", spelling, self._sources)
                for spelling in spellings
            )
            for spelling, operand in zip(spellings, mapped, strict=True):
                rejected = _is_root(operand.path) or (
                    _has_dot_segment(operand.path)
                    if recursive
                    else _has_final_dot_segment(operand.path)
                )
                if rejected:
                    rendered = _render_diagnostic_value(spelling)
                    _usage_error("rm", f"{rendered}: rejected path")
            _run_async_command(
                "rm",
                lambda: _run_rm(
                    "rm",
                    _RmRequest(
                        force=force,
                        directory=directory,
                        recursive=recursive,
                        verbose=bool(verbose),
                        operands=mapped,
                    ),
                    self._sources,
                ),
            )

        if self._capabilities.recursive_remove:

            @self.typer_app.command(name="rm")
            def recursive_rm(
                operands: Annotated[
                    list[str] | None,
                    typer.Argument(metavar="name:/path"),
                ] = None,
                *,
                directory: Annotated[
                    bool,
                    typer.Option("-d", help="Remove empty directories."),
                ] = False,
                force: Annotated[
                    bool,
                    typer.Option("-f", help="Ignore missing operands."),
                ] = False,
                verbose: Annotated[
                    int,
                    typer.Option("-v", count=True, help="Print removed operands."),
                ] = 0,
                recursive: Annotated[
                    bool,
                    typer.Option(
                        "-R",
                        "-r",
                        help="Remove directory trees with guarded traversal.",
                    ),
                ] = False,
            ) -> None:
                """Remove files or directories with guarded -R or -r."""
                run_rm(
                    operands,
                    directory=directory,
                    force=force,
                    verbose=verbose,
                    recursive=recursive,
                )

        else:

            @self.typer_app.command(name="rm")
            def rm(
                operands: Annotated[
                    list[str] | None,
                    typer.Argument(metavar="name:/path"),
                ] = None,
                *,
                directory: Annotated[
                    bool,
                    typer.Option("-d", help="Remove empty directories."),
                ] = False,
                force: Annotated[
                    bool,
                    typer.Option("-f", help="Ignore missing operands."),
                ] = False,
                verbose: Annotated[
                    int,
                    typer.Option("-v", count=True, help="Print removed operands."),
                ] = 0,
            ) -> None:
                """Remove files; -d removes empty directories."""
                run_rm(
                    operands,
                    directory=directory,
                    force=force,
                    verbose=verbose,
                )

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
            _run_async_command(
                "info",
                lambda: _run_info("info", mapped, self._sources),
            )

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
            _run_async_command(
                "size",
                lambda: _run_size("size", mapped, self._sources),
            )

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
            _run_async_command(
                "test",
                lambda: _run_test("test", selected[0], mapped, self._sources),
            )

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
            _run_async_command(
                "stat",
                lambda: _run_stat("stat", mapped, self._sources),
            )

        def run_listing(
            command: Literal["ls", "ll"],
            operands: list[str],
            *,
            include_almost_all: bool,
            long_listing: bool,
            human_readable: bool,
        ) -> None:
            mapped = tuple(
                _parse_mapped_operand(command, operand, self._sources)
                for operand in operands
            )
            if human_readable and not long_listing:
                _usage_error(command, "-h: requires long listing")
            _run_async_command(
                command,
                lambda: _run_ls(
                    command,
                    _LsRequest(
                        include_almost_all=include_almost_all,
                        long_listing=long_listing,
                        human_readable=human_readable,
                        operands=mapped,
                    ),
                    self._sources,
                ),
            )

        @self.typer_app.command()
        def ls(
            operands: Annotated[
                list[str],
                typer.Argument(metavar="name:/path"),
            ],
            *,
            include_almost_all: Annotated[bool, typer.Option("-A")] = False,
            long_listing: Annotated[bool, typer.Option("-l")] = False,
            human_readable: Annotated[bool, typer.Option("-h")] = False,
        ) -> None:
            """List directory contents."""
            run_listing(
                "ls",
                operands,
                include_almost_all=include_almost_all,
                long_listing=long_listing,
                human_readable=human_readable,
            )

        @self.typer_app.command()
        def ll(
            operands: Annotated[
                list[str],
                typer.Argument(metavar="name:/path"),
            ],
            *,
            include_almost_all: Annotated[bool, typer.Option("-A")] = False,
            _long_listing: Annotated[bool, typer.Option("-l")] = False,
            human_readable: Annotated[bool, typer.Option("-h")] = False,
        ) -> None:
            """List directory contents in long form."""
            run_listing(
                "ll",
                operands,
                include_almost_all=include_almost_all,
                long_listing=True,
                human_readable=human_readable,
            )

        @self.typer_app.command()
        def du(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
            *,
            summarize: Annotated[bool, typer.Option("-s")] = False,
            human_readable: Annotated[bool, typer.Option("-h")] = False,
        ) -> None:
            """Estimate file space usage."""
            mapped = _parse_mapped_operand("du", operand, self._sources)
            _run_async_command(
                "du",
                lambda: _run_du(
                    "du",
                    _DuRequest(
                        summarize=summarize,
                        human_readable=human_readable,
                        operand=mapped,
                    ),
                    self._sources,
                ),
            )

        @self.typer_app.command()
        def find(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
            maxdepth: Annotated[
                int | None,
                typer.Option("--maxdepth", metavar="N", min=0),
            ] = None,
            kind: Annotated[
                Literal["f", "d"],
                typer.Option("--type", metavar="f|d"),
            ] = "f",
        ) -> None:
            """Find files recursively."""
            mapped = _parse_mapped_operand("find", operand, self._sources)
            _run_async_command(
                "find",
                lambda: _run_find(
                    "find",
                    _FindRequest(
                        maxdepth=maxdepth,
                        kind=kind,
                        operand=mapped,
                    ),
                    self._sources,
                ),
            )

        @self.typer_app.command()
        def tree(
            operand: Annotated[str, typer.Argument(metavar="name:/path")],
            maxdepth: Annotated[
                int | None,
                typer.Option("--maxdepth", metavar="N", min=0),
            ] = None,
        ) -> None:
            """Display a recursive directory tree."""
            mapped = _parse_mapped_operand("tree", operand, self._sources)
            _run_async_command(
                "tree",
                lambda: _run_tree(
                    "tree",
                    _TreeRequest(maxdepth=maxdepth, operand=mapped),
                    self._sources,
                ),
            )

        @self.typer_app.command()
        def mv(
            operands: Annotated[
                list[str],
                typer.Argument(
                    metavar="name:/path",
                    help="One or more source files followed by one destination.",
                ),
            ],
        ) -> None:
            """Move or rename files on one mapped filesystem."""
            if len(operands) < 2:  # noqa: PLR2004 - command arity.
                message = "requires at least one source and one destination"
                raise typer.BadParameter(message, param_hint="name:/path")
            mapped = tuple(
                _parse_mapped_operand("mv", operand, self._sources)
                for operand in operands
            )
            plan = _plan_mv("mv", mapped)
            _run_async_command(
                "mv",
                lambda: _run_mv("mv", plan, self._sources),
            )
