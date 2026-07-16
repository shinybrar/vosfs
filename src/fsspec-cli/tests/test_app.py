"""Tests for the public embedded-command application seam."""

import asyncio
from typing import NoReturn

import pytest
import typer
from fsspec_cli import App, AsyncFilesystemSource
from typer.testing import CliRunner


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def test_app_rejects_an_empty_source_mapping() -> None:
    with pytest.raises(ValueError) as error:  # noqa: PT011 - message is not API.
        App({})
    assert type(error.value) is ValueError


@pytest.mark.parametrize(
    ("name", "error_type"),
    [
        (1, TypeError),
        ("", ValueError),
        ("bad:name", ValueError),
        ("bad\0name", ValueError),
        ("bad\nname", ValueError),
    ],
)
def test_app_rejects_invalid_source_names(name, error_type) -> None:
    with pytest.raises(error_type) as error:
        App({name: _source_must_not_run})
    assert type(error.value) is error_type


def test_public_exports_are_app_and_the_source_type() -> None:
    assert AsyncFilesystemSource is not None
    assert __import__("fsspec_cli").__all__ == ["App", "AsyncFilesystemSource"]


def test_ls_rejects_a_missing_mapped_filesystem_operand() -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: missing mapped filesystem operand\n"


def test_ls_refuses_an_active_same_thread_event_loop() -> None:
    async def invoke() -> object:
        return CliRunner().invoke(
            App({"memory": _source_must_not_run}).typer_app,
            ["ls", "memory:/docs"],
        )

    result = asyncio.run(invoke())

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "ls: cannot run from an active event loop\n"


@pytest.mark.parametrize(
    "option",
    ["-l", "-ll", "-Al", "--long", "-a", "--all", "-h", "--help=value"],
)
def test_ls_rejects_the_complete_unsupported_option_token(option: str) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", option, "memory:/docs"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == f"ls: {option}: unsupported option\n"


@pytest.mark.parametrize(
    "supported_options",
    [["-A"], ["-AA"], ["-A", "-AAA"], ["memory:/docs", "-A"]],
)
def test_ls_accepts_repeated_grouped_and_interspersed_uppercase_a(
    supported_options: list[str],
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", *supported_options, "-l"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: -l: unsupported option\n"


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
        (["--", "-l"], "-l"),
        (["--", "-A"], "-A"),
        (["--", "--"], "--"),
    ],
)
def test_ls_rejects_malformed_mapped_filesystem_operands(
    arguments: list[str],
    rendered: str,
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", *arguments],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (f"ls: {rendered}: invalid mapped filesystem operand\n")


def test_ls_reports_unknown_names_with_locale_sorted_known_names() -> None:
    result = CliRunner().invoke(
        App(
            {
                "zeta": _source_must_not_run,
                "alpha": _source_must_not_run,
            }
        ).typer_app,
        ["ls", "other:/docs"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "ls: other:/docs: unknown filesystem (known: alpha, zeta)\n"
    )


def test_app_snapshots_its_source_mapping_once() -> None:
    sources = {"memory": _source_must_not_run}
    typer_app = App(sources).typer_app
    sources.clear()
    sources["later"] = _source_must_not_run

    result = CliRunner().invoke(typer_app, ["ls", "later:/docs"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == ("ls: later:/docs: unknown filesystem (known: memory)\n")


def test_ls_escapes_each_known_name_in_an_unknown_name_diagnostic() -> None:
    result = CliRunner().invoke(
        App({"known\\name\r": _source_must_not_run}).typer_app,
        ["ls", "other:/docs"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "ls: other:/docs: unknown filesystem (known: known\\\\name\\r)\n"
    )


@pytest.mark.parametrize("arguments", [["-A"], ["-AA", "--"]])
def test_ls_reports_a_missing_operand_after_supported_option_syntax(
    arguments: list[str],
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", *arguments],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: missing mapped filesystem operand\n"


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        (["bare", "-l"], "ls: bare: invalid mapped filesystem operand\n"),
        (
            ["unknown:/docs", "-l"],
            "ls: unknown:/docs: unknown filesystem (known: memory)\n",
        ),
        (["-l", "bare"], "ls: -l: unsupported option\n"),
    ],
)
def test_ls_reports_only_the_first_preflight_error_in_argument_order(
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", *arguments],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == diagnostic


@pytest.mark.parametrize("arguments", [["--help"], ["-l", "--help"]])
def test_ls_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", *arguments],
    )

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("operand", ["--help", "--help=value"])
def test_ls_treats_help_tokens_after_the_option_delimiter_as_operands(
    operand: str,
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", "--", operand],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (f"ls: {operand}: invalid mapped filesystem operand\n")


def test_ls_preserves_preflight_when_mounted_below_a_parent_typer_app() -> None:
    parent = typer.Typer(add_completion=False)
    parent.add_typer(
        App({"memory": _source_must_not_run}).typer_app,
        name="data",
    )

    result = CliRunner().invoke(
        parent,
        ["data", "ls", "-l", "memory:/docs"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: -l: unsupported option\n"


def test_active_loop_refusal_precedes_command_preflight() -> None:
    async def invoke() -> object:
        return CliRunner().invoke(
            App({"memory": _source_must_not_run}).typer_app,
            ["ls", "-l"],
        )

    result = asyncio.run(invoke())

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "ls: cannot run from an active event loop\n"


def test_ls_renders_all_diagnostic_control_characters_in_order() -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", "memory:/bad\\\0\r\n"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "ls: memory:/bad\\\\\\0\\r\\n: invalid mapped filesystem operand\n"
    )


@pytest.mark.parametrize(
    "valid_prefix",
    [
        ["memory:/"],
        ["memory:/path:with:colons"],
        ["memory:/one", "memory:/two"],
        ["--", "memory:/docs"],
    ],
)
def test_ls_accepts_locked_operand_grammar_before_a_later_error(
    valid_prefix: list[str],
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["ls", *valid_prefix, "bad"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "ls: bad: invalid mapped filesystem operand\n"
