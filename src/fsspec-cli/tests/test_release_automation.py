"""Release Please contract for the two-package repository."""

from __future__ import annotations

import json
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CONFIG = _REPOSITORY_ROOT / "release-please-config.json"
_MANIFEST = _REPOSITORY_ROOT / ".release-please-manifest.json"
_RELEASE_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/release.yml"
_COMPONENT_RELEASE_WORKFLOW = (
    _REPOSITORY_ROOT / ".github/workflows/fsspec-cli-release.yml"
)
_PUBLISH_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/fsspec-cli-publish.yml"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_release_please_uses_one_manifest_and_config_for_both_packages() -> None:
    config = _json(_CONFIG)
    manifest = _json(_MANIFEST)

    assert config["separate-pull-requests"] is True
    assert set(config["packages"]) == {".", "src/fsspec-cli"}
    assert config["packages"]["src/fsspec-cli"] == {
        "package-name": "fsspec-cli",
        "changelog-path": "CHANGELOG.md",
        "release-type": "python",
        "path": "src/fsspec-cli",
        "exclude-paths": ["src/vosfs"],
        "bump-minor-pre-major": True,
        "bump-patch-for-minor-pre-major": True,
        "draft": True,
        "prerelease": False,
        "include-component-in-tag": True,
        "component": "fsspec-cli",
        "include-v-in-tag": True,
        "initial-version": "0.1.0",
        "extra-files": [
            "pyproject.toml",
            {
                "jsonpath": '$.package[?(@.name.value=="fsspec-cli")].version',
                "path": "/uv.lock",
                "type": "toml",
            },
        ],
    }
    assert manifest == {".": "0.3.3", "src/fsspec-cli": "0.0.0"}

    root_package = config["packages"]["."]
    assert "src/fsspec-cli/**" in root_package["exclude-paths"]
    assert "uv.lock" in root_package["exclude-paths"]
    assert {
        ".fsspec-cli-release-please-manifest.json",
        ".pre-commit-config.yaml",
        ".release-please-manifest.json",
        "docs/agents/release.md",
        "fsspec-cli-release-please-config.json",
        "release-please-config.json",
    } <= set(root_package["exclude-paths"])
    assert not (_REPOSITORY_ROOT / "fsspec-cli-release-please-config.json").exists()
    assert not (_REPOSITORY_ROOT / ".fsspec-cli-release-please-manifest.json").exists()


def test_one_release_please_action_dispatches_both_package_builds() -> None:
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("googleapis/release-please-action@") == 1
    assert "config-file: release-please-config.json" in workflow
    assert "manifest-file: .release-please-manifest.json" in workflow
    assert "steps.release.outputs.release_created" in workflow
    assert "-f event_type=release-build" in workflow
    assert "steps.release.outputs['src/fsspec-cli--release_created']" in workflow
    assert "steps.release.outputs['src/fsspec-cli--tag_name']" in workflow
    assert "steps.release.outputs['src/fsspec-cli--sha']" in workflow
    assert "-f event_type=fsspec-cli-release-build" in workflow
    assert not _COMPONENT_RELEASE_WORKFLOW.exists()


def test_component_publication_only_builds_fsspec_cli() -> None:
    workflow = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "types: [fsspec-cli-release-build]" in workflow
    assert "^fsspec-cli-v" in workflow
    assert "ref: ${{ github.event.client_payload.tag_name }}" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$RELEASE_SHA"' in workflow
    assert "uv build --package fsspec-cli --out-dir dist/fsspec-cli" in workflow
    assert "dist/fsspec-cli/*.whl" in workflow
    assert "dist/fsspec-cli/*.tar.gz" in workflow
    assert 'gh release edit "$RELEASE_TAG" --draft=false' in workflow

    assert "verify_release.py" not in workflow
    assert "--package vosfs" not in workflow
    assert "pypi" not in workflow.lower()
    assert "docs-publish" not in workflow
