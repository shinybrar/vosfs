"""Enforce repository-local closing references for pull requests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


RELEASE_BRANCH = "release-please--branches--main"
RELEASE_LABEL = "autorelease: pending"
DEPENDABOT_AUTHOR = "dependabot[bot]"
DEPENDABOT_BRANCH_PREFIX = "dependabot/"


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        return {}
    return cast("dict[str, object]", value)


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        return ()
    return value


def _value(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        current = _mapping(current).get(key)
    return current


def _labels(pull_request: Mapping[str, object]) -> set[str]:
    return {
        name
        for label in _sequence(pull_request.get("labels"))
        if isinstance(name := _mapping(label).get("name"), str)
    }


def _is_automation_exception(
    event: Mapping[str, object],
    pull_request: Mapping[str, object],
) -> bool:
    author = _value(pull_request, "user", "login")
    branch = _value(pull_request, "head", "ref")
    repository = _value(event, "repository", "full_name")
    repository_owner = (
        repository.partition("/")[0] if isinstance(repository, str) else None
    )
    release_exception = (
        author == repository_owner
        and branch == RELEASE_BRANCH
        and RELEASE_LABEL in _labels(pull_request)
    )
    dependabot_exception = (
        author == DEPENDABOT_AUTHOR
        and isinstance(branch, str)
        and branch.startswith(DEPENDABOT_BRANCH_PREFIX)
    )
    return release_exception or dependabot_exception


def _reference_pages(references: object) -> Iterable[Mapping[str, object]]:
    if isinstance(references, dict):
        yield references
        return
    for page in _sequence(references):
        if isinstance(page, dict):
            yield page


def _has_local_closing_reference(
    event: Mapping[str, object],
    references: object,
) -> bool:
    repository = _value(event, "repository", "full_name")
    if not isinstance(repository, str):
        return False

    for page in _reference_pages(references):
        nodes = _value(
            page,
            "data",
            "repository",
            "pullRequest",
            "closingIssuesReferences",
            "nodes",
        )
        for node in _sequence(nodes):
            if _value(node, "repository", "nameWithOwner") == repository:
                return True
    return False


def check_metadata(event: object, references: object) -> str | None:
    """Return an error when a PR lacks a local closing reference.

    Args:
        event: Parsed GitHub workflow event JSON.
        references: Parsed paginated GraphQL response JSON.

    Returns:
        An error message, or ``None`` when the policy passes.
    """
    event_mapping = _mapping(event)
    pull_request = _mapping(event_mapping.get("pull_request"))
    if not pull_request:
        return None
    if _is_automation_exception(event_mapping, pull_request):
        return None
    if _has_local_closing_reference(event_mapping, references):
        return None
    return "pull request must close at least one issue in this repository"


def main(argv: Sequence[str] | None = None) -> int:
    """Check event and GraphQL JSON files supplied by CI."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:  # noqa: PLR2004
        usage = "usage: check_pr_metadata.py EVENT_JSON REFERENCES_JSON"
        raise SystemExit(usage)

    event = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    references = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
    if error := check_metadata(event, references):
        raise SystemExit(error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
