"""Tests for the namespace and mutation contract (section 10)."""

from urllib.parse import unquote

import httpx
import pytest
import respx
from conftest import (
    AUTHORITY,
    BASE_URL,
    NODES_URL,
    make_fs,
    mock_capabilities,
    mock_transfers,
)
from defusedxml import ElementTree
from vospace_sim import VOSpaceSim

from vosfs import errors, paths
from vosfs.nodes import LENGTH_PROPERTY_URI, VOSPACE_NS, XML_HEADERS


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
    mock_transfers(router, files)

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
    return created, deleted


async def test_update_node_posts_property_and_refreshes_metadata(
    router: respx.Router,
) -> None:
    property_uri = "ivo://example.org/vosfs#issue-65"
    sim = VOSpaceSim().add_container("/dir").add_file("/dir/café.bin", b"data")
    fs = _fs(router, sim)

    before = await fs._ls("/dir", detail=True)
    assert property_uri not in before[0]["properties"]

    await fs._update_node(
        "vos://dir/caf%C3%A9.bin",
        {property_uri: "hermetic-update"},
    )

    request = sim.node_update_requests[-1]
    assert request.method == "POST"
    assert str(request.url) == f"{NODES_URL}/dir/caf%C3%A9.bin"
    assert {name: request.headers[name] for name in XML_HEADERS} == XML_HEADERS
    document = ElementTree.fromstring(request.content)
    assert document.get("uri") == f"vos://{AUTHORITY}/dir/café.bin"
    assert document.get("{http://www.w3.org/2001/XMLSchema-instance}type") == (
        "vos:DataNode"
    )
    assert document.get("busy") == "false"
    properties = {
        element.get("uri"): element.text
        for element in document.findall(
            f"{{{VOSPACE_NS}}}properties/{{{VOSPACE_NS}}}property"
        )
    }
    assert properties == {property_uri: "hermetic-update"}

    refreshed = await fs._ls("/dir", detail=True)
    assert refreshed[0]["properties"][property_uri] == "hermetic-update"
    assert (await fs._info("/dir/café.bin"))["properties"][property_uri] == (
        "hermetic-update"
    )
    await fs.aclose()


async def test_update_node_discovers_root_authority_before_target(
    router: respx.Router,
) -> None:
    sim = VOSpaceSim().add_file("/data.bin", b"data")
    fs = _fs(router, sim)

    await fs._update_node("/data.bin", {"ivo://example.org/props#x": "value"})

    node_requests = [
        call.request
        for call in router.calls
        if str(call.request.url).startswith(NODES_URL)
    ]
    assert [(request.method, str(request.url)) for request in node_requests] == [
        ("GET", NODES_URL),
        ("GET", f"{NODES_URL}/data.bin"),
        ("POST", f"{NODES_URL}/data.bin"),
    ]
    await fs.aclose()


async def test_update_node_rejects_target_authority_mismatch_before_post(
    router: respx.Router,
) -> None:
    sim = (
        VOSpaceSim()
        .add_file("/data.bin", b"data")
        .with_authority("/data.bin", "other.example!vault")
    )
    fs = _fs(router, sim)

    with pytest.raises(OSError, match="does not match"):
        await fs._update_node("/data.bin", {"ivo://example.org/props#x": "value"})

    assert sim.node_update_requests == []
    await fs.aclose()


@pytest.mark.parametrize("wire_type", ["StructuredDataNode", "UnstructuredDataNode"])
async def test_update_node_preserves_concrete_data_node_type(
    router: respx.Router,
    wire_type: str,
) -> None:
    sim = VOSpaceSim().add_file("/data.bin", b"data", wire_type=wire_type)
    fs = _fs(router, sim)

    await fs._update_node("/data.bin", {"ivo://example.org/props#x": "value"})

    request = sim.node_update_requests[-1]
    document = ElementTree.fromstring(request.content)
    assert document.get("{http://www.w3.org/2001/XMLSchema-instance}type") == (
        f"vos:{wire_type}"
    )
    await fs.aclose()


async def test_update_node_round_trips_xml_metacharacters(
    router: respx.Router,
) -> None:
    property_uri = "ivo://example.org/vosfs/custom?left=1&right=2"
    property_value = 'R&D <science> "quoted"'
    sim = VOSpaceSim().add_file("/data.bin", b"data")
    fs = _fs(router, sim)

    await fs._update_node("/data.bin", {property_uri: property_value})

    assert (await fs._info("/data.bin"))["properties"][property_uri] == property_value
    await fs.aclose()


