"""Verified recursive ``cp`` tests through ``App(sources).typer_app``."""

from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import MethodType
from typing import TYPE_CHECKING, NoReturn

import pytest
from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class _TreeFileSystem(AsyncFileSystem):
    cachable = False

    def __init__(
        self,
        entries: dict[str, bytes | None],
        calls: list[tuple[object, ...]],
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(asynchronous=True)
        self.entries = entries
        self.calls = calls
        self.metadata = metadata or {}

    def _metadata(self, path: str) -> dict[str, object]:
        if path in self.metadata:
            scripted = self.metadata[path]
            if isinstance(scripted, BaseException):
                raise scripted
            assert isinstance(scripted, dict)
            return scripted
        if path not in self.entries:
            raise FileNotFoundError(path)
        payload = self.entries[path]
        return {
            "name": path,
            "type": "directory" if payload is None else "file",
            "size": 0 if payload is None else len(payload),
        }

    async def _info(self, path: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        self.calls.append(("info", path))
        return self._metadata(path)

    async def _walk(
        self,
        path: str,
        *,
        detail: bool,
        on_error: str,
        **kwargs: object,
    ) -> AsyncIterator[object]:
        del kwargs
        self.calls.append(("walk", path, detail, on_error))
        pending = [path]
        while pending:
            root = pending.pop(0)
            prefix = f"{root.rstrip('/')}"
            directories: dict[str, object] = {}
            files: dict[str, object] = {}
            for candidate in sorted(self.entries):
                if candidate == root or not candidate.startswith(f"{prefix}/"):
                    continue
                relative = candidate[len(prefix) + 1 :]
                if "/" in relative:
                    continue
                metadata = self._metadata(candidate)
                if metadata["type"] == "directory":
                    directories[relative] = metadata
                    pending.append(candidate)
                else:
                    files[relative] = metadata
            yield root, directories, files

    async def _mkdir(
        self,
        path: str,
        create_parents: bool = True,  # noqa: FBT002 - fsspec hook signature.
        **kwargs: object,
    ) -> None:
        del kwargs
        self.calls.append(("mkdir", path, create_parents))
        self.entries[path] = None

    async def _get_file(self, remote: str, local: str, **kwargs: object) -> None:
        del kwargs
        self.calls.append(("get_file", remote))
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
        self.calls.append(("put_file", remote, mode))
        self.entries[remote] = Path(local).read_bytes()  # noqa: ASYNC240

    async def _cp_file(self, path1: str, path2: str, **kwargs: object) -> None:
        del path1, path2, kwargs
        message = "recursive cp must not call _cp_file"
        raise AssertionError(message)


def _source(
    entries: dict[str, bytes | None],
    calls: list[tuple[object, ...]],
    *,
    metadata: dict[str, object] | None = None,
    configure: Callable[[_TreeFileSystem], None] | None = None,
):
    @asynccontextmanager
    async def source():
        filesystem = _TreeFileSystem(entries, calls, metadata)
        if configure is not None:
            configure(filesystem)
        yield filesystem

    return source


def _invoke(
    arguments: list[str],
    sources: dict[str, object],
):
    return CliRunner().invoke(App(sources).typer_app, ["cp", *arguments])  # type: ignore[arg-type]


def test_recursive_cp_reports_source_factory_failure() -> None:
    def fail_factory() -> NoReturn:
        message = "factory"
        raise ValueError(message)

    result = _invoke(
        ["-R", "broken:/docs", "broken:/out"],
        {"broken": fail_factory},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: broken: source factory failure (ValueError): factory\n",
    )


def test_recursive_cp_reports_source_exit_failure_after_verified_copy() -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/notes.txt": b"notes",
        "/out": None,
    }

    @asynccontextmanager
    async def source():
        yield _TreeFileSystem(entries, [])
        message = "cleanup"
        raise OSError(message)

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out"],
        {"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory: source exit failure (OSError): cleanup\n",
    )
    assert entries["/out/docs/notes.txt"] == b"notes"


def test_recursive_cp_copies_nested_and_empty_directories_through_host_staging() -> (
    None
):
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/empty": None,
        "/docs/nested": None,
        "/docs/nested/notes.txt": b"notes",
        "/target": None,
    }
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "memory:/docs", "memory:/target"],
        {"memory": _source(entries, calls)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert entries["/target/docs/empty"] is None
    assert entries["/target/docs/nested/notes.txt"] == b"notes"
    assert [call[0] for call in calls].count("walk") == 2
    assert [call for call in calls if call[0] in {"mkdir", "get_file", "put_file"}] == [
        ("mkdir", "/target/docs", False),
        ("mkdir", "/target/docs/empty", False),
        ("mkdir", "/target/docs/nested", False),
        ("get_file", "/docs/nested/notes.txt"),
        ("put_file", "/target/docs/nested/notes.txt", "overwrite"),
    ]
    assert not [call for call in calls if call[0] == "cp_file"]


def test_recursive_cp_supports_distinct_configured_sources() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/notes.txt": b"notes",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-r", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, calls),
            "destination": _source(destination_entries, calls),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source_entries["/docs/notes.txt"] == b"notes"
    assert destination_entries["/out/copy/notes.txt"] == b"notes"


