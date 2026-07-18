"""``tree`` command tests through the public embedded-command seam."""

from __future__ import annotations

import asyncio
import locale
import sys
import threading
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Literal, NoReturn

import pytest
import typer
from click.utils import strip_ansi
from fsspec.asyn import AsyncFileSystem
from fsspec_cli import App, AsyncFilesystemSource
from typer.testing import CliRunner, Result

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from types import TracebackType

_EXACT_TREE_HELP = (
    "                                                                                \n"
    " Usage: tree [--maxdepth N] [--] name:/path                                     \n"
    "                                                                                \n"
    " Display a recursive directory tree                                             \n"
    "                                                                                \n"
    "╭─ Options ────────────────────────────────────────────────────────────────────╮\n"
    "│ --help          Show this message and exit.                                  │\n"
    "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    "\n"
)


class _TreeControl(BaseException):
    pass


class _CheckedIterator(Iterator[object]):
    def __init__(
        self,
        values: Sequence[object],
        invocation_thread: int,
        error: BaseException | None,
    ) -> None:
        self._values = iter(values)
        self._invocation_thread = invocation_thread
        self._error = error
        self.thread_ids: list[int] = []

    def __next__(self) -> object:
        thread_id = threading.get_ident()
        self.thread_ids.append(thread_id)
        if thread_id == self._invocation_thread:
            message = "synchronous walk iterator consumed on event loop"
            raise AssertionError(message)
        try:
            return next(self._values)
        except StopIteration:
            if self._error is not None:
                raise self._error from None
            raise


class _TreeFileSystem(AsyncFileSystem):
    cachable = False

    def __init__(self, source: _TreeSource) -> None:
        super().__init__(asynchronous=True)
        self.source = source

    def _walk(  # type: ignore[override]
        self,
        path: str,
        maxdepth: int | None = None,
        on_error: str = "omit",
        **kwargs: object,
    ) -> object:
        detail = kwargs.pop("detail", False)
        assert type(detail) is bool
        self.source.walk_calls.append((path, maxdepth, detail, on_error, kwargs))
        if self.source.invoke_error is not None:
            raise self.source.invoke_error
        if self.source.shape == "invalid":
            return self.source.rows
        if self.source.shape == "adapted-invalid":
            return self._invalid_adapted_rows()
        if self.source.shape == "native":
            return self._native_rows()
        return self._adapted_rows()

    async def _native_rows(self) -> AsyncIterator[object]:
        assert type(self.source.rows) is list
        for row in self.source.rows:
            yield row
        if self.source.iteration_error is not None:
            raise self.source.iteration_error

    async def _adapted_rows(self) -> Iterator[object]:
        if self.source.await_error is not None:
            raise self.source.await_error
        assert type(self.source.rows) is list
        iterator = _CheckedIterator(
            self.source.rows,
            threading.get_ident(),
            self.source.iteration_error,
        )
        self.source.iterators.append(iterator)
        return iterator

    async def _invalid_adapted_rows(self) -> object:
        return self.source.rows


