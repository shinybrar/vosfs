"""Hermetic Local, Memory, and native-vosfs evidence for ``info``."""

from __future__ import annotations

import os
import pprint
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
import pytest
from fsspec.asyn import AsyncFileSystem
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem
from fsspec_cli import App
from typer.testing import CliRunner

from vosfs import VOSpaceFileSystem

from ._matrix_support import _block_network

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from types import TracebackType

_BASE_URL = "https://example.test/arc"
_AUTHORITY = "example.test!vault"
_CAPABILITIES = f"""<?xml version="1.0" encoding="UTF-8"?>
<vosi:capabilities xmlns:vosi="http://www.ivoa.net/xml/VOSICapabilities/v1.0"
                   xmlns:vs="http://www.ivoa.net/xml/VODataService/v1.1"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <capability standardID="ivo://ivoa.net/std/VOSpace/v2.0#nodes">
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="base">{_BASE_URL}/nodes</accessURL>
    </interface>
  </capability>
</vosi:capabilities>
""".encode()
_VOS_INFO = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/docs/report.txt">
  <vos:properties>
    <vos:property uri="ivo://ivoa.net/vospace/core#length">3</vos:property>
    <vos:property uri="ivo://ivoa.net/vospace/core#mtime">2026-07-17T18:00:00Z</vos:property>
    <vos:property uri="ivo://ivoa.net/vospace/core#MD5">abc123</vos:property>
    <vos:property uri="ivo://ivoa.net/vospace/core#contenttype">text/plain</vos:property>
    <vos:property uri="ivo://example.test/project">science</vos:property>
  </vos:properties>
