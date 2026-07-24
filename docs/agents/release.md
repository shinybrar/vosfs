# Release automation

One Release Please action manages both packages in this repository. The shared
`release-please-config.json` contains package entries for `.` (`vosfs`) and
`src/fsspec-cli`; `.release-please-manifest.json` records both released
versions. `separate-pull-requests: true` gives each package its own release pull
request and schedule.

Every push to `main` runs the `Release` workflow directly. It invokes Release
Please once with `RELEASE_PLEASE_TOKEN`, allowing generated release pull
requests to run normal CI and review. Root outputs such as `release_created`,
`tag_name`, and `sha` belong to `vosfs`. Path-prefixed outputs such as
`src/fsspec-cli--release_created`, `src/fsspec-cli--tag_name`, and
`src/fsspec-cli--sha` belong to `fsspec-cli`.

The root package excludes the component tree (`src/fsspec-cli`), all of
`docs/`, and the `.superpowers/` agent scratch directory from its commit
analysis. Component-only work and documentation changes therefore do not
propose a `vosfs` release. The component package is scoped to
`src/fsspec-cli`, so it already ignores everything outside that directory.
Never hand-edit versioned changelog entries or couple an `fsspec-cli` cut to a
`vosfs` version.

`vosfs` uses ordinary SemVer bumping. Before 1.0, `fsspec-cli` treats a
breaking change as a minor bump, so a breaking change from 0.4.x produces
0.5.0 instead of 1.0.0. Both packages use tagged draft GitHub Releases.
`force-tag-creation` ensures the exact tag exists for publication and previous
release discovery. The component package also uses a Release Please extra-file
update to keep its package metadata and the shared `uv.lock` entry at the same
version.

## Publication

When Release Please creates a component release, `Release` sends one
`package-release` repository event containing that package's allowlisted name,
exact tag, and full commit SHA. The single `Publish Package` workflow accepts
only `vosfs` and `fsspec-cli`. It verifies the package-specific tag form, full
SHA, tag-to-commit equality, and matching GitHub Release before building only
that package with `uv build --no-sources --package`.

The publisher requires exactly one wheel and one source distribution. Existing
assets may contain only those expected names. Draft and mutable releases replace
both assets with `--clobber`; the workflow verifies the final asset set exactly,
then publishes the release only if it is still a draft. A rerun of an
already-published immutable release
verifies the same two expected assets without attempting a forbidden mutation.
Rerunning the same workflow run is the only recovery path and remains safe after
a partial upload, publication, or failed documentation dispatch. A different
tag, SHA, package, or unexpected asset fails validation.

Every `main` push separately dispatches `dev` documentation for its exact
`github.sha`; release orchestration never coalesces these pushes. Pages isolates
each `dev` run by that SHA, so a late stale dispatch cannot cancel a newer run.
It requires the dispatched SHA to equal current `origin/main` both after
checkout and immediately before publication, making `dev` latest-wins.
Versioned documentation is causally downstream of successful `vosfs`
publication: only the completed `vosfs` publisher dispatches the exact
`vX.Y.Z` tag and SHA to Pages. Versioned runs are isolated per tag and never
cancelled. `fsspec-cli` publication never dispatches versioned documentation.
Pages accepts repository dispatches only; it has no manual release path.

Every generated release pull request remains subject to CI, review, and
squash-merge gates. Release Please owns `CHANGELOG.md` for `vosfs` and
`src/fsspec-cli/CHANGELOG.md` for the embedded command library from Conventional
Commit titles.
