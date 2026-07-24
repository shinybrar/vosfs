"""Raw parsing, walk normalization, and Unicode rendering for ``tree``."""

from __future__ import annotations

import asyncio
import inspect
import locale
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias, TypeGuard

from ._command import (
    _drain_current_operation,
    _Failure,
    _MappedOperand,
    _run_single_operand_text,
)

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from ._app import AsyncFilesystemSource

_WALK_ROW_LENGTH = 3


@dataclass(frozen=True)
class _TreeRequest:
    maxdepth: int | None
    operand: _MappedOperand


@dataclass(frozen=True)
class _WalkRow:
    root: str
    directories: tuple[str, ...]
    files: tuple[str, ...]


@dataclass(frozen=True)
class _MaterializedRows:
    values: list[object]


@dataclass(frozen=True)
class _MaterializationError:
    error: BaseException


_MaterializationOutcome: TypeAlias = _MaterializedRows | _MaterializationError


def _valid_root(value: object) -> TypeGuard[str]:
    return type(value) is str and "\0" not in value and "\n" not in value


def _valid_entry(value: object, *, root_file: bool = False) -> TypeGuard[str]:
    return (
        type(value) is str
        and (bool(value) or root_file)
        and "/" not in value
        and "\0" not in value
        and "\n" not in value
    )


def _entries(
    values: Sequence[object],
    *,
    root_file: bool = False,
) -> tuple[str, ...] | None:
    entries: list[str] = []
    for value in values:
        if not _valid_entry(value, root_file=root_file):
            return None
        entries.append(value)
    if len(set(entries)) != len(entries):
        return None
    return tuple(entries)


def _row(value: object) -> _WalkRow | None:
    if type(value) is not tuple or len(value) != _WALK_ROW_LENGTH:
        return None
    root, directories, files = value
    if not (_valid_root(root) and type(directories) is list and type(files) is list):
        return None
    typed_directories = _entries(directories)
    typed_files = _entries(files, root_file=True)
    if typed_directories is None or typed_files is None:
        return None
    if set(typed_directories).intersection(typed_files):
        return None
    return _WalkRow(root, typed_directories, typed_files)


def _canonical_root(path: str) -> str:
    return path.rstrip("/") or "/"


def _child_root(root: str, name: str) -> str:
    if not root or root == "/":
        return f"/{name}"
    return f"{root.rstrip('/')}/{name}"


def _index_rows(values: list[object]) -> tuple[_WalkRow, dict[str, _WalkRow]] | None:
    indexed: dict[str, _WalkRow] = {}
    first = None
    for value in values:
        row = _row(value)
        if row is None:
            return None
        if first is None:
            first = row
        canonical = _canonical_root(row.root)
        if canonical in indexed:
            return None
        indexed[canonical] = row
    if first is None:
        return None
    return first, indexed


def _valid_root_file(rows: Mapping[str, _WalkRow], root: _WalkRow) -> bool:
    rows_with_empty_file = [row for row in rows.values() if "" in row.files]
    if not rows_with_empty_file:
        return True
    return (
        rows_with_empty_file == [root]
        and root.directories == ()
        and root.files == ("",)
        and len(rows) == 1
    )


def _all_rows_reachable(rows: Mapping[str, _WalkRow], root: _WalkRow) -> bool:
    reachable = {_canonical_root(root.root)}
    pending = [root]
    while pending:
        parent = pending.pop()
        for directory in parent.directories:
            child_key = _canonical_root(_child_root(parent.root, directory))
            child = rows.get(child_key)
            if child is not None and child_key not in reachable:
                reachable.add(child_key)
                pending.append(child)
    return reachable == rows.keys()


def _validated_rows(
    request: _TreeRequest,
    values: list[object],
) -> Mapping[str, _WalkRow] | None:
    indexed = _index_rows(values)
    if indexed is None:
        return None
    root, rows = indexed

    root_key = _canonical_root(root.root)
    if root_key != _canonical_root(request.operand.path):
        return None
    if not _valid_root_file(rows, root):
        return None
    if not _all_rows_reachable(rows, root):
        return None
    return rows


def _sorted_entries(entries: tuple[str, ...]) -> list[str]:
    return sorted(entries, key=lambda entry: (locale.strxfrm(entry), entry))


def _render_tree(request: _TreeRequest, rows: Mapping[str, _WalkRow]) -> str:
    lines = [request.operand.path]
    root = rows[_canonical_root(request.operand.path)]
    if root.files == ("",):
        return f"{request.operand.path}\n"

    stack: list[tuple[_WalkRow, int, str, str, bool, bool]] = []

    def push_children(row: _WalkRow, depth: int, prefix: str) -> None:
        if request.maxdepth is not None and depth >= request.maxdepth:
            return
        directories = _sorted_entries(row.directories)
        files = _sorted_entries(row.files)
        children = [(name, True) for name in directories]
        children.extend((name, False) for name in files)
        for index in range(len(children) - 1, -1, -1):
            name, is_directory = children[index]
            is_last = index == len(children) - 1
            stack.append((row, depth, prefix, name, is_directory, is_last))

    push_children(root, 0, "")
    while stack:
        parent, depth, prefix, name, is_directory, is_last = stack.pop()
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{name}")
        if not is_directory:
            continue
        child = rows.get(_canonical_root(_child_root(parent.root, name)))
        if child is None:
            continue
        continuation = "    " if is_last else "│   "
        push_children(child, depth + 1, prefix + continuation)

    return "\n".join(lines) + "\n"


def _materialize_sync(iterator: Iterator[object]) -> _MaterializationOutcome:
    try:
        values = list(iterator)
    except BaseException as error:  # noqa: BLE001 - cross task boundary as data.
        return _MaterializationError(error)
    return _MaterializedRows(values)


async def _materialize_iterator(iterator: Iterator[object]) -> list[object]:
    outcome = await _drain_current_operation(
        asyncio.to_thread(_materialize_sync, iterator)
    )
    if isinstance(outcome, _MaterializationError):
        raise outcome.error
    return outcome.values


async def _consume_walk(result: object) -> list[object] | None:
    if isinstance(result, AsyncIterator):
        values = []
        while True:
            has_value, value = await _next_async(result)
            if not has_value:
                return values
            values.append(value)
    if not inspect.isawaitable(result):
        return None
    resolved = await _drain_current_operation(result)
    if not isinstance(resolved, Iterator):
        return None
    return await _materialize_iterator(resolved)


async def _next_async(iterator: AsyncIterator[object]) -> tuple[bool, object]:
    try:
        return True, await _drain_current_operation(anext(iterator))
    except StopAsyncIteration:
        return False, None


async def _walk(request: _TreeRequest, filesystem: AsyncFileSystem) -> str | _Failure:
    try:
        result = filesystem._walk(  # noqa: SLF001
            request.operand.path,
            maxdepth=1 if request.maxdepth == 0 else request.maxdepth,
            detail=False,
            on_error="raise",
        )
        values = await _consume_walk(result)
    except Exception as error:  # noqa: BLE001 - invoke/await/iteration boundary.
        return _Failure(request.operand, backend_error=error)
    if values is None:
        return _Failure(request.operand)
    rows = _validated_rows(request, values)
    if rows is None:
        return _Failure(request.operand)
    return _render_tree(request, rows)


async def _run_tree(
    command: str,
    request: _TreeRequest,
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    await _run_single_operand_text(
        command,
        request.operand,
        sources,
        lambda filesystem: _walk(request, filesystem),
    )
