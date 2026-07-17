"""Base file-only ``rm`` tests through the public embedded-command seam."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, NoReturn

import pytest
import typer

from ._support import _invoke_rm, _RecordingSource, _source_must_not_run

if TYPE_CHECKING:
    from collections.abc import Callable


def test_rm_removes_one_file_without_stdout() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_rm(["memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert [(event[0], *event[2:-1]) for event in events] == [
        ("factory",),
        ("enter",),
        ("info", "/docs/notes.txt"),
        ("rm_file", "/docs/notes.txt"),
        ("info", "/docs/notes.txt"),
        ("exit",),
    ]
    assert not any(event[0] in {"rm", "rmdir", "ls"} for event in events)


def test_rm_removes_many_files_without_stdout() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_rm(
        ["memory:/docs/a.txt", "memory:/docs/b.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert [event[2] for event in events if event[0] in {"info", "rm_file"}] == [
        "/docs/a.txt",
        "/docs/a.txt",
        "/docs/a.txt",
        "/docs/b.txt",
        "/docs/b.txt",
        "/docs/b.txt",
    ]


def test_rm_acquires_distinct_sources_before_reusing_them() -> None:
    events: list[tuple[object, ...]] = []
    shared_source = _RecordingSource(events)

    result = _invoke_rm(
        ["alpha:/one", "beta:/two", "alpha:/three"],
        sources={
            "beta": shared_source,
            "alpha": shared_source,
            "unused": _source_must_not_run,
        },
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert [(event[0], *event[1:-1]) for event in events] == [
        ("factory",),
        ("enter", 1),
        ("factory",),
        ("enter", 2),
        ("info", 1, "/one"),
        ("rm_file", 1, "/one"),
        ("info", 1, "/one"),
        ("info", 2, "/two"),
        ("rm_file", 2, "/two"),
        ("info", 2, "/two"),
        ("info", 1, "/three"),
        ("rm_file", 1, "/three"),
        ("info", 1, "/three"),
        ("exit", 2),
        ("exit", 1),
    ]


def test_rm_continues_after_an_earlier_success() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        info_by_path={
            "/docs/good": {"type": "file"},
            "/docs/bad": {"type": "directory"},
        },
    )

    result = _invoke_rm(
        ["memory:/docs/good", "memory:/docs/bad"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: memory:/docs/bad: is a directory\n"
    assert [event[2] for event in events if event[0] == "rm_file"] == ["/docs/good"]


def test_rm_continues_after_an_earlier_failure() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        info_by_path={
            "/docs/bad": {"type": "directory"},
            "/docs/good": {"type": "file"},
        },
    )

    result = _invoke_rm(
        ["memory:/docs/bad", "memory:/docs/good"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: memory:/docs/bad: is a directory\n"
    assert [event[2] for event in events if event[0] == "rm_file"] == ["/docs/good"]


def test_rm_processes_repeated_operands_independently() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_rm(
        ["memory:/docs/notes.txt", "memory:/docs/notes.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: memory:/docs/notes.txt: not found\n"
    assert [event[0] for event in events if event[0] in {"info", "rm_file"}] == [
        "info",
        "rm_file",
        "info",
        "info",
    ]


def test_rm_rejects_a_missing_mapped_filesystem_operand() -> None:
    result = _invoke_rm([])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "rm: missing mapped filesystem operand\n"


def test_rm_force_without_operands_succeeds_without_source_entry() -> None:
    source_calls = 0

    def source_must_not_run() -> object:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_rm(["-f"], sources={"memory": source_must_not_run})

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert source_calls == 0


def test_rm_force_ignores_missing_operands_and_removes_later_files() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        info_by_path={"/docs/missing.txt": FileNotFoundError("missing")},
    )

    result = _invoke_rm(
        ["-f", "memory:/docs/missing.txt", "memory:/docs/notes.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert [event[2] for event in events if event[0] == "rm_file"] == [
        "/docs/notes.txt"
    ]


def test_rm_force_succeeds_when_all_operands_are_missing() -> None:
    source = _RecordingSource(
        [],
        info_by_path={
            "/docs/first.txt": FileNotFoundError(),
            "/docs/second.txt": FileNotFoundError(),
        },
    )

    result = _invoke_rm(
        ["-f", "memory:/docs/first.txt", "memory:/docs/second.txt"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert [event[0] for event in source.events].count("rm_file") == 0


@pytest.mark.parametrize(
    "arguments",
    [["-f", "-f"], ["-ff"], ["-fff"]],
)
def test_rm_force_accepts_repeated_and_grouped_flags(arguments: list[str]) -> None:
    result = _invoke_rm(arguments)

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "option",
    [
        "-d",
        "-R",
        "-r",
        "-v",
        "-i",
        "-l",
        "--force",
        "--recursive",
        "-A",
        "-h",
        "--help=value",
        "-fr",
        "-fd",
        "-Rf",
        "-fi",
    ],
)
def test_rm_rejects_every_option_without_entering_sources(option: str) -> None:
    result = _invoke_rm([option, "memory:/file"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == f"rm: {option}: unsupported option\n"


@pytest.mark.parametrize("option", ["-R", "-r"])
def test_rm_recursive_options_are_equivalent_source_free_rejections(
    option: str,
) -> None:
    factory_calls = 0

    def source_must_not_run() -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError

    result = _invoke_rm(
        [option, "memory:/docs"],
        sources={"memory": source_must_not_run},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        f"rm: {option}: unsupported option\n",
    )
    assert factory_calls == 0


@pytest.mark.parametrize(
    ("error_factory", "category"),
    [
        (PermissionError, "permission denied"),
        (RuntimeError, "backend failure (RuntimeError): "),
    ],
)
def test_rm_force_reports_non_missing_pre_mutation_failures(
    error_factory: Callable[[], Exception],
    category: str,
) -> None:
    source = _RecordingSource([], info_error=error_factory())

    result = _invoke_rm(["-f", "memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"rm: memory:/docs/notes.txt: {category}\n"


@pytest.mark.parametrize(
    "error",
    [TimeoutError("request timed out"), RuntimeError("service timeout")],
)
def test_rm_force_reports_timeouts_before_mutation(error: Exception) -> None:
    source = _RecordingSource([], info_error=error)

    result = _invoke_rm(["-f", "memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "rm: memory:/docs/notes.txt: backend failure "
        f"({type(error).__name__}): {error}\n"
    )
    assert [event[0] for event in source.events].count("rm_file") == 0


def test_rm_force_continues_mixed_operands_in_order() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        info_by_path={
            "/docs/missing.txt": FileNotFoundError("missing"),
            "/docs/failing.txt": PermissionError("denied"),
        },
    )

    result = _invoke_rm(
        [
            "-f",
            "memory:/docs/missing.txt",
            "memory:/docs/existing.txt",
            "memory:/docs/failing.txt",
        ],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: memory:/docs/failing.txt: permission denied\n"
    assert [
        (event[0], event[2]) for event in events if event[0] in {"info", "rm_file"}
    ] == [
        ("info", "/docs/missing.txt"),
        ("info", "/docs/existing.txt"),
        ("rm_file", "/docs/existing.txt"),
        ("info", "/docs/existing.txt"),
        ("info", "/docs/failing.txt"),
    ]


def test_rm_force_confirms_many_file_removals() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_rm(
        ["-f", "memory:/docs/a.txt", "memory:/docs/b.txt", "memory:/docs/c.txt"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert [
        (event[0], event[2]) for event in events if event[0] in {"info", "rm_file"}
    ] == [
        ("info", "/docs/a.txt"),
        ("rm_file", "/docs/a.txt"),
        ("info", "/docs/a.txt"),
        ("info", "/docs/b.txt"),
        ("rm_file", "/docs/b.txt"),
        ("info", "/docs/b.txt"),
        ("info", "/docs/c.txt"),
        ("rm_file", "/docs/c.txt"),
        ("info", "/docs/c.txt"),
    ]


def test_rm_force_accepts_operand_after_option_terminator() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_rm(["-f", "--", "name:/file"], sources={"name": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert [event[0] for event in events] == [
        "factory",
        "enter",
        "info",
        "rm_file",
        "info",
        "exit",
    ]


@pytest.mark.parametrize("arguments", [["memory:/file", "-f"], ["-i"], ["--force"]])
def test_rm_force_profile_rejects_unsupported_options_before_source_entry(
    arguments: list[str],
) -> None:
    source_calls = 0

    def source_must_not_run() -> object:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_rm(arguments, sources={"memory": source_must_not_run})

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "unsupported option" in result.stderr
    assert source_calls == 0


@pytest.mark.parametrize(
    ("info_result", "category"),
    [
        ({"type": "directory"}, "is a directory"),
        ({"type": "link"}, "incompatible result"),
    ],
)
def test_rm_force_preserves_non_file_failures(
    info_result: object,
    category: str,
) -> None:
    source = _RecordingSource([], info_result=info_result)

    result = _invoke_rm(["-f", "memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"rm: memory:/docs: {category}\n"


def test_rm_force_preserves_uncertain_mutation_failure() -> None:
    source = _RecordingSource([], rm_file_error=PermissionError())

    result = _invoke_rm(["-f", "memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: memory:/docs/notes.txt: uncertain mutation state\n"


def test_rm_force_keeps_post_mutation_not_found_uncertain() -> None:
    source = _RecordingSource([], rm_file_error=FileNotFoundError("raced removal"))

    result = _invoke_rm(["-f", "memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: memory:/docs/notes.txt: uncertain mutation state\n"


def test_rm_force_uses_distinct_sources_and_skips_missing_operands() -> None:
    events: list[tuple[object, ...]] = []
    alpha = _RecordingSource(
        events,
        info_by_path={"/docs/missing.txt": FileNotFoundError("missing")},
    )
    beta = _RecordingSource(events)

    result = _invoke_rm(
        ["-f", "alpha:/docs/missing.txt", "beta:/docs/notes.txt"],
        sources={"alpha": alpha, "beta": beta},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert alpha.call_count == beta.call_count == 1
    assert [event[2] for event in events if event[0] == "rm_file"] == [
        "/docs/notes.txt"
    ]


def test_rm_force_preserves_cancellation() -> None:
    control = asyncio.CancelledError()
    source = _RecordingSource([], rm_file_error=control)

    with pytest.raises(asyncio.CancelledError) as caught:
        _invoke_rm(["-f", "memory:/docs/notes.txt"], sources={"memory": source})

    assert caught.value is control
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is asyncio.CancelledError
    assert exception is control
    assert traceback is not None


def test_rm_force_reports_cleanup_failure() -> None:
    source = _RecordingSource([], exit_error=OSError("cleanup failed"))

    result = _invoke_rm(["-f", "memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert (
        result.stderr == "rm: memory: source exit failure (OSError): cleanup failed\n"
    )


def test_rm_accepts_operand_after_option_terminator() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_rm(
        ["--", "memory:/docs/notes.txt"],
        sources={"memory": source},
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert [event[0] for event in events] == [
        "factory",
        "enter",
        "info",
        "rm_file",
        "info",
        "exit",
    ]


def test_rm_treats_dashed_tokens_after_terminator_as_operands() -> None:
    result = _invoke_rm(["--", "-f"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "rm: -f: invalid mapped filesystem operand\n"


@pytest.mark.parametrize(
    ("path", "rendered"),
    [
        ("memory:/", "memory:/"),
        ("memory:/.", "memory:/."),
        ("memory:/..", "memory:/.."),
        ("memory:/docs/.", "memory:/docs/."),
        ("memory:/docs/..", "memory:/docs/.."),
        ("memory:/./", "memory:/./"),
        ("memory:/docs/./", "memory:/docs/./"),
        ("memory:/docs/../", "memory:/docs/../"),
    ],
)
def test_rm_rejects_root_and_final_dot_paths_before_source_entry(
    path: str,
    rendered: str,
) -> None:
    source_calls = 0

    def source_must_not_run() -> object:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_rm([path], sources={"memory": source_must_not_run})

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == f"rm: {rendered}: rejected path\n"
    assert source_calls == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["memory:/docs/a.txt", "memory:/"],
        ["memory:/", "memory:/docs/a.txt"],
        ["memory:/docs/a.txt", "memory:/docs/."],
        ["memory:/docs/..", "memory:/docs/a.txt"],
    ],
)
def test_rm_rejects_whole_argv_destructive_guards_before_any_factory(
    arguments: list[str],
) -> None:
    source_calls = 0

    def source_must_not_run() -> object:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_rm(arguments, sources={"memory": source_must_not_run})

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "rejected path" in result.stderr
    assert source_calls == 0


@pytest.mark.parametrize(
    ("arguments", "rendered"),
    [
        (["memory:"], "memory:"),
        (["memory:relative"], "memory:relative"),
        (["/bare"], "/bare"),
        ([":/path"], ":/path"),
        (["-"], "-"),
        (["memory:/bad\0path"], "memory:/bad\\0path"),
        (["memory:/bad\npath"], "memory:/bad\\npath"),
        (["--", "-f"], "-f"),
        (["--", "--"], "--"),
    ],
)
def test_rm_rejects_malformed_mapped_filesystem_operands(
    arguments: list[str],
    rendered: str,
) -> None:
    result = _invoke_rm(arguments)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (f"rm: {rendered}: invalid mapped filesystem operand\n")


def test_rm_reports_unknown_names_with_locale_sorted_known_names() -> None:
    result = _invoke_rm(
        ["other:/file"],
        sources={
            "zeta": _source_must_not_run,
            "alpha": _source_must_not_run,
        },
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "rm: other:/file: unknown filesystem (known: alpha, zeta)\n"
    )


@pytest.mark.parametrize("info_result", [{"type": "directory"}, {"type": "link"}, {}])
def test_rm_rejects_non_file_types_without_calling_rm_file(info_result: object) -> None:
    source = _RecordingSource([], info_result=info_result)

    result = _invoke_rm(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    expected = (
        "rm: memory:/docs: is a directory\n"
        if info_result == {"type": "directory"}
        else "rm: memory:/docs: incompatible result\n"
    )
    assert result.stderr == expected
    assert [event[0] for event in source.events].count("rm_file") == 0


def test_rm_rejects_a_missing_file() -> None:
    source = _RecordingSource([], info_error=FileNotFoundError("missing"))

    result = _invoke_rm(["memory:/missing"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: memory:/missing: not found\n"
    assert [event[0] for event in source.events].count("rm_file") == 0


@pytest.mark.parametrize(
    ("error_factory", "category"),
    [
        (FileNotFoundError, "not found"),
        (PermissionError, "permission denied"),
        (IsADirectoryError, "is a directory"),
        (NotImplementedError, "unsupported operation"),
        (RuntimeError, "backend failure (RuntimeError): "),
    ],
)
def test_rm_maps_pre_mutation_failures_to_locked_categories(
    error_factory: Callable[[], Exception],
    category: str,
) -> None:
    error = error_factory()
    source = _RecordingSource([], info_error=error)

    result = _invoke_rm(["memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"rm: memory:/docs/notes.txt: {category}\n"
    assert [event[0] for event in source.events].count("rm_file") == 0


@pytest.mark.parametrize("stage", ["rm_file", "post_info"])
@pytest.mark.parametrize(
    "error_factory",
    [
        FileNotFoundError,
        PermissionError,
        IsADirectoryError,
        NotImplementedError,
        RuntimeError,
    ],
)
def test_rm_reports_uncertain_mutation_after_delete_attempt(
    stage: str,
    error_factory: Callable[[], Exception],
) -> None:
    if stage == "post_info" and error_factory is FileNotFoundError:
        pytest.skip("post-check FileNotFoundError confirms success")
    error = error_factory()
    source = _RecordingSource(
        [],
        rm_file_error=error if stage == "rm_file" else None,
        post_info_by_path={
            "/docs/notes.txt": error if stage == "post_info" else FileNotFoundError()
        },
    )

    result = _invoke_rm(["memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == ("rm: memory:/docs/notes.txt: uncertain mutation state\n")


def test_rm_rejects_when_post_check_shows_the_file_still_present() -> None:
    source = _RecordingSource(
        [],
        post_info_by_path={"/docs/notes.txt": {"type": "file"}},
    )

    result = _invoke_rm(["memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == ("rm: memory:/docs/notes.txt: uncertain mutation state\n")


def test_rm_refuses_an_active_same_thread_event_loop(monkeypatch) -> None:
    real_run = asyncio.run
    recording_run = pytest.importorskip("unittest.mock").Mock(wraps=real_run)

    async def invoke() -> object:
        monkeypatch.setattr(asyncio, "run", recording_run)
        return _invoke_rm(["memory:/file"])

    result = real_run(invoke())

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "rm: cannot run from an active event loop\n"
    assert recording_run.call_count == 0


class _ControlFlow(BaseException):
    pass


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_rm_preserves_control_flow_unchanged(control: BaseException) -> None:
    source = _RecordingSource([], rm_file_error=control)

    with pytest.raises(type(control)) as caught:
        _invoke_rm(["memory:/docs/notes.txt"], sources={"memory": source})

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(control)
    assert exception is control
    assert traceback is not None


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_rm_preserves_earlier_removal_when_later_operand_is_cancelled(
    control: BaseException,
) -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        rm_file_by_path={"/docs/second.txt": control},
    )

    with pytest.raises(type(control)) as caught:
        _invoke_rm(
            ["memory:/docs/first.txt", "memory:/docs/second.txt"],
            sources={"memory": source},
        )

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    assert [event[2] for event in events if event[0] == "rm_file"] == [
        "/docs/first.txt",
        "/docs/second.txt",
    ]
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(control)
    assert exception is control
    assert traceback is not None


def test_rm_preserves_backend_error_when_its_diagnostic_write_fails(
    monkeypatch,
) -> None:
    backend_error = PermissionError("denied")
    renderer_error = RuntimeError("stderr failed")
    source = _RecordingSource([], info_error=backend_error)

    def fail_diagnostic(
        _message: object = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args
        if kwargs.get("err") is True:
            raise renderer_error
        raise AssertionError

    monkeypatch.setattr(typer, "echo", fail_diagnostic)

    result = _invoke_rm(["memory:/file"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.exception is renderer_error
    assert result.stdout == ""
    assert result.stderr == ""
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is PermissionError
    assert exception is backend_error
    assert traceback is not None


def test_rm_stops_acquisition_after_a_source_factory_failure() -> None:
    events: list[tuple[object, ...]] = []
    factory_error = ValueError("factory")
    first = _RecordingSource(events, exit_result=True)

    def broken_source() -> NoReturn:
        raise factory_error

    result = _invoke_rm(
        ["first:/one", "broken:/two", "later:/three"],
        sources={
            "first": first,
            "broken": broken_source,
            "later": _source_must_not_run,
        },
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "rm: broken: source factory failure (ValueError): factory\n"
    )
    assert [event[0] for event in events] == ["factory", "enter", "exit"]


def test_rm_reports_source_exit_failures_in_reverse_order() -> None:
    events: list[tuple[object, ...]] = []
    alpha = _RecordingSource(events, exit_error=OSError("alpha exit"))
    beta = _RecordingSource(events, exit_error=RuntimeError("beta exit"))

    result = _invoke_rm(
        ["alpha:/one", "beta:/two"],
        sources={"alpha": alpha, "beta": beta},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "rm: beta: source exit failure (RuntimeError): beta exit\n"
        "rm: alpha: source exit failure (OSError): alpha exit\n"
    )


@pytest.mark.parametrize("arguments", [["--help"], ["-f", "--help"]])
def test_rm_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = _invoke_rm(arguments)

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert result.stderr == ""


def test_rm_help_describes_force_profile() -> None:
    result = _invoke_rm(["--help"])

    assert result.exit_code == 0
    plain_help = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout)
    assert "rm -f ignores files already missing before removal" in " ".join(
        plain_help.split()
    )
    assert result.stderr == ""


def test_rm_accepts_hidden_file_paths_that_are_not_final_dot_components() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_rm(["memory:/.hidden"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert [event[2] for event in events if event[0] in {"info", "rm_file"}] == [
        "/.hidden",
        "/.hidden",
        "/.hidden",
    ]


def test_rm_never_calls_rm_or_rmdir_primitives() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events, trap_rmdir=True)

    result = _invoke_rm(["memory:/docs/notes.txt"], sources={"memory": source})

    assert result.exit_code == 0
    assert [event[0] for event in events if event[0] == "rm_file"] == ["rm_file"]
    assert not any(event[0] in {"rm", "rmdir"} for event in events)

    filesystem = source.contexts[0].filesystem

    async def prove_traps() -> None:
        with pytest.raises(
            AssertionError, match="_rm must not be called by file-only removal"
        ):
            await filesystem._rm("/docs/notes.txt")
        with pytest.raises(
            AssertionError, match="_rmdir must not be called by file-only removal"
        ):
            await filesystem._rmdir("/docs")

    asyncio.run(prove_traps())
    assert [event[0] for event in events if event[0] in {"rm", "rmdir"}] == [
        "rm",
        "rmdir",
    ]


def test_rm_reuses_unlink_confirmed_removal_boundary() -> None:
    from fsspec_cli._rm import _confirmed_rm_file as rm_confirmed
    from fsspec_cli._unlink import _confirmed_rm_file as unlink_confirmed

    assert rm_confirmed is unlink_confirmed
