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
    from typing import Any


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
        self._outer_owns_finish = False

    def close(self) -> None:
        """Close locally, committing only when no outer wrapper owns the finish.

        The temporary file is removed even if the final flush (``super().close``)
        or the upload raises, so a failed commit never leaks a staging file. A
        flush failure skips the upload, since the buffer is then incomplete. An
        outer text wrapper separately selects commit or discard after it closes.
        """
        if self._done or self.closed:
            return
        if self._outer_owns_finish:
            super().close()
        else:
            self._finish(upload=True)

    def _finish(self, *, upload: bool) -> None:
        """Close, optionally upload, and unlink through one terminal path."""
        if self._done:
            return
        self._done = True
        try:
            if not self.closed:
                super().close()
            if upload:
                self._on_commit(self._path)
        finally:
            with contextlib.suppress(OSError):
                Path(self._path).unlink()

    def _handoff_to_outer(self) -> None:
        """Give an outer wrapper ownership of the terminal upload decision."""
        self._outer_owns_finish = True

    def _commit(self) -> None:
        """Upload and clean up after an outer wrapper closes successfully."""
        self._finish(upload=True)

    def discard(self) -> None:
        """Discard the buffer without uploading (used on a failed write)."""
        self._finish(upload=False)

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


class StagedTextWriteFile(io.TextIOWrapper):
    """Text wrapper that owns its staged buffer's terminal upload decision."""

    def __init__(
        self,
        buffer: Any,  # noqa: ANN401 - fsspec compression wrappers are file-like
        staged: StagedWriteFile,
        *,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> None:
        """Wrap ``buffer`` and take ownership of staged commit or discard."""
        self._staged = staged
        self._failed = False
        staged._handoff_to_outer()  # noqa: SLF001 - same-module lifecycle peer
        super().__init__(buffer, encoding=encoding, errors=errors, newline=newline)

    def write(self, text: str) -> int:
        """Write text, remembering any failure for the terminal close decision."""
        try:
            return super().write(text)
        except BaseException:
            self._failed = True
            raise

    def flush(self) -> None:
        """Flush text, remembering any failure for the terminal close decision."""
        try:
            super().flush()
        except BaseException:
            self._failed = True
            raise

    def close(self) -> None:
        """Commit only after the complete outer text stack closes cleanly."""
        try:
            super().close()
        except BaseException:
            self._failed = True
            self._staged.discard()
            raise
        if self._failed:
            self._staged.discard()
        else:
            self._staged._commit()  # noqa: SLF001 - same-module lifecycle peer

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close normally, but discard when the context block raised."""
        if exc_type is not None:
            self._failed = True
        super().__exit__(exc_type, exc_val, exc_tb)
