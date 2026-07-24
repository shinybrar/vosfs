"""Construction and lexical behavior for the ``basename`` command."""

from typing import NoReturn

import pytest
from click.utils import strip_ansi
from fsspec_cli import App, AsyncFilesystemSource
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
        env=_CLI_RUNNER_ENV,
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
    assert result.stderr == "basename: bad\\x00name: invalid operand\n"


def test_basename_rejects_a_missing_operand() -> None:
    result = _invoke_basename([])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Missing argument 'OPERAND'" in strip_ansi(result.stderr)


def test_basename_rejects_a_third_operand() -> None:
    result = _invoke_basename(["a", "suffix", "extra"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "unexpected extra argument" in strip_ansi(result.stderr)
    assert "extra" in strip_ansi(result.stderr)


@pytest.mark.parametrize(
    ("operand", "suffix", "expected_stdout"),
    [
        ("foo.bar", ".bar", "foo\n"),
        ("foo.bar", "bar", "foo.\n"),
        ("report.txt", ".txt", "report\n"),
        ("a/b/file.txt", ".txt", "file\n"),
        ("/a/b/c.txt", ".txt", "c\n"),
        ("memory:/path/y.z", ".z", "y\n"),
        ("café.txt", ".txt", "café\n"),
        ("foo.txt.txt", ".txt", "foo.txt\n"),
        ("naïve", "ïve", "na\n"),
        ("repeat", "eat", "rep\n"),
        ("a/b/", "b", "b\n"),
        ("///", "/", "/\n"),
    ],
)
def test_basename_removes_a_matching_suffix_after_base_extraction(
    operand: str,
    suffix: str,
    expected_stdout: str,
) -> None:
    result = _invoke_basename([operand, suffix])

    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("operand", "suffix", "expected_stdout"),
    [
        ("foo.bar", "foo.bar", "foo.bar\n"),
        ("c", "c", "c\n"),
        ("report.txt", ".pdf", "report.txt\n"),
        ("a", "aa", "a\n"),
        ("short", "longer", "short\n"),
        ("foo.bar", "", "foo.bar\n"),
        ("foo.bar", ".baz", "foo.bar\n"),
        ("repeat", "pea", "repeat\n"),
    ],
)
def test_basename_leaves_the_extracted_basename_unchanged_for_nonmatching_suffixes(
    operand: str,
    suffix: str,
    expected_stdout: str,
) -> None:
    result = _invoke_basename([operand, suffix])

    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""


def test_basename_applies_suffix_after_base_extraction_for_embedded_newlines() -> None:
    operand = "dir\nname.txt"
    result = _invoke_basename([operand, ".txt"])

    assert result.exit_code == 0
    assert result.stdout == "dir\nname\n"
    assert result.stderr == ""


def test_basename_removes_a_matching_suffix_with_embedded_newline() -> None:
    operand = "prefix\ntail"
    suffix = "\ntail"
    result = _invoke_basename([operand, suffix])

    assert result.exit_code == 0
    assert result.stdout == "prefix\n"
    assert result.stderr == ""


def test_basename_honors_the_option_delimiter_for_an_option_looking_suffix() -> None:
    result = _invoke_basename(["foo-l", "--", "-l"])

    assert result.exit_code == 0
    assert result.stdout == "foo\n"
    assert result.stderr == ""


def test_basename_treats_a_slash_containing_suffix_as_lexical_data() -> None:
    result = _invoke_basename(["report.txt", "foo/bar"])

    assert result.exit_code == 0
    assert result.stdout == "report.txt\n"
    assert result.stderr == ""


def test_basename_honors_the_option_delimiter_for_suffix_operands() -> None:
    result = _invoke_basename(["--", "-l", "suffix"])

    assert result.exit_code == 0
    assert result.stdout == "-l\n"
    assert result.stderr == ""


def test_basename_rejects_nul_in_the_suffix_operand() -> None:
    result = _invoke_basename(["a", "bad\0suffix"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "basename: bad\\x00suffix: invalid operand\n"


@pytest.mark.parametrize(
    "option",
    ["-a", "-z", "-ab", "--all", "--suffix", "-h", "--help=value"],
)
def test_basename_rejects_every_unsupported_option(option: str) -> None:
    result = _invoke_basename([option, "a"])

    assert result.exit_code == 2
    assert result.stdout == ""
    diagnostic = strip_ansi(result.stderr)
    assert "Option '--help' does not take a value" in diagnostic or (
        "No such option" in diagnostic and option.split("=", 1)[0][:2] in diagnostic
    )


def test_basename_honors_the_option_delimiter_for_option_looking_operands() -> None:
    result = _invoke_basename(["--", "-l"])

    assert result.exit_code == 0
    assert result.stdout == "-l\n"
    assert result.stderr == ""


def test_basename_help_comes_from_typed_callback() -> None:
    source_calls = 0

    def source_must_not_run() -> NoReturn:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_basename(["--help"], sources={"memory": source_must_not_run})

    assert result.exit_code == 0
    assert result.stderr == ""
    help_text = strip_ansi(result.stdout)
    assert "Usage: root basename [OPTIONS] {OPERAND} [SUFFIX]" in help_text
    assert "Strip directory and suffix from a path" in help_text
    assert source_calls == 0


def test_basename_treats_help_tokens_after_the_option_delimiter_as_operands() -> None:
    result = _invoke_basename(["--", "--help"])

    assert result.exit_code == 0
    assert result.stdout == "--help\n"
    assert result.stderr == ""


def test_basename_renders_all_diagnostic_control_characters_in_order() -> None:
    result = _invoke_basename(["bad\\\0\r\n"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "basename: bad\\\\\\x00\\x0d\\x0a: invalid operand\n"


@pytest.mark.parametrize(
    "arguments",
    [["a"], ["memory:/docs/a.txt", ".txt"], ["-l"], []],
)
def test_basename_never_calls_a_configured_source(
    arguments: list[str],
) -> None:
    source_calls = 0

    def source_must_not_run() -> NoReturn:
        nonlocal source_calls
        source_calls += 1
        raise AssertionError

    result = _invoke_basename(arguments, sources={"memory": source_must_not_run})

    assert source_calls == 0
    assert result.exit_code in {0, 2}
