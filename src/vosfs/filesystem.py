"""The public ``vos`` fsspec filesystem for the OpenCADC VOSpace profile.

This module hosts :class:`VOSpaceFileSystem`, the single public class of the
package. Behaviour is layered across cohesive internal modules (paths,
configuration, transport, and so on); this class composes them into the fsspec
async filesystem contract.
"""

from __future__ import annotations

import asyncio
import datetime
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import httpx
from fsspec.asyn import AsyncFileSystem, sync

from vosfs import capabilities, config, errors, negotiate, nodes, paths
from vosfs.transport import ClientPool, build_timeout

if TYPE_CHECKING:
    from collections.abc import Mapping

    from vosfs.capabilities import ServiceBindings
    from vosfs.negotiate import NegotiatedEndpoint
    from vosfs.nodes import Node

_SECURITY_METHOD_BY_CREDENTIAL = {
    "anonymous": capabilities.ANONYMOUS_METHOD,
    "token": capabilities.TOKEN_METHOD,
    "certificate": capabilities.CERTIFICATE_METHOD,
}
_HTTP_OK = 200
_HTTP_SEE_OTHER = 303


class VOSpaceFileSystem(AsyncFileSystem):
    """An asynchronous fsspec filesystem for the OpenCADC VOSpace profile.

    The service is selected only by ``endpoint_url``. The apparent authority
    after the ``vos://`` protocol marker is part of the filesystem path, not a
    service or VOSpace authority.
    """

    protocol = "vos"
    async_impl = True
    cachable = True
    # ``transport`` is the single internal HTTP transport seam and ``loop`` is a
    # runtime handle; neither is part of the serialized ``storage_options``.
    _strip_tokenize_options = ("transport", "loop")

    def __init__(  # noqa: PLR0913 - the public transport options are the contract (section 3)
        self,
        endpoint_url: str,
        *,
        token: str | None = None,
        tokenfile: str | None = None,
        certfile: str | None = None,
        timeouts: Mapping[str, float] | None = None,
        trust_env: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        asynchronous: bool = False,
        loop: Any = None,  # noqa: ANN401 - fsspec passes an opaque event loop
        batch_size: int | None = None,
        **storage_options: Any,  # noqa: ANN401 - fsspec passthrough options
    ) -> None:
        """Construct a filesystem bound to one OpenCADC VOSpace deployment.

        Args:
            endpoint_url: Absolute OpenCADC service base URL.
            token: Literal bearer token, including an OIDC access token.
            tokenfile: File whose bearer token is reread before every request.
            certfile: Combined X.509 certificate-chain and private-key PEM file.
            timeouts: Optional finite positive ``connect``/``read``/``write``/
                ``pool`` inactivity limits.
            trust_env: Whether HTTPX honours proxy and CA environment handling.
            transport: Test-only HTTP transport injected at the internal seam.
                Never appears in ``storage_options``.
            asynchronous: fsspec async-mode flag.
            loop: fsspec event loop handle; not serialized.
            batch_size: fsspec bulk-operation concurrency hint.
            storage_options: Remaining fsspec options (``skip_instance_cache``,
                ``use_listings_cache``, ``listings_expiry_time``, ``max_paths``).
        """
        config.reject_forbidden_options(storage_options)
        super().__init__(
            asynchronous=asynchronous,
            loop=loop,
            batch_size=batch_size,
            **storage_options,
        )
        self._credential = config.resolve_credential(
            token=token,
            tokenfile=tokenfile,
            certfile=certfile,
            environ=os.environ,
        )
        self.endpoint_url = config.validate_endpoint(
            endpoint_url,
            has_credential=not self._credential.is_anonymous,
        )
        self.timeouts = config.resolve_timeouts(timeouts)
        self.trust_env = trust_env
        self._pool = ClientPool(
            certfile=self._credential.certfile,
            trust_env=trust_env,
            timeout=build_timeout(self.timeouts),
            injected_transport=transport,
        )
        self._bindings: ServiceBindings | None = None
        self._bindings_lock: asyncio.Lock | None = None
        self._authority: str | None = None

    @classmethod
    def _strip_protocol(cls, path: str) -> str:
        """Normalize a user path to the canonical internal VOSpace path."""
        return paths.strip_protocol(path)

    def _security_method(self) -> str:
        """Return the security-method identifier for the configured credential."""
        return _SECURITY_METHOD_BY_CREDENTIAL[self._credential.method]

    async def _get_bindings(self) -> ServiceBindings:
        """Return the service bindings, discovering them once per instance.

        The binding cache is immutable for the instance: it is fetched on first
        I/O, is never refreshed by directory-cache invalidation, and is rebuilt
        only by reconstruction.
        """
        if self._bindings is not None:
            return self._bindings
        if self._bindings_lock is None:
            self._bindings_lock = asyncio.Lock()
        async with self._bindings_lock:
            if self._bindings is None:
                self._bindings = await self._discover_bindings()
            return self._bindings

    async def _discover_bindings(self) -> ServiceBindings:
        """Fetch and parse the VOSI capabilities document."""
        response = await self._send_to_service(
            "GET", self.endpoint_url + "/capabilities"
        )
        if response.status_code != _HTTP_OK:
            body = errors.bounded_text(response.content)
            raise errors.http_exception(
                response.status_code, body=body, path="/capabilities"
            )
        return capabilities.parse_bindings(
            response.content, security_method=self._security_method()
        )

    async def _send_to_service(
        self,
        method: str,
        url: str,
        *,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        """Send an authenticated request to the service origin (node/capabilities).

        The configured credential is applied by origin: a bearer header for the
        token method, or the certificate client for the certificate method.
        """
        request_headers = dict(headers or {})
        use_cert = False
        if self._credential.method == "token":
            bearer = self._credential.read_bearer()
            request_headers["Authorization"] = f"Bearer {bearer}"
        elif self._credential.method == "certificate":
            use_cert = True
        request = httpx.Request(method, url, headers=request_headers, content=content)
        try:
            return await self._pool.send(request, use_cert=use_cert, stream=stream)
        except httpx.HTTPError as exc:
            raise errors.transport_exception(exc, path=url) from exc

    # -- node metadata and listing -------------------------------------------

    async def _get_node_document(self, path: str) -> bytes:
        """GET the node document for ``path`` or raise the mapped exception."""
        bindings = await self._get_bindings()
        url = bindings.require_nodes() + paths.encode_url_path(path)
        response = await self._send_to_service("GET", url, headers=nodes.XML_HEADERS)
        if response.status_code != _HTTP_OK:
            body = errors.bounded_text(response.content)
            raise errors.http_exception(response.status_code, body=body, path=path)
        return response.content

    def _parse_and_note(self, data: bytes) -> Node:
        """Parse a node document and record or validate its VOSpace authority."""
        node = nodes.parse_node(data)
        self._note_authority(node.uri)
        return node

    def _note_authority(self, uri: str) -> None:
        """Record the VOSpace authority on first sight, then require it to match.

        The authority is discovered from the URI returned by the root node,
        cached per instance, and every later node URI must carry the same one.
        """
        authority = _authority_of(uri)
        if self._authority is None:
            self._authority = authority
        elif authority != self._authority:
            msg = f"node URI authority {authority!r} does not match {self._authority!r}"
            raise errors.VOSpaceError(msg)

    async def _info(self, path: str, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401 - fsspec hook signature
        """Return the fsspec metadata for ``path`` or raise ``FileNotFoundError``."""
        path = self._strip_protocol(path)
        node = self._parse_and_note(await self._get_node_document(path))
        return nodes.to_info(node, path)

    async def _ls(self, path: str, detail: bool = True, **_kwargs: Any) -> list[Any]:  # noqa: ANN401, FBT001, FBT002 - fsspec hook signature
        """List the immediate children of ``path`` (or the node itself if a file)."""
        path = self._strip_protocol(path)
        data = await self._get_node_document(path)
        node = self._parse_and_note(data)
        if node.node_type != "container":
            entries = [nodes.to_info(node, path)]
        else:
            entries = [
                self._child_info(path, child) for child in nodes.parse_listing(data)
            ]
        if detail:
            return entries
        return [entry["name"] for entry in entries]

    def _child_info(self, parent: str, child: Node) -> dict[str, Any]:
        """Build the info dict for a listing child under ``parent``."""
        name = _child_name(child.uri)
        child_path = f"/{name}" if parent == "/" else f"{parent}/{name}"
        self._note_authority(child.uri)
        return nodes.to_info(child, child_path)

    async def _modified(self, path: str) -> datetime.datetime:
        """Return the OpenCADC modification date for ``path``."""
        path = self._strip_protocol(path)
        node = self._parse_and_note(await self._get_node_document(path))
        if node.mtime is None:
            msg = f"no modification date is available for {path}"
            raise errors.VOSpaceError(msg)
        return _parse_datetime(node.mtime)

    # -- synchronous byte negotiation ----------------------------------------

    async def _require_authority(self) -> str:
        """Return the discovered VOSpace authority, discovering it if needed."""
        if self._authority is None:
            self._parse_and_note(await self._get_node_document("/"))
        if self._authority is None:  # pragma: no cover - root always carries a URI
            msg = "unable to discover the VOSpace authority"
            raise errors.VOSpaceError(msg)
        return self._authority

    async def _negotiate(
        self, path: str, *, direction: str, protocol_uri: str
    ) -> NegotiatedEndpoint:
        """Negotiate a byte endpoint for one logical transfer of ``path``.

        Builds a VOSpace 2.1 transfer document with one authority-qualified
        target, POSTs it to the discovered sync binding with redirects disabled,
        follows the single 303 to the transfer details, and chooses a protocol
        whose security method is compatible with the configured credential.
        """
        bindings = await self._get_bindings()
        sync_url = bindings.require_sync()
        authority = await self._require_authority()
        target = negotiate.build_target_uri(authority, path)
        document = nodes.build_transfer_document(
            target, direction=direction, protocols=[protocol_uri]
        )
        post = await self._send_to_service(
            "POST", sync_url, content=document, headers=nodes.XML_HEADERS
        )
        if post.status_code != _HTTP_SEE_OTHER:
            body = errors.bounded_text(post.content)
            raise errors.http_exception(post.status_code, body=body, path=path)
        location = negotiate.validate_redirect(
            post.headers.get("location"),
            base=sync_url,
            sending_bearer=self._credential.method == "token",
        )
        details = await self._send_to_service(
            "GET", location, headers=nodes.XML_HEADERS
        )
        if details.status_code != _HTTP_OK:
            body = errors.bounded_text(details.content)
            raise errors.http_exception(details.status_code, body=body, path=path)
        protocol = negotiate.choose_protocol(
            negotiate.parse_transfer_details(details.content),
            self._security_method(),
        )
        return negotiate.NegotiatedEndpoint(protocol.endpoint, protocol.security_method)

    def _byte_routing(
        self, endpoint: NegotiatedEndpoint
    ) -> tuple[dict[str, str], bool]:
        """Return the headers and cert flag for a negotiated byte request.

        Credentials are routed by the negotiated security method: a
        pre-authorized or anonymous endpoint gets nothing; a token endpoint gets
        a freshly resolved bearer header over https; a certificate endpoint uses
        the X.509 client over https.
        """
        method = endpoint.security_method
        if method == capabilities.ANONYMOUS_METHOD:
            return {}, False
        scheme = urlsplit(endpoint.url).scheme
        if method == capabilities.TOKEN_METHOD:
            if scheme != "https":
                msg = "a token byte endpoint must use https"
                raise errors.VOSpaceError(msg)
            return {"Authorization": f"Bearer {self._credential.read_bearer()}"}, False
        if method == capabilities.CERTIFICATE_METHOD:
            if scheme != "https":
                msg = "a certificate byte endpoint must use https"
                raise errors.VOSpaceError(msg)
            return {}, True
        msg = f"unsupported negotiated security method: {method!r}"  # pragma: no cover
        raise errors.VOSpaceError(msg)  # pragma: no cover

    async def _byte_send(
        self,
        endpoint: NegotiatedEndpoint,
        method: str,
        *,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        """Perform the one byte GET/HEAD/PUT against a negotiated endpoint.

        A redirect (3xx) response fails: only the approved synchronous-transfer
        303 chain may redirect.
        """
        request_headers, use_cert = self._byte_routing(endpoint)
        request_headers.update(headers or {})
        request = httpx.Request(
            method, endpoint.url, headers=request_headers, content=content
        )
        try:
            response = await self._pool.send(request, use_cert=use_cert, stream=stream)
        except httpx.HTTPError as exc:
            raise errors.transport_exception(exc, path=endpoint.url) from exc
        if response.is_redirect:
            if stream:
                await response.aclose()
            msg = f"unexpected redirect from byte endpoint: {response.status_code}"
            raise errors.VOSpaceError(msg, status=response.status_code)
        return response

    async def aclose(self) -> None:
        """Close every realized HTTP client and evict the instance (idempotent).

        After this call the instance is removed from fsspec's instance cache and
        any later HTTP I/O fails as closed.
        """
        await self._pool.aclose()
        # Evict just this instance; fsspec's public API only clears the whole cache.
        type(self)._cache.pop(self._fs_token, None)  # noqa: SLF001

    def close(self) -> None:
        """Synchronously close the filesystem, bridging through the fsspec loop.

        Raises:
            RuntimeError: If called on an ``asynchronous=True`` instance, which
                must be closed with ``await aclose()`` instead.
        """
        if self.asynchronous:
            msg = "use 'await aclose()' to close an asynchronous filesystem"
            raise RuntimeError(msg)
        sync(self.loop, self.aclose)


def _authority_of(uri: str) -> str:
    """Return the VOSpace authority carried by a ``vos://authority/...`` URI."""
    _scheme, separator, rest = uri.partition("://")
    if not separator or not rest:
        msg = f"node URI is not a VOSpace URI: {uri!r}"
        raise errors.VOSpaceError(msg)
    return rest.split("/", 1)[0]


def _child_name(uri: str) -> str:
    """Return the decoded final segment of a child node URI."""
    return unquote(uri.rstrip("/").rsplit("/", 1)[-1])


def _parse_datetime(value: str) -> datetime.datetime:
    """Parse an ISO 8601 modification date, tolerating a trailing ``Z``."""
    return datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
