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
