"""Long-listing command tests through the public embedded-command seam."""

from types import MappingProxyType

import pytest
from click.utils import strip_ansi
from fsspec_cli._listing import to_listing as normalize_listing

from ._support import _invoke_ll, _invoke_ls, _RecordingSource


@pytest.mark.parametrize(
    ("command", "arguments", "stdout"),
    [
        (
            "ls",
            ["-l", "memory:/docs"],
            "file     2  guide.md\nfile  1536  notes.txt\n",
        ),
        (
            "ls",
            ["-lh", "memory:/docs"],
            "file    2B  guide.md\nfile  1.5K  notes.txt\n",
        ),
        (
            "ll",
            ["memory:/docs"],
            "file     2  guide.md\nfile  1536  notes.txt\n",
        ),
        (
            "ll",
            ["-h", "memory:/docs"],
            "file    2B  guide.md\nfile  1.5K  notes.txt\n",
        ),
    ],
)
def test_long_listing_renders_detail_rows_with_one_directory_call(
    command: str,
    arguments: list[str],
    stdout: str,
) -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        MappingProxyType({"name": "/docs", "type": "directory", "size": 0}),
        ls_result=[
            MappingProxyType({"name": "/docs/notes.txt", "type": "file", "size": 1536}),
            MappingProxyType({"name": "/docs/guide.md", "type": "file", "size": 2}),
        ],
    )

    invoke = _invoke_ls if command == "ls" else _invoke_ll
    result = invoke(arguments, sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, "")
    assert [(event[0], *event[2:-1]) for event in events] == [
        ("factory",),
        ("enter",),
        ("info", "/docs"),
        ("ls", "/docs", True),
        ("exit",),
    ]


def test_long_listing_file_uses_its_info_result_without_calling_ls() -> None:
    events: list[tuple[object, ...]] = []
    source = _RecordingSource(
        events,
        MappingProxyType({"name": "/docs/report.bin", "type": "file", "size": 2048}),
    )

    result = _invoke_ls(["-lh", "memory:/docs/report.bin"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "file  2K  report.bin\n",
        "",
    )
    assert [event[0] for event in events] == ["factory", "enter", "info", "exit"]


def test_long_listing_preserves_almost_all_selection_and_sorting() -> None:
    source = _RecordingSource(
        [],
        {"name": "/docs", "type": "directory"},
        ls_result=[
            {"name": "/docs/visible", "type": "file", "size": 1},
            {"name": "/docs/..", "type": "directory", "size": 0},
            {"name": "/docs/.hidden", "type": "file", "size": 2},
            {"name": "/docs/.", "type": "directory", "size": 0},
        ],
    )

    result = _invoke_ls(["-Al", "memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "file  2  .hidden\nfile  1  visible\n",
        "",
    )


def test_long_listing_does_not_normalize_omitted_hidden_rows(monkeypatch) -> None:
    hidden = {"name": "/docs/.hidden", "type": "file", "size": 2}

    def normalize_visible(info):
        if info is hidden:
            msg = "omitted hidden metadata must not be normalized"
            raise AssertionError(msg)
        return normalize_listing(info)

    monkeypatch.setattr("fsspec_cli._ls.to_listing", normalize_visible)
    source = _RecordingSource(
        [],
        {"name": "/docs", "type": "directory"},
        ls_result=[
            hidden,
            {"name": "/docs/visible", "type": "file", "size": 1},
        ],
    )

    result = _invoke_ls(["-l", "memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "file  1  visible\n",
        "",
    )


def test_long_listing_selected_normalization_failure_is_atomic(monkeypatch) -> None:
    rejected = {"name": "/docs/rejected", "type": "file", "size": 2}

    def reject_selected(info):
        if info is rejected:
            msg = "invalid selected metadata"
            raise ValueError(msg)
        return normalize_listing(info)

    monkeypatch.setattr("fsspec_cli._ls.to_listing", reject_selected)
    source = _RecordingSource(
        [],
        {"name": "/docs", "type": "directory"},
        ls_result=[
            {"name": "/docs/accepted", "type": "file", "size": 1},
            rejected,
        ],
    )

    result = _invoke_ls(["-l", "memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "ls: memory:/docs: incompatible result\n",
    )


def test_long_listing_preserves_multi_operand_grouping() -> None:
    source = _RecordingSource(
        [],
        info_by_path={
            "/z": {"name": "/z", "type": "directory", "size": 0},
            "/b.txt": {"name": "/b.txt", "type": "file", "size": 1},
            "/a": {"name": "/a", "type": "directory", "size": 0},
        },
        ls_by_path={
            "/z": [{"name": "/z/c.txt", "type": "file", "size": 3}],
            "/a": [],
        },
    )

    result = _invoke_ls(
        ["-l", "memory:/z", "memory:/b.txt", "memory:/a"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        ("file  1  b.txt\n\nmemory:/a:\n\nmemory:/z:\nfile  3  c.txt\n"),
        "",
    )


def test_long_listing_continues_after_an_incompatible_operand_atomically() -> None:
    source = _RecordingSource(
        [],
        info_by_path={
            "/bad": {"name": "/bad", "type": "directory"},
            "/good": {"name": "/good", "type": "directory"},
        },
        ls_by_path={
            "/bad": [
                {"name": "/bad/accepted.txt", "type": "file", "size": 1},
                {"name": "/bad/nested/rejected.txt", "type": "file", "size": 2},
            ],
            "/good": [{"name": "/good/ok.txt", "type": "file", "size": 4}],
        },
    )

    result = _invoke_ll(
        ["memory:/bad", "memory:/good"],
        sources={"memory": source},
    )

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "memory:/good:\nfile  4  ok.txt\n",
        "ll: memory:/bad: incompatible result\n",
    )
    assert [
        (event[0], event[2], event[3]) for event in source.events if event[0] == "ls"
    ] == [
        ("ls", "/bad", True),
        ("ls", "/good", True),
    ]


@pytest.mark.parametrize(
    "listing",
    [
        None,
        {"name": "/docs/a.txt", "type": "file", "size": 1},
        ({"name": "/docs/a.txt", "type": "file", "size": 1},),
        ["/docs/a.txt"],
        [{"type": "file", "size": 1}],
        [{"name": "/docs/bad\nname", "type": "file", "size": 1}],
    ],
)
def test_long_listing_rejects_non_concrete_detail_lists(listing: object) -> None:
    source = _RecordingSource(
        [],
        {"name": "/docs", "type": "directory"},
        ls_result=listing,
    )

    result = _invoke_ls(["-l", "memory:/docs"], sources={"memory": source})

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        "ls: memory:/docs: incompatible result\n",
    )


def test_ll_uses_its_own_typer_command_context() -> None:
    result = _invoke_ll(["--long", "memory:/docs"])

    assert (result.exit_code, result.stdout) == (2, "")
    diagnostic = strip_ansi(result.stderr)
    assert "Usage: root ll" in diagnostic
    assert "No such option: --long" in diagnostic
