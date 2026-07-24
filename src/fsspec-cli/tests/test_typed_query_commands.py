"""Typed query-command registration through the mounted application seam."""

from __future__ import annotations

from typing import NoReturn

import pytest
from fsspec_cli import App
from typer.testing import CliRunner


def _source_must_not_run() -> NoReturn:
    raise AssertionError


@pytest.mark.parametrize(
    ("command", "summary", "parameters"),
    [
        ("basename", "Strip directory and suffix from a path", ("OPERAND", "SUFFIX")),
        ("dirname", "Strip the last component from a path", ("OPERAND",)),
        ("info", "Display normalized file information", ("name:/path",)),
        ("size", "Display exact file sizes", ("name:/path",)),
        ("test", "Evaluate a file predicate", ("name:/path", "-e", "-d", "-f")),
        ("stat", "Display file status", ("name:/path",)),
    ],
)
def test_query_help_comes_from_typed_callback_metadata(
    command: str,
    summary: str,
    parameters: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        [command, "--help"],
    )
    help_text = result.stdout

    assert (result.exit_code, result.stderr) == (0, "")
    assert f"Usage: root {command} [OPTIONS]" in help_text
    assert summary in help_text
    for parameter in parameters:
        assert parameter in help_text


@pytest.mark.parametrize(
    ("command", "arguments", "contexts"),
    [
        ("basename", [], ("Missing argument", "OPERAND")),
        ("dirname", ["a", "b"], ("unexpected extra argument", "b")),
        ("info", ["--unknown", "memory:/a"], ("No such option", "--unknown")),
        ("size", [], ("Missing argument", "name:/path")),
        ("test", ["-e"], ("Missing argument", "name:/path")),
        ("stat", [], ("Missing argument", "name:/path")),
    ],
)
def test_typer_rejects_query_syntax_before_source_acquisition(
    command: str,
    arguments: list[str],
    contexts: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        [command, *arguments],
    )

    assert (result.exit_code, result.stdout_bytes) == (2, b"")
    diagnostic = result.stderr
    for context in contexts:
        assert context in diagnostic
