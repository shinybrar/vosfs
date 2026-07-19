"""Focused contracts for repository-owned bulk scheduling and batch policy."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from vosfs import _bulk


@pytest.mark.parametrize(
    ("requested", "configured", "expected"),
    [
        (3, 7, 3),
        (0, 7, 7),
        (None, 7, 7),
        (-1, 7, None),
    ],
)
def test_batch_policy_preserves_requested_and_configured_precedence(
    requested: int | None,
    configured: int | None,
    expected: int | None,
) -> None:
    assert _bulk.resolve_batch_size(requested, configured, nofiles=False) == expected


def test_batch_policy_uses_supported_fsspec_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_bulk.conf, "gather_batch_size", 5)
    monkeypatch.setitem(_bulk.conf, "nofiles_gather_batch_size", 9)

    assert _bulk.resolve_batch_size(None, None, nofiles=False) == 5
    assert _bulk.resolve_batch_size(None, None, nofiles=True) == 9


@pytest.mark.parametrize(("nofiles", "expected"), [(False, 128), (True, 1280)])
def test_batch_policy_uses_cross_platform_defaults_without_resource(
    monkeypatch: pytest.MonkeyPatch,
    nofiles: bool,
    expected: int,
) -> None:
    monkeypatch.delitem(_bulk.conf, "gather_batch_size", raising=False)
    monkeypatch.delitem(_bulk.conf, "nofiles_gather_batch_size", raising=False)
    monkeypatch.setattr(_bulk, "resource", None)

    assert _bulk.resolve_batch_size(None, None, nofiles=nofiles) == expected


def test_batch_policy_uses_posix_file_descriptor_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(_bulk.conf, "gather_batch_size", raising=False)
    fake_resource = SimpleNamespace(
        RLIMIT_NOFILE=1,
        RLIM_INFINITY=-1,
        getrlimit=lambda _limit: (800, 1600),
    )
    monkeypatch.setattr(_bulk, "resource", fake_resource)

    assert _bulk.resolve_batch_size(None, None, nofiles=False) == 100

    fake_resource.getrlimit = lambda _limit: (-1, -1)
    assert _bulk.resolve_batch_size(None, None, nofiles=False) is None


@pytest.mark.parametrize("invalid", [-2, 0])
def test_batch_policy_rejects_invalid_effective_size(invalid: int) -> None:
    with pytest.raises(ValueError, match=r"^$"):
        _bulk.resolve_batch_size(None, invalid, nofiles=False)


async def test_factory_runner_is_lazy_bounded_and_result_ordered() -> None:
    started: list[int] = []
    release = [asyncio.Event() for _ in range(3)]
    first_batch_started = asyncio.Event()
    third_started = asyncio.Event()
    successes = 0

    async def item(index: int) -> int:
        started.append(index)
        if len(started) == 2:
            first_batch_started.set()
        if index == 2:
            third_started.set()
        await release[index].wait()
        return index

    def record_success() -> None:
        nonlocal successes
        successes += 1

    factories = [lambda index=index: item(index) for index in range(3)]
    runner = asyncio.create_task(
        _bulk.run_factories(
            factories,
            max_concurrency=2,
            on_success=record_success,
        )
    )
    await first_batch_started.wait()
    assert started == [0, 1]
    release[1].set()
    await third_started.wait()
    release[0].set()
    release[2].set()

    assert await runner == [0, 1, 2]
    assert successes == 3


async def test_factory_runner_prefers_lowest_coincident_failure_index() -> None:
    async def fail(index: int) -> None:
        msg = f"failure {index}"
        raise RuntimeError(msg)

    factories = [lambda index=index: fail(index) for index in range(2)]

    with pytest.raises(RuntimeError, match="failure 0"):
        await _bulk.run_factories(factories, max_concurrency=2)
