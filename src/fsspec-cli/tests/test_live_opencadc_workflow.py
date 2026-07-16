"""Static trust-boundary checks for the live OpenCADC workflow."""

from __future__ import annotations

import re
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/fsspec-cli-live.yml"
_FULL_ACTION_PIN = re.compile(r"^\s*uses: [^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$")


def _workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_live_workflow_has_only_trusted_main_ci_and_manual_triggers() -> None:
    workflow = _workflow_text()
    triggers = workflow.split("permissions:", maxsplit=1)[0]

    assert workflow.startswith("name: fsspec-cli live OpenCADC\n")
    assert "  workflow_run:\n    workflows: [CI]\n" in triggers
    assert "    types: [completed]\n    branches: [main]\n" in triggers
    assert "  workflow_dispatch:\n    inputs:\n      ci_run_id:\n" in triggers
    assert "        required: true\n" in triggers
    assert "        type: string\n" in triggers
    for untrusted_or_coupled_trigger in (
        "pull_request:",
        "pull_request_target:",
        "push:",
        "release:",
        "repository_dispatch:",
        "schedule:",
    ):
        assert untrusted_or_coupled_trigger not in triggers

    assert "permissions:\n  actions: read\n  contents: read\n" in workflow
    assert "write" not in workflow


def test_live_workflow_selects_one_current_successful_ci_commit() -> None:
    workflow = _workflow_text()

    assert "repos/$GITHUB_REPOSITORY/actions/runs/$CI_RUN_ID" in workflow
    assert "repos/$GITHUB_REPOSITORY/git/ref/heads/main" in workflow
    for required_run_field in (
        '.name == "CI"',
        '.conclusion == "success"',
        '.head_branch == "main"',
    ):
        assert required_run_field in workflow
    assert '"$HEAD_SHA" == "$MAIN_SHA"' in workflow
    assert "ref: ${{ steps.selection.outputs.commit }}" in workflow
    assert '"$(git rev-parse HEAD)" == "$SELECTED_COMMIT"' in workflow
    assert '"$GITHUB_REF" == "refs/heads/main"' in workflow
    assert 'TEMP_SELECTED_EVIDENCE="$RUNNER_TEMP/' in workflow
    assert '"$EVIDENCE_FILE" > "$TEMP_SELECTED_EVIDENCE"' in workflow
    assert 'mv "$TEMP_SELECTED_EVIDENCE" "$SELECTED_EVIDENCE"' in workflow
    assert '"$EVIDENCE_FILE" > "$SELECTED_EVIDENCE"' not in workflow


def test_live_workflow_runs_only_exact_wheels_and_routes_sanitized_evidence() -> None:
    workflow = _workflow_text()

    required_fragments = (
        "uv build --package fsspec-cli --wheel",
        "uv build --package vosfs --wheel",
        "uv export --frozen --all-packages",
        "--no-dev --no-emit-workspace --no-header --no-annotate",
        "uv export --frozen --only-group live",
        'uv venv "$LIVE_ENVIRONMENT"',
        'uv pip install --python "$LIVE_PYTHON"',
        "--require-hashes",
        "--no-deps",
        'env -u PYTHONPATH "$LIVE_PYTHON" -I',
        "src/fsspec-cli/tests/_live_opencadc_gate.py",
        "FSSPEC_CLI_LIVE_DIRECTORY",
        "vars.VOSFS_TEST_ENDPOINT || 'https://staging.canfar.net/arc'",
        "vars.FSSPEC_CLI_LIVE_SERVICE_ENVIRONMENT || 'OpenCADC staging'",
        "vars.VOSFS_CADC_CREDENTIAL_HOST || 'ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca'",
        "secrets.CADC_USERNAME",
        "secrets.CADC_PASSWORD",
        "chmod 600",
        "--days-valid 1",
        "if: always()",
        "retention-days: 90",
        "fsspec-cli-live-evidence-${{ steps.selection.outputs.commit }}",
    )
    for fragment in required_fragments:
        assert fragment in workflow

    assert workflow.count("secrets.CADC_USERNAME") == 1
    assert workflow.count("secrets.CADC_PASSWORD") == 1
    assert "uv tool run" not in workflow
    assert "cadc_get_cert=$CREDENTIAL_ENVIRONMENT/bin/cadc-get-cert" in workflow
    assert '--host "$CADC_HOST"' in workflow
    assert "FSSPEC_CLI_LIVE_LOCK_IDENTITY" in workflow
    assert '--argjson gate_status "$GATE_STATUS"' in workflow
    assert "OBSERVATION_OUTCOME: ${{ steps.observation.outcome }}" in workflow
    assert '[[ "$OBSERVATION_OUTCOME" == "success" ]]' in workflow
    assert "persist-credentials: false" in workflow
    assert workflow.index("Initialize sanitized fallback evidence") < workflow.index(
        "Check out selected CI commit"
    )
    assert workflow.index("Run one sanitized read-only plain ls") < workflow.index(
        "Upload sanitized live evidence"
    )
    assert workflow.index("Upload sanitized live evidence") < workflow.index(
        "Enforce live classification"
    )


def test_credential_helper_is_a_locked_non_runtime_group() -> None:
    pyproject = (_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lockfile = (_REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert 'live = [\n    "cadcutils' in pyproject
    assert 'name = "cadcutils"' in lockfile


def test_actions_are_pinned_and_live_gate_is_not_coupled() -> None:
    workflow = _workflow_text()
    uses_lines = [line for line in workflow.splitlines() if "uses:" in line]

    assert uses_lines
    assert all(_FULL_ACTION_PIN.fullmatch(line) for line in uses_lines)
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in workflow
    assert "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990" in workflow
    assert (
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    )

    for workflow_name in ("ci.yml", "pages.yml", "publish.yml", "release.yml"):
        coupled_workflow = (
            _REPOSITORY_ROOT / ".github/workflows" / workflow_name
        ).read_text(encoding="utf-8")
        assert "fsspec-cli-live" not in coupled_workflow
        assert "fsspec-cli live OpenCADC" not in coupled_workflow
