# `fsspec-cli` `mkdir -p` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#129](https://github.com/shinybrar/vosfs/issues/129)

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

This contract defines parent-creating directory creation through the fsspec-owned
composite:

```text
mkdir -p [-p] [--] name:/path...
```

The command operates only on invocation-owned filesystems yielded by configured
async filesystem sources. It does not own source configuration or authentication
and does not branch on backend type.

The supported surface is deliberately smaller than POSIX Issue 8:

- `-p` MUST be present before operands; long options such as `--parents` are
  unsupported;
- at least one mapped filesystem operand is required;
- `-m`, grouped parent/mode options, and other long forms remain unsupported;
  and
- successful invocations emit no stdout.

[Base `mkdir`](fsspec-cli-base-mkdir-command-profile.md) without `-p` remains a
separate locked profile and MUST NOT be broadened by this profile.

## 2. Mapped filesystem operands

Operand grammar matches the
[plain `ls` profile](fsspec-cli-plain-ls-command-profile.md#2-mapped-filesystem-operands).

One or more operands are accepted. Repeated operands remain repeated, and one
invocation MAY address several configured filesystems. Zero operands is a
usage error.

## 2.1 Option and operand preflight

Before any source factory call, context entry, backend call, or command output,
the command MUST validate option syntax, operand presence, operand grammar, and
mapped filesystem names.

`--` ends option parsing. `-p` is idempotent when repeated or grouped. A grouped
option token is valid only when every option character is `p`; otherwise the
complete token is reported as unsupported. Option tokens after the first operand
are unsupported. Typer's framework-owned `--help` short circuit is explicitly
exempt from this command compatibility profile.

The first preflight error in argument order MUST produce one diagnostic and
exit `2`. No source may be entered and no stdout output written before it.

| Condition | Diagnostic |
| --- | --- |
| No operands | `mkdir: missing mapped filesystem operand` |
| Unsupported option token | `mkdir: <option token>: unsupported option` |
| Malformed operand | `mkdir: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `mkdir: <operand>: unknown filesystem (known: <name>, ...)` |

## 3. Backend operation semantics

Production code MUST NOT invoke fsspec's synchronous facades. After every
distinct referenced source is acquired sequentially in first-operand order, the
command MUST process operands sequentially in original order.

For each operand it MUST:

1. await `_makedirs(path, exist_ok=True)` at the pinned fsspec baseline;
2. await `_info(path)`; and
3. require the returned mapping to report `type == "directory"`.

A void `_makedirs` return alone is not success. The command MUST NOT enumerate,
split, or create individual parent components in CLI code. Parent creation is
delegated entirely to the backend composite.

An already-existing directory is success. An existing non-directory remains a
failure.

## 4. POSIX Issue 8 mode divergence

POSIX Issue 8 creation mode derives from an explicit or default mode and the
process umask. A mode-less fsspec source cannot provide that contract. A passing
matrix row means only that the source created the requested directory with its
documented default; it does not claim mode or umask semantics.

## 5. Standard output

Successful invocations MUST emit no stdout. There is no success banner,
confirmation line, or created-path listing.

## 6. Runtime failures and diagnostics

Operands MUST be processed in original argument order. An ordinary per-operand
failure MUST NOT stop later operands. Earlier successful directories MUST
remain created; the command MUST NOT claim rollback or atomicity.

Diagnostics use this shape:

```text
mkdir: <mapped operand>: <stable category>
```

| Exception or condition | Category |
| --- | --- |
| Confirmed `_makedirs` `FileNotFoundError` | `not found` |
| Confirmed `_makedirs` `FileExistsError` | `file exists` |
| Confirmed `_makedirs` `PermissionError` | `permission denied` |
| Confirmed `_makedirs` `NotADirectoryError` | `not a directory` |
| Confirmed `_makedirs` `NotImplementedError` | `unsupported operation` |
| Confirmed `_makedirs` any other backend exception | `backend failure (<class>): <message>` |
| Post-success `_info` backend exception | `uncertain state (<confirmed category>)` |
| Invalid or non-directory `_info` after `_makedirs` returned | `uncertain state (incompatible result)` |

Diagnostic rendering, escaping, cleanup precedence, and source lifecycle rules
match the plain `ls` profile and
[base `mkdir`](fsspec-cli-base-mkdir-command-profile.md).

## 7. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | Every operand succeeded and every entered source exited cleanly. |
| `1` | At least one operand failed, source cleanup failed, or control-flow precedence required failure. |
| `2` | Preflight rejected the invocation before source entry. |

## 8. Evidence

Hermetic matrix probes exercise adapted async Local, adapted async Memory, and
native async `vosfs` through the production `App` seam. Source-free rejection
tests prove `-m`, `--parents`, mixed option groups, options after operands, and
other unsupported forms complete during command preflight without entering a
source.

Native `vosfs` hermetic evidence does not broaden into a general service
guarantee.
