# `fsspec-cli` base `rmdir` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#130](https://github.com/shinybrar/vosfs/issues/130)

Client baseline: **fsspec 2026.6.0**

## Post-profile async constraint

Production CLI orchestration and filesystem calls remain async-only through
`App(sources).typer_app`. Source acquisition, cleanup, cancellation, and
failure precedence follow
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope

This contract defines empty-directory removal without recursion or parent
traversal:

```text
rmdir [--] name:/path...
```

The command operates only on invocation-owned filesystems yielded by configured
async filesystem sources. It does not own source configuration or
authentication and does not branch on backend type.

The supported surface is deliberately smaller than POSIX Issue 8:

- at least one mapped filesystem operand is required;
- no options are supported in this profile; and
- successful invocations emit no stdout.

`-p`, grouped parent options, and long forms remain unsupported until their
dedicated profiles exist.

## 2. Mapped filesystem operands

Operand grammar matches the
[plain `ls` profile](fsspec-cli-plain-ls-command-profile.md#2-mapped-filesystem-operands).

One or more operands are accepted. Repeated operands remain repeated, and one
invocation MAY address several configured filesystems. Zero operands is a
usage error.

Before source acquisition, the command MUST reject:

- configured source root (`name:/`);
- any operand whose final path component is `.` or `..` after removing trailing
  slash characters.

These are source-free safety guards with exit status `2`.

### 2.1 Option and operand preflight

Before any source factory call, context entry, backend call, or stdout output,
the command MUST validate option syntax, operand presence, operand grammar,
mapped filesystem names, and root or final dot-component safety guards.

`--` ends option parsing. Every option token is unsupported in this profile.
Typer's framework-owned `--help` short circuit is explicitly exempt from this
command compatibility profile.

The first preflight error in argument order MUST produce one diagnostic and
exit `2`. No source may be entered and no stdout output written before it.

| Condition | Diagnostic |
| --- | --- |
| No operands | `rmdir: missing mapped filesystem operand` |
| Unsupported option token | `rmdir: <option token>: unsupported option` |
| Malformed operand | `rmdir: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `rmdir: <operand>: unknown filesystem (known: <name>, ...)` |
| Root or final `.` / `..` | `rmdir: <operand>: rejected path` |

## 3. Backend operation semantics

Production code MUST NOT invoke fsspec's synchronous facades, call `_ls`, list
children, recurse, or alias to `rm`. After every distinct referenced source is
acquired sequentially in first-operand order, the command MUST process
operands sequentially in original order.

For each operand it MUST:

1. await `_info(path)` and require source-reported `type == "directory"`;
2. await the exact source-form async `_rmdir(path)` operation; and
3. await `_info(path)` again, requiring a distinguishable `FileNotFoundError`.

A void `_rmdir` return alone is not success. Non-`FileNotFoundError`
post-check failures remain visible.

## 4. Standard output

Successful invocations MUST emit no stdout. There is no success banner,
confirmation line, or removed-path listing.

## 5. Runtime failures and diagnostics

Operands MUST be processed in original argument order. An ordinary per-operand
failure MUST NOT stop later operands. Earlier successful removals MUST
remain removed; the command MUST NOT claim rollback or atomicity.

Diagnostics use this shape:

```text
rmdir: <mapped operand>: <stable category>
```

| Exception or condition | Category |
| --- | --- |
| `FileNotFoundError` | `not found` |
| `PermissionError` | `permission denied` |
| `NotADirectoryError` or source-reported file | `not a directory` |
| `OSError` with `errno.ENOTEMPTY` | `directory not empty` |
| `NotImplementedError` | `unsupported operation` |
| Invalid consumed backend shape or path still present | `incompatible result` |
| Any other backend exception | `backend failure (<class>): <message>` |

Diagnostic rendering, escaping, cleanup precedence, and source lifecycle rules
match the plain `ls` profile.

## 6. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | Every operand succeeded and every entered source exited cleanly. |
| `1` | At least one operand failed, source cleanup failed, or control-flow precedence required failure. |
| `2` | Preflight rejected the invocation before source entry. |

## 7. Evidence

Hermetic matrix probes exercise adapted async Local, adapted async Memory, and
native async `vosfs` through the production `App` seam. Source-free rejection
tests prove `-p` completes during command preflight without entering a source.

Live OpenCADC evidence is not required for this profile in v1; native `vosfs`
hermetic evidence does not broaden into a general service guarantee.
