"""Cancellation-safe coordination for one fsspec bulk write."""

from __future__ import annotations

import asyncio
import contextvars
from contextlib import asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _TaskWithUncancel(Protocol):
    """Cancellation counting plus ``uncancel()``, available on Python 3.11+."""

    def cancelling(self) -> int: ...

    def uncancel(self) -> int: ...


class _WriteParentState:
    """One owner-bound write and its remote-parent materialization ledger."""

    def __init__(
        self,
        owner: object,
        owner_task: asyncio.Task[object] | None,
    ) -> None:
        self.owner = owner
        self.owner_task = owner_task
        self.active = True
        self.tasks: set[asyncio.Task[object]] = set()
        self.lock = asyncio.Lock()
        self.materialized: set[str] = set()
        self.failure: Exception | None = None


_CURRENT: contextvars.ContextVar[_WriteParentState | None] = contextvars.ContextVar(
    "vosfs_coordinated_write",
    default=None,
)


@asynccontextmanager
async def scope(
    owner: object,
) -> AsyncIterator[None]:
    """Coordinate one owner's bulk write and quiesce its joined descendants."""
    state = _WriteParentState(owner, asyncio.current_task())
    entry_cancellation_count = _cancellation_count(state.owner_task)
    token = _CURRENT.set(state)
    body_error: BaseException | None = None
    failure_before_cleanup: Exception | None = None
    cleanup_cancel: asyncio.CancelledError | None = None
    try:
        try:
            yield
        except BaseException as exc:
            body_error = exc
            failure_before_cleanup = state.failure
            raise
        else:
            failure_before_cleanup = state.failure
    finally:
        cleanup_cancellation_count = _cancellation_count(state.owner_task)
        state.active = False
        try:
            cleanup_cancel = await _finish_uninterruptibly(state)
        finally:
            recorded_failure_wins = failure_before_cleanup is not None and (
                body_error is None or isinstance(body_error, asyncio.CancelledError)
            )
            restore_count = (
                entry_cancellation_count
                if recorded_failure_wins
                else cleanup_cancellation_count
            )
            _restore_cancellation_count(state.owner_task, restore_count)
            _CURRENT.reset(token)
        if recorded_failure_wins:
            raise failure_before_cleanup
        if cleanup_cancel is not None and body_error is None:
            raise cleanup_cancel


def current(owner: object) -> _WriteParentState | None:
    """Return the owner's ambient write state and join descendant callers."""
    state = _CURRENT.get()
    if state is None or state.owner is not owner:
        return None
    task = asyncio.current_task()
    if task is not None and task is not state.owner_task and task not in state.tasks:
        state.tasks.add(task)
        task.add_done_callback(partial(_retrieve_and_discard, state))
    if not state.active:
        raise asyncio.CancelledError
    return state


def _retrieve_and_discard(
    state: _WriteParentState,
    task: asyncio.Task[object],
) -> None:
    """Retrieve one joined outcome, remember its failure, and release the task."""
    try:
        failure = task.exception()
    except asyncio.CancelledError:
        failure = None
    if isinstance(failure, Exception) and state.failure is None:
        state.failure = failure
    state.tasks.discard(task)


def _cancellation_count(task: asyncio.Task[object] | None) -> int | None:
    """Read the cancellation count when this runtime can later restore it."""
    if task is None or not hasattr(task, "cancelling") or not hasattr(task, "uncancel"):
        return None
    return cast("_TaskWithUncancel", task).cancelling()


def _restore_cancellation_count(
    task: asyncio.Task[object] | None,
    baseline: int | None,
) -> None:
    """Use ``Task.uncancel()`` to remove only intercepted cancellation requests."""
    if task is None or baseline is None:
        return
    task_with_uncancel = cast("_TaskWithUncancel", task)
    while task_with_uncancel.cancelling() > baseline:
        task_with_uncancel.uncancel()


async def _finish_uninterruptibly(
    state: _WriteParentState,
) -> asyncio.CancelledError | None:
    """Drain in a dedicated task despite any later cancellation requests."""
    cleanup = asyncio.create_task(_drain(state))
    interrupted: asyncio.CancelledError | None = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:  # noqa: PERF203 - intentional drain loop
            if interrupted is None:
                interrupted = exc
    cleanup.result()
    return interrupted


async def _drain(state: _WriteParentState) -> None:
    """Cancel and await every joined descendant, including late joiners."""
    observed = -1
    while observed != len(state.tasks):
        observed = len(state.tasks)
        await asyncio.sleep(0)
        registered = list(state.tasks)
        for task in registered:
            if task.done():
                continue
            task.cancel()
        if registered:
            await asyncio.gather(*registered, return_exceptions=True)
