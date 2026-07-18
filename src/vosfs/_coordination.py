"""Cancellation-safe ambient coordination for fsspec bulk operations."""

from __future__ import annotations

import asyncio
import contextvars
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_State = TypeVar("_State")


class _Operation:
    """One owner-bound ambient operation and every child task that joins it."""

    def __init__(self, owner: object, state: object) -> None:
        self.owner = owner
        self.state = state
        self.active = True
        self.tasks: set[asyncio.Task[object]] = set()


_CURRENT: contextvars.ContextVar[_Operation | None] = contextvars.ContextVar(
    "vosfs_coordinated_operation",
    default=None,
)


@asynccontextmanager
async def scope(owner: object, state: _State) -> AsyncIterator[_State]:
    """Bind ``state`` to ``owner`` and quiesce all joined tasks before exit.

    Once exit starts, copied same-owner contexts expire before further I/O.
    Cleanup runs in its own task and later cancellation requests are consumed
    only until every joined child is canceled and awaited. The exception that
    entered cleanup remains authoritative; a first cancellation received during
    otherwise successful cleanup is re-raised after quiescence.
    """
    operation = _Operation(owner, state)
    token = _CURRENT.set(operation)
    body_error: BaseException | None = None
    cleanup_cancel: asyncio.CancelledError | None = None
    try:
        try:
            yield state
        except BaseException as exc:
            body_error = exc
            raise
    finally:
        operation.active = False
        try:
            cleanup_cancel = await _finish_uninterruptibly(operation)
        finally:
            _CURRENT.reset(token)
        if cleanup_cancel is not None and not isinstance(
            body_error, asyncio.CancelledError
        ):
            raise cleanup_cancel


def current(owner: object, state_type: type[_State]) -> _State | None:
    """Return the active state for ``owner`` and register the calling task."""
    operation = _CURRENT.get()
    if operation is None or operation.owner is not owner:
        return None
    task = asyncio.current_task()
    if task is not None:
        operation.tasks.add(task)
    if not operation.active:
        raise asyncio.CancelledError
    if not isinstance(operation.state, state_type):
        msg = "coordinated operation state has an unexpected type"
        raise TypeError(msg)
    return operation.state


async def _finish_uninterruptibly(
    operation: _Operation,
) -> asyncio.CancelledError | None:
    """Drain in a dedicated task despite any later cancellation requests."""
    cleanup = asyncio.create_task(_drain(operation))
    interrupted: asyncio.CancelledError | None = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:  # noqa: PERF203 - intentional drain loop
            if interrupted is None:
                interrupted = exc
    cleanup.result()
    return interrupted


async def _drain(operation: _Operation) -> None:
    """Cancel and await every joined child, including tasks joining on expiry."""
    current_task = asyncio.current_task()
    observed = -1
    while observed != len(operation.tasks):
        observed = len(operation.tasks)
        await asyncio.sleep(0)
        pending = [
            task
            for task in operation.tasks
            if task is not current_task and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
