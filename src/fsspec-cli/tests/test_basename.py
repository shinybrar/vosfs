"""Construction and lexical behavior for the ``basename`` command."""

from typing import NoReturn

import pytest
from fsspec_cli import App, AsyncFilesystemSource
from typer.testing import CliRunner, Result


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def _invoke_basename(
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(
        App(sources).typer_app,
        ["basename", *arguments],
    )


@pytest.mark.parametrize(
    ("operand", "expected_stdout"),
    [
        ("a", "a\n"),
        ("a/b", "b\n"),
        ("/a/b", "b\n"),
        ("/", "/\n"),
        ("///", "/\n"),
        ("//", "/\n"),
        (".", ".\n"),
        ("..", "..\n"),
        ("a/b/", "b\n"),
        ("memory:/docs/a.txt", "a.txt\n"),
        ("café/naïve.txt", "naïve.txt\n"),
        ("no/slash", "slash\n"),
    ],
)
def test_basename_applies_the_locked_posix_golden_vectors(
    operand: str,
    expected_stdout: str,
) -> None:
    result = _invoke_basename([operand])

    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""


def test_basename_preserves_embedded_newline_in_the_operand() -> None:
    operand = "dir\nname"
    result = _invoke_basename([operand])

    assert result.exit_code == 0
    assert result.stdout == f"{operand}\n"
    assert result.stderr == ""


def test_basename_treats_source_looking_text_as_lexical_data() -> None:
    operand = "memory:/docs/a.txt"
    result = _invoke_basename([operand])

    assert result.exit_code == 0
    assert result.stdout == "a.txt\n"
    assert result.stderr == ""


def test_basename_rejects_nul_in_the_operand() -> None:
    result = _invoke_basename(["bad\0name"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "basename: bad\\0name: invalid operand\n"


def test_basename_rejects_a_missing_operand() -> None:
    result = _invoke_basename([])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "basename: missing operand\n"


def test_basename_rejects_an_extra_operand() -> None:
    result = _invoke_basename(["a", "suffix"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "basename: extra operand\n"


@pytest.mark.parametrize(
    "option",
    ["-a", "-z", "-ab", "--all", "--suffix", "-h", "--help=value"],
)
def test_basename_rejects_every_unsupported_option(option: str) -> None:
    result = _invoke_basename([option, "a"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == f"basename: {option}: unsupported option\n"


def test_basename_honors_the_option_delimiter_for_option_looking_operands() -> None:
    result = _invoke_basename(["--", "-l"])

    assert result.exit_code == 0
    assert result.stdout == "-l\n"
    assert result.stderr == ""


@pytest.mark.parametrize("arguments", [["--help"], ["-a", "--help"]])
def test_basename_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = _invoke_basename(arguments)

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert result.stderr == ""


def test_basename_treats_help_tokens_after_the_option_delimiter_as_operands() -> None:
    result = _invoke_basename(["--", "--help"])

    assert result.exit_code == 0
    assert result.stdout == "--help\n"
    assert result.stderr == ""


def test_basename_reports_only_the_first_preflight_error_in_argument_order() -> None:
    result = _invoke_basename(["-l", "a", "b"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "basename: -l: unsupported option\n"


def test_basename_renders_all_diagnostic_control_characters_in_order() -> None:
    result = _invoke_basename(["bad\\\0\r\n"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "basename: bad\\\\\\0\\r\\n: invalid operand\n"


@pytest.mark.parametrize(
    ("arguments", "expected_stdout"),
    [
        (["a"], "a\n"),
        (["-l"], "basename: -l: unsupported option\n"),
        ([], "basename: missing operand\n"),
    ],
)
def test_basename_never_calls_a_configured_source(
    arguments: list[str],
    expected_stdout: str,
) -> None:
    source_calls = 0

    def source_must_not_run() -> NoReturn:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_basename(arguments, sources={"memory": source_must_not_run})

    assert source_calls == 0
    if result.exit_code == 0:
        assert result.stdout == expected_stdout
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr == expected_stdout
