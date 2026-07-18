"""Direct contracts for the ambient write-coordination scope."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

import pytest

from vosfs import _write_coordination

if TYPE_CHECKING:
    from vosfs._write_coordination import _WriteParentState


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
) -> None:
    owner = object()
    joined = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    if not cancel_during_cleanup:
        release_cleanup.set()

    async def operation() -> None:
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

    assert error.value.args == ("cleanup cancellation",)


async def test_scope_raises_body_failure_after_cleanup() -> None:
    with pytest.raises(ValueError, match="body failure"):
        await _run_scope_case("failure", cancel_during_cleanup=False)


async def test_scope_preserves_body_failure_when_cleanup_is_cancelled() -> None:
    with pytest.raises(ValueError, match="body failure"):
        await _run_scope_case("failure", cancel_during_cleanup=True)


async def test_scope_raises_initial_cancellation_after_cleanup() -> None:
    with pytest.raises(asyncio.CancelledError) as error:
        await _run_scope_case("initial-cancellation", cancel_during_cleanup=False)

    assert error.value.args == ("initial cancellation",)


async def test_scope_preserves_initial_cancellation_when_cleanup_is_cancelled() -> None:
    with pytest.raises(asyncio.CancelledError) as error:
        await _run_scope_case("initial-cancellation", cancel_during_cleanup=True)

    assert error.value.args == ("initial cancellation",)


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
