"""``find`` command tests through the public embedded-command seam."""

import locale
from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import NoReturn

import pytest
import typer
from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App, AsyncFilesystemSource
from typer.testing import CliRunner, Result

_EXACT_FIND_HELP = (
    "                                                                                \n"
    " Usage: find [--maxdepth N] [--type f|d] [--] name:/path                        \n"
    "                                                                                \n"
    " Find files recursively                                                         \n"
    "                                                                                \n"
    "╭─ Options ────────────────────────────────────────────────────────────────────╮\n"
    "│ --help          Show this message and exit.                                  │\n"
    "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    "\n"
)


class _ListSubclass(list[str]):
    pass


class _ExplodingMapping(dict[str, object]):
    def items(self) -> NoReturn:
        raise RuntimeError


class _ExplodingInfo(dict[str, object]):
    def get(self, key: str, default: object = None) -> NoReturn:
        del key, default
        raise RuntimeError


class _MalformedItemsMapping(dict[str, object]):
    def items(self) -> Iterator[object]:  # type: ignore[override]
        return iter(
            (
                ("/docs/good", {"type": "directory"}),
                ("/docs/bad",),
            )
        )


class _FindControl(BaseException):
    pass


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def _invoke_find(
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(App(sources).typer_app, ["find", *arguments])


class _FindFileSystem(AsyncFileSystem):
    cachable = False

    def __init__(self, source: "_FindSource") -> None:
        super().__init__(asynchronous=True)
        self.source = source

    async def _find(
        self,
        path: str,
        maxdepth: int | None = None,
        withdirs: bool = False,  # noqa: FBT002 - fsspec hook signature.
        **kwargs: object,
    ) -> object:
        detail = kwargs.pop("detail", False)
        assert type(detail) is bool
        self.source.find_calls.append((path, maxdepth, withdirs, detail, kwargs))
        if self.source.error is not None:
            raise self.source.error
        return self.source.result


class _FindSource:
    def __init__(
        self,
        *,
        result: object = (),
        error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.exit_error = exit_error
        self.lifecycle: list[str] = []
        self.find_calls: list[
            tuple[str, int | None, bool, bool, dict[str, object]]
        ] = []
        self.exit_calls: list[
            tuple[
                type[BaseException] | None,
                BaseException | None,
                TracebackType | None,
            ]
        ] = []

    def __call__(self) -> "_FindContext":
        self.lifecycle.append("factory")
        return _FindContext(self)


class _FindContext(AbstractAsyncContextManager[_FindFileSystem]):
    def __init__(self, source: _FindSource) -> None:
        self.source = source
        self.filesystem = _FindFileSystem(source)

    async def __aenter__(self) -> _FindFileSystem:
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


def test_find_renders_recursive_backend_file_paths_after_one_call() -> None:
    source = _FindSource(
        result=["/docs/sub/b.txt", "/docs/a.txt"],
    )

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "/docs/a.txt\n/docs/sub/b.txt\n",
        "",
    )
    assert source.lifecycle == ["factory", "enter", "exit"]
    assert source.find_calls == [
        ("/docs", None, False, False, {}),
    ]


def test_find_orders_paths_by_locale_then_raw_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FindSource(result=["/docs/b.txt", "/docs/z.txt", "/docs/a.txt"])
    transformed = {
        "/docs/z.txt": "0",
        "/docs/a.txt": "1",
        "/docs/b.txt": "1",
    }
    monkeypatch.setattr(locale, "strxfrm", transformed.__getitem__)

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "/docs/z.txt\n/docs/a.txt\n/docs/b.txt\n",
        "",
    )


def test_find_does_not_misclassify_an_internal_locale_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    internal_error = RuntimeError("locale failure")
    source = _FindSource(result=["/docs/a.txt"])

    def fail_locale(_path: str) -> str:
        raise internal_error

    monkeypatch.setattr(locale, "strxfrm", fail_locale)
    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (1, "", "")
    assert result.exception is internal_error
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is RuntimeError
    assert exception is internal_error
    assert traceback is not None


