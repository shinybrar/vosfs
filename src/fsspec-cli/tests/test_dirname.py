"""Construction and lexical behavior for the ``dirname`` command."""

from typing import NoReturn

import pytest
from fsspec_cli import App, AsyncFilesystemSource
from fsspec_cli._path import _lexical_parent
from typer.testing import CliRunner, Result

from ._matrix_support import _block_network

_CLI_RUNNER_ENV = {
    "NO_COLOR": "1",
    "TERM": "dumb",
}

_EXACT_DIRNAME_HELP = (
    "                                                                                \n"
    " Usage: root dirname [OPTIONS]                                                  \n"
    "                                                                                \n"
    " Strip the last component from a path                                           \n"
    "                                                                                \n"
    "╭─ Options ────────────────────────────────────────────────────────────────────╮\n"
    "│ --help          Show this message and exit.                                  │\n"
    "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    "\n"
)


@pytest.fixture(autouse=True)
def _prohibit_unplanned_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def _invoke_dirname(
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(
        App(sources).typer_app,
        ["dirname", *arguments],
        env=_CLI_RUNNER_ENV,
    )


@pytest.mark.parametrize(
    ("operand", "expected_stdout"),
    [
        ("a", ".\n"),
        ("a/b", "a\n"),
        ("/a/b", "/a\n"),
        ("/", "/\n"),
        ("///", "/\n"),
        ("//", "/\n"),
        (".", ".\n"),
        ("..", ".\n"),
        ("a/b/", "a\n"),
        ("memory:/docs/a.txt", "memory:/docs\n"),
        ("café/naïve.txt", "café\n"),
        ("no/slash", "no\n"),
        ("/a", "/\n"),
        ("a/", ".\n"),
        ("a//b", "a/\n"),
        ("/a//b", "/a/\n"),
        ("a///b/", "a//\n"),
    ],
)
def test_dirname_applies_the_locked_posix_golden_vectors(
    operand: str,
    expected_stdout: str,
) -> None:
    result = _invoke_dirname([operand])

    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""


def test_dirname_preserves_embedded_newline_in_the_operand() -> None:
    operand = "dir\nname"
    result = _invoke_dirname([operand])

    assert result.exit_code == 0
    assert result.stdout == ".\n"
    assert result.stderr == ""


def test_dirname_treats_source_looking_text_as_lexical_data() -> None:
    operand = "memory:/docs/a.txt"
    result = _invoke_dirname([operand])

    assert result.exit_code == 0
    assert result.stdout == "memory:/docs\n"
    assert result.stderr == ""


def test_dirname_rejects_nul_in_the_operand() -> None:
    result = _invoke_dirname(["bad\0name"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: bad\\x00name: invalid operand\n"


def test_dirname_rejects_a_missing_operand() -> None:
    result = _invoke_dirname([])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: missing operand\n"


def test_dirname_rejects_an_extra_operand() -> None:
    result = _invoke_dirname(["a", "suffix"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: extra operand\n"


@pytest.mark.parametrize(
    "option",
    ["-a", "-z", "-ab", "--all", "--suffix", "-h", "--help=value"],
)
def test_dirname_rejects_every_unsupported_option(option: str) -> None:
    result = _invoke_dirname([option, "a"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == f"dirname: {option}: unsupported option\n"


def test_dirname_honors_the_option_delimiter_for_option_looking_operands() -> None:
    result = _invoke_dirname(["--", "-l"])

    assert result.exit_code == 0
    assert result.stdout == ".\n"
    assert result.stderr == ""


@pytest.mark.parametrize("arguments", [["--help"], ["-a", "--help"]])
def test_dirname_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    source_calls = 0

    def source_must_not_run() -> NoReturn:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_dirname(arguments, sources={"memory": source_must_not_run})

    assert result.exit_code == 0
    assert result.stdout == _EXACT_DIRNAME_HELP
    assert result.stderr == ""
    assert source_calls == 0


def test_dirname_treats_help_tokens_after_the_option_delimiter_as_operands() -> None:
    result = _invoke_dirname(["--", "--help"])

    assert result.exit_code == 0
    assert result.stdout == ".\n"
    assert result.stderr == ""


def test_dirname_reports_only_the_first_preflight_error_in_argument_order() -> None:
    result = _invoke_dirname(["-l", "a", "b"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: -l: unsupported option\n"


def test_dirname_reports_extra_operand_before_a_later_unsupported_option() -> None:
    result = _invoke_dirname(["a", "b", "-z"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: extra operand\n"


def test_dirname_reports_extra_operand_before_nul_in_a_later_operand() -> None:
    result = _invoke_dirname(["a", "bad\0name"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: extra operand\n"


@pytest.mark.parametrize(
    ("operand", "expected"),
    [
        ("a", "."),
        ("a/b", "a"),
        ("/a/b", "/a"),
        ("/", "/"),
        ("///", "/"),
        ("//", "/"),
        (".", "."),
        ("..", "."),
        ("a/b/", "a"),
        ("a//b", "a/"),
        ("/a//b", "/a/"),
        ("a///b/", "a//"),
        ("memory:/docs/a.txt", "memory:/docs"),
        ("dir\nname", "."),
    ],
)
def test_lexical_parent_applies_the_locked_dirname_algorithm(
    operand: str,
    expected: str,
) -> None:
    assert _lexical_parent(operand) == expected


def test_dirname_renders_all_diagnostic_control_characters_in_order() -> None:
    result = _invoke_dirname(["bad\\\0\r\n"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: bad\\\\\\x00\\x0d\\x0a: invalid operand\n"


@pytest.mark.parametrize(
    ("arguments", "expected_stdout"),
    [
        (["a/b"], "a\n"),
        (["-l"], "dirname: -l: unsupported option\n"),
        ([], "dirname: missing operand\n"),
    ],
)
def test_dirname_never_calls_a_configured_source(
    arguments: list[str],
    expected_stdout: str,
) -> None:
    source_calls = 0

    def source_must_not_run() -> NoReturn:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_dirname(arguments, sources={"memory": source_must_not_run})

    assert source_calls == 0
    if result.exit_code == 0:
        assert result.stdout == expected_stdout
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr == expected_stdout
