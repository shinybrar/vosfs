from __future__ import annotations

import locale
from contextlib import suppress
from typing import Any
from uuid import uuid4

import pytest
from fsspec import AbstractFileSystem
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
