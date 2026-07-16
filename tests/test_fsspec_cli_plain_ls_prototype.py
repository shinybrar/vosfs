from __future__ import annotations

import locale
from contextlib import suppress
from typing import Any
from uuid import uuid4

import pytest
from fsspec import AbstractFileSystem
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem

pytest.importorskip("typer")
from prototypes.fsspec_cli_plain_ls import App
from typer.testing import CliRunner


class RecordingFileSystem(AbstractFileSystem):
    protocol = "recording"

    def __init__(self, backend: AbstractFileSystem, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.backend = backend
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("info", path, kwargs))
        return self.backend.info(path, **kwargs)

    def ls(
        self,
        path: str,
        detail: bool = True,  # noqa: FBT002
        **kwargs: Any,
    ) -> list[str] | list[dict[str, Any]]:
        self.calls.append(("ls", path, {"detail": detail, **kwargs}))
        return self.backend.ls(path, detail=detail, **kwargs)


class ScriptedRecordingFileSystem(AbstractFileSystem):
    protocol = "scripted-recording"

    def __init__(self, children: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.children = children
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("info", path, kwargs))
        return {"name": path, "type": "directory"}

    def ls(
        self,
        path: str,
        detail: bool = True,  # noqa: FBT002
        **kwargs: Any,
    ) -> list[str]:
        self.calls.append(("ls", path, {"detail": detail, **kwargs}))
        return list(self.children)


def test_ls_lists_one_memory_directory() -> None:
    filesystem = MemoryFileSystem(skip_instance_cache=True)
    with suppress(FileNotFoundError):
        filesystem.rm("/docs", recursive=True)
    filesystem.makedirs("/docs")
    filesystem.pipe_file("/docs/guide.md", b"guide")
    filesystem.pipe_file("/docs/notes.txt", b"notes")
    recording = RecordingFileSystem(filesystem, skip_instance_cache=True)

    try:
        result = CliRunner().invoke(
            App({"memory": recording}).typer_app,
            ["ls", "memory:/docs"],
        )
    finally:
        filesystem.rm("/docs", recursive=True)

    assert result.exit_code == 0
    assert result.stdout == "guide.md\nnotes.txt\n"
    assert result.stderr == ""
    assert recording.calls == [
        ("info", "/docs", {}),
        ("ls", "/docs", {"detail": False}),
    ]


def test_ls_prints_explicit_memory_file_without_listing_it() -> None:
    filesystem = MemoryFileSystem(skip_instance_cache=True)
    namespace = f"/issue80-slice2-{uuid4().hex}"
    file_path = f"{namespace}/guide.md"
    filesystem.makedirs(namespace)
    filesystem.pipe_file(file_path, b"guide")
    recording = RecordingFileSystem(filesystem, skip_instance_cache=True)

    try:
        result = CliRunner().invoke(
            App({"memory": recording}).typer_app,
            ["ls", f"memory:{file_path}"],
        )
    finally:
        filesystem.rm(namespace, recursive=True)

    assert result.exit_code == 0
    assert result.stdout == f"memory:{file_path}\n"
    assert result.stderr == ""
    assert recording.calls == [("info", file_path, {})]


def test_ls_hides_dot_entries_and_sorts_visible_memory_children() -> None:
    filesystem = MemoryFileSystem(skip_instance_cache=True)
    namespace = f"/issue80-slice3-{uuid4().hex}"
    directory = f"{namespace}/docs"
    filesystem.makedirs(directory)
    filesystem.pipe_file(f"{directory}/.hidden", b"hidden")
    filesystem.pipe_file(f"{directory}/z.txt", b"z")
    filesystem.pipe_file(f"{directory}/a.txt", b"a")

    try:
        result = CliRunner().invoke(
            App({"memory": filesystem}).typer_app,
            ["ls", f"memory:{directory}"],
        )
    finally:
        filesystem.rm(namespace, recursive=True)

    assert result.exit_code == 0
    assert result.stdout == "a.txt\nz.txt\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("options", "expected_stdout"),
    [
        pytest.param((), "visible.txt\n", id="default"),
        pytest.param(("-A",), ".hidden\nvisible.txt\n", id="almost-all"),
        pytest.param(("-AA",), ".hidden\nvisible.txt\n", id="grouped-almost-all"),
        pytest.param(
            ("-A", "-A"),
            ".hidden\nvisible.txt\n",
            id="repeated-almost-all",
        ),
    ],
)
def test_ls_almost_all_selects_backend_dot_entries(
    options: tuple[str, ...],
    expected_stdout: str,
) -> None:
    filesystem = ScriptedRecordingFileSystem(
        ["/docs/visible.txt", "/docs/..", "/docs/.hidden", "/docs/."],
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", *options, "scripted:/docs"],
    )

    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""
    assert filesystem.calls == [
        ("info", "/docs", {}),
        ("ls", "/docs", {"detail": False}),
    ]


