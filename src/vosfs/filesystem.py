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
import datetime
import errno
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import httpx
from fsspec.asyn import AsyncFileSystem, sync
from fsspec.callbacks import DEFAULT_CALLBACK

from vosfs import capabilities, config, errors, negotiate, nodes, paths, staging
from vosfs.transport import ClientPool, build_timeout

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

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
            await response.aclose()
            raise errors.http_exception(response.status_code, body=body, path=path)
        return response

    async def _read_whole(self, path: str) -> bytes:
        """Download one whole object into memory (an empty 204 reads as ``b''``)."""
        response = await self._open_read_stream(path)
        try:
            if response.status_code == _HTTP_NO_CONTENT:
                return b""
            return await response.aread()
        finally:
            await response.aclose()

    async def _get_file(
        self,
        rpath: str,
        lpath: str,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Stream one negotiated whole-object GET to a local file."""
        callback: Callback = kwargs.get("callback", DEFAULT_CALLBACK)
        rpath = self._strip_protocol(rpath)
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

    async def _cat_ranges(  # noqa: PLR0913 - fsspec hook signature
        self,
        paths: list[str],
        starts: Sequence[int | None],
        ends: Sequence[int | None],
        max_gap: int | None = None,  # noqa: ARG002 - accepted for fsspec compatibility
        batch_size: int | None = None,  # noqa: ARG002 - accepted for fsspec compatibility
        on_error: str = "return",
        **_kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[bytes | BaseException]:
        """Return each range with at most one whole GET per object per call."""
        stripped = [self._strip_protocol(path) for path in paths]
        cache: dict[str, bytes | BaseException] = {}
        for path in dict.fromkeys(stripped):
            try:
                cache[path] = await self._read_whole(path)
            except Exception as exc:  # noqa: PERF203 - per-object on_error handling
                if on_error == "raise":
                    raise
                cache[path] = exc
        results: list[bytes | BaseException] = []
        for path, start, end in zip(stripped, starts, ends, strict=True):
            whole = cache[path]
            results.append(whole[start:end] if isinstance(whole, bytes) else whole)
        return results

    def _open(
        self,
        path: str,
        mode: str = "rb",
        block_size: int | None = None,  # noqa: ARG002 - accepted, non-behavioural
        autocommit: bool = True,  # noqa: ARG002, FBT001, FBT002 - fsspec hook signature
        cache_options: Any = None,  # noqa: ANN401, ARG002 - accepted, non-behavioural
        **_kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> staging.StagedReadFile | staging.StagedWriteFile:
        """Open ``path``, disk-staging the whole object for read or write.

        Only binary modes reach here directly; fsspec wraps text mode over the
        staged binary file. Append and update (``+``) modes are unsupported.
        """
        if "a" in mode or "+" in mode:
            msg = f"append and update modes are unsupported: {mode!r}"
            raise NotImplementedError(msg)
        path = self._strip_protocol(path)
        if "r" in mode:
            temp_path = staging.new_temp_path()
            self.get_file(path, temp_path)
            return staging.StagedReadFile(temp_path)
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
        response = await self._byte_send(endpoint, "PUT", content=body, headers=headers)
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

    async def _pipe_file(
        self,
        path: str,
        value: bytes,
        mode: str = "overwrite",
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Write ``value`` to ``path`` with one whole PUT (create or overwrite)."""
        path = self._strip_protocol(path)
        if mode == "create" and await self._exists(path):
            msg = f"path already exists: {path}"
            raise FileExistsError(msg)
        await self._write(
            path,
            value,
            size=len(value),
            content_type=kwargs.get("content_type"),
            expected_digest=hashlib.md5(value).digest(),  # noqa: S324 - integrity, not security
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
        if mode == "create" and await self._exists(rpath):
            msg = f"path already exists: {rpath}"
            raise FileExistsError(msg)
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

    async def _create_container(self, path: str) -> None:
        """PUT one ContainerNode at ``path``."""
        bindings = await self._get_bindings()
        authority = await self._require_authority()
        document = nodes.build_container_document(f"vos://{authority}{path}")
        url = bindings.require_nodes() + paths.encode_url_path(path)
        response = await self._send_to_service(
            "PUT", url, content=document, headers=nodes.XML_HEADERS
        )
        if response.status_code not in (_HTTP_OK, _HTTP_CREATED):
            body = errors.bounded_text(response.content)
            raise errors.http_exception(response.status_code, body=body, path=path)
        self._invalidate(path)

    async def _delete_node(self, path: str) -> None:
        """DELETE the node at ``path``."""
        bindings = await self._get_bindings()
        url = bindings.require_nodes() + paths.encode_url_path(path)
        response = await self._send_to_service("DELETE", url)
        if response.status_code not in (_HTTP_OK, _HTTP_NO_CONTENT):
            body = errors.bounded_text(response.content)
            raise errors.http_exception(response.status_code, body=body, path=path)
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
        if not exist_ok and await self._exists(path):
            msg = f"path already exists: {path}"
            raise FileExistsError(msg)
        for ancestor in _ancestors_top_down(path):
            await self._ensure_container(ancestor)

    async def _ensure_container(self, path: str) -> None:
        """Create a container, tolerating a concurrently-created container."""
        try:
            await self._create_container(path)
        except FileExistsError:
            info = await self._info(path)
            if info["type"] != "directory":
                raise

    async def _rm_file(self, path: str, **_kwargs: Any) -> None:  # noqa: ANN401 - fsspec hook signature
        """Delete one non-container node."""
        await self._delete_node(self._strip_protocol(path))

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
        for child in await self._ls(path, detail=True):
            child_path = child["name"]
            if child["type"] == "directory":
                await self._rm_tree(child_path)
            else:
                await self._delete_node(child_path)
        await self._delete_node(path)

    async def _cp_file(self, path1: str, path2: str, **_kwargs: Any) -> None:  # noqa: ANN401
        """Copy one object with a bounded read-to-write relay (bytes only).

        The destination's parent container is created if missing, so a recursive
        copy materializes the destination tree even though fsspec's coordinator
        relays only the files.
        """
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        parent = paths.parent(path2)
        if parent not in ("/", path2) and not await self._exists(parent):
            await self._makedirs(parent, exist_ok=True)
        data = await self._read_whole(path1)
        await self._write(
            path2,
            data,
            size=len(data),
            content_type=None,
            expected_digest=hashlib.md5(data).digest(),  # noqa: S324 - integrity, not security
        )

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
        destination succeeds; a failed source deletion may leave both paths.
        """
        source = self._strip_protocol(path1)
        destination = self._strip_protocol(path2)
        if source == destination:
            return
        if self.exists(destination):
            msg = f"move destination already exists: {destination}"
            raise FileExistsError(msg)
        self.copy(source, destination, recursive=recursive, maxdepth=maxdepth, **kwargs)
        self.rm(source, recursive=recursive)
        self._invalidate(source)
        self._invalidate(destination)

    def _invalidate(self, path: str) -> None:
        """Invalidate the directory cache for ``path`` and its parent."""
        self.invalidate_cache(path)
        parent = paths.parent(path)
        if parent != path:
            self.invalidate_cache(parent)

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
    digest = hashlib.md5()  # noqa: S324 - integrity check, not a security primitive
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
