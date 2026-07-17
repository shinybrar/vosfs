"""Recording filesystem sources for public-seam command tests."""

import asyncio
from collections.abc import Mapping
from pathlib import Path
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


def _invoke_mkdir(
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(App(sources).typer_app, ["mkdir", *arguments])


def _invoke_rmdir(
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(App(sources).typer_app, ["rmdir", *arguments])


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
        self._pending_mkdir_verify: set[str] = set()
        self._pending_rmdir_verify: set[str] = set()
        self._created_dirs: set[str] = set()
        self._removed_paths: set[str] = set()

    def _consume_post_info(self, path: str) -> object | None:
        if path in self.source.post_info_by_path:
            scripted = self.source.post_info_by_path[path]
            if isinstance(scripted, BaseException):
                raise scripted
            return scripted
        return None

    def _post_rmdir_info(self, path: str) -> object:
        self._pending_rmdir_verify.discard(path)
        try:
            scripted = self._consume_post_info(path)
        except BaseException:
            self._removed_paths.discard(path)
            raise
        if scripted is not None:
            self._removed_paths.discard(path)
            return scripted
        if self.source.post_info_error is not None:
            self._removed_paths.discard(path)
            raise self.source.post_info_error
        raise FileNotFoundError(path)

    def _post_mkdir_info(self, path: str) -> object | None:
        self._pending_mkdir_verify.discard(path)
        scripted = self._consume_post_info(path)
        if scripted is not None:
            return scripted
        if self.source.post_info_result is not None:
            return self.source.post_info_result
        return None

    async def _info(self, path: str, **kwargs: object) -> object:
        del kwargs
        self.source.events.append(
            ("info", self.source_id, path, id(asyncio.get_running_loop()))
        )
        if path in self._pending_rmdir_verify:
            return self._post_rmdir_info(path)
        if path in self._removed_paths:
            raise FileNotFoundError(path)
        if path in self.source.info_by_path:
            scripted = self.source.info_by_path[path]
            if isinstance(scripted, BaseException):
                raise scripted
            return scripted
        if path in self._pending_mkdir_verify:
            verified = self._post_mkdir_info(path)
            if verified is not None:
                return verified
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
        if path in self.source.ls_by_path:
            scripted = self.source.ls_by_path[path]
            if isinstance(scripted, BaseException):
                raise scripted
            return scripted
        if self.source.ls_error is not None:
            raise self.source.ls_error
        return self.source.ls_result

    async def _get_file(
        self,
        rpath: str,
        lpath: str,
        **kwargs: object,
    ) -> None:
        del kwargs
        self.source.events.append(
            (
                "get_file",
                self.source_id,
                rpath,
                id(asyncio.get_running_loop()),
            )
        )
        if rpath in self.source.get_file_by_path:
            scripted = self.source.get_file_by_path[rpath]
            if isinstance(scripted, BaseException):
                raise scripted
            if callable(scripted):
                scripted(lpath)
                return
            with Path(lpath).open("wb") as handle:  # noqa: ASYNC230
                handle.write(scripted)
            return
        if self.source.get_file_error is not None:
            raise self.source.get_file_error
        if self.source.get_file_hook is not None:
            self.source.get_file_hook(rpath, lpath)
            return
        with Path(lpath).open("wb") as handle:  # noqa: ASYNC230
            handle.write(self.source.get_file_content)

    async def _mkdir(
        self,
        path: str,
        create_parents: bool = True,  # noqa: FBT002 - matches the fsspec hook signature.
        **kwargs: object,
    ) -> None:
        del kwargs
        self.source.events.append(
            (
                "mkdir",
                self.source_id,
                path,
                create_parents,
                id(asyncio.get_running_loop()),
            )
        )
        if path in self.source.mkdir_by_path:
            scripted = self.source.mkdir_by_path[path]
            if isinstance(scripted, BaseException):
                raise scripted
        elif path in self._created_dirs:
            raise FileExistsError(path)
        if self.source.mkdir_error is not None:
            raise self.source.mkdir_error
        self._created_dirs.add(path)
        self._pending_mkdir_verify.add(path)

    async def _rmdir(self, path: str, **kwargs: object) -> None:
        del kwargs
        self.source.events.append(
            ("rmdir", self.source_id, path, id(asyncio.get_running_loop()))
        )
        if path in self.source.rmdir_by_path:
            scripted = self.source.rmdir_by_path[path]
            if isinstance(scripted, BaseException):
                raise scripted
        elif self.source.rmdir_error is not None:
            raise self.source.rmdir_error
        self._removed_paths.add(path)
        self._pending_rmdir_verify.add(path)


class _RecordingSource:
    def __init__(  # noqa: PLR0913 - configurable external-boundary recording fake.
        self,
        events: list[tuple[object, ...]],
        info_result: object = MappingProxyType({"type": "file"}),
        *,
        info_error: BaseException | None = None,
        ls_result: object = None,
        ls_error: BaseException | None = None,
        mkdir_error: BaseException | None = None,
        rmdir_error: BaseException | None = None,
        post_info_result: object | None = MappingProxyType({"type": "directory"}),
        post_info_error: BaseException | None = None,
        exit_result: object = None,
        exit_error: BaseException | None = None,
        info_by_path: Mapping[str, object] | None = None,
        ls_by_path: Mapping[str, object] | None = None,
        mkdir_by_path: Mapping[str, object] | None = None,
        rmdir_by_path: Mapping[str, object] | None = None,
        post_info_by_path: Mapping[str, object] | None = None,
        get_file_content: bytes = b"",
        get_file_error: BaseException | None = None,
        get_file_by_path: Mapping[str, object] | None = None,
        get_file_hook: object | None = None,
    ) -> None:
        self.events = events
        self.info_result = info_result
        self.info_error = info_error
        self.ls_result = ls_result
        self.ls_error = ls_error
        self.mkdir_error = mkdir_error
        self.rmdir_error = rmdir_error
        self.post_info_result = post_info_result
        self.post_info_error = post_info_error
        self.exit_result = exit_result
        self.exit_error = exit_error
        self.info_by_path = info_by_path or {}
        self.ls_by_path = ls_by_path or {}
        self.mkdir_by_path = mkdir_by_path or {}
        self.rmdir_by_path = rmdir_by_path or {}
        self.post_info_by_path = post_info_by_path or {}
        self.get_file_content = get_file_content
        self.get_file_error = get_file_error
        self.get_file_by_path = get_file_by_path or {}
        self.get_file_hook = get_file_hook
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
