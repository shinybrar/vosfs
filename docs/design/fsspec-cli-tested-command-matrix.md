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
| Scope | `source` when filesystem behavior is exercised; `command preflight` when rejection completes before source entry. |
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

Until qualifying source-form gates exist, every cell remains `unverified`.
The narrow production tracer and prototype research do not qualify as
backend-specific evidence on their own.

| Command profile | Scope | Source form | Current status | Required status for `fsspec-cli` 0.1.0 | Required gates | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| [Plain `ls`](fsspec-cli-plain-ls-command-profile.md) | source | `local / adapted async` | `unverified` | `pass` | Hermetic | — |
| [Plain `ls`](fsspec-cli-plain-ls-command-profile.md) | source | `memory / adapted async` | `unverified` | `pass` | Hermetic | — |
| [Plain `ls`](fsspec-cli-plain-ls-command-profile.md) | source | `vosfs / native async` | `unverified` | `pass` | Hermetic and live OpenCADC | — |
| [`ls -l` strict rejection](fsspec-cli-ls-long-rejection-profile.md) | command preflight | `not entered` | `unverified` | `unsupported` | Hermetic negative rejection | — |

Other backends and source forms remain implicitly `unverified`. They do not
block the first release because they are not required release rows.

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