def test_ls_sorts_backend_children_with_current_c_locale() -> None:
    filesystem = ScriptedRecordingFileSystem(
        ["/docs/z.txt", "/docs/m.txt", "/docs/a.txt"],
        skip_instance_cache=True,
    )
    previous_locale = locale.setlocale(locale.LC_COLLATE)

    try:
        controlled_locale = locale.setlocale(locale.LC_COLLATE, "C")
        result = CliRunner().invoke(
            App({"scripted": filesystem}).typer_app,
            ["ls", "scripted:/docs"],
        )
        assert locale.setlocale(locale.LC_COLLATE) == controlled_locale
    finally:
        locale.setlocale(locale.LC_COLLATE, previous_locale)

    assert result.exit_code == 0
    assert result.stdout == "a.txt\nm.txt\nz.txt\n"
    assert result.stderr == ""
    assert filesystem.calls == [
        ("info", "/docs", {}),
        ("ls", "/docs", {"detail": False}),
    ]


def test_ls_groups_cross_filesystem_file_before_directory(tmp_path: Any) -> None:
    local_directory = tmp_path / "docs"
    local_directory.mkdir()
    (local_directory / "z.txt").write_bytes(b"z")
    (local_directory / "a.txt").write_bytes(b"a")
    local_filesystem = RecordingFileSystem(
        LocalFileSystem(skip_instance_cache=True),
        skip_instance_cache=True,
    )
    directory_operand = f"local:{local_directory}"

    memory_filesystem = MemoryFileSystem(skip_instance_cache=True)
    memory_namespace = f"/issue80-slice5-{uuid4().hex}"
    memory_file = f"{memory_namespace}/guide.md"
    memory_filesystem.makedirs(memory_namespace)
    memory_filesystem.pipe_file(memory_file, b"guide")
    memory_recording = RecordingFileSystem(
        memory_filesystem,
        skip_instance_cache=True,
    )
    file_operand = f"memory:{memory_file}"

    try:
        result = CliRunner().invoke(
            App(
                {
                    "local": local_filesystem,
                    "memory": memory_recording,
                }
            ).typer_app,
            ["ls", directory_operand, file_operand],
        )
    finally:
        memory_filesystem.rm(memory_namespace, recursive=True)

    assert result.exit_code == 0
    assert result.stdout == (f"{file_operand}\n\n{directory_operand}:\na.txt\nz.txt\n")
    assert result.stderr == ""
    assert local_filesystem.calls == [
        ("info", str(local_directory), {}),
        ("ls", str(local_directory), {"detail": False}),
    ]
    assert memory_recording.calls == [("info", memory_file, {})]


def test_ls_sorts_multi_operand_blocks_after_original_order_processing() -> None:
    backend = MemoryFileSystem(skip_instance_cache=True)
    namespace = f"/issue80-multi-{uuid4().hex}"
    adir = f"{namespace}/adir"
    empty = f"{namespace}/empty"
    zdir = f"{namespace}/zdir"
    afile = f"{namespace}/afile"
    zfile = f"{namespace}/zfile"
    backend.makedirs(adir)
    backend.makedirs(empty)
    backend.makedirs(zdir)
    backend.pipe_file(afile, b"a")
    backend.pipe_file(zfile, b"z")
    backend.pipe_file(f"{adir}/b.txt", b"b")
    backend.pipe_file(f"{adir}/a.txt", b"a")
    backend.pipe_file(f"{zdir}/z.txt", b"z")
    filesystem = RecordingFileSystem(backend, skip_instance_cache=True)
    adir_operand = f"memory:{adir}"
    empty_operand = f"memory:{empty}"
    zdir_operand = f"memory:{zdir}"
    afile_operand = f"memory:{afile}"
    zfile_operand = f"memory:{zfile}"
    previous_locale = locale.setlocale(locale.LC_COLLATE)

    try:
        controlled_locale = locale.setlocale(locale.LC_COLLATE, "C")
        result = CliRunner().invoke(
            App({"memory": filesystem}).typer_app,
            [
                "ls",
                zdir_operand,
                zfile_operand,
                empty_operand,
                afile_operand,
                adir_operand,
                zfile_operand,
            ],
        )
        assert locale.setlocale(locale.LC_COLLATE) == controlled_locale
    finally:
        backend.rm(namespace, recursive=True)
        locale.setlocale(locale.LC_COLLATE, previous_locale)

    assert result.exit_code == 0
    assert result.stdout == (
        f"{afile_operand}\n"
        f"{zfile_operand}\n"
        f"{zfile_operand}\n\n"
        f"{adir_operand}:\na.txt\nb.txt\n\n"
        f"{empty_operand}:\n\n"
        f"{zdir_operand}:\nz.txt\n"
    )
    assert result.stderr == ""
    assert filesystem.calls == [
        ("info", zdir, {}),
        ("ls", zdir, {"detail": False}),
        ("info", zfile, {}),
        ("info", empty, {}),
        ("ls", empty, {"detail": False}),
        ("info", afile, {}),
        ("info", adir, {}),
        ("ls", adir, {"detail": False}),
        ("info", zfile, {}),
    ]


