"""Recursive ``cp`` failure, cleanup, and cancellation tests."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from types import MethodType
from typing import NoReturn

import pytest

from .test_recursive_cp import _invoke, _source, _TreeFileSystem


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
