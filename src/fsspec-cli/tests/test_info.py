"""``info`` command tests through the public embedded-command seam."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest
import typer
from fsspec_cli._info import _preflight

from ._support import _invoke_info, _RecordingSource

_INFO = MappingProxyType(
    {
        "name": "/docs/report.txt",
        "type": "file",
        "size": 3,
        "mtime": "2026-07-17T18:00:00Z",
        "mode": 0o100644,
        "nlink": 1,
        "uid": 1000,
        "gid": "science",
        "ETag": b"abc",
        "created": datetime(2026, 7, 16, 18, tzinfo=timezone.utc),
        "properties": MappingProxyType({"z": (2, 1), "a": {"b", "a"}}),
    }
)
_OUTPUT = (
    "{'extra': {'ETag': b'abc',\n"
    "           'created': datetime.datetime(2026, 7, 16, 18, 0, "
    "tzinfo=datetime.timezone.utc),\n"
    "           'properties': {'a': {'a', 'b'}, 'z': (2, 1)}},\n"
    " 'group': 'science',\n"
    " 'kind': 'file',\n"
    " 'link_target': None,\n"
    " 'mode': 33188,\n"
    " 'mtime': 1784311200.0,\n"
    " 'name': 'report.txt',\n"
    " 'nlink': 1,\n"
    " 'owner': 1000,\n"
    " 'size': 3}\n"
)


def test_info_help_matches_locked_usage() -> None:
    result = _invoke_info(["--help"])

    assert result.exit_code == 0
    plain_help = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout)
    assert "Usage: info [--] name:/path" in plain_help
    assert "Display normalized file information" in plain_help
    assert "root info [OPTIONS]" not in plain_help


def test_info_renders_every_normalized_field_and_python_extra_value() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(events, info_result=_INFO)

    result = _invoke_info(["memory:/docs/report.txt"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, _OUTPUT, "")
    assert [(event[0], event[2]) for event in events if event[0] == "info"] == [
        ("info", "/docs/report.txt")
    ]
    assert not any(event[0] == "ls" for event in events)
    assert source.call_count == 1


def test_info_rendering_is_stable_across_python_hash_seeds() -> None:
    child = Path(__file__).with_name("_info_process_child.py")
    outputs = []
    for seed in ("1", "987654"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and script.
            [sys.executable, str(child)],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        assert completed.stderr == ""
        outputs.append(completed.stdout)

    assert (
        outputs
        == [
            "{'extra': {'properties': {'a': {'alpha', 'bravo', 'charlie'}, "
            "'z': (2, 1)}},\n"
            " 'group': None,\n"
            " 'kind': 'file',\n"
            " 'link_target': None,\n"
            " 'mode': None,\n"
            " 'mtime': None,\n"
            " 'name': 'x',\n"
            " 'nlink': None,\n"
            " 'owner': None,\n"
            " 'size': None}\n"
        ]
        * 2
    )


def test_info_accepts_the_option_delimiter() -> None:
    source = _RecordingSource([], info_result=_INFO)

    result = _invoke_info(
        ["--", "memory:/docs/report.txt"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (0, _OUTPUT, "")


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "missing mapped filesystem operand"),
        (["-x", "memory:/x"], "-x: unsupported option"),
        (["--long", "memory:/x"], "--long: unsupported option"),
        (["--help=value"], "--help=value: unsupported option"),
        (["bare"], "bare: invalid mapped filesystem operand"),
        (["memory:relative"], "memory:relative: invalid mapped filesystem operand"),
        (["unknown:/x"], "unknown:/x: unknown filesystem (known: memory)"),
        (["memory:/one", "memory:/two"], "extra operand"),
        (["--", "-x"], "-x: invalid mapped filesystem operand"),
    ],
)
def test_info_rejects_invalid_argv_before_source_entry(
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = _invoke_info(arguments)

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        f"info: {diagnostic}\n",
    )


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        {"type": "file"},
        {"name": 3, "type": "file"},
        {"name": "/x", "type": "file", 1: "not a string key"},
    ],
)
def test_info_rejects_malformed_result(result: object) -> None:
    source = _RecordingSource([], info_result=result)

    invocation = _invoke_info(["memory:/x"], sources={"memory": source})

    assert (invocation.exit_code, invocation.stdout, invocation.stderr) == (
        1,
        "",
        "info: memory:/x: incompatible result\n",
    )


def test_info_rejects_a_recursive_extra_value() -> None:
    info: dict[str, object] = {"name": "/x", "type": "file"}
    info["cycle"] = info
    source = _RecordingSource([], info_result=info)

    result = _invoke_info(["memory:/x"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "info: memory:/x: incompatible result\n",
    )


def test_info_maps_an_ordinary_backend_failure() -> None:
    source = _RecordingSource([], info_error=FileNotFoundError("gone"))

    result = _invoke_info(["memory:/missing"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "info: memory:/missing: not found\n",
    )


def test_info_writes_and_flushes_one_complete_binary_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _RecordingSource([], info_result=_INFO)
    calls: list[tuple[str, bytes | None]] = []

    class _Stdout:
        def write(self, payload: bytes) -> int:
            calls.append(("write", payload))
            return len(payload)

        def flush(self) -> None:
            calls.append(("flush", None))

    monkeypatch.setattr("fsspec_cli._info._binary_stdout", _Stdout)

    result = _invoke_info(["memory:/docs/report.txt"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert calls == [("write", _OUTPUT.encode()), ("flush", None)]


def test_info_reports_a_short_write_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _RecordingSource([], info_result=_INFO)

    class _ShortStdout:
        def write(self, payload: bytes) -> int:
            return len(payload) - 1

        def flush(self) -> None:
            message = "short writes must not flush"
            raise AssertionError(message)

    monkeypatch.setattr("fsspec_cli._info._binary_stdout", _ShortStdout)

    result = _invoke_info(["memory:/docs/report.txt"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "info: output: output failure (OSError): short write\n",
    )
    assert len(source.exit_calls) == 1


def test_info_keeps_broken_pipe_silent_but_reports_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_pipe = BrokenPipeError("closed")
    source = _RecordingSource([], info_result=_INFO, exit_error=OSError("cleanup"))

    class _BrokenStdout:
        def write(self, payload: bytes) -> int:
            del payload
            raise broken_pipe

        def flush(self) -> None:
            message = "broken writes must not flush"
            raise AssertionError(message)

    monkeypatch.setattr("fsspec_cli._info._binary_stdout", _BrokenStdout)

    result = _invoke_info(["memory:/docs/report.txt"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "info: memory: source exit failure (OSError): cleanup\n",
    )
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is BrokenPipeError
    assert exception is broken_pipe
    assert traceback is not None


def test_info_propagates_control_flow_unchanged_after_cleanup() -> None:
    control = asyncio.CancelledError("stop info")
    source = _RecordingSource([], info_error=control)

    with pytest.raises(asyncio.CancelledError) as caught:
        _invoke_info(["memory:/x"], sources={"memory": source})

    assert caught.value is control
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is asyncio.CancelledError
    assert exception is control
    assert traceback is not None


def test_info_preflight_escapes_the_concrete_command_label(capsys) -> None:
    with pytest.raises(typer.Exit) as caught:
        _preflight("future\\command\0\r\n", ("bad",), {"memory"})

    assert caught.value.exit_code == 2
    assert capsys.readouterr().err == (
        "future\\\\command\\x00\\x0d\\x0a: bad: invalid mapped filesystem operand\n"
    )
