# Issue #120 claim and evidence audit

> **Historical, non-normative.** This audit records retired evidence policy and
> defines no current CI, release, or support requirement.

<!-- pyml disable line-length -->

Status: **Honest post-campaign audit (#147)** — not a greenwash pass

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

Reconciles: [Issue #147](https://github.com/shinybrar/vosfs/issues/147)

Catalog: [Issue #120 catalog dispositions](fsspec-cli-issue-120-catalog.md)

Matrix: [tested command matrix](fsspec-cli-tested-command-matrix.md)

Observed tip for this audit: `2971c43` (`feat(fsspec-cli): add reduced BSD/macOS-shaped
stat (#146)`), package metadata version then `0.1.1`. Later lane since cut:
[PR #152](https://github.com/shinybrar/vosfs/pull/152) **MERGED** →
`fsspec-cli` `0.2.0` published and verified (see
[later release verification](fsspec-cli-later-release-verification.md)).

## 1. Claim language checklist

| Claim rule | Verdict |
| --- | --- |
| Package describes an **Issue 8-aligned supported subset** for Issue 8 primitives | **pass** — README and profiles use that framing |
| Separately named **reduced BSD/macOS `stat`** profile; never POSIX `stat` | **pass** — [stat profile](fsspec-cli-bsd-macos-stat-command-profile.md) and README |
| No blanket POSIX / GNU / BSD/macOS / all-fsspec compatibility claim | **pass** in design docs and README |
| mkdir / cp / mv / rm / `type=file` divergences repeated at claim sites | **pass** where profiles admit reduced surfaces; keep repeating in release notes |
| Native vosfs priority without forcing unsupported backend capability | **pass** — many native rows stay `unverified` or closed-fail rather than forced `pass` |
| Live evidence never substitutes for hermetic proof | **pass** contract in matrix §6; live required only where stated |

## 2. Matrix honesty snapshot (tip `2971c43`)

Statuses below are the **published matrix**, not a wish list. `unverified`
includes missing immutable CI IDs, incomplete metadata shapes, and exact-operation
gaps. Do not promote those rows to `pass` for release marketing.

### 2.1 Positive / mixed source rows worth calling out

| Surface | Local | Memory | vosfs native | Notes |
| --- | --- | --- | --- | --- |
| Plain `ls` | `pass` | `pass` | `pass` (+ live) | First-release quality rows |
| Base `rmdir` | `pass` | `pass` | `pass` | Immutable CI cited |
| Base `rm` | `pass` | `pass` | `pass` | Immutable CI cited |
| Base `mkdir` | `pass` | **`fail`** | `pass` | Memory failure is a reached contradiction — keep `fail` |
| `mkdir -p` | `unverified` | `unverified` | `unverified` | Hermetic probes exist; matrix still lacks qualifying immutable evidence IDs |
| XSI `unlink` | `unverified` | `unverified` | `unverified` | Evidence cells still `—` |
| Plain `cat` | `pass` | `pass` | `unverified` | Native live absent |
| Same-source `cp` | `unverified` | `unverified` | `unverified` | Hermetic present; no immutable pass IDs on Local/Memory |
| Cross-source `cp` | Local↔Memory `pass` | — | directions `unverified` | |
| Multi-source `cp` | `pass` | `pass` | `unverified` | |
| Same-source file / multi-file `mv` | `unverified` | `unverified` | `unverified` | Exact awaitable `_mv` often absent; rejection/classification only |
| Reduced `stat` | `pass` | `unverified` | `unverified` | Incomplete `_info` shape closed-fails; not sparse pass |
| `rm -f` / `rm -d` / `rm -v` | mostly `unverified` | mostly `unverified` | mixed | Do not overclaim in release text |

### 2.2 Deliberate `unsupported` (source-free rejection)

Preserved and must stay documentation + negative-test backed:

- `ls -l`
- `cat -u`
- `rm -R` / `rm -r`
- same-source and cross-source `cp -R`
- cross-source `mv`
- reduced `stat` option/operand rejection
- other profiled option/operand rejections listed in the matrix

### 2.3 Vocabulary reminders

| Status | Use when |
| --- | --- |
| `unverified` | Missing, stale, blocked, credential, or infrastructure evidence |
| `fail` | Command ran and contradicted the profile (example: Memory base `mkdir`) |
| `unsupported` | Deliberate source-free rejection with negative proof |
| `pass` | Every required gate for that exact row passed |

## 3. README / help / profile alignment

| Artifact | Audit |
| --- | --- |
| `src/fsspec-cli/README.md` | Describes admitted surfaces including reduced BSD/macOS `stat`; does not claim POSIX `stat` |
| Command profiles under `docs/design/` | Present for each admitted/rejected slice in the catalog |
| Typer stubs for deferred utilities | **Absent** (correct) |
| Fake backend matrix rows for unavailable utilities | **Absent** (correct) |
| `src/fsspec-cli/CHANGELOG.md` Unreleased | Hand-maintained notes for early #120 slices; later feat commits rely on conventional titles for Release Please — **do not hand-edit versioned changelog entries** |

## 4. Backend / #113 boundary

Native vosfs remains the priority real source. This audit does **not** absorb
[#113](https://github.com/shinybrar/vosfs/issues/113) backend hardening. Gaps that
block exact `_mv`, rich `_info` for `stat`, or live mutation stay `unverified`
or closed-fail rather than release-forced.

## 5. Audit verdict

**Catalog reconciled; claim language honest; evidence incomplete for a
blanket “all #120 surfaces pass on all sources” release claim.**

Safe release messaging:

- Admit only profiles and rows that are `pass` or deliberate `unsupported`.
- Name `unverified` and `fail` rows explicitly when discussing compatibility.
- Keep `stat` reduced BSD/macOS-shaped and non-POSIX.
- Keep unavailable/deferred utilities docs-only.

Unsafe release messaging (forbidden):

- “POSIX compatible”
- “works with all fsspec backends”
- Treating Memory `mkdir` `fail` or `mv`/`stat` `unverified` as success
- Citing `stat` as POSIX evidence or as `ls -l` evidence
