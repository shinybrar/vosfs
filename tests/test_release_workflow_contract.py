"""Static contracts for single-path release and documentation publication."""

from pathlib import Path

_ROOT = Path(__file__).parents[1]
_WORKFLOWS = _ROOT / ".github/workflows"
_RELEASE = _WORKFLOWS / "release.yml"
_PUBLISH = _WORKFLOWS / "publish.yml"
_PAGES = _WORKFLOWS / "pages.yml"


def _step(workflow: str, name: str, next_name: str | None = None) -> str:
    """Return one named workflow step using stable step names as boundaries."""
    block = workflow.split(f"      - name: {name}\n", 1)[1]
    if next_name is not None:
        block = block.split(f"      - name: {next_name}\n", 1)[0]
    return block


def test_release_runs_once_on_each_main_push() -> None:
    workflow = _RELEASE.read_text()

    assert "on:\n  push:\n    branches: [main]\n" in workflow
    assert "workflow_run:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "Verify CI validated current main" not in workflow
    assert workflow.count("googleapis/release-please-action@") == 1
    assert "token: ${{ secrets.RELEASE_PLEASE_TOKEN }}" in workflow


def test_release_dispatches_exact_component_payloads_and_dev_docs() -> None:
    workflow = _RELEASE.read_text()
    vosfs = _step(
        workflow,
        "Dispatch vosfs package publication",
        "Dispatch fsspec-cli package publication",
    )
    fsspec_cli = _step(
        workflow,
        "Dispatch fsspec-cli package publication",
        "Dispatch dev documentation",
    )
    dev_docs = _step(workflow, "Dispatch dev documentation")

    assert vosfs.count("-f event_type=package-release") == 1
    assert '-f "client_payload[package]=vosfs"' in vosfs
    assert "${{ steps.release.outputs.tag_name }}" in vosfs
    assert "${{ steps.release.outputs.sha }}" in vosfs
    assert fsspec_cli.count("-f event_type=package-release") == 1
    assert '-f "client_payload[package]=fsspec-cli"' in fsspec_cli
    assert "${{ steps.release.outputs['src/fsspec-cli--tag_name'] }}" in fsspec_cli
    assert "${{ steps.release.outputs['src/fsspec-cli--sha'] }}" in fsspec_cli
    assert "if: always()" in dev_docs
    assert '-f "client_payload[target]=dev"' in dev_docs
    assert '-f "client_payload[sha]=${{ github.sha }}"' in dev_docs
    assert "client_payload[target]=release" not in workflow


def test_one_publisher_validates_package_tag_sha_and_release_identity() -> None:
    workflow = _PUBLISH.read_text()

    assert not (_WORKFLOWS / "fsspec-cli-publish.yml").exists()
    assert "types: [package-release]" in workflow
    assert 'case "$PACKAGE" in' in workflow
    assert "vosfs) expected_tag='^v" in workflow
    assert "fsspec-cli) expected_tag='^fsspec-cli-v" in workflow
    assert "unknown package: $PACKAGE" in workflow
    assert '[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]' in workflow
    assert "releases/tags/$RELEASE_TAG" in workflow
    assert ".tag_name == $tag" in workflow
    assert '(.immutable | type == "boolean")' in workflow
    assert 'git rev-parse "$RELEASE_TAG^{commit}"' in workflow
    assert 'test "$TAG_SHA" = "$RELEASE_SHA"' in workflow


