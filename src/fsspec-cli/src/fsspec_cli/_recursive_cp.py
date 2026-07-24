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

from ._command import (
    _backend_category,
    _CommandFailureError,
    _drain_current_operation,
    _MappedOperand,
    _render_operand_diagnostic,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value
from ._path import (
    _has_dot_segment,
    _is_root,
    _is_same_or_descendant,
    _lexical_basename,
    _lexical_join,
    _lexical_parent,
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
class _RecursiveCpFailure:
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
    if _has_dot_segment(operand.path):
        rendered = _render_diagnostic_value(operand.spelling)
        _usage_error(command, f"{rendered}: dot segment unsupported")
    if source and _is_root(operand.path):
        rendered = _render_diagnostic_value(operand.spelling)
        _usage_error(command, f"{rendered}: source root unsupported")
    return operand


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


def _render_failure(command: str, failure: _RecursiveCpFailure) -> None:
    if failure.rendered:
        return
    suffix = "; destination residue may remain" if failure.residue else ""
    _render_operand_diagnostic(
        command,
        failure.operand,
        f"{failure.category}{suffix}",
    )


def _read_failure(operand: _MappedOperand, error: Exception) -> _RecursiveCpFailure:
    return _RecursiveCpFailure(operand, _backend_category(error), error=error)


def _staging_failure(source: _MappedOperand, error: Exception) -> _RecursiveCpFailure:
    rendered_class = _render_diagnostic_value(type(error).__name__)
    return _RecursiveCpFailure(
        source,
        f"staging failure ({rendered_class})",
        error=error,
        residue=True,
    )


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
) -> _RecursiveCpFailure | None:
    if not isinstance(info, Mapping):
        return _RecursiveCpFailure(operand, "incompatible result")
    typed_info = cast("Mapping[object, object]", info)
    kind = typed_info.get("type")
    if type(kind) is not str:
        return _RecursiveCpFailure(operand, "incompatible result")
    islink = typed_info.get("islink", False)
    if type(islink) is not bool:
        return _RecursiveCpFailure(operand, "incompatible result")
    if islink or kind not in {"directory", "file"}:
        return _RecursiveCpFailure(operand, "unsupported entry type")
    if kind == "file":
        return _RecursiveCpFailure(operand, "not a directory")
    return None


def _classify_existing(  # noqa: PLR0911 - stable metadata categories.
    operand: _MappedOperand,
    path: str,
    info: object,
    *,
    require_name: bool = True,
) -> _ManifestEntry | _RecursiveCpFailure:
    if not require_name:
        if not isinstance(info, Mapping):
            return _RecursiveCpFailure(operand, "incompatible result")
        typed_info = cast("Mapping[object, object]", info)
        kind = typed_info.get("type")
        if type(kind) is not str:
            return _RecursiveCpFailure(operand, "incompatible result")
        islink = typed_info.get("islink", False)
        if type(islink) is not bool:
            return _RecursiveCpFailure(operand, "incompatible result")
        if islink or kind not in {"directory", "file"}:
            return _RecursiveCpFailure(operand, "unsupported entry type")
        return _ManifestEntry("", path, kind, None, ())
    try:
        entry = _entry("", path, info)
    except _UnsupportedEntryError as error:
        return _RecursiveCpFailure(operand, "unsupported entry type", error=error)
    except _IncompatibleResultError as error:
        return _RecursiveCpFailure(operand, "incompatible result", error=error)
    return entry


def _destination_path(root: str, relative: str) -> str:
    return root if not relative else _lexical_join(root, relative)


def _cleanup_staging(
    command: str,
    source: _MappedOperand,
    path: str,
) -> Exception | None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as error:  # noqa: BLE001 - diagnostic boundary.
        rendered_class = _render_diagnostic_value(type(error).__name__)
        _render_operand_diagnostic(
            command,
            source,
            "staging cleanup failure "
            f"({rendered_class}); host staging residue may remain; "
            "destination residue may remain",
        )
        return error
    return None


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
    ) -> tuple[str, _RecursiveCpFailure | None]:
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
            entry = _classify_existing(
                self.destination,
                self.destination.path,
                destination_info,
                require_name=False,
            )
            if isinstance(entry, _RecursiveCpFailure):
                return resolved, entry
            if entry.kind == "directory":
                known_parent = self.destination.path
                resolved = _lexical_join(
                    self.destination.path,
                    _lexical_basename(self.source.path),
                )
                resolved_info, error = await _optional_info(
                    self.destination_filesystem,
                    resolved,
                )
                if error is not None:
                    return resolved, _read_failure(self.destination, error)

        parent = _lexical_parent(resolved)
        if parent != known_parent:
            parent_info, error = await _optional_info(
                self.destination_filesystem,
                parent,
            )
            if error is not None:
                return resolved, _read_failure(self.destination, error)
            if parent_info is None:
                return resolved, _RecursiveCpFailure(self.destination, "not found")
            parent_entry = _classify_existing(
                self.destination,
                parent,
                parent_info,
                require_name=False,
            )
            if isinstance(parent_entry, _RecursiveCpFailure):
                if parent_entry.category == "unsupported entry type":
                    return resolved, _RecursiveCpFailure(
                        self.destination, "not a directory"
                    )
                return resolved, parent_entry
            if parent_entry.kind != "directory":
                return resolved, _RecursiveCpFailure(
                    self.destination, "not a directory"
                )

        if resolved_info is not None:
            root_entry = _classify_existing(
                self.destination,
                resolved,
                resolved_info,
                require_name=False,
            )
            if isinstance(root_entry, _RecursiveCpFailure):
                return resolved, root_entry
            if root_entry.kind == "file":
                return resolved, _RecursiveCpFailure(
                    self.destination,
                    "destination type conflict",
                )

        if self.source.name == self.destination.name and _is_same_or_descendant(
            self.source.path,
            resolved,
        ):
            return resolved, _RecursiveCpFailure(
                self.destination,
                "destination is inside source",
            )
        return resolved, None

    async def _preflight_destination(
        self,
        root: str,
        manifest: _Manifest,
    ) -> tuple[tuple[_ManifestEntry, ...], _RecursiveCpFailure | None]:
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
            existing = _classify_existing(self.destination, path, info)
            if isinstance(existing, _RecursiveCpFailure):
                return (), existing
            if existing.kind != entry.kind:
                return (), _RecursiveCpFailure(
                    self.destination,
                    "destination type conflict",
                )
        return tuple(missing), None

    async def _transfer(  # noqa: C901, PLR0912
        self,
        source_entry: _ManifestEntry,
        destination_path: str,
    ) -> _RecursiveCpFailure | None:
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
                except Exception as error:  # noqa: BLE001 - stable transfer category.
                    failure = _RecursiveCpFailure(
                        self.source,
                        "transfer failure",
                        error=error,
                        residue=True,
                    )

            if failure is None:
                try:
                    staged_size = Path(temporary).stat().st_size  # noqa: ASYNC240
                except Exception as error:  # noqa: BLE001 - staging stat boundary.
                    failure = _staging_failure(self.source, error)
                else:
                    if staged_size != source_entry.size:
                        failure = _RecursiveCpFailure(
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
                except Exception as error:  # noqa: BLE001 - stable mutation category.
                    failure = _RecursiveCpFailure(
                        self.destination,
                        "mutation failure",
                        error=error,
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
        cleanup_error = _cleanup_staging(
            self.command,
            self.source,
            temporary,
        )
        if failure is not None:
            return replace(failure, rendered=True)
        if cleanup_error is not None:
            return _RecursiveCpFailure(self.source, error=cleanup_error, rendered=True)
        return None

    async def _mutate(
        self,
        root: str,
        manifest: _Manifest,
        missing_directories: tuple[_ManifestEntry, ...],
    ) -> _RecursiveCpFailure | None:
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
            except Exception as error:  # noqa: BLE001, PERF203 - stable mutation category.
                return _RecursiveCpFailure(
                    self.destination,
                    "mutation failure",
                    error=error,
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

    async def _revalidate_source(self, frozen: _Manifest) -> _RecursiveCpFailure | None:
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
        except Exception as error:  # noqa: BLE001 - stable revalidation category.
            return _RecursiveCpFailure(
                self.source,
                "source revalidation failure",
                error=error,
                residue=True,
            )
        if current != frozen:
            return _RecursiveCpFailure(self.source, "source changed", residue=True)
        return None

    async def _verify_destination(
        self,
        root: str,
        manifest: _Manifest,
    ) -> _RecursiveCpFailure | None:
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
                    return _RecursiveCpFailure(
                        self.destination,
                        "verification failure",
                        residue=True,
                    )
        except Exception as error:  # noqa: BLE001 - stable verification category.
            return _RecursiveCpFailure(
                self.destination,
                "verification failure",
                error=error,
                residue=True,
            )
        return None

    async def run(self) -> _RecursiveCpFailure | None:  # noqa: C901, PLR0911
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
        except _UnsupportedEntryError as error:
            return _RecursiveCpFailure(
                self.source, "unsupported entry type", error=error
            )
        except _EntryLimitError as error:
            return _RecursiveCpFailure(
                self.source,
                f"source tree exceeds {_MAX_ENTRIES} entries",
                error=error,
            )
        except _IncompatibleResultError as error:
            return _RecursiveCpFailure(self.source, "incompatible result", error=error)
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
    filesystems: Mapping[str, AsyncFileSystem],
) -> None:
    failure = await _RecursiveCopy(
        command,
        source_operand,
        destination_operand,
        filesystems[source_operand.name],
        filesystems[destination_operand.name],
    ).run()
    if failure is not None:
        try:
            _render_failure(command, failure)
        except Exception as error:
            raise _CommandFailureError(
                error=failure.error,
                render=False,
                propagate=error,
            ) from error
        raise _CommandFailureError(error=failure.error, render=False)
