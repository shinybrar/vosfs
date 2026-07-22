# `fsspec-cli` verified same-source two-operand `cp` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add verified same-source two-operand cp](https://github.com/shinybrar/vosfs/issues/137)

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

Client baseline: **fsspec 2026.6.0**

## Post-profile async constraint

Production CLI orchestration and filesystem calls remain async-only. Source
acquisition, cleanup, cancellation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope

This contract defines the first copy reduced profile:

```text
cp [--] source:/file source:/target
```

Both operands use the same configured source name. The source MUST be
source-reported `type == "file"`. For distinct configured names, use
[verified cross-source `cp`](fsspec-cli-cross-source-cp-command-profile.md).
Multi-source, directory source, recursive copy, and every option remain outside
this profile.

A passing row proves target resolution, replacement, destination file type and
size, comparable metadata-token agreement, diagnostics, cleanup, and
partial-state reporting only. Mode-less sources do not prove POSIX Issue 8
creation mode, ownership, link identity, timestamps, or other characteristics.

`type == "file"` is only fsspec's common type shape. It does not prove POSIX
regular-file or non-link identity.

## 2. Mapped filesystem operands

Operand grammar matches the plain-`ls` profile. Exactly two operands select
this profile. Configured source names, not Python object identity or protocol
strings, define same-source behavior.

### 2.1 Option and operand preflight

Before any source factory call, context entry, backend call, temporary
creation, or stdout byte, the command MUST validate:

1. option syntax;
2. the presence of at least two operands;
3. every operand's grammar;
4. every mapped filesystem name.

`--` ends option parsing. Typer's framework-owned `--help` short circuit is
explicitly exempt. Every other command option is unsupported.

| Condition | Diagnostic |
| --- | --- |
| Fewer than two operands | `cp: missing mapped filesystem operand` |
| Unsupported option token | `cp: <option token>: unsupported option` |
| Malformed operand | `cp: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `cp: <operand>: unknown filesystem (known: <name>, ...)` |

Three or more operands are not a usage error; they select the
[verified multi-source `cp` profile](fsspec-cli-multi-source-cp-command-profile.md),
which requires an existing destination directory.

`cp -R` and `cp -r` select the separate implemented
[recursive-copy profile](fsspec-cli-recursive-cp-rejection-profile.md) unless
`capabilities.recursion.copy` is explicitly false.

## 3. Target resolution

After acquiring the one referenced source, the command MUST:

1. await `_info(source_path)` and require `type == "file"` with a non-negative
   integer `size`;
2. resolve the destination:
   - if the destination path identifies an existing directory, append the
     source basename;
   - otherwise use the exact destination path;
3. require that any existing resolved destination is a replaceable file (not a
   directory or other type);
4. require `type == "directory"` for the resolved destination parent, reusing
   the validated destination metadata only when that destination is a directory
   and its path is exactly the resolved parent; otherwise await
   `_info(parent)`; and
5. reject exact same configured source name plus exact same backend path before
   mutation.

## 4. Transfer and success proof

Production code MUST await `_cp_file(source_path, resolved_destination)` exactly
once. It MUST NOT call public synchronous facades, retries, alternate-operation
fallbacks, concurrency, or transport replay.

Immediately after validating source `_info`, command freezes the expected size
and recognized source tokens into an immutable proof. Destination resolution and
mutation cannot change that proof through a backend-owned mutable mapping.

Success requires the destination to be source-reported `type == "file"` and to
report the expected byte count from the pre-copy source `_info`. Verification
recognizes exact `str` or `bytes` values under `ETag` / `etag`, `md5`,
`content-md5` / `content_md5`, and `checksum`. Aliases are normalized; every
normalized field present on both source and destination MUST match exactly. No
shared recognized field means exact type and size are the truthful success
proof, preserving Local and Memory source forms. These tokens are backend
metadata, not a claim of cryptographic strength.

Verification MUST await destination `_info` exactly once and MUST NOT download
source or destination bytes, create a local verification temporary, call a
public checksum or synchronous facade, or branch on backend type. Any future
byte comparison requires a separately profiled explicit opt-in and MUST run its
blocking comparison through `asyncio.to_thread`.

The command MUST NEVER delete the source. A failed or unverifiable copy MAY
leave a partial or complete destination; diagnostics MUST disclose residue and
MUST NOT claim rollback, transactionality, or atomicity.

## 5. Runtime failures and diagnostics

Diagnostics use this shape:

```text
cp: <mapped operand>: <stable category>
```

| Exception or condition | Category |
| --- | --- |
| Pre-mutation `FileNotFoundError` | `not found` |
| Pre-mutation `PermissionError` | `permission denied` |
| Pre-mutation source directory / `IsADirectoryError` | `is a directory` |
| Pre-mutation parent that is a file | `not a directory` |
| Pre-mutation same resolved path | `same path` |
| Pre-mutation `NotImplementedError` | `unsupported operation` |
| Pre-mutation invalid consumed backend shape | `incompatible result` |
| Pre-mutation any other backend exception | `backend failure (<class>): <message>` |
| `_cp_file` exception | `uncertain mutation state; destination residue may remain` |
| Post-copy type/size/comparable-token mismatch | `verification failure; destination residue may remain` |

## 6. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | Metadata-verified copy completed and the source was retained. |
| `1` | Source-lifecycle, backend, verification, residue, or cleanup failure. |
| `2` | Usage, option, mapped-operand, or mapped-name preflight failed. |

## 7. Downstream ownership

- [Tested command matrix contract](fsspec-cli-tested-command-matrix.md) owns
  source-form dispositions and immutable evidence IDs.
- [Cross-source `cp`](fsspec-cli-cross-source-cp-command-profile.md) owns
  distinct configured-name file copy. Multi-source and recursive copy retain
  separate profiles.