def test_publisher_builds_and_replaces_exactly_two_assets_on_rerun() -> None:
    workflow = _PUBLISH.read_text()

    assert workflow.count('uv build --no-sources --package "$PACKAGE"') == 1
    assert "GITHUB_RUN_ATTEMPT" not in workflow
    assert '[[ "${#DISTRIBUTIONS[@]}" -ne 2 ]]' in workflow
    assert '[[ "$WHEEL_COUNT" -eq 1 && "$SDIST_COUNT" -eq 1 ]]' in workflow
    assert "unexpected release asset" in workflow
    assert (
        'gh release upload "$RELEASE_TAG" "${DISTRIBUTIONS[@]}" --clobber' in workflow
    )
    assert '[[ "${#FINAL_ASSETS[@]}" -ne 2 ]]' in workflow
    assert 'gh release edit "$RELEASE_TAG" --draft=false' in workflow
    assert workflow.index("unexpected release asset") < workflow.index("--clobber")
    assert workflow.index("--clobber") < workflow.index("FINAL_ASSETS")
    assert workflow.index("FINAL_ASSETS") < workflow.index("--draft=false")


def test_published_immutable_rerun_verifies_without_mutation() -> None:
    workflow = _PUBLISH.read_text()
    guard = 'if [[ "$IS_DRAFT" == "false" && "$IS_IMMUTABLE" == "true" ]]; then'
    immutable_branch, mutable_branch = workflow.split(guard, 1)[1].split("else", 1)

    assert "IS_DRAFT: ${{ steps.release.outputs.is_draft }}" in workflow
    assert "IS_IMMUTABLE: ${{ steps.release.outputs.is_immutable }}" in workflow
    assert workflow.index("unexpected release asset") < workflow.index(guard)
    assert "verifying assets without mutation" in immutable_branch
    assert "--clobber" not in immutable_branch
    assert "--clobber" in mutable_branch
    assert workflow.index(guard) < workflow.index("FINAL_ASSETS")
    assert '[[ "${#FINAL_ASSETS[@]}" -ne 2 ]]' in workflow
    assert 'test "${FINAL_ASSETS[0]}" = "${EXPECTED_ASSETS[0]}"' in workflow
    assert 'test "${FINAL_ASSETS[1]}" = "${EXPECTED_ASSETS[1]}"' in workflow
    assert 'if [[ "$IS_DRAFT" == "true" ]]; then' in workflow
    assert workflow.index("FINAL_ASSETS") < workflow.index(
        "Dispatch published vosfs documentation"
    )


def test_release_docs_follow_only_successful_vosfs_publication() -> None:
    workflow = _PUBLISH.read_text()
    docs = _step(workflow, "Dispatch published vosfs documentation")

    assert "if: env.PACKAGE == 'vosfs'" in docs
    assert "-f event_type=docs-publish" in docs
    assert '-f "client_payload[target]=release"' in docs
    assert '-f "client_payload[tag_name]=$RELEASE_TAG"' in docs
    assert '-f "client_payload[sha]=$RELEASE_SHA"' in docs
    assert workflow.index("--draft=false") < workflow.index(
        "Dispatch published vosfs documentation"
    )


def test_pages_accepts_only_causal_repository_dispatch_payloads() -> None:
    workflow = _PAGES.read_text()

    assert "types: [docs-publish]" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "inputs." not in workflow
    assert "EVENT_NAME" not in workflow
    assert 'case "$TARGET" in' in workflow
    assert "dev)" in workflow
    assert "release)" in workflow
    assert workflow.count('[[ "$VALIDATED_SHA" =~ ^[0-9a-f]{40}$ ]]') == 2
    assert '[[ -z "$RELEASE_TAG" ]]' in workflow
    assert "release tag must be an exact vX.Y.Z tag" in workflow
    assert 'git merge-base --is-ancestor "$VALIDATED_SHA" origin/main' in workflow
    assert "releases/tags/$RELEASE_TAG" in workflow
    assert ".draft == false" in workflow
    assert 'git rev-parse "$RELEASE_TAG^{commit}"' in workflow


def test_completion_workflows_and_docs_have_no_package_registry_lane() -> None:
    paths = [*_WORKFLOWS.glob("*.yml"), _ROOT / "docs/agents/release.md"]

    for path in paths:
        assert "pypi" not in path.read_text().lower(), path
