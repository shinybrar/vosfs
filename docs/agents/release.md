# Release automation

Release Please runs only after successful `CI` on the current `main` commit. It
authenticates with the `RELEASE_PLEASE_TOKEN` repository secret. This must be a
fine-grained personal access token owned by the repository owner, limited to
this repository, with Contents, Issues, and Pull Requests read/write
permissions.

Release Please opens its release pull request from
`release-please--branches--main--components--vosfs` with the
`autorelease: pending` label. It remains subject to CI, review, and squash-merge
gates.

The Release workflow follows the same dispatch model as `opencadc/canfar`:

- every successful `main` commit dispatches its validated SHA for `dev` docs;
- a Release Please tag dispatches the tag and commit to code publication; and
- an independent tag dispatch publishes versioned docs and moves `latest` to
  it.

Code publication is locked down and has no manual trigger. Release Please
creates a tagged draft. The publication workflow accepts only a first-attempt
dispatch containing an exact `vX.Y.Z` tag and full matching commit SHA, builds
the wheel and source distribution from that tag, attaches them, and publishes
the complete immutable GitHub Release. It does not publish to a package
registry. A failed code publication is not resumed or repaired by automation;
fix the cause and release the next patch version.

Documentation publication is intentionally relaxed. Mike pushes the validated
`main` commit to `dev` on the `gh-pages` branch. A Release Please tag publishes
that exact `vX.Y.Z` version, updates `latest`, and makes `latest` the default.
The Pages workflow also accepts an existing exact tag through
`workflow_dispatch` so documentation can be republished independently. GitHub
Pages serves the `gh-pages` branch directly; there is no Pages artifact or
deployment workflow.

Trusted `main` CI always runs the live OpenCADC integration job. Pull requests
skip it because credentials are unavailable to untrusted code; the `Required`
aggregate accepts that skip only for pull-request events. Release Please
therefore runs only after the exact `main` commit has passed the live gate.

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