async def test_update_node_preserves_container_type(router: respx.Router) -> None:
    property_uri = "ivo://example.org/vosfs#container-update"
    sim = VOSpaceSim().add_container("/dir")
    fs = _fs(router, sim)

    await fs._update_node("/dir", {property_uri: "container"})

    request = sim.node_update_requests[-1]
    document = ElementTree.fromstring(request.content)
    assert document.get("{http://www.w3.org/2001/XMLSchema-instance}type") == (
        "vos:ContainerNode"
    )
    assert document.find(f"{{{VOSPACE_NS}}}nodes") is not None
    assert (await fs._info("/dir"))["properties"][property_uri] == "container"
    await fs.aclose()


async def test_update_node_maps_service_failure_without_changing_metadata(
    router: respx.Router,
) -> None:
    property_uri = "ivo://example.org/vosfs#issue-65"
    sim = VOSpaceSim().add_file("/data.bin", b"data")
    sim.node_update_status = 409
    fs = _fs(router, sim)
    await fs._ls("/", detail=True)

    with pytest.raises(FileExistsError):
        await fs._update_node("/data.bin", {property_uri: "rejected"})

    assert "/" not in fs.dircache
    assert property_uri not in (await fs._info("/data.bin"))["properties"]
    await fs.aclose()


async def test_update_node_rejects_core_property_before_http(
    router: respx.Router,
) -> None:
    sim = VOSpaceSim().add_file("/data.bin", b"data")
    fs = _fs(router, sim)

    with pytest.raises(ValueError, match="administrative"):
        await fs._update_node("/data.bin", {LENGTH_PROPERTY_URI: "99"})

    assert len(router.calls) == 0
    await fs.aclose()


# --- mkdir / makedirs -----------------------------------------------------------


async def test_mkdir_creates_container(router: respx.Router) -> None:
    sim = VOSpaceSim()
    fs = _fs(router, sim)
    await fs._mkdir("/dir", create_parents=False)
    assert sim.nodes["/dir"] == "container"
    await fs.aclose()


async def test_delete_then_recreate_discards_stale_node_authority(
    router: respx.Router,
) -> None:
    sim = VOSpaceSim().add_container("/dir").with_authority("/dir", "old.example!vault")
    fs = _fs(router, sim)

    await fs._delete_node("/dir")
    await fs._create_container("/dir")

    assert (await fs._info("/dir"))["uri"] == f"vos://{AUTHORITY}/dir"
    await fs.aclose()


async def test_makedirs_creates_ancestors_top_down(router: respx.Router) -> None:
    sim = VOSpaceSim()
    fs = _fs(router, sim)
    await fs._makedirs("/a/b/c", exist_ok=False)
    assert sim.nodes["/a"] == "container"
    assert sim.nodes["/a/b"] == "container"
    assert sim.nodes["/a/b/c"] == "container"
    await fs.aclose()


async def test_makedirs_exist_ok_false_on_existing(router: respx.Router) -> None:
    sim = VOSpaceSim().add_container("/a")
    fs = _fs(router, sim)
    with pytest.raises(FileExistsError):
        await fs._makedirs("/a", exist_ok=False)
    await fs.aclose()


async def test_makedirs_exist_ok_true_tolerates_existing(router: respx.Router) -> None:
    sim = VOSpaceSim().add_container("/a")
    fs = _fs(router, sim)
    await fs._makedirs("/a/b", exist_ok=True)
    assert sim.nodes["/a/b"] == "container"
    await fs.aclose()


# --- rm / rmdir -----------------------------------------------------------------


async def test_rm_file(router: respx.Router) -> None:
    sim = VOSpaceSim().add_file("/f.txt", b"data")
    fs = _fs(router, sim)
    await fs._rm_file("/f.txt")
    assert "/f.txt" not in sim.nodes
    await fs.aclose()


async def test_rmdir_empty(router: respx.Router) -> None:
    sim = VOSpaceSim().add_container("/empty")
    fs = _fs(router, sim)
    await fs._rmdir("/empty")
    assert "/empty" not in sim.nodes
    await fs.aclose()


