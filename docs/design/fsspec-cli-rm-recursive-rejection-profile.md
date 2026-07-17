# `fsspec-cli` recursive `rm` rejection profile

Status: **Locked source-free rejection**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) /
[#135](https://github.com/shinybrar/vosfs/issues/135)

## 1. Verdict

`rm -R` and `rm -r` are equivalent, unsupported options. Each invocation
fails during command preflight: status `2`, empty stdout, exactly one
`rm: <option>: unsupported option` diagnostic, zero source factories, and zero
filesystem mutation. `fsspec-cli` owns no recursive traversal.

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
| adapted Local `rm(path, recursive=True)` | [`LocalFileSystem.rm`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L191-L204) calls `shutil.rmtree`; inherited legacy [`_rm(path)`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L1247-L1250) has no `recursive` parameter. | Local recursive mutation is a direct host-tree operation, not a source-owned complete result. |
| adapted Memory `rm(path, recursive=True)` | [`MemoryFileSystem.rm`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L259-L275) expands recursively and reverses leaf deletion; its [`_rm(path)`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L234-L239) has no `recursive` parameter. | Expansion plus per-path mutation returns no complete aggregate result. |
| native vosfs `_rm(path, recursive=True)` | [`VOSpaceFileSystem._rm`, `_rm_one`, `_rm_tree`](https://github.com/shinybrar/vosfs/blob/063f1741b5e2a323119769c4a1f3e36754428bac/src/vosfs/filesystem.py#L738-L771) strips each path, lists directories, and deletes recursively leaves-first. | Client traversal exposes no root/dot admission, special-descendant classification, bounded completion, or aggregate outcome at command seam. |

The immutable negative command evidence is
[`test_rm.py::test_rm_recursive_options_are_equivalent_source_free_rejections`](https://github.com/shinybrar/vosfs/blob/ed022d3de5957c929c8a1836d47c0efc0d45f157/src/fsspec-cli/tests/test_rm.py)
at commit
[`ed022d3de5957c929c8a1836d47c0efc0d45f157`](https://github.com/shinybrar/vosfs/commit/ed022d3de5957c929c8a1836d47c0efc0d45f157);
the current test also asserts its recording source receives no factory or
mutation event.

## 4. Frontier

Reconsider only after one source-owned recursive composite exposes and passes a
locked complete-result contract for every Section 2 property. Keep traversal
out of `fsspec-cli`; do not infer admission from another command or source form.