@pytest.mark.parametrize(
    ("arguments", "find_result", "call", "stdout"),
    [
        (
            ["--maxdepth", "2", "memory:/docs"],
            ["/docs/sub/b.txt", "/docs/a.txt"],
            ("/docs", 2, False, False),
            "/docs/a.txt\n/docs/sub/b.txt\n",
        ),
        (
            ["--maxdepth", "0002", "memory:/docs"],
            ["/docs/sub/b.txt", "/docs/a.txt"],
            ("/docs", 2, False, False),
            "/docs/a.txt\n/docs/sub/b.txt\n",
        ),
        (
            ["memory:/docs", "--type", "f"],
            ["/docs/a.txt"],
            ("/docs", None, False, False),
            "/docs/a.txt\n",
        ),
        (
            ["--type", "d", "memory:/docs"],
            {
                "/docs/sub": {"type": "directory"},
                "/docs/a.txt": {"type": "file"},
                "/docs": {"type": "directory"},
                "/docs/link": {"type": "other"},
            },
            ("/docs", None, True, True),
            "/docs\n/docs/sub\n",
        ),
        (
            ["--maxdepth", "3", "memory:/docs", "--maxdepth", "1"],
            ["/docs/a.txt"],
            ("/docs", 1, False, False),
            "/docs/a.txt\n",
        ),
        (
            ["--type", "d", "--type", "f", "memory:/docs"],
            ["/docs/a.txt"],
            ("/docs", None, False, False),
            "/docs/a.txt\n",
        ),
        (
            ["--", "memory:/docs"],
            ["/docs/a.txt"],
            ("/docs", None, False, False),
            "/docs/a.txt\n",
        ),
    ],
)
def test_find_accepts_locked_interspersed_options_and_call_shapes(
    arguments: list[str],
    find_result: object,
    call: tuple[str, int | None, bool, bool],
    stdout: str,
) -> None:
    source = _FindSource(result=find_result)

    result = _invoke_find(arguments, sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")
    assert source.find_calls == [(*call, {})]


@pytest.mark.parametrize(
    ("arguments", "find_result", "call", "stdout"),
    [
        (
            ["--maxdepth", "0", "memory:/docs"],
            ["/docs/child.txt"],
            ("/docs", 1, False, False),
            "",
        ),
        (
            ["--maxdepth", "0", "--type", "f", "memory:/file.txt"],
            ["/file.txt"],
            ("/file.txt", 1, False, False),
            "/file.txt\n",
        ),
        (
            ["--type", "d", "--maxdepth", "0", "memory:/docs/"],
            {
                "/docs/child": {"type": "directory"},
                "/docs": {"type": "directory"},
            },
            ("/docs/", 1, True, True),
            "/docs\n",
        ),
    ],
)
def test_find_maxdepth_zero_filters_the_single_backend_call_to_the_root(
    arguments: list[str],
    find_result: object,
    call: tuple[str, int, bool, bool],
    stdout: str,
) -> None:
    source = _FindSource(result=find_result)

    result = _invoke_find(arguments, sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")
    assert source.find_calls == [(*call, {})]


@pytest.mark.parametrize("arguments", [["--help"], ["--type", "d", "--help"]])
def test_find_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = _invoke_find(arguments)

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        _EXACT_FIND_HELP,
        "",
    )


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "find: missing mapped filesystem operand\n"),
        (["-x", "memory:/docs"], "find: -x: unsupported option\n"),
        (
            ["--maxdepth=1", "memory:/docs"],
            "find: --maxdepth=1: unsupported option\n",
        ),
        (["--type=f", "memory:/docs"], "find: --type=f: unsupported option\n"),
        (["--maxdepth"], "find: --maxdepth: option requires an argument\n"),
        (["--type"], "find: --type: option requires an argument\n"),
        (
            ["--maxdepth", "-1", "memory:/docs"],
            "find: -1: invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", "+1", "memory:/docs"],
            "find: +1: invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", "1.0", "memory:/docs"],
            "find: 1.0: invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", "", "memory:/docs"],
            "find: : invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", " ", "memory:/docs"],
            "find:  : invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", "\u0661", "memory:/docs"],
            "find: \u0661: invalid --maxdepth value\n",
        ),
        (
            ["--type", "x", "memory:/docs"],
            "find: x: invalid --type value\n",
        ),
        (
            ["memory:relative"],
            "find: memory:relative: invalid mapped filesystem operand\n",
        ),
        (
            ["unknown:/docs"],
            "find: unknown:/docs: unknown filesystem (known: memory)\n",
        ),
        (["memory:/a", "memory:/b"], "find: extra operand\n"),
        (
            ["--", "--help"],
            "find: --help: invalid mapped filesystem operand\n",
        ),
    ],
)
def test_find_preflight_failures_are_stable_and_source_free(
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = _invoke_find(arguments)

    assert (result.exit_code, result.stdout, result.stderr) == (2, "", diagnostic)


def test_find_rejects_a_depth_too_large_for_the_runtime_deterministically() -> None:
    value = "9" * 5000

    result = _invoke_find(["--maxdepth", value, "memory:/docs"])

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        f"find: {value}: invalid --maxdepth value\n",
    )


