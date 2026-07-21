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

Guarded recursive removal is admitted as a **client-derived capability** for
these exact **async filesystem source** forms:

- `local / adapted async`;
- `memory / adapted async`; and
- `vosfs / native async`.

The **embedded command library** may implement this profile through a complete
pre-mutation manifest followed by sequential leaves-first primitive removals.
It MUST NOT delegate to `_rm(path, recursive=True)`. This decision replaces the
source-owned-composite admission bar in the
[recursive rejection profile](fsspec-cli-rm-recursive-rejection-profile.md),
but it does not add production behavior. `fsspec-cli` 0.4.0 keeps its
source-free rejection until #288 implements this contract and qualifies the
currently `unverified` **tested command matrix** rows.

The command compatibility profile covers source-reported directory operands:

```text
rm -R [options] [--] name:/directory...
rm -r [options] [--] name:/directory...
```

`-R` and `-r` are equivalent. This is not a POSIX, GNU, BSD, or generic fsspec
recursive-removal claim. File operands continue to use base `rm`; a
source-reported non-directory passed to this profile fails before mutation as
`not a directory`.

## 2. Source-free preflight

Short-option tokens before the first operand MAY group only `R`, `r`, `f`, and
`v`. At least one `R` or `r` is REQUIRED; repeated `R`, `r`, and `f` characters
are idempotent. `v` MAY occur once. `--` ends option parsing. Options after the
first operand, a second `v`, and every other option character or long option
are unsupported.

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

## 3. Source acquisition and required operations

Source ownership, validation, acquisition order, cleanup, diagnostics, and
ordinary failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).
Every distinct referenced source is acquired before any filesystem work;
operands then run sequentially in argv order.

The implementation MUST consume only awaited `_info`, `_ls(detail=True)`,
`_rm_file`, and `_rmdir` operations. It MUST NOT call `_rm`, `_find`, `_walk`,
a public synchronous facade, a backend-specific recursive-delete API, or an
interactive capability probe. It MUST NOT branch on backend class or protocol.

Raw synchronous Local and Memory instances, wrong-mode async filesystems, and
host-owned reusable instances remain rejected by source validation. Other
valid async source forms remain `unverified`, not implicitly supported. The
tested command matrix is evidence only; it MUST NOT become a runtime registry.

## 4. Complete pre-mutation manifest

Each operand is independently planned immediately before its mutation. A plan
begins with `_info(root)` and requires a mapping whose `name`, after removing
trailing slashes, is the requested root and whose `type` is `directory`.
Pre-mutation `FileNotFoundError` is `not found`, or a successful no-op under
`-f` with no verbose line.

The command recursively calls `_ls(path, detail=True)` for directories and
MUST finish and validate the complete manifest before issuing the first
mutation for that operand. Every result MUST satisfy all these conditions:

1. `_ls` returns a list of mappings. Each mapping has string `name` and a
   `type` equal to `file` or `directory`.
2. An `islink` field, when present, is boolean. `islink is True`, `type` other
   than `file` or `directory`, and every special descendant reject the operand
   before mutation as `unsupported operation`.
3. A listed child is exactly one immediate, non-empty child of the directory
   being listed. Its final component is neither `.` nor `..`.
4. Every candidate is either the requested root or starts with the exact
   `root + "/"` prefix. Shared prefixes such as `/a` and `/ab` do not match.
5. Names are unique. Duplicate, cyclic, self, non-immediate, malformed, or
   out-of-tree entries are `incompatible result`.

The command never lists a link or special entry and never operates on its
target. Immediate children are sorted by full path using Python string order;
each directory is recorded after its sorted descendants. The resulting
postorder manifest is deterministic and leaves-first.

### 4.1 Containment proof

Root and dot-component rejection completes source-free. Manifest construction
accepts only an exact immediate-child edge whose candidate also satisfies the
exact root-prefix predicate. Induction over those accepted edges makes every
manifest member equal to or below the selected root. Before every mutation,
the command reasserts both that the candidate is the next exact manifest member
and that the root-prefix predicate still holds. No mutation call exists on a
path that failed either proof.