class _TreeSource:
    def __init__(  # noqa: PLR0913 - focused backend-boundary test fake.
        self,
        *,
        rows: object | None = None,
        shape: Literal["native", "adapted", "invalid", "adapted-invalid"] = "native",
        invoke_error: BaseException | None = None,
        await_error: BaseException | None = None,
        iteration_error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.rows = rows if rows is not None else [("/docs", [], [])]
        self.shape = shape
        self.invoke_error = invoke_error
        self.await_error = await_error
        self.iteration_error = iteration_error
        self.exit_error = exit_error
        self.lifecycle: list[str] = []
        self.walk_calls: list[tuple[str, int | None, bool, str, dict[str, object]]] = []
        self.iterators: list[_CheckedIterator] = []
        self.exit_calls: list[
            tuple[
                type[BaseException] | None,
                BaseException | None,
                TracebackType | None,
            ]
        ] = []

    def __call__(self) -> _TreeContext:
        self.lifecycle.append("factory")
        return _TreeContext(self)


class _TreeContext(AbstractAsyncContextManager[_TreeFileSystem]):
    def __init__(self, source: _TreeSource) -> None:
        self.source = source
        self.filesystem = _TreeFileSystem(source)

    async def __aenter__(self) -> _TreeFileSystem:
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


class _BlockingIterator(Iterator[object]):
    def __init__(self, source: _BlockingSource) -> None:
        self.source = source

    def __next__(self) -> object:
        self.source.events.append("iterator-start")
        self.source.started.set()
        if not self.source.release.wait(timeout=5):
            message = "timed out waiting to release blocking iterator"
            raise AssertionError(message)
        self.source.events.append("iterator-done")
        raise StopIteration


class _BlockingFileSystem(AsyncFileSystem):
    cachable = False

    def __init__(self, source: _BlockingSource) -> None:
        super().__init__(asynchronous=True)
        self.source = source

    def _walk(  # type: ignore[override]
        self,
        path: str,
        maxdepth: int | None = None,
        on_error: str = "omit",
        **kwargs: object,
    ) -> object:
        assert (path, maxdepth, on_error, kwargs) == (
            "/docs",
            None,
            "raise",
            {"detail": False},
        )

        async def adapted_walk() -> Iterator[object]:
            return _BlockingIterator(self.source)

        return adapted_walk()


class _BlockingSource:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.events: list[str] = []

    def __call__(self) -> _BlockingContext:
        self.events.append("factory")
        return _BlockingContext(self)


class _BlockingContext(AbstractAsyncContextManager[_BlockingFileSystem]):
    def __init__(self, source: _BlockingSource) -> None:
        self.source = source

    async def __aenter__(self) -> _BlockingFileSystem:
        self.source.events.append("enter")
        return _BlockingFileSystem(self.source)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del traceback
        assert exc_type is asyncio.CancelledError
        assert isinstance(exc, asyncio.CancelledError)
        self.source.events.append("exit")


def _source_must_not_run() -> NoReturn:
    raise AssertionError


def _invoke_tree(
    arguments: list[str],
    *,
    sources: dict[str, AsyncFilesystemSource] | None = None,
) -> Result:
    if sources is None:
        sources = {"memory": _source_must_not_run}
    return CliRunner().invoke(App(sources).typer_app, ["tree", *arguments])


_TREE_ROWS: list[object] = [
    ("/docs", ["z-dir", "a-dir"], ["z.txt", "a.txt"]),
    ("/docs/a-dir", ["nested"], ["b.txt"]),
    ("/docs/a-dir/nested", [], ["c.txt"]),
    ("/docs/z-dir", [], []),
]

_TREE_OUTPUT = (
    "/docs\n"
    "├── a-dir\n"
    "│   ├── nested\n"
    "│   │   └── c.txt\n"
    "│   └── b.txt\n"
    "├── z-dir\n"
    "├── a.txt\n"
    "└── z.txt\n"
)


@pytest.mark.parametrize("shape", ["native", "adapted"])
def test_tree_consumes_both_pinned_walk_shapes_and_renders_exactly(
    shape: Literal["native", "adapted"],
) -> None:
    source = _TreeSource(rows=_TREE_ROWS, shape=shape)

    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        _TREE_OUTPUT,
        "",
    )
    assert source.lifecycle == ["factory", "enter", "exit"]
    assert source.walk_calls == [
        ("/docs", None, False, "raise", {}),
    ]
    if shape == "adapted":
        assert source.iterators[0].thread_ids
        assert set(source.iterators[0].thread_ids).isdisjoint({threading.get_ident()})


def test_tree_joins_adapted_iterator_before_source_exit_on_task_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _BlockingSource()
    real_run = asyncio.run

    def cancelling_run(coroutine: Coroutine[object, object, None]) -> None:
        async def supervise() -> None:
            command_task = asyncio.create_task(coroutine)
            started = await asyncio.to_thread(source.started.wait, 5)
            assert started
            command_task.cancel("original tree cancellation")
            asyncio.get_running_loop().call_later(0.01, source.release.set)
            await command_task

        real_run(supervise())

    monkeypatch.setattr(asyncio, "run", cancelling_run)

    with pytest.raises(asyncio.CancelledError) as caught:
        _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert caught.value.args == ("original tree cancellation",)
    assert source.events == [
        "factory",
        "enter",
        "iterator-start",
        "iterator-done",
        "exit",
    ]


