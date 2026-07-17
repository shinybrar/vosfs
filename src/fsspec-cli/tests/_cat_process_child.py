"""Subprocess fixture for public-seam binary ``cat`` output tests."""

from __future__ import annotations

import io
import sys
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _real_stdout() -> BinaryIO:
    stream = sys.__stdout__
    if stream is None or not hasattr(stream, "buffer"):
        msg = "the subprocess requires a real binary stdout stream"
        raise RuntimeError(msg)
    return stream.buffer


class _FileSystem(AsyncFileSystem):
    def __init__(self, mode: str) -> None:
        super().__init__(asynchronous=True)
        self._mode = mode

    async def _info(self, path: str, **kwargs: object) -> dict[str, str]:
        del kwargs
        if path == "/missing":
            raise FileNotFoundError(path)
        return {"type": "file"}

    async def _get_file(self, rpath: str, lpath: str, **kwargs: object) -> None:
        del kwargs
        payloads = {
            "/empty": b"",
            "/bytes": bytes(range(256)),
            "/prefix": b"abcdefghij",
            "/docs": b"payload",
        }
        with Path(lpath).open("wb") as handle:  # noqa: ASYNC230
            handle.write(payloads.get(rpath, b"payload"))


@asynccontextmanager
async def _source(mode: str) -> AsyncIterator[_FileSystem]:
    yield _FileSystem(mode)


class _PrefixThenFailure(io.RawIOBase):
    def __init__(self, accepted_bytes: int) -> None:
        self._remaining = accepted_bytes

    def writable(self) -> bool:
        return True

    def write(self, value: bytes | bytearray | memoryview) -> int:  # type: ignore[override]
        data = bytes(value)
        accepted = min(self._remaining, len(data))
        if accepted:
            stream = _real_stdout()
            stream.write(data[:accepted])
            stream.flush()
            self._remaining -= accepted
        if accepted != len(data):
            message = "disk\\bad\nline"
            raise OSError(message)
        return len(data)

    def flush(self) -> None:
        _real_stdout().flush()


def _configure_stdout(mode: str) -> None:
    if mode == "fail":
        sys.stdout = io.TextIOWrapper(_PrefixThenFailure(0), write_through=True)
    elif mode == "prefix":
        sys.stdout = io.TextIOWrapper(_PrefixThenFailure(3), write_through=True)
    elif mode == "runtime-and-fail":
        sys.stdout = io.TextIOWrapper(_PrefixThenFailure(0), write_through=True)
    elif mode not in {"normal", "bytes", "empty"}:
        msg = f"unknown child mode: {mode}"
        raise RuntimeError(msg)


def main() -> None:
    mode = sys.argv.pop(1)
    _configure_stdout(mode)
    App({"memory": partial(_source, mode)}).typer_app()


if __name__ == "__main__":
    main()
