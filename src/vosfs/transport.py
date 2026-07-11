"""HTTPX client pool and lifecycle for the vos filesystem.

HTTPX is the sole production HTTP client. The pool is keyed by TLS
configuration and built lazily and concurrency-safely: one validating
no-client-certificate client for anonymous, bearer, and pre-authorized
requests, and — when a certificate is configured — one validating client whose
fresh ``ssl.SSLContext`` loads the combined PEM. Bearer credentials are
per-request headers, never pool keys.
"""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

# Default HTTP timeouts in seconds, fixed by the contract (section 3).
DEFAULT_TIMEOUTS: dict[str, float] = {
    "connect": 10.0,
    "read": 60.0,
    "write": 60.0,
    "pool": 10.0,
}

_PLAIN = "plain"
_CERT = "cert"


def build_timeout(overrides: Mapping[str, float] | None) -> httpx.Timeout:
    """Return an ``httpx.Timeout`` from the defaults plus any overrides."""
    values = dict(DEFAULT_TIMEOUTS)
    if overrides:
        values.update(overrides)
    return httpx.Timeout(
        connect=values["connect"],
        read=values["read"],
        write=values["write"],
        pool=values["pool"],
    )


class ClientPool:
    """A lazily-populated, TLS-keyed pool of HTTPX async clients.

    At most one client is created per TLS key per pool and event loop. Every
    client disables redirects, client-level auth, and transport retries, and
    holds no cookie jar: response cookies are discarded after each send so no
    ``Cookie`` header is ever derived from a prior response.
    """

    def __init__(
        self,
        *,
        certfile: str | None,
        trust_env: bool,
        timeout: httpx.Timeout,
        injected_transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        """Configure the pool; no client is built until first use."""
        self._certfile = certfile
        self._trust_env = trust_env
        self._timeout = timeout
        self._injected = injected_transport
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._lock: asyncio.Lock | None = None
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            msg = "I/O operation on closed filesystem"
            raise ValueError(msg)

    def _get_lock(self) -> asyncio.Lock:
        """Return the pool lock, creating it lazily on first contention."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def client(self, *, use_cert: bool = False) -> httpx.AsyncClient:
        """Return the pooled client for the requested TLS configuration.

        Raises:
            ValueError: If the pool is closed, or a certificate client is
                requested without a configured certificate.
        """
        self._ensure_open()
        if use_cert and self._certfile is None:
            msg = "no certificate is configured for a certificate transfer"
            raise ValueError(msg)
        key = _CERT if use_cert else _PLAIN
        if key in self._clients:
            return self._clients[key]
        async with self._get_lock():
            # Re-check under the lock: a concurrent ``aclose`` may have run
            # between the fast-path check and lock acquisition, so a client must
            # never be built (and leaked) into an already-closed pool.
            self._ensure_open()
            if key not in self._clients:
                self._clients[key] = self._build(key)
            return self._clients[key]

    def _build(self, key: str) -> httpx.AsyncClient:
        """Construct one client for the given TLS key."""
        return httpx.AsyncClient(
            transport=self._transport_for(key),
            follow_redirects=False,
            trust_env=self._trust_env,
            timeout=self._timeout,
            auth=None,
        )

    def _transport_for(self, key: str) -> httpx.AsyncBaseTransport:
        """Return the transport for a TLS key: injected, certificate, or plain."""
        if self._injected is not None:
            return self._injected
        # Real client-certificate TLS needs a genuine PEM; the live gate covers it.
        if key == _CERT:  # pragma: no cover
            return self._build_cert_transport()
        return httpx.AsyncHTTPTransport(
            verify=True, retries=0, trust_env=self._trust_env
        )

    def _build_cert_transport(self) -> httpx.AsyncHTTPTransport:  # pragma: no cover
        """Build a client-certificate transport (exercised by the live gate).

        Real client-certificate TLS requires a genuine combined PEM, so this
        path is covered by the credential-gated integration suite rather than
        the hermetic tests.
        """
        certfile = self._certfile
        if certfile is None:
            msg = "no certificate is configured for a certificate transfer"
            raise ValueError(msg)
        context = ssl.create_default_context()
        context.load_cert_chain(certfile)
        return httpx.AsyncHTTPTransport(
            verify=context, retries=0, trust_env=self._trust_env
        )

    async def send(
        self,
        request: httpx.Request,
        *,
        use_cert: bool = False,
        stream: bool = False,
    ) -> httpx.Response:
        """Send one request through the pooled client, holding no cookies.

        The client's cookie jar is cleared after every send so a ``Set-Cookie``
        response can never cause a later request to carry a ``Cookie`` header.
        """
        client = await self.client(use_cert=use_cert)
        response = await client.send(request, stream=stream)
        client.cookies.clear()
        return response

    async def aclose(self) -> None:
        """Close every realized client and mark the pool closed (idempotent).

        The closed flag is set and the client map is drained under the same lock
        that guards lazy client creation, so a waiter cannot build and store an
        open client after the pool has been closed.
        """
        async with self._get_lock():
            self._closed = True
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            await client.aclose()

    @property
    def closed(self) -> bool:
        """Whether the pool has been closed."""
        return self._closed
