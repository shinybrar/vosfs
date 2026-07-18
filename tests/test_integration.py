"""Credential-gated live integration suite against a real OpenCADC service.

This suite is the authoritative release gate (contract section 15.2). It is
marked ``integration`` and skipped by default; it runs only when a credential
and endpoint are configured. It creates a unique run namespace, exercises the
full lifecycle, and removes the namespace leaves-first in ``finally``.

Configuration (environment):

- ``VOSFS_TEST_ENDPOINT`` — service base URL (default ``https://staging.canfar.net/arc``).
- ``VOSFS_CERT_FILE`` / ``VOSFS_TOKEN`` / ``VOSFS_TOKEN_FILE`` — one credential.
- ``VOSFS_TEST_ROOT`` — an existing writable container to create the run
  namespace under (for example ``/home/<user>``).

Run with ``pytest --no-cov -m integration`` so the focused live suite does not
inherit the offline suite's global coverage threshold.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import TYPE_CHECKING

import pytest
from fsspec.asyn import sync

from vosfs import VOSpaceFileSystem

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

_ENDPOINT = os.environ.get("VOSFS_TEST_ENDPOINT", "https://staging.canfar.net/arc")
_ROOT = os.environ.get("VOSFS_TEST_ROOT")
_HAS_CREDENTIAL = any(
    os.environ.get(name)
    for name in ("VOSFS_CERT_FILE", "VOSFS_TOKEN", "VOSFS_TOKEN_FILE")
)

_SKIP_REASON = "set VOSFS_TEST_ROOT and one credential env var to run the live gate"
requires_service = pytest.mark.skipif(
    not (_ROOT and _HAS_CREDENTIAL), reason=_SKIP_REASON
)


@pytest.fixture
def fs() -> VOSpaceFileSystem:
    """A live filesystem resolving its credential from the environment."""
    filesystem = VOSpaceFileSystem(_ENDPOINT, skip_instance_cache=True)
    try:
        yield filesystem
    finally:
        filesystem.close()


@pytest.fixture
def run_namespace(fs: VOSpaceFileSystem) -> str:
    """A unique run container removed leaves-first afterwards, reporting residue."""
    namespace = f"{_ROOT.rstrip('/')}/vosfs-it-{uuid.uuid4().hex[:12]}"
    fs.mkdir(namespace)
    try:
        yield namespace
    finally:
        with contextlib.suppress(FileNotFoundError):
            fs.rm(namespace, recursive=True)
        if fs.exists(namespace):
            pytest.fail(f"integration namespace was not fully removed: {namespace}")


@requires_service
def test_full_lifecycle(fs: VOSpaceFileSystem, run_namespace: str) -> None:
    payload = b"vosfs integration payload\n" * 32
    remote = f"{run_namespace}/data.bin"

    # Create, list, inspect.
    fs.pipe_file(remote, payload)
    assert fs.exists(remote)
    assert fs.info(remote)["size"] == len(payload)
    assert fs.ls(run_namespace, detail=False) == [remote]

    # Private protocol-conformance update, verified only through public metadata.
    property_uri = f"ivo://example.org/vosfs/integration#{uuid.uuid4().hex}"
    property_value = f"vosfs-{uuid.uuid4().hex}"
    sync(
        fs.loop,
        fs._update_node,
        remote,
        {property_uri: property_value},
    )
    assert fs.info(remote)["properties"][property_uri] == property_value

    # Byte round trip.
    assert fs.cat_file(remote) == payload

    # Copy and move.
    copy_target = f"{run_namespace}/copy.bin"
    fs.copy(remote, copy_target)
    assert fs.cat_file(copy_target) == payload
    move_target = f"{run_namespace}/moved.bin"
    fs.mv(copy_target, move_target)
    assert fs.exists(move_target)
    assert not fs.exists(copy_target)

    # Non-recursive delete of a single node.
    fs.rm_file(move_target)
    assert not fs.exists(move_target)


@requires_service
def test_directory_operations(fs: VOSpaceFileSystem, run_namespace: str) -> None:
    nested = f"{run_namespace}/a/b/c"
    fs.makedirs(nested, exist_ok=True)
    assert fs.isdir(nested)
    fs.pipe_file(f"{nested}/leaf.txt", b"leaf")
    found = fs.find(run_namespace)
    assert f"{nested}/leaf.txt" in found


@requires_service
def test_recursive_get(
    fs: VOSpaceFileSystem,
    run_namespace: str,
    tmp_path: Path,
) -> None:
    remote_tree = f"{run_namespace}/recursive-get"
    remote_empty = f"{remote_tree}/empty"
    remote_nested = f"{remote_tree}/nested"
    fs.makedirs(remote_empty)
    fs.makedirs(remote_nested)
    fs.pipe_file(f"{remote_tree}/root.bin", b"root-live-bytes")
    fs.pipe_file(f"{remote_nested}/leaf.bin", b"leaf-live-bytes")

    local_tree = tmp_path / "download"
    fs.get(remote_tree, str(local_tree), recursive=True)

    assert local_tree.is_dir()
    assert (local_tree / "empty").is_dir()
    assert list((local_tree / "empty").iterdir()) == []
    assert (local_tree / "root.bin").read_bytes() == b"root-live-bytes"
    assert (local_tree / "nested" / "leaf.bin").read_bytes() == b"leaf-live-bytes"
