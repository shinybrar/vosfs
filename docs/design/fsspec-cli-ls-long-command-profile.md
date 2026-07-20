# `fsspec-cli` long-listing command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add `ls -l` / `-lh` and `ll` long listing](https://github.com/shinybrar/vosfs/issues/194)

Client baseline: **fsspec 2026.6.0**

This profile admits the shell-experience specification's best-effort long
listing. It supersedes the historical
[`ls -l` strict rejection profile](fsspec-cli-ls-long-rejection-profile.md)
without changing the locked behavior of bare
[`ls`](fsspec-cli-plain-ls-command-profile.md).

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command forms

The supported forms are:

```text
ls [-Alh] [--] name:/path...
ll [-Alh] [--] name:/path...
```

`ls` enters long mode when any valid option token contains `l`. `ll` is the
same command logic with long mode inherent; `l` is therefore accepted
idempotently by `ll`. `h` changes only the size rendering in long mode and is
never a help alias. Exact `--help` remains the framework-owned help spelling.

Short-option tokens MAY group `A`, `l`, and `h` in any order, repeat them, and
appear before or between operands while option parsing is active. For `ls`, a
valid `l` MAY occur in a different token from `h`; `ls -h -l` and `ls -lh` are
equivalent. `ls -h` without a valid `l` is unsupported. `--` ends option
parsing. Long option spellings, lowercase `a`, and a token containing any
other option character are unsupported; the complete token is diagnosed.

The mapped-operand grammar, validation order, source pre-acquisition,
invocation ownership, diagnostics, output-failure behavior, cleanup, and exit
statuses are exactly those of the plain-`ls` profile and ADRs
[0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[0003](../adr/0003-acquire-referenced-async-filesystem-sources.md). Every
preflight error completes before source entry or output.

## 2. Backend operation contract

Operands are processed in original argument order. Each operand first consumes
exactly one awaited `_info(path)` result. It MUST be a mapping with a string
`type`:

- `type == "file"`: normalize and render that existing info mapping; `_ls`
  MUST NOT be called;
- `type == "directory"`: await exactly one `_ls(path, detail=True)` call; and
- every other or missing type is an incompatible result.

A detailed directory result MUST be a concrete list of mappings. Every mapping
MUST report a string `name` that is an immediate lexical child of the requested
directory under the plain-`ls` child-validation rules. The complete list is
validated before hidden-entry selection, sorting, normalization, or output.

Every selected file info mapping and detailed child mapping is passed to the
single `to_listing(info)` normalization layer. Every completed row set is
passed to `render_listing`; the command MUST NOT copy field mapping or rendering
logic. It MUST NOT use a synchronous filesystem facade, another underscore
hook, backend branch, retry, capability registry, or fabricated value.

## 3. Selection, sorting, and adaptive rendering

The plain-`ls` `-A`, explicit dot-operand, locale sorting, duplicate, and raw
string tie-break rules apply unchanged. Without `-A`, dot-prefixed directory
children are omitted. With `-A`, returned children are included except exact
`.` and `..`; neither is synthesized.

Rows use the normalization layer's adaptive union of supported columns. A
column backed by at least one row is rendered for that row set, with `-` for an
unsupported value in another row. A column unsupported by every row is
omitted. Type, exact or human-readable size, modification time, mode, link
count, owner, group, and link target therefore remain backend-neutral data
claims rather than a fixed POSIX schema. The renderer never invents metadata.

Without `h`, a known size is an exact byte count. With `h`, it uses the locked
1024-base `format_size` representation. Unknown size stays `-`. `h` affects no
other column.

## 4. Standard output and multi-operand grouping

For one operand, a file renders its normalized `_info` row and a directory
renders its selected detailed children with no header. An empty directory
writes nothing.

For multiple operands, successful file operands form the first adaptive row
set, sorted by exact mapped spelling. Each successful directory then forms its
own adaptive row set, sorted by exact mapped spelling and headed by that exact
spelling plus `:`. Blocks are separated by exactly one empty line. An empty
directory retains its header. This preserves plain-`ls` files-first grouping
while ensuring each long-listed directory chooses columns only from its own
returned rows.

```text
file  12  report.txt

memory:/docs:
file  1K  guide.md
dir    -  sub
```

All successful output is formatted and buffered before the command's first
stdout write. A malformed detailed list or normalization failure produces no
partial block for that operand. Per-operand backend and incompatible-result
failures continue in original argument order, retain other complete results,
use the plain-`ls` stable diagnostics, and make the final status `1`.

## 5. Alias and bare-`ls` invariants

`ll` is registered through `App(sources).typer_app` and delegates to the same
runner, preflight, source lifecycle, filesystem operations, normalization, and
rendering code as `ls -l`. Diagnostics use the invoked command name (`ll`). It
is not a second implementation or a backend alias.

The bare `ls` and `ls -A` paths remain byte-for-byte governed by the plain-`ls`
profile. They continue to call `_ls(path, detail=False)`, consume concrete
lists of strings, and emit names-only output. Long-listing admission MUST NOT
decorate, normalize, or otherwise alter that path.

## 6. Evidence

Hermetic golden and call-shape tests MUST exercise the public embedded-command
seam across:

- adapted async Local, proving rich mode/link-count/owner/group/size/mtime rows;
- adapted async Memory, proving sparse type/size rows and omission of unsupported
  columns; and
- native async `vosfs` with a mocked transport, proving remote size/mtime/link
  rows without network access.

Focused tests additionally lock option grouping, `h` not being help, the `ll`
alias, file operands using only `_info`, one detailed `_ls` per directory,
hidden selection, malformed detail rejection, failure continuation, output
atomicity, multi-operand grouping, and unchanged bare-`ls` behavior. Evidence
remains limited to the named source forms.
