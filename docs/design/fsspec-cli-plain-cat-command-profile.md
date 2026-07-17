# `fsspec-cli` plain mapped-file `cat` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add binary mapped-file `cat`](https://github.com/shinybrar/vosfs/issues/126)

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

This contract defines the first binary content profile:

```text
cat [--] name:/path...
```

Stdin and bare `-` are owned by the separate
[binary stdin and `-` sequencing profile](fsspec-cli-cat-stdin-command-profile.md).
`-u` remains outside both profiles. The command operates only on
invocation-owned filesystems yielded by configured async filesystem sources
through `App(sources).typer_app`. It does not own source configuration or
authentication and does not branch on backend type.

The mapped-file profile is deliberately smaller than POSIX Issue 8 `cat`:

- only entries classified as fsspec `type == "file"` are supported;
- no options are admitted; and
- stdout is exact binary concatenation with no text conversion.

Unsupported behavior is rejected rather than emulated. `type == "file"` is only
fsspec's common type shape. It does not prove POSIX regular-file or non-link
identity.

## 2. Mapped filesystem operands

A mapped filesystem operand has the exact form `<name>:/<path>`.

- `name` MUST be non-empty, MUST NOT contain `:`, and MUST exactly match one
  key in the configured source mapping.
- The path portion MUST begin with `/`. `name:/` selects filesystem root.
- Parsing splits on the first `:` only. Later colons belong to the path.
- The complete path portion, including its leading `/`, MUST be passed to the
  selected filesystem unchanged.
- The command MUST NOT expand `~`, resolve dot segments, normalize separators,
  strip a backend protocol, or infer a default filesystem.
- Bare paths, `name:`, protocol URLs, and unknown names are invalid.

One or more mapped operands are accepted. Repeated operands remain repeated, and
one invocation MAY address several configured filesystems. Zero operands and
bare `-` are defined by the stdin profile, not this mapped-file profile.

### 2.1 Option and operand preflight

Before any source factory call, context entry, backend call, temporary
creation, or stdout byte, the command MUST validate:

1. option syntax;
2. every mapped operand's grammar; and
3. every mapped filesystem name.

`--` ends option parsing. Typer's framework-owned `--help` short circuit is
explicitly exempt from this command compatibility profile. Every command
option, including `-u`, grouped short options, and long options, is
unsupported. Bare `-` is admitted only by the stdin profile.

The first preflight error in argument order MUST produce one diagnostic and
exit `2`. No source may be entered, no backend call made, no temporary created,
and no stdout output written before it. An unknown-name diagnostic MUST include
every configured name in locale-sorted order. These are the exact preflight
diagnostics, before the diagnostic rendering defined in Section 6:

| Condition | Diagnostic |
| --- | --- |
| Unsupported option token | `cat: <option token>: unsupported option` |
| Malformed operand | `cat: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `cat: <operand>: unknown filesystem (known: <name>, <name>, ...)` |

Option tokens and operands are inspected from left to right. An explicit
operand containing NUL or newline is also a preflight error.

## 3. Backend operation semantics

Production code MUST NOT invoke fsspec's synchronous facades, whole-object
in-memory `cat`, or text `open` facades. For every preflight-valid operand, it
MUST:

1. await the selected filesystem's version-tested, documented `_info(path)`
   coroutine;
2. require a mapping whose `type` is exactly the string `"file"`; and
3. await the pinned async download primitive `_get_file(remote, local)` into
   one secure invocation-owned disk temporary.

Any other or missing `type` is an incompatible result. The command MUST NOT
guess directory, link, device, or stdin behavior. The command MUST NOT use
underscore hooks other than the version-tested, documented `_info` and
`_get_file` coroutines required by this profile. It also MUST NOT use public
synchronous filesystem facades, retries, alternate-operation fallbacks,
concurrency, transport replay, or remote `Range` assumptions.

## 4. Staging and standard output

Each remote object MUST be staged through exactly one secure local temporary at
a time. Peak CLI memory MUST remain bounded independently of object size.
After a successful `_get_file`, the command MUST forward the temporary's bytes
to binary stdout in bounded chunks with:

- no decoding;
- no headers or separators;
- no newline insertion or removal; and
- no text normalization.

TTY and redirected invocations MUST produce byte-equivalent content for the
same backend results. Per-operand atomicity covers backend validation and
staging: no bytes from a failed staging operand MAY reach stdout. Earlier
successfully forwarded bytes remain emitted.

Temporary files MUST always be closed and removed after success, ordinary
failure, cancellation, broken pipe, output failure, or source cleanup failure.
Temporary paths and file bytes MUST NOT appear in diagnostics or evidence.

## 5. Runtime failures and diagnostics

Operands MUST be processed in their original argument order. All distinct
referenced sources MUST be acquired once in first-appearance order before the
first `_info`, `_get_file`, temporary creation, or stdout byte.

A read, validation, or local staging failure diagnoses that operand and
continues to later operands. A stdout failure stops further reads and output,
preserves accepted bytes, and still runs temporary and source cleanup.

Diagnostics use this shape:

```text
cat: <mapped operand>: <stable category>
```

The recognized exception-class mapping is:

| Exception or condition | Category |
| --- | --- |
| `FileNotFoundError` | `not found` |
| `PermissionError` | `permission denied` |
| `NotADirectoryError` | `not a directory` |
| `IsADirectoryError` | `is a directory` |
| `NotImplementedError` | `unsupported operation` |
| Invalid consumed backend shape | `incompatible result` |
| Local temporary create/write/read/cleanup failure | `staging failure (<class>): <message>` |
| Any other backend exception | `backend failure (<class>): <message>` |

Exception rows are tested top to bottom with `isinstance`. Categories MUST be
selected by exception class or validated result shape, never by parsing errno
values or message text. For fallback categories, `<class>` is exactly
`type(exception).__name__` and `<message>` is exactly `str(exception)` before
diagnostic rendering.

Every diagnostic is terminated by one newline. For diagnostics only, each
inserted option token, operand, configured name, exception class, and exception
message is rendered by replacing, in order, `\\` with `\\\\`, NUL with `\\0`,
carriage return with `\\r`, and newline with `\\n`; every other character is
unchanged. Literal command text and stable categories are not transformed.
This is the only diagnostic escaping algorithm. No traceback is written.

A stdout write failure is a runtime failure. `BrokenPipeError` stops output
immediately, writes no diagnostic or traceback for that output fault, and exits
`1`; bytes already accepted by the output stream cannot be retracted. Every
other stdout write exception emits exactly
`cat: output: output failure (<class>): <message>`, using the same class,
message, and rendering rules, then exits `1`. A short stdout write that accepts
fewer bytes than requested is an output failure.

## 6. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | Every operand completed successfully. |
| `1` | A source-lifecycle, backend, incompatible-result, staging, or output-write failure occurred. |
| `2` | Usage, option, mapped-operand, or mapped-name preflight failed. |

## 7. Downstream ownership

- [Tested command matrix contract](fsspec-cli-tested-command-matrix.md) owns
  matrix statuses, versions, and hermetic-versus-live evidence rules.
- [Binary stdin and `-` sequencing](fsspec-cli-cat-stdin-command-profile.md)
  owns stdin admission, dash sequencing, and operand-free reads without
  changing mapped-file staging rules in this profile.

## Primary evidence

- [POSIX Issue 8 `cat`](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cat.html)
- [Issue #120](https://github.com/shinybrar/vosfs/issues/120) mapped-file `cat` contract
- [fsspec backend tribal knowledge](../research/fsspec-backend-tribal-knowledge.md)
  whole-object staging, explicit resource ownership, no Range, no transport replay
