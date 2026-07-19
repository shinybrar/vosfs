"""Cancellation and ownership tests for coordinated uploads."""

from __future__ import annotations

import asyncio
import gc
import traceback
import warnings
import weakref
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
from conftest import BASE_URL, NODES_URL, SYNC_URL, make_fs
from fsspec.asyn import AsyncFileSystem
from fsspec.callbacks import Callback
from vospace_sim import VOSpaceSim

from vosfs import VOSpaceError, VOSpaceFileSystem, _write_coordination

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from pathlib import Path

    from vosfs._write_coordination import _WriteParentState


def _three_file_upload(
    fs: VOSpaceFileSystem,
    operation: str,
    tmp_path: Path,
    remote_paths: list[str],
    callback: Callback | None = None,
) -> Coroutine[Any, Any, list[Any] | None]:
    """Create one inherited three-file upload through its public mapping inputs."""
    if operation == "pipe":
        return fs._pipe(
            dict.fromkeys(remote_paths, b"data"),
            batch_size=1,
            mode="overwrite",
        )
    local_paths = []
    for name in ("a", "b", "c"):
        local_path = tmp_path / f"{name}.bin"
        local_path.write_bytes(b"data")
        local_paths.append(str(local_path))
    return fs._put(
        local_paths,
        remote_paths,
        batch_size=1,
        callback=callback or Callback(),
        mode="overwrite",
    )


async def _await_upload_termination(
    operation_task: asyncio.Task[list[Any] | None],
    termination: str,
) -> None:
    """End one upload and release exception frames that own raw awaitables."""
    if termination == "cancellation":
        operation_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation_task
        return
    with pytest.raises(VOSpaceError, match="forced transfer failure") as error:
        await operation_task
    if error.value.__traceback__ is not None:
        traceback.clear_frames(error.value.__traceback__)
        error.value.__traceback__ = None


