"""Hermetic Local, Memory, and native-vosfs evidence for ``tree``."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
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
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path
    from types import TracebackType

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


def _container(path: str, children: str = "") -> bytes:
    return f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:ContainerNode" uri="vos://{_AUTHORITY}{path}">
  <vos:properties/>
  <vos:nodes>{children}</vos:nodes>
</vos:node>
""".encode()


def _data_child(path: str) -> str:
    return f"""<vos:node xsi:type="vos:DataNode"
      uri="vos://{_AUTHORITY}{path}">
  <vos:properties>
    <vos:property uri="ivo://ivoa.net/vospace/core#length">1</vos:property>
  </vos:properties>
</vos:node>"""


def _directory_child(path: str) -> str:
    return f"""<vos:node xsi:type="vos:ContainerNode"
      uri="vos://{_AUTHORITY}{path}">
  <vos:properties/>
  <vos:nodes/>
</vos:node>"""


_DOCS = _container(
    "/docs",
    _data_child("/docs/a.txt")
    + _directory_child("/docs/sub")
    + _directory_child("/docs/empty"),
)
_SUB = _container("/docs/sub", _data_child("/docs/sub/b.txt"))
_EMPTY = _container("/docs/empty")
_RESPONSES: dict[tuple[str, str], httpx.Response] = {
    ("GET", "/arc/capabilities"): httpx.Response(200, content=_CAPABILITIES),
    ("GET", "/arc/nodes/docs"): httpx.Response(200, content=_DOCS),
    ("GET", "/arc/nodes/docs/sub"): httpx.Response(200, content=_SUB),
    ("GET", "/arc/nodes/docs/empty"): httpx.Response(200, content=_EMPTY),
}


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


class _StrictTreeTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        super().__init__(self._respond)

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        call = (request.method, request.url.path)
        self.calls.append(call)
        response = _RESPONSES.get(call)
        if response is None:
            message = f"unplanned mocked request: {call!r}"
            raise AssertionError(message)
        return response

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


@dataclass(frozen=True)
class _WalkCall:
    path: str
    maxdepth: int | None
    detail: bool
    on_error: str
    kwargs: dict[str, object]


class _TreeProfileSource:
    def __init__(
        self,
        factory: Callable[[], AsyncFileSystem],
        *,
        close: Callable[[AsyncFileSystem], Awaitable[None]] | None = None,
    ) -> None:
        self._factory = factory
        self._close = close
        self.lifecycle: list[str] = []
        self.calls: list[_WalkCall] = []
        self.filesystems: list[AsyncFileSystem] = []
        self.exit_calls: list[
            tuple[
                type[BaseException] | None,
                BaseException | None,
                TracebackType | None,
            ]
        ] = []

    def __call__(self) -> _TreeProfileContext:
        self.lifecycle.append("factory")
        return _TreeProfileContext(self)


class _TreeProfileContext(AbstractAsyncContextManager[AsyncFileSystem]):
    def __init__(self, source: _TreeProfileSource) -> None:
        self.source = source
        self.filesystem: AsyncFileSystem | None = None

    async def __aenter__(self) -> AsyncFileSystem:
        filesystem = self.source._factory()
        self.filesystem = filesystem
        self.source.filesystems.append(filesystem)
        self.source.lifecycle.append("enter")
        self._instrument_walk(filesystem)
        return filesystem

    def _instrument_walk(self, filesystem: AsyncFileSystem) -> None:
        original_walk = filesystem._walk
        if isinstance(filesystem, AsyncFileSystemWrapper):

            def adapted_walk(
                path: str,
                maxdepth: int | None = None,
                on_error: str = "omit",
                **kwargs: object,
            ) -> object:
                detail = kwargs.pop("detail", False)
                assert type(detail) is bool
                self.source.calls.append(
                    _WalkCall(path, maxdepth, detail, on_error, kwargs)
                )
                return original_walk(
                    path,
                    maxdepth=maxdepth,
                    detail=detail,
                    on_error=on_error,
                    **kwargs,
                )

            setattr(filesystem, "_walk", adapted_walk)  # noqa: B010 - probe.
            return

        active = False

        def native_walk(
            path: str,
            maxdepth: int | None = None,
            on_error: str = "omit",
            **kwargs: object,
        ) -> AsyncIterator[object]:
            detail = kwargs.pop("detail", False)
            assert type(detail) is bool
            top_level = not active
            if top_level:
                self.source.calls.append(
                    _WalkCall(path, maxdepth, detail, on_error, kwargs)
                )

            async def traverse() -> AsyncIterator[object]:
                nonlocal active
                previous = active
                if top_level:
                    active = True
                try:
                    async for row in original_walk(
                        path,
                        maxdepth=maxdepth,
                        detail=detail,
                        on_error=on_error,
                        **kwargs,
                    ):
                        yield row
                finally:
                    active = previous

            return traverse()

        setattr(filesystem, "_walk", native_walk)  # noqa: B010 - probe.

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


