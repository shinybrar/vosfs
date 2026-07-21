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

At the pinned source commit, `_info` parses the node document, then
[`to_info`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/nodes.py#L165-L196)
maps a DataNode to requested-path `name`, `type`, `size`, and the document's
node-identifier `uri`. It may also expose `mtime`, OpenCADC `md5`,
`content_type`, and the read-only `properties` mapping containing every
URI-keyed property. A minimal DataNode has only the four required fields; the
exact complete and minimal shapes are covered by
[`test_nodes.py`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/tests/test_nodes.py#L302-L330).

None identifies an immutable generation. The `uri` is path identity and can be
reused by a replacement. Cavern explicitly does not preserve its internal
[`Node.id` except for root](https://github.com/opencadc/vos/blob/cf976ce8141dd3341631b7f3e07aa38443d42f58/cavern/src/main/java/org/opencadc/cavern/nodes/NodeUtil.java#L159-L180).
`md5` identifies content, `mtime`/date and `content_type` are property values,
and the OpenCADC profile defines no URI-keyed property as an immutable
generation token. Arbitrary properties therefore cannot be promoted into a
guard by this command profile.

`VOSpaceFileSystem._rm_file` checks only that a fresh `_info` result is not a
directory. It passes no fingerprint to `_delete_node`, whose exact request is
`_send_to_service("DELETE", url)` with no conditional header or generation
parameter; see
[`_delete_node`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L1156-L1166)
and
[`_rm_file`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/vosfs/filesystem.py#L1239-L1250).
The pinned Cavern persistence likewise resolves the path and calls
[`Files.delete`](https://github.com/opencadc/vos/blob/cf976ce8141dd3341631b7f3e07aa38443d42f58/cavern/src/main/java/org/opencadc/cavern/nodes/FileSystemNodePersistence.java#L512-L526).
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

The tested command matrix retains one `command preflight` / `not entered` row.
Its current status is `unverified`; `unsupported` remains the required status
until qualifying evidence covers the complete gate. It must not invent Local,
Memory, or `vosfs` source-pair rows because no source is entered. The hermetic
public-seam test at the pinned commit uses independent recording factories,
proves neither is called, and proves both stores remain unchanged:
[`test_mv.py::test_mv_rejects_cross_source_without_factories_or_mutation`](https://github.com/shinybrar/vosfs/blob/4d53a5b5ffdf898e50eec95bf6b865ec7ad0cd4f/src/fsspec-cli/tests/test_mv.py#L493-L515).

The current isolated-wheel gate builds a workspace `fsspec-cli` wheel and
source distribution, proves that the source distribution can produce another
wheel, but installs the original workspace-built wheel. Its isolated
environments let `uv` resolve declared fsspec, Typer, and pytest constraints;
they do not install the rebuilt wheel with this research tuple's exact pins.
Running `test_mv.py` through that gate is useful local regression evidence, but
is not qualifying immutable evidence for this row.

Before the row becomes `unsupported`, CI must install the wheel rebuilt from
the source distribution with exact fsspec 2026.6.0 and Typer 0.27.0 wheels from
a local wheelhouse, run the negative `test_mv.py` case outside the workspace,
and record an immutable run for Ubuntu with Python 3.10 through 3.14 and macOS
with Python 3.12. No network resolution may fill missing dependencies. Any
future positive profile must additionally install the exact admitted source
pair, add its tests to that rebuilt-wheel gate, and replace rather than
reinterpret the rejection row.

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
