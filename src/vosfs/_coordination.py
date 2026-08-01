"""Inherited fsspec coordination over canonical VOSpace paths."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from functools import partial
from typing import TYPE_CHECKING, Any, overload
from urllib.parse import unquote_to_bytes

from fsspec.asyn import AsyncFileSystem
from fsspec.utils import other_paths

from vosfs import paths

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Generator

    from fsspec.callbacks import Callback


class _CanonicalPath(str):
    """A path already decoded at the inherited-fsspec seam."""

    __slots__ = ()


def canonical_path(path: str) -> str:
    """Retain canonical-path provenance across a public fsspec result."""
    if isinstance(path, _CanonicalPath) or unquote_to_bytes(path) == path.encode():
        return path
    return _CanonicalPath(path)


def normalize_path(path: str) -> str:
    """Normalize one user path unless it already crossed this seam."""
    if isinstance(path, _CanonicalPath):
        return path
    return canonical_path(paths.strip_protocol(path))


class WriteState:
    """Operation-scoped upload state and owned child tasks."""

    def __init__(self, owner: object) -> None:
        """Initialize state owned by one bulk write."""
        self.owner = owner
        self.owner_task = asyncio.current_task()
        self.active = True
        self.tasks: set[asyncio.Task[object]] = set()
        self.lock = asyncio.Lock()
        self.materialized: set[str] = set()
        self.failure: Exception | None = None


_WRITE_STATE: contextvars.ContextVar[WriteState | None] = contextvars.ContextVar(
    "vosfs_coordinated_write_state",
    default=None,
)


@contextlib.asynccontextmanager
async def write_scope(owner: object) -> AsyncIterator[None]:
    """Bind one bulk write and drain every child before returning."""
    state = WriteState(owner)
    token = _WRITE_STATE.set(state)
    body_error: BaseException | None = None
    try:
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 - drain before propagation
            body_error = exc
    finally:
        state.active = False
        try:
            cleanup = asyncio.create_task(_drain_write_tasks(state))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError as exc:  # noqa: PERF203 - drain loop
                    body_error = exc
            cleanup.result()
        finally:
            _WRITE_STATE.reset(token)
    if body_error is not None:
        raise body_error


def join_write_scope(owner: object) -> WriteState | None:
    """Return owner state and register the calling child task."""
    state = _WRITE_STATE.get()
    if state is None or state.owner is not owner:
        return None
    task = asyncio.current_task()
    if task is not None and task is not state.owner_task and task not in state.tasks:
        state.tasks.add(task)
        task.add_done_callback(partial(_discard_write_task, state))
    if not state.active:
        raise asyncio.CancelledError
    return state


def _discard_write_task(state: WriteState, task: asyncio.Task[object]) -> None:
    """Retrieve child outcome and release completed task ownership."""
    with contextlib.suppress(asyncio.CancelledError):
        task.exception()
    state.tasks.discard(task)


async def _drain_write_tasks(state: WriteState) -> None:
    """Cancel and await registered children, including late joiners."""
    observed: set[asyncio.Task[object]] | None = None
    while observed != state.tasks:
        observed = set(state.tasks)
        await asyncio.sleep(0)
        registered = list(state.tasks)
        for task in registered:
            if not task.done():
                task.cancel()
        if registered:
            await asyncio.gather(*registered, return_exceptions=True)


class _DeferredAwaitable:
    """Create a callback coroutine only after fsspec schedules its task."""

    __slots__ = ("_factory", "_owner")

    def __init__(
        self,
        owner: object,
        factory: Callable[[], Awaitable[Any]],
    ) -> None:
        self._owner = owner
        self._factory = factory

    def __await__(self) -> Generator[Any, None, Any]:
        join_write_scope(self._owner)
        return self._factory().__await__()


class DeferredBranchCallback:
    """Delegate progress while making each callback prelude task-owned."""

    def __init__(self, callback: Callback, owner: object) -> None:
        """Wrap ``callback`` on behalf of one bulk write owned by ``owner``."""
        self._callback = callback
        self._owner = owner

    def set_size(self, size: int) -> None:
        """Forward the total size to the wrapped callback."""
        self._callback.set_size(size)

    def relative_update(self, inc: int = 1) -> None:
        """Forward a progress increment to the wrapped callback."""
        self._callback.relative_update(inc)

    def branch_coro(
        self,
        function: Callable[..., Awaitable[Any]],
    ) -> Callable[..., _DeferredAwaitable]:
        """Branch ``function`` while deferring its coroutine to its own task."""
        wrapped = self._callback.branch_coro(function)

        def deferred(
            path1: str,
            path2: str,
            **kwargs: Any,  # noqa: ANN401 - fsspec forwards hook options
        ) -> _DeferredAwaitable:
            return _DeferredAwaitable(
                self._owner,
                partial(wrapped, path1, path2, **kwargs),
            )

        return deferred


def normalize_hook_path(path: str) -> str:
    """Normalize one hook-entry path, preserving a trailing slash."""
    normalized = normalize_path(path)
    if path.endswith("/") and normalized != "/":
        return f"{normalized}/"
    return normalized


@overload
def normalize_hook_paths(value: str) -> str: ...


@overload
def normalize_hook_paths(value: list[str]) -> list[str]: ...


def normalize_hook_paths(value: str | list[str]) -> str | list[str]:
    """Normalize one hook-entry path, or each path in a list."""
    if isinstance(value, list):
        return [normalize_hook_path(path) for path in value]
    return normalize_hook_path(value)


def _forward(path: str) -> str:
    """Encode a canonical path for one normal filesystem hook entry."""
    return paths.encode_url_path(path) or "/"


def canonical_info(info: dict[str, Any]) -> dict[str, Any]:
    """Copy one info dict, retaining canonical-path provenance on its name."""
    result = dict(info)
    name = result.get("name")
    if isinstance(name, str):
        result["name"] = canonical_path(name)
    return result


def remap(source_paths: list[str], destination: str) -> list[str]:
    """Map canonical source paths beneath one canonical destination."""
    return [
        canonical_path(path)
        for path in other_paths(source_paths, normalize_hook_path(destination))
    ]


class FsspecAdapter:
    """Run inherited coordinators with canonical paths inside their seam."""

    _expand_path = AsyncFileSystem._expand_path  # noqa: SLF001 - inherited seam
    _exists = AsyncFileSystem._exists  # noqa: SLF001 - inherited seam
    _find = AsyncFileSystem._find  # noqa: SLF001 - inherited seam
    _glob = AsyncFileSystem._glob  # noqa: SLF001 - inherited seam
    _isdir = AsyncFileSystem._isdir  # noqa: SLF001 - inherited seam
    _isfile = AsyncFileSystem._isfile  # noqa: SLF001 - inherited seam
    _walk = AsyncFileSystem._walk  # noqa: SLF001 - inherited seam

    def __init__(self, filesystem: AsyncFileSystem) -> None:
        """Bind the adapter to the filesystem whose hooks it forwards to."""
        self._filesystem = filesystem

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - fsspec hook surface
        """Delegate every unpinned attribute to the wrapped filesystem."""
        return getattr(self._filesystem, name)

    @overload
    @classmethod
    def _strip_protocol(cls, path: str) -> str: ...

    @overload
    @classmethod
    def _strip_protocol(cls, path: list[str]) -> list[str]: ...

    @classmethod
    def _strip_protocol(cls, path: str | list[str]) -> str | list[str]:
        if isinstance(path, list):
            return [cls._strip_protocol(item) for item in path]
        segments = [segment for segment in path.split("/") if segment]
        return canonical_path("/" + "/".join(segments)) if segments else "/"

    async def _info(self, path: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return await self._filesystem._info(_forward(path), **kwargs)  # noqa: SLF001

    async def _ls(
        self,
        path: str,
        detail: bool = True,  # noqa: FBT001, FBT002 - fsspec hook signature
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[Any]:
        return await self._filesystem._ls(  # noqa: SLF001
            _forward(path),
            detail=detail,
            **kwargs,
        )

    async def _get_file(self, rpath: str, lpath: str, **kwargs: Any) -> None:  # noqa: ANN401
        await self._filesystem._get_file(_forward(rpath), lpath, **kwargs)  # noqa: SLF001

    async def _cat_file(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> bytes:
        return await self._filesystem._cat_file(  # noqa: SLF001
            _forward(path),
            start=start,
            end=end,
            **kwargs,
        )

    def _pipe_file(
        self,
        path: str,
        value: bytes,
        mode: str = "overwrite",
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> _DeferredAwaitable:
        return _DeferredAwaitable(
            self._filesystem,
            partial(
                self._filesystem._pipe_file,  # noqa: SLF001
                _forward(path),
                value,
                mode=mode,
                **kwargs,
            ),
        )

    def _put_file(
        self,
        lpath: str,
        rpath: str,
        mode: str = "overwrite",
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> _DeferredAwaitable:
        return _DeferredAwaitable(
            self._filesystem,
            partial(
                self._filesystem._put_file,  # noqa: SLF001
                lpath,
                _forward(rpath),
                mode=mode,
                **kwargs,
            ),
        )

    def _makedirs(
        self,
        path: str,
        exist_ok: bool = False,  # noqa: FBT001, FBT002 - fsspec hook signature
    ) -> _DeferredAwaitable:
        return _DeferredAwaitable(
            self._filesystem,
            partial(
                self._filesystem._makedirs,  # noqa: SLF001
                _forward(path),
                exist_ok=exist_ok,
            ),
        )

    async def _cp_file(
        self,
        path1: str,
        path2: str,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        await self._filesystem._cp_file(  # noqa: SLF001
            _forward(path1),
            _forward(path2),
            **kwargs,
        )
