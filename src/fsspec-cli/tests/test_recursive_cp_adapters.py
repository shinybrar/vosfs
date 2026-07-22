"""Backend-neutral recursive-copy evidence through the public ``App`` seam."""

from __future__ import annotations

import ast
from contextlib import asynccontextmanager
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn

import pytest
from fsspec import AbstractFileSystem
from fsspec.asyn import AsyncFileSystem
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec_cli import App
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def _metadata(entries: dict[str, bytes | None], path: str) -> dict[str, object]:
    if path not in entries:
        raise FileNotFoundError(path)
    payload = entries[path]
    return {
        "name": path,
        "type": "directory" if payload is None else "file",
        "size": 0 if payload is None else len(payload),
        "islink": False,
        "opaque-backend-field": {"arbitrary": path},
    }


def _listing(
    entries: dict[str, bytes | None],
    path: str,
) -> list[dict[str, object]]:
    prefix = path.rstrip("/")
    children = []
    for candidate in sorted(entries):
        if candidate == path or not candidate.startswith(f"{prefix}/"):
            continue
        if "/" in candidate[len(prefix) + 1 :]:
            continue
        children.append(_metadata(entries, candidate))
    return children


def _walk_rows(
    entries: dict[str, bytes | None],
    path: str,
) -> tuple[object, ...]:
    pending = [path]
    rows = []
    while pending:
        root = pending.pop(0)
        directories: dict[str, object] = {}
        files: dict[str, object] = {}
        for info in _listing(entries, root):
            name = str(info["name"]).rsplit("/", 1)[-1]
            if info["type"] == "directory":
                directories[name] = info
                pending.append(str(info["name"]))
            else:
                files[name] = info
        rows.append((root, directories, files))
    return tuple(rows)


