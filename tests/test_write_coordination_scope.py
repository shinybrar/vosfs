"""Direct contracts for the ambient write-coordination scope."""

from __future__ import annotations

import asyncio
import builtins
import gc
import sys
from typing import TYPE_CHECKING, Literal

import pytest

from vosfs import _write_coordination

if TYPE_CHECKING:
    from vosfs._write_coordination import _WriteParentState


def _assert_cancellation_reason(
    error: asyncio.CancelledError,
    expected: str,
) -> None:
    """Check reasons where ``Task`` preserves them across an await boundary."""
    if sys.version_info >= (3, 11):
        assert error.args == (expected,)


async def _joined_descendant(
    owner: object,
    expected_state: _WriteParentState,
    joined: asyncio.Event,
    cleanup_started: asyncio.Event,
    release_cleanup: asyncio.Event,
) -> None:
    assert _write_coordination.current(owner) is expected_state
    joined.set()
    try:
        await asyncio.Event().wait()
    finally:
        cleanup_started.set()
        await release_cleanup.wait()


async def _run_scope_case(
    outcome: Literal["success", "failure", "initial-cancellation"],
    *,
    cancel_during_cleanup: bool,
    cancellation_counts: list[int] | None = None,
) -> None:
    owner = object()
    joined = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    if not cancel_during_cleanup:
        release_cleanup.set()

    async def operation() -> None:
        try:
            async with _write_coordination.scope(owner):
                state = _write_coordination.current(owner)
                assert state is not None
                descendant_task = asyncio.create_task(
                    _joined_descendant(
                        owner,
                        state,
                        joined,
                        cleanup_started,
                        release_cleanup,
                    )
                )
                await joined.wait()
                assert not descendant_task.done()
                if outcome == "failure":
                    msg = "body failure"
                    raise ValueError(msg)
                if outcome == "initial-cancellation":
                    task = asyncio.current_task()
                    assert task is not None
                    task.cancel("initial cancellation")
                    await asyncio.sleep(0)
        finally:
            if cancellation_counts is not None:
                task = asyncio.current_task()
                assert task is not None
                cancellation_counts.append(task.cancelling())

    operation_task = asyncio.create_task(operation())

    async def cancel_cleanup() -> None:
        await cleanup_started.wait()
        operation_task.cancel("cleanup cancellation")
        release_cleanup.set()

    cleanup_canceller = (
        asyncio.create_task(cancel_cleanup()) if cancel_during_cleanup else None
    )
    try:
        await operation_task
    finally:
        if cleanup_canceller is not None:
            await cleanup_canceller


async def test_scope_completes_after_successful_body_and_cleanup() -> None:
    await _run_scope_case("success", cancel_during_cleanup=False)


async def test_scope_raises_cancellation_received_during_successful_cleanup() -> None:
    with pytest.raises(asyncio.CancelledError) as error:
        await _run_scope_case("success", cancel_during_cleanup=True)

    _assert_cancellation_reason(error.value, "cleanup cancellation")


async def test_scope_raises_body_failure_after_cleanup() -> None:
    with pytest.raises(ValueError, match="body failure"):
        await _run_scope_case("failure", cancel_during_cleanup=False)


async def test_scope_preserves_body_failure_when_cleanup_is_cancelled() -> None:
    with pytest.raises(ValueError, match="body failure"):
        await _run_scope_case("failure", cancel_during_cleanup=True)


async def test_scope_raises_initial_cancellation_after_cleanup() -> None:
    with pytest.raises(asyncio.CancelledError) as error:
        await _run_scope_case("initial-cancellation", cancel_during_cleanup=False)

    _assert_cancellation_reason(error.value, "initial cancellation")


async def test_scope_preserves_initial_cancellation_when_cleanup_is_cancelled() -> None:
    with pytest.raises(asyncio.CancelledError) as error:
        await _run_scope_case("initial-cancellation", cancel_during_cleanup=True)

    _assert_cancellation_reason(error.value, "initial cancellation")


@pytest.mark.skipif(
    not hasattr(asyncio.Task, "uncancel"),
    reason="cancellation-count restoration requires Python 3.11 or newer",
)
@pytest.mark.parametrize(
    ("outcome", "expected_reason", "expected_count"),
    [
        ("success", "cleanup cancellation", 0),
        ("initial-cancellation", "initial cancellation", 1),
    ],
)
async def test_cleanup_cancellation_restores_only_intercepted_count(
    outcome: Literal["success", "initial-cancellation"],
    expected_reason: str,
    expected_count: int,
) -> None:
    cancellation_counts: list[int] = []

    with pytest.raises(asyncio.CancelledError) as error:
        await _run_scope_case(
            outcome,
            cancel_during_cleanup=True,
            cancellation_counts=cancellation_counts,
        )

    _assert_cancellation_reason(error.value, expected_reason)
    assert cancellation_counts == [expected_count]


