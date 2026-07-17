# `fsspec-cli` XSI `unlink` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add XSI unlink for one mapped file](https://github.com/shinybrar/vosfs/issues/131)

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

Client baseline: **fsspec 2026.6.0**

## 1. Scope

This contract defines the XSI-optional thin primitive:

```text
unlink [--] name:/path
```

The command operates only on invocation-owned filesystems yielded by configured
async filesystem sources through `App(sources).typer_app`. It accepts exactly
one mapped source-reported file operand and never enables directory or recursive
deletion.

The profile is deliberately smaller than POSIX Issue 8 `unlink` plus GNU
extensions:

- exactly one mapped filesystem operand is required;
- only entries classified as fsspec `type == "file"` are removed;
- no options are admitted; and
- success emits no stdout.

`type == "file"` is only fsspec's common type shape. It does not prove POSIX
regular-file or non-link identity.

## 2. Mapped filesystem operands

Operand grammar matches the plain-`ls` profile. One operand only. Repeated
operands are a usage error, not a multi-delete command.

Before source acquisition, the command MUST reject:

- configured source root (`name:/`);
- any operand whose final path component is `.` or `..` after removing trailing
  slash characters.

These are source-free safety guards with exit status `2`.

### 2.1 Option and operand preflight

Before any source factory call, context entry, backend call, or stdout output,
the command MUST validate:

1. option syntax;
2. the presence of exactly one operand;
3. every operand's grammar;
4. every mapped filesystem name; and
5. root and final dot-component safety guards.

`--` ends option parsing. Typer's framework-owned `--help` short circuit is
explicitly exempt. Every other command option is unsupported.

| Condition | Diagnostic |
| --- | --- |
| No operands | `unlink: missing mapped filesystem operand` |
| More than one operand | `unlink: extra operand` |
| Unsupported option token | `unlink: <option token>: unsupported option` |
| Malformed operand | `unlink: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `unlink: <operand>: unknown filesystem (known: <name>, ...)` |
| Root or final `.` / `..` | `unlink: <operand>: rejected path` |

## 3. Backend operation semantics

Production code MUST await, in order:

1. `_info(path)` and require `type == "file"`;
2. `_rm_file(path)` once; and
3. `_info(path)` again, requiring a distinguishable `FileNotFoundError`.

The command MUST NOT call `_rm`, `_rmdir`, `_ls`, listing, recursive removal,
or public synchronous facades. Non-`FileNotFoundError` post-check failures
remain visible.

## 4. Runtime failures and diagnostics

| Exception or condition | Category |
| --- | --- |
| `FileNotFoundError` | `not found` |
| `PermissionError` | `permission denied` |
| `IsADirectoryError` or source-reported directory | `is a directory` |
| `NotImplementedError` | `unsupported operation` |
| Invalid consumed backend shape or path still present | `incompatible result` |
| Any other backend exception | `backend failure (<class>): <message>` |

## 5. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | The file was removed and absence was confirmed. |
| `1` | Source-lifecycle, backend, incompatible-result, or cleanup failure. |
| `2` | Usage, option, mapped-operand, mapped-name, or safety-guard preflight failed. |
