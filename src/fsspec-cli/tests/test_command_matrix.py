"""Hermetic evidence independent of the vosfs integration dependency."""

import socket
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from fsspec.asyn import AsyncFileSystem
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem
from fsspec_cli import App
from typer.testing import CliRunner

from ._matrix_support import (
    _block_network,
    _exercise_cat_profile,
    _exercise_locked_profile,
    _exercise_mkdir_locked_profile,
    _exercise_mkdir_memory_over_eager_failure,
    _exercise_rmdir_locked_profile,
    _exercise_unlink_locked_profile,
    _invoke,
    _invoke_cat,
    _invoke_ls,
    _ProbedSource,
)
from ._support import _source_must_not_run


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


def _populate_local(root: Path) -> None:
    root.mkdir()
    for name in ("notes.txt", ".hidden", "guide.md"):
        (root / name).write_text(name, encoding="utf-8")


def _populate_local_with_empty(root: Path) -> None:
    _populate_local(root)
    (root / "empty").mkdir()


def test_hermetic_guard_rejects_name_resolution() -> None:
    with pytest.raises(AssertionError) as caught:
        socket.getaddrinfo("example.test", 443)

    assert str(caught.value) == (
        "hermetic command-matrix tests prohibit network access"
    )


def _local_command_path(root: Path) -> str:
    resolved = root.resolve()
    path = resolved.as_posix()
    if not resolved.drive:
        return path
    message = "the Windows hermetic Local gate requires a drive-letter tmp_path"
    assert len(resolved.drive) == 2, message
    assert resolved.drive.endswith(":"), message
    return f"//?/{path}"


def test_adapted_local_plain_ls_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _populate_local(root)
    path = _local_command_path(root)
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_locked_profile("local", source, path)

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in source.filesystems)
    assert all(fs.asynchronous is True for fs in source.filesystems)
    assert len({id(fs) for fs in source.filesystems}) == 3
    assert len({id(fs.sync_fs) for fs in source.filesystems}) == 3
    if root.drive:
        assert path.startswith(f"//?/{root.drive}/")


def test_adapted_memory_plain_ls_profile_has_isolated_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.makedirs("/docs")
        for name in ("notes.txt", ".hidden", "guide.md"):
            filesystem.pipe_file(f"/docs/{name}", name.encode())
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_locked_profile("memory", source, "/docs")

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in source.filesystems)
    assert all(fs.asynchronous is True for fs in source.filesystems)
    assert len({id(fs) for fs in source.filesystems}) == 3
    assert len({id(fs.sync_fs) for fs in source.filesystems}) == 3


def test_adapted_local_base_mkdir_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _populate_local(root)
    path = _local_command_path(root)
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_mkdir_locked_profile("local", source, path)


def test_adapted_memory_base_mkdir_profile_over_eager_parent_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.makedirs("/docs")
        for name in ("notes.txt", ".hidden", "guide.md"):
            filesystem.pipe_file(f"/docs/{name}", name.encode())
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_mkdir_memory_over_eager_failure("memory", source, "/docs")


def test_mkdir_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "mkdir",
        ["-p", "memory:/docs/new"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "mkdir: -p: unsupported option\n",
    )
    assert source_calls == 0


def test_ls_long_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_ls(App({"memory": source_must_not_run}), ["-l", "memory:/docs"])

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "ls: -l: unsupported option\n",
    )
    assert source_calls == 0


def test_basename_string_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = CliRunner().invoke(
        App({"memory": source_must_not_run}).typer_app,
        ["basename", "memory:/docs/a.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "a.txt\n",
        "",
    )
    assert source_calls == 0


def test_basename_suffix_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = CliRunner().invoke(
        App({"memory": source_must_not_run}).typer_app,
        ["basename", "memory:/docs/a.txt", ".txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "a\n",
        "",
    )
    assert source_calls == 0


def test_basename_extra_operand_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = CliRunner().invoke(
        App({"memory": source_must_not_run}).typer_app,
        ["basename", "a", "b", "c"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "basename: extra operand\n",
    )
    assert source_calls == 0


def test_basename_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = CliRunner().invoke(
        App({"memory": source_must_not_run}).typer_app,
        ["basename", "-a", "a"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "basename: -a: unsupported option\n",
    )
    assert source_calls == 0


def test_dirname_string_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = CliRunner().invoke(
        App({"memory": source_must_not_run}).typer_app,
        ["dirname", "memory:/docs/a.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "memory:/docs\n",
        "",
    )
    assert source_calls == 0


def test_dirname_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = CliRunner().invoke(
        App({"memory": source_must_not_run}).typer_app,
        ["dirname", "-a", "a"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "dirname: -a: unsupported option\n",
    )
    assert source_calls == 0


def test_adapted_local_plain_cat_profile(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    payload = bytes(range(256)) + b"\nno-final"
    target = root / "blob.bin"
    target.write_bytes(payload)
    path = _local_command_path(target)
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_cat_profile("local", source, path, payload=payload)

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in source.filesystems)
    assert all(fs.asynchronous is True for fs in source.filesystems)


def test_adapted_memory_plain_cat_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})
    payload = b"\xff\xfe\0memory-cat"

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("/docs/blob.bin", payload)
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_cat_profile("memory", source, "/docs/blob.bin", payload=payload)

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in source.filesystems)
    assert all(fs.asynchronous is True for fs in source.filesystems)


def test_cat_u_rejection_is_source_free() -> None:
    source = _source_must_not_run
    app = App({"memory": source})
    result = _invoke_cat(app, ["-u", "memory:/file"])

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "cat: -u: unsupported option\n",
    )


def test_adapted_memory_cat_stdin_dash_mixed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("/docs/left.bin", b"L")
        filesystem.pipe_file("/docs/right.bin", b"R")
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)
    app = App({"memory": source})
    result = CliRunner().invoke(
        app.typer_app,
        ["cat", "memory:/docs/left.bin", "-", "memory:/docs/right.bin"],
        input=b"S",
    )

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (0, b"LSR", "")
    assert [event.stage for event in source.lifecycle] == ["factory", "enter", "exit"]
    assert [call.operation for call in source.calls] == [
        "info",
        "get_file",
        "info",
        "get_file",
    ]


def test_adapted_local_base_rmdir_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _populate_local_with_empty(root)
    path = _local_command_path(root)
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_rmdir_locked_profile("local", source, path)


def test_adapted_memory_base_rmdir_profile_has_isolated_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.makedirs("/docs")
        filesystem.mkdir("/docs/empty")
        for name in ("notes.txt", ".hidden", "guide.md"):
            filesystem.pipe_file(f"/docs/{name}", name.encode())
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_rmdir_locked_profile("memory", source, "/docs")


def test_rmdir_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "rmdir",
        ["-p", "memory:/docs/empty"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "rmdir: -p: unsupported option\n",
    )
    assert source_calls == 0


def test_adapted_local_unlink_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _populate_local(root)
    path = _local_command_path(root)
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_unlink_locked_profile("local", source, path)


def test_adapted_memory_unlink_profile_has_isolated_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.store.clear()
        MemoryFileSystem.pseudo_dirs[:] = [""]
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.makedirs("/docs")
        for name in ("notes.txt", ".hidden", "guide.md"):
            filesystem.pipe_file(f"/docs/{name}", name.encode())
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_unlink_locked_profile("memory", source, "/docs")


def test_unlink_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "unlink",
        ["-f", "memory:/docs/notes.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "unlink: -f: unsupported option\n",
    )
    assert source_calls == 0