@pytest.mark.parametrize("operation", ["pipe", "put"])
async def test_coordinator_delegates_to_authoritative_fsspec_hook(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    fs = make_fs(router, asynchronous=True)
    delegated: list[tuple[tuple[object, ...], dict[str, object]]] = []
    expected = [object()]

    async def inherited(
        _self: object,
        *args: object,
        **kwargs: object,
    ) -> list[Any]:
        callback = kwargs.pop("callback", None)
        if callback is not None:
            assert callable(callback.branch_coro)
        delegated.append((args, kwargs))
        return expected

    monkeypatch.setattr(AsyncFileSystem, f"_{operation}", inherited)
    if operation == "pipe":
        result = await fs._pipe(
            {"/parent/data.bin": b"data"},
            batch_size=0,
            mode="create",
        )
        assert delegated == [
            (
                ({"/parent/data.bin": b"data"},),
                {"value": None, "batch_size": 0, "mode": "create"},
            )
        ]
    else:
        result = await fs._put(
            "local/*.bin",
            "/parent/",
            recursive=True,
            batch_size=0,
            maxdepth=2,
            mode="create",
        )
        assert delegated == [
            (
                ("local/*.bin", "/parent/"),
                {
                    "recursive": True,
                    "batch_size": 0,
                    "maxdepth": 2,
                    "mode": "create",
                },
            )
        ]

    await fs.aclose()
    assert result is expected


async def test_deferred_callback_preserves_caller_branch_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BranchOverrideCallback(Callback):
        def __init__(self) -> None:
            super().__init__()
            self.branch_calls = 0
            self.wrapped_calls = 0
            self.child = Callback()

        def branch_coro(
            self,
            function: Callable[..., Awaitable[Any]],
        ) -> Callable[..., Awaitable[Any]]:
            self.branch_calls += 1

            def wrapped(
                path1: str,
                path2: str,
                **kwargs: Any,
            ) -> Awaitable[Any]:
                self.wrapped_calls += 1
                return function(path1, path2, callback=self.child, **kwargs)

            return wrapped

    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    fs = make_fs(router, asynchronous=True)
    callback = BranchOverrideCallback()
    awaitable_built = asyncio.Event()
    release_schedule = asyncio.Event()
    observed_children: list[Callback] = []

    async def put_file(
        _lpath: str,
        _rpath: str,
        *,
        callback: Callback,
    ) -> None:
        observed_children.append(callback)

    async def inherited_put(
        _self: object,
        *_args: object,
        callback: Callback,
        **_kwargs: object,
    ) -> list[None]:
        wrapped = callback.branch_coro(put_file)
        item = wrapped("local", "/remote")
        awaitable_built.set()
        await release_schedule.wait()
        return [await item]

    monkeypatch.setattr(AsyncFileSystem, "_put", inherited_put)
    operation_task = asyncio.create_task(fs._put("local", "/remote", callback=callback))
    await awaitable_built.wait()
    try:
        assert callback.branch_calls == 1
        assert callback.wrapped_calls == 0
    finally:
        release_schedule.set()
        await operation_task
        await fs.aclose()

    assert callback.wrapped_calls == 1
    assert observed_children == [callback.child]


async def test_cancelled_put_waits_for_callback_prelude_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prelude_started = asyncio.Event()
    release_prelude = asyncio.Event()
    child_done = asyncio.Event()

    class PreludeCallback(Callback):
        def branch_coro(
            self,
            function: Callable[..., Awaitable[Any]],
        ) -> Callable[..., Awaitable[Any]]:
            async def wrapped(
                path1: str,
                path2: str,
                **kwargs: Any,
            ) -> Any:
                prelude_started.set()
                try:
                    await release_prelude.wait()
                    return await function(path1, path2, **kwargs)
                finally:
                    child_done.set()

            return wrapped

    async def put_file(
        _lpath: str,
        _rpath: str,
        **_kwargs: Any,
    ) -> None:
        pass

    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    fs = make_fs(router, asynchronous=True)
    monkeypatch.setattr(fs, "_put_file", put_file)
    local_path = tmp_path / "data.bin"
    local_path.write_bytes(b"data")
    operation_task = asyncio.create_task(
        fs._put(
            [str(local_path)],
            ["/remote/data.bin"],
            callback=PreludeCallback(),
            batch_size=1,
        )
    )
    await prelude_started.wait()
    operation_task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await operation_task
        child_done_when_operation_returned = child_done.is_set()
    finally:
        release_prelude.set()
        await child_done.wait()
        await fs.aclose()

    assert child_done_when_operation_returned


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
    await asyncio.gather(*handler_tasks, return_exceptions=True)
    requests_after_release = len(transport_requests)
    await fs.aclose()

    assert finished_when_cancel_returned
    assert handler_done_when_cancel_returned
    assert requests_after_release == requests_when_cancel_returned


@pytest.mark.parametrize("termination", ["cancellation", "error"])
@pytest.mark.parametrize("operation", ["pipe", "put"])
async def test_bounded_coordinator_owns_every_created_upload_awaitable(
    operation: str,
    termination: str,
    tmp_path: Path,
) -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    sim = VOSpaceSim().add_container("/bounded")
    sim.install(router)
    transfer_started = asyncio.Event()
    release_transfer = asyncio.Event()
    transfer_finished = asyncio.Event()
    transport_requests: list[tuple[str, str]] = []

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        transport_requests.append((request.method, str(request.url)))
        if request.method == "POST" and str(request.url) == SYNC_URL:
            transfer_started.set()
            if termination == "error":
                return httpx.Response(500, text="forced transfer failure")
            try:
                await release_transfer.wait()
            finally:
                transfer_finished.set()
            raise asyncio.CancelledError
        return await router.async_handler(request)

    fs = VOSpaceFileSystem(
        BASE_URL,
        transport=httpx.MockTransport(transport_handler),
        asynchronous=True,
        skip_instance_cache=True,
    )
    remote_paths = [f"/bounded/{name}.bin" for name in ("a", "b", "c")]
    callback = Callback()
    coordinated = _three_file_upload(
        fs,
        operation,
        tmp_path,
        remote_paths,
        callback,
    )

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", RuntimeWarning)
        operation_task = asyncio.create_task(coordinated)
        await asyncio.wait_for(transfer_started.wait(), timeout=1)
        await _await_upload_termination(operation_task, termination)
        started_transfers = sum(
            method == "POST" and url == SYNC_URL for method, url in transport_requests
        )

        requests_when_operation_returned = len(transport_requests)
        release_transfer.set()
        if termination == "cancellation":
            await transfer_finished.wait()
        del coordinated, operation_task
        gc.collect()
        leaked_awaitables = [
            str(warning.message)
            for warning in caught_warnings
            if "was never awaited" in str(warning.message)
        ]

    requests_after_release = len(transport_requests)
    await fs.aclose()

    assert started_transfers == 1
    assert requests_after_release == requests_when_operation_returned
    assert leaked_awaitables == []
    if operation == "put":
        assert callback.size == 3
        assert callback.value == 1


async def test_transfer_failure_wins_cleanup_cancellation(
    tmp_path: Path,
) -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    sim = VOSpaceSim().add_container("/race")
    sim.install(router)
    second_transfer_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    transfer_count = 0

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal transfer_count
        if request.method == "POST" and str(request.url) == SYNC_URL:
            transfer_count += 1
            if transfer_count == 1:
                await second_transfer_started.wait()
                return httpx.Response(500, text="forced transfer failure")
            second_transfer_started.set()
            while not release_cleanup.is_set():
                try:
                    await release_cleanup.wait()
                except asyncio.CancelledError:  # noqa: PERF203 - cancellation barrier
                    cleanup_started.set()
            raise asyncio.CancelledError
        return await router.async_handler(request)

    fs = VOSpaceFileSystem(
        BASE_URL,
        transport=httpx.MockTransport(transport_handler),
        asynchronous=True,
        skip_instance_cache=True,
    )
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    callback = Callback()
    operation_task = asyncio.create_task(
        fs._put(
            [str(first), str(second)],
            ["/race/first.bin", "/race/second.bin"],
            batch_size=2,
            callback=callback,
        )
    )
    await cleanup_started.wait()
    operation_task.cancel("cleanup cancellation")
    release_cleanup.set()

    with pytest.raises(VOSpaceError, match="forced transfer failure"):
        await operation_task

    await fs.aclose()
    if hasattr(operation_task, "cancelling"):
        assert operation_task.cancelling() == 0
    assert callback.value == 2


async def test_batch_one_does_not_fan_out_tasks_for_unstarted_items() -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    sim = VOSpaceSim().add_container("/fanout")
    sim.install(router)
    transfer_started = asyncio.Event()
    transfer_finished = asyncio.Event()

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url) == SYNC_URL:
            transfer_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                transfer_finished.set()
        return await router.async_handler(request)

    fs = VOSpaceFileSystem(
        BASE_URL,
        transport=httpx.MockTransport(transport_handler),
        asynchronous=True,
        skip_instance_cache=True,
    )
    paths = {f"/fanout/{index}.bin": b"data" for index in range(2000)}
    tasks_before_upload = asyncio.all_tasks()
    operation_task = asyncio.create_task(fs._pipe(paths, batch_size=1))
    await asyncio.wait_for(transfer_started.wait(), timeout=1)
    live_upload_tasks = {
        task
        for task in asyncio.all_tasks()
        if task not in tasks_before_upload and not task.done()
    }
    operation_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation_task
    await transfer_finished.wait()
    await fs.aclose()

    assert len(live_upload_tasks) <= 3


