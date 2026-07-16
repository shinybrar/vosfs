"""Tests for the installed fsspec-cli distribution boundary."""

from importlib.metadata import version

import fsspec_cli


def test_distribution_imports_and_reports_an_installed_version() -> None:
    assert fsspec_cli.__name__ == "fsspec_cli"
    assert version("fsspec-cli")
