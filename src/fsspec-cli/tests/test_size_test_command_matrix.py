"""Focused Local, Memory, and native-vosfs evidence for ``size`` and ``test``."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

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


@dataclass(frozen=True)
class _SizeHookCall:
    operation: Literal["size", "sizes"]
    paths: tuple[str, ...]


@dataclass(frozen=True)
class _PredicateHookCall:
    operation: Literal["exists", "isdir", "isfile"]
    path: str


@dataclass(frozen=True)
class _ProfilePaths:
    directory: str
    first: str
    second: str
    missing: str


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
        self.size_calls: list[_SizeHookCall] = []
        self.predicate_calls: list[_PredicateHookCall] = []
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
        original_size = filesystem._size
        original_sizes = filesystem._sizes
        original_exists = filesystem._exists
        original_isdir = filesystem._isdir
        original_isfile = filesystem._isfile
        batch_active = False

        async def size(path: str) -> object:
            if not batch_active:
                self.source.size_calls.append(_SizeHookCall("size", (path,)))
            return await original_size(path)

        async def sizes(
            paths: list[str],
            batch_size: int | None = None,
        ) -> object:
            nonlocal batch_active
            assert batch_size is None
            self.source.size_calls.append(_SizeHookCall("sizes", tuple(paths)))
            batch_active = True
            try:
                return await original_sizes(paths)
            finally:
                batch_active = False

        async def exists(path: str, **kwargs: object) -> object:
            self.source.predicate_calls.append(_PredicateHookCall("exists", path))
            return await original_exists(path, **kwargs)

        async def isdir(path: str) -> object:
            self.source.predicate_calls.append(_PredicateHookCall("isdir", path))
            return await original_isdir(path)

        async def isfile(path: str) -> object:
            self.source.predicate_calls.append(_PredicateHookCall("isfile", path))
            return await original_isfile(path)

        setattr(filesystem, "_size", size)  # noqa: B010 - instance probe.
        setattr(filesystem, "_sizes", sizes)  # noqa: B010 - instance probe.
        setattr(filesystem, "_exists", exists)  # noqa: B010 - instance probe.
        setattr(filesystem, "_isdir", isdir)  # noqa: B010 - instance probe.
        setattr(filesystem, "_isfile", isfile)  # noqa: B010 - instance probe.

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
    paths: _ProfilePaths,
) -> None:
    app = App({source_name: source})
    runner = CliRunner()
    mapped_first = f"{source_name}:{paths.first}"
    mapped_second = f"{source_name}:{paths.second}"

    single = runner.invoke(app.typer_app, ["size", mapped_first])
    batched = runner.invoke(
        app.typer_app,
        ["size", mapped_first, mapped_second, mapped_first],
    )

    assert (single.exit_code, single.stdout, single.stderr) == (
        0,
        f"5\t{mapped_first}\n",
        "",
    )
    assert (batched.exit_code, batched.stdout, batched.stderr) == (
        0,
        f"5\t{mapped_first}\n7\t{mapped_second}\n5\t{mapped_first}\n",
        "",
    )

    predicate_cases = [
        ("-e", paths.first, 0),
        ("-e", paths.missing, 1),
        ("-d", paths.directory, 0),
        ("-d", paths.first, 1),
        ("-f", paths.first, 0),
        ("-f", paths.directory, 1),
    ]
    for selector, path, exit_code in predicate_cases:
        result = runner.invoke(
            app.typer_app,
            ["test", selector, f"{source_name}:{path}"],
        )
        assert (result.exit_code, result.stdout, result.stderr) == (exit_code, "", "")

    assert source.size_calls == [
        _SizeHookCall("size", (paths.first,)),
        _SizeHookCall("sizes", (paths.first, paths.second, paths.first)),
    ]
    assert source.predicate_calls == [
        _PredicateHookCall("exists", paths.first),
        _PredicateHookCall("exists", paths.missing),
        _PredicateHookCall("isdir", paths.directory),
        _PredicateHookCall("isdir", paths.first),
        _PredicateHookCall("isfile", paths.first),
        _PredicateHookCall("isfile", paths.directory),
    ]
    assert not any(call[1] is not None for call in source.exit_calls)


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


def test_adapted_local_size_and_test_profiles_use_native_storage(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    first = directory / "a.txt"
    second = directory / "b.bin"
    first.write_bytes(b"12345")
    second.write_bytes(b"1234567")
    source = _ProfileSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_profiles(
        "local",
        source,
        _ProfilePaths(
            directory.as_posix(),
            first.as_posix(),
            second.as_posix(),
            (directory / "missing").as_posix(),
        ),
    )

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in source.filesystems)


def test_adapted_memory_size_and_test_profiles_use_isolated_state(
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
        filesystem.makedirs("/docs")
        filesystem.pipe_file("/docs/a.txt", b"12345")
        filesystem.pipe_file("/docs/b.bin", b"1234567")
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProfileSource(make_filesystem)

    _exercise_profiles(
        "memory",
        source,
        _ProfilePaths("/docs", "/docs/a.txt", "/docs/b.bin", "/docs/missing"),
    )

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in source.filesystems)


_BASE_URL = "https://example.test/arc"
_NODES_URL = f"{_BASE_URL}/nodes"
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
</vosi:capabilities>
""".encode()


def _node(
    path: str,
    *,
    kind: Literal["ContainerNode", "DataNode"],
    size: int = 0,
) -> bytes:
    length = (
        ""
        if kind == "ContainerNode"
        else (
            '<vos:property uri="ivo://ivoa.net/vospace/core#length">'
            f"{size}</vos:property>"
        )
    )
    return f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:{kind}" uri="vos://{_AUTHORITY}{path}">
  <vos:properties>{length}</vos:properties>
</vos:node>
""".encode()


_VOS_RESPONSES = {
    "/arc/capabilities": httpx.Response(200, content=_CAPABILITIES),
    "/arc/nodes/docs": httpx.Response(
        200,
        content=_node("/docs", kind="ContainerNode"),
    ),
    "/arc/nodes/docs/a.txt": httpx.Response(
        200,
        content=_node("/docs/a.txt", kind="DataNode", size=5),
    ),
    "/arc/nodes/docs/b.bin": httpx.Response(
        200,
        content=_node("/docs/b.bin", kind="DataNode", size=7),
    ),
    "/arc/nodes/docs/missing": httpx.Response(404),
}


class _StrictProfileTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.closed = False
        super().__init__(self._respond)

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        call = (request.method, request.url.path)
        self.requests.append(call)
        if request.method != "GET" or request.url.path not in _VOS_RESPONSES:
            message = f"unplanned mocked request: {call!r}"
            raise AssertionError(message)
        return _VOS_RESPONSES[request.url.path]

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


async def _close_vosfs(filesystem: VOSpaceFileSystem) -> None:
    await filesystem.aclose()


def test_native_vosfs_size_and_test_profiles_use_only_mocked_transport() -> None:
    transports: list[_StrictProfileTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictProfileTransport()
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _ProfileSource(make_filesystem, close=_close_vosfs)

    _exercise_profiles(
        "vos",
        source,
        _ProfilePaths("/docs", "/docs/a.txt", "/docs/b.bin", "/docs/missing"),
    )

    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
    assert all(fs._pool.closed is True for fs in source.filesystems)
    assert all(transport.closed for transport in transports)
    assert all(
        transport.requests[0] == ("GET", "/arc/capabilities")
        for transport in transports
    )
