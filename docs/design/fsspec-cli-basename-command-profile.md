# `fsspec-cli` `basename string` command profile

<!-- pyml disable line-length -->

Status: **Locked lexical command semantics**

Question: [Add source-free `basename string`](https://github.com/shinybrar/vosfs/issues/123)

Client baseline: **fsspec 2026.6.0**

## 1. Scope

This contract defines the first source-free lexical command:

```text
basename string
```

The command applies the POSIX Issue 8 `basename` string algorithm to exactly
one host-decoded argv token. It never interprets the token as a mapped
filesystem operand, never validates a configured source name, and never
acquires a source or performs a filesystem call.

The supported surface is deliberately smaller than GNU `basename`:

- exactly one string operand is required in the base profile;
- no command options are supported; and
- the optional suffix operand is defined in a
  [separate profile](fsspec-cli-basename-suffix-command-profile.md).

## 2. Operand preflight

Before any stdout output, the command MUST validate:

1. option syntax;
2. the presence of exactly one operand; and
3. that the operand contains no NUL byte.

`--` ends option parsing. Typer's framework-owned `--help` short circuit is
explicitly exempt from this command compatibility profile: its text and
successful exit are not `basename string` behavior. Every other command
option, including `-a`, `-s`, and grouped short-option tokens, is unsupported.

The first preflight error in argument order MUST produce one diagnostic and
exit `2`. No source may be entered and no stdout output may be written before
it. These are the exact preflight diagnostics, before the diagnostic rendering
defined in Section 5:

| Condition | Diagnostic |
| --- | --- |
| No operands | `basename: missing operand` |
| More than two operands | `basename: extra operand` |
| Unsupported option token | `basename: <option token>: unsupported option` |
| Operand containing NUL | `basename: <operand>: invalid operand` |

Option tokens and operands are inspected from left to right. A grouped option
token is unsupported as a whole. A lone `-` is a valid operand, not an option.

Embedded newline in the operand is data, not a preflight error. A token such as
`memory:/docs/a.txt` is ordinary lexical data; the command MUST NOT validate
the source name, check existence, or normalize the token for later filesystem
use.

## 3. Lexical algorithm

The command MUST apply the POSIX Issue 8 `basename string` algorithm to the
operand:

1. If the operand consists entirely of slash (`/`) characters, the result is a
   single slash character. This deterministic rule resolves the Issue 8
   ambiguity for inputs such as `//` and `///`.
2. Otherwise, remove trailing slash characters from the operand.
3. If the operand still contains at least one slash character, remove the
   prefix through and including the final slash character.
4. Write the remaining string.

Examples:

```text
a            -> a
a/b          -> b
/a/b         -> b
/            -> /
//           -> /
a/b/         -> b
memory:/x/y  -> y
.            -> .
..           -> ..
dir\nname    -> dir\nname
```

The command MUST NOT expand `~`, resolve dot segments, normalize separators
for filesystem use, or infer a default source.

## 4. Standard output

The command writes exactly one result string followed by one newline. TTY and
redirected invocations MUST produce byte-equivalent content for the same
operand. The command MUST NOT quote, escape, or color the result.

## 5. Diagnostic rendering and exit status

Every diagnostic is terminated by one newline. For diagnostics only, each
inserted option token or operand is rendered by first replacing `\` with `\\`,
then escaping every control character (any code point below U+0020, or U+007F
DELETE) as a lowercase `\xNN` hex sequence; every other character is unchanged.
Literal command text and stable categories are not transformed. This is the
only diagnostic escaping algorithm.

| Status | Meaning |
| ---: | --- |
| `0` | Exactly one valid operand completed successfully. |
| `2` | Usage or option preflight failed. |

## 6. Downstream ownership

- [Tested command matrix contract](fsspec-cli-tested-command-matrix.md) owns
  matrix statuses, the `source-free command` scope, and hermetic evidence
  rules.
- The [optional suffix profile](fsspec-cli-basename-suffix-command-profile.md)
  owns the two-operand GNU extension and every rejected third-operand shape.

## Primary evidence

- [POSIX Issue 8 `basename`](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/basename.html)
- Hermetic App-seam tests in
  [`test_basename.py`](../../src/fsspec-cli/tests/test_basename.py),
  [`test_basename_process.py`](../../src/fsspec-cli/tests/test_basename_process.py),
  and
  [`test_command_matrix.py`](../../src/fsspec-cli/tests/test_command_matrix.py)