</vos:node>
""".encode()


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


def _expected(values: dict[str, object]) -> str:
    return f"{pprint.pformat(values, width=80, sort_dicts=True)}\n"


class _StrictInfoTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        super().__init__(self._respond)

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        call = (request.method, request.url.path)
        self.calls.append(call)
        if call == ("GET", "/arc/capabilities"):
            return httpx.Response(200, content=_CAPABILITIES)
        if call == ("GET", "/arc/nodes/docs/report.txt"):
            return httpx.Response(200, content=_VOS_INFO)
        message = f"unplanned mocked request: {call!r}"
        raise AssertionError(message)

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class _InfoProfileSource:
    def __init__(
        self,
        factory: Callable[[], AsyncFileSystem],
        *,
        close: Callable[[AsyncFileSystem], Awaitable[None]] | None = None,
    ) -> None:
        self._factory = factory
        self._close = close
        self.lifecycle: list[str] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.filesystems: list[AsyncFileSystem] = []
        self.exit_calls: list[
            tuple[
                type[BaseException] | None,
                BaseException | None,
                TracebackType | None,
            ]
        ] = []

    def __call__(self) -> _InfoProfileContext:
        self.lifecycle.append("factory")
        return _InfoProfileContext(self)


class _InfoProfileContext(AbstractAsyncContextManager[AsyncFileSystem]):
    def __init__(self, source: _InfoProfileSource) -> None:
        self.source = source
        self.filesystem: AsyncFileSystem | None = None

    async def __aenter__(self) -> AsyncFileSystem:
        filesystem = self.source._factory()
        self.filesystem = filesystem
        self.source.filesystems.append(filesystem)
        self.source.lifecycle.append("enter")
        original_info = filesystem._info

        async def instrumented_info(path: str, **kwargs: object) -> object:
            self.source.calls.append((path, kwargs))
            return await original_info(path, **kwargs)

        async def reject_ls(*args: object, **kwargs: object) -> object:
            del args, kwargs
            message = "info must not call _ls"
            raise AssertionError(message)

        def reject_sync(*args: object, **kwargs: object) -> object:
            del args, kwargs
            message = "info must not use a public synchronous facade"
            raise AssertionError(message)

        setattr(filesystem, "_info", instrumented_info)  # noqa: B010 - probe.
        setattr(filesystem, "_ls", reject_ls)  # noqa: B010 - probe.
        setattr(filesystem, "info", reject_sync)  # noqa: B010 - probe.
        return filesystem

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        filesystem = self.filesystem
        assert filesystem is not None
        if self.source._close is not None:
            await self.source._close(filesystem)
            self.source.lifecycle.append("close")
        self.source.exit_calls.append((exc_type, exc, traceback))
        self.source.lifecycle.append("exit")


def _exercise_info(
    source_name: str,
    source: _InfoProfileSource,
    path: str,
    expected: str,
) -> None:
    result = CliRunner().invoke(
        App({source_name: source}).typer_app,
        ["info", f"{source_name}:{path}"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, expected, "")
    assert source.calls == [(path, {})]
    expected_lifecycle = ["factory", "enter"]
    if source._close is not None:
        expected_lifecycle.append("close")
    expected_lifecycle.append("exit")
    assert source.lifecycle == expected_lifecycle
    assert source.exit_calls == [(None, None, None)]


def test_adapted_memory_info_preserves_sparse_shape_and_datetime_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})
    created = datetime(2026, 7, 17, 18, tzinfo=timezone.utc)

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("/docs/report.txt", b"abc")
        filesystem.store["/docs/report.txt"].created = created
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _InfoProfileSource(make_filesystem)
    expected = _expected(
        {
            "name": "report.txt",
            "kind": "file",
            "size": 3,
            "mtime": None,
            "mode": None,
            "nlink": None,
            "owner": None,
            "group": None,
            "link_target": None,
            "extra": {"created": created},
        }
    )

    _exercise_info("memory", source, "/docs/report.txt", expected)

    assert all(
        isinstance(filesystem, AsyncFileSystemWrapper)
        and isinstance(filesystem.sync_fs, MemoryFileSystem)
        for filesystem in source.filesystems
    )


def test_adapted_local_info_preserves_rich_shape_and_local_extras(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.txt"
    report.write_bytes(b"abc")
    report.chmod(0o640)
    os.utime(report, (1_784_311_200, 1_784_311_200))
    metadata = report.stat(follow_symlinks=False)
    path = report.resolve().as_posix()
    source = _InfoProfileSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )
    expected = _expected(
        {
            "name": "report.txt",
            "kind": "file",
            "size": 3,
            "mtime": float(metadata.st_mtime),
            "mode": metadata.st_mode,
            "nlink": metadata.st_nlink,
            "owner": metadata.st_uid,
            "group": metadata.st_gid,
            "link_target": None,
            "extra": {
                "created": getattr(metadata, "st_birthtime", metadata.st_ctime),
                "ino": metadata.st_ino,
            },
        }
    )

    _exercise_info("local", source, path, expected)

    assert all(
        isinstance(filesystem, AsyncFileSystemWrapper)
        and isinstance(filesystem.sync_fs, LocalFileSystem)
        for filesystem in source.filesystems
    )


async def _close_vosfs(filesystem: AsyncFileSystem) -> None:
    assert isinstance(filesystem, VOSpaceFileSystem)
    await filesystem.aclose()


def test_native_vosfs_info_exposes_remote_extras_over_mocked_transport() -> None:
    transports: list[_StrictInfoTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictInfoTransport()
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _InfoProfileSource(make_filesystem, close=_close_vosfs)
    properties = {
        "ivo://ivoa.net/vospace/core#length": "3",
        "ivo://ivoa.net/vospace/core#mtime": "2026-07-17T18:00:00Z",
        "ivo://ivoa.net/vospace/core#MD5": "abc123",
        "ivo://ivoa.net/vospace/core#contenttype": "text/plain",
        "ivo://example.test/project": "science",
    }
    expected = _expected(
        {
            "name": "report.txt",
            "kind": "file",
            "size": 3,
            "mtime": 1_784_311_200.0,
            "mode": None,
            "nlink": None,
            "owner": None,
            "group": None,
            "link_target": None,
            "extra": {
                "uri": f"vos://{_AUTHORITY}/docs/report.txt",
                "md5": "abc123",
                "content_type": "text/plain",
                "properties": properties,
            },
        }
    )

    _exercise_info("vos", source, "/docs/report.txt", expected)

    assert source.calls == [("/docs/report.txt", {})]
    assert transports[0].calls == [
        ("GET", "/arc/capabilities"),
        ("GET", "/arc/nodes/docs/report.txt"),
    ]
    assert transports[0].closed
    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
