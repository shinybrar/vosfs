"""Typed ``rm`` registration through the mounted application seam."""

from __future__ import annotations

from typing import NoReturn

import pytest
from fsspec_cli import App
from typer.testing import CliRunner

from ._ansi import strip_ansi


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def _invoke(
    arguments: list[str],
    *,
    recursive: bool = False,
):
    return CliRunner().invoke(
        App(
            {"memory": _source_must_not_run},
            capabilities={"recursion": {"remove": recursive}},
        ).typer_app,
        ["rm", *arguments],
        env={"FORCE_COLOR": "1"},
    )


def _typer_error(message: str) -> str:
    return (
        "Usage: root rm [OPTIONS] [name:/path]\n"
        "Try 'root rm --help' for help.\n"
        f"╭─ Error {'─' * 70}╮\n"
        f"│ {message:<76} │\n"
        f"╰{'─' * 78}╯\n"
    )


def test_rm_help_comes_from_capability_selected_typed_callback() -> None:
    disabled = _invoke(["--help"])
    enabled = _invoke(["--help"], recursive=True)
    disabled_help = strip_ansi(disabled.stdout)
    enabled_help = strip_ansi(enabled.stdout)

    assert (disabled.exit_code, disabled.stderr) == (0, "")
    assert (enabled.exit_code, enabled.stderr) == (0, "")
    assert "Usage: root rm [OPTIONS] [name:/path]" in disabled_help
    assert "Remove files; -d removes empty directories." in disabled_help
    assert all(option in disabled_help for option in ("-d", "-f", "-v"))
    assert "-R" not in disabled_help
    assert "-r" not in disabled_help
    assert "Remove files or directories with guarded -R or -r." in enabled_help
    assert all(option in enabled_help for option in ("-R", "-r", "-d", "-f", "-v"))


@pytest.mark.parametrize(
    ("recursive", "arguments", "diagnostic"),
    [
        (
            False,
            ["-R", "not-mapped"],
            "No such option: -R",
        ),
        (
            True,
            ["--recursive", "memory:/docs"],
            "No such option: --recursive",
        ),
    ],
)
def test_typer_rejects_unavailable_rm_options_before_source_acquisition(
    recursive: bool,
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = _invoke(arguments, recursive=recursive)

    assert (result.exit_code, result.stdout_bytes) == (2, b"")
    assert strip_ansi(result.stderr) == _typer_error(diagnostic)
