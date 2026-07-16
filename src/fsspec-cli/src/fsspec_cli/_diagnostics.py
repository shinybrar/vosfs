"""Shared rendering for stable ``ls`` diagnostics."""


def _render_diagnostic_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