def _exercise_tree_profile(
    source_name: str,
    source: _TreeProfileSource,
    path: str,
) -> None:
    app = App({source_name: source})
    operand = f"{source_name}:{path}"
    runner = CliRunner()

    recursive = runner.invoke(app.typer_app, ["tree", operand])
    direct = runner.invoke(
        app.typer_app,
        ["tree", "--maxdepth", "1", operand],
    )

    assert (recursive.exit_code, recursive.stdout, recursive.stderr) == (
        0,
        f"{path}\n├── empty\n├── sub\n│   └── b.txt\n└── a.txt\n",
        "",
    )
    assert (direct.exit_code, direct.stdout, direct.stderr) == (
        0,
        f"{path}\n├── empty\n├── sub\n└── a.txt\n",
        "",
    )
    assert source.calls == [
        _WalkCall(path, None, detail=False, on_error="raise", kwargs={}),
        _WalkCall(path, 1, detail=False, on_error="raise", kwargs={}),
    ]
    expected_lifecycle = ["factory", "enter"]
    if source._close is not None:
        expected_lifecycle.append("close")
    expected_lifecycle.append("exit")
    assert source.lifecycle == expected_lifecycle * 2
    assert all(call[1] is None for call in source.exit_calls)


def test_adapted_local_tree_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("b", encoding="utf-8")
    path = root.resolve().as_posix()
    source = _TreeProfileSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_tree_profile("local", source, path)

    adapted_filesystems = [
        fs for fs in source.filesystems if isinstance(fs, AsyncFileSystemWrapper)
    ]
    assert adapted_filesystems == source.filesystems
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in adapted_filesystems)


def test_adapted_memory_tree_profile_has_isolated_state(
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
        filesystem.makedirs("/docs/sub")
        filesystem.makedirs("/docs/empty")
        filesystem.pipe_file("/docs/a.txt", b"a")
        filesystem.pipe_file("/docs/sub/b.txt", b"b")
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _TreeProfileSource(make_filesystem)

    _exercise_tree_profile("memory", source, "/docs")

    adapted_filesystems = [
        fs for fs in source.filesystems if isinstance(fs, AsyncFileSystemWrapper)
    ]
    assert adapted_filesystems == source.filesystems
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in adapted_filesystems)


async def _close_vosfs(filesystem: AsyncFileSystem) -> None:
    assert isinstance(filesystem, VOSpaceFileSystem)
    await filesystem.aclose()


def test_native_vosfs_tree_profile_uses_client_traversal_over_mocked_transport() -> (
    None
):
    transports: list[_StrictTreeTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictTreeTransport()
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _TreeProfileSource(make_filesystem, close=_close_vosfs)

    _exercise_tree_profile("vos", source, "/docs")

    vos_filesystems = [
        fs for fs in source.filesystems if isinstance(fs, VOSpaceFileSystem)
    ]
    assert vos_filesystems == source.filesystems
    assert all(filesystem._pool.closed is True for filesystem in vos_filesystems)
    assert [transport.calls for transport in transports] == [
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs/sub"),
            ("GET", "/arc/nodes/docs/empty"),
        ],
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes/docs"),
        ],
    ]
    assert all(transport.closed for transport in transports)
