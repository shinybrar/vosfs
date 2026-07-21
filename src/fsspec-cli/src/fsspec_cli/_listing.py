"""Pure normalization and rendering helpers for heterogeneous fsspec metadata."""

from __future__ import annotations

import math
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from ._path import _lexical_basename

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_INFO_FIELDS = frozenset(
    {
        "name",
        "type",
        "islink",
        "size",
        "mtime",
        "LastModified",
        "last_modified",
        "mode",
        "nlink",
        "uid",
        "gid",
        "destination",
        "target",
    }
)
_MTIME_FIELDS = ("mtime", "LastModified", "last_modified")
_SIZE_UNITS = ("K", "M", "G", "T", "P", "E", "Z", "Y")
_SIZE_BASE = 1024
_SINGLE_DECIMAL_LIMIT = 100

ListingKind = Literal["file", "dir", "link", "other"]
_MODE_KIND_BY_TYPE: dict[int, ListingKind] = {
    stat.S_IFREG: "file",
    stat.S_IFDIR: "dir",
    stat.S_IFLNK: "link",
}


@dataclass(frozen=True)
class ListingRow:
    """Backend-neutral metadata used by listing and information commands."""

    name: str
    kind: ListingKind
    size: int | None
    mtime: float | None
    mode: int | None
    nlink: int | None
    owner: str | int | None
    group: str | int | None
    link_target: str | None
    extra: Mapping[str, object]


def _kind(info: Mapping[str, object]) -> ListingKind:
    if info.get("islink") is True:
        return "link"

    value = info.get("type")
    if value == "file":
        return "file"
    if value in {"dir", "directory"}:
        return "dir"
    if value in {"link", "symlink"}:
        return "link"
    return "other"


def _non_negative_int(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _identity(value: object) -> str | int | None:
    if type(value) is int:
        return value if value >= 0 else None
    return value if type(value) is str else None


def _supported_time(normalized: float) -> float | None:
    if not math.isfinite(normalized):
        return None
    try:
        datetime.fromtimestamp(normalized, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return normalized


def _datetime_time(value: datetime) -> float | None:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        return _supported_time(value.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_time(value: object) -> float | None:
    if type(value) is int or type(value) is float:
        try:
            normalized = float(value)
        except OverflowError:
            return None
        return _supported_time(normalized)
    if isinstance(value, datetime):
        return _datetime_time(value)
    if type(value) is not str:
        return None

    spelling = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(spelling)
    except ValueError:
        return None
    return _datetime_time(parsed)


def _mtime(info: Mapping[str, object]) -> float | None:
    for field in _MTIME_FIELDS:
        if field in info:
            return _normalize_time(info[field])
    return None


def _link_target(info: Mapping[str, object]) -> str | None:
    for field in ("destination", "target"):
        value = info.get(field)
        if type(value) is str:
            return value
    return None


def _extra(info: Mapping[str, object]) -> Mapping[str, object]:
    values = {
        key: value
        for key, value in info.items()
        if type(key) is str and key not in _INFO_FIELDS
    }
    return MappingProxyType(values)


def to_listing(info: Mapping[str, object]) -> ListingRow:
    """Normalize one fsspec ``info`` mapping without filesystem I/O."""
    name = info.get("name")
    if type(name) is not str:
        message = "info name must be a string"
        raise ValueError(message)

    return ListingRow(
        name=_lexical_basename(name),
        kind=_kind(info),
        size=_non_negative_int(info.get("size")),
        mtime=_mtime(info),
        mode=_non_negative_int(info.get("mode")),
        nlink=_non_negative_int(info.get("nlink")),
        owner=_identity(info.get("uid")),
        group=_identity(info.get("gid")),
        link_target=_link_target(info),
        extra=_extra(info),
    )


def format_size(size: int | None, *, human_readable: bool = False) -> str:
    """Render an exact byte count, or a compact 1024-base human size."""
    if size is None:
        return "-"
    if type(size) is not int or size < 0:
        message = "size must be a non-negative integer or None"
        raise ValueError(message)
    if not human_readable:
        return str(size)
    if size < _SIZE_BASE:
        return f"{size}B"

    unit_index = 0
    unit_size = _SIZE_BASE
    while True:
        tenths = (size * 10 + unit_size // 2) // unit_size
        if tenths < _SINGLE_DECIMAL_LIMIT:
            whole, decimal = divmod(tenths, 10)
            rendered = str(whole) if decimal == 0 else f"{whole}.{decimal}"
            return f"{rendered}{_SIZE_UNITS[unit_index]}"

        rounded = (size + unit_size // 2) // unit_size
        if rounded < _SIZE_BASE or unit_index == len(_SIZE_UNITS) - 1:
            return f"{rounded}{_SIZE_UNITS[unit_index]}"
        unit_index += 1
        unit_size *= _SIZE_BASE


def _format_mtime(value: float | None) -> str:
    if value is None:
        return "-"
    return time.strftime("%b %e %H:%M", time.localtime(value))


def _type_indicator(row: ListingRow) -> str:
    if row.mode is None:
        return row.kind
    mode_kind = _MODE_KIND_BY_TYPE.get(stat.S_IFMT(row.mode), "other")
    if mode_kind != row.kind:
        return row.kind
    return stat.filemode(row.mode)


def _display_name(row: ListingRow) -> str:
    if row.kind == "link" and row.link_target is not None:
        return f"{row.name} -> {row.link_target}"
    return row.name


def render_listing(
    rows: Sequence[ListingRow],
    *,
    human_readable: bool = False,
) -> str:
    """Render rows with only supported optional columns and neutral gaps."""
    if not rows:
        return ""

    rendered_columns: list[tuple[bool, list[str]]] = [
        (False, [_type_indicator(row) for row in rows])
    ]
    optional_columns = (
        (False, "nlink", lambda row: str(row.nlink)),
        (False, "owner", lambda row: str(row.owner)),
        (False, "group", lambda row: str(row.group)),
        (
            True,
            "size",
            lambda row: format_size(row.size, human_readable=human_readable),
        ),
        (False, "mtime", lambda row: _format_mtime(row.mtime)),
    )
    for right_aligned, field, render in optional_columns:
        if any(getattr(row, field) is not None for row in rows):
            rendered_columns.append(
                (
                    right_aligned,
                    [
                        render(row) if getattr(row, field) is not None else "-"
                        for row in rows
                    ],
                )
            )
    rendered_columns.append((False, [_display_name(row) for row in rows]))

    widths = [max(len(cell) for cell in column) for _, column in rendered_columns]
    lines = []
    for index in range(len(rows)):
        cells = []
        for column_index, (right_aligned, column) in enumerate(rendered_columns):
            cell = column[index]
            if column_index == len(rendered_columns) - 1:
                cells.append(cell)
            elif right_aligned:
                cells.append(cell.rjust(widths[column_index]))
            else:
                cells.append(cell.ljust(widths[column_index]))
        lines.append("  ".join(cells))
    return "\n".join(lines) + "\n"
