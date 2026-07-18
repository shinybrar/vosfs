"""``find`` command tests through the public embedded-command seam."""

from collections.abc import Iterator
from typing import NoReturn

import pytest
import typer

from ._support import _invoke_find, _RecordingSource

_EXACT_FIND_HELP = (
    "                                                                                \n"
    " Usage: find [--maxdepth N] [--type f|d] [--] name:/path                        \n"
    "                                                                                \n"
    " Find files recursively                                                         \n"
    "                                                                                \n"
    "╭─ Options ────────────────────────────────────────────────────────────────────╮\n"
    "│ --help          Show this message and exit.                                  │\n"
    "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    "\n"
)


class _ExplodingList(list[str]):
    def __iter__(self) -> Iterator[str]:
        raise RuntimeError


class _ExplodingMapping(dict[str, object]):
    def items(self) -> NoReturn:
        raise RuntimeError


class _FindControl(BaseException):
    pass


def test_find_renders_recursive_backend_file_paths_after_one_call() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        find_result=["/docs/sub/b.txt", "/docs/a.txt"],
    )

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "/docs/a.txt\n/docs/sub/b.txt\n",
        "",
    )
    assert [(event[0], *event[2:-1]) for event in events] == [
        ("factory",),
        ("enter",),
        ("find", "/docs", None, False, False),
        ("exit",),
    ]


@pytest.mark.parametrize(
    ("arguments", "find_result", "call", "stdout"),
    [
        (
            ["--maxdepth", "2", "memory:/docs"],
            ["/docs/sub/b.txt", "/docs/a.txt"],
            ("/docs", 2, False, False),
            "/docs/a.txt\n/docs/sub/b.txt\n",
        ),
        (
            ["memory:/docs", "--type", "f"],
            ["/docs/a.txt"],
            ("/docs", None, False, False),
            "/docs/a.txt\n",
        ),
        (
            ["--type", "d", "memory:/docs"],
            {
                "/docs/sub": {"type": "directory"},
                "/docs/a.txt": {"type": "file"},
                "/docs": {"type": "directory"},
                "/docs/link": {"type": "other"},
            },
            ("/docs", None, True, True),
            "/docs\n/docs/sub\n",
        ),
        (
            ["--maxdepth", "3", "memory:/docs", "--maxdepth", "1"],
            ["/docs/a.txt"],
            ("/docs", 1, False, False),
            "/docs/a.txt\n",
        ),
        (
            ["--", "memory:/docs"],
            ["/docs/a.txt"],
            ("/docs", None, False, False),
            "/docs/a.txt\n",
        ),
    ],
)
def test_find_accepts_locked_interspersed_options_and_call_shapes(
    arguments: list[str],
    find_result: object,
    call: tuple[str, int | None, bool, bool],
    stdout: str,
) -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events, find_result=find_result)

    result = _invoke_find(arguments, sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")
    find_events = [event for event in events if event[0] == "find"]
    assert [(event[2], event[3], event[4], event[5]) for event in find_events] == [call]


@pytest.mark.parametrize(
    ("arguments", "find_result", "call", "stdout"),
    [
        (
            ["--maxdepth", "0", "memory:/docs"],
            ["/docs/child.txt"],
            ("/docs", 1, False, False),
            "",
        ),
        (
            ["--maxdepth", "0", "--type", "f", "memory:/file.txt"],
            ["/file.txt"],
            ("/file.txt", 1, False, False),
            "/file.txt\n",
        ),
        (
            ["--type", "d", "--maxdepth", "0", "memory:/docs/"],
            {
                "/docs/child": {"type": "directory"},
                "/docs": {"type": "directory"},
            },
            ("/docs/", 1, True, True),
            "/docs\n",
        ),
    ],
)
def test_find_maxdepth_zero_filters_the_single_backend_call_to_the_root(
    arguments: list[str],
    find_result: object,
    call: tuple[str, int, bool, bool],
    stdout: str,
) -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events, find_result=find_result)

    result = _invoke_find(arguments, sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")
    find_event = next(event for event in events if event[0] == "find")
    assert (find_event[2], find_event[3], find_event[4], find_event[5]) == call


@pytest.mark.parametrize("arguments", [["--help"], ["--type", "d", "--help"]])
def test_find_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = _invoke_find(arguments)

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        _EXACT_FIND_HELP,
        "",
    )


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "find: missing mapped filesystem operand\n"),
        (["-x", "memory:/docs"], "find: -x: unsupported option\n"),
        (
            ["--maxdepth=1", "memory:/docs"],
            "find: --maxdepth=1: unsupported option\n",
        ),
        (["--type=f", "memory:/docs"], "find: --type=f: unsupported option\n"),
        (["--maxdepth"], "find: --maxdepth: option requires an argument\n"),
        (["--type"], "find: --type: option requires an argument\n"),
        (
            ["--maxdepth", "-1", "memory:/docs"],
            "find: -1: invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", "+1", "memory:/docs"],
            "find: +1: invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", "1.0", "memory:/docs"],
            "find: 1.0: invalid --maxdepth value\n",
        ),
        (
            ["--type", "x", "memory:/docs"],
            "find: x: invalid --type value\n",
        ),
        (
            ["memory:relative"],
            "find: memory:relative: invalid mapped filesystem operand\n",
        ),
        (
            ["unknown:/docs"],
            "find: unknown:/docs: unknown filesystem (known: memory)\n",
        ),
        (["memory:/a", "memory:/b"], "find: extra operand\n"),
        (
            ["--", "--help"],
            "find: --help: invalid mapped filesystem operand\n",
        ),
    ],
)
def test_find_preflight_failures_are_stable_and_source_free(
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = _invoke_find(arguments)

    assert (result.exit_code, result.stdout, result.stderr) == (2, "", diagnostic)


def test_find_rejects_a_depth_too_large_for_the_runtime_deterministically() -> None:
    value = "9" * 5000

    result = _invoke_find(["--maxdepth", value, "memory:/docs"])

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        f"find: {value}: invalid --maxdepth value\n",
    )


