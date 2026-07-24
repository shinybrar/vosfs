# `fsspec-cli` XSI `unlink` command profile

<!-- pyml disable line-length -->

> Current interface ownership: [ADR 0005](../adr/0005-define-typer-owned-commands-and-callback-extensions.md).

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
Typer owns argument arity, option handling, `--`, help, and framework usage
errors. Its exact rendered wording is not part of this profile. The callback
then validates the mapped operand and destructive-path guards before event-loop
entry or source acquisition.

| Condition | Diagnostic |
| --- | --- |
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

Pre-mutation failures happen before `_rm_file` and use the confirmed categories
below. Any failure after `_rm_file` begins — including `_rm_file` exceptions,
non-`FileNotFoundError` post-check exceptions, and a path that still resolves —
is uncertain mutation state. The command MUST NOT reuse confirmed pre-mutation
categories for that phase.

| Exception or condition | Category |
| --- | --- |
| Pre-mutation `FileNotFoundError` | `not found` |
| Pre-mutation `PermissionError` | `permission denied` |
| Pre-mutation `IsADirectoryError` or source-reported directory | `is a directory` |
| Pre-mutation `NotImplementedError` | `unsupported operation` |
| Pre-mutation invalid consumed backend shape | `incompatible result` |
| Pre-mutation any other backend exception | `backend failure (<class>): <message>` |
| Post-mutation `_rm_file` failure, non-not-found post-check, or path still present | `uncertain mutation state` |

## 5. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | The file was removed and absence was confirmed. |
| `1` | Source-lifecycle, backend, incompatible-result, or cleanup failure. |
| `2` | Usage, option, mapped-operand, mapped-name, or safety-guard preflight failed. |
