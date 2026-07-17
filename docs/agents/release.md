# Release automation

`vosfs` and the embedded `fsspec-cli` command library have independent Release
Please state, pull requests, tags, artifacts, and publication workflows. Both
authenticate with the `RELEASE_PLEASE_TOKEN` repository secret. This must be a
fine-grained personal access token owned by the repository owner, limited to
this repository, with Contents, Issues, and Pull Requests read/write
permissions.

## vosfs route

The existing `Release` workflow runs only after successful `CI` on the current
`main` commit. It uses `release-please-config.json` and
`.release-please-manifest.json`, opens its pull request from
`release-please--branches--main--components--vosfs`, and keeps the existing
`vX.Y.Z` tag lineage.

The workflow follows the same dispatch model as `opencadc/canfar`:

- every successful `main` commit dispatches its validated SHA for `dev` docs;
- a `vosfs` Release Please tag dispatches the tag and commit to code
  publication; and
- an independent tag dispatch publishes versioned docs and moves `latest` to
  it.

The root release configuration excludes paths owned only by `fsspec-cli`.
CLI-only commits therefore neither bump nor refresh a `vosfs` release pull
request.

## fsspec-cli route

The `fsspec-cli Release` workflow runs only after the trusted read-only
`fsspec-cli live OpenCADC` workflow passes. Before invoking Release Please, it
downloads that run's sanitized evidence and verifies all of the following:

1. the live classification is `pass` for `vosfs / native async` plain `ls`;
2. the evidence names a successful `CI` run for the same full commit SHA;
3. the live run, CI run, current `main`, and evidence all name that SHA; and
4. the CI aggregate has therefore completed the hermetic, installed-wheel,
   quality, and governance gates.

It then uses `fsspec-cli-release-please-config.json` and
`.fsspec-cli-release-please-manifest.json`. The component manifest deliberately
starts at `0.0.0`, which is unreleased state rather than a claim that `0.1.0`
already exists. Release Please's Python strategy and the component's releasable
commits produce the first dedicated `0.1.0` release pull request and the
`fsspec-cli-v0.1.0` tag after merge. Its branch is
`release-please--branches--main--components--fsspec-cli`; its component-only
lifecycle labels keep an untagged release in either route from blocking the
other.

The component configuration uses Release Please's repository-root extra-file
path `/uv.lock`, so the same component release pull request updates the shared
editable `fsspec-cli` lock entry. It does not change the root `vosfs` version or
changelog.

When Release Please creates a tagged draft, the component workflow dispatches
only `fsspec-cli-release-build`. `Publish fsspec-cli` accepts only a first
attempt containing an exact `fsspec-cli-vX.Y.Z` tag and full matching commit
SHA. It checks out that tag, validates the draft and commit, builds only the
workspace member, and rejects the output directory unless it contains exactly
one correctly versioned `fsspec-cli` wheel and one source distribution with
matching archive metadata. It attaches those two files and publishes the
complete immutable GitHub Release.

The component route has no PyPI action, console executable, `vosfs` artifact,
root `vX.Y.Z` tag, or documentation dispatch. A failed publication is not
resumed; fix the cause and release the next patch.

## Shared release rules

Every generated release pull request remains subject to CI, review, and
squash-merge gates. A release tag is created only after the merged candidate's
exact `main` commit passes its required gates. A failing or `unverified`
component live result blocks only `fsspec-cli`; it does not block `vosfs` or
development documentation.

Root code publication is also locked down and has no manual trigger. It accepts
only an exact `vX.Y.Z` tag and full matching commit SHA, builds the root wheel
and source distribution from that tag, attaches them to the Release Please
draft, and publishes the immutable GitHub Release. It does not publish to a
package registry.

Documentation publication remains independent. Mike pushes the validated
`main` commit to `dev` on `gh-pages`. A root `vosfs` tag publishes that exact
version, updates `latest`, and makes `latest` the default. The Pages workflow
also accepts an existing exact root tag through `workflow_dispatch` so docs can
be republished without changing either code-release route.

The one-time unreleased `0.0.0` bootstrap marker is the only manual content in
the command-library changelog. Otherwise, do not hand-format either generated
changelog. Release Please owns
`CHANGELOG.md` for `vosfs` and `src/fsspec-cli/CHANGELOG.md` for the command
library from Commitizen-compatible Conventional Commit titles. PyMarkdown
excludes only the generated root changelog; all general whitespace and
end-of-file hooks still apply.

## Lockfile fallback

The tagged-TOML rules in both Release Please configurations are the default.
If a future Release Please parser stops matching one editable workspace entry,
remove only that component's `extra-files` rule and add a trusted post-action
step that:

1. runs only for the verified release branch and repository-owner identity;
2. runs `uv lock` after Release Please updates the applicable `pyproject.toml`;
3. fails unless `uv.lock` is the only additional changed file; and
4. commits `uv.lock` conventionally with the same Release Please PAT.

The resulting `synchronize` event must rerun pull-request CI. Do not implement
this fallback with `pull_request_target` or weaken the exact-lock gate.
