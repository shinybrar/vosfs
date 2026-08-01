"""Verified two-operand recursive ``cp``."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ._command import (
    _backend_category,
    _call,
    _CommandFailureError,
    _MappedOperand,
    _render_operand_diagnostic,
    _usage_error,
)
from ._diagnostics import _render_diagnostic_value
from ._manifest import (
    _MAX_ENTRIES,
    _entry,
    _EntryLimitError,
    _IncompatibleResultError,
    _Manifest,
    _manifest,
    _ManifestEntry,
    _shared_tokens_match,
    _UnsupportedEntryError,
)
from ._path import (
    _has_dot_segment,
    _is_root,
    _is_same_or_descendant,
    _lexical_basename,
    _lexical_join,
    _lexical_parent,
)

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem


@dataclass(frozen=True)
class _RecursiveCpFailure:
    operand: _MappedOperand
    category: str | None = None
    backend_error: Exception | None = None
    residue: bool = False
    rendered: bool = False


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
    return _RecursiveCpFailure(operand, _backend_category(error), backend_error=error)


def _staging_failure(source: _MappedOperand, error: Exception) -> _RecursiveCpFailure:
    rendered_class = _render_diagnostic_value(type(error).__name__)
    return _RecursiveCpFailure(
        source,
        f"staging failure ({rendered_class})",
        backend_error=error,
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
    entry = _classify_existing(operand, operand.path, info, require_name=False)
    if isinstance(entry, _RecursiveCpFailure):
        return entry
    if entry.kind == "file":
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
        return _RecursiveCpFailure(
            operand, "unsupported entry type", backend_error=error
        )
    except _IncompatibleResultError as error:
        return _RecursiveCpFailure(operand, "incompatible result", backend_error=error)
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
        for entry in manifest:
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
                        backend_error=error,
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
                        backend_error=error,
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
            return _RecursiveCpFailure(
                self.source, backend_error=cleanup_error, rendered=True
            )
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
                    backend_error=error,
                    residue=True,
                )

        for entry in manifest:
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
                backend_error=error,
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
            for source_entry in manifest:
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
                        dict(destination_entry.tokens),
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
                backend_error=error,
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
                self.source, "unsupported entry type", backend_error=error
            )
        except _EntryLimitError as error:
            return _RecursiveCpFailure(
                self.source,
                f"source tree exceeds {_MAX_ENTRIES} entries",
                backend_error=error,
            )
        except _IncompatibleResultError as error:
            return _RecursiveCpFailure(
                self.source, "incompatible result", backend_error=error
            )
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
                error=failure.backend_error,
                render=False,
                propagate=error,
            ) from error
        raise _CommandFailureError(error=failure.backend_error, render=False)