@pytest.mark.skipif(
    not hasattr(asyncio.Task, "uncancel") or not hasattr(asyncio, "TaskGroup"),
    reason="structured cancellation accounting requires Python 3.11 or newer",
)
async def test_body_failure_cleanup_cancellation_does_not_poison_caller() -> None:
    owner = object()
    joined = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    caller = asyncio.current_task()
    assert caller is not None
    baseline = caller.cancelling()

    async def cancel_cleanup() -> None:
        await cleanup_started.wait()
        caller.cancel("cleanup cancellation")
        release_cleanup.set()

    async def fail_in_later_task_group() -> None:
        await asyncio.sleep(0)
        msg = "later task group failure"
        raise RuntimeError(msg)

    descendant_tasks: list[asyncio.Task[None]] = []

    async def fail_body() -> None:
        async with _write_coordination.scope(owner):
            state = _write_coordination.current(owner)
            assert state is not None
            descendant_tasks.append(
                asyncio.create_task(
                    _joined_descendant(
                        owner,
                        state,
                        joined,
                        cleanup_started,
                        release_cleanup,
                    )
                )
            )
            await joined.wait()
            msg = "body failure"
            raise ValueError(msg)

    cleanup_canceller = asyncio.create_task(cancel_cleanup())
    try:
        with pytest.raises(ValueError, match="body failure"):
            await fail_body()

        assert descendant_tasks[0].done()
        assert caller.cancelling() == baseline
        exception_group = getattr(builtins, "ExceptionGroup", Exception)
        with pytest.raises(
            exception_group,
            match="unhandled errors in a TaskGroup",
        ) as task_group_error:
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(fail_in_later_task_group())
        assert "later task group failure" in repr(task_group_error.value)
    finally:
        await cleanup_canceller
        while caller.cancelling() > baseline:
            caller.uncancel()


async def test_scope_retrieves_failures_from_done_descendants_on_cancellation() -> None:
    owner = object()
    owner_task = asyncio.current_task()
    assert owner_task is not None
    descendants_done = asyncio.Event()
    never = asyncio.Event()
    completed = 0
    descendant_tasks: list[asyncio.Task[None]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    exception_contexts: list[dict[str, object]] = []

    def capture_exception(
        _loop: asyncio.AbstractEventLoop,
        context: dict[str, object],
    ) -> None:
        exception_contexts.append(context)

    async def fail_after_joining() -> None:
        nonlocal completed
        assert _write_coordination.current(owner) is not None
        completed += 1
        if completed == 2:
            descendants_done.set()
        msg = "joined descendant failure"
        raise RuntimeError(msg)

    async def cancel_owner() -> None:
        await descendants_done.wait()
        owner_task.cancel("body cancellation")

    async def cancel_scope_with_failed_descendants() -> None:
        async with _write_coordination.scope(owner):
            descendant_tasks.extend(
                asyncio.create_task(fail_after_joining()) for _ in range(2)
            )
            await never.wait()

    loop.set_exception_handler(capture_exception)
    cancel_task = asyncio.create_task(cancel_owner())
    try:
        with pytest.raises(asyncio.CancelledError):
            await cancel_scope_with_failed_descendants()
        await cancel_task
        descendant_tasks.clear()
        gc.collect()
    finally:
        loop.set_exception_handler(previous_handler)

    unretrieved = [
        context
        for context in exception_contexts
        if context.get("message") == "Task exception was never retrieved"
    ]
    assert unretrieved == []


async def test_scope_owner_and_descendants_share_state_without_owner_deadlock() -> None:
    owner = object()
    descendant_joined = asyncio.Event()
    release_descendant = asyncio.Event()
    descendant_state: list[_WriteParentState | None] = []
    owner_was_registered = False
    owner_state: _WriteParentState | None = None

    async def descendant() -> None:
        descendant_state.append(_write_coordination.current(owner))
        descendant_joined.set()
        await release_descendant.wait()

    async with _write_coordination.scope(owner):
        owner_state = _write_coordination.current(owner)
        assert owner_state is not None
        owner_task = asyncio.current_task()
        assert owner_task is not None
        owner_was_registered = owner_task in owner_state.tasks
        # Keep the failing implementation from deadlocking its own test cleanup.
        owner_state.tasks.discard(owner_task)
        descendant_task = asyncio.create_task(descendant())
        await descendant_joined.wait()

    assert descendant_state == [owner_state]
    assert descendant_task.done()
    assert not owner_was_registered
