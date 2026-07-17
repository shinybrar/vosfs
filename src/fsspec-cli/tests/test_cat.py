"""Mapped-file ``cat`` tests through the public embedded-command seam."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import NoReturn

import pytest
from fsspec_cli import App
from typer.testing import CliRunner, Result

from ._support import _RecordingSource, _source_must_not_run


def _invoke_cat(
    arguments: list[str],
    *,
    sources: dict[str, object] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(App(sources).typer_app, ["cat", *arguments])


def test_cat_rejects_missing_operand_without_entering_sources() -> None:
    result = _invoke_cat([])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "cat: missing mapped filesystem operand\n"


@pytest.mark.parametrize(
    ("arguments", "stderr"),
    [
        (["-u", "memory:/file"], "cat: -u: unsupported option\n"),
        (["-A", "memory:/file"], "cat: -A: unsupported option\n"),
        (["--help=x", "memory:/file"], "cat: --help=x: unsupported option\n"),
        (["-"], "cat: -: unsupported operand\n"),
        (["--", "-"], "cat: -: unsupported operand\n"),
        (["/bare"], "cat: /bare: invalid mapped filesystem operand\n"),
        (
            ["memory:relative"],
            "cat: memory:relative: invalid mapped filesystem operand\n",
        ),
    ],
)
def test_cat_preflight_rejects_unsupported_shapes(
    arguments: list[str],
    stderr: str,
) -> None:
    result = _invoke_cat(arguments)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == stderr


def test_cat_emits_exact_bytes_for_one_file_in_operand_order() -> None:
    events: list[tuple[object, ...]] = []
    temps: list[str] = []
    payload = b"hello\0world\xff\n"

    def hook(rpath: str, lpath: str) -> None:
        del rpath
        temps.append(lpath)
        Path(lpath).write_bytes(payload)

    source = _RecordingSource(events, get_file_hook=hook)

    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout_bytes == payload
    assert result.stderr == ""
    assert [event[0] for event in events] == [
        "factory",
        "enter",
        "info",
        "get_file",
        "exit",
    ]
    assert len(temps) == 1
    assert not Path(temps[0]).exists()


def test_cat_acquires_all_sources_before_first_info_or_get_file() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        get_file_by_path={"/one": b"a", "/two": b"b", "/three": b"a"},
    )

    result = _invoke_cat(
        ["alpha:/one", "beta:/two", "alpha:/three"],
        sources={
            "alpha": source,
            "beta": source,
            "unused": _source_must_not_run,
        },
    )

    assert result.exit_code == 0
    assert result.stdout_bytes == b"aba"
    assert [(event[0], *event[1:-1]) for event in events] == [
        ("factory",),
        ("enter", 1),
        ("factory",),
        ("enter", 2),
        ("info", 1, "/one"),
        ("get_file", 1, "/one"),
        ("info", 2, "/two"),
        ("get_file", 2, "/two"),
        ("info", 1, "/three"),
        ("get_file", 1, "/three"),
        ("exit", 2),
        ("exit", 1),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        bytes(range(256)),
        b"\xff\xfe invalid",
        b"cr\r lf\n nul\0",
        b"no-final-newline",
        b"x" * (1 << 20),
    ],
    ids=[
        "empty",
        "all-bytes",
        "invalid-utf8",
        "cr-lf-nul",
        "no-final-newline",
        "large",
    ],
)
def test_cat_forwards_binary_payloads_verbatim(payload: bytes) -> None:
    source = _RecordingSource([], get_file_content=payload)

    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout_bytes == payload
    assert result.stderr == ""


def test_cat_continues_after_staging_failures_and_keeps_earlier_bytes() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        info_by_path={
            "/ok": {"type": "file"},
            "/missing": FileNotFoundError(),
            "/dir": {"type": "directory"},
            "/link": {"type": "other"},
            "/denied": PermissionError(),
            "/later": {"type": "file"},
        },
        get_file_by_path={
            "/ok": b"OK",
            "/later": b"LATER",
            "/denied": PermissionError(),
        },
    )

    result = _invoke_cat(
        [
            "memory:/ok",
            "memory:/missing",
            "memory:/dir",
            "memory:/link",
            "memory:/denied",
            "memory:/later",
        ],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout_bytes == b"OKLATER"
    assert result.stderr == (
        "cat: memory:/missing: not found\n"
        "cat: memory:/dir: incompatible result\n"
        "cat: memory:/link: incompatible result\n"
        "cat: memory:/denied: permission denied\n"
    )


def test_cat_reports_download_failure_without_emitting_bytes() -> None:
    source = _RecordingSource(
        [],
        get_file_error=OSError("download"),
    )

    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == ("cat: memory:/blob: backend failure (OSError): download\n")


def test_cat_reports_temporary_creation_failure(monkeypatch) -> None:
    source = _RecordingSource([], get_file_content=b"secret")

    def fail_mkstemp(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        message = "disk\\\0\r\nfull"
        raise OSError(message)

    monkeypatch.setattr(tempfile, "mkstemp", fail_mkstemp)
    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == (
        "cat: memory:/blob: staging failure (OSError): disk\\\\\\0\\r\\nfull\n"
    )
    assert "secret" not in result.stderr
    assert [event[0] for event in source.events] == ["factory", "enter", "info", "exit"]


def test_cat_stops_on_stdout_failure_and_preserves_accepted_bytes(
    monkeypatch,
) -> None:
    source = _RecordingSource(
        [],
        get_file_by_path={"/one": b"abcdef", "/two": b"SHOULD_NOT"},
    )
    accepted: list[bytes] = []

    class _PrefixStdout:
        def write(self, chunk: bytes) -> int:
            if not isinstance(chunk, bytes):
                raise TypeError
            accepted.append(chunk[:3])
            return 3

        def flush(self) -> None:
            return None

    monkeypatch.setattr("fsspec_cli._cat._binary_stdout", _PrefixStdout)
    result = _invoke_cat(
        ["memory:/one", "memory:/two"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert accepted == [b"abc"]
    assert result.stderr == "cat: output: output failure (OSError): short write\n"
    get_files = [event for event in source.events if event[0] == "get_file"]
    assert len(get_files) == 1


def test_cat_keeps_broken_pipe_silent_but_reports_exit_failure(
    monkeypatch,
) -> None:
    broken_pipe = BrokenPipeError()
    source = _RecordingSource(
        [],
        get_file_content=b"data",
        exit_error=OSError("cleanup"),
    )

    def break_stdout(chunk: bytes) -> None:
        del chunk
        raise broken_pipe

    monkeypatch.setattr("fsspec_cli._cat._write_stdout", break_stdout)
    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stderr == ("cat: memory: source exit failure (OSError): cleanup\n")
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is BrokenPipeError
    assert exception is broken_pipe
    assert traceback is not None


def test_cat_removes_temporary_after_cleanup_failure(monkeypatch) -> None:
    temps: list[str] = []
    source = _RecordingSource([])

    def tracking_hook(rpath: str, lpath: str) -> None:
        del rpath
        temps.append(lpath)
        Path(lpath).write_bytes(b"data")

    source.get_file_hook = tracking_hook
    real_unlink = Path.unlink

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if temps and self == Path(temps[0]):
            message = "busy"
            raise OSError(message)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b"data"
    assert result.stderr == ("cat: memory:/blob: staging failure (OSError): busy\n")


def test_cat_unknown_name_lists_known_sources() -> None:
    result = _invoke_cat(
        ["zeta:/file"],
        sources={"beta": _source_must_not_run, "alpha": _source_must_not_run},
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "cat: zeta:/file: unknown filesystem (known: alpha, beta)\n"
    )
