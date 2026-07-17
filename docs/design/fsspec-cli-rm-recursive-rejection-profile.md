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
| Root and dot-segment containment | fsspec's inherited async `_rm` accepts paths after `_expand_path`; native `vosfs._rm` delegates raw stripped paths to `_rm_one`. Neither composite exposes configured-source root or ambiguous-dot rejection. |
| Links and special descendants | fsspec's generic `_find`/`_walk` result has no profile-owned link or malformed-descendant classification. Native `vosfs._rm_tree` treats every non-`directory` child as deletable. |
| Bounded source-owned work | Generic fsspec expands recursive operands through `_find` before deleting. Native `vosfs._rm_tree` recursively lists children. Neither supplies a bounded, independently observable completion result. |
| Partial completion and failures | Both composites return `None`; generic fsspec chunks reversed `_rm_file` coroutines. No complete first/aggregate failure record is available at the command seam. |
| Cancellation | No composite result distinguishes cancellation before, during, and after mutation. |
| Confirmed absence | A void return does not distinguish absence from permission, service, or parse failure for every descendant. |
| Uncertain final state | Neither composite reports a truthful aggregate final state without invented descendant reporting. |

Happy-path deletion, method inheritance, construction, and void success do not
meet this profile.

## 3. Versioned inspection

Inspected at base commit
[`063f1741b5e2a323119769c4a1f3e36754428bac`](https://github.com/shinybrar/vosfs/commit/063f1741b5e2a323119769c4a1f3e36754428bac):
fsspec 2026.6.0, Typer 0.27.0, vosfs 0.4.0, CPython 3.13.5, macOS 15.7.7
arm64. `AsyncFileSystem._rm` expands with `_find`, then awaits reversed
`_rm_file` coroutines. `VOSpaceFileSystem._rm` calls `_rm_one`; recursive
directories call `_rm_tree`, which lists and deletes leaves first.

The immutable negative command evidence is
[`test_rm.py::test_rm_recursive_options_are_equivalent_source_free_rejections`](https://github.com/shinybrar/vosfs/blob/ed022d3de5957c929c8a1836d47c0efc0d45f157/src/fsspec-cli/tests/test_rm.py)
at commit
[`ed022d3de5957c929c8a1836d47c0efc0d45f157`](https://github.com/shinybrar/vosfs/commit/ed022d3de5957c929c8a1836d47c0efc0d45f157).

## 4. Frontier

Reconsider only after one source-owned recursive composite exposes and passes a
locked complete-result contract for every Section 2 property. Keep traversal
out of `fsspec-cli`; do not infer admission from another command or source form.
