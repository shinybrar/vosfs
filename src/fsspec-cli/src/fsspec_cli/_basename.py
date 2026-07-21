"""Raw Typer parsing and lexical execution for ``basename``."""

from __future__ import annotations

from dataclasses import dataclass

import typer

from ._command import _usage_error
from ._diagnostics import _render_diagnostic_value
from ._path import _lexical_basename

_MAX_OPERANDS = 2


@dataclass(frozen=True)
class _BasenameRequest:
    operand: str
    suffix: str | None = None


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
) -> _BasenameRequest:
    operands = []
    options_active = True

    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-") and argument != "-":
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")

        if "\0" in argument:
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: invalid operand")

        operands.append(argument)

    if not operands:
        _usage_error(command, "missing operand")
    if len(operands) > _MAX_OPERANDS:
        _usage_error(command, "extra operand")

    suffix = operands[1] if len(operands) == _MAX_OPERANDS else None
    return _BasenameRequest(operand=operands[0], suffix=suffix)


def _apply_optional_suffix(base: str, suffix: str) -> str:
    if not suffix or suffix == base:
        return base
    if base.endswith(suffix):
        return base[: -len(suffix)]
    return base


def _run_basename(command: str, raw_arguments: tuple[str, ...]) -> None:
    request = _preflight(command, raw_arguments)
    result = _lexical_basename(request.operand)
    if request.suffix is not None:
        result = _apply_optional_suffix(result, request.suffix)
    typer.echo(result, nl=True, color=True)