async def test_completed_bulk_tasks_are_released_at_120k_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    fs = make_fs(router, asynchronous=True)
    item_count = 120_000
    started = 0
    first_task: weakref.ReferenceType[asyncio.Task[None]] | None = None
    state: _WriteParentState | None = None
    last_started = asyncio.Event()
    release_last = asyncio.Event()

    async def pipe_file(
        _path: str,
        _value: bytes,
        **_kwargs: Any,
    ) -> None:
        nonlocal first_task, started, state
        state = _write_coordination.current(fs)
        assert state is not None
        task = asyncio.current_task()
        assert task is not None
        if started == 0:
            first_task = weakref.ref(task)
        started += 1
        if started == item_count:
            last_started.set()
            await release_last.wait()

    monkeypatch.setattr(fs, "_pipe_file", pipe_file)
    paths = dict.fromkeys((f"/bulk/{index}" for index in range(item_count)), b"")
    operation_task = asyncio.create_task(fs._pipe(paths, batch_size=1))
    try:
        await asyncio.wait_for(last_started.wait(), timeout=60)
        gc.collect()
        assert first_task is not None
        assert first_task() is None
        assert state is not None
        assert len(state.tasks) <= 1
    finally:
        release_last.set()
        await operation_task
        await fs.aclose()


@pytest.mark.parametrize("operation", ["pipe", "put"])
async def test_bounded_coordinator_preserves_limit_results_mode_and_callback(
    operation: str,
    tmp_path: Path,
) -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    sim = VOSpaceSim().add_container("/bounded")
    sim.install(router)
    first_transfer_started = asyncio.Event()
    release_transfers = asyncio.Event()
    active_transfers = 0
    max_active_transfers = 0
    configured_batch_size = 1 if operation == "pipe" else 2
    requested_batch_size = 0 if operation == "pipe" else 1

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_transfers, max_active_transfers
        if request.method == "POST" and str(request.url) == SYNC_URL:
            active_transfers += 1
            max_active_transfers = max(max_active_transfers, active_transfers)
            try:
                if active_transfers == 1:
                    await asyncio.sleep(0)
                    first_transfer_started.set()
                await release_transfers.wait()
                return await router.async_handler(request)
            finally:
                active_transfers -= 1
        return await router.async_handler(request)

    fs = VOSpaceFileSystem(
        BASE_URL,
        transport=httpx.MockTransport(transport_handler),
        asynchronous=True,
        skip_instance_cache=True,
        batch_size=configured_batch_size,
    )
    names = ("a", "b", "c")
    remote_paths = [f"/bounded/{name}.bin" for name in names]
    values = [name.encode() for name in names]
    callback = Callback()
    if operation == "pipe":
        coordinated = fs._pipe(
            dict(zip(remote_paths, values, strict=True)),
            batch_size=requested_batch_size,
            mode="create",
        )
    else:
        local_paths = []
        for name, value in zip(names, values, strict=True):
            local_path = tmp_path / f"{name}.bin"
            local_path.write_bytes(value)
            local_paths.append(str(local_path))
        coordinated = fs._put(
            local_paths,
            remote_paths,
            batch_size=requested_batch_size,
            callback=callback,
            mode="create",
        )

    operation_task = asyncio.create_task(coordinated)
    await asyncio.wait_for(first_transfer_started.wait(), timeout=1)
    release_transfers.set()
    results = await operation_task
    stored_values = [await fs._cat_file(path) for path in remote_paths]
    await fs.aclose()

    assert max_active_transfers == 1
    assert results == [None, None, None]
    assert stored_values == values
    if operation == "put":
        assert callback.size == 3
        assert callback.value == 3


@pytest.mark.parametrize("operation", ["pipe", "put"])
async def test_bounded_coordinator_preserves_invalid_batch_error(
    operation: str,
    tmp_path: Path,
) -> None:
    router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
    sim = VOSpaceSim().add_container("/bounded")
    sim.install(router)
    fs = make_fs(router, asynchronous=True)
    remote_path = "/bounded/a.bin"
    if operation == "pipe":
        coordinated = fs._pipe({remote_path: b"data"}, batch_size=-2)
    else:
        local_path = tmp_path / "a.bin"
        local_path.write_bytes(b"data")
        coordinated = fs._put([str(local_path)], [remote_path], batch_size=-2)

    with pytest.raises(ValueError, match=r"^$"):
        await coordinated

    await fs.aclose()
    assert not router.calls


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
