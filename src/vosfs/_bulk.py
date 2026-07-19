"""Lazy, cancellation-safe scheduling for bulk filesystem writes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar, cast

from fsspec.config import conf

if TYPE_CHECKING:
    import resource
    from collections.abc import Callable, Coroutine, Sequence
    from typing import Any

    ResourceError = resource.error
else:
    try:
        import resource
    except ImportError:  # pragma: no cover - exercised on non-POSIX Python
        resource = None
        ResourceError = OSError
    else:
        ResourceError = getattr(resource, "error", OSError)

_DEFAULT_BATCH_SIZE = 128
_NOFILES_DEFAULT_BATCH_SIZE = 1280
_Result = TypeVar("_Result")


def resolve_batch_size(
    requested: int | None,
    configured: int | None,
    *,
    nofiles: bool,
) -> int | None:
    """Resolve fsspec-compatible batch policy without importing private APIs.

    ``None`` means unbounded. Configuration keys, resource-limit behavior, and
    fallback constants match fsspec 2026.6's documented bulk-operation behavior.
    """
    effective = requested or configured
    if effective is None:
        effective = _configured_or_default_batch_size(nofiles=nofiles)
    if effective == -1:
        return None
    if effective <= 0:
        raise ValueError
    return effective


def _configured_or_default_batch_size(*, nofiles: bool) -> int:
    key = "nofiles_gather_batch_size" if nofiles else "gather_batch_size"
    if key in conf:
        return cast("int", conf[key])
    if nofiles:
        return _NOFILES_DEFAULT_BATCH_SIZE
    if resource is None:
        return _DEFAULT_BATCH_SIZE
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, ValueError, ResourceError):
        return _DEFAULT_BATCH_SIZE
    if soft_limit == resource.RLIM_INFINITY:
        return -1
    return soft_limit // 8


async def run_factories(
    factories: Sequence[Callable[[], Coroutine[Any, Any, _Result]]],
    *,
    max_concurrency: int | None,
    on_success: Callable[[], object] | None = None,
) -> list[_Result]:
    """Run lazy factories with bounded fanout and ordered results."""
    if not factories:
        return []
    limit = len(factories) if max_concurrency is None else max_concurrency
    results: list[_Result | None] = [None] * len(factories)
    created: list[asyncio.Task[_Result]] = []
    running: dict[asyncio.Task[_Result], int] = {}
    next_index = 0

    def schedule_one() -> None:
        nonlocal next_index
        task = asyncio.create_task(factories[next_index]())
        created.append(task)
        running[task] = next_index
        next_index += 1

    for _ in range(min(limit, len(factories))):
        schedule_one()

    try:
        while running:
            done, _ = await asyncio.wait(
                running,
                return_when=asyncio.FIRST_COMPLETED,
            )
            failures = _collect_completed(done, running, results, on_success)
            if failures:
                failures.sort(key=lambda failure: failure[0])
                raise failures[0][1]  # noqa: TRY301 - outer guard owns cleanup
            while len(running) < limit and next_index < len(factories):
                schedule_one()
    except BaseException:
        await _cancel_and_gather(created)
        raise

    return cast("list[_Result]", results)


def _collect_completed(
    done: set[asyncio.Task[_Result]],
    running: dict[asyncio.Task[_Result], int],
    results: list[_Result | None],
    on_success: Callable[[], object] | None,
) -> list[tuple[int, BaseException]]:
    """Retrieve one completed batch and report every failure by input index."""
    failures = []
    for task in sorted(done, key=running.__getitem__):
        index = running.pop(task)
        try:
            results[index] = task.result()
            if on_success is not None:
                on_success()
        except BaseException as exc:  # noqa: BLE001 - cancellation is a result here
            failures.append((index, exc))
    return failures


async def _cancel_and_gather(tasks: Sequence[asyncio.Task[_Result]]) -> None:
    """Cancel incomplete tasks and retrieve every created task result."""
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
