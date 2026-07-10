"""Tests for pull-request issue-link policy."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "pr_metadata"
SCRIPT = Path(__file__).parents[1] / "scripts" / "check_pr_metadata.py"


@pytest.mark.parametrize(
    ("fixture_name", "expected_returncode"),
    [
        ("valid", 0),
        ("missing", 1),
        ("cross-repository", 1),
        ("release-please", 0),
        ("release-please-wrong-author", 1),
        ("release-please-wrong-branch", 1),
        ("release-please-wrong-label", 1),
        ("dependabot", 0),
        ("dependabot-wrong-branch", 1),
    ],
)
def test_pull_request_issue_link_policy(
    fixture_name: str,
    expected_returncode: int,
    tmp_path: Path,
) -> None:
    """Accept local closures and exact automation exceptions only."""
    fixture = json.loads((FIXTURES / f"{fixture_name}.json").read_text())
    event_path = tmp_path / "event.json"
    references_path = tmp_path / "references.json"
    event_path.write_text(json.dumps(fixture["event"]))
    references_path.write_text(json.dumps(fixture["references"]))

    result = subprocess.run(  # noqa: S603
        [sys.executable, SCRIPT, event_path, references_path],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == expected_returncode, result.stderr


def test_push_event_does_not_require_pull_request_metadata(tmp_path: Path) -> None:
    """Allow non-pull-request workflow triggers."""
    event_path = tmp_path / "event.json"
    references_path = tmp_path / "references.json"
    event_path.write_text('{"ref": "refs/heads/main"}')
    references_path.write_text("[]")

    result = subprocess.run(  # noqa: S603
        [sys.executable, SCRIPT, event_path, references_path],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
