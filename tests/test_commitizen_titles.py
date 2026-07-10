"""Tests for the pull-request Conventional Commit title gate."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("title", "expected_outcome"),
    [
        ("ci: enforce the required quality gate", "success"),
        ("chore: release 1.2.3", "success"),
        ("Add the required quality gate", "failure"),
    ],
)
def test_pull_request_title_uses_commitizen_schema(
    title: str,
    expected_outcome: str,
) -> None:
    """Accept only titles allowed by the configured Commitizen schema."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "commitizen", "check", "--message", title],
        check=False,
        capture_output=True,
        text=True,
    )

    outcome = "success" if result.returncode == 0 else "failure"
    assert outcome == expected_outcome
