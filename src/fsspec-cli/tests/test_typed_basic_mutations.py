"""Typed basic-mutation registration through the mounted application seam."""

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
        ("mkdir", "Create directories", ("name:/path", "-p")),
        ("rmdir", "Remove empty directories", ("name:/path",)),
        ("unlink", "Remove a single file", ("name:/path",)),
    ],
)
def test_basic_mutation_help_comes_from_typed_callback_metadata(
    command: str,
    summary: str,
    parameters: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        [command, "--help"],
    )

    assert (result.exit_code, result.stderr) == (0, "")
    assert "Usage:" in result.stdout
    assert command in result.stdout
    assert "OPTIONS" in result.stdout
    assert summary in result.stdout
    for parameter in parameters:
        assert parameter in result.stdout


@pytest.mark.parametrize(
    ("command", "arguments", "contexts"),
    [
        ("mkdir", [], ("Missing argument", "name:/path")),
        (
            "rmdir",
            ["--parents", "memory:/docs"],
            ("No such option", "parents"),
        ),
        (
            "unlink",
            ["memory:/one", "memory:/two"],
            ("unexpected extra argument", "memory:/two"),
        ),
    ],
)
def test_typer_rejects_basic_mutation_syntax_before_source_acquisition(
    command: str,
    arguments: list[str],
    contexts: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        [command, *arguments],
    )

    assert (result.exit_code, result.stdout_bytes) == (2, b"")
    for context in contexts:
        assert context in result.stderr