@pytest.mark.parametrize(
    "find_result",
    [
        None,
        (),
        "docs/a.txt",
        {"/docs/a.txt": {}},
        [1],
        ["/docs/bad\nname"],
        ["/docs/bad\0name"],
        _ExplodingList(["/docs/a.txt"]),
    ],
)
def test_find_rejects_incompatible_file_results_atomically(
    find_result: object,
) -> None:
    source = _RecordingSource([], find_result=find_result)

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: memory:/docs: incompatible result\n",
    )


@pytest.mark.parametrize(
    "find_result",
    [
        None,
        [],
        {1: {"type": "directory"}},
        {"/docs": None},
        {"/docs": {}},
        {"/docs": {"type": True}},
        {"/docs/bad\nname": {"type": "directory"}},
        {"/docs/bad\0name": {"type": "directory"}},
        _ExplodingMapping({"/docs": {"type": "directory"}}),
    ],
)
def test_find_rejects_incompatible_directory_results_atomically(
    find_result: object,
) -> None:
    source = _RecordingSource([], find_result=find_result)

    result = _invoke_find(
        ["--type", "d", "memory:/docs"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: memory:/docs: incompatible result\n",
    )


def test_find_validates_the_complete_result_before_output() -> None:
    source = _RecordingSource(
        [],
        find_result=["/docs/good", "/docs/bad\nname"],
    )

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: memory:/docs: incompatible result\n",
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
def test_find_reports_backend_failures_and_passes_them_to_cleanup(
    error: Exception,
    diagnostic: str,
) -> None:
    source = _RecordingSource([], find_error=error)

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"find: memory:/docs: {diagnostic}\n",
    )
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(error)
    assert exception is error
    assert traceback is not None


def test_find_cleans_up_after_an_output_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_error = OSError("write failed")
    source = _RecordingSource([], find_result=["/docs/a"])
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
    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: output: output failure (OSError): write failed\n",
    )
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is OSError
    assert exception is output_error
    assert traceback is not None


def test_find_keeps_broken_pipe_silent_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_pipe = BrokenPipeError()
    source = _RecordingSource([], find_result=["/docs/a"])

    def break_stdout(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise broken_pipe

    monkeypatch.setattr(typer, "echo", break_stdout)
    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (1, "", "")
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is BrokenPipeError
    assert exception is broken_pipe
    assert traceback is not None


def test_find_retains_complete_output_when_source_exit_fails() -> None:
    source = _RecordingSource(
        [],
        find_result=["/docs/a"],
        exit_error=OSError("cleanup"),
    )

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "/docs/a\n",
        "find: memory: source exit failure (OSError): cleanup\n",
    )


def test_find_cleans_up_then_propagates_backend_control_flow() -> None:
    control = _FindControl("stop")
    source = _RecordingSource(
        [],
        find_error=control,
        exit_error=OSError("cleanup"),
    )

    with pytest.raises(_FindControl) as caught:
        _invoke_find(["memory:/docs"], sources={"memory": source})

    assert caught.value is control
