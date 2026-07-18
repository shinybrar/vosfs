"""Hermetic Local, Memory, and native-vosfs evidence for ``find``."""

from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from fsspec.asyn import AsyncFileSystem
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem
from fsspec_cli import App
from typer.testing import CliRunner

from vosfs import VOSpaceFileSystem

from ._matrix_support import _block_network, _ProbedSource

_FilesystemT = TypeVar("_FilesystemT", bound=AsyncFileSystem)
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


class _StrictFindTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.closed = False
        super().__init__(self._respond)

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        call = (request.method, request.url.path)
        self.requests.append(call)
        response = _RESPONSES.get(call)
        if response is None:
            message = f"unplanned mocked request: {call!r}"
            raise AssertionError(message)
        return response

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


async def _close_vosfs(filesystem: VOSpaceFileSystem) -> None:
    await filesystem.aclose()


def _exercise_find_profile(  # noqa: PLR0913 - matrix golden expectations.
    source_name: str,
    source: _ProbedSource[_FilesystemT],
    path: str,
    *,
    recursive_files: str,
    direct_files: str,
    directories: str,
    root_directory: str,
) -> None:
    app = App({source_name: source})
    operand = f"{source_name}:{path}"
    runner = CliRunner()

    recursive = runner.invoke(app.typer_app, ["find", operand])
    direct = runner.invoke(
        app.typer_app,
        ["find", "--maxdepth", "1", operand],
    )
    dirs = runner.invoke(app.typer_app, ["find", "--type", "d", operand])
    root = runner.invoke(
        app.typer_app,
        ["find", "--maxdepth", "0", "--type", "d", operand],
    )

    assert (recursive.exit_code, recursive.stdout, recursive.stderr) == (
        0,
        recursive_files,
        "",
    )
    assert (direct.exit_code, direct.stdout, direct.stderr) == (
        0,
        direct_files,
        "",
    )
    assert (dirs.exit_code, dirs.stdout, dirs.stderr) == (0, directories, "")
    assert (root.exit_code, root.stdout, root.stderr) == (0, root_directory, "")
    assert [event.stage for event in source.lifecycle] == [
        "factory",
        "enter",
        "exit",
    ] * 4
    find_calls = [call for call in source.calls if call.operation == "find"]
    assert [
        (call.path, call.maxdepth, call.withdirs, call.detail, call.kwargs)
        for call in find_calls
    ] == [
        (path, None, False, False, {}),
        (path, 1, False, False, {}),
        (path, None, True, True, {}),
        (path, 1, True, True, {}),
    ]
    assert not source.errors


def test_adapted_local_find_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("b", encoding="utf-8")
    path = root.resolve().as_posix()
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_find_profile(
        "local",
        source,
        path,
        recursive_files=f"{path}/a.txt\n{path}/sub/b.txt\n",
        direct_files=f"{path}/a.txt\n",
        directories=f"{path}\n{path}/empty\n{path}/sub\n",
        root_directory=f"{path}\n",
    )

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in source.filesystems)


def test_adapted_memory_find_profile_has_isolated_state(
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

    source = _ProbedSource(make_filesystem)

    _exercise_find_profile(
        "memory",
        source,
        "/docs",
        recursive_files="/docs/a.txt\n/docs/sub/b.txt\n",
        direct_files="/docs/a.txt\n",
        directories="/docs\n/docs/empty\n/docs/sub\n",
        root_directory="/docs\n",
    )

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in source.filesystems)


def test_native_vosfs_find_profile_uses_only_mocked_transport() -> None:
    transports: list[_StrictFindTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictFindTransport()
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _ProbedSource(make_filesystem, close=_close_vosfs)

    _exercise_find_profile(
        "vos",
        source,
        "/docs",
        recursive_files="/docs/a.txt\n/docs/sub/b.txt\n",
        direct_files="/docs/a.txt\n",
        directories="/docs\n/docs/empty\n/docs/sub\n",
        root_directory="/docs\n",
    )

    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
    assert all(fs._pool.closed is True for fs in source.filesystems)
    assert [transport.requests for transport in transports] == [
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
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs/sub"),
            ("GET", "/arc/nodes/docs/empty"),
        ],
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs"),
        ],
    ]
    assert all(transport.closed for transport in transports)
