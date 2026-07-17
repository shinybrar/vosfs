# `fsspec-cli` `dirname string` command profile

<!-- pyml disable line-length -->

Status: **Locked lexical command semantics**

Question: [Add source-free `dirname string`](https://github.com/shinybrar/vosfs/issues/125)

Client baseline: **fsspec 2026.6.0**

## 1. Scope

This contract defines the source-free lexical command:

```text
dirname string
```

The command applies the POSIX Issue 8 `dirname` string algorithm to exactly
one host-decoded argv token. It never interprets the token as a mapped
filesystem operand, never validates a configured source name, and never
acquires a source or performs a filesystem call.

The supported surface is deliberately smaller than GNU `dirname`:

- exactly one string operand is required;
- no command options are supported; and
- zero-delimited or multi-operand modes remain unsupported under separate
  profiles.

## 2. Operand preflight

Before any stdout output, the command MUST validate:

1. option syntax;
2. the presence of exactly one operand; and
3. that the operand contains no NUL byte.

`--` ends option parsing. Typer's framework-owned `--help` short circuit is
explicitly exempt from this command compatibility profile: its text and
successful exit are not `dirname string` behavior. Every other command
option, including `-a`, `-z`, and grouped short-option tokens, is unsupported.

The first preflight error in argument order MUST produce one diagnostic and
exit `2`. No source may be entered and no stdout output may be written before
it. These are the exact preflight diagnostics, before the diagnostic rendering
defined in Section 5:

| Condition | Diagnostic |
| --- | --- |
| No operands | `dirname: missing operand` |
| More than one operand | `dirname: extra operand` |
| Unsupported option token | `dirname: <option token>: unsupported option` |
| Operand containing NUL | `dirname: <operand>: invalid operand` |

Option tokens and operands are inspected from left to right. A grouped option
token is unsupported as a whole. A lone `-` is a valid operand, not an option.

Embedded newline in the operand is data, not a preflight error. A token such as
`memory:/docs/a.txt` is ordinary lexical data; the command MUST NOT validate
the source name, check existence, or normalize the token for later filesystem
use.

## 3. Lexical algorithm

The command MUST apply the POSIX Issue 8 `dirname` string algorithm to the
operand:

1. If the operand contains no slash (`/`) character, the result is a single
   dot (`.`).
2. If the operand consists entirely of slash characters, the result is a
   single slash character. This deterministic rule resolves the Issue 8
   ambiguity for inputs such as `//` and `///`.
3. Otherwise, remove trailing slash characters from the operand.
4. If the operand no longer contains a slash character, the result is `.`.
5. Otherwise, remove the suffix starting with the final slash character.
6. If the remaining string is empty, the result is `/`.

Examples:

```text
a            -> .
a/b          -> a
/a/b         -> /a
/            -> /
//           -> /
a/b/         -> a
memory:/x/y  -> memory:/x
.            -> .
..           -> ..
dir\nname    -> .
a\n/b        -> a\n
```

The command MUST NOT expand `~`, resolve dot segments, normalize separators
for filesystem use, or infer a default source.

## 4. Standard output

The command writes exactly one result string followed by one newline. TTY and
redirected invocations MUST produce byte-equivalent content for the same
operand. The command MUST NOT quote, escape, or color the result.

## 5. Diagnostic rendering and exit status

Every diagnostic is terminated by one newline. For diagnostics only, each
inserted option token or operand is rendered by replacing, in order, `\` with
`\\`, NUL with `\0`, carriage return with `\r`, and newline with `\n`; every
other character is unchanged. Literal command text and stable categories are
not transformed. This is the only diagnostic escaping algorithm.

| Status | Meaning |
| ---: | --- |
| `0` | Exactly one valid operand completed successfully. |
| `2` | Usage or option preflight failed. |

## 6. Downstream ownership

- [Tested command matrix contract](fsspec-cli-tested-command-matrix.md) owns
  matrix statuses, the `source-free command` scope, and hermetic evidence
  rules.
- Multi-operand and zero-delimited GNU extensions remain outside this profile
  and are rejected through the `extra operand` diagnostic.

## Primary evidence

- [POSIX Issue 8 `dirname`](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/dirname.html)
- Hermetic App-seam tests in
  [`test_dirname.py`](../../src/fsspec-cli/tests/test_dirname.py),
  [`test_dirname_process.py`](../../src/fsspec-cli/tests/test_dirname_process.py),
  and
  [`test_command_matrix.py`](../../src/fsspec-cli/tests/test_command_matrix.py)
