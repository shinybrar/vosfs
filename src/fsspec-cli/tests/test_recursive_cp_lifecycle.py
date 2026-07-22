"""Recursive ``cp`` lifecycle and source-manager tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import MethodType
from typing import TYPE_CHECKING, NoReturn

import pytest

from .test_recursive_cp import _invoke, _source, _TreeFileSystem

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


@pytest.mark.parametrize("name", [".", ".."])
def test_recursive_cp_rejects_walk_dot_segments_before_mutation(name: str) -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        f"/docs/{name}": None,
        "/out": None,
    }
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, calls)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/docs: incompatible result\n",
    )
    assert not [call for call in calls if call[0] in {"mkdir", "put_file"}]


@pytest.mark.parametrize(
    "tokens",
    [
        {"checksum": 7},
        {"ETag": "one", "etag": "two"},
        {"content-md5": "one", "content_md5": "two"},
    ],
)
def test_recursive_cp_rejects_invalid_recognized_tokens(
    tokens: dict[str, object],
) -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
        "/out": None,
    }
    metadata = {
        "/docs/file": {
            "name": "/docs/file",
            "type": "file",
            "size": 1,
            **tokens,
        }
    }

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, [], metadata=metadata)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/docs: incompatible result\n",
    )


def test_recursive_cp_closes_late_sync_walk_iterator_before_source_exit() -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/out": None,
    }
    events: list[str] = []

    class Rows:
        def __iter__(self):
            return self

        def __next__(self) -> object:
            raise StopIteration

        def close(self) -> None:
            events.append("iterator close")

    @asynccontextmanager
    async def source():
        filesystem = _TreeFileSystem(entries, [])
        owner = asyncio.current_task()
        assert owner is not None

        async def walk(
            self: _TreeFileSystem,
            path: str,
            *,
            detail: bool,
            on_error: str,
            **kwargs: object,
        ) -> Rows:
            del self, path, detail, on_error, kwargs
            owner.cancel()
            await asyncio.sleep(0)
            return Rows()

        filesystem._walk = MethodType(walk, filesystem)  # type: ignore[method-assign]
        try:
            yield filesystem
        finally:
            events.append("source exit")

    with pytest.raises(asyncio.CancelledError):
        _invoke(
            ["-R", "memory:/docs", "memory:/out/copy"],
            {"memory": source},
        )

    assert events == ["iterator close", "source exit"]


def test_recursive_cp_cleans_staging_when_primary_rendering_escapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop(BaseException):
        pass

    control = Stop()
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    temporary_paths: list[str] = []

    def configure(filesystem: _TreeFileSystem) -> None:
        async def get_file(
            self: _TreeFileSystem,
            remote: str,
            local: str,
            **kwargs: object,
        ) -> NoReturn:
            del self, remote, kwargs
            temporary_paths.append(local)
            Path(local).write_bytes(b"partial")  # noqa: ASYNC240
            message = "download"
            raise OSError(message)

        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    def fail_render(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise control

    monkeypatch.setattr("fsspec_cli._recursive_cp._render_failure", fail_render)
    with pytest.raises(Stop) as caught:
        _invoke(
            ["-R", "source:/docs", "destination:/out/copy"],
            {
                "source": _source(source_entries, [], configure=configure),
                "destination": _source(destination_entries, []),
            },
        )

    assert caught.value is control
    assert len(temporary_paths) == 1
    assert not Path(temporary_paths[0]).exists()


def test_recursive_cp_cleans_staging_before_reverse_exits_during_control_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop(BaseException):
        pass

    control = Stop()
    events: list[str] = []
    temporary_paths: list[str] = []
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}

    def configure(filesystem: _TreeFileSystem) -> None:
        async def get_file(
            self: _TreeFileSystem,
            remote: str,
            local: str,
            **kwargs: object,
        ) -> NoReturn:
            del self, remote, kwargs
            temporary_paths.append(local)
            Path(local).write_bytes(b"partial")  # noqa: ASYNC240
            raise control

        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    def managed_source(
        name: str,
        entries: dict[str, bytes | None],
        *,
        configure_source: Callable[[_TreeFileSystem], None] | None = None,
    ):
        @asynccontextmanager
        async def source():
            filesystem = _TreeFileSystem(entries, [])
            if configure_source is not None:
                configure_source(filesystem)
            try:
                yield filesystem
            finally:
                events.append(f"{name} exit")

        return source

    real_unlink = Path.unlink

    def recording_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if "fsspec-cli-cp-recursive-" in path.name:
            events.append("staging cleanup")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", recording_unlink)
    with pytest.raises(Stop) as caught:
        _invoke(
            ["-R", "source:/docs", "destination:/out/copy"],
            {
                "source": managed_source(
                    "source",
                    source_entries,
                    configure_source=configure,
                ),
                "destination": managed_source(
                    "destination",
                    destination_entries,
                ),
            },
        )

    assert caught.value is control
    assert events == ["staging cleanup", "destination exit", "source exit"]
    assert len(temporary_paths) == 1
    assert not Path(temporary_paths[0]).exists()


def test_recursive_cp_passes_operation_failure_to_reverse_exits_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_error = OSError("download")
    events: list[str] = []
    exit_calls: list[tuple[str, BaseException]] = []
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}

    def configure(filesystem: _TreeFileSystem) -> None:
        async def get_file(
            self: _TreeFileSystem,
            remote: str,
            local: str,
            **kwargs: object,
        ) -> NoReturn:
            del self, remote, local, kwargs
            raise operation_error

        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    def managed_source(
        name: str,
        entries: dict[str, bytes | None],
        *,
        configure_source: Callable[[_TreeFileSystem], None] | None = None,
    ) -> object:
        @asynccontextmanager
        async def source() -> AsyncIterator[_TreeFileSystem]:
            filesystem = _TreeFileSystem(entries, [])
            if configure_source is not None:
                configure_source(filesystem)
            try:
                yield filesystem
            except BaseException as error:
                exit_calls.append((name, error))
                events.append(f"{name} exit")
                raise

        return source

    real_unlink = Path.unlink

    def recording_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if "fsspec-cli-cp-recursive-" in path.name:
            events.append("staging cleanup")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", recording_unlink)
    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": managed_source(
                "source",
                source_entries,
                configure_source=configure,
            ),
            "destination": managed_source("destination", destination_entries),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: transfer failure; destination residue may remain\n",
    )
    assert events == ["staging cleanup", "destination exit", "source exit"]
    assert [name for name, _ in exit_calls] == ["destination", "source"]
    for _, error in exit_calls:
        assert error is operation_error


def test_recursive_cp_renders_not_a_directory_os_error_as_backend_failure() -> None:
    entries: dict[str, bytes | None] = {"/": None, "/docs": None, "/out": None}
    metadata = {"/docs": NotADirectoryError("backend-specific")}

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, [], metadata=metadata)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/docs: backend failure (NotADirectoryError): backend-specific\n",
    )


def test_recursive_cp_reports_incompatible_source_context_manager() -> None:
    def source() -> object:
        return object()

    result = _invoke(
        ["-R", "broken:/docs", "broken:/out"],
        {"broken": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: broken: source factory returned incompatible async context manager\n",
    )


def test_recursive_cp_reports_source_entry_failure() -> None:
    class Manager:
        async def __aenter__(self) -> NoReturn:
            message = "entry"
            raise ValueError(message)

        async def __aexit__(self, *args: object) -> None:
            del args

    result = _invoke(
        ["-R", "broken:/docs", "broken:/out"],
        {"broken": Manager},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: broken: source entry failure (ValueError): entry\n",
    )


def test_recursive_cp_reports_incompatible_yielded_filesystem() -> None:
    @asynccontextmanager
    async def source():
        yield object()

    result = _invoke(
        ["-R", "broken:/docs", "broken:/out"],
        {"broken": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: broken: source yielded incompatible async filesystem\n",
    )


def test_recursive_cp_reports_reverse_source_exit_failures() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}

    def exiting_source(
        entries: dict[str, bytes | None],
        error: Exception,
    ):
        @asynccontextmanager
        async def source():
            yield _TreeFileSystem(entries, [])
            raise error

        return source

    result = _invoke(
        ["-R", "alpha:/docs", "beta:/out/copy"],
        {
            "alpha": exiting_source(source_entries, OSError("alpha exit")),
            "beta": exiting_source(
                destination_entries,
                RuntimeError("beta exit"),
            ),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: beta: source exit failure (RuntimeError): beta exit\n"
        "cp: alpha: source exit failure (OSError): alpha exit\n",
    )
    assert destination_entries["/out/copy/file"] == b"x"


def test_recursive_cp_keeps_acquisition_failure_primary_before_exit_failure() -> None:
    @asynccontextmanager
    async def alpha():
        try:
            yield _TreeFileSystem({"/": None, "/docs": None}, [])
        finally:
            message = "alpha exit"
            raise OSError(message)

    def beta() -> NoReturn:
        message = "beta factory"
        raise ValueError(message)

    result = _invoke(
        ["-R", "alpha:/docs", "beta:/out"],
        {"alpha": alpha, "beta": beta},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: beta: source factory failure (ValueError): beta factory\n"
        "cp: alpha: source exit failure (OSError): alpha exit\n",
    )


@pytest.mark.parametrize("phase", ["invocation", "await", "iteration"])
def test_recursive_cp_classifies_walk_failures(phase: str) -> None:
    entries: dict[str, bytes | None] = {"/": None, "/docs": None, "/out": None}

    def configure(filesystem: _TreeFileSystem) -> None:
        if phase == "invocation":

            def walk(
                self: _TreeFileSystem, *args: object, **kwargs: object
            ) -> NoReturn:
                del self, args, kwargs
                raise OSError(phase)

        elif phase == "await":

            async def walk(
                self: _TreeFileSystem,
                *args: object,
                **kwargs: object,
            ) -> NoReturn:
                del self, args, kwargs
                raise OSError(phase)

        else:

            async def walk(  # type: ignore[misc]
                self: _TreeFileSystem,
                *args: object,
                **kwargs: object,
            ):
                del self, args, kwargs
                if False:  # pragma: no cover - makes an async iterator.
                    yield None
                raise OSError(phase)

        filesystem._walk = MethodType(walk, filesystem)  # type: ignore[method-assign, possibly-unbound-attribute]

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, [], configure=configure)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"cp: memory:/docs: backend failure (OSError): {phase}\n",
    )


def test_recursive_cp_closes_failed_sync_walk_before_source_exit() -> None:
    entries: dict[str, bytes | None] = {"/": None, "/docs": None, "/out": None}
    events: list[str] = []

    class Rows:
        def __iter__(self):
            return self

        def __next__(self) -> object:
            message = "iteration"
            raise OSError(message)

        def close(self) -> None:
            events.append("iterator close")

    @asynccontextmanager
    async def source():
        filesystem = _TreeFileSystem(entries, [])

        async def walk(
            self: _TreeFileSystem,
            *args: object,
            **kwargs: object,
        ) -> Rows:
            del self, args, kwargs
            return Rows()

        filesystem._walk = MethodType(walk, filesystem)  # type: ignore[method-assign]
        try:
            yield filesystem
        finally:
            events.append("source exit")

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/docs: backend failure (OSError): iteration\n",
    )
    assert events == ["iterator close", "source exit"]
