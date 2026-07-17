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
    _exercise_cp_locked_profile,
    _exercise_locked_profile,
    _exercise_mkdir_locked_profile,
    _exercise_mkdir_memory_over_eager_failure,
    _exercise_mkdir_p_locked_profile,
    _exercise_multi_source_cp_locked_profile,
    _exercise_rm_directory_profile,
    _exercise_rm_force_profile,
    _exercise_rm_locked_profile,
    _exercise_rm_verbose_profile,
    _exercise_rmdir_locked_profile,
    _exercise_stat_incomplete_profile,
    _exercise_stat_locked_profile,
    _exercise_unlink_locked_profile,
    _invoke,
    _invoke_cat,
    _invoke_cp,
    _invoke_ls,
    _ProbedSource,
)
from ._support import _source_must_not_run

_SYNC_MV_MESSAGE = "public sync mv must not be called"


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
    return root.resolve().as_posix()


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


def test_mkdir_m_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "mkdir",
        ["-m", "755", "memory:/docs/new"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "mkdir: -m: unsupported option\n",
    )
    assert source_calls == 0


def test_mkdir_pm_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "mkdir",
        ["-pm", "memory:/docs/new"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "mkdir: -pm: unsupported option\n",
    )
    assert source_calls == 0


def test_mkdir_parents_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "mkdir",
        ["--parents", "memory:/docs/new"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "mkdir: --parents: unsupported option\n",
    )
    assert source_calls == 0


def test_mkdir_p_after_operand_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "mkdir",
        ["memory:/docs/a", "-p", "memory:/docs/b"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "mkdir: -p: unsupported option\n",
    )
    assert source_calls == 0


