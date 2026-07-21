# `fsspec-cli` cross-source `mv` deletion-guard rejection profile

<!-- pyml disable line-length -->

Status: **Locked source-free rejection after deletion-guard research**

Question: [Research staged non-atomic cross-source file `mv`](https://github.com/shinybrar/vosfs/issues/285)

Supersedes the rationale, but not the observable rejection, locked by
[#142](https://github.com/shinybrar/vosfs/issues/142).

## 1. Verdict and scope

No current tested async filesystem source form can prove that an unchanged
source generation is selected for deletion after the verified cross-source
`cp` staging contract completes. Cross-source `mv` therefore remains an
unsupported capability.

```text
mv [--] source:/file destination:/target
```

After complete option, operand-grammar, mapped-name, and operand-count
preflight identifies distinct configured source names, `mv` rejects before
either async filesystem source is called. It writes no stdout, writes exactly

```text
mv: cross-source move unsupported
```

to stderr, and exits `2`. No source lifecycle, filesystem call, local staging,
destination mutation, source mutation, or cleanup begins. Configured source
names define this boundary; shared backend classes, protocols, source
callables, or yielded objects do not make a cross-source move supported.

Directory operands and moves with more than one source operand remain outside
the file-only profile. A cross-source shape rejects during the same preflight,
before source entry can classify an operand as a file or directory. Directory
movement remains explicit `cp -R` followed by `rm -R`, with each command's own
locked profile and result.

## 2. Exact research tuple

Research ran from source commit
[`4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f`](https://github.com/shinybrar/vosfs/commit/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f)
with:

- `fsspec-cli` 0.4.0 and `vosfs` 0.5.0 from that commit;
- fsspec 2026.6.0, whose locked wheel SHA-256 is
  `02e0b71817df9b2169dc30a16832045764def1191b43dcff5bb85bdee212d2a1`;
- Typer 0.27.0;
- CPython 3.13.5; and
- macOS 15.7.7 arm64.

The complete resolved set is recoverable from that commit's
[`uv.lock`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/uv.lock).
The fsspec wheel corresponds to tag `2026.6.0`, commit
[`a2457004d03e0312f715f90f58873de5ab195a37`](https://github.com/fsspec/filesystem_spec/commit/a2457004d03e0312f715f90f58873de5ab195a37).

The examined command seam was `App(sources).typer_app`. The exact source forms
were:

- `local / adapted async`:
  `AsyncFileSystemWrapper(LocalFileSystem(), asynchronous=True)`;
- `memory / adapted async`:
  `AsyncFileSystemWrapper(MemoryFileSystem(), asynchronous=True)`; and
- `vosfs / native async`:
  `VOSpaceFileSystem(asynchronous=True, skip_instance_cache=True)`.

The tested command matrix has positive cross-source `cp` evidence only for
Local to Memory and Memory to Local. Those rows deliberately prove a
size-only destination boundary. Native `vosfs` directions remain `unverified`.

## 3. Required deletion proof

A positive profile needs a source-supplied immutable generation fingerprint,
not only the transfer proof already frozen by verified `cp`. The minimum proof
is:

1. source `_info(path)` reports `type == "file"` and a non-negative integer
   `size`;
2. the same result carries one non-empty, opaque generation token whose source
   contract guarantees a new value for every replacement or in-place content
   mutation; and
3. type, size, and token are copied into an immutable value before destination
   resolution or mutation.

After successful staging, upload, destination verification, and local staging
cleanup, the command would have to await one fresh source `_info(path)` and
require exact type, size, and token equality. Missing, malformed, changed, or
unavailable revalidation must retain both source and verified destination and
exit nonzero. No other awaited operation may occur between successful
revalidation and the deletion call.

This revalidation would still leave a time-of-check/time-of-use race before an
unconditional deletion. Without a source-owned conditional delete, it cannot
guarantee deletion of the revalidated generation. A future positive profile
must state that residual race and must not claim atomic rename, transactionality,
conditional generation deletion, rollback, or POSIX metadata preservation.

## 4. Source-form findings

### 4.1 Local adapted async

Pinned fsspec
[`LocalFileSystem.info`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py)
reports stat-derived `size`, `created`, `mtime`, `ino`, and other attributes.
It reports no immutable generation token. `rm_file` calls `os.remove` without a
generation condition.

A local probe changed four bytes from `same` to `diff` in place, restored the
original `st_mtime_ns`, and then called `info` again. The candidate
`(type, size, ino, mtime)` fingerprint was exactly unchanged. Adding `created`
does not repair the cross-platform contract: fsspec uses birth time when
available and otherwise ctime. Therefore Local cannot prove an unchanged
generation before deletion.

### 4.2 Memory adapted async

Pinned fsspec
[`MemoryFileSystem.info`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py)
reports only `name`, `size`, `type`, and object `created` for a file. A local
probe opened a four-byte file as `r+b`, changed `same` to `diff` in place, and
observed the complete before and after `info` mappings compare equal. Removal
is unconditional. Memory therefore supplies no mutation-sensitive generation
fingerprint.

### 4.3 Native `vosfs`

At the pinned source commit, `VOSpaceFileSystem._info` maps a DataNode to
`name`, `type`, `size`, and `uri`, with optional `mtime` and OpenCADC `md5`.
The exact mapping and its minimal form are covered by
[`test_nodes.py`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/tests/test_nodes.py#L302-L330).
Neither optional value is an immutable node-generation identifier: `md5`
identifies content and `mtime` is a property value. A minimal DataNode supplies
neither.

`VOSpaceFileSystem._rm_file` checks only that a fresh `_info` result is not a
directory, then `_delete_node` issues an unconditional node `DELETE`; see
[`filesystem.py`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L1156-L1166)
and the
[`_rm_file` implementation](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L1239-L1250).
Native `vosfs` therefore supplies no deletion guard, and its cross-source `cp`
directions also lack qualifying evidence.

## 5. Failure, residue, cleanup, and cancellation contract

Because rejection finishes before source entry, all mutation-stage outcomes
collapse to one stable result:

| Requested boundary | Locked observation |
| --- | --- |
| Source fingerprint unavailable | Rejected before either source is entered or inspected. |
| Staging download, upload, or destination verification | Not started; source and destination remain unchanged. |
| Destination changes during or after copy | No destination is created or replaced by this command. |
| Source revalidation change or failure | No source revalidation or deletion begins. |
| Source deletion failure or uncertain result | No deletion begins; no rollback is attempted. |
| Local temporary or source cleanup failure | No temporary or source lifecycle exists to clean up. |
| Cancellation before or after copy | No copy await point exists; command performs only source-free preflight. |
| Cancellation during deletion | No deletion await point exists. |

For every cross-source shape in this profile, stdout is empty, stderr contains
only `mv: cross-source move unsupported`, status is `2`, and both source and
destination retain their pre-invocation states. The command makes no stronger
claim about concurrent external mutations it never observes.

## 6. Matrix and isolated-wheel gate

The tested command matrix retains one `command preflight` / `not entered` /
`unsupported` row. It must not invent Local, Memory, or `vosfs` source-pair
rows because no source is entered. The hermetic public-seam test at the pinned
commit uses independent recording factories, proves neither is called, and
proves both stores remain unchanged:
[`test_mv.py::test_mv_rejects_cross_source_without_factories_or_mutation`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/fsspec-cli/tests/test_mv.py#L493-L515).

The isolated-wheel gate builds `fsspec-cli`, rebuilds its wheel from the source
distribution, installs it with pinned `vosfs`, fsspec, and Typer wheels outside
the workspace, and runs `test_mv.py`. Required CI repeats that gate on Ubuntu
with Python 3.10 through 3.14 and on macOS with Python 3.12. A future positive
profile must add exact source-pair tests to that same gate before changing the
matrix row or removing this rejection.

## 7. Rejected positive shortcuts

- Verified `cp` size equality and optional content tokens do not identify a
  source generation.
- Hashing the staging temporary and downloading the source again would not
  satisfy the required pre-transfer source-supplied proof, would add another
  transfer, and would still miss a same-content replacement.
- Comparing complete `_info` mappings would freeze backend-specific incidental
  fields and still misses the demonstrated Local and Memory mutations.
- Destination rollback after any later failure would add another uncertain
  mutation. Retaining a verified destination is the only admissible future
  failure direction.

Future admission requires one exact source form with immutable generation
identity plus a verified cross-source `cp` pair. Until then, implementation
issue [#287](https://github.com/shinybrar/vosfs/issues/287) remains blocked and
this ticket adds no production move behavior.
