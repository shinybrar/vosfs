"""Mapped-file ``cat`` tests through the public embedded-command seam."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import NoReturn

import pytest
from fsspec.asyn import AsyncFileSystem
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
            real_unlink(self, *args, **kwargs)
            message = "busy"
            raise OSError(message)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b"data"
    assert result.stderr == ("cat: memory:/blob: staging failure (OSError): busy\n")
    assert len(temps) == 1
    assert not Path(temps[0]).exists()


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


def test_cat_stops_acquisition_after_a_source_factory_failure() -> None:
    events: list[tuple[object, ...]] = []
    factory_error = ValueError("factory\\\0\r\n")
    first = _RecordingSource(events, exit_result=True)

    def broken_source() -> NoReturn:
        raise factory_error

    result = _invoke_cat(
        ["first:/one", "broken:/two", "later:/three"],
        sources={
            "first": first,
            "broken": broken_source,
            "later": _source_must_not_run,
        },
    )

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == (
        "cat: broken: source factory failure (ValueError): factory\\\\\\0\\r\\n\n"
    )
    assert [event[0] for event in events] == ["factory", "enter", "exit"]
    exception_type, exception, traceback = first.exit_calls[0]
    assert exception_type is ValueError
    assert exception is factory_error
    assert traceback is not None


@pytest.mark.parametrize(
    "incompatible_manager",
    [
        object(),
        type("MissingExit", (), {"__aenter__": lambda _self: None})(),
        type("NonCallable", (), {"__aenter__": 1, "__aexit__": 2})(),
    ],
)
def test_cat_rejects_an_incompatible_source_context_manager(
    incompatible_manager: object,
) -> None:
    def source() -> object:
        return incompatible_manager

    result = _invoke_cat(["broken:/file"], sources={"broken": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == (
        "cat: broken: source factory returned incompatible async context manager\n"
    )


def test_cat_stops_after_source_entry_failure_without_exiting_failed_entry() -> None:
    events: list[tuple[object, ...]] = []
    entry_error = LookupError("entry\\\0\r\n")
    first = _RecordingSource(events)

    class BrokenContext:
        async def __aenter__(self) -> NoReturn:
            events.append(("broken-enter", id(asyncio.get_running_loop())))
            raise entry_error

        async def __aexit__(self, *exc_info: object) -> NoReturn:
            raise AssertionError

    def broken_source() -> BrokenContext:
        events.append(("broken-factory",))
        return BrokenContext()

    result = _invoke_cat(
        ["first:/one", "broken:/two", "later:/three"],
        sources={
            "first": first,
            "broken": broken_source,
            "later": _source_must_not_run,
        },
    )

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == (
        "cat: broken: source entry failure (LookupError): entry\\\\\\0\\r\\n\n"
    )
    assert [event[0] for event in events] == [
        "factory",
        "enter",
        "broken-factory",
        "broken-enter",
        "exit",
    ]
    exception_type, exception, traceback = first.exit_calls[0]
    assert exception_type is LookupError
    assert exception is entry_error
    assert traceback is not None


@pytest.mark.parametrize(
    "filesystem_factory",
    [
        object,
        lambda: AsyncFileSystem(asynchronous=False, skip_instance_cache=True),
        lambda: _async_filesystem_with_flag("async_impl", value=False),
        lambda: _async_filesystem_with_flag("asynchronous", value=1),
    ],
)
def test_cat_exits_a_source_that_yields_an_incompatible_filesystem(
    filesystem_factory,
) -> None:
    events: list[tuple[object, ...]] = []

    class YieldingContext:
        async def __aenter__(self) -> object:
            events.append(("enter", id(asyncio.get_running_loop())))
            return filesystem_factory()

        async def __aexit__(self, *exc_info: object) -> None:
            events.append(("exit", id(asyncio.get_running_loop())))

    def source() -> YieldingContext:
        events.append(("factory",))
        return YieldingContext()

    result = _invoke_cat(["broken:/file"], sources={"broken": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == (
        "cat: broken: source yielded incompatible async filesystem\n"
    )
    assert [event[0] for event in events] == ["factory", "enter", "exit"]
    assert events[1][-1] == events[2][-1]


def _async_filesystem_with_flag(
    name: str,
    *,
    value: object,
) -> AsyncFileSystem:
    filesystem = AsyncFileSystem(asynchronous=True, skip_instance_cache=True)
    setattr(filesystem, name, value)
    return filesystem


class _ControlFlow(BaseException):
    pass


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_cat_removes_temporary_on_get_file_cancellation(
    control: BaseException,
) -> None:
    temps: list[str] = []
    source = _RecordingSource([])

    def tracking_hook(rpath: str, lpath: str) -> None:
        del rpath
        temps.append(lpath)
        Path(lpath).write_bytes(b"secret")
        raise control

    source.get_file_hook = tracking_hook

    with pytest.raises(type(control)) as caught:
        _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    assert len(temps) == 1
    assert not Path(temps[0]).exists()
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(control)
    assert exception is control
    assert traceback is not None


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_cat_retries_temporary_cleanup_after_get_file_cancellation(
    control: BaseException,
    monkeypatch,
) -> None:
    temps: list[str] = []
    diagnostics: list[tuple[str, str, Exception]] = []
    unlink_attempts = 0
    source = _RecordingSource([])
    real_unlink = Path.unlink

    def tracking_hook(rpath: str, lpath: str) -> None:
        del rpath
        temps.append(lpath)
        Path(lpath).write_bytes(b"secret")
        raise control

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        nonlocal unlink_attempts
        if temps and self == Path(temps[0]):
            unlink_attempts += 1
            if unlink_attempts == 1:
                message = "unlink-denied"
                raise OSError(message)
        real_unlink(self, *args, **kwargs)

    def capture_render(command: str, operand: object, error: Exception) -> None:
        diagnostics.append((command, getattr(operand, "spelling", ""), error))

    source.get_file_hook = tracking_hook
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    monkeypatch.setattr("fsspec_cli._cat._render_staging_failure", capture_render)

    with pytest.raises(type(control)) as caught:
        _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    assert len(temps) == 1
    assert unlink_attempts == 2
    assert not Path(temps[0]).exists()
    assert diagnostics == []


def test_cat_continues_after_temporary_open_failure(monkeypatch) -> None:
    source = _RecordingSource(
        [],
        get_file_by_path={"/bad": b"secret", "/ok": b"OK"},
    )
    real_open = Path.open
    failed = False

    def fail_open(self: Path, *args: object, **kwargs: object):
        nonlocal failed
        if not failed and self.name.startswith("fsspec-cli-cat-") and "rb" in args:
            failed = True
            message = "open-denied"
            raise OSError(message)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    result = _invoke_cat(
        ["memory:/bad", "memory:/ok"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout_bytes == b"OK"
    assert result.stderr == (
        "cat: memory:/bad: staging failure (OSError): open-denied\n"
    )
    assert "secret" not in result.stderr


def test_cat_continues_after_temporary_read_failure(monkeypatch) -> None:
    source = _RecordingSource(
        [],
        get_file_by_path={"/bad": b"secret", "/ok": b"OK"},
    )

    class _FailingHandle:
        def read(self, size: int = -1) -> bytes:
            del size
            message = "read-denied"
            raise OSError(message)

        def close(self) -> None:
            return None

    real_open = Path.open
    failed = False

    def fail_read_open(self: Path, *args: object, **kwargs: object):
        nonlocal failed
        if not failed and self.name.startswith("fsspec-cli-cat-") and "rb" in args:
            failed = True
            return _FailingHandle()
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_read_open)
    result = _invoke_cat(
        ["memory:/bad", "memory:/ok"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout_bytes == b"OK"
    assert result.stderr == (
        "cat: memory:/bad: staging failure (OSError): read-denied\n"
    )
    assert "secret" not in result.stderr


def test_cat_continues_after_temporary_close_failure(monkeypatch) -> None:
    source = _RecordingSource(
        [],
        get_file_by_path={"/bad": b"", "/ok": b"OK"},
    )
    real_open = Path.open
    failed = False

    class _CloseFailHandle:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        def read(self, size: int = -1) -> bytes:
            return self._handle.read(size)

        def seek(self, offset: int) -> int:
            return self._handle.seek(offset)

        def close(self) -> None:
            self._handle.close()
            message = "close-denied"
            raise OSError(message)

    def wrap_open(self: Path, *args: object, **kwargs: object):
        nonlocal failed
        handle = real_open(self, *args, **kwargs)
        if not failed and self.name.startswith("fsspec-cli-cat-") and "rb" in args:
            failed = True
            return _CloseFailHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", wrap_open)
    result = _invoke_cat(
        ["memory:/bad", "memory:/ok"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    # Failed staging operand emits zero bytes (per-operand atomicity).
    assert result.stdout_bytes == b"OK"
    assert result.stderr == (
        "cat: memory:/bad: staging failure (OSError): close-denied\n"
    )


def test_cat_continues_after_delayed_temporary_read_failure(monkeypatch) -> None:
    source = _RecordingSource(
        [],
        get_file_by_path={"/bad": b"partial-secret", "/ok": b"OK"},
    )

    class _DelayedFailHandle:
        def __init__(self) -> None:
            self._reads = 0

        def read(self, size: int = -1) -> bytes:
            del size
            self._reads += 1
            if self._reads == 1:
                return b"partial"
            message = "late-read-denied"
            raise OSError(message)

        def close(self) -> None:
            return None

    real_open = Path.open
    failed = False

    def fail_late_read_open(self: Path, *args: object, **kwargs: object):
        nonlocal failed
        if not failed and self.name.startswith("fsspec-cli-cat-") and "rb" in args:
            failed = True
            return _DelayedFailHandle()
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_late_read_open)
    result = _invoke_cat(
        ["memory:/bad", "memory:/ok"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout_bytes == b"OK"
    assert result.stderr == (
        "cat: memory:/bad: staging failure (OSError): late-read-denied\n"
    )
    assert "partial" not in result.stdout_bytes.decode("latin1")
    assert "secret" not in result.stderr


def test_cat_stops_when_single_handle_read_fails_after_first_output(
    monkeypatch,
) -> None:
    source = _RecordingSource(
        [],
        get_file_by_path={"/bad": b"secret", "/later": b"LATER"},
    )
    open_calls = 0

    class _PostOutputFailHandle:
        def __init__(self) -> None:
            self._phase = "validate"
            self._reads = 0

        def read(self, size: int = -1) -> bytes:
            del size
            self._reads += 1
            if self._phase == "validate":
                return b"validated" if self._reads == 1 else b""
            if self._reads == 1:
                return b"accepted"
            message = "post-output-read-denied"
            raise OSError(message)

        def seek(self, offset: int) -> int:
            assert offset == 0
            self._phase = "emit"
            self._reads = 0
            return 0

        def close(self) -> None:
            return None

    real_open = Path.open

    def fail_after_output_open(self: Path, *args: object, **kwargs: object):
        nonlocal open_calls
        if self.name.startswith("fsspec-cli-cat-") and "rb" in args:
            open_calls += 1
            if open_calls == 1:
                return _PostOutputFailHandle()
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_after_output_open)
    result = _invoke_cat(
        ["memory:/bad", "memory:/later"],
        sources={"memory": source},
    )

    assert result.exit_code == 1
    assert result.stdout_bytes == b"accepted"
    assert result.stderr == (
        "cat: output: output failure (OSError): post-output-read-denied\n"
    )
    assert open_calls == 1
    assert [event[0] for event in source.events].count("get_file") == 1


def test_cat_reports_cleanup_failure_after_download_failure(monkeypatch) -> None:
    temps: list[str] = []
    source = _RecordingSource([], get_file_error=OSError("download"))
    real_unlink = Path.unlink
    real_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, path = real_mkstemp(*args, **kwargs)
        temps.append(path)
        return descriptor, path

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if temps and self == Path(temps[0]):
            real_unlink(self, *args, **kwargs)
            message = "busy"
            raise OSError(message)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == ("cat: memory:/blob: staging failure (OSError): busy\n")
    assert len(temps) == 1
    assert not Path(temps[0]).exists()


def test_cat_finally_closes_descriptor_after_two_os_close_failures(monkeypatch) -> None:
    temps: list[str] = []
    closed: list[int] = []
    source = _RecordingSource([], get_file_content=b"secret")
    real_mkstemp = tempfile.mkstemp
    real_close = os.close
    attempts = 0

    def tracking_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, path = real_mkstemp(*args, **kwargs)
        temps.append(path)
        return descriptor, path

    def fail_close(fd: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            # True pre-close failure: descriptor still open and must be released.
            message = "close-denied"
            raise OSError(message)
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(os, "close", fail_close)
    result = _invoke_cat(["memory:/blob"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout_bytes == b""
    assert result.stderr == (
        "cat: memory:/blob: staging failure (OSError): close-denied\n"
    )
    assert len(temps) == 1
    assert not Path(temps[0]).exists()
    assert attempts >= 3
    assert closed
    assert [event[0] for event in source.events] == ["factory", "enter", "info", "exit"]
    assert "secret" not in result.stderr
