# CANFAR release mechanism comparison

Research snapshot: 2026-07-14, `opencadc/canfar` `main` at
[`9f497a8`](https://github.com/opencadc/canfar/commit/9f497a849651d71371591c0351bdffcedbf07d7b).
The latest completed release examined is
[`v1.4.1`](https://github.com/opencadc/canfar/releases/tag/v1.4.1).

## Recommendation for `vosfs` v0.3.1

Adopt CANFAR's dispatch-based separation of release concerns, exact-tag
checkout for release builds, SHA-pinned actions, and direct Mike publication to
the `gh-pages` branch. Correct its docs source selection by checking out the
dispatched SHA or tag. CANFAR is not an immutable-release example: its `v1.4.1`
release is published, mutable, and has no attached assets according to the
[GitHub Releases API](https://api.github.com/repos/opencadc/canfar/releases/tags/v1.4.1).

For `vosfs`, the safe patch-release sequence is:

1. Release Please creates `v0.3.1` as a draft and creates the tag immediately.
2. Build the wheel and sdist from the tag's resolved commit and attach both
   files while the release is still a draft.
3. Publish the complete release. GitHub recommends precisely this ordering when
   release immutability is enabled because assets cannot be changed after
   publication ([GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)).
4. In the Release Please workflow, independently dispatch the validated `main`
   SHA for `dev` docs, each created tag for code publication, and the same tag
   for versioned docs.
5. Let Mike push `dev`, `vX.Y.Z`, and `latest` directly to `gh-pages`. Keep a
   docs-only `workflow_dispatch`, but provide no manual or automated recovery
   for a failed code publication; fix the cause and release the next patch.

## What CANFAR currently does

CANFAR runs Release Please on every push to `main`. The orchestrator then sends
repository-dispatch events: edge container and docs events always, and release
container plus PyPI events only when a release was created
([`cd.yml` lines 3-86](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/cd.yml#L3-L86)).
The dispatch steps enqueue independent runs; the orchestrator does not wait for
or aggregate their outcomes. The `v1.4.1` fan-out did succeed for
[the orchestrator](https://github.com/opencadc/canfar/actions/runs/27375629554),
[the container build](https://github.com/opencadc/canfar/actions/runs/27375656362),
[PyPI](https://github.com/opencadc/canfar/actions/runs/27375656932), and
[docs](https://github.com/opencadc/canfar/actions/runs/27375657036).

### Release orchestration

CANFAR uses Release Please followed by four `repository_dispatch` events.
`vosfs` adopts this separation: the orchestrator confirms dispatch, while each
downstream workflow is the source of truth for its own result.

### Draft and immutable releases

CANFAR's Release Please configuration has no `draft` or
`force-tag-creation` setting
([configuration](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/release-please-config.json#L1-L22));
downstream jobs never attach GitHub assets. `vosfs` should use `draft: true` and
`force-tag-creation: true`, attach the built assets, then publish. Release Please
documents immediate tag creation as important for draft releases
([manifest configuration](https://github.com/googleapis/release-please/blob/main/docs/manifest-releaser.md)).

### Release artifacts

The container workflow checks out the dispatched tag, builds and pushes
multi-architecture images with SBOM and provenance, and attests the digest
([`release.yml` lines 37-87](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/release.yml#L37-L87)).
The PyPI workflow separately runs `uv build` and immediately publishes
([`pypi.yml` lines 34-56](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/pypi.yml#L34-L56)).
`vosfs` should copy exact-tag checkout and build once from the resolved tag
commit before attaching the resulting distributions to the draft release.

### PyPI trusted publishing

CANFAR uses environment `pypi`, job-scoped `id-token: write`, no long-lived
PyPI token, and the PyPA publisher action, followed by attestations
([`pypi.yml` lines 13-21 and 50-71](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/pypi.yml#L13-L71)).
That matches PyPI's recommended Trusted Publisher shape
([PyPI documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/)).
Reuse it when PyPI publication enters `vosfs` scope; it is unnecessary for
v0.3.1 if the contract remains GitHub artifacts plus docs. Prefer a
non-cancelling, exact-tag recovery path for any future package publication.

### Documentation

CANFAR uses Mike and maintains release/`latest` versus `edge` versions
([`docs.yml` lines 38-50](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/docs.yml#L38-L50)).
However, checkout has no `ref`; the payload SHA and tag are only echoed or used
as the version label
([lines 7-10 and 29-34](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/docs.yml#L7-L34)).
There is no strict build or deployment concurrency. `vosfs` should check out
the dispatched SHA or tag, serialize `gh-pages` updates, keep its locked strict
build, and retain a docs-only `workflow_dispatch` for republishing an existing
tag.

### Permissions and concurrency

CANFAR defaults to read access, grants job-scoped writes, and pins third-party
actions to commit SHAs. PyPI alone has per-tag concurrency with
`cancel-in-progress: true`
([`pypi.yml` lines 7-21](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/pypi.yml#L7-L21)).
Release, docs, and container publication are not serialized. `vosfs` should
keep narrow permissions and SHA pinning, serialize code publication without
cancellation, and serialize Pages updates.

### Recovery

CANFAR's downstream workflows can be re-fired through the dispatch API, but
expose no typed manual input. `vosfs` keeps an exact `vX.Y.Z` manual input only
for relaxed docs publication. Code publication has no manual entry point and
never resumes a failed draft.

### Live integration

CANFAR conditionally mints `~/.ssl/cadcproxy.pem` and cleans it in an `always()`
step, but runs `pytest -m "not slow"` in both credential branches
([`ci.yml` lines 42-107](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/.github/workflows/ci.yml#L42-L107)).
Its current guidance says live tests must be both `slow` and `integration`,
while the non-slow suite is deterministic
([architecture notes](https://github.com/opencadc/canfar/blob/9f497a849651d71371591c0351bdffcedbf07d7b/docs/agents/architecture.md#L22-L26)).
The release flow therefore has no unambiguous live gate. Keep the `vosfs` gate
explicit: pass `VOSFS_CERT_FILE`, run only `-m integration`, disable the default
coverage threshold for that narrow suite, and always remove temporary
credentials. Record a local pass as v0.3.1 release evidence rather than copying
CANFAR's ambiguous secret-conditional command.

## Rough edges not to inherit

- `if: always()` dispatches edge docs/builds even when Release Please fails, so
  empty or stale outputs can reach downstream workflows.
- Downstream success is intentionally independent of the orchestrator; inspect
  the code-publication and docs runs for their respective outcomes.
- Docs may label default-branch content with a release tag because the tag is not
  checked out.
- Package distributions are built independently for PyPI and are neither
  compared with nor attached to the GitHub release.
- Only PyPI has concurrency control, and cancellation is a poor recovery model
  for a non-replaceable publication operation.
- There is no retry for a partially completed code release; `vosfs` explicitly
  fails closed and moves to the next patch.

The useful CANFAR lesson is modularity. `vosfs` keeps each boundary small:
validated commit, dispatched tag, draft artifacts, published release,
and exact-source docs on `gh-pages`.