@pytest.mark.parametrize(
    ("arguments", "backend_depth", "stdout"),
    [
        (["--maxdepth", "0", "memory:/docs"], 1, "/docs\n"),
        (
            ["--maxdepth", "1", "memory:/docs"],
            1,
            "/docs\n├── a-dir\n├── z-dir\n├── a.txt\n└── z.txt\n",
        ),
        (
            ["--maxdepth", "2", "memory:/docs"],
            2,
            "/docs\n├── a-dir\n│   ├── nested\n│   └── b.txt\n"
            "├── z-dir\n├── a.txt\n└── z.txt\n",
        ),
        (
            ["--maxdepth", "3", "memory:/docs", "--maxdepth", "1"],
            1,
            "/docs\n├── a-dir\n├── z-dir\n├── a.txt\n└── z.txt\n",
        ),
        (
            ["--maxdepth", "0001", "--", "memory:/docs"],
            1,
            "/docs\n├── a-dir\n├── z-dir\n├── a.txt\n└── z.txt\n",
        ),
    ],
)
def test_tree_depth_contract_filters_over_yielded_rows(
    arguments: list[str],
    backend_depth: int,
    stdout: str,
) -> None:
    source = _TreeSource(rows=_TREE_ROWS)

    result = _invoke_tree(arguments, sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")
    assert source.walk_calls == [
        ("/docs", backend_depth, False, "raise", {}),
    ]


@pytest.mark.parametrize(
    ("rows", "operand"),
    [
        ([("/empty", [], [])], "memory:/empty"),
        ([("/file.txt", [], [""])], "memory:/file.txt"),
    ],
)
def test_tree_empty_directory_and_file_root_render_only_the_operand(
    rows: list[object],
    operand: str,
) -> None:
    source = _TreeSource(rows=rows)

    result = _invoke_tree([operand], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        f"{operand.partition(':')[2]}\n",
        "",
    )


@pytest.mark.parametrize(
    ("operand", "rows", "stdout"),
    [
        ("memory:/", [("", ["docs"], []), ("/docs", [], [])], "/\n└── docs\n"),
        ("memory:/docs/", [("/docs", [], ["a.txt"])], "/docs/\n└── a.txt\n"),
    ],
)
def test_tree_preserves_operand_root_spelling_after_relationship_validation(
    operand: str,
    rows: list[object],
    stdout: str,
) -> None:
    source = _TreeSource(rows=rows)

    result = _invoke_tree([operand], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")


def test_tree_renders_a_valid_chain_deeper_than_the_python_recursion_limit() -> None:
    depth = sys.getrecursionlimit() + 25
    rows: list[object] = []
    path = "/root"
    for index in range(depth):
        name = f"d{index}"
        rows.append((path, [name], []))
        path = f"{path}/{name}"
    rows.append((path, [], ["leaf"]))
    source = _TreeSource(rows=rows)
    expected = "/root\n" + "".join(
        f"{'    ' * index}└── d{index}\n" for index in range(depth)
    )
    expected += f"{'    ' * depth}└── leaf\n"

    result = _invoke_tree(["memory:/root"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, expected, "")
    assert source.walk_calls == [("/root", None, False, "raise", {})]


def test_tree_orders_each_group_by_locale_then_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _TreeSource(rows=[("/docs", ["z-dir", "a-dir"], ["z", "a"])])
    transformed = {
        "z-dir": "directory",
        "a-dir": "directory",
        "z": "file",
        "a": "file",
    }
    monkeypatch.setattr(locale, "strxfrm", transformed.__getitem__)

    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "/docs\n├── a-dir\n├── z-dir\n├── a\n└── z\n",
        "",
    )


@pytest.mark.parametrize("arguments", [["--help"], ["--maxdepth", "2", "--help"]])
def test_tree_leaves_exact_help_to_the_framework(arguments: list[str]) -> None:
    result = _invoke_tree(arguments)

    assert (result.exit_code, strip_ansi(result.stdout), result.stderr) == (
        0,
        _EXACT_TREE_HELP,
        "",
    )


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "tree: missing mapped filesystem operand\n"),
        (["-L", "1", "memory:/docs"], "tree: -L: unsupported option\n"),
        (["--maxdepth=1", "memory:/docs"], "tree: --maxdepth=1: unsupported option\n"),
        (["--maxdepth"], "tree: --maxdepth: option requires an argument\n"),
        (["--maxdepth", "-1", "memory:/docs"], "tree: -1: invalid --maxdepth value\n"),
        (["--maxdepth", "+1", "memory:/docs"], "tree: +1: invalid --maxdepth value\n"),
        (
            ["--maxdepth", "1.0", "memory:/docs"],
            "tree: 1.0: invalid --maxdepth value\n",
        ),
        (
            ["--maxdepth", "\u0661", "memory:/docs"],
            "tree: \u0661: invalid --maxdepth value\n",
        ),
        (["memory:/a", "memory:/b"], "tree: extra operand\n"),
        (["--", "--help"], "tree: --help: invalid mapped filesystem operand\n"),
    ],
)
def test_tree_preflight_failures_are_stable_and_source_free(
    arguments: list[str],
    diagnostic: str,
) -> None:
    result = _invoke_tree(arguments)

    assert (result.exit_code, result.stdout, result.stderr) == (2, "", diagnostic)


