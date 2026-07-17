"""Hermetic compatibility evidence for the native vosfs source form."""

import re
from urllib.parse import quote, unquote

import httpx
import pytest

from vosfs import VOSpaceFileSystem

from ._matrix_support import (
    _block_network,
    _exercise_cat_profile,
    _exercise_locked_profile,
    _ProbedSource,
)
from ._matrix_support import _exercise_mkdir_locked_profile as _exercise_mkdir_profile

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
</vosi:capabilities>
""".encode()
_CAT_CAPABILITIES = f"""<?xml version="1.0" encoding="UTF-8"?>
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
_BLOB_PAYLOAD = b"vos-cat\0\xff\xfe"
_BLOB = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:DataNode" uri="vos://{_AUTHORITY}/docs/blob.bin">
  <vos:properties>
    <vos:property uri="ivo://ivoa.net/vospace/core#length">{len(_BLOB_PAYLOAD)}</vos:property>
  </vos:properties>
</vos:node>
""".encode()


_SUBDIR = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:ContainerNode" uri="vos://{_AUTHORITY}/docs/subdir">
  <vos:properties/>
  <vos:nodes/>
</vos:node>
""".encode()
_ROOT = f"""<vos:node
    xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:type="vos:ContainerNode" uri="vos://{_AUTHORITY}/">
  <vos:properties/>
  <vos:nodes/>
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
        if call == ("GET", "/arc/nodes"):
            return httpx.Response(200, content=_ROOT)
        if call == ("GET", "/arc/nodes/docs"):
            return httpx.Response(200, content=_DOCS)
        if call == ("GET", "/arc/nodes/docs/missing"):
            return httpx.Response(404, text="not found")
        if call == ("PUT", "/arc/nodes/docs/subdir"):
            return httpx.Response(201, content=_SUBDIR)
        if call == ("GET", "/arc/nodes/docs/subdir"):
            return httpx.Response(200, content=_SUBDIR)
        if call == ("PUT", "/arc/nodes/docs/notes.txt"):
            return httpx.Response(409, text="conflict")
        if call == ("PUT", "/arc/nodes/docs/notes.txt/child"):
            return httpx.Response(404, text="not found")
        message = f"unplanned mocked request: {call!r}"
        raise AssertionError(message)

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


def _target_path(content: bytes | None) -> str:
    match = re.search(r"<[^>]*target[^>]*>([^<]+)</", (content or b"").decode())
    if match is None:
        return "/"
    prefix = f"vos://{_AUTHORITY}"
    return match.group(1).strip()[len(prefix) :] or "/"


def _transfer_details(endpoint: str) -> bytes:
    return (
        f'<vos:transfer xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" version="2.1">'
        f'<vos:protocol uri="ivo://ivoa.net/vospace/core#httpsget">'
        f"<vos:endpoint>{endpoint}</vos:endpoint></vos:protocol></vos:transfer>"
    ).encode()


class _CatMockTransport(httpx.MockTransport):
    def __init__(self, payload: bytes) -> None:
        self.requests: list[tuple[str, str]] = []
        self.closed = False
        self._payload = payload
        super().__init__(self._respond)

    def _file_response(self, path: str) -> httpx.Response:
        if path != "/docs/blob.bin":
            return httpx.Response(404, text="not found")

        async def _stream() -> object:
            yield self._payload

        return httpx.Response(200, content=_stream())

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        if "range" in {name.lower() for name in request.headers}:
            message = f"unexpected Range header: {request.headers!r}"
            raise AssertionError(message)
        call = (request.method, request.url.path)
        self.requests.append(call)
        handlers = {
            ("GET", "/arc/capabilities"): lambda: httpx.Response(
                200, content=_CAT_CAPABILITIES
            ),
            ("GET", "/arc/nodes/docs/blob.bin"): lambda: httpx.Response(
                200, content=_BLOB
            ),
            ("GET", "/arc/nodes/docs/blob.bin.missing"): lambda: httpx.Response(
                404, text="not found"
            ),
            ("POST", "/arc/synctrans"): lambda: httpx.Response(
                303,
                headers={
                    "Location": (
                        f"{_BASE_URL}/details?t={quote(_target_path(request.content))}"
                    )
                },
            ),
            ("GET", "/arc/details"): lambda: httpx.Response(
                200,
                content=_transfer_details(
                    f"{_BASE_URL}/files?p={quote(unquote(request.url.params['t']))}"
                ),
            ),
            ("GET", "/arc/files"): lambda: self._file_response(
                unquote(request.url.params["p"])
            ),
        }
        handler = handlers.get(call)
        if handler is None:
            message = f"unplanned mocked request: {call!r}"
            raise AssertionError(message)
        return handler()

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


def test_native_vosfs_plain_cat_profile_uses_only_mocked_transport() -> None:
    transports: list[_CatMockTransport] = []

    def make_filesystem() -> VOSpaceFileSystem:
        transport = _CatMockTransport(_BLOB_PAYLOAD)
        transports.append(transport)
        return VOSpaceFileSystem(
            _BASE_URL,
            transport=transport,
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )

    source = _ProbedSource(make_filesystem, close=_close_vosfs)

    _exercise_cat_profile("vos", source, "/docs/blob.bin", payload=_BLOB_PAYLOAD)

    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
    assert all(fs.asynchronous is True for fs in source.filesystems)
    assert all(fs._pool.closed is True for fs in source.filesystems)
    assert [call.source_id for call in source.close_calls] == [1, 2, 3]
    assert all(transport.closed for transport in transports)
    assert all(
        ("GET", "/arc/files") in transport.requests
        or ("GET", "/arc/nodes/docs/blob.bin.missing") in transport.requests
        for transport in transports
    )


def test_native_vosfs_base_mkdir_profile_uses_only_mocked_transport() -> None:
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

    _exercise_mkdir_profile("vos", source, "/docs", parent_file_category="not found")

    assert all(isinstance(fs, VOSpaceFileSystem) for fs in source.filesystems)
    assert [transport.requests for transport in transports] == [
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes"),
            ("PUT", "/arc/nodes/docs/subdir"),
            ("GET", "/arc/nodes/docs/subdir"),
        ],
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes"),
            ("PUT", "/arc/nodes/docs/notes.txt"),
        ],
        [
            ("GET", "/arc/capabilities"),
            ("GET", "/arc/nodes"),
            ("PUT", "/arc/nodes/docs/notes.txt/child"),
        ],
    ]
    assert all(transport.closed for transport in transports)
