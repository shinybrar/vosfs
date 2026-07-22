# `fsspec-cli` base file-only `rm` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#132](https://github.com/shinybrar/vosfs/issues/132)

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

This contract defines base file-only removal without force, directory, or
recursive options:

```text
rm [--] name:/file...
```

The command operates only on invocation-owned filesystems yielded by configured
async filesystem sources. It does not own source configuration or
authentication and does not branch on backend type.

The supported surface is deliberately smaller than POSIX Issue 8:

- at least one mapped filesystem operand is required;
- only entries classified as fsspec `type == "file"` are removed;
- no options are supported in this profile; and
- successful invocations emit no stdout.

`type == "file"` is only fsspec's common type shape. It does not prove POSIX
regular-file or non-link identity. Implicit permission-based POSIX prompting
is unavailable in this profile.

`-i`, grouped forms, and long forms remain unsupported until their
dedicated profiles exist. [`rm -d`](fsspec-cli-rm-directory-command-profile.md),
[`rm -f`](fsspec-cli-rm-force-command-profile.md), and
[`rm -v`](fsspec-cli-rm-verbose-command-profile.md) are separate profiles.
[`rm -R`/`-r`](fsspec-cli-rm-recursive-rejection-profile.md) are source-free
rejections in fsspec-cli 0.4.0. After #288 implements the application policy,
the same rejection remains required whenever
`capabilities.recursion.remove` is false. The locked
[guarded recursive profile](fsspec-cli-rm-recursive-command-profile.md) defines
the capability-enabled implementation frontier without changing this base
file-only contract.

## 2. Mapped filesystem operands

Operand grammar matches the
[plain `ls` profile](fsspec-cli-plain-ls-command-profile.md#2-mapped-filesystem-operands).

One or more operands are accepted. Repeated operands remain repeated, and one
invocation MAY address several configured filesystems. Zero operands is a
usage error.

Before source acquisition, the command MUST reject every configured source root
(`name:/`) and every operand whose final path component is `.` or `..` after
removing trailing slash characters for the entire argv. These whole-argv
destructive guards are source-free and exit with status `2`.

### 2.1 Option and operand preflight

Before any source factory call, context entry, backend call, or stdout output,
the command MUST validate option syntax, operand presence, operand grammar,
mapped filesystem names, and root or final dot-component safety guards across
the entire argv.

`--` ends option parsing. Every option token is unsupported in this profile.
Typer's framework-owned `--help` short circuit is explicitly exempt from this
command compatibility profile.

The first preflight error in argument order MUST produce one diagnostic and
exit `2`. No source may be entered and no stdout output written before it.

| Condition | Diagnostic |
| --- | --- |
| No operands | `rm: missing mapped filesystem operand` |
| Unsupported option token | `rm: <option token>: unsupported option` |
| Malformed operand | `rm: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `rm: <operand>: unknown filesystem (known: <name>, ...)` |
| Root or final `.` / `..` | `rm: <operand>: rejected path` |

## 3. Backend operation semantics

Production code MUST reuse the exact confirmed-file-removal boundary proven by
[XSI `unlink`](fsspec-cli-unlink-command-profile.md): await `_info`, require
`type == "file"`, await `_rm_file` once, then await `_info` again requiring a
distinguishable `FileNotFoundError`.

The command MUST NOT call `_rm`, `_rmdir`, `_ls`, listing, recursive removal,
or public synchronous facades. Directory operands MUST fail before `_rm_file`
and MUST NOT become recursive deletion. A missing path is a runtime failure.

After every distinct referenced source is acquired sequentially in
first-operand order, the command MUST process operands sequentially in
original order.

## 4. Standard output

Successful invocations MUST emit no stdout. There is no success banner,
confirmation line, or removed-path listing.

## 5. Runtime failures and diagnostics

Operands MUST be processed in original argument order. An ordinary per-operand
failure MUST NOT stop later operands. Earlier successful removals MUST
remain removed; the command MUST NOT claim rollback or atomicity.

Diagnostics use this shape:

```text
rm: <mapped operand>: <stable category>
```

| Exception or condition | Category |
| --- | --- |
| Pre-mutation `FileNotFoundError` | `not found` |
| Pre-mutation `PermissionError` | `permission denied` |
| Pre-mutation `IsADirectoryError` or source-reported directory | `is a directory` |
| Pre-mutation `NotImplementedError` | `unsupported operation` |
| Pre-mutation invalid consumed backend shape | `incompatible result` |
| Pre-mutation any other backend exception | `backend failure (<class>): <message>` |
| Post-mutation `_rm_file` failure, non-not-found post-check, or path still present | `uncertain mutation state` |

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
tests prove unsupported options complete during command preflight without
entering a source.

Native `vosfs` hermetic evidence does not broaden into a general service
guarantee.