def test_tree_rejects_a_runtime_oversized_depth_deterministically() -> None:
    value = "9" * 5000

    result = _invoke_tree(["--maxdepth", value, "memory:/docs"])

    assert (result.exit_code, result.stdout, result.stderr) == (
        2,
        "",
        f"tree: {value}: invalid --maxdepth value\n",
    )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [["/docs", [], []]],
        [("/docs", (), [])],
        [("/docs", [], ())],
        [(1, [], [])],
        [("/wrong", [], [])],
        [("/docs\nbad", [], [])],
        [("/docs", ["bad/name"], [])],
        [("/docs", [""], [])],
        [("/docs", [], ["bad\0name"])],
        [("/docs", ["same", "same"], [])],
        [("/docs", [], ["same", "same"])],
        [("/docs", ["same"], ["same"])],
        [("/docs", [], ["", "other"])],
        [("/docs", ["child"], []), ("/orphan", [], [])],
        [("/docs", ["child"], []), ("/docs", [], [])],
        [("/docs", [], []), ("/docs/child", [], [])],
    ],
)
def test_tree_rejects_malformed_or_impossible_walks_atomically(
    rows: list[object],
) -> None:
    source = _TreeSource(rows=rows)

    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "tree: memory:/docs: incompatible result\n",
    )
    assert source.lifecycle == ["factory", "enter", "exit"]


@pytest.mark.parametrize("invalid", [None, (), [], {}, "walk"])
def test_tree_rejects_an_incompatible_top_level_walk_shape(invalid: object) -> None:
    source = _TreeSource(rows=invalid, shape="invalid")

    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "tree: memory:/docs: incompatible result\n",
    )


def test_tree_rejects_an_awaitable_resolving_to_a_non_iterator() -> None:
    source = _TreeSource(rows=[("/docs", [], [])], shape="adapted-invalid")

    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "tree: memory:/docs: incompatible result\n",
    )


@pytest.mark.parametrize(
    ("stage", "error"),
    [
        ("invoke", RuntimeError("invoke")),
        ("await", RuntimeError("await")),
        ("iterate-native", RuntimeError("iterate native")),
        ("iterate-adapted", RuntimeError("iterate adapted")),
    ],
)
def test_tree_reports_every_ordinary_walk_failure_as_backend_failure(
    stage: str,
    error: Exception,
) -> None:
    source = _TreeSource(
        shape="adapted" if stage in {"await", "iterate-adapted"} else "native",
        invoke_error=error if stage == "invoke" else None,
        await_error=error if stage == "await" else None,
        iteration_error=error if stage.startswith("iterate") else None,
    )

    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"tree: memory:/docs: backend failure (RuntimeError): {error}\n",
    )
    assert source.exit_calls[0][1] is error


@pytest.mark.parametrize(
    "stage", ["invoke", "await", "iterate-native", "iterate-adapted"]
)
def test_tree_cleans_up_then_preserves_walk_control_flow(stage: str) -> None:
    control = _TreeControl(stage)
    source = _TreeSource(
        shape="adapted" if stage in {"await", "iterate-adapted"} else "native",
        invoke_error=control if stage == "invoke" else None,
        await_error=control if stage == "await" else None,
        iteration_error=control if stage.startswith("iterate") else None,
    )

    with pytest.raises(_TreeControl) as caught:
        _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert caught.value is control
    assert source.lifecycle == ["factory", "enter", "exit"]
    assert source.exit_calls[0][1] is control


def test_tree_cleans_up_after_output_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    output_error = OSError("write failed")
    source = _TreeSource()
    real_echo = typer.echo

    def fail_stdout(
        message: object = None,
        *_args: object,
        **kwargs: object,
    ) -> None:
        if kwargs.get("err") is True:
            real_echo(message, err=True)
            return
        raise output_error

    monkeypatch.setattr(typer, "echo", fail_stdout)
    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "tree: output: output failure (OSError): write failed\n",
    )
    assert source.exit_calls[0][1] is output_error


def test_tree_retains_complete_output_when_source_exit_fails() -> None:
    source = _TreeSource(exit_error=OSError("cleanup"))

    result = _invoke_tree(["memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "/docs\n",
        "tree: memory: source exit failure (OSError): cleanup\n",
    )
