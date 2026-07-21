"""Tested-source matrix for the opt-in ``sign`` extension."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import pytest
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem
from fsspec_cli import App
from fsspec_cli.extensions import sign
from typer.testing import CliRunner

from vosfs import VOSpaceFileSystem

from ._matrix_support import _block_network

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fsspec_cli import AsyncFilesystemSource


def _assert_missing_capability(
    source_name: str,
    source: AsyncFilesystemSource,
) -> None:
    result = CliRunner().invoke(
        App({source_name: source}, extensions=[sign]).typer_app,
        ["sign", f"{source_name}:/report.csv"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"sign: {source_name}:/report.csv: unsupported operation\n",
    )


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


def test_adapted_local_sign_profile_detects_missing_capability() -> None:
    @asynccontextmanager
    async def source() -> AsyncIterator[AsyncFileSystemWrapper]:
        yield AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )

    _assert_missing_capability("local", source)


def test_adapted_memory_sign_profile_detects_missing_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    @asynccontextmanager
    async def source() -> AsyncIterator[AsyncFileSystemWrapper]:
        yield AsyncFileSystemWrapper(
            MemoryFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )

    _assert_missing_capability("memory", source)


def test_native_vosfs_sign_profile_detects_missing_capability() -> None:
    requests: list[httpx.Request] = []

    def reject_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        message = "sign capability detection must not probe the backend"
        raise AssertionError(message)

    @asynccontextmanager
    async def source() -> AsyncIterator[VOSpaceFileSystem]:
        filesystem = VOSpaceFileSystem(
            "https://example.test/arc",
            transport=httpx.MockTransport(reject_request),
            asynchronous=True,
            skip_instance_cache=True,
            trust_env=False,
        )
        try:
            yield filesystem
        finally:
            await filesystem.aclose()

    _assert_missing_capability("vos", source)
    assert requests == []
