"""Cancellation and ownership tests for coordinated uploads."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from conftest import BASE_URL, NODES_URL, SYNC_URL, make_fs
from vospace_sim import VOSpaceSim

from vosfs import VOSpaceFileSystem

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "repeat_cancel",
    [False, True],
    ids=["single-cancel", "repeated-cancel"],
)
@pytest.mark.parametrize("operation", ["pipe", "put"])
async def test_cancelled_coordinator_quiesces_spawned_transfers_before_return(
    operation: str,
    repeat_cancel: bool,
    tmp_path: Path,
) -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    sim = VOSpaceSim()
    sim.install(router)
    parent_request_started = asyncio.Event()
    release_parent_request = asyncio.Event()
    parent_request_finished = asyncio.Event()
    handler_tasks: list[asyncio.Task[object]] = []
    transport_requests: list[tuple[str, str]] = []

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        transport_requests.append((request.method, str(request.url)))
        if request.method == "GET" and str(request.url) == f"{NODES_URL}/cancel":
            task = asyncio.current_task()
            assert task is not None
            handler_tasks.append(task)
            parent_request_started.set()
            try:
                await release_parent_request.wait()
            finally:
                parent_request_finished.set()
            raise asyncio.CancelledError
        return await router.async_handler(request)

    fs = VOSpaceFileSystem(
        BASE_URL,
        transport=httpx.MockTransport(transport_handler),
        asynchronous=True,
        skip_instance_cache=True,
    )
    if operation == "pipe":
        coordinated = fs._pipe(
            {"/cancel/a.bin": b"a", "/cancel/b.bin": b"b"},
            batch_size=2,
        )
    else:
        first = tmp_path / "a.bin"
        second = tmp_path / "b.bin"
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        coordinated = fs._put(
            [str(first), str(second)],
            ["/cancel/a.bin", "/cancel/b.bin"],
            batch_size=2,
        )

    operation_task = asyncio.create_task(coordinated)
    await asyncio.wait_for(parent_request_started.wait(), timeout=1)
    operation_task.cancel()
    if repeat_cancel:
        asyncio.get_running_loop().call_soon(operation_task.cancel)
    with pytest.raises(asyncio.CancelledError):
        await operation_task

    finished_when_cancel_returned = parent_request_finished.is_set()
    handler_done_when_cancel_returned = all(task.done() for task in handler_tasks)
    requests_when_cancel_returned = len(transport_requests)
    release_parent_request.set()
    for _ in range(10):
        await asyncio.sleep(0)
    requests_after_release = len(transport_requests)
    await fs.aclose()

    assert finished_when_cancel_returned
    assert handler_done_when_cancel_returned
    assert requests_after_release == requests_when_cancel_returned


async def test_coordinator_state_never_applies_to_another_filesystem() -> None:
    first_router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    second_router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    first_sim = VOSpaceSim()
    second_sim = VOSpaceSim()
    first_sim.install(first_router)
    second_sim.install(second_router)
    first_fs = make_fs(first_router, asynchronous=True)
    second_fs = make_fs(second_router, asynchronous=True)

    async def negotiate_after_nested_work(request: httpx.Request) -> httpx.Response:
        await second_fs._makedirs("/shared", exist_ok=True)
        return first_sim._negotiate(request)

    first_router.post(SYNC_URL).mock(side_effect=negotiate_after_nested_work)

    await first_fs._pipe("/shared/nested/data.bin", b"first")

    assert await second_fs._isdir("/shared")
    await first_fs.aclose()
    await second_fs.aclose()


async def test_expired_coordinator_context_stops_spawned_descendant_before_io() -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    sim = VOSpaceSim()
    sim.install(router)
    release_descendant = asyncio.Event()
    descendant_tasks: list[asyncio.Task[None]] = []

    async def late_write() -> None:
        await release_descendant.wait()
        await fs._pipe_file("/late/data.bin", b"late")

    async def negotiate_after_spawning(request: httpx.Request) -> httpx.Response:
        if not descendant_tasks:
            descendant_tasks.append(asyncio.create_task(late_write()))
        return sim._negotiate(request)

    router.post(SYNC_URL).mock(side_effect=negotiate_after_spawning)
    fs = make_fs(router, asynchronous=True)

    await fs._pipe("/primary/data.bin", b"primary")
    requests_when_operation_returned = len(router.calls)
    release_descendant.set()
    with pytest.raises(asyncio.CancelledError):
        await descendant_tasks[0]
    requests_after_descendant = len(router.calls)
    await fs.aclose()

    assert requests_after_descendant == requests_when_operation_returned