def test_recursive_cp_rejects_third_operand_before_source_entry() -> None:
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "memory:/one", "memory:/two", "memory:/three"],
        {"memory": _source({"/": None}, calls)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "cp: extra operand\n",
    )
    assert calls == []


def test_recursive_cp_rejects_destination_inside_source_before_mutation() -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/nested": None,
    }
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "memory:/docs", "memory:/docs/nested"],
        {"memory": _source(entries, calls)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/docs/nested: destination is inside source\n",
    )
    assert not [call for call in calls if call[0] in {"mkdir", "put_file"}]


@pytest.mark.parametrize(
    ("parent_entry", "parent_metadata", "diagnostic"),
    [
        (None, None, "not found"),
        (b"parent", None, "not a directory"),
        (
            None,
            {"name": "/parent", "type": "directory", "islink": True},
            "not a directory",
        ),
    ],
)
def test_recursive_cp_rejects_missing_file_or_link_resolved_parent(
    parent_entry: bytes | None,
    parent_metadata: dict[str, object] | None,
    diagnostic: str,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None}
    metadata = None
    if parent_entry is not None or parent_metadata is not None:
        destination_entries["/parent"] = parent_entry
        metadata = {"/parent": parent_metadata} if parent_metadata is not None else None
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "source:/docs", "destination:/parent/copy"],
        {
            "source": _source(source_entries, calls),
            "destination": _source(
                destination_entries,
                calls,
                metadata=metadata,
            ),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"cp: destination:/parent/copy: {diagnostic}\n",
    )
    assert not [call for call in calls if call[0] in {"walk", "mkdir", "put_file"}]


@pytest.mark.parametrize(
    ("root_entry", "root_metadata", "diagnostic"),
    [
        (b"existing", None, "destination type conflict"),
        (
            None,
            {"name": "/out/copy", "type": "directory", "islink": True},
            "unsupported entry type",
        ),
    ],
)
def test_recursive_cp_rejects_existing_resolved_root_file_or_link(
    root_entry: bytes | None,
    root_metadata: dict[str, object] | None,
    diagnostic: str,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {
        "/": None,
        "/out": None,
        "/out/copy": root_entry,
    }
    metadata = {"/out/copy": root_metadata} if root_metadata is not None else None
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, calls),
            "destination": _source(
                destination_entries,
                calls,
                metadata=metadata,
            ),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"cp: destination:/out/copy: {diagnostic}\n",
    )
    assert not [call for call in calls if call[0] in {"walk", "mkdir", "put_file"}]