async def test_rmdir_non_empty_fails(router: respx.Router) -> None:
    sim = VOSpaceSim().add_container("/d").add_file("/d/f", b"x")
    fs = _fs(router, sim)
    with pytest.raises(OSError, match="not empty"):
        await fs._rmdir("/d")
    assert "/d" in sim.nodes  # not deleted
    await fs.aclose()


async def test_recursive_rm_deletes_leaves_first(router: respx.Router) -> None:
    sim = (
        VOSpaceSim()
        .add_container("/tree")
        .add_container("/tree/sub")
        .add_file("/tree/sub/a", b"a")
        .add_file("/tree/b", b"b")
    )
    fs = _fs(router, sim)
    await fs._rm("/tree", recursive=True)
    assert not any(p.startswith("/tree") for p in sim.nodes)
    await fs.aclose()


@pytest.mark.parametrize(
    ("parent", "child_uri"),
    [
        ("/tree", f"vos://{AUTHORITY}/tree/sub/escape"),
        ("/tree", f"vos://{AUTHORITY}/tree/%2E%2E/escape"),
        ("/tree", f"vos://{AUTHORITY}/sibling"),
        ("/", f"vos://{AUTHORITY}/top/escape"),
        ("/", f"vos://{AUTHORITY}/%2E%2E"),
        ("/", "vos://other.example!vault/escape"),
    ],
)
async def test_recursive_rm_rejects_non_immediate_listing_children(
    router: respx.Router, parent: str, child_uri: str
) -> None:
    suffix = "" if parent == "/" else parent
    document = f"""<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xsi:type="vos:ContainerNode" uri="vos://{AUTHORITY}{suffix}">
      <vos:properties/><vos:nodes>
        <vos:node xsi:type="vos:DataNode" uri="{child_uri}">
          <vos:properties><vos:property uri="ivo://ivoa.net/vospace/core#length">1</vos:property></vos:properties>
        </vos:node>
      </vos:nodes>
    </vos:node>""".encode()
    mock_capabilities(router)
    router.get(NODES_URL + suffix).mock(
        return_value=httpx.Response(200, content=document)
    )
    deletes = router.delete(url__regex=rf"^{NODES_URL}").mock(
        return_value=httpx.Response(200)
    )
    fs = make_fs(router, asynchronous=True)

    with pytest.raises(OSError, match="recursive removal failed") as excinfo:
        await fs._rm(parent, recursive=True)

    assert excinfo.value.completed == []
    assert excinfo.value.failed == [parent]
    assert deletes.call_count == 0
    await fs.aclose()


async def test_recursive_rm_reports_leaves_first_partial_completion(
    router: respx.Router,
) -> None:
    sim = (
        VOSpaceSim()
        .add_container("/tree")
        .add_file("/tree/a", b"a")
        .add_file("/tree/b", b"b")
    )
    sim.delete_statuses["/tree/b"] = 500
    fs = _fs(router, sim)

    with pytest.raises(OSError, match="recursive removal failed") as excinfo:
        await fs._rm("/tree", recursive=True)

    assert excinfo.value.completed == ["/tree/a"]
    assert excinfo.value.failed == ["/tree/b"]
    assert sim.delete_requests == ["/tree/a", "/tree/b"]
    assert "/tree" in sim.nodes
    await fs.aclose()


async def test_non_recursive_rm_on_non_empty_fails(router: respx.Router) -> None:
    sim = VOSpaceSim().add_container("/d").add_file("/d/f", b"x")
    fs = _fs(router, sim)
    with pytest.raises(OSError, match="not empty"):
        await fs._rm("/d", recursive=False)
    await fs.aclose()


async def test_rm_maxdepth_unsupported(router: respx.Router) -> None:
    sim = VOSpaceSim().add_container("/d")
    fs = _fs(router, sim)
    with pytest.raises(NotImplementedError):
        await fs._rm("/d", recursive=True, maxdepth=2)
    await fs.aclose()


# --- copy -----------------------------------------------------------------------


async def test_cp_file_relays_bytes(router: respx.Router) -> None:
    sim = VOSpaceSim().add_file("/src", b"copy-me")
    fs = _fs(router, sim)
    await fs._cp_file("/src", "/dst")
    assert sim.blobs["/dst"] == b"copy-me"
    assert sim.blobs["/src"] == b"copy-me"  # source preserved
    await fs.aclose()


