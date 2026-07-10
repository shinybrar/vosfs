"""Tests for the public vosfs package helpers."""

from vosfs import hello


def test_hello_returns_greeting() -> None:
    """Return the package greeting."""
    assert hello() == "Hello from vosfs!"
