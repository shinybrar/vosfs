"""Focused Local, Memory, and native-vosfs evidence for ``head`` and ``tail``."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, NoReturn, TypeVar

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

_FilesystemT = TypeVar("_FilesystemT", bound=AsyncFileSystem)
_PAYLOAD = b"abc\0def\xffgh"


@dataclass(frozen=True)
class _ReadHookCall:
    operation: Literal["info", "cat_file"]
    path: str
    start: int | None = None
    end: int | None = None


class _ProfileSource(Generic[_FilesystemT]):
    def __init__(
        self,
        factory: Callable[[], _FilesystemT],
        *,
        close: Callable[[_FilesystemT], Awaitable[None]] | None = None,
    ) -> None:
        self._factory = factory
        self._close = close
        self.lifecycle: list[str] = []
        self.calls: list[_ReadHookCall] = []
        self.filesystems: list[_FilesystemT] = []
        self.exit_calls: list[
            tuple[
                type[BaseException] | None,
                BaseException | None,
                TracebackType | None,
            ]
        ] = []

    def __call__(self) -> _ProfileContext[_FilesystemT]:
        self.lifecycle.append("factory")
        return _ProfileContext(self)


class _ProfileContext(
    AbstractAsyncContextManager[_FilesystemT],
    Generic[_FilesystemT],
):
    def __init__(self, source: _ProfileSource[_FilesystemT]) -> None:
        self.source = source
        self.filesystem: _FilesystemT | None = None

    async def __aenter__(self) -> _FilesystemT:
        filesystem = self.source._factory()
        self.filesystem = filesystem
        self.source.filesystems.append(filesystem)
        self.source.lifecycle.append("enter")
        self._instrument(filesystem)
        return filesystem

    def _instrument(self, filesystem: _FilesystemT) -> None:
        original_info = filesystem._info
        original_cat_file = filesystem._cat_file

        async def info(path: str, **kwargs: object) -> object:
            assert not kwargs
            self.source.calls.append(_ReadHookCall("info", path))
            return await original_info(path)

        async def cat_file(
            path: str,
            start: int | None = None,
            end: int | None = None,
            **kwargs: object,
        ) -> object:
            assert not kwargs
            self.source.calls.append(_ReadHookCall("cat_file", path, start, end))
            return await original_cat_file(path, start=start, end=end)

        async def get_file(*args: object, **kwargs: object) -> NoReturn:
            del args, kwargs
            message = "head and tail must not use _get_file"
            raise AssertionError(message)

        setattr(filesystem, "_info", info)  # noqa: B010 - instance probe.
        setattr(filesystem, "_cat_file", cat_file)  # noqa: B010 - instance probe.
        setattr(filesystem, "_get_file", get_file)  # noqa: B010 - negative trap.

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


def _exercise_profiles(
    source_name: str,
    source: _ProfileSource[_FilesystemT],
    path: str,
) -> None:
    app = App({source_name: source})
    operand = f"{source_name}:{path}"
    runner = CliRunner()

    head = runner.invoke(app.typer_app, ["head", "-c", "4", operand])
    tail = runner.invoke(app.typer_app, ["tail", "-c", "3", operand])

    assert (head.exit_code, head.stdout_bytes, head.stderr) == (0, _PAYLOAD[:4], "")
    assert (tail.exit_code, tail.stdout_bytes, tail.stderr) == (0, _PAYLOAD[-3:], "")
    assert source.calls == [
        _ReadHookCall("cat_file", path, 0, 4),
        _ReadHookCall("info", path),
        _ReadHookCall("cat_file", path, len(_PAYLOAD) - 3, None),
    ]
    assert not any(call[1] is not None for call in source.exit_calls)


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


def test_adapted_local_head_and_tail_profiles_use_native_storage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(_PAYLOAD)
    source = _ProfileSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_profiles("local", source, path.as_posix())

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in source.filesystems)


def test_adapted_memory_head_and_tail_profiles_use_isolated_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("/blob.bin", _PAYLOAD)
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProfileSource(make_filesystem)

    _exercise_profiles("memory", source, "/blob.bin")

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in source.filesystems)


_BASE_URL = "https://example.test/arc"
_NODES_URL = f"{_BASE_URL}/nodes"
_SYNC_URL = f"{_BASE_URL}/synctrans"
_AUTHORITY = "example.test!vault"
_CAPABILITIES = f"""<?xml version="1.0" encoding="UTF-8"?>
<vosi:capabilities xmlns:vosi="http://www.ivoa.net/xml/VOSICapabilities/v1.0"
                   xmlns:vs="http://www.ivoa.net/xml/VODataService/v1.1"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <capability standardID="ivo://ivoa.net/std/VOSpace/v2.0#nodes">
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="base">{_NODES_URL}</accessURL>
    </interface>
  </capability>
  <capability standardID="ivo://ivoa.net/std/VOSpace#sync-2.1">
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="full">{_SYNC_URL}</accessURL>
    </interface>
  </capability>
