from __future__ import annotations

import errno
import locale
import os
import select
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fsspec import AbstractFileSystem
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem

pytest.importorskip("typer")
import typer
from conftest import make_fs
from prototypes.fsspec_cli_plain_ls import App
from typer.testing import CliRunner
from vospace_sim import VOSpaceSim

if os.name == "posix":
    import pty
    import termios


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


class RuntimeScriptedFileSystem(AbstractFileSystem):
    protocol = "runtime-scripted"

    def __init__(
        self,
        *,
        info_results: dict[str, object],
        ls_results: dict[str, object] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.info_results = info_results
        self.ls_results = ls_results or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    @staticmethod
    def _resolve(value: object) -> Any:
        if isinstance(value, BaseException):
            raise value
        return value

    def info(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(("info", path, kwargs))
        return self._resolve(self.info_results[path])

    def ls(
        self,
        path: str,
        detail: bool = True,  # noqa: FBT002
        **kwargs: Any,
    ) -> Any:
        self.calls.append(("ls", path, {"detail": detail, **kwargs}))
        return self._resolve(self.ls_results[path])


@dataclass(frozen=True)
class BackendMatrixCase:
    name: str
    filesystem: AbstractFileSystem
    root_path: str

    def path(self, suffix: str) -> str:
        if not suffix:
            return self.root_path
        if self.root_path == "/":
            return f"/{suffix}"
        return f"{self.root_path}/{suffix}"

    def operand(self, suffix: str) -> str:
        return f"{self.name}:{self.path(suffix)}"


def _seed_local_matrix(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "root.txt").write_bytes(b"root")
    (root / ".hidden-root").write_bytes(b"hidden")
    (root / "docs" / "guide.md").write_bytes(b"guide")
    (root / "docs" / ".hidden").write_bytes(b"hidden")


def _seed_memory_matrix(filesystem: MemoryFileSystem) -> None:
    filesystem.makedirs("/docs")
    filesystem.makedirs("/empty")
    filesystem.pipe_file("/root.txt", b"root")
    filesystem.pipe_file("/.hidden-root", b"hidden")
    filesystem.pipe_file("/docs/guide.md", b"guide")
    filesystem.pipe_file("/docs/.hidden", b"hidden")


@pytest.fixture(params=("local", "memory", "vos"))
def backend_matrix_case(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Any:
    if request.param == "local":
        # LocalFileSystem has no chroot option. This temporary directory is its
        # hermetic dataset root; literal ``local:/`` remains a host-root probe.
        root = tmp_path / "local-root"
        root.mkdir()
        _seed_local_matrix(root)
        yield BackendMatrixCase(
            "local",
            LocalFileSystem(skip_instance_cache=True),
            str(root),
        )
        return

    if request.param == "memory":
        isolated_type = type(
            f"Issue80MemoryFileSystem{uuid4().hex}",
            (MemoryFileSystem,),
            {"store": {}, "pseudo_dirs": [""]},
        )
        filesystem = isolated_type(skip_instance_cache=True)
        _seed_memory_matrix(filesystem)
        try:
            yield BackendMatrixCase("memory", filesystem, "/")
        finally:
            filesystem.store.clear()
            filesystem.pseudo_dirs[:] = [""]
        return

    router = request.getfixturevalue("router")
    (
        VOSpaceSim()
        .add_container("/docs")
        .add_container("/empty")
        .add_file("/root.txt", b"root")
        .add_file("/.hidden-root", b"hidden")
        .add_file("/docs/guide.md", b"guide")
        .add_file("/docs/.hidden", b"hidden")
        .install(router)
    )
    filesystem = make_fs(router, use_listings_cache=False)
    try:
        yield BackendMatrixCase("vos", filesystem, "/")
    finally:
        filesystem.close()


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


def test_ls_accepts_almost_all_option_after_operand() -> None:
    filesystem = ScriptedRecordingFileSystem(
        ["/docs/visible.txt", "/docs/.hidden"],
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", "scripted:/docs", "-A"],
    )

    assert result.exit_code == 0
    assert result.stdout == ".hidden\nvisible.txt\n"
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


@pytest.mark.parametrize(
    ("arguments", "expected_stderr"),
    [
        pytest.param(
            ("not-a-mapped-operand", "-l"),
            ("ls: not-a-mapped-operand: invalid mapped filesystem operand\n"),
            id="malformed-operand-before-option",
        ),
        pytest.param(
            ("unknown:/path", "-l"),
            "ls: unknown:/path: unknown filesystem (known: scripted)\n",
            id="unknown-name-before-option",
        ),
    ],
)
def test_ls_reports_first_preflight_error_in_argument_order(
    arguments: tuple[str, ...],
    expected_stderr: str,
) -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", *arguments],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    assert filesystem.calls == []


@pytest.mark.parametrize(
    ("unsupported_option", "expected_stderr"),
    [
        pytest.param("-a", "ls: -a: unsupported option\n", id="almost-all"),
        pytest.param("-h", "ls: -h: unsupported option\n", id="short-help"),
        pytest.param("-l", "ls: -l: unsupported option\n", id="long-listing"),
        pytest.param(
            "-Al",
            "ls: -Al: unsupported option\n",
            id="mixed-group",
        ),
        pytest.param(
            "--help=x",
            "ls: --help=x: unsupported option\n",
            id="help-with-value",
        ),
    ],
)
def test_ls_rejects_unsupported_option_before_backend_calls(
    unsupported_option: str,
    expected_stderr: str,
) -> None:
    filesystem = RecordingFileSystem(
        MemoryFileSystem(skip_instance_cache=True),
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"memory": filesystem}).typer_app,
        ["ls", unsupported_option, "memory:/docs"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    assert filesystem.calls == []


@pytest.mark.parametrize(
    ("operand", "expected_stderr"),
    [
        pytest.param(
            "-a",
            "ls: -a: invalid mapped filesystem operand\n",
            id="short-option",
        ),
        pytest.param(
            "--help",
            "ls: --help: invalid mapped filesystem operand\n",
            id="framework-help",
        ),
    ],
)
def test_ls_treats_option_like_argument_after_delimiter_as_operand(
    operand: str,
    expected_stderr: str,
) -> None:
    filesystem = RecordingFileSystem(
        MemoryFileSystem(skip_instance_cache=True),
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"memory": filesystem}).typer_app,
        ["ls", "--", operand],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    assert filesystem.calls == []


def test_ls_delegates_exact_help_to_framework_without_backend_calls() -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", "--help"],
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert filesystem.calls == []


@pytest.mark.parametrize(
    ("operand", "expected_stderr"),
    [
        pytest.param(
            "/bare/path",
            "ls: /bare/path: invalid mapped filesystem operand\n",
            id="bare-path",
        ),
        pytest.param(
            "scripted:",
            "ls: scripted:: invalid mapped filesystem operand\n",
            id="missing-path",
        ),
        pytest.param(
            "scripted:relative",
            "ls: scripted:relative: invalid mapped filesystem operand\n",
            id="relative-path",
        ),
        pytest.param(
            ":/path",
            "ls: :/path: invalid mapped filesystem operand\n",
            id="empty-name",
        ),
    ],
)
def test_ls_rejects_malformed_mapped_filesystem_grammar(
    operand: str,
    expected_stderr: str,
) -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", operand],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == expected_stderr
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
            App({"a\nname": first, "b\\name": second}).typer_app,
            ["ls", "unknown:/x"],
        )
    finally:
        locale.setlocale(locale.LC_COLLATE, previous_locale)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "ls: unknown:/x: unknown filesystem (known: a\\nname, b\\\\name)\n"
    )
    assert first.calls == []
    assert second.calls == []


