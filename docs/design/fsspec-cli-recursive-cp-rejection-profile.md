# `fsspec-cli` verified two-operand recursive `cp` command profile

<!-- pyml disable line-length -->

Status: **Locked positive contract; production remains source-free rejected until issue #286**

Question: [Research verified recursive cp -R across mapped filesystem sources](https://github.com/shinybrar/vosfs/issues/283)

Implementation frontier: [Issue #286](https://github.com/shinybrar/vosfs/issues/286)

Upstream requirement: [CANFAR issue #150](https://github.com/opencadc/canfar/issues/150)

## 1. Verdict and scope

Verified two-operand recursive copy is feasible without fsspec's inherited
recursive `_copy` composite. The admitted command forms are:

```text
cp -R source_a:/directory source_b:/target
cp -r source_a:/directory source_b:/target
```

`-R` and `-r` are equivalent and exactly one MUST be present. Exactly two
[mapped filesystem operands](../../CONTEXT.md#mapped-filesystem-operand) are
required. Configured source names, not backend class, protocol, or object
identity, select same-source versus cross-source behavior. Every route uses the
same bounded source manifest, one-file host-local staging, and complete
source-entry verification. Same-source, Local-to-remote, remote-to-Local, and
remote-to-remote routes therefore have one observable contract.

This profile deliberately replaces the prior rejection decision. Production
MUST continue its current source-free rejection until issue #286 implements and
tests the whole contract. This issue adds no recursive-copy command code.

Multi-source copy, implicit or bare local operands, retries, transfer
concurrency, progress output, JSON/YAML output, ownership, mode, timestamp or
link preservation, and server-side transfer optimization remain out of scope.

## 2. Exact research tuple and evidence

Research ran from immutable source commit
[`4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f`](https://github.com/shinybrar/vosfs/commit/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f)
with fsspec 2026.6.0, Typer 0.27.0, `fsspec-cli` 0.4.0, `vosfs` 0.5.0,
CPython 3.13.5, and macOS 15.7.7 arm64. The complete dependency set is
recoverable from that commit's
[`uv.lock`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/uv.lock).
The local tuple was observed with:

```text
uv run --all-packages python -c <version-and-platform probe>
python=3.13.5
platform=macOS-15.7.7-arm64-arm-64bit-Mach-O
machine=arm64
fsspec=2026.6.0
fsspec-cli=0.4.0
vosfs=0.5.0
```

fsspec 2026.6.0 resolves to source commit
[`a2457004d03e0312f715f90f58873de5ab195a37`](https://github.com/fsspec/filesystem_spec/tree/a2457004d03e0312f715f90f58873de5ab195a37).
The admission rests on these version-pinned seams:

- fsspec's [`AsyncFileSystem._walk`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/asyn.py#L748-L800) exposes detailed directory and file rows; adapted sync methods run through [`AsyncFileSystemWrapper`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py#L11-L97).
- Local metadata reports `type`, `size`, and `islink`, distinguishing other filesystem types in [`LocalFileSystem.info`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L78-L127). Memory reports exact file/directory type and size in [`MemoryFileSystem.info`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L149-L169).
- The existing [`tree` walk adapter](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/fsspec-cli/src/fsspec_cli/_tree.py#L252-L327) proves the public `App` seam can consume both native async iterators and adapted awaitables resolving to sync iterators, while draining an adapted worker before source cleanup.
- Native `vosfs` supplies `_walk`, `_get_file`, `_put_file`, and `_mkdir` at the pinned source in [`filesystem.py`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py). Its [metadata mapping](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/nodes.py#L165-L198) exposes a LinkNode as `type == "other"` with `islink == True`.
- Existing two-operand [same-source](fsspec-cli-same-source-cp-command-profile.md) and [cross-source](fsspec-cli-cross-source-cp-command-profile.md) profiles supply the target resolution, secure staging, recognized-token, lifecycle, and residue floors. The pinned [Local/Memory matrix tests](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/fsspec-cli/tests/test_command_matrix.py) and [mocked native `vosfs` tests](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/fsspec-cli/tests/test_vosfs_command_matrix.py) establish those public-seam primitives.

These sources establish implementation feasibility, not passing recursive-copy
matrix evidence. Section 9 keeps every positive row `unverified` until the
complete implementation gate exists.

## 3. Source forms

The implementation frontier may qualify only the matrix's exact initial source
forms:

- `local / adapted async`:
  `AsyncFileSystemWrapper(LocalFileSystem(skip_instance_cache=True), asynchronous=True)`;
- `memory / adapted async`:
  `AsyncFileSystemWrapper(MemoryFileSystem(), asynchronous=True)` with isolated
  store, pseudo-directory, and instance-cache state; and
- `vosfs / native async`: a fresh
  `VOSpaceFileSystem(asynchronous=True, skip_instance_cache=True)` closed on
  the invocation loop.

All ordered pairs and both same-name and distinct-name routing require their
own matrix row. A missing row remains `unverified`, not unsupported. Raw sync,
wrong-mode async, and host-owned reusable instances remain unsupported by
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and MUST fail
source validation before filesystem work or mutation.

Method presence or inheritance does not admit another source form. A future
source form needs its own exact-version hermetic and isolated-wheel evidence.

## 4. Source-free preflight and lexical paths

Before any source factory call, context entry, temporary creation, filesystem
call, stdout byte, or mutation, the command MUST validate option syntax,
exactly two operands, every mapped-operand grammar, and every configured name.
`--` ends option parsing. Framework-owned `--help` is exempt.

For this profile, each backend path is canonicalized by splitting on `/`,
discarding empty segments, and joining the remaining segments beneath one
leading `/`. A literal `.` or `..` segment is rejected rather than resolved.
Percent-encoded text is opaque to the embedded command library. The canonical
source path `/` is rejected. A trailing or repeated slash otherwise has no
semantic effect.

| Condition | Exact category |
| --- | --- |
| Missing operand | `missing mapped filesystem operand` |
| Third operand | `extra operand` |
| Any option other than one `-R` or `-r` | `<option token>: unsupported option` |
| Malformed or unknown mapped operand | Existing mapped-operand category |
| Literal `.` or `..` path segment | `<mapped operand>: dot segment unsupported` |
| Canonical source root | `<mapped operand>: source root unsupported` |

Every row above exits `2`, writes empty stdout, emits exactly one
`cp: <category>` line on stderr, and enters zero sources.

## 5. Acquisition, source validation, and target resolution

After preflight, source acquisition follows
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md): distinct
configured names are acquired once in first-operand order before filesystem
work. A repeated name reuses its invocation-owned filesystem.

The command then:

1. awaits source `_info` and requires a non-link `type == "directory"`;
2. resolves the destination under the existing two-operand `cp` rules: an
   existing destination directory receives the source basename; an absent or
   non-directory destination names the resolved root directly;
3. requires the resolved root's parent to exist as a non-link directory;
4. rejects an existing resolved root unless it is a non-link directory; and
5. rejects an exact resolved source path or a resolved destination inside the
   source when both operands use the same configured name.

Distinct configured names remain cross-source even if their yielded objects,
classes, or protocols match. If two names yield the identical filesystem
object, the same exact-path and destination-inside-source guards also apply as
a safety check. Namespace aliasing through distinct filesystem instances is
host configuration outside the observable source seam and carries no
snapshot, isolation, or alias-detection claim.

Every failure in this section occurs before mutation. Existing destination
entries outside the resolved copied subtree are retained and excluded from the
success proof.

## 6. Frozen source manifest and destination preflight

The command MUST build one immutable pre-mutation source manifest through
`_walk(source, detail=True, on_error="raise")`. It MUST NOT call `_find`,
`_glob`, inherited `_copy`, public sync facades, or backend-specific methods.

The consumer accepts only a native async iterator or an awaitable resolving to
a sync iterator. It validates rows while consuming them and stops at a hard
limit of **10,000 total entries including the source root**. The sync-iterator
worker closes the iterator and terminates before the command continues or any
source exits. Limit overflow is a pre-mutation failure; no command option or
environment variable changes the limit.

Every manifest entry contains:

- a unique relative path, with `""` identifying the root;
- exactly `directory` or `file` type;
- a non-negative integer size for files; and
- immutable exact `str` or `bytes` recognized tokens under normalized
  `ETag` / `etag`, `md5`, `content-md5` / `content_md5`, and `checksum` names.

Each walk root and metadata `name` MUST equal its expected canonical path.
Rows MUST be unique, reachable from the requested root, and complete: every
reported directory has one row, including an empty directory. NUL, newline,
slash-bearing child names, duplicate normalized tokens, malformed row shapes,
missing fields, boolean sizes, and unreachable or missing rows are
`incompatible result`.

Any entry with `islink == True`, `type` other than `file` or `directory`, or a
Local-style `type` that reports a symlink or special filesystem object is
`unsupported entry type`. This rejects Local symlinks, `vosfs` internal and
external LinkNodes, FIFOs, sockets, devices, and unknown types before mutation.
No link is followed, recreated, or materialized as bytes.

Before mutation, the command awaits destination `_info` for every corresponding
manifest path. Absence is allowed. Existing directories may satisfy directory
entries, and existing files may be replaced by file entries. Any link, special
type, file/directory mismatch, or incompatible result fails before mutation.
This complete preflight permits merge into an existing destination directory;
unrelated destination entries remain untouched.

## 7. Mutation and complete success proof

Mutation is serial and deterministic:

1. create missing destination directories by depth then relative-path order
   through `_mkdir(path, create_parents=False)`;
2. process files in relative-path order;
3. for each file, create one secure host-local staging file, await source
   `_get_file`, close it, require its local byte size to equal the frozen size,
   await destination `_put_file(..., mode="overwrite")`, then remove the
   staging file before advancing; and
4. never call `_cp_file`, `_copy`, a destination download, a public sync
   facade, retry, concurrent transfer, or source deletion.

Same-source copies deliberately use the same host-local relay as cross-source
copies. At most one file's bytes occupy command-owned staging. Temporary paths
and source content never appear in diagnostics.

Before success, the command MUST:

1. build a second bounded source manifest and require exact agreement with the
   frozen relative-path, type, size, and recognized-token projection; and
2. await destination `_info` once for every frozen entry, require each relative
   path and type, require every file size, and require every recognized token
   shared by that frozen source entry and destination entry to match exactly.

No shared recognized token means exact path, type, and size are the truthful
file proof. It is not byte-identity or cryptographic proof. Changes invisible
to those fields are also invisible to this profile. No destination download is
performed.

Status `0` therefore proves source retention, a destination entry for every
frozen source entry, exact file sizes, preserved empty directories, agreement
of every shared recognized token, and no detected source-manifest change.
Pre-existing destination extras may remain. Success writes empty stdout and
empty stderr. The command claims no snapshot, transaction, rollback,
atomicity, characteristic preservation, or exact mirror.

## 8. Failure, residue, cancellation, and precedence

Command diagnostics have shape:

```text
cp: <mapped operand>: <stable category>
```

| Phase or condition | Stable category | Destination state |
| --- | --- | --- |
| Missing source or destination parent | `not found` | Unchanged |
| Permission failure before mutation | `permission denied` | Unchanged |
| Source not a directory | `not a directory` | Unchanged |
| Destination-inside-source guard | `destination is inside source` | Unchanged |
| Missing required coroutine or `NotImplementedError` before mutation | `unsupported operation` | Unchanged |
| Malformed walk or metadata result | `incompatible result` | Unchanged |
| Link or special entry | `unsupported entry type` | Unchanged |
| More than 10,000 source entries | `source tree exceeds 10000 entries` | Unchanged |
| Existing destination type conflict | `destination type conflict` | Unchanged |
| Staging creation or local stat failure | `staging failure (<class>); destination residue may remain` | Prior destination mutation may remain |
| Directory creation, download, or upload failure after first mutation | `mutation failure; destination residue may remain` | Prior or current residue may remain |
| Staged size mismatch | `source changed; destination residue may remain` | Prior or current residue may remain |
| Final source-manifest mismatch | `source changed; destination residue may remain` | Copied destination may remain |
| Destination proof mismatch | `verification failure; destination residue may remain` | Copied destination may remain |
| Staging cleanup failure without an earlier command failure | `staging cleanup failure (<class>); destination residue may remain` | Host staging and destination residue may remain |

All ordinary failures exit `1` and keep stdout empty. Acquisition failures use
ADR 0003's lifecycle diagnostics and may be followed by reverse-entry cleanup
diagnostics; they emit no command diagnostic. After successful acquisition,
the first ordinary command failure in deterministic operation order is primary
and stops new work. It emits exactly one command diagnostic. A staging-cleanup
failure does not mask or add to an existing command failure. Source-exit
diagnostics then follow in ADR 0003 reverse-entry order. No destination entry
is removed to simulate rollback.

Each walk materialization and each filesystem operation is owned by one
command task. On `CancelledError`, `KeyboardInterrupt`, `SystemExit`, or other
escaping `BaseException`, the command stops scheduling new work, drains the
current operation, removes the current staging file, begins source cleanup,
then propagates the original object unchanged. It emits no command diagnostic
and returns no numeric command status. Completed destination entries and a
current upload's residue may remain.

This required current-operation drain is a recursive-copy-specific exception
to ADR 0003's present tree-only worker rule. Issue #286 MUST update ADR 0003 in
the same implementation change; the positive command MUST NOT ship while the
documents conflict. There is no timeout or background worker after source
cleanup.

## 9. Matrix delta and implementation gate

The [tested command matrix](fsspec-cli-tested-command-matrix.md) replaces the
old command-preflight `unsupported` row with explicit same-name and
distinct-name rows for every ordered pair of the three initial source forms.
Every new row is `unverified`; research inspection is not qualifying command
evidence.

Issue #286 may promote a row only after hermetic tests through
`App(sources).typer_app` cover:

- both `-R` and `-r`, every route, target-resolution branch, existing-target
  merge and replacement, empty directories, source root, destination inside
  source, repeated/trailing slash, and dot-segment rejection;
- exact 10,000/10,001-entry boundaries; malformed, duplicate, unreachable,
  link, LinkNode, and special-type manifests; source changes before, during,
  and after transfer; and shared-token match and mismatch;
- failure injection at acquisition, walk invocation/await/iteration,
  destination preflight, directory creation, staging creation/stat/cleanup,
  download, upload, final source walk, destination proof, cancellation, and
  reverse source exit;
- exact stdout, stderr, status, call order, mutation frontier, residue, staging
  cleanup, worker quiescence, and absence of `_copy`, `_cp_file`, public sync
  facades, retries, deletion, and destination downloads; and
- adapted Local and Memory plus a fully mocked native `vosfs` transport on the
  supported Python/Linux/macOS matrix.

The isolated-wheel gate MUST build the `fsspec-cli` wheel and source
distribution plus the `vosfs` wheel, install them outside the workspace with
declared dependencies only, and run the recursive `cp` unit and command-matrix
tests extracted from the source distribution. No matrix row may become `pass`
until that exact-commit gate is immutable.
