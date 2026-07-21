"""Shared hermetic support for namespace mutation tests."""

from urllib.parse import unquote

import httpx
import respx
from conftest import AUTHORITY, NODES_URL, make_fs, mock_transfers


def _fs(router, sim, *, asynchronous=True):
    sim.install(router)
    return make_fs(router, asynchronous=asynchronous)


def _install_percent_mutation_routes(
    router: respx.Router,
    files: dict[str, bytes],
    *,
    listings: dict[str, bytes] | None = None,
    data_nodes: set[str] | None = None,
) -> tuple[set[str], list[str]]:
    """Install a percent-aware node store with a misleading ``100A`` alias."""
    created: set[str] = set()
    deleted: list[str] = []
    listings = listings or {}
    data_nodes = data_nodes or set(files)

    def node_op(request: httpx.Request) -> httpx.Response:
        encoded = str(request.url).split(NODES_URL, 1)[1]
        internal = unquote(encoded)
        if request.method == "GET":
            document = listings.get(internal)
            if document is None and (internal in files or internal in data_nodes):
                document = (
                    f'<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
                    f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                    f'xsi:type="vos:DataNode" uri="vos://{AUTHORITY}{encoded}">'
                    f"<vos:properties><vos:property "
                    f'uri="ivo://ivoa.net/vospace/core#length">'
                    f"{len(files.get(internal, b''))}</vos:property>"
                    f"</vos:properties></vos:node>"
                ).encode()
            if document is not None:
                return httpx.Response(200, content=document)
            if encoded in created or "100A" in encoded:
                document = (
                    f'<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
                    f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                    f'xsi:type="vos:ContainerNode" uri="vos://{AUTHORITY}{encoded}">'
                    f"<vos:properties/><vos:nodes/></vos:node>"
                ).encode()
                return httpx.Response(200, content=document)
            return httpx.Response(404)
        if request.method == "PUT":
            created.add(encoded)
            return httpx.Response(201)
        if request.method == "DELETE":
            deleted.append(internal)
            files.pop(internal, None)
            return httpx.Response(200)
        return httpx.Response(405)

    router.route(url__regex=rf"^{NODES_URL}/").mock(side_effect=node_op)
    mock_transfers(router, files)
    return created, deleted
