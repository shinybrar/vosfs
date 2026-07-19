"""Public behavior for the opt-in ``sign`` extension."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import NoReturn

import pytest
from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App
from fsspec_cli.extensions import sign
from typer.testing import CliRunner


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def test_sign_command_is_absent_without_the_extension() -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}).typer_app,
        ["sign", "memory:/report.csv"],
    )

    assert result.exit_code == 2
    assert "No such command 'sign'" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "sign: missing mapped filesystem operand\n"),
        (
            ["--expiration", "10", "memory:/report.csv"],
            "sign: --expiration: unsupported option\n",
        ),
        (
            ["memory:/one", "memory:/two"],
            "sign: extra operand\n",
        ),
        (["bare"], "sign: bare: invalid mapped filesystem operand\n"),
        (
            ["other:/report.csv"],
            "sign: other:/report.csv: unknown filesystem (known: memory)\n",
        ),
    ],
)
def test_sign_extension_rejects_invalid_argv_before_source_entry(
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = CliRunner().invoke(
        App({"memory": _source_must_not_run}, extensions=[sign]).typer_app,
        ["sign", *arguments],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (2, "", diagnostic)


def test_sign_extension_calls_capability_on_the_invocation_loop() -> None:
    events: list[tuple[object, ...]] = []

    class SigningFileSystem(AsyncFileSystem):
        cachable = False

        def sign(
            self,
            path: str,
            expiration: int = 100,
            **kwargs: object,
        ) -> str:
            assert kwargs == {}
            events.append(("sign", path, expiration, id(asyncio.get_running_loop())))
            return "https://download.example/report.csv?token=abc"

    @asynccontextmanager
    async def source():
        loop_id = id(asyncio.get_running_loop())
        events.append(("enter", loop_id))
        try:
            yield SigningFileSystem(asynchronous=True)
        finally:
            events.append(("exit", id(asyncio.get_running_loop())))

    result = CliRunner().invoke(
        App({"signed": source}, extensions=[sign]).typer_app,
        ["sign", "signed:/report.csv"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "https://download.example/report.csv?token=abc\n",
        "",
    )
    assert [event[:-1] for event in events] == [
        ("enter",),
        ("sign", "/report.csv", 100),
        ("exit",),
    ]
    assert len({event[-1] for event in events}) == 1


def test_sign_extension_reports_missing_capability_without_a_traceback() -> None:
    lifecycle: list[str] = []

    @asynccontextmanager
    async def source():
        lifecycle.append("enter")
        try:
            yield AsyncFileSystem(asynchronous=True)
        finally:
            lifecycle.append("exit")

    result = CliRunner().invoke(
        App({"memory": source}, extensions=[sign]).typer_app,
        ["sign", "memory:/report.csv"],
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "sign: memory:/report.csv: unsupported operation\n",
    )
    assert lifecycle == ["enter", "exit"]
