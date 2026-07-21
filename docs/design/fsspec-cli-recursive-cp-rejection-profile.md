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
  `AsyncFileSystemWrapper(LocalFileSystem(), asynchronous=True)`;
- `memory / adapted async`:
  `AsyncFileSystemWrapper(MemoryFileSystem(), asynchronous=True)`; and
- `vosfs / native async`: a fresh
  `VOSpaceFileSystem(asynchronous=True, skip_instance_cache=True)` closed on
  the invocation loop.

Hermetic tests isolate Local temporary roots and Memory store,
pseudo-directory, and instance-cache state without changing either canonical
source-form identity. All ordered pairs and both same-name and distinct-name
routing require their own matrix row. A missing row remains `unverified`, not
unsupported. Raw sync, wrong-mode async, and host-owned reusable instances
remain unsupported by
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

In this section, `S` means the exact source mapped-operand spelling and `D`
means the exact destination mapped-operand spelling. Inserted values use the
shared diagnostic escaping rules. Every command diagnostic has one of these
exact shapes:

```text
cp: S: <stable category>
cp: D: <stable category>
```

### 8.1 Source-free preflight

Section 4 owns every source-free failure. Each writes empty stdout, exactly
one listed stderr line, exits `2`, enters zero sources, creates no staging
file, and leaves the destination unchanged. A duplicate or mixed second
recursive option is the unsupported `<option token>`; no option is silently
coalesced.

### 8.2 Source lifecycle

Lifecycle failure rendering is the ADR 0003 contract with command name `cp`:

| Reachable failure | Exact first stderr line | Status and residue |
| --- | --- | --- |
| Source factory raises | `cp: <name>: source factory failure (<class>): <message>` | Empty stdout; status `1`; no filesystem or staging mutation. |
| Factory returns incompatible context manager | `cp: <name>: source factory returned incompatible async context manager` | Empty stdout; status `1`; no filesystem or staging mutation. |
| Context entry raises | `cp: <name>: source entry failure (<class>): <message>` | Empty stdout; status `1`; no filesystem or staging mutation. |
| Entered source yields incompatible filesystem | `cp: <name>: source yielded incompatible async filesystem` | Empty stdout; status `1`; no filesystem or staging mutation. |
| Source exit raises after otherwise successful command | `cp: <name>: source exit failure (<class>): <message>` | Empty stdout; status `1`; fully verified destination remains. |

An acquisition failure is primary. Already-entered sources then emit any exit
diagnostics in reverse-entry order. After a command failure, exit diagnostics
follow command and staging-cleanup diagnostics. Every such ordinary outcome
exits `1`; no lifecycle failure changes the command's recorded destination or
host-staging residue.

### 8.3 Read-only command phases

The following table is exhaustive before the first `_mkdir` call. Every row
writes empty stdout, exactly the listed primary stderr line followed only by
ADR 0003 reverse-entry source-exit diagnostics, exits `1`, creates no staging
file, and leaves the destination unchanged. A destination `_info`
`FileNotFoundError` for the resolved root or a corresponding manifest entry is
expected absence, not failure; a missing resolved parent is the listed
destination `not found` failure.

| Phase and reached condition | Attributed operand | Exact primary stderr line |
| --- | --- | --- |
| Initial source `_info`: `FileNotFoundError` | `S` | `cp: S: not found` |
| Initial source `_info`: `PermissionError` | `S` | `cp: S: permission denied` |
| Initial source `_info`: `NotImplementedError` or missing required coroutine | `S` | `cp: S: unsupported operation` |
| Initial source `_info`: malformed result | `S` | `cp: S: incompatible result` |
| Initial source `_info`: file instead of directory | `S` | `cp: S: not a directory` |
| Initial source `_info`: link or other type | `S` | `cp: S: unsupported entry type` |
| Initial source `_info`: any other `Exception` | `S` | `cp: S: backend failure (<class>): <message>` |
| Destination root or parent `_info`: `PermissionError` | `D` | `cp: D: permission denied` |
| Destination root or parent `_info`: `NotImplementedError` or missing required coroutine | `D` | `cp: D: unsupported operation` |
| Destination root or parent `_info`: malformed result | `D` | `cp: D: incompatible result` |
| Resolved parent missing | `D` | `cp: D: not found` |
| Resolved parent is not a non-link directory | `D` | `cp: D: not a directory` |
| Existing resolved root is a link or other type | `D` | `cp: D: unsupported entry type` |
| Existing resolved root is a file | `D` | `cp: D: destination type conflict` |
| Same namespace exact or contained target | `D` | `cp: D: destination is inside source` |
| Destination root or parent `_info`: any other `Exception` | `D` | `cp: D: backend failure (<class>): <message>` |
| Initial source `_walk`: `FileNotFoundError` | `S` | `cp: S: not found` |
| Initial source `_walk`: `PermissionError` | `S` | `cp: S: permission denied` |
| Initial source `_walk`: `NotImplementedError` or missing required coroutine | `S` | `cp: S: unsupported operation` |
| Initial source `_walk`: invocation, await, or iteration raises another `Exception` | `S` | `cp: S: backend failure (<class>): <message>` |
| Initial source `_walk`: wrong awaitable/iterator or malformed manifest | `S` | `cp: S: incompatible result` |
| Initial manifest contains link or other type | `S` | `cp: S: unsupported entry type` |
| Initial manifest reaches entry 10,001 | `S` | `cp: S: source tree exceeds 10000 entries` |
| Corresponding destination `_info`: `PermissionError` | `D` | `cp: D: permission denied` |
| Corresponding destination `_info`: `NotImplementedError` or missing required coroutine | `D` | `cp: D: unsupported operation` |
| Corresponding destination `_info`: malformed result | `D` | `cp: D: incompatible result` |
| Corresponding destination entry is a link or other type | `D` | `cp: D: unsupported entry type` |
| Corresponding destination entry has conflicting file/directory type | `D` | `cp: D: destination type conflict` |
| Corresponding destination `_info`: any other `Exception` | `D` | `cp: D: backend failure (<class>): <message>` |

