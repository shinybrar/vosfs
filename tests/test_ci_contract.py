"""Static contract for deterministic, credential-free CI."""

from pathlib import Path

_ROOT = Path(__file__).parents[1]
_CI = _ROOT / ".github/workflows/ci.yml"


def test_ci_uses_full_supported_matrix_for_every_trigger() -> None:
    workflow = _CI.read_text()

    assert "  pull_request:\n" in workflow
    assert "  push:\n    branches: [main]\n" in workflow
    assert "  workflow_dispatch:\n" in workflow
    for entry in (
        '- { os: ubuntu-latest, python: "3.10" }',
        '- { os: ubuntu-latest, python: "3.11" }',
        '- { os: ubuntu-latest, python: "3.12" }',
        '- { os: ubuntu-latest, python: "3.13" }',
        '- { os: ubuntu-latest, python: "3.14" }',
        '- { os: macos-latest, python: "3.12" }',
    ):
        # The static matrix is inlined in both matrixed jobs.
        assert workflow.count(entry) == 2
    assert "EVENT_NAME" not in workflow


def test_ci_has_no_live_test_or_credential_wiring() -> None:
    workflow = _CI.read_text()

    assert not (_ROOT / ".github/workflows/fsspec-cli-live.yml").exists()
    for forbidden in (
        "live-integration",
        "CADC_USERNAME",
        "CADC_PASSWORD",
        "VOSFS_TEST_",
        "VOSFS_CERT_FILE",
        "cadcutils",
        "secrets.",
    ):
        assert forbidden not in workflow
