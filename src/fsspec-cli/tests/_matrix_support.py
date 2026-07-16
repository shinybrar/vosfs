"""Shared probes for real-source command-matrix tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App
from typer.testing import CliRunner, Result

if TYPE_CHECKING:
    from types import TracebackType

_FilesystemT = TypeVar("_FilesystemT", bound=AsyncFileSystem)
_FilesystemFactory = Callable[[], _FilesystemT]
_FilesystemCloser = Callable[[_FilesystemT], Awaitable[None]]


@dataclass(frozen=True)
class LifecycleEvent:
    stage: Literal["factory", "enter", "close", "exit"]
    source_id: int
    loop_id: int


@dataclass(frozen=True)
class ExitCall:
    source_id: int
    exc_type: type[BaseException] | None
    exception: BaseException | None
    traceback: TracebackType | None
    loop_id: int


@dataclass(frozen=True)
class FilesystemCall:
    operation: Literal["info", "ls"]
    source_id: int
    path: str
    detail: bool | None
    kwargs: Mapping[str, object]
    loop_id: int


def _block_network(monkeypatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        message = "hermetic command-matrix tests prohibit network access"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network)


class _ProbedSource(Generic[_FilesystemT]):
    def __init__(
        self,
        factory: _FilesystemFactory[_FilesystemT],
        *,
        close: _FilesystemCloser[_FilesystemT] | None = None,
    ) -> None:
        self._factory = factory
        self._close = close
        self.lifecycle: list[LifecycleEvent] = []
        self.exit_calls: list[ExitCall] = []
        self.calls: list[FilesystemCall] = []
        self.info_results: list[tuple[int, object]] = []
        self.ls_results: list[tuple[int, object]] = []
        self.errors: list[tuple[int, str, Exception]] = []
        self.filesystems: list[_FilesystemT] = []
        self.close_calls: list[LifecycleEvent] = []
        self._source_count = 0

    def __call__(self) -> AbstractAsyncContextManager[_FilesystemT]:
        self._source_count += 1
        source_id = self._source_count
        self.lifecycle.append(
            LifecycleEvent("factory", source_id, id(asyncio.get_running_loop()))
        )
        return _ProbedContext(self, source_id)

    def instrument(self, source_id: int, filesystem: _FilesystemT) -> None:
        original_info = filesystem._info
        original_ls = filesystem._ls

        async def info(path: str, **kwargs: object) -> object:
            self.calls.append(
                FilesystemCall(
                    "info",
                    source_id,
                    path,
                    None,
                    kwargs,
                    id(asyncio.get_running_loop()),
                )
            )
            try:
                result = await original_info(path, **kwargs)
            except Exception as error:
                self.errors.append((source_id, "info", error))
                raise
            self.info_results.append((source_id, result))
            return result

        async def ls(
            path: str,
            detail: bool = True,  # noqa: FBT002 - mirrors the fsspec hook.
            **kwargs: object,
        ) -> object:
            self.calls.append(
                FilesystemCall(
                    "ls",
                    source_id,
                    path,
                    detail,
                    kwargs,
                    id(asyncio.get_running_loop()),
                )
            )
            try:
                result = await original_ls(path, detail=detail, **kwargs)
            except Exception as error:
                self.errors.append((source_id, "ls", error))
                raise
            self.ls_results.append((source_id, result))
            return result

        setattr(filesystem, "_info", info)  # noqa: B010 - instrument this instance.
        setattr(filesystem, "_ls", ls)  # noqa: B010 - instrument this instance.


class _ProbedContext(
    AbstractAsyncContextManager[_FilesystemT],
    Generic[_FilesystemT],
):
    def __init__(self, source: _ProbedSource[_FilesystemT], source_id: int) -> None:
        self._source = source
        self._source_id = source_id
        self._filesystem: _FilesystemT | None = None

    async def __aenter__(self) -> _FilesystemT:
        self._filesystem = self._source._factory()
        self._source.filesystems.append(self._filesystem)
        self._source.lifecycle.append(
            LifecycleEvent(
                "enter",
                self._source_id,
                id(asyncio.get_running_loop()),
            )
        )
        self._source.instrument(self._source_id, self._filesystem)
        return self._filesystem

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        filesystem = self._filesystem
        assert filesystem is not None
        if self._source._close is not None:
            await self._source._close(filesystem)
            self._source.close_calls.append(
                LifecycleEvent(
                    "close",
                    self._source_id,
                    id(asyncio.get_running_loop()),
                )
            )
        loop_id = id(asyncio.get_running_loop())
        self._source.exit_calls.append(
            ExitCall(
                self._source_id,
                exc_type,
                exc,
                traceback,
                loop_id,
            )
        )
        self._source.lifecycle.append(LifecycleEvent("exit", self._source_id, loop_id))


def _invoke(app: App, arguments: list[str]) -> Result:
    return CliRunner().invoke(app.typer_app, ["ls", *arguments])


def _exercise_locked_profile(
    source_name: str,
    source: _ProbedSource[_FilesystemT],
    path: str,
) -> None:
    app = App({source_name: source})
    operand = f"{source_name}:{path}"
    missing_operand = f"{operand}/missing"

    plain = _invoke(app, [operand])
    almost_all = _invoke(app, ["-A", operand])
    missing = _invoke(app, [missing_operand])

    assert (plain.exit_code, plain.stdout, plain.stderr) == (
        0,
        "guide.md\nnotes.txt\n",
        "",
    )
    assert (almost_all.exit_code, almost_all.stdout, almost_all.stderr) == (
        0,
        ".hidden\nguide.md\nnotes.txt\n",
        "",
    )
    assert (missing.exit_code, missing.stdout, missing.stderr) == (
        1,
        "",
        f"ls: {missing_operand}: not found\n",
    )
    assert [event.stage for event in source.lifecycle] == [
        "factory",
        "enter",
        "exit",
        "factory",
        "enter",
        "exit",
        "factory",
        "enter",
        "exit",
    ]
    assert [event.source_id for event in source.lifecycle] == [
        1,
        1,
        1,
        2,
        2,
        2,
        3,
        3,
        3,
    ]
    assert [call.operation for call in source.calls] == [
        "info",
        "ls",
        "info",
        "ls",
        "info",
    ]
    assert [call.source_id for call in source.calls] == [1, 1, 2, 2, 3]
    assert [call.path for call in source.calls] == [
        path,
        path,
        path,
        path,
        f"{path}/missing",
    ]
    assert [call.detail for call in source.calls] == [None, False, None, False, None]
    assert all(not call.kwargs for call in source.calls)
    assert [source_id for source_id, _result in source.info_results] == [1, 2]
    assert [source_id for source_id, _result in source.ls_results] == [1, 2]
    assert all(
        isinstance(result, Mapping) and result.get("type") == "directory"
        for _source_id, result in source.info_results
    )
    assert all(
        isinstance(result, list)
        and set(result) == {f"{path}/.hidden", f"{path}/guide.md", f"{path}/notes.txt"}
        for _source_id, result in source.ls_results
    )
    assert len(source.errors) == 1
    error_source_id, operation, error = source.errors[0]
    assert (error_source_id, operation) == (3, "info")
    assert isinstance(error, FileNotFoundError)

    first_exit, second_exit, failing_exit = source.exit_calls
    assert first_exit.exc_type is None
    assert first_exit.exception is None
    assert first_exit.traceback is None
    assert second_exit.exc_type is None
    assert second_exit.exception is None
    assert second_exit.traceback is None
    assert failing_exit.exc_type is FileNotFoundError
    assert failing_exit.exception is error
    assert failing_exit.traceback is not None
    for source_id in (1, 2, 3):
        loop_ids = [
            event.loop_id for event in source.lifecycle if event.source_id == source_id
        ]
        loop_ids.extend(
            call.loop_id for call in source.calls if call.source_id == source_id
        )
        exit_call = next(
            call for call in source.exit_calls if call.source_id == source_id
        )
        loop_ids.append(exit_call.loop_id)
        assert len(set(loop_ids)) == 1
