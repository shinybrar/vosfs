"""Copy behavior for the VOSpace namespace contract."""

import pytest
import respx
from conftest import AUTHORITY, BASE_URL, make_fs
from namespace_support import _fs, _install_percent_mutation_routes
from vospace_sim import VOSpaceSim


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

    fs.copy("/source", "vos://root/100%2541/copied")

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
