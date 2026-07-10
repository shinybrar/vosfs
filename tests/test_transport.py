"""Tests for the HTTPX client pool, lifecycle, and serialization (section 12)."""

import pickle

import httpx
import pytest
import respx
from conftest import BASE_URL, make_fs

from vosfs import VOSpaceFileSystem
from vosfs.transport import DEFAULT_TIMEOUTS, ClientPool, build_timeout


def _pool(**kwargs: object) -> ClientPool:
    defaults = {
        "certfile": None,
        "trust_env": True,
        "timeout": build_timeout(None),
        "injected_transport": None,
    }
    defaults.update(kwargs)
    return ClientPool(**defaults)  # type: ignore[arg-type]


# --- timeouts -------------------------------------------------------------------


def test_default_timeout_values() -> None:
    timeout = build_timeout(None)
    assert timeout.connect == DEFAULT_TIMEOUTS["connect"]
    assert timeout.read == DEFAULT_TIMEOUTS["read"]
    assert timeout.write == DEFAULT_TIMEOUTS["write"]
    assert timeout.pool == DEFAULT_TIMEOUTS["pool"]


def test_timeout_overrides() -> None:
    timeout = build_timeout({"read": 5.0})
    assert timeout.read == 5.0
    assert timeout.connect == DEFAULT_TIMEOUTS["connect"]


# --- lazy, TLS-keyed, single-instance construction ------------------------------


async def test_client_is_built_once_per_key(router: respx.Router) -> None:
    transport = httpx.MockTransport(router.async_handler)
    pool = _pool(injected_transport=transport)
    first = await pool.client()
    second = await pool.client()
    assert first is second
    assert first.follow_redirects is False
    assert first.auth is None
    await pool.aclose()


async def test_plain_production_client_builds_without_network() -> None:
    pool = _pool()  # no injected transport, no certfile
    client = await pool.client()
    assert isinstance(client, httpx.AsyncClient)
    await pool.aclose()


async def test_cert_client_requested_without_certfile_raises() -> None:
    transport = httpx.MockTransport(respx.Router().async_handler)
    pool = _pool(injected_transport=transport)
    with pytest.raises(ValueError, match="no certificate"):
        await pool.client(use_cert=True)
    await pool.aclose()


# --- no cookie jar --------------------------------------------------------------


async def test_no_cookie_is_resent_after_set_cookie(router: respx.Router) -> None:
    seen: dict[str, str | None] = {}
    router.get("/a").mock(
        return_value=httpx.Response(200, headers={"Set-Cookie": "sid=abc; Path=/"})
    )

    def capture(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200)

    router.get("/b").side_effect = capture
    transport = httpx.MockTransport(router.async_handler)
    pool = _pool(injected_transport=transport)
    await pool.send(httpx.Request("GET", f"{BASE_URL}/a"))
    await pool.send(httpx.Request("GET", f"{BASE_URL}/b"))
    assert seen["cookie"] is None
    await pool.aclose()


# --- close semantics ------------------------------------------------------------


async def test_aclose_is_idempotent_and_blocks_later_io(router: respx.Router) -> None:
    transport = httpx.MockTransport(router.async_handler)
    pool = _pool(injected_transport=transport)
    await pool.client()
    await pool.aclose()
    await pool.aclose()  # idempotent
    assert pool.closed is True
    with pytest.raises(ValueError, match="closed"):
        await pool.client()
    with pytest.raises(ValueError, match="closed"):
        await pool.send(httpx.Request("GET", f"{BASE_URL}/x"))


async def test_filesystem_aclose_evicts_instance_cache(router: respx.Router) -> None:
    transport = httpx.MockTransport(router.async_handler)
    fs = VOSpaceFileSystem(BASE_URL, transport=transport, asynchronous=True)
    assert fs._fs_token in type(fs)._cache
    await fs.aclose()
    assert fs._fs_token not in type(fs)._cache


def test_sync_close_bridges_through_loop(router: respx.Router) -> None:
    fs = make_fs(router)  # synchronous instance
    fs.close()
    assert fs._pool.closed is True


async def test_async_close_rejects_sync_close(router: respx.Router) -> None:
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(RuntimeError, match="aclose"):
        fs.close()
    await fs.aclose()


# --- serialization --------------------------------------------------------------


def test_pickle_round_trip_before_and_after_client_creation(
    router: respx.Router,
) -> None:
    fs = make_fs(router, token="lit")
    restored = pickle.loads(pickle.dumps(fs))  # noqa: S301 - trusted local round-trip
    assert restored.endpoint_url == fs.endpoint_url
    assert restored._credential.token_literal == "lit"
    # Live pool is never serialized; reconstruction builds a fresh one.
    assert restored._pool is not fs._pool


async def test_pickle_round_trip_after_client_realized(router: respx.Router) -> None:
    fs = make_fs(router, asynchronous=True)
    await fs._pool.client()  # realize a live client
    restored = pickle.loads(pickle.dumps(fs))  # noqa: S301
    assert restored.endpoint_url == fs.endpoint_url
    assert not restored._pool.closed
    await fs.aclose()


def test_json_round_trip(router: respx.Router) -> None:
    fs = make_fs(router, token="lit")
    blob = fs.to_json()
    assert "transport" not in blob
    restored = VOSpaceFileSystem.from_json(blob)
    assert restored.endpoint_url == fs.endpoint_url
