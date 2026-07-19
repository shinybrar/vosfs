# `fsspec-cli` positively evidenced same-source multi-file `mv` profile

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#144](https://github.com/shinybrar/vosfs/issues/144)

## Scope

```text
mv [--] name:/file1 name:/file2 [name:/fileN ...] name:/directory
```

At least two source operands plus a final existing destination directory. Every
operand uses the same configured source name. File sources only. Directory
sources stay under the separate directory-mv admission verdict. Cross-source
names, prompt flags, `-f`, and other options reject before source entry with
status `2`.

## Execution

Preflight validates mapped operands and same configured name before acquisition.
Command acquires that source once. The final destination must already exist as
`type == "directory"` or the invocation fails before any move. Sources then
process sequentially in argv order. Each destination resolves from the source
basename under that directory and reuses the
[two-operand same-source file `mv`](fsspec-cli-same-source-mv-command-profile.md)
positively evidenced move path, including exact awaitable `_mv`, destination
metadata proof, source absence, same-path no-op after resolution, and eligible
replacement. When no recognized metadata token is shared, exact destination
type and size plus source absence are the truthful proof.

Earlier completed moves remain after a later failure. There is no rollback.
Successful invocations emit no stdout.

## Evidence

Hermetic `test_mv.py` covers multi-file argv order, duplicate basenames,
existing targets, same-path no-op, missing or non-directory destination,
missing/other/directory sources, mid-sequence move and verification failures,
cancellation, cleanup, and source-free rejections. Adapted Local, adapted
Memory, and native `vosfs` remain independently `unverified` without an exact
awaitable `_mv`, matching the two-operand classification. Isolated-wheel gate
runs `test_mv.py`.
