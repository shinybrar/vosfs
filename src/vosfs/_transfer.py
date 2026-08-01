"""The OpenCADC VOSpace synchronous byte-transfer protocol.

Reading or writing bytes is never a direct request to the node URL. The client
first negotiates a transfer: it POSTs a transfer document to the service's sync
binding, follows an approved 303 chain to the transfer-details document, and
picks a protocol whose security method matches the configured credential. Only
then does it issue the single byte GET/HEAD/PUT against the negotiated
endpoint.

Credential routing is decided by the *negotiated* security method, not by the
filesystem's own credential: a pre-authorized endpoint receives no credential
at all. That distinction is the reason this layer is separate from the fsspec
adapter in :mod:`vosfs.filesystem`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast
from urllib.parse import urlsplit

import httpx

from vosfs import _integrity, capabilities, errors, negotiate, nodes, staging, transport

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vosfs.filesystem import VOSpaceFileSystem
    from vosfs.negotiate import NegotiatedEndpoint
    from vosfs.nodes import Node

DEFAULT_CONTENT_TYPE = "application/octet-stream"
_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$", re.IGNORECASE)


async def negotiate_endpoint(
    filesystem: VOSpaceFileSystem, path: str, *, direction: str, protocol_uri: str
) -> NegotiatedEndpoint:
    """Negotiate a byte endpoint for one logical transfer of ``path``.

    Builds a VOSpace 2.1 transfer document with one authority-qualified
    target, POSTs it to the discovered sync binding with redirects disabled,
    follows the approved 303 chain to the transfer-details document, and
    chooses a protocol whose security method is compatible with the
    configured credential. Redirect loops and more than five hops fail.
    """
    bindings = await filesystem._get_bindings()
    sync_url = bindings.require_sync()
    authority = await filesystem._require_authority()
    target = f"vos://{authority}{path}"
    document = nodes.build_transfer_document(
        target, direction=direction, protocols=[protocol_uri]
    )
    post = await filesystem._send_to_service(
        "POST", sync_url, content=document, headers=nodes.XML_HEADERS
    )
    filesystem._raise_for_status(post, path=path, allowed=(transport.HTTP_SEE_OTHER,))
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
            return negotiate.NegotiatedEndpoint(location, capabilities.ANONYMOUS_METHOD)
        location = negotiate.validate_redirect(
            location,
            base=location,
            sending_bearer=(
                filesystem._credential.method == "token"
                and transport.same_origin(location, filesystem.endpoint_url)
            ),
        )
        details = await filesystem._send_to_service(
            "GET", location, headers=nodes.XML_HEADERS
        )
        filesystem._raise_for_status(
            details, path=path, allowed=(transport.HTTP_OK, transport.HTTP_SEE_OTHER)
        )
        if details.status_code == transport.HTTP_OK:
            return negotiate.choose_protocol(
                negotiate.parse_transfer_details(details.content),
                filesystem._security_method(),
            )
        location = negotiate.validate_redirect(
            details.headers.get("location"),
            base=location,
            sending_bearer=False,
        )
    msg = "synchronous-transfer negotiation returned more than five redirects"
    raise errors.VOSpaceError(msg)


def byte_routing(
    filesystem: VOSpaceFileSystem, endpoint: NegotiatedEndpoint
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
            f"negotiated byte endpoint is not an absolute http(s) URL: {endpoint.url!r}"
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
        bearer = filesystem._credential.read_bearer()
        return {"Authorization": f"Bearer {bearer}"}, False
    if method == capabilities.CERTIFICATE_METHOD:
        if scheme != "https":
            msg = "a certificate byte endpoint must use https"
            raise errors.VOSpaceError(msg)
        return {}, True
    msg = f"unsupported negotiated security method: {method!r}"  # pragma: no cover
    raise errors.VOSpaceError(msg)  # pragma: no cover


async def byte_send(  # noqa: PLR0913 - one parameter per HTTP request element.
    filesystem: VOSpaceFileSystem,
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
    filesystem._ensure_usable()
    request_headers, use_cert = byte_routing(filesystem, endpoint)
    request_headers.update(headers or {})
    request = httpx.Request(
        method, endpoint.url, headers=request_headers, content=content
    )
    try:
        response = await filesystem._pool.send(
            request, use_cert=use_cert, stream=stream
        )
    except httpx.HTTPError as exc:
        raise errors.transport_exception(exc, path=endpoint.url) from exc
    if response.is_redirect:
        if stream:
            await response.aclose()
        msg = f"unexpected redirect from byte endpoint: {response.status_code}"
        raise errors.VOSpaceError(msg, status=response.status_code)
    return response


# -- reading bytes -------------------------------------------------------


async def validate_read_target(filesystem: VOSpaceFileSystem, path: str) -> Node:
    """Reject external LinkNodes before synchronous transfer negotiation."""
    authority = await filesystem._require_authority()
    node = filesystem._parse_and_note(await filesystem._get_node_document(path))
    if node.node_type == "link":
        target = urlsplit(cast("str", node.target))
        if target.scheme != "vos" or target.netloc != authority:
            msg = "external LinkNode byte reads are unsupported"
            raise NotImplementedError(msg)
    return node


async def preflight_read_target(filesystem: VOSpaceFileSystem, path: str) -> Node:
    """Validate byte-read capability and LinkNode target before staging."""
    (await filesystem._get_bindings()).require_sync()
    return await validate_read_target(filesystem, path)


async def open_read_stream(
    filesystem: VOSpaceFileSystem, path: str, *, target_validated: bool = False
) -> httpx.Response:
    """Negotiate a read and return the open, streaming byte response."""
    (await filesystem._get_bindings()).require_sync()
    if not target_validated:
        await validate_read_target(filesystem, path)
    endpoint = await negotiate_endpoint(
        filesystem,
        path,
        direction=negotiate.DIRECTION_PULL,
        protocol_uri=negotiate.PROTOCOL_HTTPS_GET,
    )
    response = await byte_send(
        filesystem, endpoint, "GET", headers=transport.IDENTITY_ENCODING, stream=True
    )
    if response.status_code not in (transport.HTTP_OK, transport.HTTP_NO_CONTENT):
        try:
            await _raise_byte_error(response, path)
        finally:
            await response.aclose()
    return response


async def read_whole(filesystem: VOSpaceFileSystem, path: str) -> bytes:
    """Download one whole object into memory (an empty 204 reads as ``b''``).

    Consumes the raw response bytes so HTTP content decoding can never alter
    filesystem content, matching the streamed ``_get_file`` path.
    """
    response = await open_read_stream(filesystem, path)
    try:
        return await _raw_body(response)
    finally:
        await response.aclose()


def http_range_header(start: int | None, end: int | None) -> str | None:
    """Map Python half-open bounds to an HTTP ``Range`` value, or ``None``.

    ``None`` means the caller should issue a whole GET (no partial request, or
    bounds that need the object size before they can be expressed).
    """
    if start is None and end is None:
        return None
    if start is not None and start < 0:
        return f"bytes={start}" if end is None else None
    if end is not None and end < 0:
        return None
    first = 0 if start is None else start
    if end is None:
        return f"bytes={first}-"
    if end <= first:
        return None
    return f"bytes={first}-{end - 1}"


async def read_slice(
    filesystem: VOSpaceFileSystem,
    path: str,
    start: int | None = None,
    end: int | None = None,
) -> bytes:
    """Return one object slice, using ``Range`` when the byte endpoint agrees."""
    range_header = http_range_header(start, end)
    if range_header is None:
        return (await read_whole(filesystem, path))[start:end]
    values = await read_grouped_ranges(filesystem, path, [(0, start, end)])
    return values[0][1]


async def read_grouped_ranges(
    filesystem: VOSpaceFileSystem,
    path: str,
    ranges: Sequence[tuple[int, int | None, int | None]],
) -> list[tuple[int, bytes]]:
    """Return indexed slices for one object, ranging while responses are ``206``."""
    await preflight_read_target(filesystem, path)
    if not ranges:
        return []

    async def download(temp_path: str) -> None:
        await filesystem._download_file(path, temp_path, target_validated=True)

    first_range = http_range_header(ranges[0][1], ranges[0][2])
    if first_range is None:
        return await staging.read_ranges(download, ranges)

    endpoint = await negotiate_endpoint(
        filesystem,
        path,
        direction=negotiate.DIRECTION_PULL,
        protocol_uri=negotiate.PROTOCOL_HTTPS_GET,
    )
    values: list[tuple[int, bytes]] = []
    for index, start, end in ranges:
        range_header = http_range_header(start, end)
        if range_header is None:
            return await staging.read_ranges(download, ranges)
        headers = {**transport.IDENTITY_ENCODING, "Range": range_header}
        response = await byte_send(
            filesystem, endpoint, "GET", headers=headers, stream=True
        )
        try:
            status = response.status_code
            if status == transport.HTTP_PARTIAL_CONTENT:
                body = await _raw_body(response)
                _validate_partial(
                    body, response.headers.get("content-range"), range_header
                )
                values.append((index, body))
                continue
            if status in (transport.HTTP_OK, transport.HTTP_NO_CONTENT):
                # Whole-object fallback wins for every range on this object.
                return await _slice_streamed_object(response, ranges)
            await _raise_byte_error(response, path)
        finally:
            await response.aclose()
    return values


async def _raise_byte_error(response: httpx.Response, path: str) -> NoReturn:
    """Raise the mapped exception for a failed byte response."""
    body = errors.bounded_text(await response.aread())
    raise errors.http_exception(
        response.status_code,
        body=body,
        fault=errors.extract_fault(body),
        path=path,
        retry_after=errors.parse_retry_after(response.headers.get("retry-after")),
    )


async def _raw_body(response: httpx.Response) -> bytes:
    if response.status_code == transport.HTTP_NO_CONTENT:
        return b""
    chunks = response.aiter_raw(_integrity.READ_CHUNK)
    return b"".join([chunk async for chunk in chunks])


def _validate_partial(
    body: bytes, content_range: str | None, range_header: str
) -> None:
    if content_range is None:
        msg = "206 Partial Content without Content-Range"
        raise errors.VOSpaceError(msg, status=transport.HTTP_PARTIAL_CONTENT)
    match = _CONTENT_RANGE.fullmatch(content_range.strip())
    if match is None:
        msg = f"malformed Content-Range: {content_range!r}"
        raise errors.VOSpaceError(msg, status=transport.HTTP_PARTIAL_CONTENT)
    first, last = int(match.group(1)), int(match.group(2))
    if last < first or len(body) != last - first + 1:
        msg = "206 body does not match Content-Range"
        raise errors.VOSpaceError(msg, status=transport.HTTP_PARTIAL_CONTENT)
    spec = range_header.removeprefix("bytes=")
    if spec.startswith("-"):
        return
    if spec.endswith("-"):
        if first != int(spec[:-1]):
            msg = "206 Content-Range start does not match request"
            raise errors.VOSpaceError(msg, status=transport.HTTP_PARTIAL_CONTENT)
        return
    want_first, want_last = spec.split("-", 1)
    if first != int(want_first) or last != int(want_last):
        msg = "206 Content-Range does not match request"
        raise errors.VOSpaceError(msg, status=transport.HTTP_PARTIAL_CONTENT)


async def _slice_streamed_object(
    response: httpx.Response,
    ranges: Sequence[tuple[int, int | None, int | None]],
) -> list[tuple[int, bytes]]:
    """Stage one whole-object response to disk and return local slices."""

    async def download(temp_path: str) -> None:
        with Path(temp_path).open("wb") as local:  # noqa: ASYNC230 - disk-backed staging
            if response.status_code != transport.HTTP_NO_CONTENT:
                async for chunk in response.aiter_raw(_integrity.READ_CHUNK):
                    local.write(chunk)

    return await staging.read_ranges(download, ranges)


# -- writing bytes -------------------------------------------------------


async def write_whole(  # noqa: PLR0913 - one parameter per PUT request element.
    filesystem: VOSpaceFileSystem,
    path: str,
    body: Any,  # noqa: ANN401 - httpx accepts bytes or an async byte iterator
    *,
    size: int | None,
    content_type: str | None,
    expected_digest: bytes | None,
) -> None:
    """Perform one negotiated whole PUT, validating status and integrity."""
    endpoint = await negotiate_endpoint(
        filesystem,
        path,
        direction=negotiate.DIRECTION_PUSH,
        protocol_uri=negotiate.PROTOCOL_HTTPS_PUT,
    )
    headers = {"Content-Type": content_type or DEFAULT_CONTENT_TYPE}
    if size is not None:
        headers["Content-Length"] = str(size)
    try:
        response = await byte_send(
            filesystem,
            endpoint,
            "PUT",
            content=body,
            headers=headers,
        )
        if response.status_code == transport.HTTP_PRECONDITION_FAILED:
            msg = f"integrity check failed for {path}"
            raise errors.VOSpaceError(msg, status=transport.HTTP_PRECONDITION_FAILED)
        if response.status_code != transport.HTTP_CREATED:
            detail = errors.bounded_text(response.content)
            msg = (
                f"uncertain write to {path}: HTTP {response.status_code}; the "
                f"target may have been truncated. {detail}"
            )
            raise errors.VOSpaceError(msg, status=response.status_code)
        _integrity.verify_returned_digest(response, expected_digest, path)
    finally:
        # Once PUT dispatch begins, success, failure, and cancellation can all
        # leave remote state changed. Never promise rollback; evict stale views.
        filesystem._invalidate(path)
