"""Lexical execution for typed ``basename``."""

from __future__ import annotations

import typer

from ._command import _usage_error
from ._diagnostics import _render_diagnostic_value
from ._path import _lexical_basename


def _validate_operand(command: str, operand: str) -> None:
    if "\0" in operand:
        rendered = _render_diagnostic_value(operand)
        _usage_error(command, f"{rendered}: invalid operand")


def _apply_optional_suffix(base: str, suffix: str) -> str:
    if not suffix or suffix == base:
        return base
    if base.endswith(suffix):
        return base[: -len(suffix)]
    return base


def _run_basename(command: str, operand: str, suffix: str | None) -> None:
    _validate_operand(command, operand)
    if suffix is not None:
        _validate_operand(command, suffix)
    result = _lexical_basename(operand)
    if suffix is not None:
        result = _apply_optional_suffix(result, suffix)
    typer.echo(result, nl=True, color=True)