def test_recursive_cp_merges_existing_tree_and_replaces_files() -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/empty": None,
        "/docs/notes.txt": b"new",
        "/target": None,
        "/target/docs": None,
        "/target/docs/extra.txt": b"keep",
        "/target/docs/notes.txt": b"old",
    }
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-r", "memory:/docs/", "memory:/target//"],
        {"memory": _source(entries, calls)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert entries["/target/docs/notes.txt"] == b"new"
    assert entries["/target/docs/extra.txt"] == b"keep"
    assert entries["/target/docs/empty"] is None


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        (["-R", "memory:/", "memory:/out"], "cp: memory:/: source root unsupported\n"),
        (
            ["-R", "memory:/docs/../secret", "memory:/out"],
            "cp: memory:/docs/../secret: dot segment unsupported\n",
        ),
        (
            ["-R", "memory:/docs", "memory:/out/./copy"],
            "cp: memory:/out/./copy: dot segment unsupported\n",
        ),
        (
            ["-R", "-r", "memory:/docs", "memory:/out"],
            "cp: -r: unsupported option\n",
        ),
    ],
)
def test_recursive_cp_path_and_option_preflight_is_source_free(
    arguments: list[str],
    diagnostic: str,
) -> None:
    calls: list[tuple[object, ...]] = []

    result = _invoke(arguments, {"memory": _source({"/": None}, calls)})

    assert (result.exit_code, result.stdout, result.stderr) == (2, "", diagnostic)
    assert calls == []


@pytest.mark.parametrize(
    ("metadata", "diagnostic"),
    [
        (
            {"name": "/docs/link", "type": "file", "size": 1, "islink": True},
            "unsupported entry type",
        ),
        (
            {"name": "/docs/link", "type": "other", "size": 1},
            "unsupported entry type",
        ),
        (
            {"name": "/wrong", "type": "file", "size": 1},
            "incompatible result",
        ),
    ],
)
def test_recursive_cp_rejects_manifest_entry_before_mutation(
    metadata: dict[str, object],
    diagnostic: str,
) -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/link": b"x",
        "/out": None,
    }
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, calls, metadata={"/docs/link": metadata})},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"cp: memory:/docs: {diagnostic}\n",
    )
    assert not [call for call in calls if call[0] in {"mkdir", "put_file"}]


def test_recursive_cp_rejects_destination_type_conflict_before_mutation() -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/nested": None,
        "/out": None,
        "/out/copy": None,
        "/out/copy/docs": None,
        "/out/copy/docs/nested": b"file",
    }
    calls: list[tuple[object, ...]] = []

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, calls)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/out/copy: destination type conflict\n",
    )
    assert not [call for call in calls if call[0] in {"mkdir", "put_file"}]


def test_recursive_cp_reports_source_change_after_transfer() -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/notes.txt": b"notes",
        "/out": None,
    }
    calls: list[tuple[object, ...]] = []

    def configure(filesystem: _TreeFileSystem) -> None:
        original = filesystem._put_file

        async def put_file(
            self: _TreeFileSystem,
            local: str,
            remote: str,
            mode: str = "overwrite",
            **kwargs: object,
        ) -> None:
            await original(local, remote, mode, **kwargs)
            self.entries["/docs/notes.txt"] = b"changed"

        filesystem._put_file = MethodType(put_file, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, calls, configure=configure)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/docs: source changed; destination residue may remain\n",
    )
    assert entries["/out/copy/notes.txt"] == b"notes"


def test_recursive_cp_detects_source_mutation_before_transfer() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    source_calls: list[tuple[object, ...]] = []
    destination_calls: list[tuple[object, ...]] = []

    def configure(filesystem: _TreeFileSystem) -> None:
        original = filesystem._info

        async def info(
            self: _TreeFileSystem,
            path: str,
            **kwargs: object,
        ) -> dict[str, object]:
            del self
            if path == "/out/copy/file":
                source_entries["/docs/file"] = b"changed"
            return await original(path, **kwargs)

        filesystem._info = MethodType(info, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, source_calls),
            "destination": _source(
                destination_entries,
                destination_calls,
                configure=configure,
            ),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: source changed; destination residue may remain\n",
    )
    assert ("get_file", "/docs/file") in source_calls
    assert not [call for call in destination_calls if call[0] == "put_file"]
    assert destination_entries["/out/copy"] is None


