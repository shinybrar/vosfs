"""Tests for the namespace and mutation contract (section 10)."""

import pytest
import respx
from conftest import make_fs
from vospace_sim import VOSpaceSim


def _fs(router, sim, *, asynchronous=True):
    sim.install(router)
    return make_fs(router, asynchronous=asynchronous)


# --- mkdir / makedirs -----------------------------------------------------------


async def test_mkdir_creates_container(router: respx.Router) -> None:
    sim = VOSpaceSim()
    fs = _fs(router, sim)
    await fs._mkdir("/dir", create_parents=False)
    assert sim.nodes["/dir"] == "container"
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


# --- move -----------------------------------------------------------------------


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