### 8.4 Mutating and proof phases

After the first `_mkdir` attempt, rollback is forbidden. Every ordinary row
below writes empty stdout, emits exactly its primary stderr line, attempts
current staging cleanup when a staging path exists, then emits any staging
cleanup diagnostic and ADR 0003 source-exit diagnostics in that order, and
exits `1`.

| Phase and reached condition | Attributed operand | Exact primary stderr line | Destination and host-staging residue |
| --- | --- | --- | --- |
| Destination `_mkdir` raises any `Exception` | `D` | `cp: D: mutation failure; destination residue may remain` | Earlier directories remain; current directory may be absent or complete; no staging file. |
| Secure staging creation or descriptor close raises | `S` | `cp: S: staging failure (<class>); destination residue may remain` | Earlier destination entries remain; a created host staging path may remain only when cleanup also fails. |
| Source `_get_file` raises any `Exception` | `S` | `cp: S: transfer failure; destination residue may remain` | Earlier destination entries remain; current destination file was not uploaded; partial host staging is removed unless cleanup fails. |
| Local staging stat raises | `S` | `cp: S: staging failure (<class>); destination residue may remain` | Earlier destination entries remain; host staging is removed unless cleanup fails. |
| Staged size differs from frozen size | `S` | `cp: S: source changed; destination residue may remain` | Earlier destination entries remain; current destination file was not uploaded; host staging is removed unless cleanup fails. |
| Destination `_put_file` raises any `Exception` | `D` | `cp: D: mutation failure; destination residue may remain` | Earlier entries remain; current destination file may be absent, partial, or complete; host staging is removed unless cleanup fails. |
| Final source `_walk` cannot be invoked, awaited, iterated, bounded, or validated | `S` | `cp: S: source revalidation failure; destination residue may remain` | Every attempted destination entry remains; no staging file. |
| Final source manifest differs from frozen projection | `S` | `cp: S: source changed; destination residue may remain` | Every attempted destination entry remains; no staging file. |
| Destination proof `_info` raises, is malformed, or reports any mismatch | `D` | `cp: D: verification failure; destination residue may remain` | Every attempted destination entry remains; no staging file. |

### 8.5 Staging cleanup combinations

An ordinary staging cleanup failure has exactly this source-attributed line:

```text
cp: S: staging cleanup failure (<class>); host staging residue may remain; destination residue may remain
```

- With no earlier command failure or escaping control flow, it becomes the
  primary diagnostic; stdout is empty, status is `1`, the uploaded current
  destination plus earlier destination entries remain, and host staging may
  remain.
- With an ordinary command failure, the command diagnostic remains primary and
  is emitted first. The cleanup line is emitted second. Status remains `1`;
  both lines' residue statements apply.
- With active escaping control flow, the cleanup line is emitted before any
  reverse-entry source-exit diagnostics; the original control-flow object is
  then propagated unchanged with no numeric command status.

A staging-cleanup `BaseException` is never converted to a diagnostic. With no
earlier escaping control flow it propagates unchanged; with earlier escaping
control flow the earlier object wins. Any already emitted ordinary primary
diagnostic remains visible. Host staging and destination residue may remain.

### 8.6 Cancellation and other escaping control flow

Each walk materialization and filesystem operation is owned by one command
task. On `CancelledError`, `KeyboardInterrupt`, `SystemExit`, or another
escaping `BaseException`, the command stops scheduling new work, drains the
current operation, performs current staging cleanup, begins source cleanup,
then propagates the original object unchanged. A drained operation's ordinary
failure never replaces or adds a command diagnostic to the control flow.

Stdout is always empty. There is no cancellation diagnostic and no numeric
command status. Stderr order is: any ordinary command diagnostic already
emitted before control flow arose; an ordinary staging-cleanup diagnostic; then
ADR 0003 source-exit diagnostics in reverse-entry order.

| Interrupted phase | Destination and host-staging residue after drain and cleanup |
| --- | --- |
| Acquisition, initial source `_info`/`_walk`, target resolution, or destination preflight | Destination unchanged; no staging file. |
| Destination `_mkdir` | Earlier directories remain; current directory may be absent or complete; no staging file. |
| Staging creation or source download | Earlier destination entries remain; current destination file was not uploaded; staging is removed unless cleanup fails. |
| Destination upload | Earlier entries remain; current destination file may be absent, partial, or complete; staging is removed unless cleanup fails. |
| Final source revalidation or destination proof | Every attempted destination entry remains; no staging file. |

No destination entry is removed to simulate rollback.

This required current-operation drain is a recursive-copy-specific exception
to ADR 0003's present tree-only worker rule. Issue #286 MUST update ADR 0003 in
the same implementation change; the positive command MUST NOT ship while the
documents conflict. There is no timeout or background worker after source
cleanup.

## 9. Matrix delta and implementation gate

The [tested command matrix](fsspec-cli-tested-command-matrix.md) replaces the
old command-preflight `unsupported` row with explicit same-name and
distinct-name rows for every ordered pair of the three initial source forms.
A `source` scope row means both operands use the same configured name. A
`source pair` scope row means distinct configured names, and its source-form
cell records the ordered source-to-destination forms. Source-form text exactly
matches Section 3 of the matrix. Every new row is `unverified`; research
inspection is not qualifying command evidence.

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
