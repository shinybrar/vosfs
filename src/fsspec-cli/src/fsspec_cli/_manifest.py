"""Frozen, validated manifests built from a backend directory walk.

Recursive copy plans the whole transfer before it mutates anything, so this
module turns a backend's ``_walk``/``_ls`` output into an immutable manifest of
entries — or refuses. Per
:doc:`ADR 0006 <../../../docs/adr/0006-treat-backend-results-as-untrusted-input>`,
every field of every returned row is validated here: a malformed path or a
duplicated child is not a rendering bug when the caller will go on to *delete
or overwrite* along these paths.

Three refusals are distinguished, because the command renders them differently:
an incompatible result, an entry type the profile excludes (a link or special
file), and a tree larger than the bound.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ._command import _drain_current_operation
from ._path import (
    _has_dot_segment,
    _lexical_join,
    _lexical_relative,
    _same_lexical_path,
)

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem


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
class _Rows:
    values: tuple[_WalkRow, ...]


@dataclass(frozen=True)
class _WorkerError:
    error: BaseException


def _close_sync_iterator(iterator: Iterator[object]) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


async def _resolve_sync_iterator(
    awaitable: Awaitable[object],
) -> Iterator[object]:
    resolved: object | None = None

    async def resolve() -> object:
        nonlocal resolved
        resolved = await awaitable
        return resolved

    try:
        resolved = await _drain_current_operation(resolve())
    except BaseException:
        if isinstance(resolved, Iterator):
            with suppress(BaseException):
                await _drain_current_operation(
                    asyncio.to_thread(_close_sync_iterator, resolved)
                )
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
    return await _drain_current_operation(result)


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
    if not isinstance(info, Mapping):
        raise _IncompatibleResultError
    name = info.get("name")
    if type(name) is not str or not _same_lexical_path(name, path):
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
            path = _lexical_join(root, name)
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
    outcome = await _drain_current_operation(
        asyncio.to_thread(_materialize_sync, iterator, source_path)
    )
    if isinstance(outcome, _WorkerError):
        raise outcome.error
    return outcome.values


async def _close_async_iterator(iterator: AsyncIterator[object]) -> None:
    close = getattr(iterator, "aclose", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await _drain_current_operation(result)


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
                value = await _drain_current_operation(anext(iterator))
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
    requested_path: str,
    source_path: str,
) -> tuple[_WalkRow, ...]:
    method = getattr(filesystem, "_walk", None)
    if not callable(method):
        raise NotImplementedError
    result = method(requested_path, detail=True, on_error="raise")
    if isinstance(result, AsyncIterator):
        return await _async_rows(result, source_path)
    if not inspect.isawaitable(result):
        raise _IncompatibleResultError
    return await _sync_rows(await _resolve_sync_iterator(result), source_path)


def _relative_path(root: str, path: str) -> str:
    relative = _lexical_relative(root, path)
    if relative is None:
        raise _IncompatibleResultError
    return relative


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
    if not isinstance(source_info, Mapping):
        raise _IncompatibleResultError
    reported_path = source_info.get("name")
    if type(reported_path) is not str or not _same_lexical_path(reported_path, path):
        raise _IncompatibleResultError
    root_entry = _entry("", reported_path, source_info, expected_kind="directory")
    return _manifest_from_rows(
        root_entry,
        await _walk_rows(filesystem, path, reported_path),
    )
