"""Invocation-owned async filesystem source lifecycle."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import TYPE_CHECKING, TypeAlias

import typer
from fsspec.asyn import AsyncFileSystem

from ._diagnostics import _render_diagnostic_value

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from fsspec import AbstractFileSystem

    from ._app import AsyncFilesystemSource

_ExcInfo: TypeAlias = tuple[
    type[BaseException] | None,
    BaseException | None,
    TracebackType | None,
]
_EMPTY_EXC_INFO: _ExcInfo = (None, None, None)


def _source_exception(name: str, stage: str, error: Exception) -> None:
    rendered_name = _render_diagnostic_value(name)
    rendered_class = _render_diagnostic_value(type(error).__name__)
    rendered_message = _render_diagnostic_value(str(error))
    typer.echo(
        f"ls: {rendered_name}: source {stage} failure "
        f"({rendered_class}): {rendered_message}",
        err=True,
    )


class _SourceInvocation:
    """Acquire, expose, and release sources for one command invocation."""

    def __init__(self, sources: Mapping[str, AsyncFilesystemSource]) -> None:
        self._sources = sources
        self._entered: list[
            tuple[str, AbstractAsyncContextManager[AbstractFileSystem]]
        ] = []
        self._filesystems: dict[str, AsyncFileSystem] = {}
        self._failure_exc_info: _ExcInfo = _EMPTY_EXC_INFO
        self.failed = False

    def __getitem__(self, name: str) -> AsyncFileSystem:
        return self._filesystems[name]

    async def acquire(self, names: Iterable[str]) -> None:
        """Acquire distinct mapped names sequentially in the given order."""
        for name in names:
            try:
                manager = self._sources[name]()
            except Exception as error:  # noqa: BLE001 - classify source failures.
                _source_exception(name, "factory", error)
                self._remember_failure(error)
                return

            if not (
                isinstance(manager, AbstractAsyncContextManager)
                and callable(getattr(manager, "__aenter__", None))
                and callable(getattr(manager, "__aexit__", None))
            ):
                rendered_name = _render_diagnostic_value(name)
                typer.echo(
                    "ls: "
                    f"{rendered_name}: source factory returned incompatible "
                    "async context manager",
                    err=True,
                )
                self.failed = True
                return

            try:
                filesystem = await manager.__aenter__()
            except Exception as error:  # noqa: BLE001 - classify source failures.
                _source_exception(name, "entry", error)
                self._remember_failure(error)
                return

            self._entered.append((name, manager))
            if not (
                isinstance(filesystem, AsyncFileSystem)
                and filesystem.async_impl is True
                and filesystem.asynchronous is True
            ):
                rendered_name = _render_diagnostic_value(name)
                typer.echo(
                    f"ls: {rendered_name}: source yielded incompatible "
                    "async filesystem",
                    err=True,
                )
                self.failed = True
                return
            self._filesystems[name] = filesystem

    def _remember_failure(self, error: Exception) -> None:
        self.failed = True
        self._failure_exc_info = (type(error), error, error.__traceback__)

    async def close(self, active_exc_info: _ExcInfo) -> bool:
        """Exit every entered source and preserve control-flow precedence."""
        exit_exc_info = (
            active_exc_info
            if active_exc_info[1] is not None
            else self._failure_exc_info
        )
        primary_control = (
            active_exc_info[1]
            if active_exc_info[1] is not None
            and not isinstance(active_exc_info[1], Exception)
            else None
        )
        cleanup_control = None
        cleanup_failed = False
        entered, self._entered = self._entered, []

        for name, manager in reversed(entered):
            try:
                await manager.__aexit__(*exit_exc_info)
            except Exception as error:  # noqa: BLE001, PERF203 - every exit runs.
                _source_exception(name, "exit", error)
                cleanup_failed = True
            except BaseException as error:  # noqa: BLE001 - preserve control flow.
                if primary_control is None and cleanup_control is None:
                    cleanup_control = error

        if cleanup_control is not None:
            raise cleanup_control
        return cleanup_failed
