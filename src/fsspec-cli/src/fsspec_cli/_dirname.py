"""Lexical execution for typed ``dirname``."""

from __future__ import annotations

import typer

from ._command import _usage_error
from ._diagnostics import _render_diagnostic_value
from ._path import _lexical_parent


def _run_dirname(command: str, operand: str) -> None:
    if "\0" in operand:
        rendered = _render_diagnostic_value(operand)
        _usage_error(command, f"{rendered}: invalid operand")
    typer.echo(_lexical_parent(operand), nl=True, color=True)
