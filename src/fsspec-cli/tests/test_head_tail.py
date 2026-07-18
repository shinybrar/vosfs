"""``head`` and ``tail`` tests through the public embedded-command seam."""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn

import pytest
from click.utils import strip_ansi
from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App, AsyncFilesystemSource
from fsspec_cli._head_tail import _run_head, _run_tail
from typer.testing import CliRunner, Result

if TYPE_CHECKING:
    from types import TracebackType

_EXACT_HEAD_HELP = (
    "                                                                                \n"
    " Usage: head -c N [--] name:/path                                               \n"
    "                                                                                \n"
    " Display leading bytes                                                          \n"
    "                                                                                \n"
    "╭─ Options ────────────────────────────────────────────────────────────────────╮\n"
    "│ --help          Show this message and exit.                                  │\n"
    "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    "\n"
)
_EXACT_TAIL_HELP = _EXACT_HEAD_HELP.replace("head", "tail").replace(
    "leading bytes                                                          ",
    "trailing bytes                                                         ",
)


@dataclass(frozen=True)
class _ReadCall:
    operation: Literal["info", "cat_file"]
    path: str
    start: int | None = None
    end: int | None = None


class _ReadControl(BaseException):
    pass


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def _invoke(
    command: Literal["head", "tail"],
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(App(sources).typer_app, [command, *arguments])


class _ReadFileSystem(AsyncFileSystem):
    cachable = False

    def __init__(self, source: _ReadSource) -> None:
        super().__init__(asynchronous=True)
        self.source = source

    async def _info(self, path: str, **kwargs: object) -> object:
        assert not kwargs
        self.source.calls.append(_ReadCall("info", path))
        if self.source.info_error is not None:
            raise self.source.info_error
        return self.source.info_result

    async def _cat_file(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
        **kwargs: object,
    ) -> object:
        assert not kwargs
        self.source.calls.append(_ReadCall("cat_file", path, start, end))
        if self.source.cat_error is not None:
            raise self.source.cat_error
        return self.source.payload

    async def _get_file(self, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise AssertionError


class _ReadSource:
    def __init__(
        self,
        *,
        info_result: object = None,
        payload: object = b"abc",
        info_error: BaseException | None = None,
        cat_error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.info_result = {"size": 6} if info_result is None else info_result
        self.payload = payload
        self.info_error = info_error
        self.cat_error = cat_error
        self.exit_error = exit_error
        self.calls: list[_ReadCall] = []
        self.lifecycle: list[str] = []
        self.exit_calls: list[
            tuple[
                type[BaseException] | None,
                BaseException | None,
                TracebackType | None,
            ]
        ] = []

    def __call__(self) -> _ReadContext:
        self.lifecycle.append("factory")
        return _ReadContext(self)


class _ReadContext(AbstractAsyncContextManager[_ReadFileSystem]):
    def __init__(self, source: _ReadSource) -> None:
        self.source = source
        self.filesystem = _ReadFileSystem(source)

    async def __aenter__(self) -> _ReadFileSystem:
        self.source.lifecycle.append("enter")
        return self.filesystem

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.source.exit_calls.append((exc_type, exc, traceback))
        self.source.lifecycle.append("exit")
        if self.source.exit_error is not None:
            raise self.source.exit_error


def test_head_requests_exact_leading_range_and_emits_binary_bytes() -> None:
    payload = b"a\0\xff"
    source = _ReadSource(payload=payload)

    result = _invoke("head", ["-c", "003", "memory:/blob"], sources={"memory": source})

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (0, payload, "")
    assert source.calls == [_ReadCall("cat_file", "/blob", 0, 3)]
    assert source.lifecycle == ["factory", "enter", "exit"]


@pytest.mark.parametrize("command", ["head", "tail"])
def test_byte_count_rejects_the_interpreter_configured_digit_limit(
    command: Literal["head", "tail"],
) -> None:
    previous_limit = sys.get_int_max_str_digits()
    limit = sys.int_info.str_digits_check_threshold
    oversized = "9" * (limit + 1)
    try:
        sys.set_int_max_str_digits(limit)
        result = _invoke(command, ["-c", oversized, "memory:/blob"])
    finally:
        sys.set_int_max_str_digits(previous_limit)

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (
        2,
        b"",
        f"{command}: {oversized}: invalid byte count\n",
    )


@pytest.mark.parametrize(
    ("count", "expected_start", "payload"),
    [("2", 4, b"ef"), ("20", -14, b"abcdef"), ("0", 6, b"")],
)
def test_tail_reads_size_then_requests_exact_suffix_range(
    count: str,
    expected_start: int,
    payload: bytes,
) -> None:
    source = _ReadSource(info_result={"size": 6}, payload=payload)

    result = _invoke("tail", ["memory:/blob", "-c", count], sources={"memory": source})

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (0, payload, "")
    assert source.calls == [
        _ReadCall("info", "/blob"),
        _ReadCall("cat_file", "/blob", expected_start, None),
    ]


@pytest.mark.asyncio
async def test_runner_semantics_do_not_depend_on_the_diagnostic_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[bytes] = []

    class _Stdout:
        def write(self, payload: bytes) -> int:
            emitted.append(payload)
            return len(payload)

        def flush(self) -> None:
            return None

    monkeypatch.setattr("fsspec_cli._head_tail._binary_stdout", _Stdout)
    head_source = _ReadSource(info_result={"size": 6}, payload=b"ab")
    tail_source = _ReadSource(info_result={"size": 6}, payload=b"ef")

    await _run_head(
        "tail",
        ("-c", "2", "memory:/blob"),
        {"memory": head_source},
    )
    await _run_tail(
        "head",
        ("-c", "2", "memory:/blob"),
        {"memory": tail_source},
    )

    assert emitted == [b"ab", b"ef"]
    assert head_source.calls == [_ReadCall("cat_file", "/blob", 0, 2)]
    assert tail_source.calls == [
        _ReadCall("info", "/blob"),
        _ReadCall("cat_file", "/blob", 4, None),
    ]


@pytest.mark.parametrize(
    ("command", "expected_help"),
    [("head", _EXACT_HEAD_HELP), ("tail", _EXACT_TAIL_HELP)],
)
def test_byte_range_commands_leave_exact_help_to_the_framework(
    command: Literal["head", "tail"],
    expected_help: str,
) -> None:
    result = _invoke(command, ["--help"])

    assert (result.exit_code, strip_ansi(result.stdout), result.stderr) == (
        0,
        expected_help,
        "",
    )


@pytest.mark.parametrize("command", ["head", "tail"])
@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "exactly one byte-count selector is required"),
        (["memory:/a"], "exactly one byte-count selector is required"),
        (["-c"], "-c: option requires an argument"),
        (["-c", "1"], "missing mapped filesystem operand"),
        (
            ["-c", "1", "-c", "2", "memory:/a"],
            "exactly one byte-count selector is required",
        ),
        (
            ["-c", "-c", "2", "memory:/a"],
            "exactly one byte-count selector is required",
        ),
        (["-c1", "memory:/a"], "-c1: unsupported option"),
        (["-cc", "1", "memory:/a"], "-cc: unsupported option"),
        (["-n", "1", "memory:/a"], "-n: unsupported option"),
        (["-c", "-1", "memory:/a"], "-1: invalid byte count"),
        (["-c", "+1", "memory:/a"], "+1: invalid byte count"),
        (["-c", "1K", "memory:/a"], "1K: invalid byte count"),
        (["-c", "\u0661", "memory:/a"], "\u0661: invalid byte count"),
        (["-c", "1", "memory:/a", "memory:/b"], "extra operand"),
        (["--", "-c", "1", "memory:/a"], "-c: invalid mapped filesystem operand"),
        (["-c", "1", "unknown:/a"], "unknown:/a: unknown filesystem (known: memory)"),
    ],
)
def test_byte_range_preflight_is_strict_and_source_free(
    command: Literal["head", "tail"],
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = _invoke(command, arguments)

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (
        2,
        b"",
        f"{command}: {diagnostic}\n",
    )


@pytest.mark.parametrize(
    ("command", "info_result", "expected_calls"),
    [
        ("head", {"size": 9}, [_ReadCall("cat_file", "/a", 0, 2)]),
        (
            "tail",
            {"size": 9},
            [_ReadCall("info", "/a"), _ReadCall("cat_file", "/a", 7, None)],
        ),
    ],
)
def test_byte_range_commands_accept_the_option_terminator(
    command: Literal["head", "tail"],
    info_result: object,
    expected_calls: list[_ReadCall],
) -> None:
    source = _ReadSource(info_result=info_result, payload=b"xy")

    result = _invoke(
        command, ["-c", "2", "--", "memory:/a"], sources={"memory": source}
    )

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (0, b"xy", "")
    assert source.calls == expected_calls


@pytest.mark.parametrize(
    "info_result", [None, {}, {"size": True}, {"size": -1}, {"size": 1.0}]
)
def test_tail_rejects_incompatible_info_before_read(info_result: object) -> None:
    source = _ReadSource(info_result=info_result)
    if info_result is None:
        source.info_result = None

    result = _invoke("tail", ["-c", "2", "memory:/a"], sources={"memory": source})

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (
        1,
        b"",
        "tail: memory:/a: incompatible result\n",
    )
    assert source.calls == [_ReadCall("info", "/a")]


class _HostileMapping(Mapping[str, object]):
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __getitem__(self, key: str) -> object:
        if key == "size":
            return 6
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(("size",))

    def __len__(self) -> int:
        return 1

    def get(self, key: str, default: object = None) -> object:
        del key, default
        raise self.error


def test_tail_treats_an_ordinary_hostile_mapping_as_incompatible() -> None:
    source = _ReadSource(info_result=_HostileMapping(RuntimeError("hostile")))

    result = _invoke("tail", ["-c", "2", "memory:/a"], sources={"memory": source})

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (
        1,
        b"",
        "tail: memory:/a: incompatible result\n",
    )
    assert source.calls == [_ReadCall("info", "/a")]
    assert source.exit_calls == [(None, None, None)]


def test_tail_preserves_control_flow_from_a_hostile_mapping() -> None:
    control = _ReadControl("stop")
    source = _ReadSource(info_result=_HostileMapping(control))

    with pytest.raises(_ReadControl) as caught:
        _invoke("tail", ["-c", "2", "memory:/a"], sources={"memory": source})

    assert caught.value is control
    assert source.lifecycle == ["factory", "enter", "exit"]
    assert source.exit_calls[0][1] is control


@pytest.mark.parametrize("command", ["head", "tail"])
@pytest.mark.parametrize("payload", [bytearray(b"ab"), "ab", b"abc"])
def test_byte_range_commands_reject_incompatible_payloads_atomically(
    command: Literal["head", "tail"],
    payload: object,
) -> None:
    source = _ReadSource(info_result={"size": 2}, payload=payload)

    result = _invoke(command, ["-c", "2", "memory:/a"], sources={"memory": source})

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (
        1,
        b"",
        f"{command}: memory:/a: incompatible result\n",
    )


@pytest.mark.parametrize(
    ("command", "info_error", "cat_error", "expected_calls"),
    [
        ("head", None, PermissionError(), [_ReadCall("cat_file", "/a", 0, 2)]),
        ("tail", FileNotFoundError(), None, [_ReadCall("info", "/a")]),
        (
            "tail",
            None,
            NotImplementedError(),
            [_ReadCall("info", "/a"), _ReadCall("cat_file", "/a", 4, None)],
        ),
    ],
)
def test_byte_range_backend_failures_stop_without_output(
    command: Literal["head", "tail"],
    info_error: Exception | None,
    cat_error: Exception | None,
    expected_calls: list[_ReadCall],
) -> None:
    source = _ReadSource(info_error=info_error, cat_error=cat_error)
    error = info_error or cat_error
    assert error is not None

    result = _invoke(command, ["-c", "2", "memory:/a"], sources={"memory": source})

    categories = {
        PermissionError: "permission denied",
        FileNotFoundError: "not found",
        NotImplementedError: "unsupported operation",
    }
    assert (result.exit_code, result.stdout_bytes, result.stderr) == (
        1,
        b"",
        f"{command}: memory:/a: {categories[type(error)]}\n",
    )
    assert source.calls == expected_calls
    assert source.exit_calls[0][1] is error


def test_head_reports_short_binary_write_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _ReadSource(payload=b"abc")

    class _ShortStdout:
        def write(self, payload: bytes) -> int:
            return len(payload) - 1

        def flush(self) -> None:
            raise AssertionError

    monkeypatch.setattr("fsspec_cli._head_tail._binary_stdout", _ShortStdout)
    result = _invoke("head", ["-c", "3", "memory:/a"], sources={"memory": source})

    assert (result.exit_code, result.stderr) == (
        1,
        "head: output: output failure (OSError): short write\n",
    )
    assert isinstance(source.exit_calls[0][1], OSError)


def test_head_reports_binary_flush_failure_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flush_error = OSError("flush failed")
    source = _ReadSource(payload=b"abc")

    class _FlushFailingStdout:
        def write(self, payload: bytes) -> int:
            return len(payload)

        def flush(self) -> NoReturn:
            raise flush_error

    monkeypatch.setattr("fsspec_cli._head_tail._binary_stdout", _FlushFailingStdout)
    result = _invoke("head", ["-c", "3", "memory:/a"], sources={"memory": source})

    assert (result.exit_code, result.stderr) == (
        1,
        "head: output: output failure (OSError): flush failed\n",
    )
    assert source.exit_calls[0][1] is flush_error


def test_tail_uses_cat_broken_pipe_status_when_it_is_the_only_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_pipe = BrokenPipeError()
    source = _ReadSource(payload=b"ef")

    class _BrokenStdout:
        def write(self, payload: bytes) -> NoReturn:
            del payload
            raise broken_pipe

        def flush(self) -> None:
            raise AssertionError

    monkeypatch.setattr("fsspec_cli._head_tail._binary_stdout", _BrokenStdout)
    result = _invoke("tail", ["-c", "2", "memory:/a"], sources={"memory": source})

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (141, b"", "")
    assert source.exit_calls[0][1] is broken_pipe


def test_tail_cleanup_failure_overrides_broken_pipe_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _ReadSource(payload=b"ef", exit_error=OSError("cleanup"))

    class _BrokenStdout:
        def write(self, payload: bytes) -> NoReturn:
            del payload
            raise BrokenPipeError

        def flush(self) -> None:
            raise AssertionError

    monkeypatch.setattr("fsspec_cli._head_tail._binary_stdout", _BrokenStdout)
    result = _invoke("tail", ["-c", "2", "memory:/a"], sources={"memory": source})

    assert (result.exit_code, result.stdout_bytes, result.stderr) == (
        1,
        b"",
        "tail: memory: source exit failure (OSError): cleanup\n",
    )


def test_head_cleans_up_then_propagates_backend_control_flow() -> None:
    control = _ReadControl("stop")
    source = _ReadSource(cat_error=control)

    with pytest.raises(_ReadControl) as caught:
        _invoke("head", ["-c", "2", "memory:/a"], sources={"memory": source})

    assert caught.value is control
    assert source.lifecycle == ["factory", "enter", "exit"]