class _NativeAdapter(AsyncFileSystem):
    cachable = False

    def __init__(
        self,
        entries: dict[str, bytes | None],
        events: list[tuple[object, ...]],
        *,
        walk_form: Literal["async-generator", "awaitable"] = "async-generator",
        walk_failure: Exception | None = None,
    ) -> None:
        super().__init__(asynchronous=True)
        self.entries = entries
        self.events = events
        self.walk_form = walk_form
        self.walk_failure = walk_failure

    async def _info(self, path: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        self.events.append(("info", path))
        return _metadata(self.entries, path)

    def _walk(
        self,
        path: str,
        *,
        detail: bool,
        on_error: str,
        **kwargs: object,
    ) -> AsyncIterator[object] | object:
        del kwargs
        self.events.append(("walk", path, detail, on_error))
        if self.walk_failure is not None:
            raise self.walk_failure
        rows = _walk_rows(self.entries, path)
        if self.walk_form == "awaitable":

            async def resolve() -> Iterator[object]:
                return iter(rows)

            return resolve()

        async def generate() -> AsyncIterator[object]:
            for row in rows:
                yield row

        return generate()

    async def _mkdir(
        self,
        path: str,
        create_parents: bool = True,  # noqa: FBT002 - fsspec hook signature.
        **kwargs: object,
    ) -> None:
        del kwargs
        self.events.append(("mkdir", path, create_parents))
        self.entries[path] = None

    async def _get_file(self, remote: str, local: str, **kwargs: object) -> None:
        del kwargs
        self.events.append(("get_file", remote))
        payload = self.entries[remote]
        assert isinstance(payload, bytes)
        Path(local).write_bytes(payload)  # noqa: ASYNC240

    async def _put_file(
        self,
        local: str,
        remote: str,
        mode: str = "overwrite",
        **kwargs: object,
    ) -> None:
        del kwargs
        self.events.append(("put_file", remote, mode))
        self.entries[remote] = Path(local).read_bytes()  # noqa: ASYNC240

    async def _forbidden(self, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        message = "forbidden recursive-copy operation"
        raise AssertionError(message)

    _copy = _forbidden
    _cp_file = _forbidden
    _rm = _forbidden
    _rm_file = _forbidden
    _rmdir = _forbidden

    def info(self, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        message = "public sync facade called"
        raise AssertionError(message)

    walk = info
    mkdir = info
    get_file = info
    put_file = info
    copy = info
    cp_file = info
    rm = info


class _MissingWalkAdapter(_NativeAdapter):
    _walk = None  # type: ignore[assignment]


class _SyncAdapter(AbstractFileSystem):
    cachable = False

    def __init__(
        self,
        entries: dict[str, bytes | None],
        events: list[tuple[object, ...]],
    ) -> None:
        super().__init__(skip_instance_cache=True)
        self.entries = entries
        self.events = events

    def info(self, path: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        self.events.append(("info", path))
        return _metadata(self.entries, path)

    def ls(
        self,
        path: str,
        detail: bool = True,  # noqa: FBT002 - fsspec hook signature.
        **kwargs: object,
    ) -> list[object]:
        del kwargs
        self.events.append(("ls", path, detail))
        listing = _listing(self.entries, path)
        return listing if detail else [entry["name"] for entry in listing]

    def mkdir(
        self,
        path: str,
        create_parents: bool = True,  # noqa: FBT002 - fsspec hook signature.
        **kwargs: object,
    ) -> None:
        del kwargs
        self.events.append(("mkdir", path, create_parents))
        self.entries[path] = None

    def get_file(self, remote: str, local: str, **kwargs: object) -> None:
        del kwargs
        self.events.append(("get_file", remote))
        payload = self.entries[remote]
        assert isinstance(payload, bytes)
        Path(local).write_bytes(payload)

    def put_file(
        self,
        local: str,
        remote: str,
        mode: str = "overwrite",
        **kwargs: object,
    ) -> None:
        del kwargs
        self.events.append(("put_file", remote, mode))
        self.entries[remote] = Path(local).read_bytes()

    def _forbidden(self, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        message = "forbidden recursive-copy operation"
        raise AssertionError(message)

    copy = _forbidden
    cp_file = _forbidden
    rm = _forbidden


def _native_source(
    entries: dict[str, bytes | None],
    events: list[tuple[object, ...]],
    *,
    walk_form: Literal["async-generator", "awaitable"] = "async-generator",
    walk_failure: Exception | None = None,
    missing_walk: bool = False,
):
    @asynccontextmanager
    async def source():
        adapter = (
            _MissingWalkAdapter(entries, events)
            if missing_walk
            else _NativeAdapter(
                entries,
                events,
                walk_form=walk_form,
                walk_failure=walk_failure,
            )
        )
        yield adapter

    return source


def _sync_source(
    entries: dict[str, bytes | None],
    events: list[tuple[object, ...]],
):
    @asynccontextmanager
    async def source():
        yield AsyncFileSystemWrapper(
            _SyncAdapter(entries, events),
            asynchronous=True,
        )

    return source


@pytest.mark.parametrize("direction", ["native-to-sync", "sync-to-native"])
def test_backend_neutral_harness_copies_between_minimal_adapters(
    direction: str,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/dataset": None,
        "/dataset/empty": None,
        "/dataset/file.bin": b"payload",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/landing": None}
    source_events: list[tuple[object, ...]] = []
    destination_events: list[tuple[object, ...]] = []
    if direction == "native-to-sync":
        source = _native_source(source_entries, source_events)
        destination = _sync_source(destination_entries, destination_events)
    else:
        source = _sync_source(source_entries, source_events)
        destination = _native_source(destination_entries, destination_events)

    result = CliRunner().invoke(
        App({"nebula": source, "quartz": destination}).typer_app,
        ["cp", "-R", "nebula:/dataset", "quartz:/landing/copy"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert destination_entries["/landing/copy/file.bin"] == b"payload"
    assert destination_entries["/landing/copy/empty"] is None
    assert not [event for event in source_events if event[0] in {"mkdir", "put_file"}]
    assert not [event for event in destination_events if event[0] == "get_file"]


def test_backend_neutral_harness_accepts_awaitable_sync_iterator_walk() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/dataset": None,
        "/dataset/file.bin": b"payload",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/landing": None}

    result = CliRunner().invoke(
        App(
            {
                "arbitrary-source": _native_source(
                    source_entries,
                    [],
                    walk_form="awaitable",
                ),
                "arbitrary-target": _native_source(destination_entries, []),
            }
        ).typer_app,
        [
            "cp",
            "-r",
            "arbitrary-source:/dataset",
            "arbitrary-target:/landing/copy",
        ],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert destination_entries["/landing/copy/file.bin"] == b"payload"


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        ("read-failure", "permission denied"),
        ("missing-walk", "unsupported operation"),
    ],
)
def test_backend_neutral_read_phase_failures_are_stable_and_pre_mutation(
    source: str,
    diagnostic: str,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/dataset": None,
        "/dataset/file.bin": b"payload",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/landing": None}
    destination_events: list[tuple[object, ...]] = []
    source_factory = _native_source(
        source_entries,
        [],
        walk_failure=PermissionError("denied") if source == "read-failure" else None,
        missing_walk=source == "missing-walk",
    )

    result = CliRunner().invoke(
        App(
            {
                "source": source_factory,
                "destination": _native_source(
                    destination_entries,
                    destination_events,
                ),
            }
        ).typer_app,
        ["cp", "-R", "source:/dataset", "destination:/landing/copy"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"cp: source:/dataset: {diagnostic}\n",
    )
    assert not [
        event for event in destination_events if event[0] in {"mkdir", "put_file"}
    ]
    assert "/landing/copy" not in destination_entries


def test_recursive_copy_production_has_no_backend_dispatch_or_sync_facades() -> None:
    from fsspec_cli import _recursive_cp

    source = Path(_recursive_cp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert not {
        "fsspec.implementations.local",
        "fsspec.implementations.memory",
        "fsspec.implementations.asyn_wrapper",
        "vosfs",
    }.intersection(imported_modules)
    assert not {"protocol", "sync_fs"}.intersection(attributes)
    assert not {
        "info",
        "walk",
        "mkdir",
        "get_file",
        "put_file",
        "copy",
        "cp_file",
        "rm",
    }.intersection(called_attributes)
    assert "source_filesystem is destination_filesystem" not in source
    assert "registry" not in source.casefold()

    runner = _recursive_cp._RecursiveCopy
    assert [field.name for field in fields(runner)] == [
        "command",
        "source",
        "destination",
        "source_filesystem",
        "destination_filesystem",
    ]
    assert runner.__dataclass_params__.frozen is True
    assert [
        name
        for name, member in runner.__dict__.items()
        if callable(member) and not name.startswith("_")
    ] == ["run"]
