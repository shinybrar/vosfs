"""An in-memory OpenCADC VOSpace simulator wired onto a respx router.

The simulator maintains a node tree and blob store and serves the exact
requests the filesystem makes: capabilities, node GET/PUT/POST/DELETE with child
listings, synchronous transfer negotiation, and negotiated byte GET/PUT. It
lets namespace, copy/move, and the fsspec reusable-abstract tests run against a
faithful, hermetic backend.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote, urlsplit

import httpx
from conftest import (
    AUTHORITY,
    BASE_URL,
    NODES_URL,
    SYNC_URL,
    mock_capabilities,
    transfer_details,
)
from defusedxml import ElementTree

from vosfs import paths

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx

_NODES_PREFIX = urlsplit(NODES_URL).path
_NS = (
    'xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


class VOSpaceSim:
    """A minimal, stateful VOSpace backend for hermetic tests."""

    def __init__(self) -> None:
        """Start with an empty root container."""
        self.nodes: dict[str, str] = {"/": "container"}
        self.blobs: dict[str, bytes] = {}
        self.properties: dict[str, dict[str, str]] = {"/": {}}
        self.node_update_requests: list[httpx.Request] = []
        self.node_update_status = 200

    def add_container(self, path: str) -> VOSpaceSim:
        """Seed a container node and return self for chaining."""
        self.nodes[path] = "container"
        self.properties.setdefault(path, {})
        return self

    def add_file(self, path: str, content: bytes = b"") -> VOSpaceSim:
        """Seed a data node with content and return self for chaining."""
        self.nodes[path] = "data"
        self.blobs[path] = content
        self.properties.setdefault(path, {})
        return self

    def install(self, router: respx.Router) -> None:
        """Register every simulator route on ``router``."""
        mock_capabilities(router)
        router.route(url__regex=rf"^{re.escape(NODES_URL)}").mock(
            side_effect=self._node_op
        )
        router.post(SYNC_URL).mock(side_effect=self._negotiate)
        router.get(url__regex=rf"^{re.escape(BASE_URL)}/details").mock(
            side_effect=self._details
        )
        router.route(url__regex=rf"^{re.escape(BASE_URL)}/files").mock(
            side_effect=self._byte_op
        )

    # -- node operations -----------------------------------------------------

    def _node_op(self, request: httpx.Request) -> httpx.Response:
        path = self._node_path(request)
        if request.method == "GET":
            return self._node_get(path)
        if request.method == "PUT":
            self.nodes[path] = "container"
            self.properties[path] = {}
            return httpx.Response(201)
        if request.method == "POST":
            return self._node_post(path, request)
        if request.method == "DELETE":
            if path not in self.nodes:
                return httpx.Response(404)
            del self.nodes[path]
            self.blobs.pop(path, None)
            self.properties.pop(path, None)
            return httpx.Response(200)
        return httpx.Response(405)  # pragma: no cover - unused verbs

    def _node_post(self, path: str, request: httpx.Request) -> httpx.Response:
        self.node_update_requests.append(request)
        if path not in self.nodes:
            return httpx.Response(404)
        if self.node_update_status != 200:
            return httpx.Response(self.node_update_status)
        self.properties.setdefault(path, {}).update(_properties(request.content))
        return httpx.Response(200)

    def _node_get(self, path: str) -> httpx.Response:
        kind = self.nodes.get(path)
        if kind is None:
            return httpx.Response(404)
        if kind == "container":
            return httpx.Response(200, content=self._container_document(path))
        return httpx.Response(200, content=self._data_document(path))

    def _node_path(self, request: httpx.Request) -> str:
        suffix = request.url.path[len(_NODES_PREFIX) :]
        return paths.strip_protocol(unquote(suffix)) if suffix else "/"

    def _uri(self, path: str) -> str:
        return f"vos://{AUTHORITY}" if path == "/" else f"vos://{AUTHORITY}{path}"

    def _container_document(self, path: str) -> bytes:
        children = "".join(
            self._child_element(child)
            for child in sorted(self.nodes)
            if child != path and paths.parent(child) == path
        )
        return (
            f'<vos:node {_NS} xsi:type="vos:ContainerNode" uri="{self._uri(path)}">'
            f"<vos:properties>{self._property_elements(path)}</vos:properties>"
            f"<vos:nodes>{children}</vos:nodes></vos:node>"
        ).encode()

    def _child_element(self, path: str) -> str:
        if self.nodes[path] == "container":
            return (
                f'<vos:node {_NS} xsi:type="vos:ContainerNode" uri="{self._uri(path)}">'
                f"<vos:properties>{self._property_elements(path)}</vos:properties>"
                f"</vos:node>"
            )
        return self._data_element(path)

    def _data_element(self, path: str) -> str:
        length = len(self.blobs.get(path, b""))
        length_uri = "ivo://ivoa.net/vospace/core#length"
        return (
            f'<vos:node {_NS} xsi:type="vos:DataNode" uri="{self._uri(path)}">'
            f'<vos:properties><vos:property uri="{length_uri}">{length}</vos:property>'
            f"{self._property_elements(path)}"
            f"</vos:properties></vos:node>"
        )

    def _property_elements(self, path: str) -> str:
        return "".join(
            f'<vos:property uri="{uri}">{value}</vos:property>'
            for uri, value in self.properties.get(path, {}).items()
        )

    def _data_document(self, path: str) -> bytes:
        return self._data_element(path).encode()

    # -- byte transfer -------------------------------------------------------

    def _negotiate(self, request: httpx.Request) -> httpx.Response:
        target = _target_path(request.content)
        location = f"{BASE_URL}/details?t={quote(target)}"
        return httpx.Response(303, headers={"Location": location})

    def _details(self, request: httpx.Request) -> httpx.Response:
        path = request.url.params["t"]
        endpoint = f"{BASE_URL}/files?p={quote(path)}"
        return httpx.Response(200, content=transfer_details(endpoint))

    def _byte_op(self, request: httpx.Request) -> httpx.Response:
        path = request.url.params["p"]
        if request.method == "PUT":
            self.nodes[path] = "data"
            self.blobs[path] = request.content
            self.properties.setdefault(path, {})
            return httpx.Response(201)
        content = self.blobs.get(path)
        if content is None:
            return httpx.Response(404)
        if content == b"":
            return httpx.Response(204)
        return httpx.Response(200, content=_stream(content))


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _target_path(content: bytes | None) -> str:
    match = re.search(r"<[^>]*target[^>]*>([^<]+)</", (content or b"").decode())
    if match is None:
        return "/"
    prefix = f"vos://{AUTHORITY}"
    return match.group(1).strip()[len(prefix) :] or "/"


def _properties(content: bytes | None) -> dict[str, str]:
    root = ElementTree.fromstring(content or b"")
    namespace = "{http://www.ivoa.net/xml/VOSpace/v2.0}"
    return {
        element.get("uri", ""): element.text or ""
        for element in root.findall(f"{namespace}properties/{namespace}property")
    }
