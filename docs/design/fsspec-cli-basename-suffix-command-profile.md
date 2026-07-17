# `fsspec-cli` `basename string suffix` command profile

<!-- pyml disable line-length -->

Status: **Locked lexical suffix delta**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#124](https://github.com/shinybrar/vosfs/issues/124)

Client baseline: **fsspec 2026.6.0**

## 1. Scope

This contract extends the locked
[`basename string` profile](fsspec-cli-basename-command-profile.md) with one
optional second operand:

```text
basename string suffix
```

The command still never interprets either operand as a mapped filesystem
operand, never validates a configured source name, and never acquires a source
or performs a filesystem call.

The supported surface remains deliberately smaller than GNU `basename`:

- one or two string operands are accepted;
- no command options are supported; and
- GNU multi-string modes, extension lists, pattern syntax, case folding,
  filesystem queries, and `dirname` changes remain out of scope.

## 2. Operand preflight

The base profile's option syntax, NUL rejection, `--` delimiter behavior, and
framework-owned `--help` exemption apply unchanged. This profile adds only the
second-operand shape:

1. exactly one operand remains valid and preserves the base profile byte-for-byte;
2. exactly two operands select the suffix form; and
3. three or more operands produce `basename: extra operand`.

Both operands are inspected for NUL bytes using the same diagnostic rendering as
the base profile. Embedded newline in either operand is data, not a preflight
error. A colon, slash, source-like prefix, Unicode, or embedded newline in the
suffix has no backend meaning.

| Condition | Diagnostic |
| --- | --- |
| Three or more operands | `basename: extra operand` |
| Operand or suffix containing NUL | `basename: <token>: invalid operand` |

Every other preflight diagnostic remains owned by the base profile.

## 3. Lexical algorithm

The command MUST apply the locked POSIX Issue 8 `basename string` algorithm to
the first operand first. Let `base` be that extracted string.

When a second operand is present, let `suffix` be that lexical string:

1. If `suffix` is empty, leave `base` unchanged.
2. If `suffix` is identical to the entire `base` string, leave `base`
   unchanged per Issue 8.
3. If `base` ends with `suffix`, remove exactly one trailing occurrence of
   `suffix`.
4. Otherwise leave `base` unchanged.

Examples after base extraction:

```text
foo.bar .bar     -> foo
foo.bar bar      -> foo.
foo.bar foo.bar  -> foo.bar
report.txt .txt  -> report
a/b/file.txt .txt -> file
c c              -> c
report.txt       -> report.txt
report.txt .pdf  -> report.txt
```

Suffix removal occurs only after base extraction. Inputs with no slash, root or
all-slash operands, repeated or trailing slashes, and source-looking prefixes
therefore prove suffix handling is post-base, not path parsing.

## 4. Standard output

The command writes exactly one result string followed by one newline. TTY and
redirected invocations MUST produce byte-equivalent content for the same
operands. The command MUST NOT quote, escape, or color the result.

## 5. Diagnostic rendering and exit status

Diagnostic rendering and exit status remain identical to the base profile.

| Status | Meaning |
| ---: | --- |
| `0` | One or two valid operands completed successfully. |
| `2` | Usage or option preflight failed. |

## 6. Downstream ownership

- [Tested command matrix contract](fsspec-cli-tested-command-matrix.md) owns
  matrix statuses, the `source-free command` scope, and hermetic evidence
  rules.
- The base [`basename string` profile](fsspec-cli-basename-command-profile.md)
  owns one-operand semantics and shared preflight.

## Primary evidence

- [POSIX Issue 8 `basename`](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/basename.html)
- Hermetic App-seam tests in
  [`test_basename.py`](../../src/fsspec-cli/tests/test_basename.py),
  [`test_basename_process.py`](../../src/fsspec-cli/tests/test_basename_process.py),
  and
  [`test_command_matrix.py`](../../src/fsspec-cli/tests/test_command_matrix.py)
