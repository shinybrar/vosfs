"""Tests for the installed fsspec-cli distribution boundary."""

from importlib import import_module
from importlib.metadata import version


def test_distribution_imports_and_reports_an_installed_version() -> None:
    """The installed package imports and exposes its distribution version."""
    package = import_module("fsspec_cli")

    assert package.__name__ == "fsspec_cli"  # noqa: S101
    assert version("fsspec-cli")  # noqa: S101
