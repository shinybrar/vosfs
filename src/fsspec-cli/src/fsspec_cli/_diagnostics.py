"""Shared rendering for stable command diagnostics."""

_FIRST_PRINTABLE = 0x20
_DELETE = 0x7F


def _escape_control(character: str) -> str:
    code = ord(character)
    if code < _FIRST_PRINTABLE or code == _DELETE:
        return f"\\x{code:02x}"
    return character


def _render_diagnostic_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    return "".join(_escape_control(character) for character in escaped)


def _render_diagnostic_prefix(command: str) -> str:
    return f"{_render_diagnostic_value(command)}:"
