"""Shared rendering for stable command diagnostics."""


def _render_diagnostic_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _render_diagnostic_prefix(command: str) -> str:
    return f"{_render_diagnostic_value(command)}:"