@pytest.mark.parametrize(
    "find_result",
    [
        None,
        (),
        "docs/a.txt",
        {"/docs/a.txt": {}},
        [1],
        ["/docs/bad\nname"],
        ["/docs/bad\0name"],
        _ListSubclass(["/docs/a.txt"]),
    ],
)
def test_find_rejects_incompatible_file_results_atomically(
    find_result: object,
) -> None:
    source = _FindSource(result=find_result)

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: memory:/docs: incompatible result\n",
    )


@pytest.mark.parametrize(
    "find_result",
    [
        None,
        [],
        {1: {"type": "directory"}},
        {"/docs": None},
        {"/docs": {}},
        {"/docs": {"type": True}},
        {"/docs": _ExplodingInfo({"type": "directory"})},
        {"/docs/bad\nname": {"type": "directory"}},
        {"/docs/bad\0name": {"type": "directory"}},
        _ExplodingMapping({"/docs": {"type": "directory"}}),
    ],
)
def test_find_rejects_incompatible_directory_results_atomically(
    find_result: object,
) -> None:
    source = _FindSource(result=find_result)

    result = _invoke_find(
        ["--type", "d", "memory:/docs"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: memory:/docs: incompatible result\n",
    )


def test_find_rejects_malformed_detailed_items_atomically_and_cleans_up() -> None:
    source = _FindSource(result=_MalformedItemsMapping())

    result = _invoke_find(
        ["--type", "d", "memory:/docs"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: memory:/docs: incompatible result\n",
    )
    assert source.lifecycle == ["factory", "enter", "exit"]
    assert source.find_calls == [("/docs", None, True, True, {})]


def test_find_validates_the_complete_result_before_output() -> None:
    source = _FindSource(result=["/docs/good", "/docs/bad\nname"])

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: memory:/docs: incompatible result\n",
    )


@pytest.mark.parametrize(
    ("error", "diagnostic"),
    [
        (FileNotFoundError(), "not found"),
        (PermissionError(), "permission denied"),
        (NotADirectoryError(), "not a directory"),
        (NotImplementedError(), "unsupported operation"),
        (
            RuntimeError("bad\\\0\n"),
            r"backend failure (RuntimeError): bad\\\x00\x0a",
        ),
    ],
)
def test_find_reports_backend_failures_and_passes_them_to_cleanup(
    error: Exception,
    diagnostic: str,
) -> None:
    source = _FindSource(error=error)

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"find: memory:/docs: {diagnostic}\n",
    )
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(error)
    assert exception is error
    assert traceback is not None


def test_find_cleans_up_after_an_output_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_error = OSError("write failed")
    source = _FindSource(result=["/docs/a"])
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
    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "find: output: output failure (OSError): write failed\n",
    )
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is OSError
    assert exception is output_error
    assert traceback is not None


def test_find_keeps_broken_pipe_silent_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_pipe = BrokenPipeError()
    source = _FindSource(result=["/docs/a"])

    def break_stdout(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise broken_pipe

    monkeypatch.setattr(typer, "echo", break_stdout)
    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (1, "", "")
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is BrokenPipeError
    assert exception is broken_pipe
    assert traceback is not None


def test_find_retains_complete_output_when_source_exit_fails() -> None:
    source = _FindSource(
        result=["/docs/a"],
        exit_error=OSError("cleanup"),
    )

    result = _invoke_find(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "/docs/a\n",
        "find: memory: source exit failure (OSError): cleanup\n",
    )


def test_find_cleans_up_then_propagates_backend_control_flow() -> None:
    control = _FindControl("stop")
    source = _FindSource(
        error=control,
        exit_error=OSError("cleanup"),
    )

    with pytest.raises(_FindControl) as caught:
        _invoke_find(["memory:/docs"], sources={"memory": source})

    assert caught.value is control
