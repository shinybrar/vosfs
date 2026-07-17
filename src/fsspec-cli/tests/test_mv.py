"""Same-source two-operand ``mv`` tests through public embedded-command seam."""

from __future__ import annotations

import asyncio

import pytest
from fsspec_cli import App
from typer.testing import CliRunner

from ._support import _RecordingSource, _source_must_not_run


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


def test_mv_moves_one_file_without_stdout() -> None:
    source = _source()

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source.file_contents == {"/docs/moved.txt": b"payload"}


def test_mv_resolves_directory_target_and_replaces_file() -> None:
    source = _source(
        contents={"/docs/notes.txt": b"new", "/docs/out/notes.txt": b"old"},
        directories={"/", "/docs", "/docs/out"},
    )

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/out")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert source.file_contents == {"/docs/out/notes.txt": b"new"}


def test_mv_same_path_is_noop_after_resolution() -> None:
    source = _source()

    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/notes.txt")

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
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


def test_mv_rejects_destination_content_mismatch_and_source_residue() -> None:
    source = _source()

    def corrupt(_path1: str, path2: str) -> None:
        filesystem = source.contexts[0].filesystem
        filesystem._file_contents[path2] = b"wrong"
        source.file_contents[path2] = b"wrong"

    source.mv_hook = corrupt
    result = _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")

    assert result.exit_code == 1
    assert "verification failure; destination residue may remain" in result.stderr
    assert source.file_contents["/docs/notes.txt"] == b"payload"


def test_mv_preserves_cancellation() -> None:
    source = _source(mv_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _invoke(source, "memory:/docs/notes.txt", "memory:/docs/moved.txt")
