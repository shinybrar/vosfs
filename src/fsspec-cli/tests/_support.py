"""Recording filesystem sources for public-seam command tests."""

import asyncio
from types import MappingProxyType
from typing import NoReturn

from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App, AsyncFilesystemSource
from typer.testing import CliRunner, Result


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def _invoke_ls(
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(App(sources).typer_app, ["ls", *arguments])


class _RecordingFileSystem(AsyncFileSystem):
    cachable = False

    def __init__(
        self,
        source: "_RecordingSource",
        source_id: int,
    ) -> None:
        super().__init__(asynchronous=True)
        self.source = source
        self.source_id = source_id

    async def _info(self, path: str, **kwargs: object) -> object:
        del kwargs
        self.source.events.append(
            ("info", self.source_id, path, id(asyncio.get_running_loop()))
        )
        if self.source.info_error is not None:
            raise self.source.info_error
        return self.source.info_result

    async def _ls(
        self,
        path: str,
        detail: bool = True,  # noqa: FBT002 - matches the fsspec hook signature.
        **kwargs: object,
    ) -> object:
        del kwargs
        self.source.events.append(
            (
                "ls",
                self.source_id,
                path,
                detail,
                id(asyncio.get_running_loop()),
            )
        )
        if self.source.ls_error is not None:
            raise self.source.ls_error
        return self.source.ls_result


class _RecordingSource:
    def __init__(  # noqa: PLR0913 - configurable external-boundary recording fake.
        self,
        events: list[tuple[object, ...]],
        info_result: object = MappingProxyType({"type": "file"}),
        *,
        info_error: BaseException | None = None,
        ls_result: object = None,
        ls_error: BaseException | None = None,
        exit_result: object = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.info_result = info_result
        self.info_error = info_error
        self.ls_result = ls_result
        self.ls_error = ls_error
        self.exit_result = exit_result
        self.exit_error = exit_error
        self.exit_calls: list[tuple[object, object, object]] = []
        self.contexts: list[_RecordingContext] = []
        self.call_count = 0

    def __call__(self) -> "_RecordingContext":
        self.call_count += 1
        self.events.append(("factory", self.call_count))
        context = _RecordingContext(self, self.call_count)
        self.contexts.append(context)
        return context


class _RecordingContext:
    def __init__(
        self,
        source: _RecordingSource,
        source_id: int,
    ) -> None:
        self.source = source
        self.source_id = source_id
        self.filesystem = _RecordingFileSystem(source, source_id)

    async def __aenter__(self) -> _RecordingFileSystem:
        self.source.events.append(
            ("enter", self.source_id, id(asyncio.get_running_loop()))
        )
        return self.filesystem

    async def __aexit__(self, *exc_info: object) -> object:
        self.source.exit_calls.append(exc_info)
        self.source.events.append(
            ("exit", self.source_id, id(asyncio.get_running_loop()))
        )
        if self.source.exit_error is not None:
            raise self.source.exit_error
        return self.source.exit_result