A manifest or classification failure performs no mutation for that operand.
Ordinary failure does not stop later operands, matching base `rm` ordering.

## 5. Leaves-first mutation and absence proof

After manifest admission, the command processes one entry at a time:

- `file` calls `_rm_file(path)` once;
- `directory` calls `_rmdir(path)` once; and
- each successful primitive is followed by `_info(path)`, where only a
  distinguishable `FileNotFoundError` confirms absence.

The root directory is last, so its post-removal `_info` is the REQUIRED final
absence verification for the selected tree. A concurrently added descendant
makes `_rmdir` fail rather than recursively deleting an unplanned node.

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
  mutation. It does not suppress manifest, containment, link, special-entry,
  permission, mutation, verification, output, or cleanup failures.
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

Successful silent invocations emit no stdout. Status `0` requires every
operand to be absent or force-suppressed as pre-mutation missing, all requested
verbose output to complete, and cleanup to succeed.

## 7. Cancellation, cleanup, and precedence

Cancellation before the first mutation for an operand leaves that operand
unchanged. Cancellation after mutation begins stops scheduling new manifest
entries and leaves already completed removals in place.

Adapted Local and Memory hooks run in `asyncio.to_thread`. Therefore #288 MUST
use one narrow recursive-`rm` shield-and-drain boundary around the current
mutation hook: if the invocation receives escaping `BaseException` control
flow, it drains that one in-flight hook before source cleanup, schedules no
post-check or later mutation, and then propagates the original control-flow
object unchanged. This is not a general background runner, timeout, retry, or
concurrent deletion policy. It is an explicit recursive-`rm` delta from ADR
0003's tree-only drain exception.

Cancellation during final absence verification or after verified completion
also propagates unchanged after cleanup. If final absence was not proved, the
caller MUST treat residue as possible; the command produces no success status
or fabricated diagnostic while control flow escapes. If absence was already
proved, cancellation does not undo it.

Every entered source receives reverse-order cleanup after the current mutation
has settled. Cleanup cannot suppress or replace command failure or escaping
control flow. Ordinary cleanup failures retain earlier output and diagnostics
and force status `1`. Cleanup itself remains unshielded and has no timeout, as
ADR 0003 specifies.

Outcome precedence is:

| Outcome | Result |
| --- | --- |
| Whole-argv preflight failure | Status `2`; no source lifecycle or mutation. |
| Escaping `BaseException` control flow | Drain current mutation, cleanup, then propagate unchanged. |
| Any ordinary manifest, mutation, verification, output, or cleanup failure | Status `1`; all applicable ordered diagnostics retained. |
| No failure | Status `0`. |

## 8. Explicit non-guarantees

Recursive removal is sequential, non-atomic, and vulnerable to concurrent
namespace changes. It promises no interactive confirmation, transaction,
rollback, atomicity, retry, trash, recovery, dry run, JSON/YAML result, progress
stream, cross-command orchestration, or `/async-delete` use. A host that needs
recovery or stronger coordination must provide it outside this command.

## 9. Exact admission evidence