def test_copy_preserves_literal_percent_destination_and_parent(
    router: respx.Router,
) -> None:
    files = {"/source": b"copy-me"}
    created, _deleted = _install_percent_mutation_routes(router, files)
    fs = make_fs(router)

    destination = paths.strip_protocol("vos://root/100%2541/copied")
    fs.copy("/source", destination)

    assert files["/source"] == b"copy-me"
    assert files["/root/100%41/copied"] == b"copy-me"
    assert "/root/100%2541" in created
    assert not any("100A" in path for path in created)
    byte_puts = [
        str(call.request.url)
        for call in router.calls
        if call.request.method == "PUT"
        and str(call.request.url).startswith(f"{BASE_URL}/files")
    ]
    assert byte_puts == [f"{BASE_URL}/files?p=/root/100%2541/copied"]
    fs.close()


def test_copy_raw_percent_urls_decode_source_and_destination_once(
    router: respx.Router,
) -> None:
    source_root = (
        f'<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:type="vos:ContainerNode" uri="vos://{AUTHORITY}/src%2541">'
        f'<vos:properties/><vos:nodes><vos:node xsi:type="vos:DataNode" '
        f'uri="vos://{AUTHORITY}/src%2541/child"><vos:properties>'
        f'<vos:property uri="ivo://ivoa.net/vospace/core#length">5</vos:property>'
        f"</vos:properties></vos:node></vos:nodes></vos:node>"
    ).encode()
    files = {"/src%41/file": b"scalar", "/src%41/child": b"child"}
    created, _deleted = _install_percent_mutation_routes(
        router,
        files,
        listings={"/src%41": source_root},
    )
    fs = make_fs(router)

    fs.copy("vos://src%2541/file", "vos://dest%2542/copied")
    fs.copy("vos://src%2541", "vos://tree%2542", recursive=True)

    assert files["/dest%42/copied"] == b"scalar"
    assert files["/tree%42/child"] == b"child"
    assert all("/vos:" not in path for path in files)
    assert "/tree%2542" in created
    byte_urls = [
        str(call.request.url)
        for call in router.calls
        if call.request.method in {"GET", "PUT"}
        and str(call.request.url).startswith(f"{BASE_URL}/files")
    ]
    assert any("p=/src%2541/file" in url for url in byte_urls)
    assert any("p=/dest%2542/copied" in url for url in byte_urls)
    assert any("p=/tree%2542/child" in url for url in byte_urls)
    fs.close()


def test_mkdir_preserves_literal_percent_parent(router: respx.Router) -> None:
    created, _deleted = _install_percent_mutation_routes(router, {})
    fs = make_fs(router)

    fs.mkdir("vos://root/100%2541/new", create_parents=True)

    assert "/root/100%2541" in created
    assert "/root/100%2541/new" in created
    assert not any("100A" in path for path in created)
    fs.close()


def test_recursive_copy_creates_tree(router: respx.Router) -> None:
    sim = (
        VOSpaceSim()
        .add_container("/from")
        .add_file("/from/a", b"a")
        .add_container("/from/sub")
        .add_file("/from/sub/b", b"b")
    )
    sim.install(router)
    fs = make_fs(router)
    fs.copy("/from", "/to", recursive=True)
    assert sim.blobs["/to/a"] == b"a"
    assert sim.blobs["/to/sub/b"] == b"b"
    # The intermediate container node is created, not just the leaf blob, so the
    # copy would also succeed against a real service that requires it.
    assert sim.nodes["/to/sub"] == "container"
    fs.close()


def test_copy_internal_link_materializes_target_bytes(router: respx.Router) -> None:
    target = f"vos://{AUTHORITY}/target"
    sim = VOSpaceSim().add_file("/target", b"linked").add_link("/src", target)
    sim.install(router)
    fs = make_fs(router)

    fs.copy("/src", "/dst")

    assert sim.nodes["/dst"] == "data"
    assert sim.blobs["/dst"] == b"linked"
    assert sim.nodes["/src"] == "link"
    fs.close()


def test_copy_rejects_external_link_before_destination_mutation(
    router: respx.Router,
) -> None:
    target = "vos://external.example!vault/target?token=secret-token"
    sim = VOSpaceSim().add_link("/src", target)
    sim.install(router)
    fs = make_fs(router, token="service-token")

    with pytest.raises(NotImplementedError, match="external LinkNode"):
        fs.copy("/src", "/new-parent/dst")

    assert "/new-parent" not in sim.nodes
    assert "/new-parent/dst" not in sim.nodes
    assert sim.byte_requests == []
    assert not any(call.request.url.path == "/arc/synctrans" for call in router.calls)
    assert not any(
        call.request.url.host != "staging.canfar.net" for call in router.calls
    )
    assert [call.request for call in router.calls if call.request.method != "GET"] == []
    assert "secret-token" not in str(router.calls)
    fs.close()


