"""Same-source two-operand ``mv`` tests through public embedded-command seam."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fsspec_cli import App
from typer.testing import CliRunner

from ._support import (
    _RecordingContext,
    _RecordingFileSystem,
    _RecordingSource,
    _source_must_not_run,
)

_MOVE_FAILED = "move failed"


def _source(
    *,
    contents: dict[str, bytes] | None = None,
    directories: set[str] | None = None,
    **kwargs: object,
) -> _RecordingSource:
    return _RecordingSource(
        [],
        file_contents=contents or {"/docs/notes.txt": b"payload"},
        directories=directories or {"/", "/docs"},
        **kwargs,  # type: ignore[arg-type]
    )


def _invoke(source: _RecordingSource, *arguments: str):
    return CliRunner().invoke(App({"memory": source}).typer_app, ["mv", *arguments])


def test_mv_help_explains_cross_source_rejection() -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app, ["mv", "--help"]
    )

    assert result.exit_code == 0
    assert "Cross-source moves are unsupported." in " ".join(result.stdout.split())
    assert result.stderr == ""


def test_mv_moves_one_file_without_stdout() -> None:
    source = _source()

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source.file_contents == {"/docs/moved.txt": b"payload"}
    assert len(source.get_file_paths) == 2
    assert all(not Path(path).exists() for path in source.get_file_paths)


def test_mv_rejects_inherited_async_move_operation() -> None:
    class InheritedMoveFileSystem(_RecordingFileSystem):
        pass

    class InheritedMoveContext(_RecordingContext):
        def __init__(self, source: _RecordingSource, source_id: int) -> None:
            super().__init__(source, source_id)
            self.filesystem = InheritedMoveFileSystem(source, source_id)

    class InheritedMoveSource(_RecordingSource):
        def __call__(self) -> InheritedMoveContext:
            self.call_count += 1
            self.events.append(("factory", self.call_count))
            context = InheritedMoveContext(self, self.call_count)
            self.contexts.append(context)
            return context

    source = InheritedMoveSource(
        [],
        file_contents={"/docs/notes.txt": b"payload"},
        directories={"/", "/docs"},
    )

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/moved.txt: unsupported operation\n",
    )
    assert source.file_contents == {"/docs/notes.txt": b"payload"}
    assert not [event for event in source.events if event[0] == "mv"]
    assert not [event for event in source.events if event[0] == "get_file"]


def test_mv_resolves_directory_target_and_replaces_file() -> None:
    source = _source(
        contents={"/docs/notes.txt": b"new", "/docs/out/notes.txt": b"old"},
        directories={"/", "/docs", "/docs/out"},
    )

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/out")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source.file_contents == {"/docs/out/notes.txt": b"new"}


def test_mv_replaces_direct_existing_file_target() -> None:
    source = _source(contents={"/docs/notes.txt": b"new", "/docs/moved.txt": b"old"})

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source.file_contents == {"/docs/moved.txt": b"new"}


def test_mv_rejects_same_backend_under_different_configured_name() -> None:
    source = _source()

    result = CliRunner().invoke(
        App({"memory": source, "alias": source}).typer_app,
        ["mv", "memory:/docs/notes.txt", "alias:/docs/moved.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "mv: cross-source move unsupported\n",
    )
    assert source.call_count == 0


def test_mv_rejects_cross_source_without_factories_or_mutation() -> None:
    source = _source(contents={"/docs/notes.txt": b"payload"})
    destination = _source(contents={"/docs/moved.txt": b"original"})
    source_contents_before = dict(source.file_contents)
    destination_contents_before = dict(destination.file_contents)

    result = CliRunner().invoke(
        App({"source": source, "destination": destination}).typer_app,
        ["mv", "source:/docs/notes.txt", "destination:/docs/moved.txt"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        "mv: cross-source move unsupported\n",
    )
    assert source.file_contents == source_contents_before
    assert destination.file_contents == destination_contents_before
    assert source.events == []
    assert destination.events == []
    assert source.call_count == 0
    assert destination.call_count == 0


def test_mv_same_path_is_noop_after_resolution() -> None:
    source = _source()

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/notes.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source.file_contents == {"/docs/notes.txt": b"payload"}
    assert not [event for event in source.events if event[0] == "mv"]


def test_mv_directory_target_spelling_resolves_to_same_path_noop() -> None:
    source = _source()

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source.file_contents == {"/docs/notes.txt": b"payload"}
    assert not [event for event in source.events if event[0] == "mv"]


def test_mv_rejects_missing_destination_parent_without_mutation() -> None:
    source = _source(info_by_path={"/absent": FileNotFoundError("/absent")})

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/absent/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/absent/moved.txt: not found\n",
    )
    assert source.file_contents == {"/docs/notes.txt": b"payload"}
    assert not [event for event in source.events if event[0] == "mv"]


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        (
            ("memory:/docs/notes.txt", "other:/docs/moved.txt"),
            "mv: cross-source move unsupported\n",
        ),
        (
            ("-f", "memory:/docs/notes.txt", "memory:/docs/moved.txt"),
            "mv: -f: unsupported option\n",
        ),
        (
            ("-i", "memory:/docs/notes.txt", "memory:/docs/moved.txt"),
            "mv: -i: unsupported option\n",
        ),
        (
            ("--interactive", "memory:/docs/notes.txt", "memory:/docs/moved.txt"),
            "mv: --interactive: unsupported option\n",
        ),
        (
            ("memory:/docs/notes.txt", "memory:/docs/moved.txt", "memory:/extra"),
            "mv: extra operand\n",
        ),
    ],
)
def test_mv_rejects_unsupported_shapes_without_source_entry(
    arguments: tuple[str, ...], diagnostic: str
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run, "other": _source_must_not_run}).typer_app,
        ["mv", *arguments],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (2, "", diagnostic)


def test_mv_rejects_directory_source_before_mutation() -> None:
    source = _source(
        contents={},
        directories={"/", "/docs"},
        info_by_path={"/docs": {"type": "directory"}},
    )

    result = _invoke(source, "memory:/docs", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs: is a directory\n",
    )
    assert not [event for event in source.events if event[0] == "mv"]


def test_mv_reports_mutation_exception_as_uncertain_residue() -> None:
    source = _source(mv_error=OSError("move failed"))

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/moved.txt: uncertain mutation state; "
        "destination residue may remain\n",
    )
    assert source.file_contents == {"/docs/notes.txt": b"payload"}
    assert len(source.get_file_paths) == 1
    assert all(not Path(path).exists() for path in source.get_file_paths)


def test_mv_reports_source_deletion_failure_after_destination_creation() -> None:
    source = _source()

    def copy_then_source_deletion_fails(path1: str, path2: str) -> None:
        filesystem = source.contexts[0].filesystem
        filesystem._file_contents[path2] = filesystem._file_contents[path1]
        source.file_contents[path2] = source.file_contents[path1]
        raise PermissionError(_MOVE_FAILED)

    source.mv_hook = copy_then_source_deletion_fails
    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/moved.txt: uncertain mutation state; "
        "destination residue may remain\n",
    )
    assert source.file_contents == {
        "/docs/notes.txt": b"payload",
        "/docs/moved.txt": b"payload",
    }


def test_mv_rejects_destination_type_mismatch_and_source_residue() -> None:
    source = _source()

    def make_directory(_path1: str, path2: str) -> None:
        filesystem = source.contexts[0].filesystem
        filesystem._directories.add(path2)
        source.directories.add(path2)

    source.mv_hook = make_directory
    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/moved.txt: verification failure; "
        "destination residue may remain\n",
    )
    assert source.file_contents == {"/docs/notes.txt": b"payload"}
    assert "/docs/moved.txt" in source.directories


@pytest.mark.parametrize("replacement", [b"short", b"invalid"])
def test_mv_rejects_destination_size_or_content_mismatch(
    replacement: bytes,
) -> None:
    source = _source()

    def corrupt(_path1: str, path2: str) -> None:
        filesystem = source.contexts[0].filesystem
        filesystem._file_contents[path2] = replacement
        source.file_contents[path2] = replacement

    source.mv_hook = corrupt
    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/moved.txt: verification failure; "
        "destination residue may remain\n",
    )
    assert source.file_contents == {
        "/docs/notes.txt": b"payload",
        "/docs/moved.txt": replacement,
    }


def test_mv_rejects_source_retained_after_complete_destination() -> None:
    source = _source()

    def copy_without_deletion(path1: str, path2: str) -> None:
        filesystem = source.contexts[0].filesystem
        filesystem._file_contents[path2] = filesystem._file_contents[path1]
        source.file_contents[path2] = source.file_contents[path1]

    source.mv_hook = copy_without_deletion
    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "mv: memory:/docs/moved.txt: verification failure; "
        "destination residue may remain\n",
    )
    assert source.file_contents == {
        "/docs/notes.txt": b"payload",
        "/docs/moved.txt": b"payload",
    }


def test_mv_cancellation_removes_temps_and_closes_source() -> None:
    source = _source(mv_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")
    assert len(source.get_file_paths) == 1
    source_staging_path = source.get_file_paths[0]
    assert not Path(source_staging_path).exists()
    assert len(source.exit_calls) == 1
    assert isinstance(source.exit_calls[0][1], asyncio.CancelledError)
