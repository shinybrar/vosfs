# `fsspec-cli` `rm -d` command profile

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#134](https://github.com/shinybrar/vosfs/issues/134)

## Scope

```text
rm -d [--] name:/path...
```

This exact extension removes source-reported files and empty directories. It
does not combine with `-f`, accept grouped forms, recurse, list children, or
fall back to `_rm`.

## Preflight

`-d` is accepted once before operands. It retains base `rm` mapped-operand
validation and whole-argv root/final-dot guards before source acquisition.
Zero operands, `-df`, `-fd`, `-dd`, `-R`, `-v`, long options, and every other
option form are rejected with status `2`.

## Backend operation semantics

After all referenced sources are acquired, operands run in argv order.
`type == "file"` dispatches through the confirmed `_rm_file` boundary. `type
== "directory"` dispatches through the exact async `_rmdir` boundary from the
base `rmdir` profile, including preclassification and post-removal absence
proof. Other or malformed types fail.

The command MUST NOT call `_rm`, recurse, list children, or alias any public
synchronous facade. A source without callable `_rmdir` fails as unsupported;
it never falls back to `_rm`.

## Results

Successful invocations emit no stdout. Ordinary operand failures do not stop
later operands; earlier successful changes remain and no rollback is claimed.
Missing paths, non-empty directories, unavailable `_rmdir`, access/service
errors, and uncertain post-state fail with the inherited stable diagnostics
and status `1`. Cancellation and cleanup follow the base `rm` lifecycle
rules.
