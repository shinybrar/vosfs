"""Construction and lexical behavior for the ``dirname`` command."""

from typing import NoReturn

import pytest
from click.utils import strip_ansi
from fsspec_cli import App, AsyncFilesystemSource
from fsspec_cli._dirname import _posix_dirname_string
from typer.testing import CliRunner, Result

from ._matrix_support import _block_network

_CLI_RUNNER_ENV = {
    "NO_COLOR": "1",
    "TERM": "dumb",
}


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
    assert "Missing argument 'OPERAND'" in strip_ansi(result.stderr)


def test_dirname_rejects_an_extra_operand() -> None:
    result = _invoke_dirname(["a", "suffix"])

    assert result.exit_code == 2
    assert result.stdout == ""
    diagnostic = strip_ansi(result.stderr)
    assert "unexpected extra argument" in diagnostic
    assert "suffix" in diagnostic


@pytest.mark.parametrize(
    "option",
    ["-a", "-z", "-ab", "--all", "--suffix", "-h", "--help=value"],
)
def test_dirname_rejects_every_unsupported_option(option: str) -> None:
    result = _invoke_dirname([option, "a"])

    assert result.exit_code == 2
    assert result.stdout == ""
    diagnostic = strip_ansi(result.stderr)
    assert "Option '--help' does not take a value" in diagnostic or (
        "No such option" in diagnostic and option.split("=", 1)[0][:2] in diagnostic
    )


def test_dirname_honors_the_option_delimiter_for_option_looking_operands() -> None:
    result = _invoke_dirname(["--", "-l"])

    assert result.exit_code == 0
    assert result.stdout == ".\n"
    assert result.stderr == ""


def test_dirname_help_comes_from_typed_callback() -> None:
    source_calls = 0

    def source_must_not_run() -> NoReturn:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_dirname(["--help"], sources={"memory": source_must_not_run})

    assert result.exit_code == 0
    assert result.stderr == ""
    help_text = strip_ansi(result.stdout)
    assert "Usage: root dirname [OPTIONS] {OPERAND}" in help_text
    assert "Strip the last component from a path" in help_text
    assert source_calls == 0


def test_dirname_treats_help_tokens_after_the_option_delimiter_as_operands() -> None:
    result = _invoke_dirname(["--", "--help"])

    assert result.exit_code == 0
    assert result.stdout == ".\n"
    assert result.stderr == ""


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
def test_posix_dirname_string_applies_the_locked_algorithm(
    operand: str,
    expected: str,
) -> None:
    assert _posix_dirname_string(operand) == expected


def test_dirname_renders_all_diagnostic_control_characters_in_order() -> None:
    result = _invoke_dirname(["bad\\\0\r\n"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "dirname: bad\\\\\\x00\\x0d\\x0a: invalid operand\n"


@pytest.mark.parametrize("arguments", [["a/b"], ["-l"], []])
def test_dirname_never_calls_a_configured_source(
    arguments: list[str],
) -> None:
    source_calls = 0

    def source_must_not_run() -> NoReturn:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_dirname(arguments, sources={"memory": source_must_not_run})

    assert source_calls == 0
    assert result.exit_code in {0, 2}
