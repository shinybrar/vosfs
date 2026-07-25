"""Client-derived recursive removal over the OpenCADC VOSpace profile.

The service exposes no recursive ``DELETE`` this client is willing to rely on,
so a tree is removed leaves-first from the client. That makes removal
**sequential and non-atomic**: any failure keeps the confirmed deletions that
already happened and reports them, rather than pretending the tree is gone or
that nothing was touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vosfs import errors

if TYPE_CHECKING:
    from vosfs.filesystem import VOSpaceFileSystem


def removal_error(
    path: str,
    completed: list[str],
    cause: Exception,
) -> errors.VOSpaceError:
    """Return a partial-completion error for a recursive removal failure."""
    return errors.VOSpaceError(
        f"recursive removal failed at {path}: {cause}",
        status=getattr(cause, "status", None),
        fault=getattr(cause, "fault", None),
        retry_after=getattr(cause, "retry_after", None),
        completed=list(completed),
        failed=[path],
    )


async def remove_tree(filesystem: VOSpaceFileSystem, path: str) -> None:
    """Delete a container and its descendants leaves-first, client-side."""
    completed: list[str] = []
    await _remove_subtree(filesystem, path, completed)


async def _remove_subtree(
    filesystem: VOSpaceFileSystem,
    path: str,
    completed: list[str],
) -> None:
    """Delete one validated subtree and retain confirmed partial progress."""
    try:
        children = await filesystem._ls(path, detail=True)
    except Exception as exc:
        raise removal_error(path, completed, exc) from exc
    for child in children:
        child_path = child["name"]
        if child["type"] == "directory":
            await _remove_subtree(filesystem, child_path, completed)
        else:
            await _delete_one(filesystem, child_path, completed)
    await _delete_one(filesystem, path, completed)


async def _delete_one(
    filesystem: VOSpaceFileSystem,
    path: str,
    completed: list[str],
) -> None:
    """Delete one recursive-removal node and record only confirmed success."""
    try:
        await filesystem._delete_node(path)
    except Exception as exc:
        raise removal_error(path, completed, exc) from exc
    completed.append(path)
