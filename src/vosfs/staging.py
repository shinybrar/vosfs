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
    from collections.abc import Callable, Iterator
    from io import BufferedRandom, BufferedReader
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


class StagedWriteFile:
    """A writable buffer that uploads once, only on a successful close.

    Writes accumulate in a disk-backed temporary file. On a clean close the
    ``on_commit`` callback uploads it; if the context block raises, the buffer
    is discarded without any upload, so a failed write never issues a PUT.
    """

    def __init__(self, on_commit: Callable[[str], None]) -> None:
        """Open a read/write temporary buffer; ``on_commit`` receives its path."""
        self._path = new_temp_path()
        # ``w+b`` so consumers that seek back and read (zip and archive writers)
        # work; the buffer is still uploaded once, on a clean close.
        self._file: BufferedRandom = Path(self._path).open("w+b")  # noqa: SIM115 - closed here
        self._on_commit = on_commit
        self._done = False

    def write(self, data: bytes) -> int:
        """Buffer ``data`` locally, returning the number of bytes written."""
        return self._file.write(data)

    def read(self, size: int = -1) -> bytes:
        """Read from the local buffer (present for archive writers that seek back)."""
        return self._file.read(size)

    def tell(self) -> int:
        """Return the current buffer position."""
        return self._file.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek within the local buffer."""
        return self._file.seek(offset, whence)

    def flush(self) -> None:
        """Flush the local buffer."""
        self._file.flush()

    def writable(self) -> bool:
        """Return that the buffer supports writing."""
        return True

    def readable(self) -> bool:
        """Return that the buffer supports reading."""
        return True

    def seekable(self) -> bool:
        """Return that the local buffer supports seeking."""
        return True

    @property
    def closed(self) -> bool:
        """Whether the buffer has been finalized."""
        return self._done

    def close(self) -> None:
        """Commit the buffer with one upload, then remove the temporary file."""
        if self._done:
            return
        self._done = True
        self._file.close()
        try:
            self._on_commit(self._path)
        finally:
            with contextlib.suppress(OSError):
                Path(self._path).unlink()

    def discard(self) -> None:
        """Discard the buffer without uploading (used on a failed write)."""
        if self._done:
            return
        self._done = True
        self._file.close()
        with contextlib.suppress(OSError):
            Path(self._path).unlink()

    def __enter__(self) -> StagedWriteFile:  # noqa: PYI034 - concrete return for 3.10 compatibility
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit on a clean exit; discard when the block raised."""
        if exc_type is None:
            self.close()
        else:
            self.discard()
