# `fsspec-cli` recursive `rm` rejection profile

Status: **Superseded option rejection; locked application-disabled rejection**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) /
[#135](https://github.com/shinybrar/vosfs/issues/135)

## 1. Verdict

In fsspec-cli 0.4.0, `rm -R` and `rm -r` were equivalent unsupported options.
Issue #288 supersedes that diagnostic while preserving source-free rejection
when `capabilities.recursion.remove` is false or omitted. A valid recursive
invocation fails before recursive operand or path validation: status `2`, empty
stdout, exactly `rm: recursive removal disabled by application`, zero source
factories, and zero filesystem work or mutation.

The [guarded recursive profile](fsspec-cli-rm-recursive-command-profile.md)
defines the capability-enabled implementation frontier. It does not weaken or
replace this default path.

## 2. Admission result

Admission is rejected. The tested source forms do not expose one directly
verifiable complete-result contract for recursive deletion:

| Required property | Evidence |
| --- | --- |
| Root and dot-segment containment | Local passes a stripped path to host `rmtree`; Memory expands it; native `vosfs._rm` passes stripped paths to `_rm_one`. No form exposes configured-source root or ambiguous-dot rejection. |
| Links and special descendants | Local and Memory expose no profile-owned link or malformed-descendant classification; native `vosfs._rm_tree` deletes every child not typed `directory`. |
| Bounded source-owned work | Local calls host `rmtree`; Memory expands then reverses paths; native `vosfs._rm_tree` recursively lists children. None supplies bounded, independently observable completion. |
| Partial completion and failures | All forms return `None`; Memory and native vosfs mutate descendant-by-descendant. No complete first/aggregate failure record is available at command seam. |
| Cancellation | No form returns a result that distinguishes cancellation before, during, and after mutation. |
| Confirmed absence | Void completion does not distinguish absence from permission, service, or parse failure for every descendant. |
| Uncertain final state | No form reports a truthful aggregate final state without invented descendant reporting. |

Happy-path deletion, method inheritance, construction, and void success do not
meet this profile.

## 3. Immutable admission-source inspection

Inspected at base commit
[`063f1741b5e2a323119769c4a1f3e36754428bac`](https://github.com/shinybrar/vosfs/commit/063f1741b5e2a323119769c4a1f3e36754428bac):
fsspec 2026.6.0 at
[`a2457004d03e0312f715f90f58873de5ab195a37`](https://github.com/fsspec/filesystem_spec/tree/a2457004d03e0312f715f90f58873de5ab195a37),
Typer 0.27.0, vosfs 0.4.0, CPython 3.13.5, macOS 15.7.7 arm64.

Direct source inspection fixes each exact form:

| Source form | Immutable source evidence | Admission consequence |
| --- | --- | --- |
| adapted Local `_rm(path, recursive=True)` | [`AsyncFileSystemWrapper._wrap_all_sync_methods`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py#L81-L97) installs async `self._rm` from `sync_fs.rm`; [`async_wrapper`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py#L30-L37) sends it to a thread. [`LocalFileSystem.rm`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L191-L204) receives `recursive=True` and calls `shutil.rmtree`. | Adapted Local recursive mutation is a thread-wrapped host-tree operation, not a source-owned complete result. |
| adapted Memory `_rm(path, recursive=True)` | [`AsyncFileSystemWrapper._wrap_all_sync_methods`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py#L81-L97) installs async `self._rm` from `sync_fs.rm`; [`async_wrapper`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py#L30-L37) sends it to a thread. [`MemoryFileSystem.rm`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L259-L275) receives `recursive=True`, expands paths, and reverses leaf deletion. | Adapted Memory expansion plus per-path mutation returns no complete aggregate result. |
| native vosfs `_rm(path, recursive=True)` | [`VOSpaceFileSystem._rm`, `_rm_one`, `_rm_tree`](https://github.com/shinybrar/vosfs/blob/063f1741b5e2a323119769c4a1f3e36754428bac/src/vosfs/filesystem.py#L738-L771) strips each path, lists directories, and deletes recursively leaves-first. | Client traversal exposes no root/dot admission, special-descendant classification, bounded completion, or aggregate outcome at command seam. |

Report note (#135): Local and Memory evidence now follows adapted async
`_rm(path, recursive=True)` through wrapper installation to each synchronous
`rm`, not legacy `_rm(path)`.

The immutable negative command evidence is
[`test_rm.py::test_rm_recursive_options_are_equivalent_source_free_rejections`](https://github.com/shinybrar/vosfs/blob/ed022d3de5957c929c8a1836d47c0efc0d45f157/src/fsspec-cli/tests/test_rm.py)
at commit
[`ed022d3de5957c929c8a1836d47c0efc0d45f157`](https://github.com/shinybrar/vosfs/commit/ed022d3de5957c929c8a1836d47c0efc0d45f157);
the current test also asserts its recording source receives no factory or
mutation event.

## 4. Capability-gated frontier

Issue [#284](https://github.com/shinybrar/vosfs/issues/284) replaced the former
source-owned-composite admission bar with one host-qualified, command-owned
manifest contract. The application capability defaults false; true asserts
that every configured target satisfies that contract. Production code may not
inspect backend identity or load the tested command matrix to make that choice.

The application-disabled rejection remains the default production behavior.
Enabling the application capability selects the guarded profile; evidence for
one source form or another command never opens the capability automatically.
