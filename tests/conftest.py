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