# --- move -----------------------------------------------------------------------


async def test_mv_file_copies_then_deletes_data(router: respx.Router) -> None:
    sim = VOSpaceSim().add_file("/src", b"moved")
    fs = _fs(router, sim)

    await fs._mv_file("/src", "/dst")

    assert sim.blobs["/dst"] == b"moved"
    assert "/src" not in sim.nodes
    await fs.aclose()


async def test_mv_file_same_data_path_is_noop(router: respx.Router) -> None:
    sim = VOSpaceSim().add_file("/src", b"source")
    fs = _fs(router, sim)

    await fs._mv_file("/src", "/src")

    assert [call.request for call in router.calls if call.request.method != "GET"] == []
    assert sim.nodes["/src"] == "data"
    assert sim.blobs["/src"] == b"source"
    assert sim.delete_requests == []
    await fs.aclose()


async def test_mv_file_rejects_existing_data_destination_before_mutation(
    router: respx.Router,
) -> None:
    sim = VOSpaceSim().add_file("/src", b"source").add_file("/dst", b"existing")
    fs = _fs(router, sim)

    with pytest.raises(FileExistsError, match="move destination already exists"):
        await fs._mv_file("/src", "/dst")

    assert [call.request for call in router.calls if call.request.method != "GET"] == []
    assert sim.nodes["/src"] == "data"
    assert sim.blobs["/src"] == b"source"
    assert sim.nodes["/dst"] == "data"
    assert sim.blobs["/dst"] == b"existing"
    assert sim.delete_requests == []
    await fs.aclose()


@pytest.mark.parametrize("destination_state", ["absent", "existing", "same"])
async def test_mv_file_rejects_link_before_mutation(
    router: respx.Router,
    destination_state: str,
) -> None:
    target = f"vos://{AUTHORITY}/target"
    sim = VOSpaceSim().add_file("/target", b"target").add_link("/src", target)
    destination = "/src" if destination_state == "same" else "/dst"
    if destination_state == "existing":
        sim.add_file(destination, b"existing")
    fs = _fs(router, sim)

    with pytest.raises(NotImplementedError, match="moving a LinkNode"):
        await fs._mv_file("/src", destination)

    node_requests = [
        call.request
        for call in router.calls
        if str(call.request.url).startswith(NODES_URL)
    ]
    assert ("GET", f"{NODES_URL}/src") in [
        (request.method, str(request.url)) for request in node_requests
    ]
    assert [call.request for call in router.calls if call.request.method != "GET"] == []
    assert sim.nodes["/src"] == "link"
    assert sim.targets["/src"] == target
    if destination_state == "absent":
        assert destination not in sim.nodes
        assert destination not in sim.blobs
    elif destination_state == "existing":
        assert sim.nodes[destination] == "data"
        assert sim.blobs[destination] == b"existing"
    assert sim.delete_requests == []
    await fs.aclose()


def test_move_requires_absent_destination(router: respx.Router) -> None:
    sim = VOSpaceSim().add_file("/src", b"x").add_file("/dst", b"exists")
    sim.install(router)
    fs = make_fs(router)
    with pytest.raises(FileExistsError):
        fs.mv("/src", "/dst")
    fs.close()


def test_move_copies_then_deletes_source(router: respx.Router) -> None:
    sim = VOSpaceSim().add_file("/src", b"moved")
    sim.install(router)
    fs = make_fs(router)
    fs.mv("/src", "/dst")
    assert sim.blobs["/dst"] == b"moved"
    assert "/src" not in sim.nodes
    fs.close()


def test_move_rejects_link_before_mutation(router: respx.Router) -> None:
    target = f"vos://{AUTHORITY}/target"
    sim = VOSpaceSim().add_file("/target", b"target").add_link("/src", target)
    sim.install(router)
    fs = make_fs(router)

    with pytest.raises(NotImplementedError, match="moving a LinkNode"):
        fs.mv("/src", "/dst")

    node_requests = [
        call.request
        for call in router.calls
        if str(call.request.url).startswith(NODES_URL)
    ]
    assert ("GET", f"{NODES_URL}/src") in [
        (request.method, str(request.url)) for request in node_requests
    ]
    assert [call.request for call in router.calls if call.request.method != "GET"] == []
    assert sim.nodes["/src"] == "link"
    assert sim.targets["/src"] == target
    assert "/dst" not in sim.nodes
    assert "/dst" not in sim.blobs
    assert sim.delete_requests == []
    fs.close()


