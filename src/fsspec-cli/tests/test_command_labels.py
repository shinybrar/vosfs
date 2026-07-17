"""Private orchestration tests for concrete command labels."""

import asyncio
from collections.abc import Callable
from typing import NoReturn

import fsspec_cli._app as app_module
import pytest
import typer
from fsspec_cli._ls import _preflight, _run_ls
from fsspec_cli._sources import _SourceInvocation

from ._support import _RecordingSource

_COMMAND = "future\\command\0\r\n"
_RENDERED_COMMAND = "future\\\\command\\0\\r\\n"


def test_preflight_diagnostic_escapes_concrete_command_label(capsys) -> None:
    with pytest.raises(typer.Exit) as caught:
        _preflight(_COMMAND, ("bad",), {"memory"})

    assert caught.value.exit_code == 2
    assert capsys.readouterr().err == (
        f"{_RENDERED_COMMAND}: bad: invalid mapped filesystem operand\n"
    )


def test_active_loop_refusal_escapes_concrete_command_label(capsys) -> None:
    ensure_no_active_event_loop = getattr(
        app_module,
        "_ensure_no_active_event_loop",
        None,
    )
    assert ensure_no_active_event_loop is not None

    async def invoke() -> None:
        ensure_no_active_event_loop(_COMMAND)

    with pytest.raises(typer.Exit) as caught:
        asyncio.run(invoke())

    assert caught.value.exit_code == 1
    assert capsys.readouterr().err == (
        f"{_RENDERED_COMMAND}: cannot run from an active event loop\n"
    )


class _EntryFailure:
    async def __aenter__(self) -> NoReturn:
        message = "entry"
        raise LookupError(message)

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info


class _IncompatibleYield:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info


def _factory_failure() -> NoReturn:
    message = "factory"
    raise ValueError(message)


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        (
            _factory_failure,
            "source: source factory failure (ValueError): factory",
        ),
        (
            object,
            "source: source factory returned incompatible async context manager",
        ),
        (
            _EntryFailure,
            "source: source entry failure (LookupError): entry",
        ),
        (
            _IncompatibleYield,
            "source: source yielded incompatible async filesystem",
        ),
    ],
)
def test_source_acquisition_diagnostic_uses_concrete_command_label(
    source: Callable[[], object],
    diagnostic: str,
    capsys,
) -> None:
    invocation = _SourceInvocation(_COMMAND, {"source": source})

    async def exercise() -> None:
        assert await invocation.acquire(("source",)) is None
        await invocation.close((None, None, None))

    asyncio.run(exercise())

    assert capsys.readouterr().err == f"{_RENDERED_COMMAND}: {diagnostic}\n"


def test_source_exit_diagnostic_uses_concrete_command_label(capsys) -> None:
    source = _RecordingSource([], exit_error=OSError("exit"))
    invocation = _SourceInvocation(_COMMAND, {"source": source})

    async def exercise() -> None:
        assert await invocation.acquire(("source",)) is not None
        assert await invocation.close((None, None, None)) is True

    asyncio.run(exercise())

    assert capsys.readouterr().err == (
        f"{_RENDERED_COMMAND}: source: source exit failure (OSError): exit\n"
    )


def test_output_failure_diagnostic_uses_concrete_command_label(
    monkeypatch,
    capsys,
) -> None:
    output_error = OSError("write")
    source = _RecordingSource([])
    real_echo = typer.echo

    def fail_stdout(
        message: object = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        if kwargs.get("err") is True:
            real_echo(message, *args, **kwargs)
            return
        raise output_error

    monkeypatch.setattr(typer, "echo", fail_stdout)

    with pytest.raises(typer.Exit) as caught:
        asyncio.run(_run_ls(_COMMAND, ("memory:/file",), {"memory": source}))

    assert caught.value.exit_code == 1
    assert capsys.readouterr().err == (
        f"{_RENDERED_COMMAND}: output: output failure (OSError): write\n"
    )
