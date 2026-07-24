# `fsspec-cli` binary stdin and `-` sequencing for `cat`

<!-- pyml disable line-length -->

> Current interface ownership: [ADR 0005](../adr/0005-define-typer-owned-commands-and-callback-extensions.md).

Status: **Locked stdin admission contract atop mapped-file `cat`**

Question: [Binary stdin and `-` sequencing](https://github.com/shinybrar/vosfs/issues/127)

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

Base profile: [Plain mapped-file `cat`](fsspec-cli-plain-cat-command-profile.md)

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

This contract extends mapped-file `cat` with POSIX Issue 8 stdin shapes:

```text
cat
cat -
cat name:/file - name:/other
```

Operand-free `cat` reads binary stdin once. Each operand `-` reads the same
stdin stream at that argv position. Repeated `-` occurrences observe the
stream's current position; later occurrences normally see EOF after an earlier
occurrence drained the stream.

Mapped-file operands keep the base profile's `_info` / `_get_file` staging
rules, ownership ledger, and bounded binary stdout forwarding. `-u` remains
unsupported. Named local files, terminal-device policy, retry, tee, progress,
concurrency, and multiple independent stdin streams remain out of scope.

## 2. Operand preflight

Typer owns argument collection, option handling, `--`, help, and framework
usage errors. Its exact rendered wording is not part of this profile. A
framework failure exits `2` before callback execution, stdin reads, or source
entry. Zero operands MUST expand to one implicit stdin read.
An explicit `-` is a stdin operand. Mapped operands retain the base profile
grammar and are callback-validated before event-loop entry or source
acquisition.

| Condition | Diagnostic |
| --- | --- |
| Malformed mapped operand | `cat: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `cat: <operand>: unknown filesystem (known: <name>, <name>, ...)` |

## 3. Acquisition barrier before stdin

When at least one mapped operand is present, the command MUST acquire every
distinct referenced source once in first-appearance order before any stdin
byte is read and before the first `_info`, `_get_file`, temporary creation, or
stdout byte. Operand-free and dash-only invocations MUST NOT enter sources.

## 4. Stdin forwarding

Stdin MUST be read through the process binary stdin buffer in bounded chunks
using the same stdout write, short-write, broken-pipe, and accepted-byte rules
as mapped-file forwarding. The command MUST NOT:

- decode text;
- convert newlines;
- seek stdin;
- buffer proportionally to input size; or
- invent a second independent stdin stream.

Exact byte order across file/stdin/file boundaries MUST be preserved with no
separator. A stdin read failure diagnoses that position as
`cat: -: staging failure (<class>): <message>` and continues under the base
profile continuation rule. Output failure always stops.

## 5. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | Every operand, including stdin positions, completed successfully. |
| `1` | A source-lifecycle, backend, incompatible-result, staging, stdin-read, or output-write failure occurred. |
| `2` | Usage, option, mapped-operand, or mapped-name preflight failed. |

## 6. Downstream ownership

- [Plain mapped-file `cat`](fsspec-cli-plain-cat-command-profile.md) owns
  mapped-file staging and ownership.
- [Tested command matrix contract](fsspec-cli-tested-command-matrix.md) owns
  matrix statuses and deterministic evidence rules.

## Primary evidence

- [POSIX Issue 8 `cat`](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cat.html)
- [Issue #127](https://github.com/shinybrar/vosfs/issues/127)
- [fsspec backend tribal knowledge](../research/fsspec-backend-tribal-knowledge.md)
  whole-object staging, explicit resource ownership, no Range, no transport replay
