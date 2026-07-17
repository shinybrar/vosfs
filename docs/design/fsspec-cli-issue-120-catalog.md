# Issue #120 filesystem-utility catalog dispositions

<!-- pyml disable line-length -->

Status: **Locked post-campaign catalog reconciliation (#147)**

Parent: [Expand fsspec-cli beyond plain ls](https://github.com/shinybrar/vosfs/issues/120)

Reconciles: [Reconcile Issue 120 and cut the later fsspec-cli release](https://github.com/shinybrar/vosfs/issues/147)

Canonical matrix: [tested command matrix](fsspec-cli-tested-command-matrix.md)

Claim and evidence audit: [claim/evidence audit](fsspec-cli-issue-120-claim-evidence-audit.md)

Release verification: [later release verification](fsspec-cli-later-release-verification.md)

Supported host platforms: **Linux and macOS only**.

Package claim language: **Issue 8-aligned supported subset** for Issue 8
primitives, plus the separately named **reduced BSD/macOS `stat` profile**.
Never claim POSIX, GNU, BSD/macOS generally, or all-fsspec compatibility.

## 1. Purpose

Every filesystem-related utility named in #120 has exactly one published
disposition below. Admitted surfaces link their locked command compatibility
profiles and matrix rows. Deferred and capability-gated utilities remain
documentation-only: no Typer stubs and no fake backend matrix rows.

Completion of #120 means this catalog is reconciled, not that every named
utility exists as a command.

## 2. Disposition vocabulary

| Disposition | Meaning |
| --- | --- |
| `admitted` | Shipped through `App(sources).typer_app` under a locked profile; matrix rows record evidence honestly. |
| `rejected` | Deliberate source-free or runtime rejection with a locked rejection profile and negative evidence. |
| `unavailable` | Architecture or capability forbids the utility; docs-only; no command stub. |
| `deferred` | May be profiled later; docs-only until an exact capability profile exists. |
| `outside catalog` | Explicitly not an Issue 8 filesystem primitive under #120; not added. |

## 3. Admitted command surfaces

Each admitted row links its exact profile. Matrix status remains authoritative
for source-form claims (`pass` / `fail` / `unsupported` / `unverified`).

| Utility / profile | Disposition | Profile | Matrix |
| --- | --- | --- | --- |
| Plain `ls` and `ls -A` | `admitted` | [plain ls](fsspec-cli-plain-ls-command-profile.md) | matrix rows |
| `ls -l` (and other unapproved `ls` options) | `rejected` | [ls -l rejection](fsspec-cli-ls-long-rejection-profile.md) | preflight `unsupported` |
| `basename string` | `admitted` | [basename](fsspec-cli-basename-command-profile.md) | source-free rows |
| `basename string suffix` | `admitted` | [basename suffix](fsspec-cli-basename-suffix-command-profile.md) | source-free rows |
| `dirname string` | `admitted` | [dirname](fsspec-cli-dirname-command-profile.md) | source-free rows |
| Mapped-file `cat` | `admitted` | [plain cat](fsspec-cli-plain-cat-command-profile.md) | source rows |
| `cat` stdin and `-` | `admitted` | [cat stdin](fsspec-cli-cat-stdin-command-profile.md) | stdin rows |
| `cat -u` | `rejected` | [cat stdin §2](fsspec-cli-cat-stdin-command-profile.md) | preflight `unsupported` |
| Base `mkdir` | `admitted` | [base mkdir](fsspec-cli-base-mkdir-command-profile.md) | source rows; Memory `fail` |
| `mkdir -p` | `admitted` | [mkdir -p](fsspec-cli-mkdir-p-command-profile.md) | source rows; see audit |
| `mkdir -m` | `rejected` | mkdir profiles | preflight `unsupported` |
| Base `rmdir` | `admitted` | [base rmdir](fsspec-cli-base-rmdir-command-profile.md) | source rows |
| `rmdir -p` | `deferred` | [base rmdir](fsspec-cli-base-rmdir-command-profile.md) | preflight rejection until approved |
| XSI `unlink` | `admitted` | [unlink](fsspec-cli-unlink-command-profile.md) | source rows; see audit |
| Base file-only `rm` | `admitted` | [base rm](fsspec-cli-base-rm-command-profile.md) | source rows |
| Exact `rm -f` | `admitted` | [rm -f](fsspec-cli-rm-force-command-profile.md) | source rows; see audit |
| Exact `rm -d` | `admitted` | [rm -d](fsspec-cli-rm-directory-command-profile.md) | source rows; see audit |
| Exact `rm -v` | `admitted` | [rm -v](fsspec-cli-rm-verbose-command-profile.md) | source rows; see audit |
| `rm -R` / `rm -r` | `rejected` | [rm recursive rejection](fsspec-cli-rm-recursive-rejection-profile.md) | preflight `unsupported` |
| `rm -i` and unprofiled combinations | `rejected` | base / force / directory / verbose rm profiles | preflight `unsupported` |
| Same-source two-operand file `cp` | `admitted` | [same-source cp](fsspec-cli-same-source-cp-command-profile.md) | source rows; see audit |
| Cross-source two-operand file `cp` | `admitted` | [cross-source cp](fsspec-cli-cross-source-cp-command-profile.md) | source-pair rows |
| Multi-source file `cp` into directory | `admitted` | [multi-source cp](fsspec-cli-multi-source-cp-command-profile.md) | source rows |
| Same-source / cross-source `cp -R` | `rejected` | [recursive cp rejection](fsspec-cli-recursive-cp-rejection-profile.md) | preflight `unsupported` |
| Same-source two-operand file `mv` | `admitted` | [same-source mv](fsspec-cli-same-source-mv-command-profile.md) | exact `_mv` rows; often `unverified` |
| Same-source multi-file `mv` into directory | `admitted` | [multi-file mv](fsspec-cli-same-source-multi-file-mv-command-profile.md) | exact `_mv` rows; often `unverified` |
| Same-source directory `mv` | `rejected` | [same-source mv directory boundary](fsspec-cli-same-source-mv-command-profile.md) | runtime/directory rejection |
| Cross-source `mv` | `rejected` | [cross-source mv rejection](fsspec-cli-cross-source-mv-rejection-profile.md) | preflight `unsupported` |
| Reduced BSD/macOS-shaped `stat` | `admitted` (non-POSIX) | [BSD/macOS stat](fsspec-cli-bsd-macos-stat-command-profile.md) | Local `pass`; Memory/vosfs incomplete-shape closed-fail / `unverified` |

### 3.1 Reduced-profile divergences (must stay visible)

- **basename / dirname:** Issue 8 lexical algorithms over host-decoded argv; NUL
  rejected; newline processed as data; no source acquisition.
- **mkdir / mkdir -p:** source-default creation only; not POSIX mode or umask.
- **cp / mv / rm:** passing rows prove the named reduced profile only — not
  POSIX mode, ownership, link identity, timestamps, or atomic rename.
- **`type=file`:** fsspec common type shape only; not POSIX regular-file or
  non-link proof.
- **`stat`:** never POSIX `stat`, never GNU `stat`, never full macOS/BSD
  `stat(1)`, and never evidence for `ls -l`.

## 4. Unavailable by architecture

Docs-only. No Typer commands.

| Utility | Disposition | Reason locked in #120 |
| --- | --- | --- |
| `cd`, `pwd` | `unavailable` | Mutate or report persistent shell/process state; every invocation requires explicit source and path. |
| `umask` | `unavailable` | Mutates persistent process creation policy the invocation-owned source model neither owns nor carries. |
| `df` | `unavailable` | No portable total/used/available/capacity/filesystem-identity contract in fsspec. |
| POSIX `du` | `unavailable` | fsspec `du` aggregates logical sizes; Issue 8 requires allocated space in block units. |

## 5. Capability-gated / initially unsupported

Docs-only until an exact capability profile and evidence exist. No stubs.

| Utility | Disposition | Reason |
| --- | --- | --- |
| `chmod`, `chgrp`, `chown` | `deferred` | Authoritative modes, ownership namespaces, mutation, recursion, link handling. |
| `ln`, XSI `link` | `deferred` | Copied object or redirect record is not a hard or symbolic link. |
| `readlink`, `realpath` | `deferred` | Native link text, component resolution, loop detection, existence rules. |
| `touch` | `deferred` | Generic touch may create or truncate; not timestamp-only mutation. |
| `mkfifo` | `unavailable` | Zero-byte object is not a FIFO; no generic special-file contract. |
| XSI `fuser` | `unavailable` | Host process tables, open-file state, signals, mount identity. |
| `pathchk` | `deferred` | Default needs backend pathname limits; lexical `-p`/`-P` alone insufficient. |

## 6. Outside the #120 primitive set

Not Issue 8 filesystem utilities under this catalog. Not added.

| Name | Disposition |
| --- | --- |
| `file` | `outside catalog` (content classification) |
| `mount`, `sync`, `install`, `truncate`, `mktemp` | `outside catalog` |
| `find`, walk, glob, traversal engines | `outside catalog` (fsspec owns composites) |
| `cksum`, `cmp`, `dd`, `head`, `od`, `pax`, `split`, `strings`, `tail`, `tee`, `test`, `wc` | `outside catalog` (content/stream/archive utilities) |

## 7. Closing issue 120

Issue 120 may close only when:

1. this catalog remains complete and truthful;
2. admitted profiles and matrix rows do not overclaim;
3. deferred/unavailable utilities stay docs-only; and
4. the later independent `fsspec-cli` GitHub Release lane is verified per
   [later release verification](fsspec-cli-later-release-verification.md).

Closing #120 must not imply that rejected, deferred, unavailable, or
`unverified` surfaces are supported.
