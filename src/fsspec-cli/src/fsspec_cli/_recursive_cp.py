"""Verified two-operand recursive ``cp``."""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
from collections.abc import AsyncIterator, Awaitable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer

from ._command import _MappedOperand, _usage_error
from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._path import _lexical_basename
from ._sources import _await_current, _SourceInvocation

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource

_MAX_ENTRIES = 10_000
_WALK_ROW_LENGTH = 3
_TOKEN_ALIASES = (
    ("etag", ("ETag", "etag")),
    ("md5", ("md5",)),
    ("content-md5", ("content-md5", "content_md5")),
    ("checksum", ("checksum",)),
)


class _IncompatibleResultError(Exception):
    pass


class _UnsupportedEntryError(Exception):
    pass


class _EntryLimitError(Exception):
    pass


@dataclass(frozen=True)
class _ManifestEntry:
    relative: str
    path: str
    kind: str
    size: int | None
    tokens: tuple[tuple[str, str | bytes], ...]


@dataclass(frozen=True)
class _Manifest:
    entries: tuple[_ManifestEntry, ...]


@dataclass(frozen=True)
class _WalkRow:
    root: str
    entries: tuple[_ManifestEntry, ...]
    directory_paths: tuple[str, ...]


@dataclass(frozen=True)
class _Failure:
    operand: _MappedOperand
    category: str | None = None
    error: Exception | None = None
    residue: bool = False
    rendered: bool = False


@dataclass(frozen=True)
class _Rows:
    values: tuple[_WalkRow, ...]


@dataclass(frozen=True)
class _WorkerError:
    error: BaseException


def _canonical_operand(
    command: str,
    operand: _MappedOperand,
    *,
    source: bool,
) -> _MappedOperand:
    parts = operand.path.split("/")
    if any(part in {".", ".."} for part in parts):
        rendered = _render_diagnostic_value(operand.spelling)
        _usage_error(command, f"{rendered}: dot segment unsupported")
    path = "/" + "/".join(part for part in parts if part)
    if source and path == "/":
        rendered = _render_diagnostic_value(operand.spelling)
        _usage_error(command, f"{rendered}: source root unsupported")
    return replace(operand, path=path)


async def _drain_task(task: asyncio.Task[object]) -> None:
    while not task.done():
        with suppress(BaseException):
            await asyncio.shield(task)


def _close_sync_iterator(iterator: Iterator[object]) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


async def _resolve_sync_iterator(
    awaitable: Awaitable[object],
) -> Iterator[object]:
    task = asyncio.ensure_future(awaitable)
    try:
        resolved = await asyncio.shield(task)
    except BaseException:
        await _drain_task(task)
        try:
            resolved = task.result()
        except BaseException:  # noqa: BLE001, S110 - original control flow wins.
            pass
        else:
            if isinstance(resolved, Iterator):
                close_task = asyncio.create_task(
                    asyncio.to_thread(_close_sync_iterator, resolved)
                )
                await _drain_task(close_task)
                with suppress(BaseException):
                    close_task.result()
        raise
    if not isinstance(resolved, Iterator):
        raise _IncompatibleResultError
    return resolved


async def _call(
    filesystem: AsyncFileSystem,
    operation: str,
    *args: object,
    **kwargs: object,
) -> object:
    method = getattr(filesystem, operation, None)
    if not callable(method):
        raise NotImplementedError
    result = method(*args, **kwargs)
    if not inspect.isawaitable(result):
        raise NotImplementedError
    return await _await_current(result)


def _tokens(info: Mapping[object, object]) -> tuple[tuple[str, str | bytes], ...]:
    tokens: list[tuple[str, str | bytes]] = []
    for normalized, aliases in _TOKEN_ALIASES:
        present = [alias for alias in aliases if alias in info]
        if len(present) > 1:
            raise _IncompatibleResultError
        if present:
            value = info[present[0]]
            if type(value) is not str and type(value) is not bytes:
                raise _IncompatibleResultError
            tokens.append((normalized, value))
    return tuple(tokens)


