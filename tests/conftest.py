"""Shared hermetic test harness for vosfs.

Every hermetic test drives the filesystem through the single internal HTTP
transport seam: a ``respx`` router is injected as an ``httpx`` mock transport
via the ``transport`` constructor option, which never appears in
``storage_options``. Unmatched requests raise, so tests cannot reach the
network.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit

import httpx
import pytest
import respx

from vosfs import VOSpaceFileSystem

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

BASE_URL = "https://staging.canfar.net/arc"
NODES_URL = f"{BASE_URL}/nodes"
SYNC_URL = f"{BASE_URL}/synctrans"
AUTHORITY = "example.test!vault"

CAPABILITIES = f"""<?xml version="1.0" encoding="UTF-8"?>
<vosi:capabilities xmlns:vosi="http://www.ivoa.net/xml/VOSICapabilities/v1.0"
                   xmlns:vs="http://www.ivoa.net/xml/VODataService/v1.1"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <capability standardID="ivo://ivoa.net/std/VOSpace/v2.0#nodes">
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="base">{NODES_URL}</accessURL>
    </interface>
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="base">{NODES_URL}</accessURL>
      <securityMethod standardID="ivo://ivoa.net/sso#token"/>
    </interface>
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="base">{NODES_URL}</accessURL>
      <securityMethod standardID="ivo://ivoa.net/sso#tls-with-certificate"/>
    </interface>
  </capability>
  <capability standardID="ivo://ivoa.net/std/VOSpace#sync-2.1">
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="full">{SYNC_URL}</accessURL>
    </interface>
    <interface xsi:type="vs:ParamHTTP" role="std">
      <accessURL use="full">{SYNC_URL}</accessURL>
      <securityMethod standardID="ivo://ivoa.net/sso#token"/>
      <securityMethod standardID="ivo://ivoa.net/sso#tls-with-certificate"/>
    </interface>
  </capability>
</vosi:capabilities>
""".encode()


ROOT_CONTAINER = (
    f'<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
    f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    f'xsi:type="vos:ContainerNode" uri="vos://{AUTHORITY}">'
    "<vos:properties/><vos:nodes/></vos:node>"
).encode()


def mock_capabilities(router: respx.Router) -> None:
    """Register the standard capabilities response on the router."""
    router.get("/capabilities").mock(
        return_value=httpx.Response(200, content=CAPABILITIES)
    )


def transfer_details(endpoint: str) -> bytes:
    """Return a transfer-details document advertising one anonymous endpoint."""
    return (
        f'<vos:transfer xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" version="2.1">'
        f'<vos:protocol uri="ivo://ivoa.net/vospace/core#httpsget">'
        f"<vos:endpoint>{endpoint}</vos:endpoint></vos:protocol></vos:transfer>"
    ).encode()


def target_path(content: bytes | None) -> str:
    """Extract the internal path from a transfer document's target element."""
    match = re.search(r"<[^>]*target[^>]*>([^<]+)</", (content or b"").decode())
    if match is None:
        return "/"
    prefix = f"vos://{AUTHORITY}"
    return match.group(1).strip()[len(prefix) :] or "/"


def data_node_response(
    request: httpx.Request, files: dict[str, bytes]
) -> httpx.Response:
    """Return root or DataNode metadata for the transfer test helper."""
    suffix = unquote(request.url.path.removeprefix(urlsplit(NODES_URL).path))
    path = suffix or "/"
    if path == "/":
        return httpx.Response(200, content=ROOT_CONTAINER)
    if path not in files:
        return httpx.Response(404)
    length_uri = "ivo://ivoa.net/vospace/core#length"
    document = (
        f'<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:type="vos:DataNode" uri="vos://{AUTHORITY}{path}">'
        f'<vos:properties><vos:property uri="{length_uri}">'
        f"{len(files[path])}</vos:property></vos:properties></vos:node>"
    ).encode()
    return httpx.Response(200, content=document)


def mock_transfers(router: respx.Router, files: dict[str, bytes]) -> None:
    """Wire path-aware synchronous negotiation and byte endpoints.

    Each key in ``files`` is an internal path served at a per-path byte
    endpoint; the negotiation POST and transfer-details GET route to it.
    """
    mock_capabilities(router)

    router.get(url__regex=rf"^{re.escape(NODES_URL)}(?:/.*)?$").mock(
        side_effect=lambda request: data_node_response(request, files)
    )

    def negotiate_post(request: httpx.Request) -> httpx.Response:
        details = f"{BASE_URL}/details?t={quote(target_path(request.content))}"
        return httpx.Response(303, headers={"Location": details})

    router.post(SYNC_URL).mock(side_effect=negotiate_post)

    def details_get(request: httpx.Request) -> httpx.Response:
        path = request.url.params["t"]
        endpoint = f"{BASE_URL}/files?p={quote(path)}"
        return httpx.Response(200, content=transfer_details(endpoint))

    router.get(url__regex=rf"^{re.escape(BASE_URL)}/details").mock(
        side_effect=details_get
    )

    async def _stream(data: bytes) -> AsyncIterator[bytes]:
        yield data

    def byte_op(request: httpx.Request) -> httpx.Response:
        path = request.url.params["p"]
        if request.method in ("PUT", "POST"):
            files[path] = request.content
            return httpx.Response(201)
        if path not in files:
            return httpx.Response(404)
        content = files[path]
        if content == b"":
            return httpx.Response(204)
        # An async-generator body makes the mock response genuinely streamable.
        return httpx.Response(200, content=_stream(content))

    router.route(url__regex=rf"^{re.escape(BASE_URL)}/files").mock(side_effect=byte_op)


@pytest.fixture
def router() -> Iterator[respx.Router]:
    """A respx router bound to the test service base URL.

    Unmatched requests raise (blocking network access). Tests that want to
    assert every route was called set ``router.assert_all_called`` or use
    ``assert_all_called=True`` per route.
    """
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    yield router
    router.reset()


def make_fs(
    router: respx.Router,
    *,
    asynchronous: bool = False,
    endpoint_override: str | None = None,
    **options: Any,
) -> VOSpaceFileSystem:
    """Build a filesystem whose transport seam is the given respx router."""
    transport = httpx.MockTransport(router.async_handler)
    return VOSpaceFileSystem(
        endpoint_override or BASE_URL,
        transport=transport,
        asynchronous=asynchronous,
        skip_instance_cache=True,
        **options,
    )
