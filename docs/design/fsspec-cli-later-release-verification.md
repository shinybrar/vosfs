# Later `fsspec-cli` GitHub Release verification (#147)

<!-- pyml disable line-length -->

Status: **Baseline `0.1.0` / `0.1.1` published with wheel+sdist; later `0.2.0`
blocked on green `main` CI + Release Please refresh of #152**

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

Side effect note: undrafting may flip GitHub’s repo-wide “Latest” badge onto
`fsspec-cli-v0.1.1`. Restore with `gh release edit v0.4.0 --latest` if needed;
that does not change package lineage.

## 3. Later release cut (`0.2.0`) — blocked

Release Please owns the version. Do **not** couple to vosfs. Do **not**
hand-edit versioned `CHANGELOG.md` entries.

| Item | Status |
| --- | --- |
| Open Release Please PR | [#152](https://github.com/shinybrar/vosfs/pull/152) `chore(main): release fsspec-cli 0.2.0` — **OPEN**, mergeable when CI green on the PR, but **changelog stale** |
| Completeness vs `main` tip `2971c43` | Missing admitted later surfaces: exact `rm -f`/`-d`/`-v`, cross/multi `cp`, same-source `mv` / multi-file `mv`, reduced BSD/macOS `stat`, … |
| Why stale | `Release` workflow only runs after **successful** CI on `main`. Recent `main` CI runs fail (e.g. [29619118658](https://github.com/shinybrar/vosfs/actions/runs/29619118658): Python 3.10 `CancelledError` identity asserts in `test_rm.py` / `test_stat.py`), so Release Please never refreshes #152 |
| Docs-only #147 merge | **Cannot** refresh #152 by itself: component path is `src/fsspec-cli`; `docs/` is outside that scope (`docs/agents/release.md`) |
| Required after a complete merge | `fsspec-cli-v0.2.0` tag exists; Publish attaches wheel+sdist only; draft undrafted; PyPI untouched |
| Hermetic + installed-wheel gates | Must pass on the **0.2.0 release commit** before treating that cut as verified |

### 3.1 Honest sequence to cut `0.2.0`

1. Fix `main` CI (Python 3.10 cancellation identity failures — separate from #147 docs).
2. Let successful `main` CI run the `Release` workflow so Release Please rewrites
   [#152](https://github.com/shinybrar/vosfs/pull/152) changelog against tip.
3. Review the refreshed PR; squash-merge when changelog matches admitted #120
   surfaces.
4. Confirm Publish attaches wheel+sdist and undrafts `fsspec-cli-v0.2.0`.
5. Run hermetic + installed-wheel gates on that release commit; record evidence
   here / in the #147 closing comment.

**Do not** squash-merge the current stale #152 changelog. **Do not** hand-edit
`src/fsspec-cli/CHANGELOG.md` to invent missing entries.

## 4. Closing evidence checklist (for #147 / #120)

When the later release is actually published, the closing comment needs:

- Release PR URL and merge commit SHA
- `fsspec-cli-vX.Y.Z` tag and GitHub Release URL
- Wheel/sdist filenames and isolated installation run
- Canonical matrix commit plus qualifying CI/live links
- Explicit admitted profiles, preserved strict rejections, and unverified native rows
  (see catalog + claim audit)

#120 stays open until that later cut is verified; baseline `0.1.0`/`0.1.1`
publication alone does not close it.

## 5. Out of scope (unchanged)

PyPI publication, vosfs release/docs deployment, repository extraction, new
command behavior, backend hardening (#113), CANFAR/vostools migration, or
relaxing profiles to greenwash gates.
