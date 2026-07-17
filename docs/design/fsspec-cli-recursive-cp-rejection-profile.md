# `fsspec-cli` same-source `cp -R` rejection profile

<!-- pyml disable line-length -->

Status: **Rejected after fsspec composite research**

Question: [Decide same-source cp -R admission through fsspec](https://github.com/shinybrar/vosfs/issues/140)

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

## 1. Verdict

Same-source `cp -R` is **unsupported**. `cp -R source:/directory
source:/target` MUST reject during command preflight with status `2`, empty
stdout, exactly one `cp: -R: unsupported option` diagnostic, zero source
factory calls, and zero filesystem mutation. Cross-source `cp -R` is also
unsupported.

The command MUST NOT call `_copy(..., recursive=True)`, enumerate a tree, or
replace fsspec's composite with CLI traversal. This profile adds no positive
recursive implementation.

## 2. Exact research tuple

Research ran at source commit
[`063f1741b5e2a323119769c4a1f3e36754428bac`](https://github.com/shinybrar/vosfs/commit/063f1741b5e2a323119769c4a1f3e36754428bac)
with `fsspec-cli` 0.1.1, fsspec 2026.6.0, Typer 0.27.0, vosfs 0.4.0, CPython
3.13.5, and macOS 15.7.7 arm64. The resolved set is locked in that commit's
[`uv.lock`](https://github.com/shinybrar/vosfs/blob/063f1741b5e2a323119769c4a1f3e36754428bac/uv.lock).

The examined composite is fsspec 2026.6.0
[`AsyncFileSystem._copy`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/asyn.py).
An adapted `AsyncFileSystemWrapper` replaces that inherited method at instance
creation with an async wrapper around its synchronous backend `copy`; see
[its implementation](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py).

## 3. Admission failures

- `_copy` expands paths then runs individual `_cp_file` calls and returns
  `None`; it supplies no complete-tree result or per-entry proof.
- `/tree` and `/tree/` copied successfully in adapted Local and Memory;
  `/tree/.` copied on Local but raised `FileNotFoundError` on Memory.
  `/tree` to `/tree/inside` was accepted on both forms.
- Local copied a symlink-to-file as a regular destination file containing the
  target's bytes. The composite has no authoritative link or special-type
  policy.
- Expansion and transfer are not bounded source-owned work. It has no snapshot
  or changing-source proof. The default recursive `on_error` ignores
  `FileNotFoundError`; ordinary failures can follow earlier copies.
- Adapted wrapper cancellation cancels the awaiting caller but not its
  synchronous worker; the worker continued copying all expanded entries.
  No truthful partial-completion or destination-residue result exists.
- Verification would require a second independent tree walk or fabricated
  metadata. Neither is permitted by this profile.

These failures reject every observed exact source form. They are not backend
bugs and do not establish a broader fsspec compatibility claim.

## 4. Evidence and matrix

`test_command_matrix.py::test_cp_option_rejection_is_source_free` exercises the
production `App` seam and proves the locked `-R` rejection. Its matrix row is
command preflight / not entered / unsupported; no backend form is invented.

Future admission needs a separately locked exact source form proving every
condition above, including complete-tree verification without a CLI traversal
engine. Until then, same-source and cross-source `cp -R` remain unsupported.
