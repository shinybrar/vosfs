# `fsspec-cli` positively evidenced same-source file `mv` profile

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#141](https://github.com/shinybrar/vosfs/issues/141)

## Scope

```text
mv [--] name:/file name:/target
```

Exactly two mapped operands, same configured source name, no options. File
source only. Cross-source, directory, multi-source, `-i`, `-f`, and every
other shape reject before source entry with status `2`.

Target resolution matches verified same-source `cp`: destination directory gets
source basename, existing files may replace, and resolved parent must exist as a
directory.

## Operation and proof

After source existence and target resolution, identical configured name and
backend path is status `0` no-op. It calls no move operation and emits nothing.

Every other path requires an awaitable `_mv(path1, path2)` declared directly
by configured source form. Public synchronous `mv`, copy-then-delete,
cross-source staging, and inherited or non-awaitable move defaults are
excluded.

Before mutation, command stages source bytes. After `_mv`, success requires:

1. destination is `type == "file"` with original size;
2. destination staged bytes match original staged bytes; and
3. source `_info` raises `FileNotFoundError`.

Any move exception, wrong destination result, destination mismatch, source
residue, or verification failure exits `1` with residue disclosure. Cancellation
and other control flow propagate after source cleanup. Successful invocations
emit no stdout.

Passing result proves target resolution, replacement, destination completeness,
source absence, diagnostics, cleanup, and partial-state reporting only. It does
not prove atomic rename, identity preservation, POSIX mode, ownership, links,
timestamps, or generic metadata preservation.

## Evidence

Matrix rows classify adapted async Local, adapted async Memory, and native async
`vosfs` independently. A source form lacking exact awaitable `_mv` remains
`unverified`; no backend behavior is inferred from operation names or inherited
sync facades. Isolated-wheel gate runs `test_mv.py`.
