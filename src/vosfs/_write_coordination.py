"""Cancellation-safe coordination for one fsspec bulk write."""

from __future__ import annotations

import asyncio
import contextvars
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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
async def scope(owner: object) -> AsyncIterator[None]:
    """Coordinate one owner's bulk write and quiesce its joined descendants."""
    state = _WriteParentState(owner, asyncio.current_task())
    token = _CURRENT.set(state)
    body_error: BaseException | None = None
    cleanup_cancel: asyncio.CancelledError | None = None
    try:
        try:
            yield
        except BaseException as exc:
            body_error = exc
            raise
    finally:
        state.active = False
        try:
            cleanup_cancel = await _finish_uninterruptibly(state)
        finally:
            _CURRENT.reset(token)
        if cleanup_cancel is not None and body_error is None:
            raise cleanup_cancel


def current(owner: object) -> _WriteParentState | None:
    """Return the owner's ambient write state and join descendant callers."""
    state = _CURRENT.get()
    if state is None or state.owner is not owner:
        return None
    task = asyncio.current_task()
    if task is not None and task is not state.owner_task:
        state.tasks.add(task)
    if not state.active:
        raise asyncio.CancelledError
    return state


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
        pending = [task for task in state.tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
