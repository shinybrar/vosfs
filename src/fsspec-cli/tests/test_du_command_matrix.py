"""Hermetic Local, Memory, and native-vosfs evidence for ``du``."""

from pathlib import Path

import httpx
import pytest
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem

from vosfs import VOSpaceFileSystem

from ._du_matrix_support import _exercise_du_profile
from ._matrix_support import _block_network, _ProbedSource

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
_DOCS = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:ContainerNode" uri="vos://{_AUTHORITY}/docs">
  <vos:properties/>
  <vos:nodes>
    <vos:node xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/docs/notes.txt">
      <vos:properties>
        <vos:property uri="ivo://ivoa.net/vospace/core#length">1536</vos:property>
      </vos:properties>
    </vos:node>
    <vos:node xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/docs/.hidden">
      <vos:properties>
        <vos:property uri="ivo://ivoa.net/vospace/core#length">7</vos:property>
      </vos:properties>
    </vos:node>
    <vos:node xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/docs/guide.md">
      <vos:properties>
        <vos:property uri="ivo://ivoa.net/vospace/core#length">8</vos:property>
      </vos:properties>
    </vos:node>
    <vos:node xsi:type="vos:LinkNode" uri="vos://{_AUTHORITY}/docs/shortcut">
      <vos:properties/>
      <vos:target>vos://{_AUTHORITY}/docs/guide.md</vos:target>
    </vos:node>
  </vos:nodes>
</vos:node>
""".encode()


def _data_node(path: str, size: int) -> bytes:
    return f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}{path}">
  <vos:properties>
    <vos:property uri="ivo://ivoa.net/vospace/core#length">{size}</vos:property>
  </vos:properties>
</vos:node>
""".encode()


_SHORTCUT = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:LinkNode" uri="vos://{_AUTHORITY}/docs/shortcut">
  <vos:properties/>
  <vos:target>vos://{_AUTHORITY}/docs/guide.md</vos:target>
</vos:node>
""".encode()
_RESPONSES: dict[tuple[str, str], httpx.Response] = {
    ("GET", "/arc/capabilities"): httpx.Response(200, content=_CAPABILITIES),
    ("GET", "/arc/nodes/docs"): httpx.Response(200, content=_DOCS),
    ("GET", "/arc/nodes/docs/.hidden"): httpx.Response(
        200, content=_data_node("/docs/.hidden", 7)
    ),
    ("GET", "/arc/nodes/docs/guide.md"): httpx.Response(
        200, content=_data_node("/docs/guide.md", 8)
    ),
    ("GET", "/arc/nodes/docs/notes.txt"): httpx.Response(
        200, content=_data_node("/docs/notes.txt", 1536)
    ),
    ("GET", "/arc/nodes/docs/shortcut"): httpx.Response(200, content=_SHORTCUT),
}


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


class _StrictDuTransport(httpx.MockTransport):
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


def test_adapted_local_du_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    for name in ("notes.txt", ".hidden", "guide.md"):
        (root / name).write_text(name, encoding="utf-8")
    path = root.resolve().as_posix()
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_du_profile(
        "local",
        source,
        path,
        exact_output=f"7\t{path}/.hidden\n8\t{path}/guide.md\n9\t{path}/notes.txt\n",
        human_output=(
            f"7B\t{path}/.hidden\n8B\t{path}/guide.md\n9B\t{path}/notes.txt\n"
        ),
        total=24,
        human_total="24B",
    )

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in source.filesystems)


def test_adapted_memory_du_profile_has_isolated_state(
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
        for name in ("notes.txt", ".hidden", "guide.md"):
            filesystem.pipe_file(f"/docs/{name}", name.encode())
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_du_profile(
        "memory",
        source,
        "/docs",
        exact_output="7\t/docs/.hidden\n8\t/docs/guide.md\n9\t/docs/notes.txt\n",
        human_output="7B\t/docs/.hidden\n8B\t/docs/guide.md\n9B\t/docs/notes.txt\n",
        total=24,
        human_total="24B",
    )

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in source.filesystems)


def test_native_vosfs_du_profile_uses_only_mocked_transport() -> None:
    transports: list[_StrictDuTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictDuTransport()
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _ProbedSource(make_filesystem, close=_close_vosfs)

    _exercise_du_profile(
        "vos",
        source,
        "/docs",
        exact_output=(
            "7\t/docs/.hidden\n"
            "8\t/docs/guide.md\n"
            "1536\t/docs/notes.txt\n"
            "0\t/docs/shortcut\n"
        ),
        human_output=(
            "7B\t/docs/.hidden\n"
            "8B\t/docs/guide.md\n"
            "1.5K\t/docs/notes.txt\n"
            "0B\t/docs/shortcut\n"
        ),
        total=1551,
        human_total="1.5K",
    )

    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
    assert all(fs._pool.closed is True for fs in source.filesystems)
    expected_requests = [
        ("GET", "/arc/capabilities"),
        ("GET", "/arc/nodes/docs"),
        ("GET", "/arc/nodes/docs/.hidden"),
        ("GET", "/arc/nodes/docs/guide.md"),
        ("GET", "/arc/nodes/docs/notes.txt"),
        ("GET", "/arc/nodes/docs/shortcut"),
    ]
    assert [transport.requests for transport in transports] == [
        expected_requests,
    ] * 4
    assert all(transport.closed for transport in transports)