def test_recursive_cp_reports_partial_destination_residue_after_upload_failure() -> (
    None
):
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/first.txt": b"first",
        "/docs/second.txt": b"second",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    calls: list[tuple[object, ...]] = []

    def configure(filesystem: _TreeFileSystem) -> None:
        original = filesystem._put_file

        async def put_file(
            self: _TreeFileSystem,
            local: str,
            remote: str,
            mode: str = "overwrite",
            **kwargs: object,
        ) -> None:
            del self
            if remote.endswith("second.txt"):
                message = "upload failed"
                raise OSError(message)
            await original(local, remote, mode, **kwargs)

        filesystem._put_file = MethodType(put_file, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, calls),
            "destination": _source(destination_entries, calls, configure=configure),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: destination:/out/copy: mutation failure; destination residue may remain\n",
    )
    assert destination_entries["/out/copy/first.txt"] == b"first"
    assert "/out/copy/second.txt" not in destination_entries


def test_recursive_cp_drains_cancelled_download_before_source_exit() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/notes.txt": b"notes",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    calls: list[tuple[object, ...]] = []
    drained = False

    def configure(filesystem: _TreeFileSystem) -> None:
        owner = asyncio.current_task()
        assert owner is not None

        async def get_file(
            self: _TreeFileSystem,
            remote: str,
            local: str,
            **kwargs: object,
        ) -> None:
            nonlocal drained
            del self, remote, kwargs
            owner.cancel()
            await asyncio.sleep(0)
            Path(local).write_bytes(b"notes")  # noqa: ASYNC240
            drained = True

        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        _invoke(
            ["-R", "source:/docs", "destination:/out/copy"],
            {
                "source": _source(source_entries, calls, configure=configure),
                "destination": _source(destination_entries, calls),
            },
        )

    assert drained
    assert "/out/copy/notes.txt" not in destination_entries


def test_recursive_cp_propagates_non_cancel_control_flow() -> None:
    class Stop(BaseException):
        pass

    control = Stop()
    entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/notes.txt": b"notes",
        "/out": None,
    }
    calls: list[tuple[object, ...]] = []

    def configure(filesystem: _TreeFileSystem) -> None:
        async def fail(
            self: _TreeFileSystem,
            remote: str,
            local: str,
            **kwargs: object,
        ) -> NoReturn:
            del self, remote, local, kwargs
            raise control

        filesystem._get_file = MethodType(fail, filesystem)  # type: ignore[method-assign]

    with pytest.raises(Stop) as caught:
        _invoke(
            ["-R", "memory:/docs", "memory:/out/copy"],
            {"memory": _source(entries, calls, configure=configure)},
        )

    assert caught.value is control


@pytest.mark.parametrize(
    ("entry_count", "expected"),
    [
        (10_000, (0, "")),
        (10_001, (1, "cp: memory:/source: source tree exceeds 10000 entries\n")),
    ],
)
def test_recursive_cp_enforces_exact_manifest_entry_limit(
    entry_count: int,
    expected: tuple[int, str],
) -> None:
    entries: dict[str, bytes | None] = {"/": None, "/source": None, "/out": None}
    for index in range(entry_count - 1):
        entries[f"/source/d{index:05d}"] = None
    calls: list[tuple[object, ...]] = []

    def configure(filesystem: _TreeFileSystem) -> None:
        async def walk(
            self: _TreeFileSystem,
            path: str,
            *,
            detail: bool,
            on_error: str,
            **kwargs: object,
        ) -> AsyncIterator[object]:
            del detail, on_error, kwargs
            directories = {
                child.rsplit("/", 1)[-1]: self._metadata(child)
                for child in sorted(self.entries)
                if child.startswith(f"{path}/") and "/" not in child[len(path) + 1 :]
            }
            yield path, directories, {}
            for name in directories:
                yield f"{path}/{name}", {}, {}

        filesystem._walk = MethodType(walk, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "memory:/source", "memory:/out/copy"],
        {"memory": _source(entries, calls, configure=configure)},
    )

    assert (result.exit_code, result.stderr) == expected
    assert result.stdout == ""
    if entry_count == 10_001:
        assert not [call for call in calls if call[0] in {"mkdir", "put_file"}]


