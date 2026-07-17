"""Release automation contract for the independent fsspec-cli component."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_COMPONENT_CONFIG = _REPOSITORY_ROOT / "fsspec-cli-release-please-config.json"
_COMPONENT_MANIFEST = _REPOSITORY_ROOT / ".fsspec-cli-release-please-manifest.json"
_COMPONENT_CHANGELOG = _REPOSITORY_ROOT / "src/fsspec-cli/CHANGELOG.md"
_ROOT_CONFIG = _REPOSITORY_ROOT / "release-please-config.json"
_RELEASE_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/fsspec-cli-release.yml"
_PUBLISH_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/fsspec-cli-publish.yml"
_RELEASE_VALIDATOR = _REPOSITORY_ROOT / "src/fsspec-cli/tools/verify_release.py"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_validator(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_RELEASE_VALIDATOR), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_cli_distributions(
    directory: Path,
    version: str = "0.1.0",
    metadata_name: str = "fsspec-cli",
) -> None:
    directory.mkdir()
    (directory / ".gitignore").write_text("*\n", encoding="utf-8")
    metadata = (
        f"Metadata-Version: 2.4\nName: {metadata_name}\nVersion: {version}\n"
    ).encode()

    wheel = directory / f"fsspec_cli-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr(f"fsspec_cli-{version}.dist-info/METADATA", metadata)

    source = directory / f"fsspec_cli-{version}.tar.gz"
    with tarfile.open(source, mode="w:gz") as archive:
        info = tarfile.TarInfo(f"fsspec_cli-{version}/PKG-INFO")
        info.size = len(metadata)
        archive.addfile(info, io.BytesIO(metadata))


def test_release_please_tracks_an_unreleased_independent_component() -> None:
    config = _json(_COMPONENT_CONFIG)
    manifest = _json(_COMPONENT_MANIFEST)
    root_config = _json(_ROOT_CONFIG)

    assert config["bootstrap-sha"] == "fc6936716b580c071b8a57c1a81792ba041de43b"
    assert config["label"] == "autorelease: fsspec-cli pending"
    assert config["release-label"] == "autorelease: fsspec-cli tagged"
    assert config["separate-pull-requests"] is True
    assert config["packages"] == {
        "src/fsspec-cli": {
            "release-type": "python",
            "package-name": "fsspec-cli",
            "component": "fsspec-cli",
            "versioning": "default",
            "bump-minor-pre-major": False,
            "bump-patch-for-minor-pre-major": False,
            "draft": True,
            "force-tag-creation": True,
            "include-component-in-tag": True,
            "include-v-in-tag": True,
            "pull-request-title-pattern": "chore: release ${component} ${version}",
            "extra-files": [
                {
                    "type": "toml",
                    "path": "/uv.lock",
                    "jsonpath": '$.package[?(@.name.value=="fsspec-cli")].version',
                }
            ],
        }
    }
    assert manifest == {"src/fsspec-cli": "0.0.0"}
    changelog = _COMPONENT_CHANGELOG.read_text(encoding="utf-8")
    assert "## 0.0.0 (unreleased bootstrap)" in changelog
    assert "## 0.1.0" not in changelog

    root_package = root_config["packages"]["."]
    assert "src/fsspec-cli/**" in root_package["exclude-paths"]
    assert ".github/workflows/fsspec-cli-*.yml" in root_package["exclude-paths"]
    assert "docs/design/fsspec-cli-*.md" in root_package["exclude-paths"]
    assert "docs/research/fsspec-cli-*.md" in root_package["exclude-paths"]


def test_component_release_waits_for_exact_ci_and_live_evidence() -> None:
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    triggers = workflow.split("permissions:", maxsplit=1)[0]

    assert workflow.startswith("name: fsspec-cli Release\n")
    assert "  workflow_run:\n    workflows: [fsspec-cli live OpenCADC]\n" in triggers
    assert "    types: [completed]\n    branches: [main]\n" in triggers
    for unrelated_trigger in (
        "pull_request:",
        "pull_request_target:",
        "push:",
        "release:",
        "repository_dispatch:",
        "workflow_dispatch:",
    ):
        assert unrelated_trigger not in triggers

    assert "permissions:\n  actions: read\n  contents: read\n" in workflow
    required_fragments = (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.head_branch == 'main'",
        "LIVE_RUN_ID: ${{ github.event.workflow_run.id }}",
        "VALIDATED_SHA: ${{ github.event.workflow_run.head_sha }}",
        "repos/$GITHUB_REPOSITORY/git/ref/heads/main",
        'gh run download "$LIVE_RUN_ID"',
        "fsspec-cli-live-evidence-$VALIDATED_SHA",
        '.classification == "pass"',
        ".commit == $validated_sha",
        'CI_RUN_ID="$(jq -r .ci_run_id',
        "repos/$GITHUB_REPOSITORY/actions/runs/$CI_RUN_ID",
        '.name == "CI"',
        '.conclusion == "success"',
        '.head_branch == "main"',
        ".head_sha == $validated_sha",
        "config-file: fsspec-cli-release-please-config.json",
        "manifest-file: .fsspec-cli-release-please-manifest.json",
        "steps.release.outputs['src/fsspec-cli--release_created']",
        "steps.release.outputs['src/fsspec-cli--tag_name']",
        "steps.release.outputs['src/fsspec-cli--sha']",
        "-f event_type=fsspec-cli-release-build",
    )
    for fragment in required_fragments:
        assert fragment in workflow

    assert "event_type=release-build" not in workflow
    assert "event_type=docs-publish" not in workflow


def test_release_validator_accepts_an_exact_draft_dispatch() -> None:
    sha = "a" * 40

    result = _run_validator(
        "dispatch",
        "--tag",
        "fsspec-cli-v0.1.0",
        "--release-sha",
        sha,
        "--checkout-sha",
        sha,
        "--is-draft",
        "true",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0.1.0\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("tag", "release_sha", "checkout_sha", "is_draft", "diagnostic"),
    [
        (
            "v0.1.0",
            "a" * 40,
            "a" * 40,
            "true",
            "release tag must be an exact fsspec-cli-vX.Y.Z tag",
        ),
        (
            "fsspec-cli-v0.1.0",
            "a" * 39,
            "a" * 39,
            "true",
            "release dispatch requires a full commit SHA",
        ),
        (
            "fsspec-cli-v0.1.0",
            "a" * 40,
            "b" * 40,
            "true",
            "released tag does not match the dispatched commit",
        ),
        (
            "fsspec-cli-v0.1.0",
            "a" * 40,
            "a" * 40,
            "false",
            "release must remain draft until artifacts are attached",
        ),
    ],
)
def test_release_validator_rejects_an_unsafe_dispatch(
    tag: str,
    release_sha: str,
    checkout_sha: str,
    is_draft: str,
    diagnostic: str,
) -> None:
    result = _run_validator(
        "dispatch",
        "--tag",
        tag,
        "--release-sha",
        release_sha,
        "--checkout-sha",
        checkout_sha,
        "--is-draft",
        is_draft,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert diagnostic in result.stderr


def test_release_validator_accepts_the_cli_wheel_and_source_distribution(
    tmp_path: Path,
) -> None:
    distribution_directory = tmp_path / "dist"
    _write_cli_distributions(distribution_directory)

    result = _run_validator(
        "artifacts",
        "--tag",
        "fsspec-cli-v0.1.0",
        "--dist-dir",
        str(distribution_directory),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "fsspec_cli-0.1.0-py3-none-any.whl",
        "fsspec_cli-0.1.0.tar.gz",
    ]
    assert result.stderr == ""


def test_release_validator_rejects_foreign_distribution_metadata(
    tmp_path: Path,
) -> None:
    distribution_directory = tmp_path / "dist"
    _write_cli_distributions(distribution_directory, metadata_name="vosfs")

    result = _run_validator(
        "artifacts",
        "--tag",
        "fsspec-cli-v0.1.0",
        "--dist-dir",
        str(distribution_directory),
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "wheel metadata must identify fsspec-cli 0.1.0" in result.stderr


def test_release_validator_rejects_cross_component_artifacts(tmp_path: Path) -> None:
    distribution_directory = tmp_path / "dist"
    _write_cli_distributions(distribution_directory)
    (distribution_directory / "vosfs-0.3.3-py3-none-any.whl").write_bytes(b"foreign")

    result = _run_validator(
        "artifacts",
        "--tag",
        "fsspec-cli-v0.1.0",
        "--dist-dir",
        str(distribution_directory),
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "exactly one fsspec-cli wheel and one sdist" in result.stderr


def test_publication_builds_and_attaches_only_the_component_distributions() -> None:
    workflow = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    triggers = workflow.split("permissions:", maxsplit=1)[0]

    assert workflow.startswith("name: Publish fsspec-cli\n")
    assert "  repository_dispatch:\n    types: [fsspec-cli-release-build]\n" in triggers
    for unrelated_trigger in (
        "pull_request:",
        "pull_request_target:",
        "push:",
        "release:",
        "workflow_dispatch:",
    ):
        assert unrelated_trigger not in triggers

    assert "permissions:\n  contents: read\n" in workflow
    required_fragments = (
        '"$GITHUB_RUN_ATTEMPT" != "1"',
        "^fsspec-cli-v(0|[1-9][0-9]*)",
        "^([0-9a-f]{40})$",
        'gh release view "$RELEASE_TAG" --json isDraft --jq .isDraft',
        "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "persist-credentials: false",
        "ref: ${{ github.event.client_payload.tag_name }}",
        "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990",
        "src/fsspec-cli/tools/verify_release.py dispatch",
        "src/fsspec-cli/tools/verify_release.py artifacts",
        "uv build --package fsspec-cli --out-dir dist/fsspec-cli",
        'gh release upload "$RELEASE_TAG"',
        "dist/fsspec-cli/*.whl",
        "dist/fsspec-cli/*.tar.gz",
        'gh release edit "$RELEASE_TAG" --draft=false',
    )
    for fragment in required_fragments:
        assert fragment in workflow

    assert "uv build\n" not in workflow
    assert "--package vosfs" not in workflow
    assert "pypi" not in workflow.lower()
    assert "docs-publish" not in workflow
    assert workflow.index("Validate release dispatch") < workflow.index(
        "Check out the released tag"
    )
    assert workflow.index("Verify released commit and draft") < workflow.index(
        "Build fsspec-cli distributions"
    )
    assert workflow.index("Verify fsspec-cli distributions") < workflow.index(
        "Attach fsspec-cli distributions"
    )
    assert workflow.index("Attach fsspec-cli distributions") < workflow.index(
        "Publish the complete fsspec-cli release"
    )
