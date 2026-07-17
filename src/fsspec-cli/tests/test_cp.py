"""Same-source two-operand ``cp`` tests through the public embedded-command seam."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn

import pytest

from ._support import (
    _invoke_cp,
    _RecordingFileSystem,
    _RecordingSource,
    _source_must_not_run,
)


def _file_source(  # noqa: PLR0913 - compact recording fixture.
    events: list[tuple[object, ...]] | None = None,
    *,
    content: bytes = b"payload",
    source_path: str = "/docs/notes.txt",
    parent: str = "/docs",
    file_contents: dict[str, bytes] | None = None,
    directories: set[str] | None = None,
    info_by_path: dict[str, object] | None = None,
    **kwargs: object,
) -> _RecordingSource:
    contents = dict(file_contents or {})
    contents.setdefault(source_path, content)
    dirs = set(directories or ())
    dirs.add(parent)
    dirs.add("/")
    return _RecordingSource(
        events if events is not None else [],
        file_contents=contents,
        directories=dirs,
        info_by_path=info_by_path or {},
        get_file_content=content,
        **kwargs,  # type: ignore[arg-type]
    )


def test_cp_copies_one_file_without_stdout() -> None:
    events: list[tuple[object, ...]] = []
    source = _file_source(events)

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert source.file_contents["/docs/copy.txt"] == b"payload"
    assert source.file_contents["/docs/notes.txt"] == b"payload"
    cp_events = [event for event in events if event[0] == "cp_file"]
    assert len(cp_events) == 1
    assert cp_events[0][2:4] == ("/docs/notes.txt", "/docs/copy.txt")


def test_cp_copies_file_between_distinct_configured_sources() -> None:
    source = _file_source(content=b"\0\xff cross-source")
    destination = _file_source(
        source_path="/other.txt",
        parent="/out",
        directories={"/", "/out"},
    )

    result = _invoke_cp(
        ["source:/docs/notes.txt", "destination:/out/copy.txt"],
        sources={"source": source, "destination": destination},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert source.file_contents["/docs/notes.txt"] == b"\0\xff cross-source"
    assert destination.file_contents["/out/copy.txt"] == b"\0\xff cross-source"
    assert [event[0] for event in source.events].count("get_file") == 1
    assert [event[0] for event in destination.events].count("put_file") == 1
    assert [event[0] for event in destination.events].count("get_file") == 1


def test_cp_rejects_cross_source_same_path_on_shared_backend_before_mutation() -> None:
    source = _file_source()
    filesystem = _RecordingFileSystem(source, 1)

    @asynccontextmanager
    async def shared_filesystem() -> _RecordingFileSystem:
        yield filesystem

    result = _invoke_cp(
        ["left:/docs/notes.txt", "right:/docs/notes.txt"],
        sources={"left": shared_filesystem, "right": shared_filesystem},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "cp: left:/docs/notes.txt: same path\n",
    )
    assert not any(event[0] in {"get_file", "put_file"} for event in source.events)


def test_cp_rejects_same_size_wrong_cross_source_destination() -> None:
    source = _file_source(content=b"correct")
    destination = _file_source(
        source_path="/other.txt",
        parent="/out",
        directories={"/", "/out"},
    )

    def corrupt_upload(_local_path: str, remote_path: str) -> None:
        filesystem = destination.contexts[0].filesystem
        filesystem._file_contents[remote_path] = b"corrupt"
        destination.file_contents[remote_path] = b"corrupt"

    destination.put_file_hook = corrupt_upload
    result = _invoke_cp(
        ["source:/docs/notes.txt", "destination:/out/copy.txt"],
        sources={"source": source, "destination": destination},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "cp: destination:/out/copy.txt: verification failure; "
        "destination residue may remain\n"
    )
    assert source.file_contents["/docs/notes.txt"] == b"correct"
    assert destination.file_contents["/out/copy.txt"] == b"corrupt"


def test_cp_rejects_cross_source_digest_collision_with_different_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _file_source(content=b"correct")
    destination = _file_source(
        source_path="/other.txt",
        parent="/out",
        directories={"/", "/out"},
    )

    def corrupt_upload(_local_path: str, remote_path: str) -> None:
        filesystem = destination.contexts[0].filesystem
        filesystem._file_contents[remote_path] = b"corrupt"
        destination.file_contents[remote_path] = b"corrupt"

    monkeypatch.setattr(
        "fsspec_cli._cp._file_digest",
        lambda _path: (b"same-digest", None),
        raising=False,
    )
    destination.put_file_hook = corrupt_upload
    result = _invoke_cp(
        ["source:/docs/notes.txt", "destination:/out/copy.txt"],
        sources={"source": source, "destination": destination},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: destination:/out/copy.txt: verification failure; "
        "destination residue may remain\n"
    )


def test_cp_hides_local_temporary_path_in_cross_source_staging_diagnostic() -> None:
    staged_paths: list[str] = []

    def fail_staging(local_path: str) -> None:
        staged_paths.append(local_path)
        raise OSError(f"local staging failed: {local_path}")  # noqa: EM102, TRY003

    source = _file_source(get_file_by_path={"/docs/notes.txt": fail_staging})
    destination = _file_source(
        source_path="/other.txt",
        parent="/out",
        directories={"/", "/out"},
    )

    result = _invoke_cp(
        ["source:/docs/notes.txt", "destination:/out/copy.txt"],
        sources={"source": source, "destination": destination},
    )

    assert result.exit_code == 1
    assert result.stderr == "cp: source:/docs/notes.txt: staging failure (OSError)\n"
    assert len(staged_paths) == 1
    assert staged_paths[0] not in result.stderr


def test_cp_appends_basename_when_destination_is_directory() -> None:
    source = _file_source(directories={"/", "/docs", "/docs/out"})

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/out"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert source.file_contents["/docs/out/notes.txt"] == b"payload"
    cp_events = [event for event in source.events if event[0] == "cp_file"]
    assert cp_events[0][2:4] == ("/docs/notes.txt", "/docs/out/notes.txt")


def test_cp_replaces_existing_destination_file() -> None:
    source = _file_source(
        file_contents={
            "/docs/notes.txt": b"new-bytes",
            "/docs/copy.txt": b"old-bytes",
        }
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert source.file_contents["/docs/copy.txt"] == b"new-bytes"
    assert source.file_contents["/docs/notes.txt"] == b"new-bytes"


def test_cp_rejects_same_path_before_mutation() -> None:
    source = _file_source()

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/notes.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "cp: memory:/docs/notes.txt: same path\n"
    assert [event[0] for event in source.events].count("cp_file") == 0


def test_cp_rejects_directory_destination_collision_before_mutation() -> None:
    source = _file_source(
        directories={"/", "/docs", "/docs/out", "/docs/out/notes.txt"},
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/out"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "cp: memory:/docs/out: incompatible result\n"
    assert [event[0] for event in source.events].count("cp_file") == 0


def test_cp_rejects_missing_parent() -> None:
    source = _file_source(
        info_by_path={"/missing": FileNotFoundError("missing")},
        directories={"/"},
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/missing/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "cp: memory:/missing/copy.txt: not found\n"
    assert [event[0] for event in source.events].count("cp_file") == 0


def test_cp_rejects_parent_that_is_a_file() -> None:
    source = _file_source(
        file_contents={
            "/docs/notes.txt": b"payload",
            "/docs/parent": b"not-a-dir",
        },
        directories={"/", "/docs"},
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/parent/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "cp: memory:/docs/parent/copy.txt: not a directory\n"
    assert [event[0] for event in source.events].count("cp_file") == 0


def test_cp_rejects_directory_source() -> None:
    source = _RecordingSource(
        [],
        info_by_path={"/docs": MappingProxyType({"type": "directory", "size": 0})},
        directories={"/", "/docs"},
    )

    result = _invoke_cp(
        ["memory:/docs", "memory:/docs/copy"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "cp: memory:/docs: is a directory\n"
    assert [event[0] for event in source.events].count("cp_file") == 0


def test_cp_acquires_destination_before_cross_source_backend_work() -> None:
    source = _file_source()

    result = _invoke_cp(
        ["alpha:/docs/notes.txt", "beta:/two"],
        sources={"alpha": source, "beta": _source_must_not_run},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "beta: source factory failure" in result.stderr
    assert [event[0] for event in source.events] == ["factory", "enter", "exit"]


def test_cp_uses_distinct_names_even_when_backends_are_similar() -> None:
    left = _file_source()
    right = _file_source(
        source_path="/other.txt",
        parent="/docs",
        directories={"/", "/docs"},
    )

    result = _invoke_cp(
        ["alpha:/docs/notes.txt", "beta:/docs/copy.txt"],
        sources={"alpha": left, "beta": right},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert left.call_count == 1
    assert right.call_count == 1
    assert right.file_contents["/docs/copy.txt"] == b"payload"


def test_cp_rejects_missing_operands() -> None:
    result = _invoke_cp([])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "cp: missing mapped filesystem operand\n"


def test_cp_rejects_one_operand() -> None:
    result = _invoke_cp(["memory:/one"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "cp: missing mapped filesystem operand\n"


def test_cp_rejects_extra_operands_without_entering_sources() -> None:
    result = _invoke_cp(["memory:/one", "memory:/two", "memory:/three"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "cp: extra operand\n"


@pytest.mark.parametrize(
    "option",
    [
        "-f",
        "-i",
        "-p",
        "-R",
        "-r",
        "-H",
        "-L",
        "-P",
        "--force",
        "-A",
        "-h",
        "--help=value",
        "-Rf",
    ],
)
def test_cp_rejects_every_option_without_entering_sources(option: str) -> None:
    result = _invoke_cp([option, "memory:/a", "memory:/b"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == f"cp: {option}: unsupported option\n"


def test_cp_accepts_operands_after_option_terminator() -> None:
    source = _file_source()

    result = _invoke_cp(
        ["--", "memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("empty", b""),
        ("binary", b"\x00\xff\xfe binary"),
        ("large", b"x" * (1 << 20)),
    ],
)
def test_cp_copies_empty_binary_and_large_payloads(label: str, payload: bytes) -> None:
    del label
    source = _file_source(content=payload)

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert source.file_contents["/docs/copy.txt"] == payload


def test_cp_rejects_changing_source_during_verification() -> None:
    source = _file_source(content=b"original")

    def copy_then_mutate(path1: str, path2: str) -> None:
        filesystem = source.contexts[0].filesystem
        filesystem._file_contents[path2] = filesystem._file_contents[path1]
        source.file_contents[path2] = filesystem._file_contents[path1]
        filesystem._file_contents[path1] = b"changed-after-copy"
        source.file_contents[path1] = b"changed-after-copy"

    source.cp_file_hook = copy_then_mutate

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: verification failure; "
        "destination residue may remain\n"
    )
    assert "/docs/copy.txt" in source.file_contents


def test_cp_reports_truncated_destination_as_verification_failure() -> None:
    source = _file_source(content=b"abcdef")

    def truncate_destination(path1: str, path2: str) -> None:
        del path1
        filesystem = source.contexts[0].filesystem
        filesystem._file_contents[path2] = b"abc"
        source.file_contents[path2] = b"abc"

    source.cp_file_hook = truncate_destination

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: verification failure; "
        "destination residue may remain\n"
    )


def test_cp_reports_copy_exception_as_uncertain_residue() -> None:
    source = _file_source(cp_file_error=RuntimeError("relay-failed"))

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: uncertain mutation state; "
        "destination residue may remain\n"
    )


def test_cp_never_deletes_source_on_failure() -> None:
    source = _file_source(cp_file_error=OSError("boom"))

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert source.file_contents["/docs/notes.txt"] == b"payload"
    assert [event[0] for event in source.events].count("rm_file") == 0
    assert [event[0] for event in source.events].count("rm") == 0


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), type("_ControlFlow", (BaseException,), {})("stop")],
)
def test_cp_preserves_control_flow(control: BaseException) -> None:
    source = _file_source(cp_file_error=control)

    with pytest.raises(type(control)) as caught:
        _invoke_cp(
            ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
            sources={"memory": source},
        )

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control


def test_cp_refuses_an_active_same_thread_event_loop(monkeypatch) -> None:
    real_run = asyncio.run
    recording_run = pytest.importorskip("unittest.mock").Mock(wraps=real_run)

    async def invoke() -> object:
        monkeypatch.setattr(asyncio, "run", recording_run)
        return _invoke_cp(["memory:/a", "memory:/b"])

    result = real_run(invoke())

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "cp: cannot run from an active event loop\n"
    assert recording_run.call_count == 0


def test_cp_reports_unknown_names_with_locale_sorted_known_names() -> None:
    result = _invoke_cp(
        ["other:/a", "other:/b"],
        sources={
            "zeta": _source_must_not_run,
            "alpha": _source_must_not_run,
        },
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == ("cp: other:/a: unknown filesystem (known: alpha, zeta)\n")


@pytest.mark.parametrize("arguments", [["--help"], ["-f", "--help"]])
def test_cp_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = _invoke_cp(arguments)

    assert result.exit_code == 0
    assert result.stdout != ""


def test_cp_cancels_without_claiming_success() -> None:
    source = _file_source()

    def cancel(_path1: str, _path2: str) -> NoReturn:
        raise asyncio.CancelledError

    source.cp_file_hook = cancel

    with pytest.raises(asyncio.CancelledError):
        _invoke_cp(
            ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
            sources={"memory": source},
        )
    assert "/docs/copy.txt" not in source.contexts[0].filesystem._file_contents


def test_cp_uses_exact_configured_name_identity() -> None:
    source = _file_source()

    result = _invoke_cp(
        ["vault:/docs/notes.txt", "vault:/docs/copy.txt"],
        sources={"vault": source},
    )

    assert result.exit_code == 0
    assert source.call_count == 1


def test_cp_reports_same_size_wrong_destination_as_verification_failure() -> None:
    source = _file_source(content=b"abcdef")

    def corrupt_same_size(path1: str, path2: str) -> None:
        filesystem = source.contexts[0].filesystem
        wrong = b"x" * len(filesystem._file_contents[path1])
        filesystem._file_contents[path2] = wrong
        source.file_contents[path2] = wrong

    source.cp_file_hook = corrupt_same_size

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: verification failure; "
        "destination residue may remain\n"
    )
    assert source.file_contents["/docs/copy.txt"] == b"xxxxxx"


def test_cp_reports_post_copy_destination_type_mismatch() -> None:
    source = _file_source(
        post_info_by_path={
            "/docs/copy.txt": MappingProxyType({"type": "directory", "size": 0})
        },
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: verification failure; "
        "destination residue may remain\n"
    )


def test_cp_reports_post_copy_destination_info_failure() -> None:
    source = _file_source(
        post_info_by_path={
            "/docs/copy.txt": PermissionError("verify-denied"),
        }
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: verification failure; "
        "destination residue may remain\n"
    )


def test_cp_reports_source_staging_failure_during_verification() -> None:
    source = _file_source(
        get_file_by_path={"/docs/notes.txt": OSError("staging-source")},
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: staging failure (OSError); "
        "destination residue may remain\n"
    )


def test_cp_reports_destination_staging_failure_during_verification() -> None:
    source = _file_source(
        get_file_by_path={"/docs/copy.txt": OSError("staging-dest")},
    )

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: staging failure (OSError); "
        "destination residue may remain\n"
    )


def test_cp_reports_compare_failure_during_verification(monkeypatch) -> None:
    source = _file_source()

    def fail_compare(left: str, right: str) -> tuple[bool, Exception | None]:
        del left, right
        return False, OSError("compare-failed")

    monkeypatch.setattr("fsspec_cli._cp._files_match", fail_compare)

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: staging failure (OSError); "
        "destination residue may remain\n"
    )


def test_cp_reports_verification_cleanup_failure(monkeypatch) -> None:
    source = _file_source()
    real_unlink = Path.unlink
    unlink_attempts = 0

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        nonlocal unlink_attempts
        if self.name.startswith(("fsspec-cli-cp-src-", "fsspec-cli-cp-dst-")):
            unlink_attempts += 1
            if unlink_attempts == 1:
                message = "unlink-denied"
                raise OSError(message)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    result = _invoke_cp(
        ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "cp: memory:/docs/copy.txt: staging failure (OSError); "
        "destination residue may remain\n"
    )
    assert unlink_attempts >= 1


class _ControlFlow(BaseException):
    pass


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_cp_removes_temporary_on_first_verification_get_file_cancellation(
    control: BaseException,
) -> None:
    temps: list[str] = []
    source = _file_source()

    def cancel_source_stage(lpath: str) -> None:
        temps.append(lpath)
        Path(lpath).write_bytes(b"payload")
        raise control

    source.get_file_by_path = {"/docs/notes.txt": cancel_source_stage}

    with pytest.raises(type(control)) as caught:
        _invoke_cp(
            ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
            sources={"memory": source},
        )

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    assert len(temps) == 1
    assert "fsspec-cli-cp-src-" in temps[0]
    assert not Path(temps[0]).exists()


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_cp_removes_both_temporaries_on_second_verification_get_file_cancellation(
    control: BaseException,
) -> None:
    temps: list[str] = []
    source = _file_source()

    def stage_source(lpath: str) -> None:
        temps.append(lpath)
        Path(lpath).write_bytes(b"payload")

    def cancel_destination_stage(lpath: str) -> None:
        temps.append(lpath)
        Path(lpath).write_bytes(b"payload")
        raise control

    source.get_file_by_path = {
        "/docs/notes.txt": stage_source,
        "/docs/copy.txt": cancel_destination_stage,
    }

    with pytest.raises(type(control)) as caught:
        _invoke_cp(
            ["memory:/docs/notes.txt", "memory:/docs/copy.txt"],
            sources={"memory": source},
        )

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    assert len(temps) == 2
    assert "fsspec-cli-cp-src-" in temps[0]
    assert "fsspec-cli-cp-dst-" in temps[1]
    assert not Path(temps[0]).exists()
    assert not Path(temps[1]).exists()
