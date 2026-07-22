# `fsspec-cli` guarded recursive `rm` command profile

<!-- pyml disable line-length -->

Status: **Locked implementation contract; production evidence unverified**

Research: [#284](https://github.com/shinybrar/vosfs/issues/284)
Implementation frontier: [#288](https://github.com/shinybrar/vosfs/issues/288)

Client baseline: **fsspec 2026.6.0**, **fsspec-cli 0.4.0**, **vosfs 0.5.0**

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Verdict and scope

Guarded recursive removal is feasible as an application-capability-gated core
command. `capabilities.recursion.remove` defaults to `false`. While false,
`rm -R` and `rm -r` retain the exact source-free rejection in the
[recursive rejection profile](fsspec-cli-rm-recursive-rejection-profile.md).
`fsspec-cli` 0.4.0 implements only that rejection; this document adds no
production behavior.

Setting `capabilities.recursion.remove` to `true` is the embedding host's
assertion that **every target in the configured source mapping** satisfies this
profile. The assertion is snapshotted at `App` construction. It is not a
per-source setting, runtime registry, protocol negotiation, or interactive
probe. Production command code MUST NOT inspect a filesystem class, wrapper
class, protocol string, module, or other backend identity to accept, reject, or
vary recursive behavior.

With the capability enabled, the embedded command library derives the operation
from a complete pre-mutation manifest and sequential leaves-first primitive
removals. It consumes only `_info`, `_ls(detail=True)`, `_rm_file`, and `_rmdir`.
It never delegates to `_rm(path, recursive=True)`.

The command compatibility surface covers source-reported directory operands:

```text
rm -R [options] [--] name:/directory...
rm -r [options] [--] name:/directory...
```

`-R` and `-r` are equivalent. Non-recursive file operands continue to use base
`rm`; a source-reported non-directory passed with `-R` or `-r` fails before
mutation as `not a directory`. This is not a POSIX, GNU, BSD, all-fsspec, or
all-backend recursive-removal claim. Compatibility remains command-,
source-form-, version-, and host-qualification-specific.

## 2. Capability and source-free preflight

When `capabilities.recursion.remove` is false or omitted, the first `-R` or
`-r` option is rejected exactly as the current recursive rejection profile
requires: status `2`, empty stdout, one unsupported-option diagnostic, zero
source factories, and zero filesystem work.

When it is true, short-option tokens before the first operand MAY group only
`R`, `r`, `f`, and `v`. At least one `R` or `r` is REQUIRED; repeated `R`, `r`,
and `f` characters are idempotent. `v` MAY occur once. `--` ends option parsing.
Options after the first operand, a second `v`, and every other option character
or long option are unsupported.

`-f` preserves its existing zero-operand behavior: an invocation containing
`f` and no operands succeeds without source entry or output. Without `f`, zero
operands produce `rm: missing mapped filesystem operand` and status `2`.

Before any source factory call, context entry, filesystem call, or output, the
command MUST validate the complete argv using the base `rm` mapped-operand,
known-source, option, and first-error rules. It MUST additionally reject:

- the configured-source root after removing trailing slash characters;
- any path component exactly `.` or `..`, including `/.` and `/..`; and
- any path whose trailing-slash-normalized form is empty.

The exact diagnostic remains:

```text
rm: <mapped operand>: rejected path
```

These guards inspect the filesystem path after the `name:` selector. They do
not URL-decode it or apply host path resolution. A failed whole-argv preflight
returns status `2`, empty stdout, one diagnostic, zero source factories, and
zero mutation.

## 3. Host qualification and required operations

Source ownership, validation, acquisition order, cleanup, diagnostics, and
ordinary failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).
Every distinct referenced source is acquired before any filesystem work;
operands then run sequentially in argv order.

The host assertion covers every configured target and all four required
operations. For each target, the host asserts that:

1. `_info` and `_ls(detail=True)` expose stable string names, `file` or
   `directory` types, and truthful boolean `islink` data when links can exist;
2. each selected tree is finite, each `_ls(detail=True)` result is finite and
   complete for its immediate children, and a missing path is distinguishable
   as `FileNotFoundError`;
3. `_rm_file(path)` removes only the named non-directory entry, `_rmdir(path)`
   removes only the named empty directory, and neither primitive recursively
   expands, follows a link target, or removes an unlisted descendant; and
4. the target's path and concurrency semantics are strong enough that manifest
   admission authorizes later primitive calls, or the host supplies the
   isolation and containment needed to make that assertion true.

The implementation MUST await only `_info`, `_ls(detail=True)`, `_rm_file`, and
`_rmdir`. It MUST NOT call `_rm`, `_find`, `_walk`, a public synchronous facade,
a backend-specific recursive-delete API, or a capability probe. Operation or
result failures are classified by the generic rules below. No branch may
identify Local, Memory, vosfs, native async, or adapted async at runtime.

Raw synchronous filesystems, wrong-mode async filesystems, and host-owned
reusable instances remain rejected by source validation. Enabling the
capability does not weaken that lifecycle contract.

## 4. Bounded complete pre-mutation manifest

Each operand is independently planned immediately before its mutation. A plan
begins with `_info(root)` and requires a mapping whose `name`, after removing
trailing slashes, is the requested root. If `islink` is present it MUST be
boolean. `islink is True` rejects the root as `unsupported operation` before
`_ls` or mutation, regardless of its reported `type`. Only after this link
guard may the command require `type == "directory"`. Pre-mutation
`FileNotFoundError` is `not found`, or a successful no-op under `-f` with no
verbose line.

The command recursively calls `_ls(path, detail=True)` for directories and
MUST finish and validate the complete manifest before issuing the first
mutation for that operand. Every result MUST satisfy all these conditions:

1. `_ls` returns a finite list of mappings. Each mapping has a string `name`
   and a `type` equal to `file` or `directory`.
2. An `islink` field, when present, is boolean. `islink is True`, any other
   `type`, and every special entry reject the operand before traversal below
   that entry or mutation as `unsupported operation`.
3. A listed child is exactly one immediate, non-empty child of the directory
   being listed. Its final component is neither `.` nor `..`.
4. Every candidate is either the requested root or starts with the exact
   `root + "/"` prefix. Shared prefixes such as `/a` and `/ab` do not match.
5. Names are globally unique within the operand plan. Duplicate, cyclic, self,
   non-immediate, malformed, or out-of-tree entries are `incompatible result`.

The command never lists a link or special entry and never operates on its
target. Immediate children are sorted by full path using Python string order;
each directory is recorded after its sorted descendants. The resulting
postorder manifest is deterministic and leaves-first.

The host's finite-tree assertion and the uniqueness checks make planning
bounded by the admitted manifest. For a manifest containing `D` directories
including the root and `F` files, planning performs one root `_info` and exactly
`D` `_ls(detail=True)` calls, retains `D + F` entries, and performs zero
mutation. Mutation then schedules at most `D + F` primitive calls and the same
number of absence checks, stopping earlier on failure or control flow. No
source-owned recursive composite or unbounded result stream is consumed.

### 4.1 Containment proof and concurrency boundary

Root and dot-component rejection completes source-free. Root-link rejection
completes after one `_info` and before `_ls`. Manifest construction accepts only
an exact immediate-child edge whose candidate also satisfies the exact
root-prefix predicate. Induction over those accepted edges makes every manifest
member equal to or below the selected root. Before every mutation, the command
reasserts that the candidate is the next exact manifest member and still
satisfies the root-prefix predicate. No mutation call exists on a path that
failed either proof.

This is a lexical and observed-metadata proof, not an atomic namespace lock.
Concurrent replacement, renaming, addition, or removal can invalidate the
manifest. The host capability assertion owns the target-specific protection
needed between observation and mutation. The command still detects and reports
observable mutation or absence-proof failures, but it cannot manufacture an
identity-bound delete from the four generic operations.

A manifest failure performs no mutation for that operand. Ordinary failure does
not stop later operands, matching base `rm` ordering.

## 5. Leaves-first mutation and final absence proof

After manifest admission, the command processes one entry at a time:

- `file` calls `_rm_file(path)` once;
- `directory` calls `_rmdir(path)` once; and
- each successful primitive is followed by `_info(path)`, where only a
  distinguishable `FileNotFoundError` confirms absence.

The root directory is last, so its post-removal `_info` is the REQUIRED final
absence proof for the selected tree. A concurrently added descendant must make
the non-recursive `_rmdir` fail rather than delete an unplanned node; that
requirement is part of the host assertion.

Once the first mutation call begins, any primitive failure, non-not-found
post-check result, post-check permission/service/parse failure, or failed root
absence proof stops that operand and emits:

```text
rm: <mapped operand>: recursive removal incomplete; residue possible
```

The command MUST NOT claim which unverified descendants remain. Confirmed
earlier removals stay removed; there is no rollback. Later operands still run
unless output or escaping control flow stops the invocation.

## 6. Force, verbose, and multi-operand behavior

- `-f` suppresses only a root `FileNotFoundError` found before that operand's
  mutation. It does not override disabled-capability rejection or suppress
  manifest, containment, link, special-entry, permission, mutation,
  verification, output, or cleanup failures.
- `-v` writes the exact original mapped operand spelling plus one newline only
  after the final root absence proof. It writes no descendant lines, no line
  for a force-suppressed missing root, and no line for a partial failure.
- Every source is acquired before command work. Operand manifests, mutations,
  diagnostics, and verbose lines retain argv order. Repeated and overlapping
  operands are reevaluated when reached; no result is borrowed from an earlier
  operand.
- A verbose stdout failure after confirmed removal retains that removal, stops
  later mutation and output, runs cleanup, and follows the existing `rm -v`
  output-failure contract.

Successful silent invocations emit no stdout. Status `0` requires every operand
to be absent or force-suppressed as pre-mutation missing, all requested verbose
output to complete, and cleanup to succeed.

## 7. Cancellation, cleanup, and residue truth

The command owns at most one in-flight filesystem-hook task. On escaping
`BaseException` control flow, it stops scheduling new work, shields and drains
that current hook before source cleanup, and then propagates the original
control-flow object unchanged. No post-check, later manifest entry, later
operand, diagnostic, or normal output is scheduled after interruption. This is
one backend-neutral recursive-`rm` delta from ADR 0003's tree-only drain
exception, not a backend worker, timeout, retry, or concurrent deletion policy.

The observable state rules are:

- interruption before the first mutation leaves that operand unchanged by the
  command;
- interruption during a descendant primitive or its absence check can leave
  any already confirmed removals removed and the rest present or uncertain;
- interruption during the root primitive or final absence check means final
  absence was not proved and residue MUST be treated as possible; and
- interruption after the final absence proof does not undo the confirmed
  removal.

Control flow is not converted into status `1` or a fabricated residue
diagnostic. The exception itself communicates interruption; unless final
absence was already proved, the caller must treat the selected tree as possibly
partial. If the current hook never settles, cleanup and propagation may not
complete; no timeout is promised.

Every entered source receives reverse-order cleanup after the current hook has
settled. Cleanup cannot suppress or replace command failure or escaping control
flow. Ordinary cleanup failures retain earlier output and diagnostics and force
status `1`. Cleanup itself remains unshielded and has no timeout, as ADR 0003
specifies.

Outcome precedence is:

| Outcome | Result |
| --- | --- |
| Whole-argv preflight failure | Status `2`; no source lifecycle or mutation. |
| Escaping `BaseException` control flow | Drain current hook, cleanup, then propagate unchanged. |
| Any ordinary manifest, mutation, verification, output, or cleanup failure | Status `1`; all applicable ordered diagnostics retained. |
| No failure | Status `0`. |

## 8. Explicit non-guarantees

Recursive removal is sequential and non-atomic. It promises no interactive
confirmation, transaction, rollback, atomicity, namespace lock, retry, trash,
recovery, dry run, JSON/YAML result, progress stream, cross-command
orchestration, or `/async-delete` use. A host that needs recovery, stronger
coordination, or identity-bound deletion must provide it outside this command.

## 9. Qualification evidence, not runtime classification

Inspection baseline:
[`4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f`](https://github.com/shinybrar/vosfs/commit/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f),
whose commit-pinned
[`uv.lock`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/uv.lock)
resolves fsspec 2026.6.0, Typer 0.27.0, fsspec-cli 0.4.0, and vosfs 0.5.0.
The fsspec 2026.6.0 annotated tag resolves to immutable commit
[`a2457004d03e0312f715f90f58873de5ab195a37`](https://github.com/fsspec/filesystem_spec/commit/a2457004d03e0312f715f90f58873de5ab195a37).

These rows inform an embedding host and the tested command matrix. They do not
select production behavior, qualify an entire backend, or establish a generic
fsspec guarantee.

| Source form | Immutable source evidence | Research disposition |
| --- | --- | --- |
| `memory / adapted async`: isolated `MemoryFileSystem`, then `AsyncFileSystemWrapper(..., asynchronous=True)` | Memory lists immediate file or pseudo-directory entries and reports only those two types ([listing](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L43-L105), [info/rmdir](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L136-L169)). Inherited `rm_file` calls Memory's single-key `_rm` ([base primitive](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L1243-L1250), [Memory delete](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L234-L239)). | Intended positive. Isolated state, generic manifest behavior, and hook draining require qualifying #288 tests before `pass`. |
| `vosfs / native async`: fresh `VOSpaceFileSystem(asynchronous=True, skip_instance_cache=True)` with awaited `aclose()` | DataNode, ContainerNode, and LinkNode map to `file`, `directory`, and `other` plus `islink=True` ([mapping](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/nodes.py#L165-L188)). `_info` and `_ls` expose node metadata and immediate children ([metadata/listing](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L341-L379)); `_rm_file` and `_rmdir` are guarded single-node primitives ([removal](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L1239-L1257)). VOSpace ancestors must be ContainerNodes rather than followed LinkNodes ([VOSpace 2.1 sections 6.2.1 and 6.2.4](https://www.ivoa.net/documents/VOSpace/20180620/REC-VOSpace-2.1.html)). | Intended positive for this OpenCADC source form. Mocked transport qualification is still required; no generic VOSpace claim follows. |
| `local / adapted async`: `AsyncFileSystemWrapper(LocalFileSystem(skip_instance_cache=True), asynchronous=True)` | Direct Local info follows a link for target type while retaining `islink=True`; `rm_file` is path-based `os.remove` ([info](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L78-L127), [rm_file](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L185-L189)). A 2026-07-21 probe on CPython 3.13.5 and macOS 15.7.7 built a manifest, renamed a selected descendant ancestor, replaced it with a symlink to an outside directory, and observed the manifest-path `_rm_file` delete the outside file. Replacing the selected root has the same path-based failure mode. No pinned primitive atomically binds removal to the observed root and ancestor identities. | Unverified. The command does not reject Local. A host enabling the capability with Local owns any exclusive-namespace, OS-containment, or equivalent guarantee and the residual risk. |

Root-link rejection is still required for every qualified target. It blocks a
link already visible to `_info`; it cannot close the documented Local
root/ancestor replacement race between manifest observation and mutation.

## 10. Hermetic and isolated-wheel strategy

Issue #288 MUST keep the new rows `unverified` until its exact implementation
commit supplies qualifying gates. The gate must cover:

1. App-seam tests proving the capability defaults false, the current recursive
   rejection stays source-free, true is snapshotted application policy, and no
   backend or wrapper identity affects dispatch;
2. source-free tests for both aliases, groups, `-f`/`-v` composition, zero
   operands, roots, every-position dot segments, malformed or unknown mapped
   operands, and zero factory or mutation on whole-argv rejection;
3. generic recording-source tests proving root-link rejection, the finite
   complete manifest and exact call bounds, deterministic leaves-first calls,
   containment, duplicate/cyclic/malformed results, final absence, ordinary
   partial failure, later-operand continuation, verbose output failure, cleanup
   precedence, and cancellation at every hook boundary;
4. deterministic adapted Memory tests and a fully mocked native vosfs transport
   covering their intended-positive rows, including links and special nodes,
   concurrent disappearance or addition, partial DELETE success, final
   absence, drained cancellation, and zero `/async-delete` calls;
5. a Local root/ancestor symlink-swap regression fixture retained as evidence
   for the `unverified` row, without runtime Local detection or a Local-specific
   command result; and
6. the installed-wheel gate rebuilt from the fsspec-cli source distribution and
   run outside the workspace with exact fsspec-cli and vosfs wheels on Python
   3.10-3.14 under Linux and Python 3.12 under macOS.

The qualifying CI record MUST cite its exact commit, lockfile, package versions,
native or adapted source forms, Python versions, runner images, observation
time, test paths, and immutable jobs. No production recursive removal, help,
README, changelog, or release claim is added by #284.
