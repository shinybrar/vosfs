"""Directory listing tests through the public embedded-command seam."""

import asyncio
import locale
from collections.abc import Callable

import pytest
import typer

from ._support import _invoke_ls, _RecordingSource


def test_ls_lists_one_directory_through_names_only_async_operations() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        {"type": "directory"},
        ls_result=["/docs/notes.txt", "/docs/guide.md"],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == "guide.md\nnotes.txt\n"
    assert result.stderr == ""
    assert [(event[0], *event[2:-1]) for event in events] == [
        ("factory",),
        ("enter",),
        ("info", "/docs"),
        ("ls", "/docs", False),
        ("exit",),
    ]


def test_ls_writes_nothing_for_an_empty_directory() -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=[],
    )

    result = _invoke_ls(["memory:/empty"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "listing",
    [
        None,
        "/docs/guide.md",
        {"name": "/docs/guide.md"},
        ("/docs/guide.md",),
        [1],
        ["/docs/guide.md", None],
    ],
)
def test_ls_rejects_non_concrete_names_lists(listing: object) -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=listing,
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "ls: memory:/docs: incompatible result\n"


@pytest.mark.parametrize(
    "child",
    [
        "memory:///docs/guide.md",
        "/other/guide.md",
        "/docs/nested/guide.md",
        "/docs/",
        "/docs/bad\0name",
        "/docs/bad\nname",
    ],
)
def test_ls_rejects_non_immediate_lexical_children(child: str) -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=[child],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "ls: memory:/docs: incompatible result\n"


@pytest.mark.parametrize(
    ("path", "listing", "stdout"),
    [
        ("/docs/", ["/docs/guide.md"], "guide.md\n"),
        ("/docs///", ["/docs/guide.md"], "guide.md\n"),
        ("/", ["/guide.md"], "guide.md\n"),
    ],
)
def test_ls_validates_root_and_trailing_slash_children(
    path: str,
    listing: list[str],
    stdout: str,
) -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        {"type": "directory"},
        ls_result=listing,
    )

    result = _invoke_ls([f"memory:{path}"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == stdout
    assert result.stderr == ""
    assert [(event[0], event[2]) for event in events if event[0] in {"info", "ls"}] == [
        ("info", path),
        ("ls", path),
    ]


def test_ls_omits_dot_prefixed_directory_children_by_default() -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=[
            "/docs/.hidden",
            "/docs/visible",
            "/docs/.",
            "/docs/..",
        ],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == "visible\n"
    assert result.stderr == ""


def test_ls_almost_all_includes_hidden_children_but_not_dot_entries() -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=[
            "/docs/visible",
            "/docs/..",
            "/docs/.hidden",
            "/docs/.",
        ],
    )

    result = _invoke_ls(["-A", "memory:/docs"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == ".hidden\nvisible\n"
    assert result.stderr == ""


def test_ls_preserves_duplicate_directory_children() -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=["/docs/guide.md", "/docs/guide.md"],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == "guide.md\nguide.md\n"
    assert result.stderr == ""


def test_ls_validates_hidden_children_before_filtering() -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=["/docs/visible", "/docs/.nested/bad"],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "ls: memory:/docs: incompatible result\n"


def test_ls_keeps_an_explicitly_named_dot_prefixed_file_operand() -> None:
    source = _RecordingSource([], {"type": "file"})

    result = _invoke_ls(["memory:/.hidden"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == "memory:/.hidden\n"
    assert result.stderr == ""


def test_ls_uses_current_collation_with_raw_string_ties(monkeypatch) -> None:
    collation_keys = {"zeta": "0", "beta": "1", "alpha": "1"}
    monkeypatch.setattr(locale, "strxfrm", collation_keys.__getitem__)
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=["/docs/beta", "/docs/zeta", "/docs/alpha"],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 0
    assert result.stdout == "zeta\nalpha\nbeta\n"
    assert result.stderr == ""


def test_ls_preserves_a_collation_failure_without_a_backend_diagnostic(
    monkeypatch,
) -> None:
    collation_error = ValueError("collation failed")

    def fail_collation(_name: str) -> str:
        raise collation_error

    monkeypatch.setattr(locale, "strxfrm", fail_collation)
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=["/docs/guide.md"],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.exception is collation_error
    assert result.stdout == ""
    assert result.stderr == ""
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is ValueError
    assert exception is collation_error
    assert traceback is not None


@pytest.mark.parametrize("stage", ["info", "ls"])
@pytest.mark.parametrize(
    ("error_factory", "category"),
    [
        (FileNotFoundError, "not found"),
        (PermissionError, "permission denied"),
        (NotADirectoryError, "not a directory"),
        (NotImplementedError, "unsupported operation"),
        (RuntimeError, "backend failure (RuntimeError): "),
        (
            lambda: RuntimeError("backend\\\0\r\n"),
            "backend failure (RuntimeError): backend\\\\\\0\\r\\n",
        ),
    ],
)
def test_ls_maps_runtime_failures_to_locked_categories(
    stage: str,
    error_factory: Callable[[], Exception],
    category: str,
) -> None:
    error = error_factory()
    source = _RecordingSource(
        [],
        {"type": "directory"},
        info_error=error if stage == "info" else None,
        ls_result=[],
        ls_error=error if stage == "ls" else None,
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"ls: memory:/docs: {category}\n"
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(error)
    assert exception is error
    assert traceback is not None


def test_ls_preserves_backend_error_when_its_diagnostic_write_fails(
    monkeypatch,
) -> None:
    backend_error = PermissionError("denied")
    renderer_error = RuntimeError("stderr failed")
    source = _RecordingSource([], info_error=backend_error)

    def fail_diagnostic(
        _message: object = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args
        if kwargs.get("err") is True:
            raise renderer_error
        raise AssertionError

    monkeypatch.setattr(typer, "echo", fail_diagnostic)

    result = _invoke_ls(["memory:/file"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.exception is renderer_error
    assert result.stdout == ""
    assert result.stderr == ""
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is PermissionError
    assert exception is backend_error
    assert traceback is not None


def test_ls_buffers_a_whole_directory_before_writing_stdout() -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_result=["/docs/accepted.txt", "/docs/nested/rejected.txt"],
    )

    result = _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "ls: memory:/docs: incompatible result\n"


class _ControlFlow(BaseException):
    pass


@pytest.mark.parametrize(
    "control",
    [asyncio.CancelledError(), _ControlFlow("stop")],
)
def test_ls_preserves_directory_listing_control_flow_unchanged(
    control: BaseException,
) -> None:
    source = _RecordingSource(
        [],
        {"type": "directory"},
        ls_error=control,
    )

    with pytest.raises(type(control)) as caught:
        _invoke_ls(["memory:/docs"], sources={"memory": source})

    assert type(caught.value) is type(control)
    if not isinstance(control, asyncio.CancelledError):
        assert caught.value is control
    exception_type, exception, traceback = source.exit_calls[0]
    assert exception_type is type(control)
    assert exception is control
    assert traceback is not None
