"""Verified recursive ``cp`` tests through ``App(sources).typer_app``."""

from __future__ import annotations

import asyncio
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
