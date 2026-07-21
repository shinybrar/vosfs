"""The public ``vos`` fsspec filesystem for the OpenCADC VOSpace profile.

This module hosts :class:`VOSpaceFileSystem`, the single public class of the
package. Behaviour is layered across cohesive internal modules (paths,
configuration, transport, and so on); this class composes them into the fsspec
async filesystem contract.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import datetime
import errno
import hashlib
import os
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, overload
from urllib.parse import urlsplit

import httpx
from fsspec.asyn import AsyncFileSystem, _get_batch_size, _run_coros_in_chunks, sync
from fsspec.callbacks import DEFAULT_CALLBACK
from fsspec.compression import compr
from fsspec.core import get_compression

from vosfs import capabilities, config, errors, negotiate, nodes, paths, staging
from vosfs.transport import ClientPool, build_timeout

if TYPE_CHECKING:
    import io
    from collections.abc import (
        AsyncIterator,
        Awaitable,
        Callable,
        Generator,
        Mapping,
        Sequence,
    )

    from fsspec.callbacks import Callback

    from vosfs.capabilities import ServiceBindings
    from vosfs.negotiate import NegotiatedEndpoint
    from vosfs.nodes import Node

_SECURITY_METHOD_BY_CREDENTIAL = {
    "anonymous": capabilities.ANONYMOUS_METHOD,
    "token": capabilities.TOKEN_METHOD,
    "certificate": capabilities.CERTIFICATE_METHOD,
}
_HTTP_OK = 200
_HTTP_CREATED = 201
_HTTP_NO_CONTENT = 204
_HTTP_SEE_OTHER = 303
_HTTP_PRECONDITION_FAILED = 412
_IDENTITY_ENCODING = {"Accept-Encoding": "identity"}
_READ_CHUNK = 1 << 20
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_GET_CONTAINER_MARKER = "_vosfs_materialize_get_container"


class _CoordinatedWriteState:
    """Operation-scoped upload state and owned child tasks."""

    def __init__(self, owner: object) -> None:
        self.owner = owner
        self.owner_task = asyncio.current_task()
        self.active = True
        self.tasks: set[asyncio.Task[object]] = set()
        self.lock = asyncio.Lock()
        self.materialized: set[str] = set()
        self.failure: Exception | None = None


_COORDINATED_WRITE_STATE: contextvars.ContextVar[_CoordinatedWriteState | None] = (
    contextvars.ContextVar("vosfs_coordinated_write_state", default=None)
)


@contextlib.asynccontextmanager
async def _write_scope(owner: object) -> AsyncIterator[None]:
    """Bind one bulk write and drain every child before returning."""
    state = _CoordinatedWriteState(owner)
    token = _COORDINATED_WRITE_STATE.set(state)
    body_error: BaseException | None = None
    try:
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 - drain before propagation
            body_error = exc
    finally:
        state.active = False
        try:
            cleanup = asyncio.create_task(_drain_write_tasks(state))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError as exc:  # noqa: PERF203 - drain loop
                    body_error = exc
            cleanup.result()
        finally:
            _COORDINATED_WRITE_STATE.reset(token)
    if body_error is not None:
        raise body_error


def _join_write_scope(owner: object) -> _CoordinatedWriteState | None:
    """Return owner state and register calling child task."""
    state = _COORDINATED_WRITE_STATE.get()
    if state is None or state.owner is not owner:
        return None
    task = asyncio.current_task()
    if task is not None and task is not state.owner_task and task not in state.tasks:
        state.tasks.add(task)
        task.add_done_callback(partial(_discard_write_task, state))
    if not state.active:
        raise asyncio.CancelledError
    return state


def _discard_write_task(
    state: _CoordinatedWriteState,
    task: asyncio.Task[object],
) -> None:
    """Retrieve child outcome and release completed task ownership."""
    with contextlib.suppress(asyncio.CancelledError):
        task.exception()
    state.tasks.discard(task)


async def _drain_write_tasks(state: _CoordinatedWriteState) -> None:
    """Cancel and await registered children, including late joiners."""
    observed: set[asyncio.Task[object]] | None = None
    while observed != state.tasks:
        observed = set(state.tasks)
        await asyncio.sleep(0)
        registered = list(state.tasks)
        for task in registered:
            if not task.done():
                task.cancel()
        if registered:
            await asyncio.gather(*registered, return_exceptions=True)


class _DeferredAwaitable:
    """Create child coroutine only when fsspec schedules this inert awaitable."""

    __slots__ = ("_factory", "_owner")

    def __init__(
        self,
        owner: object,
        factory: Callable[[], Awaitable[Any]],
    ) -> None:
        self._owner = owner
        self._factory = factory

    def __await__(self) -> Generator[Any, None, Any]:
        _join_write_scope(self._owner)
        return self._factory().__await__()


class _DeferredBranchCallback:
    """Delegate fsspec progress while deferring each branched upload."""

    def __init__(self, callback: Callback, owner: object) -> None:
        self._callback = callback
        self._owner = owner

    def set_size(self, size: int) -> None:
        self._callback.set_size(size)

    def relative_update(self, inc: int = 1) -> None:
        self._callback.relative_update(inc)

    def branch_coro(
        self,
        function: Callable[..., Awaitable[Any]],
    ) -> Callable[..., _DeferredAwaitable]:
        """Preserve caller callback wrapper without starting it eagerly."""
        wrapped = self._callback.branch_coro(function)

        def deferred(
            path1: str,
            path2: str,
            **kwargs: Any,  # noqa: ANN401 - fsspec forwards hook options
        ) -> _DeferredAwaitable:
            return _DeferredAwaitable(
                self._owner,
                partial(wrapped, path1, path2, **kwargs),
            )

        return deferred


class _InheritedWriteAdapter:
    """Make inherited bulk hooks build inert per-file awaitables."""

    def __init__(self, filesystem: VOSpaceFileSystem) -> None:
        self._filesystem = filesystem

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - fsspec hook surface
        return getattr(self._filesystem, name)

    def _pipe_file(
        self,
        path: str,
        value: bytes,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> _DeferredAwaitable:
        return _DeferredAwaitable(
            self._filesystem,
            partial(self._filesystem._pipe_file, path, value, **kwargs),  # noqa: SLF001
        )


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

    def _ensure_usable(self) -> None:
        """Reject closed or fork-inherited runtime state before using it."""
        if self._pid != os.getpid():
            msg = (
                "VOSpaceFileSystem cannot be used after fork; reconstruct it "
                "in the child process from pickle or fsspec JSON"
            )
            raise RuntimeError(msg)
        if self._pool.closed:
            msg = "I/O operation on closed filesystem"
            raise ValueError(msg)

    def __dask_tokenize__(self) -> tuple[Any, tuple[Any, ...], dict[str, Any]]:
        """Tokenize primitive constructor state, independent of worker identity."""
        return type(self), self.storage_args, self.storage_options

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Reconstruct outside fsspec's live instance cache."""
        constructor, (cls, args, options) = super().__reduce__()
        return constructor, (cls, args, {**options, "skip_instance_cache": True})

    def to_dict(self, *, include_password: bool = True) -> dict[str, Any]:
        """Serialize constructor state for fresh fsspec reconstruction."""
        state = super().to_dict(include_password=include_password)
        state["skip_instance_cache"] = True
        return state

    @overload
    @classmethod
    def _strip_protocol(cls, path: str) -> str: ...

    @overload
    @classmethod
    def _strip_protocol(cls, path: list[str]) -> list[str]: ...

    @classmethod
    def _strip_protocol(cls, path: str | list[str]) -> str | list[str]:
        """Normalize a user path, or a list of paths, to canonical VOSpace paths.

        fsspec's bulk coordinators (for example ``get`` with a list of sources)
        forward a list through this hook; each element is normalized
        independently, matching fsspec's base ``_strip_protocol`` contract.
        """
        if isinstance(path, list):
            return [paths.strip_protocol(item) for item in path]
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
        self._ensure_usable()
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
        self._raise_for_status(response, path="/capabilities", allowed=(_HTTP_OK,))
        return capabilities.parse_bindings(
            response.content,
            security_method=self._security_method(),
            endpoint_url=self.endpoint_url,
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
        """Send a request to the service, applying the credential same-origin only.

        The configured credential is attached only when ``url`` is same-origin as
        ``endpoint_url``: a bearer header for the token method, or the certificate
        client for the certificate method. A redirect ``Location`` (for example
        the transfer-details URL) therefore can never route a bearer token or
        client certificate to another host.
        """
        self._ensure_usable()
        request_headers = dict(headers or {})
        use_cert = False
        if _same_origin(url, self.endpoint_url):
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

    def _raise_for_status(
        self,
        response: httpx.Response,
        *,
        path: str,
        allowed: tuple[int, ...],
    ) -> None:
        """Raise the mapped exception when a response status is not allowed."""
        if response.status_code not in allowed:
            body = errors.bounded_text(response.content)
            raise errors.http_exception(
                response.status_code,
                body=body,
                fault=errors.extract_fault(body),
                path=path,
                retry_after=errors.parse_retry_after(
                    response.headers.get("retry-after")
                ),
            )

    # -- node metadata and listing -------------------------------------------

    async def _get_node_document(self, path: str) -> bytes:
        """GET the node document for ``path`` or raise the mapped exception."""
        bindings = await self._get_bindings()
        url = bindings.require_nodes() + paths.encode_url_path(path)
        response = await self._send_to_service("GET", url, headers=nodes.XML_HEADERS)
        self._raise_for_status(response, path=path, allowed=(_HTTP_OK,))
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
        """List the immediate children of ``path`` (or the node itself if a file).

        Container listings are served from and stored in the standard fsspec
        directory cache; mutations invalidate the affected entries.
        """
        self._ensure_usable()
        path = self._strip_protocol(path)
        try:
            entries = self.dircache[path]
        except KeyError:
            entries = await self._fetch_listing(path)
        if detail:
            return entries
        return [entry["name"] for entry in entries]

    async def _fetch_listing(self, path: str) -> list[dict[str, Any]]:
        """Fetch a listing, caching a container's immediate children.

        The document is parsed once for both the node and its children.
        """
        try:
            node, children = nodes.parse_container(await self._get_node_document(path))
            self._note_authority(node.uri)
            if node.node_type != "container":
                entries = [nodes.to_info(node, path)]
            else:
                entries = [self._child_info(path, child) for child in children]
                self.dircache[path] = entries
        except BaseException:
            self.dircache.pop(path, None)
            raise
        return entries

    def _child_info(self, parent: str, child: Node) -> dict[str, Any]:
        """Build the info dict for a listing child under ``parent``."""
        child_path = self._listing_child_path(parent, child.uri)
        return nodes.to_info(child, child_path)

    def _listing_child_path(self, parent: str, uri: str) -> str:
        """Validate and return one server-listed immediate child path."""
        parts = urlsplit(uri)
        if (
            parts.scheme != "vos"
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or not parts.path.startswith("/")
            or parts.path.endswith("/")
            or "//" in parts.path
        ):
            msg = f"listed child URI is not canonical: {uri!r}"
            raise errors.VOSpaceError(msg)
        self._note_authority(uri)
        try:
            child_path = paths.strip_protocol(parts.path)
        except ValueError as exc:
            msg = f"listed child URI is not canonical: {uri!r}"
            raise errors.VOSpaceError(msg) from exc
        if child_path == "/" or paths.parent(child_path) != parent:
            msg = f"listed child is not an immediate descendant of {parent}: {uri!r}"
            raise errors.VOSpaceError(msg)
        return child_path

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
        follows the approved 303 chain to the transfer-details document, and
        chooses a protocol whose security method is compatible with the
        configured credential. Redirect loops and more than five hops fail.
        """
        bindings = await self._get_bindings()
        sync_url = bindings.require_sync()
        authority = await self._require_authority()
        target = f"vos://{authority}{path}"
        document = nodes.build_transfer_document(
            target, direction=direction, protocols=[protocol_uri]
        )
        post = await self._send_to_service(
            "POST", sync_url, content=document, headers=nodes.XML_HEADERS
        )
        self._raise_for_status(post, path=path, allowed=(_HTTP_SEE_OTHER,))
        location = negotiate.validate_redirect(
            post.headers.get("location"),
            base=sync_url,
            sending_bearer=False,
        )
        seen: set[str] = set()
        for _redirect_count in range(1, 6):
            if location in seen:
                msg = "synchronous-transfer redirect loop"
                raise errors.VOSpaceError(msg)
            seen.add(location)
            if negotiate.is_direct_byte_endpoint(location):
                return negotiate.NegotiatedEndpoint(
                    location, capabilities.ANONYMOUS_METHOD
                )
            location = negotiate.validate_redirect(
                location,
                base=location,
                sending_bearer=(
                    self._credential.method == "token"
                    and _same_origin(location, self.endpoint_url)
                ),
            )
            details = await self._send_to_service(
                "GET", location, headers=nodes.XML_HEADERS
            )
            self._raise_for_status(
                details, path=path, allowed=(_HTTP_OK, _HTTP_SEE_OTHER)
            )
            if details.status_code == _HTTP_OK:
                return negotiate.choose_protocol(
                    negotiate.parse_transfer_details(details.content),
                    self._security_method(),
                )
            location = negotiate.validate_redirect(
                details.headers.get("location"),
                base=location,
                sending_bearer=False,
            )
        msg = "synchronous-transfer negotiation returned more than five redirects"
        raise errors.VOSpaceError(msg)

    def _byte_routing(
        self, endpoint: NegotiatedEndpoint
    ) -> tuple[dict[str, str], bool]:
        """Return the headers and cert flag for a negotiated byte request.

        Credentials are routed by the negotiated security method: a
        pre-authorized or anonymous endpoint gets nothing; a token endpoint gets
        a freshly resolved bearer header over https; a certificate endpoint uses
        the X.509 client over https.

        The negotiated endpoint URL is validated first: it must be an absolute
        ``http``/``https`` URL without userinfo. A ``user:pass@host`` endpoint is
        rejected before the request is built, because HTTPX would otherwise
        derive a ``Basic`` ``Authorization`` header from the URL userinfo and
        defeat the credential-routing guarantee.
        """
        method = endpoint.security_method
        parts = urlsplit(endpoint.url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            msg = (
                "negotiated byte endpoint is not an absolute http(s) URL: "
                f"{endpoint.url!r}"
            )
            raise errors.VOSpaceError(msg)
        if parts.username or parts.password:
            msg = "negotiated byte endpoint must not contain userinfo"
            raise errors.VOSpaceError(msg)
        if method == capabilities.ANONYMOUS_METHOD:
            return {}, False
        scheme = parts.scheme
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
        self._ensure_usable()
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

    # -- reading bytes -------------------------------------------------------

    async def _open_read_stream(self, path: str) -> httpx.Response:
        """Negotiate a read and return the open, streaming byte response."""
        endpoint = await self._negotiate(
            path,
            direction=negotiate.DIRECTION_PULL,
            protocol_uri=negotiate.PROTOCOL_HTTPS_GET,
        )
        response = await self._byte_send(
            endpoint, "GET", headers=_IDENTITY_ENCODING, stream=True
        )
        if response.status_code not in (_HTTP_OK, _HTTP_NO_CONTENT):
            body = errors.bounded_text(await response.aread())
            retry_after = errors.parse_retry_after(response.headers.get("retry-after"))
            await response.aclose()
            raise errors.http_exception(
                response.status_code,
                body=body,
                fault=errors.extract_fault(body),
                path=path,
                retry_after=retry_after,
            )
        return response

    async def _read_whole(self, path: str) -> bytes:
        """Download one whole object into memory (an empty 204 reads as ``b''``).

        Consumes the raw response bytes so HTTP content decoding can never alter
        filesystem content, matching the streamed ``_get_file`` path.
        """
        response = await self._open_read_stream(path)
        try:
            if response.status_code == _HTTP_NO_CONTENT:
                return b""
            return b"".join([chunk async for chunk in response.aiter_raw(_READ_CHUNK)])
        finally:
            await response.aclose()

    async def _get(
        self,
        rpath: str | list[str],
        lpath: str | list[str],
        recursive: bool = False,  # noqa: FBT001, FBT002 - fsspec hook signature
        callback: Callback = DEFAULT_CALLBACK,
        maxdepth: int | None = None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[Any] | None:
        """Use fsspec's coordinator while marking entries for container handling."""
        kwargs[_GET_CONTAINER_MARKER] = True
        return await super()._get(
            rpath,
            lpath,
            recursive=recursive,
            callback=callback,
            maxdepth=maxdepth,
            **kwargs,
        )

    async def _get_file(
        self,
        rpath: str,
        lpath: str,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Stream one negotiated whole-object GET to a local file."""
        rpath = self._strip_protocol(rpath)
        if kwargs.pop(_GET_CONTAINER_MARKER, False):
            info = await self._info(rpath)
            if info["type"] == "directory":
                Path(lpath).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
                return
        callback: Callback = kwargs.get("callback", DEFAULT_CALLBACK)
        response = await self._open_read_stream(rpath)
        try:
            size = response.headers.get("content-length")
            callback.set_size(int(size) if size is not None else None)
            with Path(lpath).open("wb") as local:  # noqa: ASYNC230 - staging to local disk
                if response.status_code == _HTTP_NO_CONTENT:
                    return
                async for chunk in response.aiter_raw(_READ_CHUNK):
                    local.write(chunk)
                    callback.relative_update(len(chunk))
        finally:
            await response.aclose()

    async def _cat_file(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
        **_kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> bytes:
        """Return one whole-object read sliced with Python half-open semantics."""
        data = await self._read_whole(self._strip_protocol(path))
        return data[start:end]

    async def _cat_ranges(  # noqa: C901, PLR0913 - validation plus fsspec hook signature
        self,
        paths: list[str],
        starts: int | Sequence[int | None] | None,
        ends: int | Sequence[int | None] | None,
        max_gap: int | None = None,  # noqa: ARG002 - accepted for fsspec compatibility
        batch_size: int | None = None,
        on_error: str = "return",
        **_kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[bytes | BaseException]:
        """Return each range with at most one whole GET per object per call.

        A scalar ``starts``/``ends`` is broadcast to every path, matching
        fsspec's ``cat_ranges`` contract.
        """
        count = len(paths)
        start_list = _broadcast(starts, count)
        end_list = _broadcast(ends, count)
        if len(start_list) != count or len(end_list) != count:
            msg = "paths, starts, and ends must have the same length"
            raise ValueError(msg)
        if on_error not in ("return", "raise"):
            msg = "on_error must be 'return' or 'raise'"
            raise ValueError(msg)
        effective_batch_size = batch_size if batch_size is not None else self.batch_size
        if (
            effective_batch_size is not None
            and effective_batch_size != -1
            and effective_batch_size <= 0
        ):
            msg = "batch_size must be a positive integer or -1"
            raise ValueError(msg)
        if count == 0:
            return []

        stripped = [self._strip_protocol(rpath) for rpath in paths]
        grouped: dict[str, list[tuple[int, int | None, int | None]]] = {}
        for index, (stripped_path, start, end) in enumerate(
            zip(stripped, start_list, end_list, strict=True)
        ):
            grouped.setdefault(stripped_path, []).append((index, start, end))

        async def stage_object(
            stripped_path: str,
            ranges: list[tuple[int, int | None, int | None]],
        ) -> list[tuple[int, bytes]]:
            temp_path = staging.new_temp_path()
            try:
                await self._get_file(stripped_path, temp_path)
                with Path(temp_path).open("rb") as local:  # noqa: ASYNC230
                    size = os.fstat(local.fileno()).st_size
                    values: list[tuple[int, bytes]] = []
                    for index, start, end in ranges:
                        first, stop, _step = slice(start, end).indices(size)
                        local.seek(first)
                        values.append((index, local.read(max(0, stop - first))))
                    return values
            finally:
                with contextlib.suppress(OSError):
                    Path(temp_path).unlink()  # noqa: ASYNC240

        grouped_items = list(grouped.items())
        chunk_size = effective_batch_size or _get_batch_size()
        if chunk_size == -1:
            chunk_size = len(grouped_items)
        object_results: list[Any] = []
        for offset in range(0, len(grouped_items), chunk_size):
            chunk = grouped_items[offset : offset + chunk_size]
            object_results.extend(
                await _run_coros_in_chunks(
                    [stage_object(path, ranges) for path, ranges in chunk],
                    batch_size=chunk_size,
                    return_exceptions=on_error == "return",
                )
            )
        results: list[bytes | BaseException] = [b""] * count
        for ranges, outcome in zip(grouped.values(), object_results, strict=True):
            if isinstance(outcome, BaseException):
                for index, _start, _end in ranges:
                    results[index] = outcome
            else:
                for index, value in outcome:
                    results[index] = value
        return results

    def open(
        self,
        path: str,
        mode: str = "rb",
        block_size: int | None = None,
        cache_options: dict[str, Any] | None = None,
        compression: str | None = None,
        **kwargs: Any,  # noqa: ANN401 - fsspec public signature
    ) -> io.IOBase:
        """Open a staged file, preserving failed-block discard for text writes."""
        if "b" in mode or not ("w" in mode or "x" in mode):
            return super().open(
                path,
                mode=mode,
                block_size=block_size,
                cache_options=cache_options,
                compression=compression,
                **kwargs,
            )

        normalized_path = self._strip_protocol(path)
        binary_mode = mode.replace("t", "") + "b"
        text_kwargs = {
            key: kwargs.pop(key)
            for key in ("encoding", "errors", "newline")
            if key in kwargs
        }
        staged = cast(
            "staging.StagedWriteFile",
            super().open(
                normalized_path,
                mode=binary_mode,
                block_size=block_size,
                cache_options=cache_options,
                compression=None,
                **kwargs,
            ),
        )
        buffer: io.BufferedIOBase = staged
        if compression is not None:
            compression = get_compression(normalized_path, compression)
            buffer = compr[compression](buffer, mode=mode[0])
        return staging.StagedTextWriteFile(
            buffer,
            staged,
            **text_kwargs,
        )

    def _open(
        self,
        path: str,
        mode: str = "rb",
        block_size: int | None = None,  # noqa: ARG002 - accepted, non-behavioural
        autocommit: bool = True,  # noqa: FBT001, FBT002 - fsspec hook signature
        cache_options: Any = None,  # noqa: ANN401, ARG002 - accepted, non-behavioural
        **_kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> staging.StagedReadFile | staging.StagedWriteFile:
        """Open ``path``, disk-staging the whole object for read or write.

        Only binary modes reach here directly; fsspec wraps text mode over the
        staged binary file. Append and update (``+``) modes are unsupported, and
        a staged write always uploads on close: deferred commit
        (``autocommit=False``) is unsupported because this profile has no
        stage-then-commit primitive.
        """
        if "a" in mode or "+" in mode:
            msg = f"append and update modes are unsupported: {mode!r}"
            raise NotImplementedError(msg)
        path = self._strip_protocol(path)
        if not autocommit and ("w" in mode or "x" in mode):
            msg = "deferred commit (autocommit=False) is unsupported"
            raise NotImplementedError(msg)
        if "r" in mode:
            temp_path = staging.new_temp_path()
            try:
                self.get_file(path, temp_path)
                return staging.StagedReadFile(temp_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    Path(temp_path).unlink()
                raise
        if "w" in mode or "x" in mode:
            if "x" in mode and self.exists(path):
                msg = f"path already exists: {path}"
                raise FileExistsError(msg)
            return staging.StagedWriteFile(
                lambda temp: self.put_file(temp, path, mode="overwrite"),
            )
        msg = f"unsupported open mode: {mode!r}"
        raise NotImplementedError(msg)

    async def open_async(
        self,
        path: str,
        mode: str = "rb",
        **_kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Reject asynchronous file opening; async consumers use coroutine hooks."""
        msg = "open_async is unsupported; use the coroutine hooks instead"
        raise NotImplementedError(msg)

    # -- writing bytes -------------------------------------------------------

    async def _put(  # noqa: PLR0913 - fsspec hook signature
        self,
        lpath: str | list[str],
        rpath: str | list[str],
        recursive: bool = False,  # noqa: FBT001, FBT002 - fsspec hook signature
        callback: Callback = DEFAULT_CALLBACK,
        batch_size: int | None = None,
        maxdepth: int | None = None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[Any] | None:
        """Use fsspec's upload coordinator with one shared parent-creation scope."""
        async with _write_scope(self):
            adapter = cast("AsyncFileSystem", _InheritedWriteAdapter(self))
            return await AsyncFileSystem._put(  # noqa: SLF001 - inherited hook seam
                adapter,
                lpath,
                rpath,
                recursive=recursive,
                callback=cast("Callback", _DeferredBranchCallback(callback, self)),
                batch_size=batch_size,
                maxdepth=maxdepth,
                **kwargs,
            )

    async def _pipe(
        self,
        path: str | Mapping[str, bytes],
        value: bytes | None = None,
        batch_size: int | None = None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[Any] | None:
        """Use fsspec's pipe coordinator with one shared parent-creation scope."""
        async with _write_scope(self):
            adapter = cast("AsyncFileSystem", _InheritedWriteAdapter(self))
            return await AsyncFileSystem._pipe(  # noqa: SLF001 - inherited hook seam
                adapter,
                path,
                value=value,
                batch_size=batch_size,
                **kwargs,
            )

    async def _write(
        self,
        path: str,
        body: Any,  # noqa: ANN401 - httpx accepts bytes or an async byte iterator
        *,
        size: int | None,
        content_type: str | None,
        expected_digest: bytes | None,
    ) -> None:
        """Perform one negotiated whole PUT, validating status and integrity."""
        endpoint = await self._negotiate(
            path,
            direction=negotiate.DIRECTION_PUSH,
            protocol_uri=negotiate.PROTOCOL_HTTPS_PUT,
        )
        headers = {"Content-Type": content_type or _DEFAULT_CONTENT_TYPE}
        if size is not None:
            headers["Content-Length"] = str(size)
        try:
            response = await self._byte_send(
                endpoint,
                "PUT",
                content=body,
                headers=headers,
            )
            if response.status_code == _HTTP_PRECONDITION_FAILED:
                msg = f"integrity check failed for {path}"
                raise errors.VOSpaceError(msg, status=_HTTP_PRECONDITION_FAILED)
            if response.status_code != _HTTP_CREATED:
                detail = errors.bounded_text(response.content)
                msg = (
                    f"uncertain write to {path}: HTTP {response.status_code}; the "
                    f"target may have been truncated. {detail}"
                )
                raise errors.VOSpaceError(msg, status=response.status_code)
            _verify_returned_digest(response, expected_digest, path)
        finally:
            # Once PUT dispatch begins, success, failure, and cancellation can all
            # leave remote state changed. Never promise rollback; evict stale views.
            self._invalidate(path)

    async def _pipe_file(
        self,
        path: str,
        value: bytes,
        mode: str = "overwrite",
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Write ``value`` to ``path`` with one whole PUT (create or overwrite)."""
        path = self._strip_protocol(path)
        state = _join_write_scope(self)
        if mode == "create" and await self._exists(path):
            msg = f"path already exists: {path}"
            raise FileExistsError(msg)
        await self._materialize_write_parent(path, state)
        await self._write(
            path,
            value,
            size=len(value),
            content_type=kwargs.get("content_type"),
            expected_digest=hashlib.md5(value, usedforsecurity=False).digest(),
        )

    async def _put_file(
        self,
        lpath: str,
        rpath: str,
        mode: str = "overwrite",
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Stream one local file through one negotiated whole PUT."""
        callback: Callback = kwargs.get("callback", DEFAULT_CALLBACK)
        rpath = self._strip_protocol(rpath)
        state = _join_write_scope(self)
        if mode == "create" and await self._exists(rpath):
            msg = f"path already exists: {rpath}"
            raise FileExistsError(msg)
        await self._materialize_write_parent(rpath, state)
        size = Path(lpath).stat().st_size  # noqa: ASYNC240 - local-disk stat, not remote I/O
        callback.set_size(size)
        await self._write(
            rpath,
            _file_body(lpath, callback),
            size=size,
            content_type=kwargs.get("content_type"),
            expected_digest=_md5_of_file(lpath),
        )

    def touch(
        self,
        path: str,
        truncate: bool = True,  # noqa: FBT001, FBT002 - fsspec signature
        **_kwargs: Any,  # noqa: ANN401 - fsspec signature
    ) -> None:
        """Create or truncate ``path`` to zero bytes.

        Raises:
            NotImplementedError: If ``truncate`` is false, which this profile
                cannot express without an atomic modification-time update.
        """
        if not truncate:
            msg = "touch(truncate=False) is unsupported"
            raise NotImplementedError(msg)
        self.pipe_file(path, b"", mode="overwrite")

    # -- namespace and mutation ----------------------------------------------

    async def _update_node(self, path: str, properties: Mapping[str, str]) -> None:
        """POST mutable properties to one node and invalidate cached metadata."""
        path = self._strip_protocol(path)
        nodes._validate_property_update(properties)  # noqa: SLF001 - package-private seam
        authority = await self._require_authority()
        bindings = await self._get_bindings()
        current_node = self._parse_and_note(await self._get_node_document(path))
        document = nodes.build_property_update(
            f"vos://{authority}{path}",
            properties,
            wire_type=current_node.wire_type,
        )
        url = bindings.require_nodes() + paths.encode_url_path(path)
        try:
            response = await self._send_to_service(
                "POST", url, content=document, headers=nodes.XML_HEADERS
            )
            self._raise_for_status(response, path=path, allowed=(_HTTP_OK,))
        finally:
            self._invalidate(path)

    async def _create_container(self, path: str) -> None:
        """PUT one ContainerNode at ``path``."""
        bindings = await self._get_bindings()
        authority = await self._require_authority()
        document = nodes.build_container_document(f"vos://{authority}{path}")
        url = bindings.require_nodes() + paths.encode_url_path(path)
        try:
            response = await self._send_to_service(
                "PUT", url, content=document, headers=nodes.XML_HEADERS
            )
            self._raise_for_status(
                response, path=path, allowed=(_HTTP_OK, _HTTP_CREATED)
            )
        finally:
            self._invalidate(path)

    async def _delete_node(self, path: str) -> None:
        """DELETE the node at ``path``."""
        bindings = await self._get_bindings()
        url = bindings.require_nodes() + paths.encode_url_path(path)
        try:
            response = await self._send_to_service("DELETE", url)
            self._raise_for_status(
                response, path=path, allowed=(_HTTP_OK, _HTTP_NO_CONTENT)
            )
        finally:
            self._invalidate(path)

    async def _mkdir(
        self,
        path: str,
        create_parents: bool = True,  # noqa: FBT001, FBT002 - fsspec hook signature
        **_kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Create one ContainerNode, optionally creating missing ancestors."""
        path = self._strip_protocol(path)
        parent = paths.parent(path)
        if create_parents and parent != path and not await self._exists(parent):
            await self._makedirs(parent, exist_ok=True)
        await self._create_container(path)

    async def _makedirs(self, path: str, exist_ok: bool = False) -> None:  # noqa: FBT001, FBT002
        """Create ``path`` and every missing ancestor, top-down."""
        path = self._strip_protocol(path)
        state = _join_write_scope(self)
        if not exist_ok and await self._exists(path):
            msg = f"path already exists: {path}"
            raise FileExistsError(msg)
        if state is not None:
            await self._materialize_write_containers(path, state)
            return
        for ancestor in _ancestors_top_down(path):
            await self._ensure_container(ancestor)

    async def _materialize_write_parent(
        self,
        path: str,
        state: _CoordinatedWriteState | None,
    ) -> None:
        """Materialize one coordinated write's missing parent containers."""
        parent = paths.parent(path)
        if state is not None and parent not in ("/", path):
            await self._materialize_write_containers(parent, state)

    async def _materialize_write_containers(
        self,
        path: str,
        state: _CoordinatedWriteState,
    ) -> None:
        """Create required containers top-down at most once in one operation."""
        async with state.lock:
            if state.failure is not None:
                raise state.failure
            try:
                for ancestor in _ancestors_top_down(path):
                    if ancestor in state.materialized:
                        continue
                    try:
                        info = await self._info(ancestor)
                    except FileNotFoundError:
                        await self._ensure_container(ancestor)
                    else:
                        if info["type"] != "directory":
                            msg = f"write parent is not a directory: {ancestor}"
                            raise FileExistsError(msg)
                    state.materialized.add(ancestor)
            except Exception as exc:
                state.failure = exc
                raise

    async def _ensure_container(self, path: str) -> None:
        """Create a container, tolerating a concurrently-created container."""
        try:
            await self._create_container(path)
        except FileExistsError:
            info = await self._info(path)
            if info["type"] != "directory":
                raise

    async def _rm_file(self, path: str, **_kwargs: Any) -> None:  # noqa: ANN401 - fsspec hook signature
        """Delete one non-container node, refusing a container target.

        ``rm_file`` is the file-only primitive; deleting a directory must go
        through ``rm``/``rmdir`` so the empty-check or leaves-first contract is
        honoured. Guarding here stops a stray ``rm_file`` on a container from
        issuing a recursive server-side ``DELETE``.
        """
        path = self._strip_protocol(path)
        if (await self._info(path))["type"] == "directory":
            raise IsADirectoryError(errno.EISDIR, "path is a container", path)
        await self._delete_node(path)

    async def _rmdir(self, path: str) -> None:
        """Delete an empty container after proving it empty (non-atomic)."""
        path = self._strip_protocol(path)
        if await self._ls(path, detail=False):
            raise OSError(errno.ENOTEMPTY, "directory not empty", path)
        await self._delete_node(path)

    async def _rm(
        self,
        path: str | list[str],
        recursive: bool = False,  # noqa: FBT001, FBT002 - fsspec hook signature
        batch_size: int | None = None,  # noqa: ARG002 - accepted for fsspec compatibility
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Delete files and, with ``recursive``, empty-check or leaves-first trees."""
        if kwargs.get("maxdepth") is not None:
            msg = "rm(maxdepth=...) is unsupported"
            raise NotImplementedError(msg)
        targets = path if isinstance(path, list) else [path]
        for target in targets:
            await self._rm_one(self._strip_protocol(target), recursive=recursive)

    async def _rm_one(self, path: str, *, recursive: bool) -> None:
        """Delete a single path, dispatching by node type and recursion."""
        info = await self._info(path)
        if info["type"] != "directory":
            await self._delete_node(path)
        elif recursive:
            await self._rm_tree(path)
        else:
            await self._rmdir(path)

    async def _rm_tree(self, path: str) -> None:
        """Delete a container and its descendants leaves-first, client-side."""
        completed: list[str] = []
        await self._rm_subtree(path, completed)

    async def _rm_subtree(self, path: str, completed: list[str]) -> None:
        """Delete one validated subtree and retain confirmed partial progress."""
        try:
            children = await self._ls(path, detail=True)
        except Exception as exc:
            raise _recursive_removal_error(path, completed, exc) from exc
        for child in children:
            child_path = child["name"]
            if child["type"] == "directory":
                await self._rm_subtree(child_path, completed)
            else:
                await self._rm_delete(child_path, completed)
        await self._rm_delete(path, completed)

    async def _rm_delete(self, path: str, completed: list[str]) -> None:
        """Delete one recursive-removal node and record only confirmed success."""
        try:
            await self._delete_node(path)
        except Exception as exc:
            raise _recursive_removal_error(path, completed, exc) from exc
        completed.append(path)

    async def _cp_file(self, path1: str, path2: str, **_kwargs: Any) -> None:  # noqa: ANN401
        """Copy one object with a bounded read-to-write relay (bytes only).

        A container source recreates the destination container (fsspec's
        recursive coordinator passes container paths here too), so empty
        directories are preserved and a container is never read as bytes. For a
        file, the destination's parent container is created if missing.
        """
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        # Resolve the source first so a missing source fails before any
        # destination container is created (no orphaned parent on error).
        source_is_dir = (await self._info(path1))["type"] == "directory"
        # Then materialize the destination's parent, for both a directory and a
        # file target, so copying into a not-yet-created subtree (for example a
        # recursive glob into ``target/newdir``) never orphans an intermediate
        # ContainerNode.
        parent = paths.parent(path2)
        if parent not in ("/", path2) and not await self._exists(parent):
            await self._makedirs(parent, exist_ok=True)
        if source_is_dir:
            await self._ensure_container(path2)
            return
        # Relay through a disk-staged temporary file so an arbitrarily large
        # object is never held whole in memory; the staged PUT still validates
        # the round-trip md5.
        temp_path = staging.new_temp_path()
        try:
            await self._get_file(path1, temp_path)
            await self._put_file(temp_path, path2, mode="overwrite")
        finally:
            with contextlib.suppress(OSError):
                Path(temp_path).unlink()  # noqa: ASYNC240 - local-disk cleanup, not remote I/O

    def mv(
        self,
        path1: str,
        path2: str,
        recursive: bool = False,  # noqa: FBT001, FBT002 - fsspec signature
        maxdepth: int | None = None,
        **kwargs: Any,  # noqa: ANN401 - fsspec signature
    ) -> None:
        """Move a path, requiring an absent destination (non-atomic, no overwrite).

        The source is copied (or recreated) and deleted only after the
        destination genuinely exists; a failed source deletion may leave both
        paths. A directory move always recurses so the whole tree is recreated.
        """
        source = self._strip_protocol(path1)
        destination = self._strip_protocol(path2)
        if source == destination:
            return
        source_info = self.info(source)
        if source_info.get("islink"):
            msg = "moving a LinkNode is unsupported"
            raise NotImplementedError(msg)
        if self.exists(destination):
            msg = f"move destination already exists: {destination}"
            raise FileExistsError(msg)
        recursive = recursive or source_info["type"] == "directory"
        self.copy(source, destination, recursive=recursive, maxdepth=maxdepth, **kwargs)
        if not self.exists(destination):
            msg = f"move did not create the destination: {destination}; source is kept"
            raise errors.VOSpaceError(msg)
        self.rm(source, recursive=recursive)
        self._invalidate(source)
        self._invalidate(destination)

    def _invalidate(self, path: str) -> None:
        """Invalidate the directory cache for ``path``, its subtree, and parent.

        Clearing the whole subtree keeps a recursively moved or removed tree from
        leaving stale descendant listings behind, and clearing the parent lets
        its listing pick up the mutation (contract section 10). fsspec's base
        ``invalidate_cache`` is a no-op outside a transaction, so the directory
        cache is pruned directly.
        """
        prefix = "/" if path == "/" else f"{path}/"
        parent = paths.parent(path)
        stale = [
            entry
            for entry in list(self.dircache)
            if entry in (path, parent) or entry.startswith(prefix)
        ]
        for entry in stale:
            self.dircache.pop(entry, None)

    def invalidate_cache(self, path: str | None = None) -> None:
        """Clear all directory state, or one path, its subtree, and parent."""
        if path is None:
            self.dircache.clear()
        else:
            self._invalidate(self._strip_protocol(path))

    async def aclose(self) -> None:
        """Close every realized HTTP client and evict the instance (idempotent).

        After this call the instance is removed from fsspec's instance cache and
        any later HTTP I/O fails as closed.
        """
        if self._pid != os.getpid():
            self._ensure_usable()
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


def _broadcast(
    value: int | Sequence[int | None] | None, count: int
) -> list[int | None]:
    """Broadcast a scalar range bound to every path, or return the sequence."""
    if value is None or isinstance(value, int):
        return [value] * count
    return list(value)


def _origin(url: str) -> tuple[str, str | None, int | None]:
    """Return the (scheme, host, port) origin of a URL with default ports."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return (parts.scheme, parts.hostname, port)


def _same_origin(a: str, b: str) -> bool:
    """Whether two URLs share the same scheme, host, and (defaulted) port."""
    return _origin(a) == _origin(b)


def _authority_of(uri: str) -> str:
    """Return the VOSpace authority carried by a ``vos://authority/...`` URI."""
    _scheme, separator, rest = uri.partition("://")
    if not separator or not rest:
        msg = f"node URI is not a VOSpace URI: {uri!r}"
        raise errors.VOSpaceError(msg)
    return rest.split("/", 1)[0]


def _parse_datetime(value: str) -> datetime.datetime:
    """Parse an ISO 8601 modification date, tolerating a trailing ``Z``."""
    return datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def _recursive_removal_error(
    path: str, completed: list[str], cause: Exception
) -> errors.VOSpaceError:
    """Return a partial-completion error for a recursive removal failure."""
    return errors.VOSpaceError(
        f"recursive removal failed at {path}: {cause}",
        status=getattr(cause, "status", None),
        fault=getattr(cause, "fault", None),
        retry_after=getattr(cause, "retry_after", None),
        completed=list(completed),
        failed=[path],
    )


def _ancestors_top_down(path: str) -> list[str]:
    """Return ``path`` and its ancestors from the topmost down to ``path``."""
    result: list[str] = []
    current = ""
    for segment in paths.segments(path):
        current = f"{current}/{segment}"
        result.append(current)
    return result


def _md5_of_file(path: str) -> bytes:
    """Return the MD5 digest of a local file, read in bounded chunks."""
    # usedforsecurity=False keeps the integrity hash available on FIPS hosts,
    # where an unqualified md5() raises before any byte transfer can complete.
    digest = hashlib.md5(usedforsecurity=False)
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.digest()


async def _file_body(path: str, callback: Callback) -> AsyncIterator[bytes]:
    """Yield a local file in bounded chunks, reporting progress via callback."""
    # Local-disk reads are fast and bounded; async file I/O would add a dependency.
    with Path(path).open("rb") as handle:  # noqa: ASYNC230 - staging from local disk
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            yield chunk
            callback.relative_update(len(chunk))


def _verify_returned_digest(
    response: httpx.Response,
    expected: bytes | None,
    path: str,
) -> None:
    """Validate a server-returned MD5 digest against the uploaded bytes."""
    if expected is None:
        return
    header = response.headers.get("content-md5") or response.headers.get("digest")
    if header is None:
        return
    returned = _decode_digest(header)
    if returned is not None and returned != expected:
        msg = f"MD5 mismatch after writing {path}"
        raise errors.VOSpaceError(msg, status=response.status_code)


def _decode_digest(header: str) -> bytes | None:
    """Decode an MD5 digest header from hex or base64, or ``None`` if unusable."""
    value = (
        header.split("=", 1)[1].strip()
        if header.lower().startswith("md5=")
        else header.strip()
    )
    with contextlib.suppress(ValueError):
        return bytes.fromhex(value)
    with contextlib.suppress(ValueError):
        return base64.b64decode(value, validate=True)
    return None
