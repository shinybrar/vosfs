# Release automation

Release Please runs only after successful `CI` on `main`, or when a maintainer
starts the release workflow manually. It authenticates with the
`RELEASE_PLEASE_TOKEN` repository secret. This must be a fine-grained personal
access token owned by the repository owner, limited to this repository, with
Contents, Issues, and Pull Requests read/write permissions.

Release pull requests are recognized only when the author is the repository
owner, the branch is `release-please--branches--main--components--vosfs`, and
the label is `autorelease: pending`. They remain subject to the normal title,
CI, review, and squash-merge gates. The workflow creates `vX.Y.Z` GitHub
Releases and attaches a wheel and source distribution built from the released
commit. It does not publish to a package registry. A manual run with an existing
exact tag rebuilds missing artifacts from that release; a manual run without a
tag retries Release Please.

Do not hand-format the generated root `CHANGELOG.md`. Release Please owns its
format from Commitizen-compatible Conventional Commit titles, and PyMarkdown
excludes it entirely. General whitespace and end-of-file hooks still apply.

## Lockfile fallback

The tagged-TOML rule in `release-please-config.json` is the proven path and must
remain the default. If a future Release Please parser stops matching the
editable vosfs entry, remove only that `extra-files` rule and add a trusted
post-action step that:

1. runs only for the verified release branch and repository-owner identity;
2. runs `uv lock` after Release Please updates `pyproject.toml`;
3. fails unless `uv.lock` is the only additional changed file; and
4. commits `uv.lock` conventionally with the same Release Please PAT.

The resulting `synchronize` event must rerun pull-request CI. Do not implement
this fallback with `pull_request_target` or weaken the exact-lock gate.
