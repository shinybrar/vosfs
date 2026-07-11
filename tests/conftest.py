"""Shared hermetic test harness for vosfs.

Every hermetic test drives the filesystem through the single internal HTTP
transport seam: a ``respx`` router is injected as an ``httpx`` mock transport
via the ``transport`` constructor option, which never appears in
``storage_options``. Unmatched requests raise, so tests cannot reach the
network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from vosfs import VOSpaceFileSystem

if TYPE_CHECKING:
    from collections.abc import Iterator

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


def mock_capabilities(router: respx.Router) -> None:
    """Register the standard capabilities response on the router."""
    router.get("/capabilities").mock(
        return_value=httpx.Response(200, content=CAPABILITIES)
    )


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