def test_recursive_cp_rejects_shared_token_mismatch_during_final_proof() -> None:
    entries: dict[str, bytes | None] = {
        "/": None,
        "/source": None,
        "/source/notes.txt": b"notes",
        "/out": None,
        "/out/copy": None,
        "/out/copy/source": None,
        "/out/copy/source/notes.txt": b"old!!",
    }
    metadata = {
        "/source/notes.txt": {
            "name": "/source/notes.txt",
            "type": "file",
            "size": 5,
            "checksum": "source-token",
        },
        "/out/copy/source/notes.txt": {
            "name": "/out/copy/source/notes.txt",
            "type": "file",
            "size": 5,
            "checksum": "destination-token",
        },
    }

    result = _invoke(
        ["-R", "memory:/source", "memory:/out/copy"],
        {"memory": _source(entries, [], metadata=metadata)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/out/copy: verification failure; destination residue may remain\n",
    )


def test_recursive_cp_orders_transfer_and_staging_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/source": None,
        "/source/notes.txt": b"notes",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    temporary_paths: list[str] = []

    def configure(filesystem: _TreeFileSystem) -> None:
        async def get_file(
            self: _TreeFileSystem,
            remote: str,
            local: str,
            **kwargs: object,
        ) -> None:
            del self, remote, kwargs
            temporary_paths.append(local)
            Path(local).write_bytes(b"partial")  # noqa: ASYNC240
            message = "download failed"
            raise OSError(message)

        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    real_unlink = Path.unlink

    def fail_temporary_unlink(path: Path, missing_ok: bool = False) -> None:  # noqa: FBT002
        if path.name.startswith("fsspec-cli-cp-recursive-"):
            message = "cleanup failed"
            raise OSError(message)
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("fsspec_cli._recursive_cp.Path.unlink", fail_temporary_unlink)
    result = _invoke(
        ["-R", "source:/source", "destination:/out/copy"],
        {
            "source": _source(source_entries, [], configure=configure),
            "destination": _source(destination_entries, []),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/source: transfer failure; destination residue may remain\n"
        "cp: source:/source: staging cleanup failure (OSError); "
        "host staging residue may remain; destination residue may remain\n",
    )
    assert len(temporary_paths) == 1
    real_unlink(Path(temporary_paths[0]), missing_ok=True)


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


@pytest.mark.parametrize("shape", ["duplicate", "missing", "unreachable", "row"])
def test_recursive_cp_rejects_malformed_walk_shapes_before_mutation(
    shape: str,
) -> None:
    entries: dict[str, bytes | None] = {"/": None, "/docs": None, "/out": None}
    calls: list[tuple[object, ...]] = []
    empty_row = ("/docs", {}, {})
    if shape == "duplicate":
        rows: list[object] = [empty_row, empty_row]
    elif shape == "missing":
        rows = [
            (
                "/docs",
                {
                    "nested": {
                        "name": "/docs/nested",
                        "type": "directory",
                        "size": 0,
                    }
                },
                {},
            )
        ]
    elif shape == "unreachable":
        rows = [empty_row, ("/outside", {}, {})]
    else:
        rows = [("/docs", {}, {}, "extra")]

    def configure(filesystem: _TreeFileSystem) -> None:
        async def walk(
            self: _TreeFileSystem,
            *args: object,
            **kwargs: object,
        ):
            del self, args, kwargs
            for row in rows:
                yield row

        filesystem._walk = MethodType(walk, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "memory:/docs", "memory:/out/copy"],
        {"memory": _source(entries, calls, configure=configure)},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: memory:/docs: incompatible result\n",
    )
    assert not [call for call in calls if call[0] in {"mkdir", "put_file"}]


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (PermissionError("denied"), "permission denied"),
        (NotImplementedError("missing"), "unsupported operation"),
        (
            NotADirectoryError("backend-specific"),
            "backend failure (NotADirectoryError): backend-specific",
        ),
    ],
)
def test_recursive_cp_classifies_destination_preflight_failures(
    error: Exception,
    category: str,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    metadata = {"/out/copy/file": error}

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, []),
            "destination": _source(
                destination_entries,
                [],
                metadata=metadata,
            ),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"cp: destination:/out/copy: {category}\n",
    )
    assert "/out/copy" not in destination_entries


