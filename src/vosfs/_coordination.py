"""Inherited fsspec coordination over canonical VOSpace paths."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from functools import partial
from typing import TYPE_CHECKING, Any, cast, overload
from urllib.parse import unquote_to_bytes

from fsspec.asyn import AsyncFileSystem
from fsspec.utils import other_paths

from vosfs import paths

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Generator, Mapping

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


class _DeferredBranchCallback:
    """Delegate progress while making each callback prelude task-owned."""

    def __init__(self, callback: Callback, owner: object) -> None:
        self._callback = callback
        self._owner = owner

    def set_size(self, size: int) -> None:
        self._callback.set_size(size)

    def relative_update(self, inc: int = 1) -> None:
        self._callback.relative_update(inc)

    def branch_coro(
        self,
        function: Callable[..., Awaitable[Any]],
    ) -> Callable[..., _DeferredAwaitable]:
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


def _normalize(path: str) -> str:
    normalized = normalize_path(path)
    if path.endswith("/") and normalized != "/":
        return f"{normalized}/"
    return normalized


@overload
def _normalize_paths(value: str) -> str: ...


@overload
def _normalize_paths(value: list[str]) -> list[str]: ...


def _normalize_paths(value: str | list[str]) -> str | list[str]:
    if isinstance(value, list):
        return [_normalize(path) for path in value]
    return _normalize(value)


def _forward(path: str) -> str:
    """Encode a canonical path for one normal filesystem hook entry."""
    return paths.encode_url_path(path) or "/"


def _canonical_info(info: dict[str, Any]) -> dict[str, Any]:
    result = dict(info)
    name = result.get("name")
    if isinstance(name, str):
        result["name"] = canonical_path(name)
    return result


class _FsspecAdapter:
    """Run inherited coordinators with canonical paths inside their seam."""

    _expand_path = AsyncFileSystem._expand_path  # noqa: SLF001 - inherited seam
    _exists = AsyncFileSystem._exists  # noqa: SLF001 - inherited seam
    _find = AsyncFileSystem._find  # noqa: SLF001 - inherited seam
    _glob = AsyncFileSystem._glob  # noqa: SLF001 - inherited seam
    _isdir = AsyncFileSystem._isdir  # noqa: SLF001 - inherited seam
    _isfile = AsyncFileSystem._isfile  # noqa: SLF001 - inherited seam
    _walk = AsyncFileSystem._walk  # noqa: SLF001 - inherited seam

    def __init__(self, filesystem: AsyncFileSystem) -> None:
        self._filesystem = filesystem

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - fsspec hook surface
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


class FsspecCoordinator:
    """Own inherited scheduling, remapping, and canonical path provenance."""

    def __init__(self, filesystem: AsyncFileSystem) -> None:
        """Bind one filesystem to its internal fsspec adapter."""
        self._filesystem = filesystem
        self._adapter = cast("AsyncFileSystem", _FsspecAdapter(filesystem))

    async def expand_path(
        self,
        path: str | list[str],
        *,
        recursive: bool,
        maxdepth: int | None,
        assume_literal: bool,
    ) -> list[str]:
        """Expand raw paths while retaining canonical coordinator results."""
        expanded = await AsyncFileSystem._expand_path(  # noqa: SLF001
            self._adapter,
            _normalize_paths(path),
            recursive=recursive,
            maxdepth=maxdepth,
            assume_literal=assume_literal,
        )
        return [canonical_path(item) for item in expanded]

    async def find(
        self,
        path: str,
        *,
        maxdepth: int | None,
        withdirs: bool,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[str] | dict[str, dict[str, Any]]:
        """Find entries without re-normalizing traversal results."""
        result = await AsyncFileSystem._find(  # noqa: SLF001
            self._adapter,
            _normalize(path),
            maxdepth=maxdepth,
            withdirs=withdirs,
            **kwargs,
        )
        if isinstance(result, dict):
            return {
                canonical_path(key): _canonical_info(info)
                for key, info in result.items()
            }
        return [canonical_path(item) for item in result]

    async def glob(
        self,
        path: str,
        *,
        maxdepth: int | None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[str] | dict[str, dict[str, Any]]:
        """Expand a raw glob while retaining canonical matches."""
        result = await AsyncFileSystem._glob(  # noqa: SLF001
            self._adapter,
            _normalize(path),
            maxdepth=maxdepth,
            **kwargs,
        )
        if isinstance(result, dict):
            return {
                canonical_path(key): _canonical_info(info)
                for key, info in result.items()
            }
        return [canonical_path(item) for item in result]

    async def walk(
        self,
        path: str,
        *,
        maxdepth: int | None,
        on_error: str | Callable[[OSError], None],
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> AsyncIterator[Any]:
        """Walk a raw path while retaining canonical descendants."""
        async for item in self._adapter._walk(  # noqa: SLF001
            _normalize(path),
            maxdepth=maxdepth,
            on_error=on_error,
            **kwargs,
        ):
            root, directories, files = item
            if isinstance(directories, dict):
                directories = {
                    name: _canonical_info(info) for name, info in directories.items()
                }
                files = {name: _canonical_info(info) for name, info in files.items()}
            yield canonical_path(root), directories, files

    async def cat(
        self,
        path: str | list[str],
        *,
        recursive: bool,
        on_error: str,
        batch_size: int | None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> bytes | dict[str, bytes | BaseException]:
        """Read scalar or expanded paths through canonical hook forwarding."""
        result = await AsyncFileSystem._cat(  # noqa: SLF001
            self._adapter,
            _normalize_paths(path),
            recursive=recursive,
            on_error=on_error,
            batch_size=batch_size,
            **kwargs,
        )
        if isinstance(result, dict):
            return {canonical_path(key): value for key, value in result.items()}
        return result

    async def du(
        self,
        path: str,
        *,
        total: bool,
        maxdepth: int | None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> int | dict[str, int]:
        """Measure a tree through canonical find and info calls."""
        result = await AsyncFileSystem._du(  # noqa: SLF001
            self._adapter,
            _normalize(path),
            total=total,
            maxdepth=maxdepth,
            **kwargs,
        )
        if isinstance(result, dict):
            return {canonical_path(key): value for key, value in result.items()}
        return result

    async def get(
        self,
        rpath: str | list[str],
        lpath: str | list[str],
        *,
        recursive: bool,
        callback: Callback,
        maxdepth: int | None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[Any] | None:
        """Download paths through canonical traversal and file hooks."""
        return await AsyncFileSystem._get(  # noqa: SLF001
            self._adapter,
            _normalize_paths(rpath),
            lpath,
            recursive=recursive,
            callback=callback,
            maxdepth=maxdepth,
            **kwargs,
        )

    async def put(  # noqa: PLR0913 - inherited fsspec hook signature
        self,
        lpath: str | list[str],
        rpath: str | list[str],
        *,
        recursive: bool,
        callback: Callback,
        batch_size: int | None,
        maxdepth: int | None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[Any] | None:
        """Upload paths within one owned write scope."""
        async with write_scope(self._filesystem):
            return await AsyncFileSystem._put(  # noqa: SLF001
                self._adapter,
                lpath,
                _normalize_paths(rpath),
                recursive=recursive,
                callback=cast(
                    "Callback", _DeferredBranchCallback(callback, self._filesystem)
                ),
                batch_size=batch_size,
                maxdepth=maxdepth,
                **kwargs,
            )

    async def pipe(
        self,
        path: str | Mapping[str, bytes],
        value: bytes | None,
        *,
        batch_size: int | None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> list[Any] | None:
        """Write byte values within one owned write scope."""
        normalized: str | dict[str, bytes]
        if isinstance(path, str):
            normalized = _normalize(path)
        else:
            normalized = {_normalize(key): item for key, item in path.items()}
        async with write_scope(self._filesystem):
            return await AsyncFileSystem._pipe(  # noqa: SLF001
                self._adapter,
                normalized,
                value=value,
                batch_size=batch_size,
                **kwargs,
            )

    async def copy(  # noqa: PLR0913 - inherited fsspec hook signature
        self,
        path1: str | list[str],
        path2: str | list[str],
        *,
        recursive: bool,
        on_error: str | None,
        maxdepth: int | None,
        batch_size: int | None,
        **kwargs: Any,  # noqa: ANN401 - fsspec hook signature
    ) -> None:
        """Copy paths through canonical traversal and copy hooks."""
        await AsyncFileSystem._copy(  # noqa: SLF001
            self._adapter,
            _normalize_paths(path1),
            _normalize_paths(path2),
            recursive=recursive,
            on_error=on_error,
            maxdepth=maxdepth,
            batch_size=batch_size,
            **kwargs,
        )

    def remap(self, source_paths: list[str], destination: str) -> list[str]:
        """Map canonical source paths beneath one canonical destination."""
        return [
            canonical_path(path)
            for path in other_paths(source_paths, _normalize(destination))
        ]