def _entry(
    relative: str,
    path: str,
    info: object,
    *,
    expected_kind: str | None = None,
) -> _ManifestEntry:
    if not isinstance(info, Mapping) or info.get("name") != path:
        raise _IncompatibleResultError
    typed_info = cast("Mapping[object, object]", info)
    islink = typed_info.get("islink", False)
    if type(islink) is not bool:
        raise _IncompatibleResultError
    kind = typed_info.get("type")
    if type(kind) is not str:
        raise _IncompatibleResultError
    if islink or kind not in {"directory", "file"}:
        raise _UnsupportedEntryError
    if expected_kind is not None and kind != expected_kind:
        raise _IncompatibleResultError
    size = None
    if kind == "file":
        size = typed_info.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise _IncompatibleResultError
    return _ManifestEntry(relative, path, kind, size, _tokens(typed_info))


def _walk_row(
    source_path: str,
    value: object,
    *,
    entry_capacity: int,
) -> _WalkRow:
    if type(value) is not tuple or len(value) != _WALK_ROW_LENGTH:
        raise _IncompatibleResultError
    root, directories, files = value
    if (
        type(root) is not str
        or not root.startswith("/")
        or root.rstrip("/") != root
        or "//" in root
        or "\0" in root
        or "\n" in root
        or "\r" in root
        or _has_dot_segment(root)
        or not isinstance(directories, Mapping)
        or not isinstance(files, Mapping)
    ):
        raise _IncompatibleResultError

    entries: list[_ManifestEntry] = []
    directory_paths: list[str] = []
    child_names: set[str] = set()
    for collection, kind in ((directories, "directory"), (files, "file")):
        for name, info in collection.items():
            if (
                type(name) is not str
                or not name
                or name in {".", ".."}
                or "/" in name
                or "\0" in name
                or "\n" in name
                or "\r" in name
                or name in child_names
            ):
                raise _IncompatibleResultError
            child_names.add(name)
            path = _child_path(root, name)
            entry = _entry(
                _relative_path(source_path, path),
                path,
                info,
                expected_kind=kind,
            )
            entries.append(entry)
            if len(entries) > entry_capacity:
                raise _EntryLimitError
            if kind == "directory":
                directory_paths.append(path)
    return _WalkRow(root, tuple(entries), tuple(directory_paths))


def _accept_walk_row(
    row: _WalkRow,
    *,
    rows: list[_WalkRow],
    seen_roots: set[str],
    expected_roots: set[str],
    seen_relatives: set[str],
) -> None:
    relatives = {entry.relative for entry in row.entries}
    if (
        row.root in seen_roots
        or row.root not in expected_roots
        or seen_relatives.intersection(relatives)
    ):
        raise _IncompatibleResultError
    seen_roots.add(row.root)
    expected_roots.update(row.directory_paths)
    seen_relatives.update(relatives)
    rows.append(row)


def _materialize_sync(
    iterator: Iterator[object],
    source_path: str,
) -> _Rows | _WorkerError:
    values: list[_WalkRow] = []
    count = 1
    seen_roots: set[str] = set()
    expected_roots = {source_path}
    seen_relatives = {""}
    error: BaseException | None = None
    try:
        for value in iterator:
            row = _walk_row(
                source_path,
                value,
                entry_capacity=_MAX_ENTRIES - count,
            )
            _accept_walk_row(
                row,
                rows=values,
                seen_roots=seen_roots,
                expected_roots=expected_roots,
                seen_relatives=seen_relatives,
            )
            count += len(row.entries)
    except BaseException as caught:  # noqa: BLE001 - return across task as data.
        error = caught
    close = getattr(iterator, "close", None)
    if callable(close):
        try:
            close()
        except BaseException as caught:  # noqa: BLE001 - return across task as data.
            if error is None:
                error = caught
    return _WorkerError(error) if error is not None else _Rows(tuple(values))


async def _sync_rows(
    iterator: Iterator[object],
    source_path: str,
) -> tuple[_WalkRow, ...]:
    worker = asyncio.create_task(
        asyncio.to_thread(_materialize_sync, iterator, source_path)
    )
    try:
        outcome = await asyncio.shield(worker)
    except BaseException:
        while not worker.done():
            with suppress(BaseException):
                await asyncio.shield(worker)
        with suppress(BaseException):
            worker.result()
        raise
    if isinstance(outcome, _WorkerError):
        raise outcome.error
    return outcome.values


