"""Disk-backed staging files for whole-object reads and writes.

OpenCADC Cavern does not implement HTTP byte ranges, so a seekable read is a
whole-object download into a disk-backed temporary file, and a staged write
buffers into a temporary file that is uploaded once on a successful close.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from io import BufferedReader
    from types import TracebackType


def new_temp_path() -> str:
    """Create an empty disk-backed temporary file and return its path."""
    handle, path = tempfile.mkstemp(prefix="vosfs-")
    os.close(handle)
    return path


class StagedReadFile:
    """A seekable read-only view over a downloaded temporary file.

    The temporary file is removed when the view is closed. All read and seek
    operations are local; no network I/O happens after construction.
    """

    def __init__(self, path: str) -> None:
        """Open ``path`` for binary reading; it is unlinked on close."""
        self._path = path
        self._file: BufferedReader = Path(path).open("rb")  # noqa: SIM115 - closed in close()
        self.size = Path(path).stat().st_size

    def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes (all remaining when ``size`` is negative)."""
        return self._file.read(size)

    def read1(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes in a single underlying read."""
        return self._file.read1(size)

    def readinto(self, buffer: bytearray | memoryview) -> int | None:
        """Read bytes into a pre-allocated buffer, returning the count."""
        return self._file.readinto(buffer)  # type: ignore[attr-defined]

    def readline(self, size: int = -1) -> bytes:
        """Read and return one line, up to ``size`` bytes."""
        return self._file.readline(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to ``offset`` relative to ``whence`` (0/1/2)."""
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        """Return the current stream position."""
        return self._file.tell()

    def seekable(self) -> bool:
        """Return that the staged file supports seeking."""
        return True

    def readable(self) -> bool:
        """Return that the staged file supports reading."""
        return True

    def writable(self) -> bool:
        """Return that the staged read file cannot be written."""
        return False

    def flush(self) -> None:
        """No-op flush, present for file-object compatibility."""

    def __iter__(self) -> Iterator[bytes]:
        """Iterate over lines of the staged file."""
        return iter(self._file)

    def __next__(self) -> bytes:
        """Return the next line of the staged file."""
        return next(self._file)

    @property
    def closed(self) -> bool:
        """Whether the staged file has been closed."""
        return self._file.closed

    def close(self) -> None:
        """Close the staged file and remove its temporary backing file."""
        if not self._file.closed:
            self._file.close()
        with contextlib.suppress(OSError):
            Path(self._path).unlink()

    def __enter__(self) -> StagedReadFile:  # noqa: PYI034 - concrete return for 3.10 compatibility
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close on context exit."""
        self.close()
