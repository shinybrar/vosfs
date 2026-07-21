"""Tests for VOSpace namespace and mutation primitives (section 10)."""

import httpx
import pytest
import respx
from conftest import AUTHORITY, NODES_URL, make_fs, mock_capabilities
from defusedxml import ElementTree
from namespace_support import _fs, _install_percent_mutation_routes
from vospace_sim import VOSpaceSim

from vosfs.nodes import LENGTH_PROPERTY_URI, VOSPACE_NS, XML_HEADERS


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


def test_mkdir_preserves_literal_percent_parent(router: respx.Router) -> None:
    created, _deleted = _install_percent_mutation_routes(router, {})
    fs = make_fs(router)

    fs.mkdir("vos://root/100%2541/new", create_parents=True)

    assert "/root/100%2541" in created
    assert "/root/100%2541/new" in created
    assert not any("100A" in path for path in created)
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