async def _close_async_iterator(iterator: AsyncIterator[object]) -> None:
    close = getattr(iterator, "aclose", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await _await_current(result)


async def _async_rows(
    iterator: AsyncIterator[object],
    source_path: str,
) -> tuple[_WalkRow, ...]:
    values: list[_WalkRow] = []
    count = 1
    seen_roots: set[str] = set()
    expected_roots = {source_path}
    seen_relatives = {""}
    try:
        while True:
            try:
                value = await _await_current(anext(iterator))
            except StopAsyncIteration:
                break
            row = _walk_row(
                source_path,
                value,
                entry_capacity=_MAX_ENTRIES - count,
            )
            _accept_walk_row(
                row,
                rows=values,
                seen_roots=seen_roots,
                expected_roots=expected_roots,
                seen_relatives=seen_relatives,
            )
            count += len(row.entries)
    except BaseException:
        with suppress(BaseException):
            await _close_async_iterator(iterator)
        raise
    await _close_async_iterator(iterator)
    return tuple(values)


async def _walk_rows(
    filesystem: AsyncFileSystem,
    path: str,
) -> tuple[_WalkRow, ...]:
    method = getattr(filesystem, "_walk", None)
    if not callable(method):
        raise NotImplementedError
    result = method(path, detail=True, on_error="raise")
    if isinstance(result, AsyncIterator):
        return await _async_rows(result, path)
    if not inspect.isawaitable(result):
        raise _IncompatibleResultError
    return await _sync_rows(await _resolve_sync_iterator(result), path)


def _child_path(root: str, name: str) -> str:
    return f"/{name}" if root == "/" else f"{root}/{name}"


def _has_dot_segment(path: str) -> bool:
    return any(part in {".", ".."} for part in path.split("/"))


def _relative_path(root: str, path: str) -> str:
    return path[len(root) + 1 :] if root != "/" else path[1:]


def _manifest_from_rows(
    root_entry: _ManifestEntry,
    values: tuple[_WalkRow, ...],
) -> _Manifest:
    entries = {"": root_entry}
    rows: dict[str, _WalkRow] = {}
    for row in values:
        if row.root in rows:
            raise _IncompatibleResultError
        rows[row.root] = row

    expected_roots = {root_entry.path}
    for row in rows.values():
        for entry in row.entries:
            if entry.relative in entries:
                raise _IncompatibleResultError
            entries[entry.relative] = entry
        expected_roots.update(row.directory_paths)
    if set(rows) != expected_roots:
        raise _IncompatibleResultError
    return _Manifest(tuple(sorted(entries.values(), key=lambda item: item.relative)))


async def _manifest(
    filesystem: AsyncFileSystem,
    path: str,
    source_info: object,
) -> _Manifest:
    root_entry = _entry("", path, source_info, expected_kind="directory")
    return _manifest_from_rows(root_entry, await _walk_rows(filesystem, path))


def _render_operand(command: str, operand: _MappedOperand, category: str) -> None:
    prefix = _render_diagnostic_prefix(command)
    rendered = _render_diagnostic_value(operand.spelling)
    typer.echo(f"{prefix} {rendered}: {category}", err=True, color=True)


def _render_failure(command: str, failure: _Failure) -> None:
    if failure.rendered:
        return
    suffix = "; destination residue may remain" if failure.residue else ""
    _render_operand(command, failure.operand, f"{failure.category}{suffix}")


def _read_failure(operand: _MappedOperand, error: Exception) -> _Failure:
    if isinstance(error, FileNotFoundError):
        category = "not found"
    elif isinstance(error, PermissionError):
        category = "permission denied"
    elif isinstance(error, NotImplementedError):
        category = "unsupported operation"
    else:
        rendered_class = _render_diagnostic_value(type(error).__name__)
        rendered_message = _render_diagnostic_value(str(error))
        category = f"backend failure ({rendered_class}): {rendered_message}"
    return _Failure(operand, category, error=error)


def _staging_failure(source: _MappedOperand, error: Exception) -> _Failure:
    rendered_class = _render_diagnostic_value(type(error).__name__)
    return _Failure(source, f"staging failure ({rendered_class})", residue=True)


async def _optional_info(
    filesystem: AsyncFileSystem,
    path: str,
) -> tuple[object | None, Exception | None]:
    try:
        return await _call(filesystem, "_info", path), None
    except FileNotFoundError:
        return None, None
    except Exception as error:  # noqa: BLE001 - classify read boundary.
        return None, error


def _classify_source_info(
    operand: _MappedOperand,
    info: object,
) -> _Failure | None:
    if not isinstance(info, Mapping):
        return _Failure(operand, "incompatible result")
    typed_info = cast("Mapping[object, object]", info)
    kind = typed_info.get("type")
    if type(kind) is not str:
        return _Failure(operand, "incompatible result")
    islink = typed_info.get("islink", False)
    if type(islink) is not bool:
        return _Failure(operand, "incompatible result")
    if islink or kind not in {"directory", "file"}:
        return _Failure(operand, "unsupported entry type")
    if kind == "file":
        return _Failure(operand, "not a directory")
    return None


def _classify_existing(  # noqa: PLR0911 - stable metadata categories.
    operand: _MappedOperand,
    path: str,
    info: object,
    *,
    expected: str | None = None,
    require_name: bool = True,
) -> tuple[_ManifestEntry | None, _Failure | None]:
    if not require_name:
        if not isinstance(info, Mapping):
            return None, _Failure(operand, "incompatible result")
        typed_info = cast("Mapping[object, object]", info)
        kind = typed_info.get("type")
        if type(kind) is not str:
            return None, _Failure(operand, "incompatible result")
        islink = typed_info.get("islink", False)
        if type(islink) is not bool:
            return None, _Failure(operand, "incompatible result")
        if islink or kind not in {"directory", "file"}:
            return None, _Failure(operand, "unsupported entry type")
        return _ManifestEntry("", path, kind, None, ()), None
    try:
        entry = _entry("", path, info)
    except _UnsupportedEntryError:
        return None, _Failure(operand, "unsupported entry type")
    except _IncompatibleResultError:
        return None, _Failure(operand, "incompatible result")
    if expected is not None and entry.kind != expected:
        return None, _Failure(operand, "destination type conflict")
    return entry, None


def _destination_path(root: str, relative: str) -> str:
    return root if not relative else _child_path(root, relative)


def _cleanup_staging(command: str, source: _MappedOperand, path: str) -> bool:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as error:  # noqa: BLE001 - diagnostic boundary.
        rendered_class = _render_diagnostic_value(type(error).__name__)
        _render_operand(
            command,
            source,
            "staging cleanup failure "
            f"({rendered_class}); host staging residue may remain; "
            "destination residue may remain",
        )
        return False
    return True


def _cleanup_under_control(command: str, source: _MappedOperand, path: str) -> None:
    with suppress(BaseException):  # Original control flow wins.
        _cleanup_staging(command, source, path)


def _shared_tokens_match(
    source_tokens: tuple[tuple[str, str | bytes], ...],
    destination_tokens: tuple[tuple[str, str | bytes], ...],
) -> bool:
    destination = dict(destination_tokens)
    return all(
        destination[name] == value
        for name, value in source_tokens
        if name in destination
    )


@dataclass(frozen=True)
class _RecursiveCopy:
    command: str
    source: _MappedOperand
    destination: _MappedOperand
    source_filesystem: AsyncFileSystem
    destination_filesystem: AsyncFileSystem

    async def _resolve_target(  # noqa: C901, PLR0911, PLR0912
        self,
    ) -> tuple[str, _Failure | None]:
        destination_info, error = await _optional_info(
            self.destination_filesystem,
            self.destination.path,
        )
        if error is not None:
            return self.destination.path, _read_failure(self.destination, error)

        known_parent = None
        resolved = self.destination.path
        resolved_info = destination_info
        if destination_info is not None:
            entry, failure = _classify_existing(
                self.destination,
                self.destination.path,
                destination_info,
                require_name=False,
            )
            if failure is not None:
                return resolved, failure
            if entry is None:
                return resolved, _Failure(self.destination, "incompatible result")
            if entry.kind == "directory":
                known_parent = self.destination.path
                resolved = _child_path(
                    self.destination.path,
                    _lexical_basename(self.source.path),
                )
                resolved_info, error = await _optional_info(
                    self.destination_filesystem,
                    resolved,
                )
                if error is not None:
                    return resolved, _read_failure(self.destination, error)

        parent = resolved.rpartition("/")[0] or "/"
        if parent != known_parent:
            parent_info, error = await _optional_info(
                self.destination_filesystem,
                parent,
            )
            if error is not None:
                return resolved, _read_failure(self.destination, error)
            if parent_info is None:
                return resolved, _Failure(self.destination, "not found")
            parent_entry, failure = _classify_existing(
                self.destination,
                parent,
                parent_info,
                require_name=False,
            )
            if failure is not None:
                if failure.category == "unsupported entry type":
                    return resolved, _Failure(self.destination, "not a directory")
                return resolved, failure
            if parent_entry is None:
                return resolved, _Failure(self.destination, "incompatible result")
            if parent_entry.kind != "directory":
                return resolved, _Failure(self.destination, "not a directory")

        if resolved_info is not None:
            root_entry, failure = _classify_existing(
                self.destination,
                resolved,
                resolved_info,
                require_name=False,
            )
            if failure is not None:
                return resolved, failure
            if root_entry is None:
                return resolved, _Failure(self.destination, "incompatible result")
            if root_entry.kind == "file":
                return resolved, _Failure(
                    self.destination,
                    "destination type conflict",
                )

        if self.source.name == self.destination.name and (
            resolved == self.source.path or resolved.startswith(f"{self.source.path}/")
        ):
            return resolved, _Failure(
                self.destination,
                "destination is inside source",
            )
        return resolved, None

    async def _preflight_destination(
        self,
        root: str,
        manifest: _Manifest,
    ) -> tuple[tuple[_ManifestEntry, ...], _Failure | None]:
        missing: list[_ManifestEntry] = []
        for entry in manifest.entries:
            path = _destination_path(root, entry.relative)
            info, error = await _optional_info(self.destination_filesystem, path)
            if error is not None:
                return (), _read_failure(self.destination, error)
            if info is None:
                if entry.kind == "directory":
                    missing.append(entry)
                continue
            existing, failure = _classify_existing(self.destination, path, info)
            if failure is not None:
                return (), failure
            if existing is None:
                return (), _Failure(self.destination, "incompatible result")
            if existing.kind != entry.kind:
                return (), _Failure(
                    self.destination,
                    "destination type conflict",
                )
        return tuple(missing), None

    async def _transfer(  # noqa: C901, PLR0912
        self,
        source_entry: _ManifestEntry,
        destination_path: str,
    ) -> _Failure | None:
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix="fsspec-cli-cp-recursive-")
        except Exception as error:  # noqa: BLE001 - staging creation boundary.
            return _staging_failure(self.source, error)

        failure = None
        try:
            try:
                os.close(descriptor)
            except Exception as error:  # noqa: BLE001 - descriptor boundary.
                failure = _staging_failure(self.source, error)

            if failure is None:
                try:
                    await _call(
                        self.source_filesystem,
                        "_get_file",
                        source_entry.path,
                        temporary,
                    )
                except Exception:  # noqa: BLE001 - stable transfer category.
                    failure = _Failure(
                        self.source,
                        "transfer failure",
                        residue=True,
                    )

            if failure is None:
                try:
                    staged_size = Path(temporary).stat().st_size  # noqa: ASYNC240
                except Exception as error:  # noqa: BLE001 - staging stat boundary.
                    failure = _staging_failure(self.source, error)
                else:
                    if staged_size != source_entry.size:
                        failure = _Failure(
                            self.source,
                            "source changed",
                            residue=True,
                        )

            if failure is None:
                try:
                    await _call(
                        self.destination_filesystem,
                        "_put_file",
                        temporary,
                        destination_path,
                        mode="overwrite",
                    )
                except Exception:  # noqa: BLE001 - stable mutation category.
                    failure = _Failure(
                        self.destination,
                        "mutation failure",
                        residue=True,
                    )
        except BaseException:
            _cleanup_under_control(self.command, self.source, temporary)
            raise

        if failure is not None:
            try:
                _render_failure(self.command, failure)
            except BaseException:
                _cleanup_under_control(self.command, self.source, temporary)
                raise
        cleanup_succeeded = _cleanup_staging(
            self.command,
            self.source,
            temporary,
        )
        if failure is not None:
            return replace(failure, rendered=True)
        if not cleanup_succeeded:
            return _Failure(self.source, rendered=True)
        return None

    async def _mutate(
        self,
        root: str,
        manifest: _Manifest,
        missing_directories: tuple[_ManifestEntry, ...],
    ) -> _Failure | None:
        for entry in sorted(
            missing_directories,
            key=lambda item: (item.relative.count("/"), item.relative),
        ):
            try:
                await _call(
                    self.destination_filesystem,
                    "_mkdir",
                    _destination_path(root, entry.relative),
                    create_parents=False,
                )
            except Exception:  # noqa: BLE001, PERF203 - stable mutation category.
                return _Failure(
                    self.destination,
                    "mutation failure",
                    residue=True,
                )

        for entry in manifest.entries:
            if entry.kind != "file":
                continue
            failure = await self._transfer(
                entry,
                _destination_path(root, entry.relative),
            )
            if failure is not None:
                return failure
        return None

    async def _revalidate_source(self, frozen: _Manifest) -> _Failure | None:
        try:
            current_info = await _call(
                self.source_filesystem,
                "_info",
                self.source.path,
            )
            current = await _manifest(
                self.source_filesystem,
                self.source.path,
                current_info,
            )
        except Exception:  # noqa: BLE001 - stable revalidation category.
            return _Failure(
                self.source,
                "source revalidation failure",
                residue=True,
            )
        if current != frozen:
            return _Failure(self.source, "source changed", residue=True)
        return None

    async def _verify_destination(
        self,
        root: str,
        manifest: _Manifest,
    ) -> _Failure | None:
        try:
            for source_entry in manifest.entries:
                path = _destination_path(root, source_entry.relative)
                info = await _call(self.destination_filesystem, "_info", path)
                destination_entry = _entry(
                    source_entry.relative,
                    path,
                    info,
                    expected_kind=source_entry.kind,
                )
                if (
                    destination_entry.size != source_entry.size
                    or not _shared_tokens_match(
                        source_entry.tokens,
                        destination_entry.tokens,
                    )
                ):
                    return _Failure(
                        self.destination,
                        "verification failure",
                        residue=True,
                    )
        except Exception:  # noqa: BLE001 - stable verification category.
            return _Failure(
                self.destination,
                "verification failure",
                residue=True,
            )
        return None

    async def run(self) -> _Failure | None:  # noqa: C901, PLR0911
        try:
            source_info = await _call(
                self.source_filesystem,
                "_info",
                self.source.path,
            )
        except Exception as error:  # noqa: BLE001 - classify read boundary.
            return _read_failure(self.source, error)
        failure = _classify_source_info(self.source, source_info)
        if failure is not None:
            return failure

        root, failure = await self._resolve_target()
        if failure is not None:
            return failure

        try:
            manifest = await _manifest(
                self.source_filesystem,
                self.source.path,
                source_info,
            )
        except _UnsupportedEntryError:
            return _Failure(self.source, "unsupported entry type")
        except _EntryLimitError:
            return _Failure(
                self.source,
                f"source tree exceeds {_MAX_ENTRIES} entries",
            )
        except _IncompatibleResultError:
            return _Failure(self.source, "incompatible result")
        except Exception as error:  # noqa: BLE001 - classify walk boundary.
            return _read_failure(self.source, error)

        missing, failure = await self._preflight_destination(root, manifest)
        if failure is not None:
            return failure
        failure = await self._mutate(root, manifest, missing)
        if failure is not None:
            return failure
        failure = await self._revalidate_source(manifest)
        if failure is not None:
            return failure
        return await self._verify_destination(root, manifest)


async def _run_recursive_cp(
    command: str,
    source_operand: _MappedOperand,
    destination_operand: _MappedOperand,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    source = _canonical_operand(command, source_operand, source=True)
    destination = _canonical_operand(command, destination_operand, source=False)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure = None
    try:
        names = tuple(dict.fromkeys((source.name, destination.name)))
        filesystems = await invocation.acquire(names)
        if filesystems is not None:
            failure = await _RecursiveCopy(
                command,
                source,
                destination,
                filesystems[source.name],
                filesystems[destination.name],
            ).run()
            if failure is not None:
                _render_failure(command, failure)
            succeeded = failure is None
    finally:
        command_error = failure.error if failure is not None else None
        cleanup_failed = await invocation.close_with_command_error(command_error)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)
