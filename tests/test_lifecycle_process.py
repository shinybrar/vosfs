"""Lifecycle and process-boundary reconstruction gates (contract section 12)."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from conftest import CAPABILITIES, make_fs
from dask.base import tokenize

from vosfs import VOSpaceFileSystem

if TYPE_CHECKING:
    import respx


def _runtime_probe(filesystem: VOSpaceFileSystem) -> dict[str, Any]:
    """Describe reconstructed state without performing remote I/O."""
    return {
        "pid": filesystem._pid,
        "process": os.getpid(),
        "token": tokenize(filesystem),
        "has_loop": filesystem._loop is not None,
        "clients": filesystem._pool._clients,
        "client_lock": filesystem._pool._lock,
        "transport": filesystem._pool._injected,
        "bindings": filesystem._bindings,
        "bindings_lock": filesystem._bindings_lock,
        "authority": filesystem._authority,
        "cache": list(filesystem.dircache),
    }


def _fork_probe(filesystem: VOSpaceFileSystem, connection: Any) -> None:
    """Try to consume an inherited cached listing in a forked child."""
    try:
        asyncio.run(filesystem._ls("/"))
    except Exception as exc:  # noqa: BLE001 - send the observable child failure
        connection.send((type(exc).__name__, str(exc)))
    else:
        connection.send((None, None))
    finally:
        connection.close()


async def test_concurrent_close_is_idempotent_and_blocks_cached_io(
    router: respx.Router,
) -> None:
    filesystem = make_fs(router, asynchronous=True)
    filesystem.dircache["/"] = [{"name": "/cached", "type": "file", "size": 1}]

    await asyncio.gather(filesystem.aclose(), filesystem.aclose())

    with pytest.raises(ValueError, match="closed"):
        await filesystem._ls("/")


async def test_pickle_and_json_reconstruct_only_constructor_state(
    router: respx.Router,
) -> None:
    router.get("/capabilities").mock(
        return_value=httpx.Response(200, content=CAPABILITIES)
    )
    filesystem = make_fs(router, asynchronous=True, token="literal")
    constructor_token = tokenize(filesystem)
    await filesystem._get_bindings()
    filesystem._authority = "example.test!vault"
    filesystem.dircache["/"] = []
    assert tokenize(filesystem) == constructor_token

    pickled = pickle.loads(pickle.dumps(filesystem))  # noqa: S301 - trusted round-trip
    type(filesystem)._cache.pop(pickled._fs_token, None)
    restored_json = VOSpaceFileSystem.from_json(filesystem.to_json())
    assert isinstance(restored_json, VOSpaceFileSystem)

    for restored in (pickled, restored_json):
        assert restored._loop is None
        assert restored._pool._clients == {}
        assert restored._pool._lock is None
        assert restored._pool._injected is None
        assert restored._bindings is None
        assert restored._bindings_lock is None
        assert restored._authority is None
        assert list(restored.dircache) == []
    await filesystem.aclose()


@pytest.mark.parametrize(("asynchronous", "has_loop"), [(True, False), (False, True)])
def test_spawn_reconstructs_fresh_runtime_with_stable_dask_token(
    router: respx.Router,
    asynchronous: bool,
    has_loop: bool,
) -> None:
    filesystem = make_fs(router, asynchronous=asynchronous, token="literal")
    expected_token = tokenize(filesystem)
    context = multiprocessing.get_context("spawn")

    with ProcessPoolExecutor(max_workers=1, mp_context=context) as executor:
        result = executor.submit(_runtime_probe, filesystem).result(timeout=30)

    assert result == {
        "pid": result["process"],
        "process": result["process"],
        "token": expected_token,
        "has_loop": has_loop,
        "clients": {},
        "client_lock": None,
        "transport": None,
        "bindings": None,
        "bindings_lock": None,
        "authority": None,
        "cache": [],
    }


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is unavailable")
def test_forked_live_instance_fails_before_cached_io(router: respx.Router) -> None:
    filesystem = make_fs(router, asynchronous=True)
    filesystem.dircache["/"] = [{"name": "/cached", "type": "file", "size": 1}]
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_fork_probe, args=(filesystem, child))

    process.start()
    child.close()
    result = parent.recv()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert result[0] == "RuntimeError"
    assert "reconstruct" in result[1]


def test_dask_token_depends_only_on_constructor_state(router: respx.Router) -> None:
    first = make_fs(router, asynchronous=True, token="one")
    same = make_fs(router, asynchronous=True, token="one")
    changed = make_fs(router, asynchronous=True, token="two")

    assert tokenize(first) == tokenize(same)
    assert tokenize(first) != tokenize(changed)
