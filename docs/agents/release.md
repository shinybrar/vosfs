# Release automation

One Release Please action manages both packages in this repository. The shared
`release-please-config.json` contains package entries for `.` (`vosfs`) and
`src/fsspec-cli`; `.release-please-manifest.json` records both released
versions. `separate-pull-requests: true` gives each package its own release pull
request and schedule.

The `Release` workflow runs after successful `CI` on the current `main` commit.
It invokes Release Please once with the shared config and manifest. Root outputs
such as `release_created`, `tag_name`, and `sha` belong to `vosfs`. The
path-prefixed outputs `src/fsspec-cli--release_created`,
`src/fsspec-cli--tag_name`, and `src/fsspec-cli--sha` belong to `fsspec-cli`.

The root package excludes `fsspec-cli`-owned paths and the shared `uv.lock` from
its commit analysis. The component package owns its version, changelog, and
`fsspec-cli-vX.Y.Z` tag lineage. Its `initial-version` is `0.1.0`, while the
manifest remains at the unreleased bootstrap value `0.0.0` until Release Please
creates the first release.

## Publication

Release Please creates a tagged draft for the package whose release pull
request was merged. The single `Release` workflow dispatches the matching
package build:

- `release-build` builds and publishes the `vosfs` wheel and source
  distribution from an exact `vX.Y.Z` tag;
- `fsspec-cli-release-build` builds and publishes only the `fsspec-cli` wheel
  and source distribution from an exact `fsspec-cli-vX.Y.Z` tag.

Neither route publishes to a package registry. Only a `vosfs` release dispatches
versioned documentation; `fsspec-cli` releases do not affect documentation.

The component package uses Release Please extra-file updates for the shared
root `uv.lock`. Its release pull request therefore keeps package metadata and
the workspace lock entry at the same version without coupling the two package
schedules.

Every generated release pull request remains subject to CI, review, and
squash-merge gates. Do not hand-format generated changelog entries. Release
Please owns `CHANGELOG.md` for `vosfs` and `src/fsspec-cli/CHANGELOG.md` for the
command library from Conventional Commit titles; the component's one-time
unreleased `0.0.0` bootstrap marker is the sole manual exception.