def test_ls_rejects_unsupported_option_before_backend_calls() -> None:
    filesystem = RecordingFileSystem(
        MemoryFileSystem(skip_instance_cache=True),
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"memory": filesystem}).typer_app,
        ["ls", "-a", "memory:/docs"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: -a: unsupported option\n"
    assert filesystem.calls == []


def test_ls_treats_option_like_argument_after_delimiter_as_operand() -> None:
    filesystem = RecordingFileSystem(
        MemoryFileSystem(skip_instance_cache=True),
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"memory": filesystem}).typer_app,
        ["ls", "--", "-a"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: -a: invalid mapped filesystem operand\n"
    assert filesystem.calls == []


def test_ls_preflights_unknown_name_before_any_backend_calls() -> None:
    backend = MemoryFileSystem(skip_instance_cache=True)
    namespace = f"/issue80-slice8-{uuid4().hex}"
    backend.makedirs(namespace)
    filesystem = RecordingFileSystem(backend, skip_instance_cache=True)

    try:
        result = CliRunner().invoke(
            App({"memory": filesystem}).typer_app,
            ["ls", f"memory:{namespace}", "unknown:/x"],
        )
    finally:
        backend.rm(namespace, recursive=True)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == ("ls: unknown:/x: unknown filesystem (known: memory)\n")
    assert filesystem.calls == []


def test_ls_rejects_missing_mapped_filesystem_operand() -> None:
    filesystem = RecordingFileSystem(
        MemoryFileSystem(skip_instance_cache=True),
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"memory": filesystem}).typer_app,
        ["ls"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: missing mapped filesystem operand\n"
    assert filesystem.calls == []


def test_ls_treats_lone_hyphen_as_malformed_operand() -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        App({"memory": filesystem}).typer_app,
        ["ls", "memory:/valid", "-"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: -: invalid mapped filesystem operand\n"
    assert filesystem.calls == []


@pytest.mark.parametrize(
    ("malformed_operand", "expected_stderr"),
    [
        (
            "memory:/bad\nname",
            "ls: memory:/bad\\nname: invalid mapped filesystem operand\n",
        ),
        (
            "mem\0ory:/x",
            "ls: mem\\0ory:/x: invalid mapped filesystem operand\n",
        ),
    ],
)
def test_ls_rejects_control_character_operand_during_complete_preflight(
    malformed_operand: str,
    expected_stderr: str,
) -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        App({"memory": filesystem}).typer_app,
        ["ls", "memory:/valid", malformed_operand],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    assert filesystem.calls == []


def test_ls_escapes_configured_names_in_unknown_filesystem_diagnostic() -> None:
    first = ScriptedRecordingFileSystem([], skip_instance_cache=True)
    second = ScriptedRecordingFileSystem([], skip_instance_cache=True)
    previous_locale = locale.setlocale(locale.LC_COLLATE)

    try:
        locale.setlocale(locale.LC_COLLATE, "C")
        result = CliRunner().invoke(
            App({"a\nname": first, "b\0name": second}).typer_app,
            ["ls", "unknown:/x"],
        )
    finally:
        locale.setlocale(locale.LC_COLLATE, previous_locale)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "ls: unknown:/x: unknown filesystem (known: a\\nname, b\\0name)\n"
    )
    assert first.calls == []
    assert second.calls == []


def test_ls_preserves_colons_in_memory_file_path() -> None:
    backend = MemoryFileSystem(skip_instance_cache=True)
    namespace = f"/issue80-operand-{uuid4().hex}"
    file_path = f"{namespace}/a:b:c"
    backend.makedirs(namespace)
    backend.pipe_file(file_path, b"colons")
    filesystem = RecordingFileSystem(backend, skip_instance_cache=True)
    operand = f"memory:{file_path}"

    try:
        result = CliRunner().invoke(
            App({"memory": filesystem}).typer_app,
            ["ls", operand],
        )
    finally:
        backend.rm(namespace, recursive=True)

    assert result.exit_code == 0
    assert result.stdout == f"{operand}\n"
    assert result.stderr == ""
    assert filesystem.calls == [("info", file_path, {})]
