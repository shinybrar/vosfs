"""Hermetic compatibility evidence for the native vosfs source form."""

import httpx
import pytest

from vosfs import VOSpaceFileSystem

from ._matrix_support import _block_network, _exercise_locked_profile, _ProbedSource

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
      <vos:properties/>
    </vos:node>
    <vos:node xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/docs/.hidden">
      <vos:properties/>
    </vos:node>
    <vos:node xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/docs/guide.md">
      <vos:properties/>
    </vos:node>
  </vos:nodes>
</vos:node>
""".encode()


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


class _StrictMockTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.closed = False
        super().__init__(self._respond)

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        call = (request.method, request.url.path)
        self.requests.append(call)
        if call == ("GET", "/arc/capabilities"):
            return httpx.Response(200, content=_CAPABILITIES)
        if call == ("GET", "/arc/nodes/docs"):
            return httpx.Response(200, content=_DOCS)
        if call == ("GET", "/arc/nodes/docs/missing"):
            return httpx.Response(404, text="not found")
        message = f"unplanned mocked request: {call!r}"
        raise AssertionError(message)

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


async def _close_vosfs(filesystem: VOSpaceFileSystem) -> None:
    await filesystem.aclose()


def test_native_vosfs_plain_ls_profile_uses_only_mocked_transport() -> None:
    transports: list[_StrictMockTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _StrictMockTransport()
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _ProbedSource(make_filesystem, close=_close_vosfs)

    _exercise_locked_profile("vos", source, "/docs")

    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
    assert all(fs.asynchronous is True for fs in source.filesystems)
    assert all(fs._pool.closed is True for fs in source.filesystems)
    assert [call.source_id for call in source.close_calls] == [1, 2, 3]
    for close_call in source.close_calls:
        exit_call = next(
            call for call in source.exit_calls if call.source_id == close_call.source_id
        )
        assert close_call.loop_id == exit_call.loop_id
    assert [transport.requests for transport in transports] == [
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs"),
        ],
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes/docs"),
            ("GET", "/arc/nodes/docs"),
        ],
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes/docs/missing"),
        ],
    ]
    assert all(transport.closed for transport in transports)