def test_recursive_cp_reports_directory_creation_failure_with_residue() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}

    def configure(filesystem: _TreeFileSystem) -> None:
        async def mkdir(
            self: _TreeFileSystem,
            path: str,
            create_parents: bool = True,  # noqa: FBT002
            **kwargs: object,
        ) -> NoReturn:
            del self, path, create_parents, kwargs
            message = "mkdir"
            raise OSError(message)

        filesystem._mkdir = MethodType(mkdir, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, []),
            "destination": _source(destination_entries, [], configure=configure),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: destination:/out/copy: mutation failure; destination residue may remain\n",
    )
    assert "/out/copy" not in destination_entries


def test_recursive_cp_reports_staging_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {
        "/": None,
        "/out": None,
        "/out/copy": None,
    }

    def fail_mkstemp(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        message = "staging"
        raise OSError(message)

    monkeypatch.setattr("fsspec_cli._recursive_cp.tempfile.mkstemp", fail_mkstemp)
    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, []),
            "destination": _source(destination_entries, []),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: staging failure (OSError); destination residue may remain\n",
    )


def test_recursive_cp_reports_staging_stat_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    real_stat = Path.stat

    def fail_temporary_stat(path: Path, *args: object, **kwargs: object):
        if path.name.startswith("fsspec-cli-cp-recursive-"):
            message = "stat"
            raise OSError(message)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr("fsspec_cli._recursive_cp.Path.stat", fail_temporary_stat)
    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, []),
            "destination": _source(destination_entries, []),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: staging failure (OSError); destination residue may remain\n",
    )
    assert "/out/copy/file" not in destination_entries


def test_recursive_cp_reports_staging_descriptor_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    real_mkstemp = tempfile.mkstemp
    real_close = os.close
    staging_descriptor: int | None = None

    def capture_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        nonlocal staging_descriptor
        descriptor, path = real_mkstemp(*args, **kwargs)
        staging_descriptor = descriptor
        return descriptor, path

    def fail_staging_close(descriptor: int) -> None:
        if descriptor == staging_descriptor:
            real_close(descriptor)
            message = "close"
            raise OSError(message)
        real_close(descriptor)

    monkeypatch.setattr(
        "fsspec_cli._recursive_cp.tempfile.mkstemp",
        capture_mkstemp,
    )
    monkeypatch.setattr("fsspec_cli._recursive_cp.os.close", fail_staging_close)
    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, []),
            "destination": _source(destination_entries, []),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: staging failure (OSError); destination residue may remain\n",
    )
    assert "/out/copy/file" not in destination_entries


def test_recursive_cp_rejects_staged_size_change_before_upload() -> None:
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
        ) -> None:
            del self, remote, kwargs
            Path(local).write_bytes(b"changed")  # noqa: ASYNC240

        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, [], configure=configure),
            "destination": _source(destination_entries, []),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: source changed; destination residue may remain\n",
    )
    assert "/out/copy/file" not in destination_entries


def test_recursive_cp_reports_cleanup_failure_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    temporary_paths: list[Path] = []
    real_unlink = Path.unlink

    def fail_temporary_unlink(path: Path, missing_ok: bool = False) -> None:  # noqa: FBT002
        if path.name.startswith("fsspec-cli-cp-recursive-"):
            temporary_paths.append(path)
            message = "cleanup"
            raise OSError(message)
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("fsspec_cli._recursive_cp.Path.unlink", fail_temporary_unlink)
    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, []),
            "destination": _source(destination_entries, []),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: staging cleanup failure (OSError); "
        "host staging residue may remain; destination residue may remain\n",
    )
    assert destination_entries["/out/copy/file"] == b"x"
    assert len(temporary_paths) == 1
    real_unlink(temporary_paths[0], missing_ok=True)


