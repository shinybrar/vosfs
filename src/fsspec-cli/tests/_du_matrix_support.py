"""Shared profile assertions for hermetic ``du`` source matrices."""

from typing import TypeVar

from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App
from typer.testing import CliRunner

from ._matrix_support import _ProbedSource

_FilesystemT = TypeVar("_FilesystemT", bound=AsyncFileSystem)


def _exercise_du_profile(  # noqa: PLR0913 - matrix golden expectations.
    source_name: str,
    source: _ProbedSource[_FilesystemT],
    path: str,
    *,
    exact_output: str,
    human_output: str,
    total: int,
    human_total: str,
) -> None:
    app = App({source_name: source})
    operand = f"{source_name}:{path}"
    runner = CliRunner()

    exact = runner.invoke(app.typer_app, ["du", operand])
    human = runner.invoke(app.typer_app, ["du", "-h", operand])
    summary = runner.invoke(app.typer_app, ["du", "-s", operand])
    human_summary = runner.invoke(app.typer_app, ["du", "-sh", operand])

    assert (exact.exit_code, exact.stdout, exact.stderr) == (0, exact_output, "")
    assert (human.exit_code, human.stdout, human.stderr) == (0, human_output, "")
    assert (summary.exit_code, summary.stdout, summary.stderr) == (
        0,
        f"{total}\t{path}\n",
        "",
    )
    assert (human_summary.exit_code, human_summary.stdout, human_summary.stderr) == (
        0,
        f"{human_total}\t{path}\n",
        "",
    )
    assert [event.stage for event in source.lifecycle] == [
        "factory",
        "enter",
        "exit",
    ] * 4
    du_calls = [call for call in source.calls if call.operation == "du"]
    assert [(call.path, call.total, call.kwargs) for call in du_calls] == [
        (path, False, {}),
        (path, False, {}),
        (path, True, {}),
        (path, True, {}),
    ]
    assert not source.errors