def test_move_rejects_link_before_existing_destination_check(
    router: respx.Router,
) -> None:
    target = f"vos://{AUTHORITY}/target"
    sim = (
        VOSpaceSim()
        .add_file("/target", b"target")
        .add_link("/src", target)
        .add_file("/dst", b"existing")
    )
    sim.install(router)
    fs = make_fs(router)

    with pytest.raises(NotImplementedError, match="moving a LinkNode"):
        fs.mv("/src", "/dst")

    node_requests = [
        call.request
        for call in router.calls
        if str(call.request.url).startswith(NODES_URL)
    ]
    assert ("GET", f"{NODES_URL}/src") in [
        (request.method, str(request.url)) for request in node_requests
    ]
    assert [call.request for call in router.calls if call.request.method != "GET"] == []
    assert sim.nodes["/src"] == "link"
    assert sim.targets["/src"] == target
    assert sim.nodes["/dst"] == "data"
    assert sim.blobs["/dst"] == b"existing"
    assert sim.delete_requests == []
    fs.close()


def test_move_rejects_link_before_same_path_noop(router: respx.Router) -> None:
    target = f"vos://{AUTHORITY}/target"
    sim = VOSpaceSim().add_file("/target", b"target").add_link("/src", target)
    sim.install(router)
    fs = make_fs(router)

    with pytest.raises(NotImplementedError, match="moving a LinkNode"):
        fs.mv("/src", "/src")

    node_requests = [
        call.request
        for call in router.calls
        if str(call.request.url).startswith(NODES_URL)
    ]
    assert ("GET", f"{NODES_URL}/src") in [
        (request.method, str(request.url)) for request in node_requests
    ]
    assert [call.request for call in router.calls if call.request.method != "GET"] == []
    assert sim.nodes["/src"] == "link"
    assert sim.targets["/src"] == target
    assert sim.delete_requests == []
    fs.close()


def test_move_preserves_literal_percent_destination_before_source_delete(
    router: respx.Router,
) -> None:
    files = {"/move-source": b"moved"}
    _created, deleted = _install_percent_mutation_routes(router, files)
    fs = make_fs(router)

    fs.mv("/move-source", "vos://moved%2541")

    assert files == {"/moved%41": b"moved"}
    assert deleted == ["/move-source"]
    assert all("movedA" not in str(call.request.url) for call in router.calls)
    byte_puts = [
        str(call.request.url)
        for call in router.calls
        if call.request.method == "PUT"
        and str(call.request.url).startswith(f"{BASE_URL}/files")
    ]
    assert byte_puts == [f"{BASE_URL}/files?p=/moved%2541"]
    fs.close()


def test_recursive_move_keeps_source_when_one_child_copy_fails(
    router: respx.Router,
) -> None:
    source_listing = (
        f'<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:type="vos:ContainerNode" uri="vos://{AUTHORITY}/src">'
        f'<vos:properties/><vos:nodes><vos:node xsi:type="vos:DataNode" '
        f'uri="vos://{AUTHORITY}/src/a"><vos:properties/></vos:node>'
        f'<vos:node xsi:type="vos:DataNode" uri="vos://{AUTHORITY}/src/b">'
        f"<vos:properties/></vos:node></vos:nodes></vos:node>"
    ).encode()
    files = {"/src/a": b"a"}
    _created, deleted = _install_percent_mutation_routes(
        router,
        files,
        listings={"/src": source_listing},
        data_nodes={"/src/a", "/src/b"},
    )
    fs = make_fs(router)

    with pytest.raises(FileNotFoundError):
        fs.mv("/src", "/dest", recursive=True)

    assert deleted == []
    assert files["/src/a"] == b"a"
    fs.close()