Inspection baseline:
[`4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f`](https://github.com/shinybrar/vosfs/commit/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f),
whose commit-pinned
[`uv.lock`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/uv.lock)
resolves fsspec 2026.6.0, Typer 0.27.0, fsspec-cli 0.4.0, and vosfs 0.5.0.
The fsspec 2026.6.0 annotated tag resolves to immutable commit
[`a2457004d03e0312f715f90f58873de5ab195a37`](https://github.com/fsspec/filesystem_spec/commit/a2457004d03e0312f715f90f58873de5ab195a37).

Local inspection and a throwaway admission probe ran on 2026-07-21 with CPython
3.13.5 on macOS 15.7.7 arm64. The probe used the exact Local and Memory source
forms below, built the complete immediate-child manifest, rejected a Local
directory symlink and FIFO before mutation, preserved the symlink's external
target, removed admitted trees leaves-first, and observed
`FileNotFoundError` for each removed root. This is admission research, not a
qualifying command-matrix run.

| Source form | Immutable source evidence | Admission result |
| --- | --- | --- |
| `local / adapted async`: `AsyncFileSystemWrapper(LocalFileSystem(skip_instance_cache=True), asynchronous=True)` | The wrapper installs awaited thread adapters for public methods ([wrapper](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py#L81-L97)). Local immediate listing and info expose `islink` and classify non-followed directory entries ([listing/info](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L59-L127)); `rmdir` and `rm_file` are single-entry primitives ([rmdir](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L55-L57), [rm_file](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L185-L189)). | Admitted with manifest link/special rejection and mutation draining. |
| `memory / adapted async`: isolated `MemoryFileSystem`, then `AsyncFileSystemWrapper(..., asynchronous=True)` | Memory lists only immediate file or pseudo-directory entries and reports only those two types ([listing](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L43-L105), [info/rmdir](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L136-L169)). Inherited `rm_file` calls Memory's single-key `_rm` ([base primitive](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L1243-L1250), [Memory delete](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L234-L239)). | Admitted with isolated state and mutation draining. |
| `vosfs / native async`: fresh `VOSpaceFileSystem(asynchronous=True, skip_instance_cache=True)` with awaited `aclose()` | At the pinned vosfs commit, DataNode, ContainerNode, and LinkNode map to `file`, `directory`, and `other` plus `islink=True` respectively ([mapping](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/nodes.py#L165-L188)). `_info` and `_ls` expose node metadata and immediate children ([metadata/listing](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L341-L379)); `_rm_file` and `_rmdir` are guarded single-node primitives ([removal](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L1239-L1257)). Hermetic namespace tests prove leaves-first single-node deletion, malformed-child containment rejection before DELETE, and partial completion reporting ([tests](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/tests/test_namespace.py#L225-L328)). | Admitted for the OpenCADC VOSpace profile; no generic VOSpace claim. |

The guarded CLI algorithm is necessary even though native vosfs already has a
recursive helper: Local and Memory recursive composites delegate or expand
differently, and none gives this command one cross-source manifest, diagnostic,
verbose, cancellation, and final-absence contract.

## 10. Hermetic and isolated-wheel strategy

Issue #288 MUST keep all four new matrix rows `unverified` until the exact
implementation commit passes:

1. source-free App-seam tests for both aliases, groups, `-f`/`-v` composition,
   zero operands, roots, every-position dot segments, malformed/unknown mapped
   operands, and zero factory/mutation on whole-argv rejection;
2. recording-source tests for complete-manifest-before-mutation, deterministic
   leaves-first calls, strict containment, duplicate/cyclic/malformed results,
   final absence, ordinary partial failure, later-operand continuation,
   verbose output failure, cleanup precedence, and cancellation at every
   boundary;
3. deterministic Local and isolated Memory tests, including an out-of-tree
   Local symlink, special entry, concurrent disappearance/addition, partial
   residue, and drained adapted-thread cancellation;
4. a fully mocked native vosfs transport covering DataNode, ContainerNode,
   LinkNode, malformed child URI, permission/service/parse failures, partial
   DELETE success, final absence, and zero `/async-delete` calls; and
5. the existing installed-wheel gate rebuilt from the fsspec-cli source
   distribution and run outside the workspace with exact fsspec-cli and vosfs
   wheels on Python 3.10-3.14 under Linux and Python 3.12 under macOS.

The qualifying CI record MUST cite its exact commit, lockfile, package
versions, native/adapted source forms, Python versions, runner images,
observation time, test paths, and immutable jobs. No production recursive
removal, help, README, changelog, or release claim is added by #284.
