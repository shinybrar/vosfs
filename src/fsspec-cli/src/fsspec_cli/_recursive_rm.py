"""Capability-gated, manifest-verified recursive ``rm``."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import typer

from ._diagnostics import _render_diagnostic_prefix, _render_diagnostic_value
from ._sources import _await_current

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from ._command import _MappedOperand

_REQUIRED_HOOKS = ("_info", "_ls", "_rm_file", "_rmdir")


class _IncompatibleManifestError(Exception):
    pass


class _UnsupportedEntryError(Exception):
    pass


@dataclass(frozen=True)
class _ManifestEntry:
    path: str
    kind: str


@dataclass(frozen=True)
class _Manifest:
    root: str
    entries: tuple[_ManifestEntry, ...]


@dataclass(frozen=True)
class _RecursiveRmFailure:
    operand: _MappedOperand
    category: str
    backend_error: Exception | None = None
    root_missing: bool = False


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


def _has_required_hooks(filesystem: AsyncFileSystem) -> bool:
    return all(callable(getattr(filesystem, name, None)) for name in _REQUIRED_HOOKS)


def _normalized_root(path: str) -> str:
    return path.rstrip("/")


def _is_contained(root: str, path: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _has_dot_segment(path: str) -> bool:
    return any(component in {".", ".."} for component in path.split("/"))


def _freeze_mapping(value: object) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise _IncompatibleManifestError
    try:
        return cast("Mapping[object, object]", dict(value))
    except Exception as error:
        raise _IncompatibleManifestError from error


def _root_entry(path: str, value: object) -> _ManifestEntry:
    info = _freeze_mapping(value)
    name = info.get("name")
    if type(name) is not str or name.rstrip("/") != path:
        raise _IncompatibleManifestError
    islink = info.get("islink", False)
    if type(islink) is not bool:
        raise _IncompatibleManifestError
    if islink:
        raise _UnsupportedEntryError
    kind = info.get("type")
    if type(kind) is not str:
        raise _IncompatibleManifestError
    if kind != "directory":
        raise NotADirectoryError(path)
    return _ManifestEntry(path, kind)


def _listed_entry(parent: str, root: str, value: object) -> _ManifestEntry:
    info = _freeze_mapping(value)
    name = info.get("name")
    if type(name) is not str:
        raise _IncompatibleManifestError
    islink = info.get("islink", False)
    if type(islink) is not bool:
        raise _IncompatibleManifestError
    if islink:
        raise _UnsupportedEntryError
    kind = info.get("type")
    if type(kind) is not str:
        raise _IncompatibleManifestError
    if kind not in {"file", "directory"}:
        raise _UnsupportedEntryError
    if (
        not name
        or name.rstrip("/") != name
        or "\0" in name
        or "\n" in name
        or "\r" in name
        or _has_dot_segment(name)
        or not _is_contained(root, name)
        or (name.rpartition("/")[0] or "/") != parent
    ):
        raise _IncompatibleManifestError
    return _ManifestEntry(name, kind)


async def _manifest(
    filesystem: AsyncFileSystem,
    root: str,
    root_info: object,
) -> _Manifest:
    root_entry = _root_entry(root, root_info)
    seen = {root}
    entries: list[_ManifestEntry] = []

    async def visit(directory: str) -> None:
        result = await _call(filesystem, "_ls", directory, detail=True)
        if not isinstance(result, list):
            raise _IncompatibleManifestError
        frozen: list[_ManifestEntry] = []
        expected_length = len(result)
        try:
            for value in result:
                entry = _listed_entry(directory, root, value)
                if entry.path in seen:
                    raise _IncompatibleManifestError  # noqa: TRY301
                seen.add(entry.path)
                frozen.append(entry)
        except (_IncompatibleManifestError, _UnsupportedEntryError):
            raise
        except Exception as error:
            raise _IncompatibleManifestError from error
        if len(result) != expected_length or len(frozen) != expected_length:
            raise _IncompatibleManifestError

        for entry in sorted(frozen, key=lambda item: item.path):
            if entry.kind == "directory":
                await visit(entry.path)
            entries.append(entry)

    await visit(root)
    entries.append(root_entry)
    manifest = _Manifest(root, tuple(entries))
    _revalidate_manifest(manifest)
    return manifest


def _revalidate_manifest(manifest: _Manifest) -> None:
    if not manifest.entries or manifest.entries[-1] != _ManifestEntry(
        manifest.root, "directory"
    ):
        raise _IncompatibleManifestError
    seen: set[str] = set()
    directories = {manifest.root}
    for entry in manifest.entries:
        if entry.path in seen or not _is_contained(manifest.root, entry.path):
            raise _IncompatibleManifestError
        seen.add(entry.path)
        if entry.kind == "directory":
            directories.add(entry.path)
    for entry in manifest.entries[:-1]:
        if (entry.path.rpartition("/")[0] or "/") not in directories:
            raise _IncompatibleManifestError


def _read_failure(
    operand: _MappedOperand,
    error: Exception,
    *,
    root_missing: bool = False,
) -> _RecursiveRmFailure:
    if isinstance(error, FileNotFoundError):
        category = "not found"
    elif isinstance(error, PermissionError):
        category = "permission denied"
    elif isinstance(error, NotADirectoryError):
        category = "not a directory"
    elif isinstance(error, NotImplementedError):
        category = "unsupported operation"
    else:
        rendered_class = _render_diagnostic_value(type(error).__name__)
        rendered_message = _render_diagnostic_value(str(error))
        category = f"backend failure ({rendered_class}): {rendered_message}"
    return _RecursiveRmFailure(
        operand,
        category,
        backend_error=error,
        root_missing=root_missing,
    )


async def _plan(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> _Manifest | _RecursiveRmFailure:
    if not _has_required_hooks(filesystem):
        return _RecursiveRmFailure(operand, "unsupported operation")
    root = _normalized_root(operand.path)
    try:
        root_info = await _call(filesystem, "_info", root)
    except Exception as error:  # noqa: BLE001 - classify read boundary.
        return _read_failure(
            operand,
            error,
            root_missing=isinstance(error, FileNotFoundError),
        )
    try:
        return await _manifest(filesystem, root, root_info)
    except _UnsupportedEntryError:
        return _RecursiveRmFailure(operand, "unsupported operation")
    except _IncompatibleManifestError:
        return _RecursiveRmFailure(operand, "incompatible result")
    except Exception as error:  # noqa: BLE001 - classify planning boundary.
        return _read_failure(operand, error)


async def _mutate(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
    manifest: _Manifest,
) -> _RecursiveRmFailure | None:
    try:
        _revalidate_manifest(manifest)
    except _IncompatibleManifestError:
        return _RecursiveRmFailure(operand, "incompatible result")

    for entry in manifest.entries:
        if not _is_contained(manifest.root, entry.path):
            return _RecursiveRmFailure(operand, "incompatible result")
        operation = "_rm_file" if entry.kind == "file" else "_rmdir"
        try:
            await _call(filesystem, operation, entry.path)
        except Exception as error:  # noqa: BLE001 - mutation may be partial.
            return _RecursiveRmFailure(
                operand,
                "recursive removal incomplete; residue possible",
                backend_error=error,
            )
        try:
            await _call(filesystem, "_info", entry.path)
        except FileNotFoundError:
            continue
        except Exception as error:  # noqa: BLE001 - absence remains uncertain.
            return _RecursiveRmFailure(
                operand,
                "recursive removal incomplete; residue possible",
                backend_error=error,
            )
        return _RecursiveRmFailure(
            operand,
            "recursive removal incomplete; residue possible",
        )
    return None


async def _remove_recursive(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> _RecursiveRmFailure | None:
    plan = await _plan(operand, filesystem)
    if isinstance(plan, _RecursiveRmFailure):
        return plan
    return await _mutate(operand, filesystem, plan)


def _render_recursive_failure(
    command: str,
    failure: _RecursiveRmFailure,
) -> None:
    prefix = _render_diagnostic_prefix(command)
    operand = _render_diagnostic_value(failure.operand.spelling)
    typer.echo(f"{prefix} {operand}: {failure.category}", err=True, color=True)