def test_ls_escapes_single_nul_configured_name_without_relative_sorting() -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        App({"nul\0name": filesystem}).typer_app,
        ["ls", "unknown:/x"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == ("ls: unknown:/x: unknown filesystem (known: nul\\0name)\n")
    assert filesystem.calls == []


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


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/docs/./../guide.md", id="dot-segments"),
        pytest.param("/~/guide.md", id="tilde-segment"),
        pytest.param("//docs///guide.md///", id="repeated-and-trailing-slashes"),
    ],
)
def test_ls_passes_noncanonical_file_path_spelling_unchanged(path: str) -> None:
    filesystem = RuntimeScriptedFileSystem(
        info_results={path: {"type": "file"}},
        skip_instance_cache=True,
    )
    operand = f"scripted:{path}"

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", operand],
    )

    assert result.exit_code == 0
    assert result.stdout == f"{operand}\n"
    assert result.stderr == ""
    assert filesystem.calls == [("info", path, {})]


@pytest.mark.parametrize(
    (
        "path",
        "operation",
        "error",
        "expected_stderr",
    ),
    [
        pytest.param(
            "/problem",
            "info",
            FileNotFoundError(),
            "ls: scripted:/problem: not found\n",
            id="not-found-info",
        ),
        pytest.param(
            "/problem",
            "ls",
            PermissionError(),
            "ls: scripted:/problem: permission denied\n",
            id="permission-denied-ls",
        ),
        pytest.param(
            "/problem",
            "info",
            NotADirectoryError(),
            "ls: scripted:/problem: not a directory\n",
            id="not-a-directory-info",
        ),
        pytest.param(
            "/problem",
            "ls",
            NotImplementedError(),
            "ls: scripted:/problem: unsupported operation\n",
            id="unsupported-operation-ls",
        ),
        pytest.param(
            "/bad\\path\rname",
            "info",
            RuntimeError("boom\\x\0y\r\nz"),
            (
                "ls: scripted:/bad\\\\path\\rname: "
                "backend failure (RuntimeError): boom\\\\x\\0y\\r\\nz\n"
            ),
            id="fallback-escaping-info",
        ),
        pytest.param(
            "/problem",
            "info",
            RuntimeError(),
            "ls: scripted:/problem: backend failure (RuntimeError): \n",
            id="fallback-empty-message-info",
        ),
    ],
)
def test_ls_maps_runtime_exception_category(
    path: str,
    operation: str,
    error: Exception,
    expected_stderr: str,
) -> None:
    info_result: object = error if operation == "info" else {"type": "directory"}
    ls_results = {path: error} if operation == "ls" else None
    filesystem = RuntimeScriptedFileSystem(
        info_results={path: info_result},
        ls_results=ls_results,
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", f"scripted:{path}"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    expected_calls = [("info", path, {})]
    if operation == "ls":
        expected_calls.append(("ls", path, {"detail": False}))
    assert filesystem.calls == expected_calls


@pytest.mark.parametrize(
    ("path", "operation", "backend_result", "expected_stderr"),
    [
        pytest.param(
            "/problem",
            "info",
            [],
            "ls: scripted:/problem: incompatible result\n",
            id="info-non-mapping",
        ),
        pytest.param(
            "/problem",
            "info",
            {},
            "ls: scripted:/problem: incompatible result\n",
            id="info-missing-type",
        ),
        pytest.param(
            "/problem",
            "info",
            {"type": 1},
            "ls: scripted:/problem: incompatible result\n",
            id="info-non-string-type",
        ),
        pytest.param(
            "/problem",
            "info",
            {"type": "link"},
            "ls: scripted:/problem: incompatible result\n",
            id="info-unsupported-type",
        ),
        pytest.param(
            "/docs///",
            "ls",
            ("/docs/guide.md",),
            "ls: scripted:/docs///: incompatible result\n",
            id="ls-non-list",
        ),
        pytest.param(
            "/docs///",
            "ls",
            ["/docs/guide.md", 1],
            "ls: scripted:/docs///: incompatible result\n",
            id="ls-non-string-child",
        ),
        pytest.param(
            "/docs///",
            "ls",
            ["memory:///docs/guide.md"],
            "ls: scripted:/docs///: incompatible result\n",
            id="ls-protocol-bearing-child",
        ),
        pytest.param(
            "/docs///",
            "ls",
            ["/docs/"],
            "ls: scripted:/docs///: incompatible result\n",
            id="ls-empty-suffix",
        ),
        pytest.param(
            "/docs///",
            "ls",
            ["/docs/nested/guide.md"],
            "ls: scripted:/docs///: incompatible result\n",
            id="ls-nested-child",
        ),
        pytest.param(
            "/docs///",
            "ls",
            ["/docs/.hidden\nname"],
            "ls: scripted:/docs///: incompatible result\n",
            id="ls-newline-child-before-selection",
        ),
        pytest.param(
            "/docs///",
            "ls",
            ["/docs/.hidden\0name"],
            "ls: scripted:/docs///: incompatible result\n",
            id="ls-nul-child-before-selection",
        ),
    ],
)
def test_ls_rejects_incompatible_backend_result(
    path: str,
    operation: str,
    backend_result: object,
    expected_stderr: str,
) -> None:
    info_result = backend_result if operation == "info" else {"type": "directory"}
    ls_results = {path: backend_result} if operation == "ls" else None
    filesystem = RuntimeScriptedFileSystem(
        info_results={path: info_result},
        ls_results=ls_results,
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", f"scripted:{path}"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    expected_calls = [("info", path, {})]
    if operation == "ls":
        expected_calls.append(("ls", path, {"detail": False}))
    assert filesystem.calls == expected_calls


def test_ls_continues_after_failures_without_leaking_partial_operand_output() -> None:
    filesystem = RuntimeScriptedFileSystem(
        info_results={
            "/bad-dir": {"type": "directory"},
            "/z-file": {"type": "file"},
            "/missing": FileNotFoundError(),
            "/good-dir": {"type": "directory"},
            "/a-file": {"type": "file"},
        },
        ls_results={
            "/bad-dir": [
                "/bad-dir/should-not-leak.txt",
                "/bad-dir/nested/invalid.txt",
            ],
            "/good-dir": ["/good-dir/z.txt", "/good-dir/a.txt"],
        },
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        [
            "ls",
            "scripted:/bad-dir",
            "scripted:/z-file",
            "scripted:/missing",
            "scripted:/good-dir",
            "scripted:/a-file",
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == (
        "scripted:/a-file\nscripted:/z-file\n\nscripted:/good-dir:\na.txt\nz.txt\n"
    )
    assert result.stderr == (
        "ls: scripted:/bad-dir: incompatible result\nls: scripted:/missing: not found\n"
    )
    assert filesystem.calls == [
        ("info", "/bad-dir", {}),
        ("ls", "/bad-dir", {"detail": False}),
        ("info", "/z-file", {}),
        ("info", "/missing", {}),
        ("info", "/good-dir", {}),
        ("ls", "/good-dir", {"detail": False}),
        ("info", "/a-file", {}),
    ]


@pytest.mark.parametrize(
    ("suffix", "options", "expected_stdout", "is_directory"),
    [
        pytest.param(
            "",
            (),
            "docs\nempty\nroot.txt\n",
            True,
            id="root",
        ),
        pytest.param(
            "docs",
            (),
            "guide.md\n",
            True,
            id="nonempty-directory",
        ),
        pytest.param(
            "empty",
            (),
            "",
            True,
            id="empty-directory",
        ),
        pytest.param(
            "root.txt",
            (),
            None,
            False,
            id="file",
        ),
        pytest.param(
            "docs/.hidden",
            (),
            None,
            False,
            id="explicit-dot-file",
        ),
        pytest.param(
            "docs",
            ("-A",),
            ".hidden\nguide.md\n",
            True,
            id="almost-all",
        ),
    ],
)
def test_ls_matches_hermetic_backend_matrix(
    backend_matrix_case: BackendMatrixCase,
    suffix: str,
    options: tuple[str, ...],
    expected_stdout: str | None,
    is_directory: bool,
) -> None:
    recording = RecordingFileSystem(
        backend_matrix_case.filesystem,
        skip_instance_cache=True,
    )
    path = backend_matrix_case.path(suffix)
    operand = backend_matrix_case.operand(suffix)
    expected_stdout = expected_stdout if expected_stdout is not None else f"{operand}\n"
    previous_locale = locale.setlocale(locale.LC_COLLATE)

    try:
        locale.setlocale(locale.LC_COLLATE, "C")
        result = CliRunner().invoke(
            App({backend_matrix_case.name: recording}).typer_app,
            ["ls", *options, operand],
        )
    finally:
        locale.setlocale(locale.LC_COLLATE, previous_locale)

    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""
    expected_calls = [("info", path, {})]
    if is_directory:
        expected_calls.append(("ls", path, {"detail": False}))
    assert recording.calls == expected_calls


def _embedded_host(filesystem: AbstractFileSystem) -> typer.Typer:
    host = typer.Typer(add_completion=False)
    host.add_typer(
        App({"memory": filesystem}).typer_app,
        name="data",
    )
    return host


def test_ls_runs_unchanged_when_embedded_in_host_typer_app() -> None:
    filesystem = ScriptedRecordingFileSystem(
        ["/docs/guide.md"],
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        _embedded_host(filesystem),
        ["data", "ls", "memory:/docs"],
    )

    assert result.exit_code == 0
    assert result.stdout == "guide.md\n"
    assert result.stderr == ""
    assert filesystem.calls == [
        ("info", "/docs", {}),
        ("ls", "/docs", {"detail": False}),
    ]


@pytest.mark.parametrize(
    ("arguments", "expected_stderr"),
    [
        pytest.param(
            ("-a", "memory:/docs"),
            "ls: -a: unsupported option\n",
            id="unsupported-option",
        ),
        pytest.param(
            ("--", "-a"),
            "ls: -a: invalid mapped filesystem operand\n",
            id="delimiter",
        ),
    ],
)
def test_embedded_ls_preserves_raw_argument_contract(
    arguments: tuple[str, ...],
    expected_stderr: str,
) -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        _embedded_host(filesystem),
        ["data", "ls", *arguments],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    assert filesystem.calls == []


def test_embedded_ls_keeps_framework_help_short_circuit() -> None:
    filesystem = ScriptedRecordingFileSystem([], skip_instance_cache=True)

    result = CliRunner().invoke(
        _embedded_host(filesystem),
        ["data", "ls", "--help"],
    )

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "data ls" in result.stdout
    assert result.stderr == ""
    assert filesystem.calls == []


def test_ls_sorts_with_controlled_swedish_locale_without_changing_it() -> None:
    filesystem = ScriptedRecordingFileSystem(
        ["/docs/ä.txt", "/docs/å.txt", "/docs/z.txt", "/docs/a.txt"],
        skip_instance_cache=True,
    )
    previous_locale = locale.setlocale(locale.LC_COLLATE)

    try:
        try:
            controlled_locale = locale.setlocale(locale.LC_COLLATE, "sv_SE.UTF-8")
        except locale.Error:
            pytest.skip("sv_SE.UTF-8 locale is unavailable")
        result = CliRunner().invoke(
            App({"scripted": filesystem}).typer_app,
            ["ls", "scripted:/docs"],
        )
        assert locale.setlocale(locale.LC_COLLATE) == controlled_locale
    finally:
        locale.setlocale(locale.LC_COLLATE, previous_locale)

    assert result.exit_code == 0
    assert result.stdout == "a.txt\nz.txt\nå.txt\nä.txt\n"
    assert result.stderr == ""
    assert filesystem.calls == [
        ("info", "/docs", {}),
        ("ls", "/docs", {"detail": False}),
    ]


def test_ls_formats_files_only_without_blank_lines() -> None:
    filesystem = RuntimeScriptedFileSystem(
        info_results={
            "/z-file": {"type": "file"},
            "/a-file": {"type": "file"},
        },
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", "scripted:/z-file", "scripted:/a-file"],
    )

    assert result.exit_code == 0
    assert result.stdout == "scripted:/a-file\nscripted:/z-file\n"
    assert result.stderr == ""
    assert filesystem.calls == [
        ("info", "/z-file", {}),
        ("info", "/a-file", {}),
    ]


def test_ls_formats_directories_only_without_leading_blank_line() -> None:
    filesystem = RuntimeScriptedFileSystem(
        info_results={
            "/z-dir": {"type": "directory"},
            "/a-dir": {"type": "directory"},
        },
        ls_results={
            "/z-dir": ["/z-dir/z.txt"],
            "/a-dir": ["/a-dir/a.txt"],
        },
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", "scripted:/z-dir", "scripted:/a-dir"],
    )

    assert result.exit_code == 0
    assert result.stdout == ("scripted:/a-dir:\na.txt\n\nscripted:/z-dir:\nz.txt\n")
    assert result.stderr == ""


def test_ls_writes_no_stdout_when_every_operand_fails() -> None:
    filesystem = RuntimeScriptedFileSystem(
        info_results={
            "/missing": FileNotFoundError(),
            "/denied": PermissionError(),
        },
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", "scripted:/missing", "scripted:/denied"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "ls: scripted:/missing: not found\nls: scripted:/denied: permission denied\n"
    )
    assert filesystem.calls == [
        ("info", "/missing", {}),
        ("info", "/denied", {}),
    ]


def test_ls_validates_trailing_slash_listing_without_changing_backend_path() -> None:
    path = "/docs///"
    filesystem = RuntimeScriptedFileSystem(
        info_results={path: {"type": "directory"}},
        ls_results={path: ["/docs/guide.md"]},
        skip_instance_cache=True,
    )

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", f"scripted:{path}"],
    )

    assert result.exit_code == 0
    assert result.stdout == "guide.md\n"
    assert result.stderr == ""
    assert filesystem.calls == [
        ("info", path, {}),
        ("ls", path, {"detail": False}),
    ]


def test_ls_uses_raw_string_to_break_equal_collation_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem = ScriptedRecordingFileSystem(
        ["/docs/z.txt", "/docs/a.txt"],
        skip_instance_cache=True,
    )
    monkeypatch.setattr(locale, "strxfrm", lambda _value: "equal")

    result = CliRunner().invoke(
        App({"scripted": filesystem}).typer_app,
        ["ls", "scripted:/docs"],
    )

    assert result.exit_code == 0
    assert result.stdout == "a.txt\nz.txt\n"
    assert result.stderr == ""


_SUBPROCESS_TIMEOUT = 10.0
_SUBPROCESS_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUBPROCESS_EXPECTED_STDOUT = b"a.txt\nz.txt\n"
_SUBPROCESS_OUTPUT_ERROR = b"ls: output: output failure (OSError): disk\\\\bad\\nline\n"
_SUBPROCESS_SOURCE = r"""
import io
import sys

from fsspec import AbstractFileSystem
from fsspec.implementations.memory import MemoryFileSystem
from prototypes.fsspec_cli_plain_ls import App


class PrefixThenFailure(io.TextIOBase):
    def __init__(self, accepted_characters: int) -> None:
        self.remaining = accepted_characters

    @property
    def encoding(self) -> str:
        return sys.__stdout__.encoding or "utf-8"

    @property
    def errors(self) -> str:
        return sys.__stdout__.errors or "strict"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, value: str) -> int:
        if not isinstance(value, str):
            raise TypeError
        accepted = min(self.remaining, len(value))
        if accepted:
            sys.__stdout__.write(value[:accepted])
            sys.__stdout__.flush()
            self.remaining -= accepted
        if accepted != len(value):
            raise OSError("disk\\bad\nline")
        return len(value)

    def flush(self) -> None:
        sys.__stdout__.flush()


class MissingFileSystem(AbstractFileSystem):
    protocol = "missing"

    def info(self, path: str, **kwargs: object) -> dict[str, object]:
        raise FileNotFoundError(path)


mode = sys.argv.pop(1)
filesystem = MemoryFileSystem(skip_instance_cache=True)
filesystem.makedirs("/docs")
filesystem.pipe_file("/docs/z.txt", b"z")
filesystem.pipe_file("/docs/a.txt", b"a")
filesystems = {"memory": filesystem}

if mode == "fail":
    sys.stdout = PrefixThenFailure(0)
elif mode == "prefix":
    sys.stdout = PrefixThenFailure(len("a.txt\n"))
elif mode == "runtime-and-fail":
    sys.stdout = PrefixThenFailure(0)
    filesystems["bad"] = MissingFileSystem(skip_instance_cache=True)
elif mode != "normal":
    raise RuntimeError(f"unknown child mode: {mode}")

App(filesystems).typer_app()
"""


def _subprocess_command(mode: str) -> list[str]:
    operands = ["memory:/docs"]
    if mode == "runtime-and-fail":
        operands.append("bad:/missing")
    return [
        sys.executable,
        "-c",
        _SUBPROCESS_SOURCE,
        mode,
        "ls",
        *operands,
    ]


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _run_redirected_subprocess(mode: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and inline test child
        _subprocess_command(mode),
        cwd=_SUBPROCESS_REPO_ROOT,
        env=_subprocess_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_SUBPROCESS_TIMEOUT,
        check=False,
    )


def _run_pty_subprocess() -> tuple[int, bytes, bytes]:  # noqa: C901, PLR0912
    if os.name != "posix":
        pytest.skip("PTY evidence requires POSIX")
    if not hasattr(termios, "ONLCR") or not hasattr(termios, "ECHO"):
        pytest.skip("required terminal flags unavailable")

    command = _subprocess_command("normal")
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    try:
        attributes = termios.tcgetattr(slave_fd)
        attributes[1] &= ~termios.ONLCR
        attributes[3] &= ~termios.ECHO
        termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)

        process = subprocess.Popen(  # noqa: S603 - fixed test child command
            command,
            cwd=_SUBPROCESS_REPO_ROOT,
            env=_subprocess_environment(),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
        )
        os.close(slave_fd)
        slave_fd = -1

        deadline = time.monotonic() + _SUBPROCESS_TIMEOUT
        chunks: list[bytes] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, _SUBPROCESS_TIMEOUT)
            readable, _, _ = select.select(
                [master_fd],
                [],
                [],
                min(0.05, remaining),
            )
            if readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)
            elif process.poll() is not None:
                break

        remaining = max(0.001, deadline - time.monotonic())
        _, stderr = process.communicate(timeout=remaining)
        return process.returncode, b"".join(chunks), stderr
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        for descriptor in (master_fd, slave_fd):
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)


