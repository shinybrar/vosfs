"""Pure lexical path helper tests."""

import pytest
from fsspec_cli._path import (
    _has_dot_segment,
    _has_final_dot_segment,
    _is_root,
    _lexical_basename,
    _lexical_join,
    _lexical_parent,
    _lexical_root,
    _strip_trailing_slashes,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("", ""),
        ("/", "/"),
        ("///", "/"),
        ("a", "a"),
        ("a/b", "b"),
        ("a/b/", "b"),
        ("memory:/docs/a.txt", "a.txt"),
    ],
)
def test_lexical_basename_preserves_the_locked_path_contract(
    path: str,
    expected: str,
) -> None:
    assert _lexical_basename(path) == expected


@pytest.mark.parametrize(
    ("path", "stripped", "root", "final_dot", "any_dot"),
    [
        ("/", "", True, False, False),
        ("///", "", True, False, False),
        ("/docs/", "/docs", False, False, False),
        ("/docs/.", "/docs/.", False, True, True),
        ("/docs/../", "/docs/..", False, True, True),
        ("/docs/./file", "/docs/./file", False, False, True),
        ("/.hidden", "/.hidden", False, False, False),
    ],
)
def test_lexical_path_safeguard_facts(
    path: str,
    stripped: str,
    root: bool,
    final_dot: bool,
    any_dot: bool,
) -> None:
    assert _strip_trailing_slashes(path) == stripped
    assert _is_root(path) is root
    assert _has_final_dot_segment(path) is final_dot
    assert _has_dot_segment(path) is any_dot


def test_lexical_root_maps_backend_empty_root_without_touching_other_spelling() -> None:
    assert _lexical_root("") == "/"
    assert _lexical_root("///") == "/"
    assert _lexical_root("/docs/") == "/docs"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("name", "."),
        ("/", "/"),
        ("///", "/"),
        ("/name", "/"),
        ("/docs/name", "/docs"),
        ("/docs/name/", "/docs"),
    ],
)
def test_lexical_parent_preserves_the_locked_path_contract(
    path: str,
    expected: str,
) -> None:
    assert _lexical_parent(path) == expected


@pytest.mark.parametrize(
    ("parent", "child", "expected"),
    [
        ("/", "name", "/name"),
        ("///", "name", "/name"),
        ("/docs", "name", "/docs/name"),
        ("/docs/", "name", "/docs/name"),
        ("/docs", "/", "/docs/"),
    ],
)
def test_lexical_join_preserves_the_locked_path_contract(
    parent: str,
    child: str,
    expected: str,
) -> None:
    assert _lexical_join(parent, child) == expected
