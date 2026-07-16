from __future__ import annotations

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