def test_public_seam_redirected_output() -> None:
    result = _run_redirected_subprocess("normal")

    assert result.returncode == 0
    assert result.stdout == _SUBPROCESS_EXPECTED_STDOUT
    assert result.stderr == b""


def test_public_seam_tty_matches_redirected_output() -> None:
    redirected = _run_redirected_subprocess("normal")
    returncode, stdout, stderr = _run_pty_subprocess()

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == _SUBPROCESS_EXPECTED_STDOUT
    assert stderr == redirected.stderr == b""


def test_public_seam_broken_pipe_is_silent_runtime_failure() -> None:
    if os.name != "posix":
        pytest.skip("closed-reader pipe evidence requires POSIX")

    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(  # noqa: S603 - fixed test child command
            _subprocess_command("normal"),
            cwd=_SUBPROCESS_REPO_ROOT,
            env=_subprocess_environment(),
            stdin=subprocess.DEVNULL,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    finally:
        os.close(write_fd)

    assert result.returncode == 1
    assert result.stderr == b""


@pytest.mark.parametrize(
    ("mode", "expected_stdout"),
    [
        pytest.param("fail", b"", id="nothing-accepted"),
        pytest.param("prefix", b"a.txt\n", id="accepted-prefix-preserved"),
    ],
)
def test_public_seam_reports_stdout_oserror(
    mode: str,
    expected_stdout: bytes,
) -> None:
    result = _run_redirected_subprocess(mode)

    assert result.returncode == 1
    assert result.stdout == expected_stdout
    assert result.stderr == _SUBPROCESS_OUTPUT_ERROR


def test_output_failure_keeps_already_known_backend_diagnostics() -> None:
    result = _run_redirected_subprocess("runtime-and-fail")

    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == (
        b"ls: bad:/missing: not found\n" + _SUBPROCESS_OUTPUT_ERROR
    )
