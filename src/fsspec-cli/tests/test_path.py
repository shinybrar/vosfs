"""Pure lexical path helper tests."""

import pytest
from fsspec_cli._path import _lexical_basename


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
