"""Base ``mkdir`` tests through the public embedded-command seam."""

import asyncio
from collections.abc import Callable
from typing import NoReturn
from unittest.mock import Mock

import pytest
import typer

from ._support import _invoke_mkdir, _RecordingSource, _source_must_not_run


def test_mkdir_creates_one_directory_without_stdout() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_mkdir(["memory:/docs/new"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert [(event[0], *event[2:-1]) for event in events] == [
        ("factory",),
        ("enter",),
        ("mkdir", "/docs/new", False),
        ("info", "/docs/new"),
        ("exit",),
    ]


def test_mkdir_acquires_distinct_sources_before_reusing_them() -> None:
    events: list[tuple[object, ...]] = []
    shared_source = _RecordingSource(events)

    result = _invoke_mkdir(
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
        ("mkdir", 1, "/one", False),
        ("info", 1, "/one"),
        ("mkdir", 2, "/two", False),
        ("info", 2, "/two"),
        ("mkdir", 1, "/three", False),
        ("info", 1, "/three"),
        ("exit", 2),
        ("exit", 1),
    ]


def test_mkdir_continues_after_an_earlier_success() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        mkdir_by_path={"/docs/bad": FileNotFoundError("missing parent")},
    )

    result = _invoke_mkdir(
        ["memory:/docs/good", "memory:/docs/bad"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "mkdir: memory:/docs/bad: not found\n"
    assert [event[2] for event in events if event[0] == "mkdir"] == [
        "/docs/good",
        "/docs/bad",
    ]


def test_mkdir_rejects_duplicate_operands_on_the_second_attempt() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_mkdir(
        ["memory:/docs/new", "memory:/docs/new"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "mkdir: memory:/docs/new: file exists\n"
    assert [event[0] for event in events].count("mkdir") == 2


def test_mkdir_asserts_create_parents_false_at_the_operation_boundary() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events)

    result = _invoke_mkdir(["memory:/docs/new"], sources={"memory": source})

    assert result.exit_code == 0
    mkdir_events = [event for event in events if event[0] == "mkdir"]
    assert len(mkdir_events) == 1
    assert mkdir_events[0][3] is False


@pytest.mark.parametrize(
    ("post_info", "category"),
    [
        ({"type": "file"}, "incompatible result"),
        ({"type": "link"}, "incompatible result"),
        (None, "incompatible result"),
        ({"name": "/docs/new"}, "incompatible result"),
    ],
)
def test_mkdir_rejects_malformed_post_verify_state(
    post_info: object,
    category: str,
) -> None:
    source = _RecordingSource([], post_info_result=post_info)

    result = _invoke_mkdir(["memory:/docs/new"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"mkdir: memory:/docs/new: {category}\n"


def test_mkdir_rejects_missing_post_verify_result() -> None:
    source = _RecordingSource([], post_info_result=None)

    result = _invoke_mkdir(["memory:/docs/new"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "mkdir: memory:/docs/new: incompatible result\n"


@pytest.mark.parametrize("stage", ["mkdir", "info"])
@pytest.mark.parametrize(
    ("error_factory", "category"),
    [
        (FileNotFoundError, "not found"),
        (FileExistsError, "file exists"),
        (PermissionError, "permission denied"),
        (NotADirectoryError, "not a directory"),
        (NotImplementedError, "unsupported operation"),
        (RuntimeError, "backend failure (RuntimeError): "),
        (
            lambda: RuntimeError("backend\\\0\r\n"),
            "backend failure (RuntimeError): backend\\\\\\0\\r\\n",
        ),
    ],
)
def test_mkdir_maps_runtime_failures_to_locked_categories(
    stage: str,
    error_factory: Callable[[], Exception],
    category: str,
) -> None:
    error = error_factory()
    source = _RecordingSource(
        [],
        mkdir_error=error if stage == "mkdir" else None,
        post_info_by_path={
            "/docs/new": error if stage == "info" else {"type": "directory"}
        },
    )

    result = _invoke_mkdir(["memory:/docs/new"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"mkdir: memory:/docs/new: {category}\n"


def test_mkdir_rejects_a_missing_mapped_filesystem_operand() -> None:
    result = _invoke_mkdir([])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "mkdir: missing mapped filesystem operand\n"


@pytest.mark.parametrize(
    "option",
    ["-p", "-m", "-pm", "--parents", "--mode", "-h", "--help=value"],
)
def test_mkdir_rejects_unsupported_options_without_entering_sources(
    option: str,
) -> None:
    result = _invoke_mkdir([option, "memory:/docs/new"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == f"mkdir: {option}: unsupported option\n"


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
        (["--", "-p"], "-p"),
        (["--", "--"], "--"),
    ],
)
def test_mkdir_rejects_malformed_mapped_filesystem_operands(
    arguments: list[str],
    rendered: str,
) -> None:
    result = _invoke_mkdir(arguments)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (f"mkdir: {rendered}: invalid mapped filesystem operand\n")


def test_mkdir_reports_unknown_names_with_locale_sorted_known_names() -> None:
    result = _invoke_mkdir(
        ["other:/docs/new"],
        sources={
            "zeta": _source_must_not_run,
            "alpha": _source_must_not_run,
        },
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "mkdir: other:/docs/new: unknown filesystem (known: alpha, zeta)\n"
    )


def test_mkdir_refuses_an_active_same_thread_event_loop(monkeypatch) -> None:
    real_run = asyncio.run
    recording_run = Mock(wraps=real_run)

    async def invoke() -> object:
        monkeypatch.setattr(asyncio, "run", recording_run)
        return _invoke_mkdir(["memory:/docs/new"])

    result = real_run(invoke())

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "mkdir: cannot run from an active event loop\n"
    assert recording_run.call_count == 0


class _ControlFlow(BaseException):
    pass


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_mkdir_preserves_control_flow_unchanged(control: BaseException) -> None:
    source = _RecordingSource([], mkdir_error=control)

    with pytest.raises(type(control)) as caught:
        _invoke_mkdir(["memory:/docs/new"], sources={"memory": source})

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(control)
    assert exception is control
    assert traceback is not None


def test_mkdir_preserves_backend_error_when_its_diagnostic_write_fails(
    monkeypatch,
) -> None:
    backend_error = PermissionError("denied")
    renderer_error = RuntimeError("stderr failed")
    source = _RecordingSource([], mkdir_error=backend_error)

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

    result = _invoke_mkdir(["memory:/docs/new"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.exception is renderer_error
    assert result.stdout == ""
    assert result.stderr == ""
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is PermissionError
    assert exception is backend_error
    assert traceback is not None


def test_mkdir_stops_acquisition_after_a_source_factory_failure() -> None:
    events: list[tuple[object, ...]] = []
    factory_error = ValueError("factory")
    first = _RecordingSource(events, exit_result=True)

    def broken_source() -> NoReturn:
        raise factory_error

    result = _invoke_mkdir(
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
        "mkdir: broken: source factory failure (ValueError): factory\n"
    )
    assert [event[0] for event in events] == ["factory", "enter", "exit"]


def test_mkdir_reports_source_exit_failures_in_reverse_order() -> None:
    events: list[tuple[object, ...]] = []
    alpha = _RecordingSource(events, exit_error=OSError("alpha exit"))
    beta = _RecordingSource(events, exit_error=RuntimeError("beta exit"))

    result = _invoke_mkdir(
        ["alpha:/one", "beta:/two"],
        sources={"alpha": alpha, "beta": beta},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "mkdir: beta: source exit failure (RuntimeError): beta exit\n"
        "mkdir: alpha: source exit failure (OSError): alpha exit\n"
    )
