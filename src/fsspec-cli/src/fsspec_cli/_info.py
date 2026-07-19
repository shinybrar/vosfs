"""Raw Typer parsing and async execution for ``info``."""

from __future__ import annotations

import pprint
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import typer

from ._command import (
    _binary_stdout,
    _Failure,
    _MappedOperand,
    _parse_mapped_operand,
    _RawCommand,
    _render_failure,
    _render_output_failure,
    _usage_error,
    _write_binary,
)
from ._diagnostics import _render_diagnostic_value
from ._listing import ListingRow, to_listing
from ._sources import _SourceInvocation

if TYPE_CHECKING:
    from collections.abc import Collection

    from fsspec.asyn import AsyncFileSystem
    from typer._click import Context
    from typer._click.formatting import HelpFormatter

    from ._app import AsyncFilesystemSource

_PRETTY_WIDTH = 80


@dataclass(frozen=True, order=True)
class _StablePresentation:
    sort_type: str
    text: str

    def __repr__(self) -> str:
        return self.text


class _InfoCommand(_RawCommand):
    def format_usage(self, ctx: Context, formatter: HelpFormatter) -> None:
        del ctx
        formatter.write_usage("info", "[--] name:/path")


def _preflight(
    command: str,
    raw_arguments: tuple[str, ...],
    known_names: Collection[str],
) -> _MappedOperand:
    operand = None
    options_active = True
    for argument in raw_arguments:
        if options_active and argument == "--":
            options_active = False
            continue
        if options_active and argument.startswith("-"):
            rendered = _render_diagnostic_value(argument)
            _usage_error(command, f"{rendered}: unsupported option")
        if operand is not None:
            _usage_error(command, "extra operand")
        operand = _parse_mapped_operand(command, argument, known_names)
    if operand is None:
        _usage_error(command, "missing mapped filesystem operand")
    return operand


def _canonical_value(value: object, active: set[int]) -> object:
    if not isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return value

    identity = id(value)
    if identity in active:
        message = "recursive metadata containers are not supported"
        raise ValueError(message)
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            canonical: object = _canonical_mapping(
                cast("Mapping[object, object]", value), active
            )
        elif isinstance(value, list):
            canonical = [_canonical_value(item, active) for item in value]
        elif isinstance(value, tuple):
            canonical = tuple(_canonical_value(item, active) for item in value)
        elif isinstance(value, set):
            canonical = _canonical_set(cast("set[object]", value), active, frozen=False)
        else:
            canonical = _canonical_set(value, active, frozen=True)
        return canonical
    finally:
        active.remove(identity)


def _canonical_mapping(
    value: Mapping[object, object],
    active: set[int],
) -> dict[object, object]:
    source_length = len(value)
    entries: list[tuple[object, object]] = []
    spellings: set[str] = set()
    for key in value:
        item = value[key]
        canonical_key = _canonical_value(key, active)
        hash(canonical_key)
        spelling = _pretty(canonical_key)
        if spelling in spellings:
            message = "distinct mapping keys have the same presentation"
            raise ValueError(message)
        spellings.add(spelling)
        entries.append(
            (
                _StablePresentation(
                    f"{type(key).__module__}.{type(key).__qualname__}",
                    spelling,
                ),
                _canonical_value(item, active),
            )
        )

    if len(entries) != source_length:
        message = "mapping iteration does not match its reported length"
        raise ValueError(message)
    canonical = dict(entries)
    if len(canonical) != source_length:
        message = "canonical mapping keys are not distinct"
        raise ValueError(message)
    return canonical


def _canonical_set(
    value: set[object] | frozenset[object],
    active: set[int],
    *,
    frozen: bool,
) -> _StablePresentation:
    members = sorted(_pretty(_canonical_value(item, active)) for item in value)
    if frozen:
        spelling = (
            "frozenset()" if not members else f"frozenset({{{', '.join(members)}}})"
        )
    else:
        spelling = "set()" if not members else f"{{{', '.join(members)}}}"
    return _StablePresentation(
        f"{type(value).__module__}.{type(value).__qualname__}", spelling
    )


def _pretty(value: object) -> str:
    return pprint.pformat(value, width=_PRETTY_WIDTH, sort_dicts=True)


def _render_info(row: ListingRow) -> bytes:
    values = {
        "name": row.name,
        "kind": row.kind,
        "size": row.size,
        "mtime": row.mtime,
        "mode": row.mode,
        "nlink": row.nlink,
        "owner": row.owner,
        "group": row.group,
        "link_target": row.link_target,
        "extra": _canonical_value(row.extra, set()),
    }
    rendered = _pretty(values)
    return f"{rendered}\n".encode()


def _normalize_info(result: object) -> bytes | None:
    if not isinstance(result, Mapping):
        return None
    try:
        if any(type(key) is not str for key in result):
            return None
        row = to_listing(cast("Mapping[str, object]", result))
        return _render_info(row)
    except Exception:  # noqa: BLE001 - malformed mapping or printable value.
        return None


async def _read_info(
    operand: _MappedOperand,
    filesystem: AsyncFileSystem,
) -> bytes | _Failure:
    try:
        result = await filesystem._info(operand.path)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001 - awaited backend boundary.
        return _Failure(operand, backend_error=error)
    payload = _normalize_info(result)
    return _Failure(operand) if payload is None else payload


async def _run_info(
    command: str,
    raw_arguments: tuple[str, ...],
    sources: Mapping[str, AsyncFilesystemSource],
) -> None:
    operand = _preflight(command, raw_arguments, sources)
    invocation = _SourceInvocation(command, sources)
    succeeded = False
    failure: _Failure | None = None
    output_error: Exception | None = None
    try:
        filesystems = await invocation.acquire((operand.name,))
        if filesystems is not None:
            result = await _read_info(operand, filesystems[operand.name])
            if isinstance(result, _Failure):
                failure = result
                _render_failure(command, result)
            else:
                try:
                    stdout = _binary_stdout()
                    _write_binary(stdout, result)
                    stdout.flush()
                except BrokenPipeError as error:
                    output_error = error
                except Exception as error:  # noqa: BLE001 - stdout boundary.
                    output_error = error
                    _render_output_failure(command, error)
            succeeded = failure is None and output_error is None
    finally:
        command_error = failure.backend_error if failure is not None else output_error
        cleanup_failed = await invocation.close_with_command_error(command_error)
    if not succeeded or cleanup_failed:
        raise typer.Exit(1)