</vosi:capabilities>
""".encode()
_NODE = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/blob.bin">
  <vos:properties>
    <vos:property uri="ivo://ivoa.net/vospace/core#length">{len(_PAYLOAD)}</vos:property>
  </vos:properties>
</vos:node>
""".encode()
_ROOT = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:ContainerNode" uri="vos://{_AUTHORITY}">
  <vos:properties/><vos:nodes/>
</vos:node>
""".encode()


def _transfer_details(endpoint: str) -> bytes:
    return (
        '<vos:transfer xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
        'version="2.1"><vos:protocol '
        'uri="ivo://ivoa.net/vospace/core#httpsget">'
        f"<vos:endpoint>{endpoint}</vos:endpoint>"
        "</vos:protocol></vos:transfer>"
    ).encode()


class _StrictReadTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.byte_payloads: list[bytes] = []
        self.closed = False
        super().__init__(self._respond)

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        if "range" in {name.lower() for name in request.headers}:
            message = f"unexpected Range header: {request.headers!r}"
            raise AssertionError(message)
        call = (request.method, request.url.path)
        self.requests.append(call)
        if call == ("GET", "/arc/capabilities"):
            return httpx.Response(200, content=_CAPABILITIES)
        if call == ("GET", "/arc/nodes"):
            return httpx.Response(200, content=_ROOT)
        if call == ("GET", "/arc/nodes/blob.bin"):
            return httpx.Response(200, content=_NODE)
        if call == ("POST", "/arc/synctrans"):
            return httpx.Response(
                303,
                headers={"Location": f"{_BASE_URL}/details"},
            )
        if call == ("GET", "/arc/details"):
            return httpx.Response(
                200,
                content=_transfer_details(f"{_BASE_URL}/files/blob.bin"),
            )
        if call == ("GET", "/arc/files/blob.bin"):
            self.byte_payloads.append(_PAYLOAD)

            async def stream() -> object:
                yield _PAYLOAD

            return httpx.Response(200, content=stream())
        message = f"unplanned mocked request: {call!r}"
        raise AssertionError(message)

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


async def _close_vosfs(filesystem: VOSpaceFileSystem) -> None:
    await filesystem.aclose()


def test_native_vosfs_head_and_tail_profiles_observe_truthful_whole_gets() -> None:
    transports: list[_StrictReadTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictReadTransport()
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _ProfileSource(make_filesystem, close=_close_vosfs)

    _exercise_profiles("vos", source, "/blob.bin")

    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
    assert all(fs._pool.closed is True for fs in source.filesystems)
    assert all(transport.closed for transport in transports)
    assert all(transport.byte_payloads == [_PAYLOAD] for transport in transports)
    assert transports[0].requests == [
        ("GET", "/arc/capabilities"),
        ("GET", "/arc/nodes"),
        ("POST", "/arc/synctrans"),
        ("GET", "/arc/details"),
        ("GET", "/arc/files/blob.bin"),
    ]
    assert transports[1].requests == [
        ("GET", "/arc/capabilities"),
        ("GET", "/arc/nodes/blob.bin"),
        ("POST", "/arc/synctrans"),
        ("GET", "/arc/details"),
        ("GET", "/arc/files/blob.bin"),
    ]
