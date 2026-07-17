# Later `fsspec-cli` GitHub Release verification (#147)

<!-- pyml disable line-length -->

Status: **Baseline tags restored and locally verified; GitHub asset attach + later `0.2.0` cut still pending**

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

Implements: [Issue #147](https://github.com/shinybrar/vosfs/issues/147)

Catalog: [Issue #120 catalog](fsspec-cli-issue-120-catalog.md)

Claim audit: [claim/evidence audit](fsspec-cli-issue-120-claim-evidence-audit.md)

Automation contract: [docs/agents/release.md](../agents/release.md)

## 1. Merge gate reality check

| Gate | Reality at audit time |
| --- | --- |
| [#108](https://github.com/shinybrar/vosfs/issues/108) closed | **Yes** (closed 2026-07-17) |
| Release PRs merged | [#149](https://github.com/shinybrar/vosfs/pull/149) `0.1.0` @ `5d99483`; [#150](https://github.com/shinybrar/vosfs/pull/150) `0.1.1` @ `dfbdae6` |
| `fsspec-cli-v0.1.0` / `fsspec-cli-v0.1.1` **git tags** | **Were missing** on `origin` after draft creation; Publish jobs failed checking out the tag |
| Draft GitHub Releases | Existed as untagged drafts with **empty assets** |
| PyPI | **No publication** (correct; out of scope) |
| vosfs `vX.Y.Z` lineage | Untouched by this lane (correct) |

Prior feature tickets waived the missing tag for merges. **#147 requires real
tag verification** for the independent `fsspec-cli` release lane.

Root cause: Release Please created draft releases with
`force-tag-creation: true`, but the `fsspec-cli-v*` tags were not present for
checkout. Publish workflow
[29563240314](https://github.com/shinybrar/vosfs/actions/runs/29563240314) /
[29564313719](https://github.com/shinybrar/vosfs/actions/runs/29564313719)
failed at “Check out the released tag” with
`A branch or tag with the name 'fsspec-cli-v0.1.0' could not be found`.
Failed publishes are not resumed by the workflow; recovery is manual tag +
asset attach + undraft (or the next Release Please cut after tags exist).

## 2. Baseline tag recovery (#147)

### 2.1 Done

Annotated tags restored on `origin` at the exact release merge commits:

| Tag | Commit | Local build + isolated install |
| --- | --- | --- |
| `fsspec-cli-v0.1.0` | `5d9948312fb5b74fb2ed9ac35c47df48c282f39b` | `fsspec_cli-0.1.0-py3-none-any.whl` + sdist; site-packages import OK (Python 3.12 + declared deps) |
| `fsspec-cli-v0.1.1` | `dfbdae6a97dfe4694edd211e11d62ae8a3903be7` | `fsspec_cli-0.1.1-py3-none-any.whl` + sdist; site-packages import OK (Python 3.12 + declared deps) |

### 2.2 Pending (human / approved publish step)

Draft releases still have **empty assets** and remain drafts. Attach only
`fsspec-cli` wheel + sdist, then undraft. Do **not** upload vosfs artifacts.
Do **not** publish to PyPI. Failed Publish workflow runs must not be resumed;
use manual upload:

```bash
uv build --no-sources --package fsspec-cli --out-dir dist/fsspec-cli  # at the tag
gh release upload fsspec-cli-v0.1.0 dist/fsspec-cli/*.whl dist/fsspec-cli/*.tar.gz
gh release upload fsspec-cli-v0.1.1 dist/fsspec-cli/*.whl dist/fsspec-cli/*.tar.gz
gh release edit fsspec-cli-v0.1.0 --draft=false
gh release edit fsspec-cli-v0.1.1 --draft=false
```

## 3. Later release cut (`0.2.0` and beyond)

Release Please owns the version. Do **not** couple to vosfs. Do **not**
hand-edit versioned `CHANGELOG.md` entries.

| Item | Status |
| --- | --- |
| Open Release Please PR | [#152](https://github.com/shinybrar/vosfs/pull/152) `chore(main): release fsspec-cli 0.2.0` |
| PR completeness vs tip `2971c43` | **Stale / incomplete** relative to later #120 feats (`rm -f/-d/-v`, cross/multi `cp`, `mv`, `stat`, …). Refresh after #147 docs land; do not squash-merge a changelog that omits admitted surfaces |
| Required after merge | `fsspec-cli-v0.2.0` tag exists; Publish attaches wheel+sdist only; draft undrafted; PyPI untouched |
| Hermetic + installed-wheel gates | Must pass on the release commit before treating the cut as verified |
| Live evidence | Sanitized, source/profile scoped; never substitutes for hermetic proof |

## 4. Closing evidence checklist (for #147 / #120)

When the later release is actually published, the closing comment needs:

- Release PR URL and merge commit SHA
- `fsspec-cli-vX.Y.Z` tag and GitHub Release URL
- Wheel/sdist filenames and isolated installation run
- Canonical matrix commit plus qualifying CI/live links
- Explicit admitted profiles, preserved strict rejections, and unverified native rows
  (see catalog + claim audit)

## 5. Out of scope (unchanged)

PyPI publication, vosfs release/docs deployment, repository extraction, new
command behavior, backend hardening (#113), CANFAR/vostools migration, or
relaxing profiles to greenwash gates.
