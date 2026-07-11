"""Disk-backed staging files for whole-object reads and writes.

OpenCADC Cavern does not implement HTTP byte ranges, so a seekable read is a
whole-object download into a disk-backed temporary file, and a staged write
buffers into a temporary file that is uploaded once on a successful close.

Both views subclass the standard buffered IO wrappers so they inherit the full
file-object protocol (``read``/``readinto``/``readline``/iteration/``seek``/
``tell`` and, for writes, ``write``) and only add temporary-file cleanup and the
upload-on-clean-close commit behaviour.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


def new_temp_path() -> str:
    """Create an empty disk-backed temporary file and return its path."""
    handle, path = tempfile.mkstemp(prefix="vosfs-")
    os.close(handle)
    return path


class StagedReadFile(io.BufferedReader):
    """A seekable read-only view over a downloaded temporary file.

    Inherits the buffered-reader protocol; the temporary file is removed when
    the view is closed. All read and seek operations are local, so no network
    I/O happens after construction.
    """

    def __init__(self, path: str) -> None:
        """Open ``path`` for binary reading; it is unlinked on close."""
        self._path = path
        super().__init__(io.FileIO(path, "rb"))
        self.size = Path(path).stat().st_size

    def close(self) -> None:
        """Close the staged file and remove its temporary backing file."""
        try:
            super().close()
        finally:
            with contextlib.suppress(OSError):
                Path(self._path).unlink()


class StagedWriteFile(io.BufferedRandom):
    """A writable buffer that uploads once, only on a successful close.

    Writes accumulate in a disk-backed temporary file. On a clean ``close`` (or
    a clean ``with`` exit) the ``on_commit`` callback uploads it; if the context
    block raises, or the object is merely discarded or garbage-collected, the
    buffer is dropped without any upload, so a failed write never issues a PUT.
    ``r+b`` backing lets consumers that seek back and read (zip and archive
    writers) work while still uploading exactly once.
    """

    def __init__(self, on_commit: Callable[[str], None]) -> None:
        """Open a read/write temporary buffer; ``on_commit`` receives its path."""
        self._path = new_temp_path()
        super().__init__(io.FileIO(self._path, "r+"))
        self._on_commit = on_commit
        self._done = False

    def close(self) -> None:
        """Commit the buffer with one upload, then remove the temporary file.

        The temporary file is removed even if the final flush (``super().close``)
        or the upload raises, so a failed commit never leaks a staging file. A
        flush failure skips the upload, since the buffer is then incomplete.
        """
        if self._done:
            return
        self._done = True
        try:
            super().close()
            self._on_commit(self._path)
        finally:
            with contextlib.suppress(OSError):
                Path(self._path).unlink()

    def discard(self) -> None:
        """Discard the buffer without uploading (used on a failed write)."""
        if self._done:
            return
        self._done = True
        super().close()
        with contextlib.suppress(OSError):
            Path(self._path).unlink()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Commit on a clean exit; discard when the block raised."""
        if exc_type is None:
            self.close()
        else:
            self.discard()

    def __del__(self) -> None:
        """Drop an unfinished buffer without uploading on garbage collection."""
        if not self._done:
            with contextlib.suppress(Exception):
                self.discard()
