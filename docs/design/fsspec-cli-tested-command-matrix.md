# `fsspec-cli` tested command matrix contract

<!-- pyml disable line-length -->

Status: **Locked matrix schema and evidence rules**

Question: [Define the tested command matrix contract](https://github.com/shinybrar/vosfs/issues/81)

First release target: **`fsspec-cli` 0.1.0, GitHub Releases only**

## 1. Purpose

The tested command matrix records exactly which command profile and async
filesystem source form combinations have qualifying evidence. It is a narrow
compatibility claim, not a declaration that `fsspec-cli` works with every
fsspec backend or that a backend implements POSIX generally.

Each claim is command-scoped, source-form-scoped, and version-scoped. A result
for plain `ls` says nothing about another command or option. A result for an
adapted Local source says nothing about a raw `LocalFileSystem` or another
adapter. A result for one exact dependency set says nothing about a later set
until the required gates run again.

The matrix is evidence for maintainers and downstream hosts. It MUST NOT be
loaded at runtime, exposed as backend capability negotiation, or used to
replace real operation and result validation.

## 2. Canonical artifact

The canonical v1 matrix is a hand-maintained, row-oriented Markdown document
under `docs/design`. Production tests and their required CI gates are the
executable evidence; prose or a manually edited status cannot override a
failing gate.

V1 defines no TOML, YAML, JSON, generator, runtime registry, or public schema.
A machine-readable form may be introduced later when a real consumer exists,
without changing the meaning of the status vocabulary below.

Rows are used instead of backend columns so a source-independent command
rejection can be recorded once. The matrix MUST NOT duplicate a preflight
result across backends that were never entered.

## 3. Matrix row identity

One matrix row identifies one claim through these fields:

| Field | Required meaning |
| --- | --- |
| Command profile | The linked, locked observable command-and-option contract. |
| Scope | `source` when filesystem behavior is exercised; `command preflight` when rejection completes before source entry; `source-free command` when a lexical command completes without source entry. |
| Source form | The configured source and whether it yields native or adapted async behavior. For command preflight, this is `not entered`. |
| Status | Exactly one of `pass`, `fail`, `unsupported`, or `unverified`. |
| Required gates | The hermetic and, where applicable, live evidence needed for this row. |
| Evidence | `—` for `unverified`; otherwise one or more evidence records satisfying Section 5. |

The initial source forms are:

- `local / adapted async`: a source yielding
  `AsyncFileSystemWrapper(LocalFileSystem(), asynchronous=True)`;
- `memory / adapted async`: a source yielding
  `AsyncFileSystemWrapper(MemoryFileSystem(), asynchronous=True)`; and
- `vosfs / native async`: a source yielding a fresh
  `VOSpaceFileSystem(asynchronous=True, skip_instance_cache=True)` and closing
  it on the invocation loop.

Raw synchronous Local and Memory instances, wrong-mode async filesystems, and
host-owned reusable instances are outside these source forms and follow the
locked source-validation contract. A protocol name alone never identifies a
tested row.

A missing row, missing cell, or missing required gate means `unverified`. It
MUST NOT be interpreted as `unsupported`.

## 4. Status vocabulary

### `pass`

The positive command profile passed every required gate for the row's exact
build, dependency, source-form, and platform evidence. Successful evidence for
only part of the required set remains `unverified`.

### `fail`

A qualifying test reached the command behavior under test and contradicted
the locked profile. Examples include wrong output, exit status, call sequence,
result validation, cleanup, or backend-independent behavior.

An infrastructure, credential, service-availability, or test-setup failure
that prevents the command behavior from being observed is inconclusive and
therefore `unverified`, not `fail`.

### `unsupported`

The locked command profile deliberately excludes the requested behavior and a
qualifying negative test proves its complete rejection contract. For `ls -l`,
that includes the diagnostic, empty stdout, exit status `2`, and zero source
entry or filesystem work.

An observed `NotImplementedError`, a missing backend field, an absent test, or
a failing positive test does not automatically make a row `unsupported`.
Changing a supported profile to unsupported requires an explicit profile
decision and its negative evidence.

### `unverified`

No current qualifying evidence establishes another status. This includes new
or absent source forms, the pre-implementation state, incomplete required
gates, version drift, and inconclusive live runs.

`unverified` is neutral. It does not block adding future or third-party
backends to the universe of possible sources. It blocks only a release or
claim that explicitly requires that row.

## 5. Evidence records

Every `pass`, `fail`, or `unsupported` row MUST cite evidence that records:

1. the exact `fsspec-cli` build identity: version and tag for a release, or
   commit for unreleased work;
2. the exact fsspec and Typer versions;
3. the exact backend distribution version or commit when it is separate from
   fsspec, including `vosfs`;
4. whether the source form is native or adapted async;
5. the Python version and operating-system runner;
6. the gate kind, observation time, and immutable test, CI-run, or release
   evidence link; and
7. enough linked lock or environment evidence to recover the complete
   resolved dependency set without copying every transitive version into the
   matrix row.

Local and Memory use the fsspec version as their backend implementation
version. A command-preflight row records the `fsspec-cli` and Typer identities
but no invented backend version because no source is entered.

Credentials, entry names, tokens, certificates, and other sensitive live data
MUST NOT appear in matrix evidence.

## 6. Evidence gates

### 6.1 Hermetic gate

Hermetic evidence is required for every claimed `pass` or `unsupported`
status. It MUST:

- prohibit unplanned network access;
- use deterministic Local temporary storage, isolated Memory state, or a
  fully mocked `vosfs` transport;
- exercise the same production handler and async source contract;
- run against the project's declared supported Python and operating-system CI
  matrix; and
- test an isolated built wheel before release so undeclared dependency leakage
  cannot satisfy the result accidentally.

For an unsupported option whose rejection completes during command preflight,
the hermetic negative test enters no source. No backend-specific hermetic or
live execution is invented for that row.

### 6.2 Live OpenCADC gate

A narrow live OpenCADC listing is additionally required for a positive
`vosfs / native async` plain-`ls` claim. It MUST be read-only, credential-gated,
run only from a trusted default-branch or manual workflow, and use the same
production handler as hermetic tests.

The evidence records the sanitized service environment, exact source build,
observation time, exit status, and call shape. It does not publish directory
contents or credential material and does not broaden one observation into a
general OpenCADC or VOSpace guarantee.

Live evidence is not required for Local, Memory, or source-independent
preflight rejection. A live run supplements hermetic evidence and can never
replace it.

## 7. Freshness and classification

Evidence is current only for its recorded version and source-form identity.
Changing the command profile, relevant `fsspec-cli` implementation, fsspec,
Typer, backend distribution, adapter mode, or declared platform set makes the
affected row `unverified` until its required gates run again.
The declared supported host platforms are Linux and macOS.

There is no arbitrary wall-clock expiry. Every release candidate obtains new
evidence for its exact build, and live observations always retain their time.
Git history preserves superseded matrix states; the current matrix does not
pretend old evidence applies to a new tuple.

When classifying a gate result:

1. setup, authentication, connectivity, or service availability prevented the
   command observation: `unverified`;
2. the command ran and violated a positive or rejection contract: `fail`;
3. every required positive gate passed: `pass`; or
4. every required negative rejection gate passed for an explicitly excluded
   profile: `unsupported`.

## 8. Initial matrix and first-release target

Only qualifying source-form gates can change a row from `unverified`. The
current rows have complete exact-commit evidence for the first-release target;
Section 9 still requires the release candidate to rerun every required gate.

| Command profile | Scope | Source form | Current status | Required status for `fsspec-cli` 0.1.0 | Required gates | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| [Plain `ls`](fsspec-cli-plain-ls-command-profile.md) | source | `local / adapted async` | `pass` | `pass` | Hermetic | [H-2026-07-16-29536484110](#h-2026-07-16-29536484110) |
| [Plain `ls`](fsspec-cli-plain-ls-command-profile.md) | source | `memory / adapted async` | `pass` | `pass` | Hermetic | [H-2026-07-16-29536484110](#h-2026-07-16-29536484110) |
| [Plain `ls`](fsspec-cli-plain-ls-command-profile.md) | source | `vosfs / native async` | `pass` | `pass` | Hermetic and live OpenCADC | [H-2026-07-16-29536484110](#h-2026-07-16-29536484110), [L-2026-07-16-29536609626](#l-2026-07-16-29536609626) |
| [Base `rmdir`](fsspec-cli-base-rmdir-command-profile.md) | source | `local / adapted async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29583728890](#h-2026-07-17-29583728890) |
| [Base `rmdir`](fsspec-cli-base-rmdir-command-profile.md) | source | `memory / adapted async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29583728890](#h-2026-07-17-29583728890) |
| [Base `rmdir`](fsspec-cli-base-rmdir-command-profile.md) | source | `vosfs / native async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29583728890](#h-2026-07-17-29583728890) |
| [Base `rmdir` `-p` strict rejection](fsspec-cli-base-rmdir-command-profile.md#21-option-and-operand-preflight) | command preflight | `not entered` | `unsupported` | `unsupported` | Hermetic negative rejection | [H-2026-07-17-29583728890](#h-2026-07-17-29583728890) |
| [`ls -l` strict rejection](fsspec-cli-ls-long-rejection-profile.md) | command preflight | `not entered` | `unsupported` | `unsupported` | Hermetic negative rejection | [H-2026-07-16-29536484110](#h-2026-07-16-29536484110) |
| [`basename string`](fsspec-cli-basename-command-profile.md) | source-free command | `not entered` | `pass` | `pass` | Hermetic | [H-2026-07-17-29564531624](#h-2026-07-17-29564531624) |
| [`basename string suffix`](fsspec-cli-basename-suffix-command-profile.md) | source-free command | `not entered` | `pass` | `pass` | Hermetic | Hermetic `test_command_matrix.py` on this change |
| [`basename string`](fsspec-cli-basename-command-profile.md) option/operand rejection | command preflight | `not entered` | `unsupported` | `unsupported` | Hermetic negative rejection | [H-2026-07-17-29564531624](#h-2026-07-17-29564531624) |
| [`basename string suffix`](fsspec-cli-basename-suffix-command-profile.md) third-operand rejection | command preflight | `not entered` | `unsupported` | `unsupported` | Hermetic negative rejection | Hermetic `test_command_matrix.py` on this change |
| [`dirname string`](fsspec-cli-dirname-command-profile.md) | source-free command | `not entered` | `pass` | `pass` | Hermetic | [H-2026-07-17-29586387337](#h-2026-07-17-29586387337) |
| [`dirname string`](fsspec-cli-dirname-command-profile.md) option/operand rejection | command preflight | `not entered` | `unsupported` | `unsupported` | Hermetic negative rejection | [H-2026-07-17-29586387337](#h-2026-07-17-29586387337) |
| [Plain mapped-file `cat`](fsspec-cli-plain-cat-command-profile.md) | source | `local / adapted async` | `pass` | — | Hermetic | Hermetic `test_command_matrix.py` on this change |
| [Plain mapped-file `cat`](fsspec-cli-plain-cat-command-profile.md) | source | `memory / adapted async` | `pass` | — | Hermetic | Hermetic `test_command_matrix.py` on this change |
| [Plain mapped-file `cat`](fsspec-cli-plain-cat-command-profile.md) | source | `vosfs / native async` | `unverified` | — | Hermetic and live OpenCADC | Hermetic mocked transport present; live evidence absent |
| [Binary stdin and `-` for `cat`](fsspec-cli-cat-stdin-command-profile.md) | stdin / mixed | `memory / adapted async` | `pass` | — | Hermetic | Hermetic `test_cat.py` and `test_cat_process.py` on this change |
| [`cat -u` strict rejection](fsspec-cli-cat-stdin-command-profile.md#2-operand-preflight) | command preflight | `not entered` | `unsupported` | `unsupported` | Hermetic negative rejection | Hermetic `test_cat.py` on this change |
| [Base `mkdir`](fsspec-cli-base-mkdir-command-profile.md) | source | `local / adapted async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29565052441](#h-2026-07-17-29565052441) |
| [Base `mkdir`](fsspec-cli-base-mkdir-command-profile.md) | source | `memory / adapted async` | `fail` | `pass` | Hermetic | [H-2026-07-17-29565052441](#h-2026-07-17-29565052441) |
| [Base `mkdir`](fsspec-cli-base-mkdir-command-profile.md) | source | `vosfs / native async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29565052441](#h-2026-07-17-29565052441) |
| [`mkdir -p`](fsspec-cli-mkdir-p-command-profile.md) | source | `local / adapted async` | `unverified` | `pass` | Hermetic | Hermetic `test_command_matrix.py` on this change; passing rows claim source-default creation only, not POSIX mode or umask |
| [`mkdir -p`](fsspec-cli-mkdir-p-command-profile.md) | source | `memory / adapted async` | `unverified` | `pass` | Hermetic | Hermetic `test_command_matrix.py` on this change; passing rows claim source-default creation only, not POSIX mode or umask |
| [`mkdir -p`](fsspec-cli-mkdir-p-command-profile.md) | source | `vosfs / native async` | `unverified` | `pass` | Hermetic | Hermetic `test_vosfs_command_matrix.py` on this change; passing rows claim source-default creation only, not POSIX mode or umask |
| [`mkdir -p` `-m` strict rejection](fsspec-cli-mkdir-p-command-profile.md#21-option-and-operand-preflight) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | Hermetic `test_command_matrix.py` on this change |
| [`mkdir -p` `--parents` strict rejection](fsspec-cli-mkdir-p-command-profile.md#21-option-and-operand-preflight) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | Hermetic `test_command_matrix.py` on this change |
| [`mkdir -p` `-p` after operand strict rejection](fsspec-cli-mkdir-p-command-profile.md#21-option-and-operand-preflight) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | Hermetic `test_command_matrix.py` on this change |
| [`mkdir -p` mixed `-pm` strict rejection](fsspec-cli-mkdir-p-command-profile.md#21-option-and-operand-preflight) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | Hermetic `test_command_matrix.py` on this change |
| [XSI `unlink`](fsspec-cli-unlink-command-profile.md) | source | `local / adapted async` | `unverified` | `pass` | Hermetic | — |
| [XSI `unlink`](fsspec-cli-unlink-command-profile.md) | source | `memory / adapted async` | `unverified` | `pass` | Hermetic | — |
| [XSI `unlink`](fsspec-cli-unlink-command-profile.md) | source | `vosfs / native async` | `unverified` | `pass` | Hermetic | — |
| [Base file-only `rm`](fsspec-cli-base-rm-command-profile.md) | source | `local / adapted async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29586378872](#h-2026-07-17-29586378872) |
| [Base file-only `rm`](fsspec-cli-base-rm-command-profile.md) | source | `memory / adapted async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29586378872](#h-2026-07-17-29586378872) |
| [Base file-only `rm`](fsspec-cli-base-rm-command-profile.md) | source | `vosfs / native async` | `pass` | `pass` | Hermetic | [H-2026-07-17-29586378872](#h-2026-07-17-29586378872) |
| [Base file-only `rm` option rejection](fsspec-cli-base-rm-command-profile.md#21-option-and-operand-preflight) | command preflight | `not entered` | `unsupported` | `unsupported` | Hermetic negative rejection | [H-2026-07-17-29586378872](#h-2026-07-17-29586378872) |
| [`rm -f`](fsspec-cli-rm-force-command-profile.md) | source | `local / adapted async` | `unverified` | — | Hermetic | Hermetic `test_command_matrix.py` on this change |
| [`rm -f`](fsspec-cli-rm-force-command-profile.md) | source | `memory / adapted async` | `unverified` | — | Hermetic | Hermetic `test_command_matrix.py` on this change |
| [`rm -f`](fsspec-cli-rm-force-command-profile.md) | source | `vosfs / native async` | `unverified` | — | Hermetic | Hermetic `test_vosfs_command_matrix.py` on this change |
| [`rm -f` unsupported-option rejection](fsspec-cli-rm-force-command-profile.md#1-scope) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | Hermetic `test_rm.py` on this change |
| [Verified same-source `cp`](fsspec-cli-same-source-cp-command-profile.md) | source | `local / adapted async` | `unverified` | — | Hermetic | — |
| [Verified same-source `cp`](fsspec-cli-same-source-cp-command-profile.md) | source | `memory / adapted async` | `unverified` | — | Hermetic | — |
| [Verified same-source `cp`](fsspec-cli-same-source-cp-command-profile.md) | source | `vosfs / native async` | `unverified` | — | Hermetic | Hermetic mocked transport present; live evidence absent |
| [Verified same-source `cp` option rejection](fsspec-cli-same-source-cp-command-profile.md#21-option-and-operand-preflight) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | — |
| [Positively evidenced same-source file `mv`](fsspec-cli-same-source-mv-command-profile.md) | source | `local / adapted async` | `unverified` | — | Hermetic exact-operation rejection | Hermetic `test_command_matrix.py`; wrapper does not declare `_mv` and sync facade is trapped |
| [Positively evidenced same-source file `mv`](fsspec-cli-same-source-mv-command-profile.md) | source | `memory / adapted async` | `unverified` | — | Hermetic exact-operation rejection | Hermetic `test_command_matrix.py`; wrapper does not declare `_mv` and sync facade is trapped |
| [Positively evidenced same-source file `mv`](fsspec-cli-same-source-mv-command-profile.md) | source | `vosfs / native async` | `unverified` | — | Hermetic exact-operation classification | Hermetic `test_vosfs_command_matrix.py`; native form does not declare `_mv` |
| [Same-source file `mv` option rejection](fsspec-cli-same-source-mv-command-profile.md) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | Hermetic `test_mv.py` on this change |

Other backends and source forms remain implicitly `unverified`. They do not
block the first release because they are not required release rows.

### H-2026-07-17-29583728890

This successful exact-commit CI run observed `fsspec-cli` 0.1.1 at
[commit `75224f7a4cff6c3c8e80dc5006f6f63f4e34c01c`](https://github.com/shinybrar/vosfs/commit/75224f7a4cff6c3c8e80dc5006f6f63f4e34c01c)
with fsspec 2026.6.0, Typer 0.27.0, and vosfs 0.4.0. The complete resolved
dependency set is recoverable from the
[commit-pinned `uv.lock`](https://github.com/shinybrar/vosfs/blob/75224f7a4cff6c3c8e80dc5006f6f63f4e34c01c/uv.lock).

The hermetic gate exercised adapted async Local and Memory sources plus a
mocked native async `vosfs` transport through the production `App` seam. The
positive source rows removed an empty directory, rejected a non-empty parent
and a file operand, and recorded exact `_info` / `_rmdir` call shapes. The
negative `-p` preflight case completed without entering a source. The
installed-wheel jobs rebuilt the member outside the workspace and ran
`test_command_matrix.py`, `test_vosfs_command_matrix.py`, and `test_rmdir.py`
with declared runtime dependencies only.

The gate ran from 2026-07-17T13:22:49Z through 2026-07-17T13:24:19Z in
[GitHub Actions run 29583728890](https://github.com/shinybrar/vosfs/actions/runs/29583728890).
Every leg used runner 2.335.1 and provisioner `20260707.563`. Runner image
versions below come from each job log's `Runner Image` group:

| Python | Operating system | Runner image | Hermetic job | Installed-wheel job |
| --- | --- | --- | --- | --- |
| 3.10.20 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87895410782](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410782) | [87895410868](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410868) |
| 3.11.15 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87895410786](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410786) | [87895410809](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410809) |
| 3.12.3 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87895410858](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410858) | [87895410815](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410815) |
| 3.13.14 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87895410790](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410790) | [87895410824](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410824) |
| 3.14.6 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87895410838](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410838) | [87895410773](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410773) |
| 3.12.10 | macOS 26.4, build 25E246 | `macos-26-arm64@20260715.0248.1` | [87895410778](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410778) | [87895410804](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895410804) |

Python patch versions for the Ubuntu 3.12 and macOS 3.12
installed-wheel jobs were not printed in those job logs; the table reuses the
matching hermetic-leg patch versions from the same workflow matrix cell. The
aggregate
[Required job](https://github.com/shinybrar/vosfs/actions/runs/29583728890/job/87895700265)
passed after quality, hermetic, and installed-wheel dependencies succeeded.

The executable evidence at that commit is the pinned
[`test_rmdir.py`](https://github.com/shinybrar/vosfs/blob/75224f7a4cff6c3c8e80dc5006f6f63f4e34c01c/src/fsspec-cli/tests/test_rmdir.py),
[`test_command_matrix.py`](https://github.com/shinybrar/vosfs/blob/75224f7a4cff6c3c8e80dc5006f6f63f4e34c01c/src/fsspec-cli/tests/test_command_matrix.py)
rmdir probes, and
[`test_vosfs_command_matrix.py`](https://github.com/shinybrar/vosfs/blob/75224f7a4cff6c3c8e80dc5006f6f63f4e34c01c/src/fsspec-cli/tests/test_vosfs_command_matrix.py)
native mocked transport probe.

### H-2026-07-17-29586378872

This successful exact-commit CI run observed `fsspec-cli` 0.1.1 at
[commit `d124c1f4c15bd2c781777ac9f164ec8fa56d80b5`](https://github.com/shinybrar/vosfs/commit/d124c1f4c15bd2c781777ac9f164ec8fa56d80b5)
with fsspec 2026.6.0, Typer 0.27.0, and vosfs 0.4.0. The complete resolved
dependency set is recoverable from the
[commit-pinned `uv.lock`](https://github.com/shinybrar/vosfs/blob/d124c1f4c15bd2c781777ac9f164ec8fa56d80b5/uv.lock).

The hermetic gate exercised adapted async Local and Memory sources plus a
mocked native async `vosfs` transport through the production `App` seam. The
positive source rows removed one and many files through `_rm_file`, rejected a
missing file and a directory operand, and recorded exact `_info` / `_rm_file`
call shapes without calling recursive `_rm` or `_rmdir`. The negative option
preflight case completed without entering a source. The installed-wheel jobs
rebuilt the member outside the workspace and ran
`test_command_matrix.py`, `test_vosfs_command_matrix.py`, and `test_rm.py`
with declared runtime dependencies only.

The gate ran from 2026-07-17T14:03:04Z through 2026-07-17T14:05:21Z in
[GitHub Actions run 29586378872](https://github.com/shinybrar/vosfs/actions/runs/29586378872).
Every leg used runner 2.335.1 and provisioner `20260707.563`. Runner image
versions below come from each job log's `Runner Image` group:

| Python | Operating system | Runner image | Hermetic job | Installed-wheel job |
| --- | --- | --- | --- | --- |
| 3.10.20 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904366512](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366512) | [87904366538](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366538) |
| 3.11.15 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904366480](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366480) | [87904366503](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366503) |
| 3.12.3 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904366747](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366747) | [87904366554](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366554) |
| 3.13.14 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904366625](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366625) | [87904366482](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366482) |
| 3.14.6 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904366501](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366501) | [87904366470](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366470) |
| 3.12.10 | macOS 26.4, build 25E246 | `macos-26-arm64@20260715.0248.1` | [87904366534](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366534) | [87904366528](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904366528) |

Python patch versions for the Ubuntu 3.12 and macOS 3.12
installed-wheel jobs were not printed in those job logs; the table reuses the
matching hermetic-leg patch versions from the same workflow matrix cell. The
aggregate
[Required job](https://github.com/shinybrar/vosfs/actions/runs/29586378872/job/87904776896)
passed after quality, hermetic, and installed-wheel dependencies succeeded.

The executable evidence at that commit is the pinned
[`test_rm.py`](https://github.com/shinybrar/vosfs/blob/d124c1f4c15bd2c781777ac9f164ec8fa56d80b5/src/fsspec-cli/tests/test_rm.py),
[`test_command_matrix.py`](https://github.com/shinybrar/vosfs/blob/d124c1f4c15bd2c781777ac9f164ec8fa56d80b5/src/fsspec-cli/tests/test_command_matrix.py)
rm probes, and
[`test_vosfs_command_matrix.py`](https://github.com/shinybrar/vosfs/blob/d124c1f4c15bd2c781777ac9f164ec8fa56d80b5/src/fsspec-cli/tests/test_vosfs_command_matrix.py)
native mocked transport probe.

### H-2026-07-17-29564531624

This successful exact-commit CI run observed `fsspec-cli` 0.1.1 at
[commit `afc7a1a8b11261acc5fb56664733f9ecd30f89d6`](https://github.com/shinybrar/vosfs/commit/afc7a1a8b11261acc5fb56664733f9ecd30f89d6)
with fsspec 2026.6.0 and Typer 0.27.0. No backend participates in either
basename row. The complete resolved dependency set is recoverable from the
[commit-pinned `uv.lock`](https://github.com/shinybrar/vosfs/blob/afc7a1a8b11261acc5fb56664733f9ecd30f89d6/uv.lock).

The hermetic gate exercised the production `App` seam with a source factory
that raises if called. The positive `source-free command` case completed a
successful lexical `basename` over source-looking text without entering a
source. The negative `command preflight` case rejected an unsupported option
with empty stdout, the locked diagnostic, exit status `2`, and zero source
calls. The installed-wheel jobs rebuilt and tested the member outside the
workspace with declared runtime dependencies only.

The gate ran from 2026-07-17T07:53:20Z through 2026-07-17T07:54:42Z in
[GitHub Actions run 29564531624](https://github.com/shinybrar/vosfs/actions/runs/29564531624).
Every leg used runner 2.335.1 and provisioner `20260707.563`. Runner image
versions below come from each job log's `Runner Image` group:

| Python | Operating system | Runner image | Hermetic job | Installed-wheel job |
| --- | --- | --- | --- | --- |
| 3.10.20 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87834083031](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083031) | [87834082985](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834082985) |
| 3.11.15 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87834083020](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083020) | [87834083023](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083023) |
| 3.12.3 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87834083024](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083024) | [87834083002](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083002) |
| 3.13.14 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87834083035](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083035) | [87834082973](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834082973) |
| 3.14.6 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87834083039](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083039) | [87834082976](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834082976) |
| 3.12.10 | macOS 26.4, build 25E246 | `macos-26-arm64@20260715.0248.1` | [87834083065](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083065) | [87834083004](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834083004) |

Python patch versions for the Ubuntu 3.12 and macOS 3.12
installed-wheel jobs were not printed in those job logs; the table reuses the
matching hermetic-leg patch versions from the same workflow matrix cell. The
aggregate
[Required job](https://github.com/shinybrar/vosfs/actions/runs/29564531624/job/87834298607)
passed after quality, hermetic, and installed-wheel dependencies succeeded.

The executable evidence at that commit is the pinned
[`test_basename.py`](https://github.com/shinybrar/vosfs/blob/afc7a1a8b11261acc5fb56664733f9ecd30f89d6/src/fsspec-cli/tests/test_basename.py),
[`test_basename_process.py`](https://github.com/shinybrar/vosfs/blob/afc7a1a8b11261acc5fb56664733f9ecd30f89d6/src/fsspec-cli/tests/test_basename_process.py),
and
[`test_command_matrix.py::test_basename_string_is_source_free`](https://github.com/shinybrar/vosfs/blob/afc7a1a8b11261acc5fb56664733f9ecd30f89d6/src/fsspec-cli/tests/test_command_matrix.py)
(which then covered both the positive lexical success and negative option
rejection surfaces in one test). Later commits keep those surfaces as separate
matrix tests while preserving this immutable run as the qualifying evidence.

### H-2026-07-17-29586387337

This successful exact-commit CI run observed `fsspec-cli` 0.1.1 at
[commit `f4faba8012689211f8a826065678bdf537a42056`](https://github.com/shinybrar/vosfs/commit/f4faba8012689211f8a826065678bdf537a42056)
with fsspec 2026.6.0 and Typer 0.27.0. No backend participates in either
dirname row. The complete resolved dependency set is recoverable from the
[commit-pinned `uv.lock`](https://github.com/shinybrar/vosfs/blob/f4faba8012689211f8a826065678bdf537a42056/uv.lock).

The hermetic gate exercised the production `App` seam with a source factory
that raises if called. The positive `source-free command` case completed a
successful lexical `dirname` over source-looking text without entering a
source. The negative `command preflight` case rejected an unsupported option
with empty stdout, the locked diagnostic, exit status `2`, and zero source
calls. The installed-wheel jobs rebuilt and tested the member outside the
workspace with declared runtime dependencies only.

The gate ran from 2026-07-17T14:03:11Z through 2026-07-17T14:04:44Z in
[GitHub Actions run 29586387337](https://github.com/shinybrar/vosfs/actions/runs/29586387337).
Every leg used runner 2.335.1 and provisioner `20260707.563`. Runner image
versions below come from each job log's `Runner Image` group:

| Python | Operating system | Runner image | Hermetic job | Installed-wheel job |
| --- | --- | --- | --- | --- |
| 3.10.20 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904314015](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904314015) | [87904313801](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313801) |
| 3.11.15 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904314012](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904314012) | [87904313836](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313836) |
| 3.12.3 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904313756](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313756) | [87904313858](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313858) |
| 3.13.14 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904313755](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313755) | [87904313779](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313779) |
| 3.14.6 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87904313952](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313952) | [87904313869](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313869) |
| 3.12.10 | macOS 26.4, build 25E246 | `macos-26-arm64@20260715.0248.1` | [87904313795](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313795) | [87904313716](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904313716) |

Python patch versions for the Ubuntu 3.12 and macOS 3.12
installed-wheel jobs were not printed in those job logs; the table reuses the
matching hermetic-leg patch versions from the same workflow matrix cell. The
aggregate
[Required job](https://github.com/shinybrar/vosfs/actions/runs/29586387337/job/87904647449)
passed after quality, hermetic, and installed-wheel dependencies succeeded.

The executable evidence at that commit is the pinned
[`test_dirname.py`](https://github.com/shinybrar/vosfs/blob/f4faba8012689211f8a826065678bdf537a42056/src/fsspec-cli/tests/test_dirname.py),
[`test_dirname_process.py`](https://github.com/shinybrar/vosfs/blob/f4faba8012689211f8a826065678bdf537a42056/src/fsspec-cli/tests/test_dirname_process.py),
and
[`test_command_matrix.py::test_dirname_string_is_source_free`](https://github.com/shinybrar/vosfs/blob/f4faba8012689211f8a826065678bdf537a42056/src/fsspec-cli/tests/test_command_matrix.py)
(which covers the positive lexical success surface) plus
[`test_command_matrix.py::test_dirname_option_rejection_is_source_free`](https://github.com/shinybrar/vosfs/blob/f4faba8012689211f8a826065678bdf537a42056/src/fsspec-cli/tests/test_command_matrix.py)
for the negative preflight surface.

### H-2026-07-16-29525392759

This hermetic pull-request matrix observed `fsspec-cli` 0.1.0 at
[commit `c7c476f2f073e15e463bda779a619109a4a842b1`](https://github.com/shinybrar/vosfs/commit/c7c476f2f073e15e463bda779a619109a4a842b1)
with fsspec 2026.6.0, Typer 0.27.0, and vosfs 0.3.3. The complete resolved
dependency set is recoverable from the
[commit-pinned `uv.lock`](https://github.com/shinybrar/vosfs/blob/c7c476f2f073e15e463bda779a619109a4a842b1/uv.lock).

The run exercised adapted async Local and Memory sources through the
production `App` seam. Its negative `ls -l` case completed during command
preflight without entering a source. The mocked VOS case exercised the native
async source form, but that observation is incomplete for the VOS matrix row
because the required live OpenCADC gate is absent; the row therefore remains
`unverified` with an evidence cell of `—`.

The gate ran from 2026-07-16T18:47:29Z through 2026-07-16T18:49:01Z in
[GitHub Actions run 29525392759](https://github.com/shinybrar/vosfs/actions/runs/29525392759).
Every leg used runner 2.335.1:

| Python | Operating system | Runner image | Image version | Provisioner version | Immutable job |
| --- | --- | --- | --- | --- | --- |
| 3.10.20 | Ubuntu 24.04.4 LTS | `ubuntu-24.04` | `20260714.240.1` | `20260707.563` | [87712449662](https://github.com/shinybrar/vosfs/actions/runs/29525392759/job/87712449662) |
| 3.11.15 | Ubuntu 24.04.4 LTS | `ubuntu-24.04` | `20260714.240.1` | `20260707.563` | [87712449679](https://github.com/shinybrar/vosfs/actions/runs/29525392759/job/87712449679) |
| 3.12.3 | Ubuntu 24.04.4 LTS | `ubuntu-24.04` | `20260714.240.1` | `20260707.563` | [87712449711](https://github.com/shinybrar/vosfs/actions/runs/29525392759/job/87712449711) |
| 3.13.14 | Ubuntu 24.04.4 LTS | `ubuntu-24.04` | `20260714.240.1` | `20260707.563` | [87712449685](https://github.com/shinybrar/vosfs/actions/runs/29525392759/job/87712449685) |
| 3.14.6 | Ubuntu 24.04.4 LTS | `ubuntu-24.04` | `20260714.240.1` | `20260707.563` | [87712449720](https://github.com/shinybrar/vosfs/actions/runs/29525392759/job/87712449720) |
| 3.12.10 | macOS 26.4, build 25E246 | `macos-26-arm64` | `20260630.0213.1` | `20260624.560` | [87712449708](https://github.com/shinybrar/vosfs/actions/runs/29525392759/job/87712449708) |

The executable evidence is the commit-pinned
[Local and Memory command matrix](https://github.com/shinybrar/vosfs/blob/c7c476f2f073e15e463bda779a619109a4a842b1/src/fsspec-cli/tests/test_command_matrix.py)
and
[mocked VOS command matrix](https://github.com/shinybrar/vosfs/blob/c7c476f2f073e15e463bda779a619109a4a842b1/src/fsspec-cli/tests/test_vosfs_command_matrix.py).
This run did not execute the command matrix against an isolated built wheel
and therefore does not complete the release-candidate gate in Section 9. Issue
[#105](https://github.com/shinybrar/vosfs/issues/105) adds that evidence; it
does not change these command classifications unless the isolated run
contradicts them.

### H-2026-07-16-29536484110

This successful exact-commit CI run observed `fsspec-cli` 0.1.0 at
[commit `8cbbfd8f8940f7f4a2f9ff31ea5a130c9b08270e`](https://github.com/shinybrar/vosfs/commit/8cbbfd8f8940f7f4a2f9ff31ea5a130c9b08270e)
with fsspec 2026.6.0, Typer 0.27.0, and vosfs 0.3.3. The complete dependency
set is recoverable from the
[commit-pinned `uv.lock`](https://github.com/shinybrar/vosfs/blob/8cbbfd8f8940f7f4a2f9ff31ea5a130c9b08270e/uv.lock).

[CI run 29536484110](https://github.com/shinybrar/vosfs/actions/runs/29536484110)
ran the production command matrix and the built-wheel gate on every supported
leg. The installed-wheel job built the member wheel and source distribution,
rebuilt the wheel from the source distribution, installed outside the
workspace with dependency checks, and exercised the same
`local / adapted async`, `memory / adapted async`, mocked
`vosfs / native async`, and source-free rejection contracts.

| Python | Operating system | Runner image | Hermetic job | Installed-wheel job |
| --- | --- | --- | --- | --- |
| 3.10.20 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87748922253](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922253) | [87748922139](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922139) |
| 3.11.15 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87748922153](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922153) | [87748922130](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922130) |
| 3.12.3 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87748922259](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922259) | [87748922173](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922173) |
| 3.13.14 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87748922293](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922293) | [87748922187](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922187) |
| 3.14.6 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87748922306](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922306) | [87748922183](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922183) |
| 3.12.10 | macOS 26.4 | `macos-26-arm64@20260630.0213.1` | [87748922260](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922260) | [87748922268](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87748922268) |

Every leg used runner 2.335.1. Ubuntu used provisioner `20260707.563`; macOS
used `20260624.560`. The aggregate
[Required job](https://github.com/shinybrar/vosfs/actions/runs/29536484110/job/87749288044)
passed only after the quality, hermetic, installed-wheel, and repository live
dependencies completed successfully.

### L-2026-07-16-29536609626

The trusted read-only live gate observed the same
[`8cbbfd8f8940f7f4a2f9ff31ea5a130c9b08270e`](https://github.com/shinybrar/vosfs/commit/8cbbfd8f8940f7f4a2f9ff31ea5a130c9b08270e)
build at 2026-07-16T21:35:45Z in OpenCADC staging. It installed exact isolated
`fsspec-cli` 0.1.0 and `vosfs` 0.3.3 wheels with fsspec 2026.6.0 and Typer
0.27.0 on Python 3.12.3, then observed one successful
`vosfs / native async` plain-`ls` call with `_info` followed by
`_ls(detail=False)`, nonempty valid output, empty stderr, and awaited cleanup.

[Live run 29536609626](https://github.com/shinybrar/vosfs/actions/runs/29536609626)
recorded classification `pass` against exact
[CI run 29536484110](https://github.com/shinybrar/vosfs/actions/runs/29536484110).
Its sanitized evidence artifact is
[`fsspec-cli-live-evidence-8cbbfd8f8940f7f4a2f9ff31ea5a130c9b08270e`](https://github.com/shinybrar/vosfs/actions/runs/29536609626/artifacts/8390767659)
with digest
`sha256:246547411d8397722c161ba22c829bf107374d697e49681950315526856bc7df`.

### H-2026-07-17-29565052441

This successful exact-commit CI run observed `fsspec-cli` 0.1.1 at
[commit `7112811ae3a0e632d7d302c9f64d162be2052d61`](https://github.com/shinybrar/vosfs/commit/7112811ae3a0e632d7d302c9f64d162be2052d61)
with fsspec 2026.6.0, Typer 0.27.0, and vosfs 0.4.0. The complete dependency
set is recoverable from the
[commit-pinned `uv.lock`](https://github.com/shinybrar/vosfs/blob/7112811ae3a0e632d7d302c9f64d162be2052d61/uv.lock).

[CI run 29565052441](https://github.com/shinybrar/vosfs/actions/runs/29565052441)
ran from 2026-07-17T08:02:31Z through 2026-07-17T08:04:42Z. It exercised the
base `mkdir` production command matrix and the built-wheel gate on every
supported leg. Adapted Local passed the locked positive gates, including
missing-parent rejection. Adapted Memory reached contradiction:
`_mkdir(..., create_parents=False)` for a missing parent still created the
parent and child, so that row is `fail`. Mocked native `vosfs` passed the
hermetic mkdir gates. Source-free `-p` rejection completed during command
preflight without entering a source.

| Python | Operating system | Runner image | Hermetic job | Installed-wheel job |
| --- | --- | --- | --- | --- |
| 3.10.20 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87835687205](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687205) | [87835687165](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687165) |
| 3.11.15 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87835687101](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687101) | [87835687156](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687156) |
| 3.12.3 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87835687032](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687032) | [87835687224](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687224) |
| 3.13.14 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87835687115](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687115) | [87835687094](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687094) |
| 3.14.6 | Ubuntu 24.04.4 LTS | `ubuntu-24.04@20260714.240.1` | [87835687079](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687079) | [87835687249](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687249) |
| 3.12.10 | macOS 26.4 | `macos-26-arm64@20260715.0248.1` | [87835687095](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687095) | [87835687120](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87835687120) |

Every leg used runner 2.335.1 and provisioner `20260707.563`. The aggregate
[Required job](https://github.com/shinybrar/vosfs/actions/runs/29565052441/job/87836052810)
passed only after the quality, hermetic, and installed-wheel dependencies
completed successfully. The executable evidence is the commit-pinned
[Local and Memory command matrix](https://github.com/shinybrar/vosfs/blob/7112811ae3a0e632d7d302c9f64d162be2052d61/src/fsspec-cli/tests/test_command_matrix.py)
and
[mocked VOS command matrix](https://github.com/shinybrar/vosfs/blob/7112811ae3a0e632d7d302c9f64d162be2052d61/src/fsspec-cli/tests/test_vosfs_command_matrix.py).

## 9. CI and release policy

Pull requests run the hermetic gates for every affected required row. A
reached-command contract failure blocks the pull request. The credentialed
live gate remains absent from untrusted pull-request execution.

Trusted default-branch or manual workflows run the narrow live gate. A
`fsspec-cli` release candidate MUST, on its exact candidate build:

1. build the independent workspace member's wheel;
2. install and test that wheel in isolation;
3. pass every required hermetic positive and negative row;
4. obtain fresh required live `vosfs` evidence; and
5. contain no required `fail` or `unverified` row.

The release policy applies to the independent `fsspec-cli` release and tag,
beginning with `fsspec-cli-v0.1.0`. It publishes to GitHub Releases only. It
does not publish to PyPI and does not force `vosfs` and `fsspec-cli` to release
on the same schedule.

A new `vosfs` release does not wait for an `fsspec-cli` release. `fsspec-cli`
adopts and claims a new `vosfs` version only after rerunning its own required
matrix gates.

## 10. Maintenance rules

- Add or change a matrix row in the same change that adds its qualifying
  evidence or explicitly records it as `unverified`.
- Never infer support from backend metadata, inheritance, protocol name, or a
  different command's result.
- Never convert a failure or absence into `unsupported` merely to make a gate
  green.
- Preserve exact native-versus-adapted source wording in every claim.
- Keep the current matrix small; untested backends need no speculative rows.
- Treat downstream consumption as documentation, not a stable machine API.

## Rejected alternatives

### Machine-readable source plus generated Markdown

This would allow schema validation and downstream automation, but v1 has no
real machine consumer and only four required rows. It would create a public
shape, generator, validation code, and migration burden before the production
tracer exists.

### One backend-column table

This is compact for positive backend tests but falsely encourages copying
source-independent rejection into backend cells. Row scope states exactly
whether a source was entered.

### Backend-declared capability registry

Backend metadata cannot prove the consumed call shape, async lifecycle,
observable output, or error behavior. Only executed profile evidence can
establish a matrix status.

## Implementation handoff

[Create the production plain-`ls` tracer and independent package skeleton](https://github.com/shinybrar/vosfs/issues/83)
owns the first executable matrix transition. It should begin with these rows
as `unverified`, land the hermetic and isolated-wheel evidence, obtain the
trusted live observation, and update only the cells whose complete gates
qualify them.
