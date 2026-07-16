"""Tests for the installed fsspec-cli distribution boundary."""

from importlib import import_module
from importlib.metadata import version


def test_distribution_imports_at_its_initial_version() -> None:
    """The installed package imports and reports its initial version."""
    package = import_module("fsspec_cli")

    assert package.__name__ == "fsspec_cli"  # noqa: S101
    assert version("fsspec-cli") == "0.1.0"  # noqa: S101
