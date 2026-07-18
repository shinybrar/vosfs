"""``du`` command tests through the public embedded-command seam."""

from types import MappingProxyType
from typing import NoReturn

import pytest
import typer
from click.utils import strip_ansi

from ._support import _invoke_du, _RecordingSource


class _ExplodingMapping(dict[str, int]):
    def items(self) -> NoReturn:
        raise RuntimeError


class _DuControl(BaseException):
    pass


def test_du_renders_exact_backend_paths_atomically_after_one_call() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        du_result=MappingProxyType(
            {
                "/docs/sub/b.bin": 1536,
                "/docs/a.txt": 2,
            }
        ),
    )

    result = _invoke_du(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "2\t/docs/a.txt\n1536\t/docs/sub/b.bin\n",
        "",
    )
    assert [(event[0], *event[2:-1]) for event in events] == [
        ("factory",),
        ("enter",),
        ("du", "/docs", False),
        ("exit",),
    ]


def test_du_accepts_an_empty_detail_mapping_without_output() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events, du_result={})

    result = _invoke_du(["memory:/empty"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert [(event[0], *event[2:-1]) for event in events] == [
        ("factory",),
        ("enter",),
        ("du", "/empty", False),
        ("exit",),
    ]


@pytest.mark.parametrize(
    ("arguments", "du_result", "total", "stdout"),
    [
        (["-h", "memory:/docs"], {"/docs/a": 1536}, False, "1.5K\t/docs/a\n"),
        (["-s", "memory:/docs"], 1536, True, "1536\t/docs\n"),
        (["-sh", "memory:/docs"], 1536, True, "1.5K\t/docs\n"),
        (["-hhs", "memory:/docs"], 1536, True, "1.5K\t/docs\n"),
        (["memory:/docs", "-s", "-h"], 1536, True, "1.5K\t/docs\n"),
        (["--", "memory:/docs"], {"/docs/a": 1}, False, "1\t/docs/a\n"),
    ],
)
def test_du_accepts_grouped_repeated_and_interspersed_options(
    arguments: list[str],
    du_result: object,
    total: bool,
    stdout: str,
) -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events, du_result=du_result)

    result = _invoke_du(arguments, sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")
    du_events = [event for event in events if event[0] == "du"]
    assert [(event[2], event[3]) for event in du_events] == [("/docs", total)]


@pytest.mark.parametrize("arguments", [["--help"], ["-s", "--help"]])
def test_du_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = _invoke_du(arguments)

    plain_help = strip_ansi(result.stdout)
    assert result.exit_code == 0
    assert "Usage: du [-sh] [--] name:/path" in plain_help
    assert "Estimate file space usage" in plain_help
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "du: missing mapped filesystem operand\n"),
        (["-"], "du: -: unsupported option\n"),
        (["-x", "memory:/docs"], "du: -x: unsupported option\n"),
        (["-sx", "memory:/docs"], "du: -sx: unsupported option\n"),
        (["--summary", "memory:/docs"], "du: --summary: unsupported option\n"),
        (["--help=value", "memory:/docs"], "du: --help=value: unsupported option\n"),
        (
            ["memory:relative"],
            "du: memory:relative: invalid mapped filesystem operand\n",
        ),
        (["unknown:/docs"], "du: unknown:/docs: unknown filesystem (known: memory)\n"),
        (["memory:/a", "memory:/b"], "du: extra operand\n"),
        (["--", "--help"], "du: --help: invalid mapped filesystem operand\n"),
    ],
)
def test_du_preflight_failures_are_stable_and_source_free(
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = _invoke_du(arguments)

    assert (result.exit_code, result.stdout, result.stderr) == (2, "", diagnostic)


@pytest.mark.parametrize(
    "du_result",
    [
        None,
        3,
        [("/docs/a", 1)],
        {1: 2},
        {"/docs/a": None},
        {"/docs/a": True},
        {"/docs/a": -1},
        {"/docs/bad\nname": 1},
        {"/docs/bad\0name": 1},
        _ExplodingMapping({"/docs/a": 1}),
    ],
)
def test_du_rejects_incompatible_detail_results_atomically(du_result: object) -> None:
    source = _RecordingSource([], du_result=du_result)

    result = _invoke_du(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "du: memory:/docs: incompatible result\n",
    )


@pytest.mark.parametrize("du_result", [None, {}, True, -1, 1.5, "1"])
def test_du_rejects_incompatible_summary_results(du_result: object) -> None:
    source = _RecordingSource([], du_result=du_result)

    result = _invoke_du(["-s", "memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "du: memory:/docs: incompatible result\n",
    )


@pytest.mark.parametrize(
    ("error", "diagnostic"),
    [
        (FileNotFoundError(), "not found"),
        (PermissionError(), "permission denied"),
        (NotADirectoryError(), "not a directory"),
        (NotImplementedError(), "unsupported operation"),
        (
            RuntimeError("bad\\\0\n"),
            r"backend failure (RuntimeError): bad\\\x00\x0a",
        ),
    ],
)
def test_du_reports_backend_failures_and_passes_them_to_cleanup(
    error: Exception,
    diagnostic: str,
) -> None:
    source = _RecordingSource([], du_error=error)

    result = _invoke_du(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"du: memory:/docs: {diagnostic}\n",
    )
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(error)
    assert exception is error
    assert traceback is not None


def test_du_validates_the_complete_mapping_before_output() -> None:
    source = _RecordingSource(
        [],
        du_result={"/docs/good": 1, "/docs/bad": -1},
    )

    result = _invoke_du(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "du: memory:/docs: incompatible result\n",
    )


def test_du_cleans_up_after_an_output_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    output_error = OSError("write failed")
    source = _RecordingSource([], du_result={"/docs/a": 1})
    real_echo = typer.echo

    def fail_stdout(
        message: object = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        if kwargs.get("err") is True:
            real_echo(message, *args, **kwargs)
            return
        raise output_error

    monkeypatch.setattr(typer, "echo", fail_stdout)
    result = _invoke_du(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "du: output: output failure (OSError): write failed\n",
    )
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is OSError
    assert exception is output_error
    assert traceback is not None


def test_du_keeps_broken_pipe_silent_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_pipe = BrokenPipeError()
    source = _RecordingSource([], du_result={"/docs/a": 1})

    def break_stdout(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise broken_pipe

    monkeypatch.setattr(typer, "echo", break_stdout)
    result = _invoke_du(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (1, "", "")
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is BrokenPipeError
    assert exception is broken_pipe
    assert traceback is not None


def test_du_retains_complete_output_when_source_exit_fails() -> None:
    source = _RecordingSource(
        [],
        du_result={"/docs/a": 1},
        exit_error=OSError("cleanup"),
    )

    result = _invoke_du(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "1\t/docs/a\n",
        "du: memory: source exit failure (OSError): cleanup\n",
    )


def test_du_cleans_up_then_propagates_backend_control_flow() -> None:
    control = _DuControl("stop")
    source = _RecordingSource(
        [],
        du_error=control,
        exit_error=OSError("cleanup"),
    )

    with pytest.raises(_DuControl) as caught:
        _invoke_du(["memory:/docs"], sources={"memory": source})

    assert caught.value is control