def test_recursive_cp_preserves_control_over_cleanup_base_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop(BaseException):
        pass

    primary = Stop()
    cleanup = Stop()
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
            raise primary

        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    def fail_unlink(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise cleanup

    monkeypatch.setattr("fsspec_cli._recursive_cp.Path.unlink", fail_unlink)
    with pytest.raises(Stop) as caught:
        _invoke(
            ["-R", "source:/docs", "destination:/out/copy"],
            {
                "source": _source(source_entries, [], configure=configure),
                "destination": _source(destination_entries, []),
            },
        )

    assert caught.value is primary


def test_recursive_cp_propagates_cleanup_base_exception_after_success(
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
    temporary_paths: list[Path] = []
    real_unlink = Path.unlink

    def fail_unlink(path: Path, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        temporary_paths.append(path)
        raise control

    monkeypatch.setattr("fsspec_cli._recursive_cp.Path.unlink", fail_unlink)
    with pytest.raises(Stop) as caught:
        _invoke(
            ["-R", "source:/docs", "destination:/out/copy"],
            {
                "source": _source(source_entries, []),
                "destination": _source(destination_entries, []),
            },
        )

    assert caught.value is control
    assert destination_entries["/out/copy/file"] == b"x"
    assert len(temporary_paths) == 1
    real_unlink(temporary_paths[0], missing_ok=True)


@pytest.mark.parametrize(
    "phase",
    [
        "source info",
        "target info",
        "walk",
        "destination preflight",
        "mkdir",
        "download",
        "upload",
        "source revalidation info",
        "source revalidation walk",
        "destination proof",
    ],
)
def test_recursive_cp_drains_current_operation_on_cancellation(  # noqa: C901, PLR0915 - explicit phase matrix.
    phase: str,
) -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    drained = False
    temporary_paths: list[str] = []
    owner_task: asyncio.Task[object] | None = None

    async def cancel_then_resume() -> None:
        nonlocal drained
        assert owner_task is not None
        owner_task.cancel()
        await asyncio.sleep(0)
        drained = True

    def configure_source(filesystem: _TreeFileSystem) -> None:
        nonlocal owner_task
        owner_task = asyncio.current_task()
        assert owner_task is not None
        original_info = filesystem._info
        info_calls = 0

        async def info(
            self: _TreeFileSystem,
            path: str,
            **kwargs: object,
        ) -> dict[str, object]:
            nonlocal info_calls
            del self
            info_calls += 1
            if (phase == "source info" and info_calls == 1) or (
                phase == "source revalidation info" and info_calls == 2
            ):
                await cancel_then_resume()
            return await original_info(path, **kwargs)

        original_walk = filesystem._walk
        walk_calls = 0

        async def walk(
            self: _TreeFileSystem,
            path: str,
            *,
            detail: bool,
            on_error: str,
            **kwargs: object,
        ):
            nonlocal walk_calls
            del self
            walk_calls += 1
            if (phase == "walk" and walk_calls == 1) or (
                phase == "source revalidation walk" and walk_calls == 2
            ):
                await cancel_then_resume()
            async for row in original_walk(
                path,
                detail=detail,
                on_error=on_error,
                **kwargs,
            ):
                yield row

        original_get = filesystem._get_file

        async def get_file(
            self: _TreeFileSystem,
            remote: str,
            local: str,
            **kwargs: object,
        ) -> None:
            del self
            temporary_paths.append(local)
            if phase == "download":
                await cancel_then_resume()
            await original_get(remote, local, **kwargs)

        filesystem._info = MethodType(info, filesystem)  # type: ignore[method-assign]
        filesystem._walk = MethodType(walk, filesystem)  # type: ignore[method-assign]
        filesystem._get_file = MethodType(get_file, filesystem)  # type: ignore[method-assign]

    def configure_destination(filesystem: _TreeFileSystem) -> None:
        nonlocal owner_task
        if owner_task is None:
            owner_task = asyncio.current_task()
        assert owner_task is not None
        original_info = filesystem._info
        path_calls: dict[str, int] = {}

        async def info(
            self: _TreeFileSystem,
            path: str,
            **kwargs: object,
        ) -> dict[str, object]:
            del self
            path_calls[path] = path_calls.get(path, 0) + 1
            should_cancel = (
                (
                    phase == "target info"
                    and path == "/out/copy"
                    and path_calls[path] == 1
                )
                or (
                    phase == "destination preflight"
                    and path == "/out/copy/file"
                    and path_calls[path] == 1
                )
                or (
                    phase == "destination proof"
                    and path == "/out/copy/file"
                    and path_calls[path] == 2
                )
            )
            if should_cancel:
                await cancel_then_resume()
            return await original_info(path, **kwargs)

        original_mkdir = filesystem._mkdir

        async def mkdir(
            self: _TreeFileSystem,
            path: str,
            create_parents: bool = True,  # noqa: FBT002
            **kwargs: object,
        ) -> None:
            del self
            if phase == "mkdir":
                await cancel_then_resume()
            await original_mkdir(path, create_parents, **kwargs)

        original_put = filesystem._put_file

        async def put_file(
            self: _TreeFileSystem,
            local: str,
            remote: str,
            mode: str = "overwrite",
            **kwargs: object,
        ) -> None:
            del self
            if phase == "upload":
                await cancel_then_resume()
            await original_put(local, remote, mode, **kwargs)

        filesystem._info = MethodType(info, filesystem)  # type: ignore[method-assign]
        filesystem._mkdir = MethodType(mkdir, filesystem)  # type: ignore[method-assign]
        filesystem._put_file = MethodType(put_file, filesystem)  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        _invoke(
            ["-R", "source:/docs", "destination:/out/copy"],
            {
                "source": _source(source_entries, [], configure=configure_source),
                "destination": _source(
                    destination_entries,
                    [],
                    configure=configure_destination,
                ),
            },
        )

    assert drained
    assert not [path for path in temporary_paths if Path(path).exists()]
    if phase in {
        "source info",
        "target info",
        "walk",
        "destination preflight",
    }:
        assert "/out/copy" not in destination_entries
    elif phase in {"mkdir", "download"}:
        assert destination_entries["/out/copy"] is None
        assert "/out/copy/file" not in destination_entries
    else:
        assert destination_entries["/out/copy/file"] == b"x"


def test_recursive_cp_classifies_final_source_walk_failure() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}

    def configure(filesystem: _TreeFileSystem) -> None:
        original = filesystem._walk
        calls = 0

        async def walk(
            self: _TreeFileSystem,
            path: str,
            *,
            detail: bool,
            on_error: str,
            **kwargs: object,
        ):
            nonlocal calls
            del self
            calls += 1
            if calls == 2:
                message = "final walk"
                raise OSError(message)
            async for row in original(
                path,
                detail=detail,
                on_error=on_error,
                **kwargs,
            ):
                yield row

        filesystem._walk = MethodType(walk, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, [], configure=configure),
            "destination": _source(destination_entries, []),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: source:/docs: source revalidation failure; "
        "destination residue may remain\n",
    )
    assert destination_entries["/out/copy/file"] == b"x"


def test_recursive_cp_classifies_destination_proof_failure() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}

    def configure(filesystem: _TreeFileSystem) -> None:
        original = filesystem._info
        file_calls = 0

        async def info(
            self: _TreeFileSystem,
            path: str,
            **kwargs: object,
        ) -> dict[str, object]:
            nonlocal file_calls
            del self
            if path == "/out/copy/file":
                file_calls += 1
                if file_calls == 2:
                    message = "proof"
                    raise OSError(message)
            return await original(path, **kwargs)

        filesystem._info = MethodType(info, filesystem)  # type: ignore[method-assign]

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, []),
            "destination": _source(destination_entries, [], configure=configure),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: destination:/out/copy: verification failure; "
        "destination residue may remain\n",
    )
    assert destination_entries["/out/copy/file"] == b"x"


def test_recursive_cp_accepts_matching_shared_tokens() -> None:
    source_entries: dict[str, bytes | None] = {
        "/": None,
        "/docs": None,
        "/docs/file": b"x",
    }
    source_metadata = {
        "/docs/file": {
            "name": "/docs/file",
            "type": "file",
            "size": 1,
            "checksum": "same",
        }
    }
    destination_entries: dict[str, bytes | None] = {"/": None, "/out": None}
    destination_metadata = {
        "/out/copy/file": {
            "name": "/out/copy/file",
            "type": "file",
            "size": 1,
            "checksum": "same",
        }
    }

    result = _invoke(
        ["-R", "source:/docs", "destination:/out/copy"],
        {
            "source": _source(source_entries, [], metadata=source_metadata),
            "destination": _source(
                destination_entries,
                [],
                metadata=destination_metadata,
            ),
        },
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert destination_entries["/out/copy/file"] == b"x"