def test_adapted_local_mkdir_p_profile_uses_native_temporary_storage(
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

    _exercise_mkdir_p_locked_profile("local", source, path)


def test_adapted_memory_mkdir_p_profile_has_isolated_state(
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

    _exercise_mkdir_p_locked_profile("memory", source, "/docs")


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


def test_adapted_local_base_rm_profile_uses_native_temporary_storage(
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

    _exercise_rm_locked_profile("local", source, path)
    (root / "notes.txt").write_text("notes.txt", encoding="utf-8")
    _exercise_rm_directory_profile("local", source, path)
    _exercise_rm_force_profile("local", source, path)
    (Path(path) / "notes.txt").write_text("notes.txt", encoding="utf-8")
    _exercise_rm_verbose_profile("local", source, path)


def test_adapted_local_cp_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _populate_local(root)
    (root / "target").mkdir()
    path = _local_command_path(root)
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_cp_locked_profile("local", source, path)


def test_adapted_local_multi_source_cp_profile_uses_native_temporary_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _populate_local(root)
    (root / "target").mkdir()
    path = _local_command_path(root)
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_multi_source_cp_locked_profile("local", source, path)


def test_adapted_memory_base_rm_profile_has_isolated_state(
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

    _exercise_rm_locked_profile("memory", source, "/docs")
    _exercise_rm_directory_profile("memory", source, "/docs")
    _exercise_rm_force_profile("memory", source, "/docs")
    _exercise_rm_verbose_profile("memory", source, "/docs")


def test_adapted_memory_cp_profile_has_isolated_state(
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
        filesystem.mkdir("/docs/target")
        for name in ("notes.txt", ".hidden", "guide.md"):
            filesystem.pipe_file(f"/docs/{name}", name.encode())
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_cp_locked_profile("memory", source, "/docs")


def test_adapted_memory_multi_source_cp_profile_has_isolated_state(
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
        filesystem.mkdir("/docs/target")
        for name in ("notes.txt", ".hidden", "guide.md"):
            filesystem.pipe_file(f"/docs/{name}", name.encode())
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_multi_source_cp_locked_profile("memory", source, "/docs")


@pytest.mark.parametrize("direction", ["local-to-memory", "memory-to-local"])
def test_cross_source_cp_between_adapted_local_and_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direction: str,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})
    payload = b"\0\xff cross-source"
    local_root = tmp_path / "local"
    local_root.mkdir()
    memory = MemoryFileSystem()
    memory.makedirs("/docs")

    if direction == "local-to-memory":
        (local_root / "source.bin").write_bytes(payload)
        source_operand = f"local:{_local_command_path(local_root / 'source.bin')}"
        destination_operand = "memory:/docs/copy.bin"
    else:
        memory.pipe_file("/docs/source.bin", payload)
        source_operand = "memory:/docs/source.bin"
        destination_path = local_root / "copy.bin"
        destination_operand = f"local:{_local_command_path(destination_path)}"

    app = App(
        {
            "local": _ProbedSource(
                lambda: AsyncFileSystemWrapper(
                    LocalFileSystem(skip_instance_cache=True), asynchronous=True
                )
            ),
            "memory": _ProbedSource(
                lambda: AsyncFileSystemWrapper(memory, asynchronous=True)
            ),
        }
    )

    result = _invoke_cp(app, [source_operand, destination_operand])

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    if direction == "local-to-memory":
        assert memory.cat("/docs/copy.bin") == payload
    else:
        assert (local_root / "copy.bin").read_bytes() == payload


def test_cp_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "cp",
        ["-R", "memory:/docs/notes.txt", "memory:/docs/copy.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "cp: -R: unsupported option\n",
    )
    assert source_calls == 0


def test_rm_force_profile_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "rm",
        ["-f", "-i", "memory:/docs/notes.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "rm: -i: unsupported option\n",
    )
    assert source_calls == 0


def test_rm_verbose_profile_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "rm",
        ["-v", "-f", "memory:/docs/notes.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "rm: -f: unsupported option\n",
    )
    assert source_calls == 0


def test_adapted_local_mv_remains_unverified_without_exact_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    source_path = root / "notes.txt"
    target_path = root / "moved.txt"
    source_path.write_bytes(b"payload")
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    def reject_sync_mv(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(_SYNC_MV_MESSAGE)

    monkeypatch.setattr(AsyncFileSystemWrapper, "mv", reject_sync_mv)
    result = _invoke(
        App({"local": source}),
        "mv",
        [f"local:{source_path}", f"local:{target_path}"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"mv: local:{target_path}: unsupported operation\n",
    )
    assert "_mv" not in type(source.filesystems[0]).__dict__
    assert source_path.read_bytes() == b"payload"
    assert not target_path.exists()
    assert not any(call.operation == "get_file" for call in source.calls)


def test_adapted_memory_mv_remains_unverified_without_exact_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("/docs/notes.txt", b"payload")
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    def reject_sync_mv(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(_SYNC_MV_MESSAGE)

    monkeypatch.setattr(AsyncFileSystemWrapper, "mv", reject_sync_mv)
    result = _invoke(
        App({"memory": source}),
        "mv",
        ["memory:/docs/notes.txt", "memory:/docs/moved.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/moved.txt: unsupported operation\n",
    )
    filesystem = source.filesystems[0]
    assert "_mv" not in type(filesystem).__dict__
    assert filesystem.sync_fs.cat("/docs/notes.txt") == b"payload"
    assert not filesystem.sync_fs.exists("/docs/moved.txt")
    assert not any(call.operation == "get_file" for call in source.calls)


def test_adapted_local_multi_file_mv_remains_unverified_without_exact_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    notes_path = root / "notes.txt"
    guide_path = root / "guide.md"
    target_dir = root / "target"
    notes_path.write_bytes(b"notes")
    guide_path.write_bytes(b"guide")
    target_dir.mkdir()
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    def reject_sync_mv(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(_SYNC_MV_MESSAGE)

    monkeypatch.setattr(AsyncFileSystemWrapper, "mv", reject_sync_mv)
    result = _invoke(
        App({"local": source}),
        "mv",
        [f"local:{notes_path}", f"local:{guide_path}", f"local:{target_dir}"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"mv: local:{target_dir}: unsupported operation\n",
    )
    assert "_mv" not in type(source.filesystems[0]).__dict__
    assert notes_path.read_bytes() == b"notes"
    assert guide_path.read_bytes() == b"guide"
    assert not (target_dir / "notes.txt").exists()
    assert not (target_dir / "guide.md").exists()
    assert not any(call.operation == "get_file" for call in source.calls)


def test_adapted_memory_multi_file_mv_remains_unverified_without_exact_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MemoryFileSystem, "store", {})
    monkeypatch.setattr(MemoryFileSystem, "pseudo_dirs", [""])
    monkeypatch.setattr(MemoryFileSystem, "_cache", {})

    def make_filesystem() -> AsyncFileSystemWrapper:
        MemoryFileSystem.clear_instance_cache()
        filesystem = MemoryFileSystem()
        filesystem.makedirs("/docs/target")
        filesystem.pipe_file("/docs/notes.txt", b"notes")
        filesystem.pipe_file("/docs/guide.md", b"guide")
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    def reject_sync_mv(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(_SYNC_MV_MESSAGE)

    monkeypatch.setattr(AsyncFileSystemWrapper, "mv", reject_sync_mv)
    result = _invoke(
        App({"memory": source}),
        "mv",
        [
            "memory:/docs/notes.txt",
            "memory:/docs/guide.md",
            "memory:/docs/target",
        ],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/target: unsupported operation\n",
    )
    filesystem = source.filesystems[0]
    assert "_mv" not in type(filesystem).__dict__
    assert filesystem.sync_fs.cat("/docs/notes.txt") == b"notes"
    assert filesystem.sync_fs.cat("/docs/guide.md") == b"guide"
    assert not filesystem.sync_fs.exists("/docs/target/notes.txt")
    assert not filesystem.sync_fs.exists("/docs/target/guide.md")
    assert not any(call.operation == "get_file" for call in source.calls)


def test_rm_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "rm",
        ["-f", "-i", "memory:/docs/notes.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "rm: -i: unsupported option\n",
    )
    assert source_calls == 0


def test_adapted_local_stat_profile_uses_native_temporary_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    import time

    time.tzset()
    root = tmp_path / "docs"
    root.mkdir()
    file_path = root / "notes.txt"
    file_path.write_text("abc", encoding="utf-8")
    directory_path = root / "subdir"
    directory_path.mkdir()
    source = _ProbedSource(
        lambda: AsyncFileSystemWrapper(
            LocalFileSystem(skip_instance_cache=True),
            asynchronous=True,
        )
    )

    _exercise_stat_locked_profile(
        "local",
        source,
        _local_command_path(file_path),
        _local_command_path(directory_path),
    )

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, LocalFileSystem) for fs in source.filesystems)
    assert all(fs.asynchronous is True for fs in source.filesystems)


def test_adapted_memory_stat_profile_fails_closed_on_incomplete_info(
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
        filesystem.pipe_file("/docs/notes.txt", b"abc")
        return AsyncFileSystemWrapper(filesystem, asynchronous=True)

    source = _ProbedSource(make_filesystem)

    _exercise_stat_incomplete_profile("memory", source, "/docs/notes.txt")

    assert all(isinstance(fs, AsyncFileSystemWrapper) for fs in source.filesystems)
    assert all(isinstance(fs.sync_fs, MemoryFileSystem) for fs in source.filesystems)


def test_stat_option_rejection_is_source_free() -> None:
    source_calls = 0

    def source_must_not_run() -> AbstractAsyncContextManager[AsyncFileSystem]:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke(
        App({"memory": source_must_not_run}),
        "stat",
        ["-l", "memory:/docs/notes.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "stat: -l: unsupported option\n",
    )
    assert source_calls == 0
