# Later `fsspec-cli` GitHub Release verification (#147)

<!-- pyml disable line-length -->

Status: **Baseline `0.1.0` / `0.1.1` published with wheel+sdist; later `0.2.0`
published and verified**

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

Implements: [Issue #147](https://github.com/shinybrar/vosfs/issues/147)

Catalog: [Issue #120 catalog](fsspec-cli-issue-120-catalog.md)

Claim audit: [claim/evidence audit](fsspec-cli-issue-120-claim-evidence-audit.md)

Automation contract: [docs/agents/release.md](../agents/release.md)

## 1. Merge gate reality check

| Gate | Reality |
| --- | --- |
| [#108](https://github.com/shinybrar/vosfs/issues/108) closed | **Yes** (closed 2026-07-17) |
| Release PRs merged | [#149](https://github.com/shinybrar/vosfs/pull/149) `0.1.0` @ `5d99483`; [#150](https://github.com/shinybrar/vosfs/pull/150) `0.1.1` @ `dfbdae6` |
| `fsspec-cli-v0.1.0` / `fsspec-cli-v0.1.1` **git tags** | Present on `origin` at those commits |
| GitHub Releases | **Published** (not draft) with `fsspec-cli` wheel + sdist only |
| PyPI | **No publication** (correct; out of scope) |
| vosfs `vX.Y.Z` lineage | Untouched by this lane (correct) |

Prior feature tickets waived the missing tag for merges. **#147 requires real
tag + asset verification** for the independent `fsspec-cli` release lane.

Root cause of the empty drafts: Release Please created draft releases with
`force-tag-creation: true`, but the `fsspec-cli-v*` tags were absent at publish
time. Publish workflow
[29563240314](https://github.com/shinybrar/vosfs/actions/runs/29563240314) /
[29564313719](https://github.com/shinybrar/vosfs/actions/runs/29564313719)
failed at “Check out the released tag”. Failed publishes are not resumed by the
workflow; recovery is manual tag + asset attach + undraft.

## 2. Baseline recovery — done (#147)

Annotated tags on `origin` at the exact release merge commits; distributions
built from those commits; assets attached; drafts undrafted:

| Tag | Commit | GitHub Release assets | Isolated install |
| --- | --- | --- | --- |
| `fsspec-cli-v0.1.0` | `5d9948312fb5b74fb2ed9ac35c47df48c282f39b` | `fsspec_cli-0.1.0-py3-none-any.whl`, `fsspec_cli-0.1.0.tar.gz` | Python 3.12 venv; site-packages import OK |
| `fsspec-cli-v0.1.1` | `dfbdae6a97dfe4694edd211e11d62ae8a3903be7` | `fsspec_cli-0.1.1-py3-none-any.whl`, `fsspec_cli-0.1.1.tar.gz` | Python 3.12 venv; site-packages import OK |

Release URLs:

- [fsspec-cli-v0.1.0](https://github.com/shinybrar/vosfs/releases/tag/fsspec-cli-v0.1.0)
- [fsspec-cli-v0.1.1](https://github.com/shinybrar/vosfs/releases/tag/fsspec-cli-v0.1.1)

Commands used (after tags existed):

```bash
uv build --no-sources --package fsspec-cli --out-dir dist/fsspec-cli  # at each tag
gh release upload fsspec-cli-v0.1.0 dist/fsspec-cli/*.whl dist/fsspec-cli/*.tar.gz
gh release upload fsspec-cli-v0.1.1 dist/fsspec-cli/*.whl dist/fsspec-cli/*.tar.gz
gh release edit fsspec-cli-v0.1.0 --draft=false
gh release edit fsspec-cli-v0.1.1 --draft=false
```

No vosfs artifacts uploaded. No PyPI publish. Failed Publish workflow runs not
resumed.

Side effect note: undrafting `fsspec-cli` releases can flip GitHub’s repo-wide
“Latest” badge. Restored with `gh release edit v0.4.0 --latest` after the `0.2.0`
cut; that does not change package lineage.

## 3. Later release cut (`0.2.0`) — done

Release Please owns the version. Do **not** couple to vosfs. Do **not**
hand-edit versioned `CHANGELOG.md` entries.

Historical unblock: [#183](https://github.com/shinybrar/vosfs/pull/183) fixed
Python 3.10 `CancelledError` identity asserts that had kept `main` CI red, so
Release Please could refresh [#152](https://github.com/shinybrar/vosfs/pull/152)
after green CI on tip
[`cb4bec3`](https://github.com/shinybrar/vosfs/commit/cb4bec342bc0b5430c4732ed398154bf8dcf8e23)
([CI 29619800790](https://github.com/shinybrar/vosfs/actions/runs/29619800790)).

| Item | Status |
| --- | --- |
| Release Please PR | [#152](https://github.com/shinybrar/vosfs/pull/152) **MERGED** (squash) — changelog included admitted later surfaces (`rm`/`cp`/`mv`/`stat`) |
| Merge commit | `ea17bad0e232c124e118d710f23effd8f2fc8728` |
| Tag | `fsspec-cli-v0.2.0` at that commit |
| GitHub Release | **Published** (not draft): [fsspec-cli-v0.2.0](https://github.com/shinybrar/vosfs/releases/tag/fsspec-cli-v0.2.0) |
| Assets | `fsspec_cli-0.2.0-py3-none-any.whl`, `fsspec_cli-0.2.0.tar.gz` only |
| Publish workflow | [29620035798](https://github.com/shinybrar/vosfs/actions/runs/29620035798) success |
| CI on release commit | [29619950931](https://github.com/shinybrar/vosfs/actions/runs/29619950931) hermetic + installed-wheel all green |
| Local re-verify (`feat/issue-147`) | Hermetic `998 passed, 8 skipped`; installed-wheel exit 0 (`802`+`16`); isolated wheel install prints `version 0.2.0` |
| PyPI | **No publication** (`fsspec-cli/0.2.0` HTTP 404; correct) |
| vosfs release | Untouched (latest remains `v0.4.0`) |

## 4. Closing evidence checklist (for #147 / #120)

Issue #147 release-contract items satisfied for the later cut:

- [x] Release PR URL and merge commit SHA (`#152` → `ea17bad…`)
- [x] `fsspec-cli-v0.2.0` tag and GitHub Release URL (published, wheel+sdist)
- [x] Publish + release-commit CI evidence; local hermetic + installed-wheel + isolated install
- [x] No PyPI; no vosfs release coupling; repo Latest badge restored to `v0.4.0`

Still required before closing #120 (not #147 release-lane alone):

- Canonical matrix commit plus qualifying CI/live links
- Explicit admitted profiles, preserved strict rejections, and unverified native rows
  (see catalog + claim audit)
- Catalog reconciliation accepted

Issue #120 stays **OPEN** until that catalog reconciliation is accepted; verified
`0.2.0` publication alone does not close it.

## 5. Out of scope (unchanged)

PyPI publication, vosfs release/docs deployment, repository extraction, new
command behavior, backend hardening (#113), CANFAR/vostools migration, or
relaxing profiles to greenwash gates.
