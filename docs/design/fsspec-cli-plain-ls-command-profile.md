# `fsspec-cli` plain `ls` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Define the plain `ls` command profile](https://github.com/shinybrar/vosfs/issues/79)

Client baseline: **fsspec 2026.6.0**

## Post-profile async constraint

After the plain-`ls` prototype verdict, the human locked all production CLI
orchestration and filesystem calls as async-only. The synchronous operations
named below remain evidence for observable semantics and backend result shapes;
they are not an allowed production execution strategy.

[Issue #90](https://github.com/shinybrar/vosfs/issues/90) established the async
constraints. [Issue #92](https://github.com/shinybrar/vosfs/issues/92) resolved
them through the invocation-owned source contract recorded in
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md). Production
uses `App(sources).typer_app`; every other command-semantic requirement remains
locked. Source acquisition, cleanup, cancellation, and failure precedence
follow [ADR 0003](../adr/0003-acquire-command-sources-before-filesystem-work.md).

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope

This contract defines the observable behavior handed to
[Prototype plain `ls` across the required filesystem matrix](https://github.com/shinybrar/vosfs/issues/80).
It covers the no-option command and `-A`:

```text
ls [-A] [--] name:/path...
```

The command operates only on invocation-owned filesystems yielded by configured
async filesystem sources through `App(sources).typer_app`. It does not own
source configuration or authentication and does not branch on backend type.

The supported surface is deliberately smaller than POSIX Issue 8:

- at least one mapped filesystem operand is required;
- only entries classified as `file` or `directory` are supported;
- `-A` is the only command option in this profile; and
- terminal output uses the same one-entry-per-line form as redirected output.

Unsupported behavior is rejected rather than emulated. In particular, `-a`,
columns, color, quoting, recursion, and metadata decoration are outside this
profile. `-l` remains unsupported under its locked
[strict rejection profile](fsspec-cli-ls-long-rejection-profile.md).

## 2. Mapped filesystem operands

A mapped filesystem operand has the exact form `<name>:/<path>`.

- `name` MUST be non-empty, MUST NOT contain `:`, and MUST exactly match one
  key in the configured source mapping.
- `App` construction MUST reject any configured mapping name containing NUL;
  such a name MUST NOT reach command preflight or locale sorting.
- The path portion MUST begin with `/`. `name:/` selects filesystem root.
- Parsing splits on the first `:` only. Later colons belong to the path.
- The complete path portion, including its leading `/`, MUST be passed to the
  selected filesystem unchanged.
- The command MUST NOT expand `~`, resolve dot segments, normalize separators,
  strip a backend protocol, or infer a default filesystem.
- Bare paths, `name:`, and unknown names are invalid.

One or more operands are accepted. Repeated operands remain repeated, and one
invocation MAY address several configured filesystems. Zero operands is a
usage error; POSIX's implicit `.` cannot select a filesystem honestly.

Examples:

```text
local:/                  valid root
local:/tmp/a:b           valid path containing a later colon
local:tmp                invalid: path has no leading slash
/tmp                     invalid: no mapped filesystem name
```

### 2.1 Option and operand preflight

Before any source factory call, context entry, backend call, or command output,
the command MUST validate:

1. option syntax;
2. the presence of at least one operand;
3. every operand's grammar; and
4. every mapped filesystem name.

`--` ends option parsing. `-A` is idempotent when repeated or grouped. Typer's
framework-owned `--help` short circuit is explicitly exempt from this command
compatibility profile: its text and successful exit are not plain-`ls`
behavior. Every other command option, including `-h`, `-a`, and `-l`, is
unsupported.

The first preflight error in argument order MUST produce one diagnostic and
exit `2`. No source may be entered, no backend call made, and no stdout output
written before it. An unknown-name diagnostic MUST include every configured
name in locale-sorted order. These are the exact preflight diagnostics, before
the diagnostic rendering defined in Section 6:

| Condition | Diagnostic |
| --- | --- |
| No operands | `ls: missing mapped filesystem operand` |
| Unsupported option token | `ls: <option token>: unsupported option` |
| Malformed operand | `ls: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `ls: <operand>: unknown filesystem (known: <name>, <name>, ...)` |

Option tokens and operands are inspected from left to right. A grouped option
token is valid only when every option character is `A`; otherwise the complete
token is reported as unsupported. Known names in the last diagnostic are each
rendered independently and joined by comma-space (`U+002C U+0020`).

An explicit operand containing NUL or newline is also a preflight error. NUL
is not a POSIX pathname byte; rejecting newline is the profile's chosen
one-record-per-line rule, consistent with POSIX Issue 8 future direction.

## 3. Backend operation semantics

Production code MUST NOT invoke fsspec's synchronous facades. For every
preflight-valid operand, it MUST await the selected filesystem's
version-tested, documented `_info(path)` coroutine first, then apply the result
rules below.

- `type == "file"`: the operand is a non-directory result. The command MUST
  NOT call `ls` for it.
- `type == "directory"`: the command MUST await `_ls(path, detail=False)`
  explicitly.
- Any other or missing `type` is an incompatible result. The command MUST NOT
  guess file, directory, device, or link behavior.

The `info` result MUST be a mapping with a string `type` consumed as above.
Other metadata is irrelevant to this profile. The names-only `ls` result MUST
be a concrete list of strings. The command validates each returned string
lexically against the requested directory path:

1. Remove trailing `/` characters from the requested path for this comparison
   only. If nothing remains, the child prefix is `/`; otherwise it is the
   remaining path followed by `/`.
2. The returned string MUST begin with that exact prefix.
3. Its suffix after the prefix MUST be non-empty and MUST NOT contain `/`.
   That suffix is the displayed basename.

The comparison never changes the path passed to the backend. A returned string
that fails these steps, or whose basename contains NUL or newline, makes that
directory operand incompatible. This rejects protocol-bearing, unrelated, and
nested results without guessing how a backend normalized them.

This strategy intentionally does not use the observed but abstractly
unguaranteed `ls(file)` behavior. It also deliberately supports fewer
non-directory types than POSIX: Local special files and `vosfs` LinkNodes can
be incompatible rather than misrepresented.

The command MUST NOT use underscore hooks other than the version-tested,
documented `_info` and `_ls` coroutines required by this profile. It also MUST
NOT use public synchronous filesystem facades, `exists`, `isfile`, `isdir`, a
static capability registry, backend-type checks, retry fallbacks, or fabricated
results. `NotImplementedError` from a real awaited operation is runtime
evidence that the operation is unsupported.

## 4. Selection and sorting

Without `-A`, directory results whose displayed basename begins with `.` MUST
be omitted. An explicitly named dot-prefixed operand remains valid.

With `-A`, every backend-returned child MUST be included except exact basename
`.` or `..`. The command MUST NOT synthesize either entry. Full POSIX `-a`
remains unsupported because the abstract fsspec listing surface cannot prove
those entries honestly.

Backend return order MUST NOT affect output. The command MUST:

1. sort successful non-directory operands by their displayed mapped spelling;
2. sort successful directory operands separately by their displayed mapped
   spelling; and
3. sort each directory's selected child basenames independently.

Sorting uses the host process's current `LC_COLLATE`; the library MUST NOT
change locale state. Equal collation keys use the raw Python string as the
deterministic tie-breaker. That Unicode-code-point tie-break is an explicit
profile divergence from POSIX's byte comparison in the POSIX locale because
generic fsspec strings do not preserve original pathname bytes.

## 5. Standard output

Every displayed name is written verbatim followed by one newline. The command
MUST NOT quote, escape, color, decorate, or select columns implicitly. TTY and
redirected invocations MUST produce byte-equivalent content for the same
locale and backend results.

For one operand:

- a file writes the exact original mapped operand;
- a directory writes its sorted immediate child basenames without a header;
  and
- an empty directory writes nothing.

```text
$ ls memory:/docs
guide.md
notes.txt
```

For several operands, successful non-directories form the first output block,
one per line. Each successful directory forms a later block headed by its
exact mapped operand and `:`. Blocks are joined by exactly one empty line.
There is no leading or trailing empty line. An empty directory block still
writes its header.

```text
$ ls local:/a.txt memory:/docs
local:/a.txt

memory:/docs:
guide.md
notes.txt
```

A files-only invocation has no empty lines between entries. A directories-only
invocation begins with the first directory header, not a blank line.

## 6. Runtime failures and diagnostics

Operands MUST be processed in their original argument order. Each operand's
backend result MUST be fully validated and buffered before any stdout for that
operand is written. A backend-call or result-validation failure therefore
writes no partial result for that operand. For those per-operand failures, the
command MUST continue processing other preflight-valid operands, retain
complete successful output, emit one stderr diagnostic per failed operand in
original argument order, and finally exit `1`. Output-write failures instead
follow the stop and partial-byte rules below.

Diagnostics use this shape:

```text
ls: <mapped operand>: <stable category>
```

The recognized exception-class mapping is:

| Exception or condition | Category |
| --- | --- |
| `FileNotFoundError` | `not found` |
| `PermissionError` | `permission denied` |
| `NotADirectoryError` | `not a directory` |
| `NotImplementedError` | `unsupported operation` |
| Invalid consumed backend shape | `incompatible result` |
| Any other backend exception | `backend failure (<class>): <message>` |

Exception rows are tested top to bottom with `isinstance`. Categories MUST be
selected by exception class or validated result shape, never by parsing errno
values or message text. For the fallback category, `<class>` is exactly
`type(exception).__name__` and `<message>` is exactly `str(exception)` before
diagnostic rendering. An empty message retains the final colon and space.

Every diagnostic is terminated by one newline. For diagnostics only, each
inserted option token, operand, configured name, exception class, and exception
message is rendered by replacing, in order, `\\` with `\\\\`, NUL with `\\0`,
carriage return with `\\r`, and newline with `\\n`; every other character is
unchanged. Literal command text and stable categories are not transformed.
This is the only diagnostic escaping algorithm. No traceback is written.

A stdout write failure is a runtime failure. `BrokenPipeError` stops output
immediately, writes no diagnostic or traceback, and exits `1`; bytes already
accepted by the output stream cannot be retracted. Every other stdout write
exception emits exactly
`ls: output: output failure (<class>): <message>`, using the same class,
message, and rendering rules, then exits `1`.

## 7. Exit status

| Status | Meaning |
| ---: | --- |
| `0` | Every operand completed successfully. |
| `1` | A source-lifecycle, backend, incompatible-result, or output-write failure occurred. |
| `2` | Usage, option, mapped-operand, or mapped-name preflight failed. |

## 8. Historical acceptance evidence from issue #80

The disposed prototype exercised the same command handler without backend-type
branches against Local, Memory, and hermetic `VOSpaceFileSystem` instances.

| Area | Required evidence |
| --- | --- |
| Single operands | Root, non-empty directory, empty directory, and file output. |
| Hidden entries | Default omission, explicit dot operand, `-A` inclusion, no synthetic dot entries, and `-a` rejection. |
| Multiple operands | Cross-filesystem files plus directories, duplicate preservation, files-first grouping, headers, empty sections, and blank-line grammar. |
| Backend calls | Every valid operand uses `info`; only directories use names-only `ls`; invalid preflight makes zero calls. |
| Ordering | C-locale golden output and a controlled non-default locale case when available. |
| Runtime errors | Every stable category, continuation, per-operand atomicity, and final exit `1`. |
| Bad input/results | Zero operands, bad grammar/name/option, malformed `info`, malformed names-only `ls`, and newline/NUL rejection. |
| TTY | TTY and redirected output are identical. |
| Live gate | One narrow, read-only OpenCADC directory listing with no backend-specific handler branch. |

## 9. Downstream ownership

- [Define the tested command matrix contract](https://github.com/shinybrar/vosfs/issues/81)
  owns matrix statuses, versions, and hermetic-versus-live evidence rules.
- [Strict `ls -l` rejection profile](fsspec-cli-ls-long-rejection-profile.md)
  records why V1 adds no long renderer and keeps whole-invocation preflight
  rejection.
- [Sequence the `fsspec-cli` tracer implementation backlog](https://github.com/shinybrar/vosfs/issues/83)
  owns production package slices, dependencies, CI, and release ordering.

Issues #79 and #80 added no production CLI implementation. The prototype was
disposed after recording its evidence; later production tickets own executable
TDD cycles.

## Primary evidence

- [POSIX Issue 8 `ls`](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/ls.html)
- [Portable fsspec capability floor for plain `ls`](../research/fsspec-cli-plain-ls-capability-floor.md)
- [Honest long-listing viability evidence](../research/fsspec-cli-ls-long-viability.md)
- [Strict `ls -l` rejection profile](fsspec-cli-ls-long-rejection-profile.md)
- [fsspec 2026.6.0 `AbstractFileSystem.ls`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L326-L365)
- [fsspec 2026.6.0 `AbstractFileSystem.info`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L682-L714)