def test_recursive_move_keeps_source_when_copy_omits_one_child(
    router: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = (
        VOSpaceSim()
        .add_container("/src")
        .add_file("/src/a", b"a")
        .add_file("/src/b", b"b")
    )
    sim.install(router)
    fs = make_fs(router)
    copy = fs.copy

    def incomplete_copy(*_args: object, **_kwargs: object) -> None:
        fs.mkdir("/dest")
        copy("/src/a", "/dest/a")

    monkeypatch.setattr(fs, "copy", incomplete_copy)

    with pytest.raises(errors.VOSpaceError, match="incomplete"):
        fs.mv("/src", "/dest", recursive=True)

    assert sim.nodes["/dest"] == "container"
    assert sim.blobs["/dest/a"] == b"a"
    assert "/dest/b" not in sim.nodes
    assert sim.blobs["/src/a"] == b"a"
    assert sim.blobs["/src/b"] == b"b"
    assert sim.delete_requests == []
    fs.close()


def test_move_same_path_is_noop(router: respx.Router) -> None:
    sim = VOSpaceSim().add_file("/src", b"x")
    sim.install(router)
    fs = make_fs(router)
    fs.mv("/src", "/src")
    assert sim.blobs["/src"] == b"x"
    fs.close()


def test_move_empty_directory_creates_destination(router: respx.Router) -> None:
    # Regression: a non-recursive move of an empty container must not delete the
    # source without creating the destination (a data-loss bug).
    sim = VOSpaceSim().add_container("/emptydir")
    sim.install(router)
    fs = make_fs(router)
    fs.mv("/emptydir", "/dest")
    assert "/emptydir" not in sim.nodes
    assert sim.nodes["/dest"] == "container"
    fs.close()


def test_recursive_copy_preserves_empty_container(router: respx.Router) -> None:
    # Regression: recursive copy must materialize empty source containers, not
    # only containers that happen to hold a copied file.
    sim = (
        VOSpaceSim()
        .add_container("/from")
        .add_file("/from/a", b"a")
        .add_container("/from/empty")
    )
    sim.install(router)
    fs = make_fs(router)
    fs.copy("/from", "/to", recursive=True)
    assert sim.blobs["/to/a"] == b"a"
    assert sim.nodes["/to/empty"] == "container"
    fs.close()


async def test_rm_file_refuses_a_container(router: respx.Router) -> None:
    sim = VOSpaceSim().add_container("/dir")
    fs = _fs(router, sim)
    with pytest.raises(IsADirectoryError):
        await fs._rm_file("/dir")
    await fs.aclose()


async def test_uncertain_node_mutation_invalidates_cached_state(
    router: respx.Router,
) -> None:
    sim = VOSpaceSim().add_file("/data.bin", b"data")
    sim.node_update_status = 500
    fs = _fs(router, sim)
    await fs._ls("/", detail=True)
    assert "/" in fs.dircache

    with pytest.raises(OSError, match="HTTP 500"):
        await fs._update_node("/data.bin", {"ivo://example.org/props#x": "value"})

    assert "/" not in fs.dircache
    await fs.aclose()


async def test_incomplete_listing_refresh_evicts_stale_cached_entry(
    router: respx.Router,
) -> None:
    malformed = f"""<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xsi:type="vos:ContainerNode" uri="vos://{AUTHORITY}/tree">
      <vos:properties/><vos:nodes>
        <vos:node xsi:type="vos:DataNode" uri="vos://{AUTHORITY}/tree/sub/escape">
          <vos:properties><vos:property uri="ivo://ivoa.net/vospace/core#length">1</vos:property></vos:properties>
        </vos:node>
      </vos:nodes>
    </vos:node>""".encode()
    mock_capabilities(router)
    router.get(f"{NODES_URL}/tree").mock(
        return_value=httpx.Response(200, content=malformed)
    )
    fs = make_fs(router, asynchronous=True)
    complete = [{"name": "/tree/known", "type": "file", "size": 1}]
    fs.dircache["/tree"] = complete

    with pytest.raises(OSError, match="immediate descendant"):
        await fs._fetch_listing("/tree")

    assert "/tree" not in fs.dircache
    await fs.aclose()


def test_invalidate_cache_clears_path_or_all_without_io(router: respx.Router) -> None:
    fs = make_fs(router)
    fs.dircache["/"] = []
    fs.dircache["/tree"] = []
    fs.dircache["/tree/sub"] = []
    fs.dircache["/other"] = []

    fs.invalidate_cache("/tree")
    assert list(fs.dircache) == ["/other"]
    fs.invalidate_cache()
    assert list(fs.dircache) == []
    assert len(router.calls) == 0
    fs.close()
