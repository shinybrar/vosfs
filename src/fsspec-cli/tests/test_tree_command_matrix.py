"""Hermetic Local, Memory, and native-vosfs evidence for ``tree``."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from fsspec.asyn import AsyncFileSystem
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem
from fsspec_cli import App
from typer.testing import CliRunner

from vosfs import VOSpaceFileSystem

from ._matrix_support import (
    _memory_factory,
)
from ._vosfs_matrix_support import (
    _BASE_URL,
    _CAPABILITIES,
    _close_vosfs,
    _StrictMockTransport,
    _vos_child,
    _vos_container,
)

_DOCS = _vos_container(
    "/docs",
    _vos_child("DataNode", "/docs/a.txt", length=1)
    + _vos_child("ContainerNode", "/docs/sub")
    + _vos_child("ContainerNode", "/docs/empty"),
)
_SUB = _vos_container("/docs/sub", _vos_child("DataNode", "/docs/sub/b.txt", length=1))
_EMPTY = _vos_container("/docs/empty")

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path
    from types import TracebackType

_RESPONSES: dict[tuple[str, str], httpx.Response] = {
    ("GET", "/arc/capabilities"): httpx.Response(200, content=_CAPABILITIES),
    ("GET", "/arc/nodes/docs"): httpx.Response(200, content=_DOCS),
    ("GET", "/arc/nodes/docs/sub"): httpx.Response(200, content=_SUB),
    ("GET", "/arc/nodes/docs/empty"): httpx.Response(200, content=_EMPTY),
}


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
    monkeypatch,
) -> None:
    source = _TreeProfileSource(
        _memory_factory(
            monkeypatch,
            {"/docs/a.txt": b"a", "/docs/sub/b.txt": b"b"},
            directories=("/docs/sub", "/docs/empty"),
        )
    )

    _exercise_tree_profile("memory", source, "/docs")

    adapted_filesystems = [
        fs for fs in source.filesystems if isinstance(fs, AsyncFileSystemWrapper)
    ]
    assert adapted_filesystems == source.filesystems
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in adapted_filesystems)


def test_native_vosfs_tree_profile_uses_client_traversal_over_mocked_transport() -> (
    None
):
    transports: list[_StrictMockTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictMockTransport(_RESPONSES)
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
    unbounded_paths = {path for _method, path in transports[0].requests}
    assert {
        "/arc/nodes/docs",
        "/arc/nodes/docs/sub",
        "/arc/nodes/docs/empty",
    } <= unbounded_paths
    direct_paths = {path for _method, path in transports[1].requests}
    assert "/arc/nodes/docs" in direct_paths
    assert "/arc/nodes/docs/sub" not in direct_paths
    assert "/arc/nodes/docs/empty" not in direct_paths
    assert all(transport.closed for transport in transports)
