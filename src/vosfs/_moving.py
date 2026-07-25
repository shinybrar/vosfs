"""Client-derived move over the OpenCADC VOSpace profile.

The service exposes no atomic rename, so a move is four explicit phases:

1. **plan** — resolve every policy question before touching the destination;
2. **copy** — recreate the source tree at the destination;
3. **verify** — prove each destination exists with the expected type and size;
4. **delete** — only then remove the sources, leaves-first.

Deletion never runs on unverified state. A failure after step 2 keeps *both*
paths and reports which entries completed, because a move that has copied but
not deleted is recoverable while one that deleted without copying is not.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vosfs import errors

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vosfs.filesystem import VOSpaceFileSystem


@dataclass(frozen=True)
class MoveEntry:
    """One source, destination, and verification expectation."""

    source: str
    destination: str
    kind: str
    size: int


@dataclass(frozen=True)
class MovePlan:
    """A preflighted client-derived move with no unresolved policy."""

    source: str
    destination: str
    entries: tuple[MoveEntry, ...]
    recursive: bool
    maxdepth: int | None


@dataclass(frozen=True)
class MoveResult:
    """Destination verification outcome for one move plan."""

    completed: tuple[str, ...]
    failed: tuple[str, ...]


async def plan(  # noqa: PLR0913 - one parameter per resolved move policy.
    filesystem: VOSpaceFileSystem,
    path1: str,
    path2: str,
    *,
    recursive: bool,
    maxdepth: int | None,
    file_only: bool,
) -> MovePlan:
    """Resolve all move policy before any destination mutation."""
    source = filesystem._strip_protocol(path1)
    destination = filesystem._strip_protocol(path2)
    source_info = await filesystem._info(source)
    if source_info.get("islink"):
        msg = "moving a LinkNode is unsupported"
        raise NotImplementedError(msg)
    if file_only and source_info["type"] == "directory":
        raise IsADirectoryError(errno.EISDIR, "move source is a container", source)
    if source == destination:
        if source_info["type"] == "directory":
            msg = f"move destination already exists: {destination}"
            raise FileExistsError(msg)
        return MovePlan(
            source=source,
            destination=destination,
            entries=(),
            recursive=False,
            maxdepth=maxdepth,
        )
    if not file_only and (source == "/" or destination.startswith(f"{source}/")):
        msg = f"move destination is within the source: {destination}"
        raise ValueError(msg)
    recursive = recursive or source_info["type"] == "directory"
    source_paths = await filesystem._expand_path(
        source,
        recursive=recursive,
        maxdepth=maxdepth,
    )
    source_manifest = [
        (
            path,
            source_info if path == source else await filesystem._info(path),
        )
        for path in source_paths
    ]
    if any(info.get("islink") for _path, info in source_manifest):
        msg = "moving a LinkNode is unsupported"
        raise NotImplementedError(msg)
    if await filesystem._exists(destination):
        msg = f"move destination already exists: {destination}"
        raise FileExistsError(msg)
    destination_paths = filesystem._coordinator.remap(source_paths, destination)
    entries = tuple(
        MoveEntry(
            source=path,
            destination=copied_path,
            kind=info["type"],
            size=int(info["size"]),
        )
        for (path, info), copied_path in zip(
            source_manifest,
            destination_paths,
            strict=True,
        )
    )
    return MovePlan(source, destination, entries, recursive, maxdepth)


async def execute(
    filesystem: VOSpaceFileSystem,
    move_plan: MovePlan,
    **kwargs: Any,  # noqa: ANN401 - fsspec forwards copy options
) -> MoveResult:
    """Copy, verify, and only then delete one preflighted move plan."""
    if not move_plan.entries:
        return MoveResult((), ())
    kwargs.pop("on_error", None)
    try:
        await filesystem._copy(
            [entry.source for entry in move_plan.entries],
            [entry.destination for entry in move_plan.entries],
            recursive=move_plan.recursive,
            maxdepth=move_plan.maxdepth,
            on_error="raise",
            **kwargs,
        )
    except Exception as exc:
        result = await verify_destinations(filesystem, move_plan)
        filesystem._invalidate(move_plan.destination)
        msg = (
            f"move copy failed ({len(result.completed)} completed, "
            f"{len(result.failed)} failed)"
        )
        raise errors.VOSpaceError(
            msg,
            completed=list(result.completed),
            failed=list(result.failed),
        ) from exc
    result = await verify_destinations(filesystem, move_plan)
    if result.failed:
        filesystem._invalidate(move_plan.destination)
        msg = (
            f"move copy is incomplete ({len(result.completed)} completed, "
            f"{len(result.failed)} failed); source is kept"
        )
        raise errors.VOSpaceError(
            msg,
            completed=list(result.completed),
            failed=list(result.failed),
        )
    await remove_sources(
        filesystem,
        move_plan.entries,
        allow_nonempty=move_plan.maxdepth is not None,
    )
    filesystem._invalidate(move_plan.source)
    filesystem._invalidate(move_plan.destination)
    return result


async def verify_destinations(
    filesystem: VOSpaceFileSystem,
    move_plan: MovePlan,
) -> MoveResult:
    """Return destination paths whose type and file size did or did not verify."""
    completed: list[str] = []
    failed: list[str] = []
    for entry in move_plan.entries:
        try:
            copied = await filesystem._info(entry.destination)
        except OSError:
            copied = None
        type_matches = copied is not None and copied["type"] == entry.kind
        size_matches = type_matches and (
            entry.kind == "directory" or copied["size"] == entry.size
        )
        if size_matches:
            completed.append(entry.destination)
        else:
            failed.append(entry.destination)
    return MoveResult(tuple(completed), tuple(failed))


async def remove_sources(
    filesystem: VOSpaceFileSystem,
    entries: Sequence[MoveEntry],
    *,
    allow_nonempty: bool,
) -> None:
    """Remove verified moved entries leaves-first, retaining bounded descendants."""
    completed: list[str] = []
    for entry in sorted(
        entries,
        key=lambda item: item.source.count("/"),
        reverse=True,
    ):
        path = entry.source
        try:
            await filesystem._rm_one(path, recursive=False)
        except OSError as exc:
            if allow_nonempty and exc.errno == errno.ENOTEMPTY:
                continue
            filesystem._invalidate(path)
            msg = f"move source deletion failed ({len(completed)} completed, 1 failed)"
            raise errors.VOSpaceError(
                msg,
                completed=completed,
                failed=[path],
            ) from exc
        completed.append(path)
