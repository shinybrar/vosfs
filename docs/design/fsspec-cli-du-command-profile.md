# `fsspec-cli` disk-usage command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add the `du` command](https://github.com/shinybrar/vosfs/issues/195)

Client baseline: **fsspec 2026.6.0**

This profile admits the shell-experience specification's backend-neutral disk
usage command over fsspec's native async `_du` hook. It does not claim POSIX,
BSD, or GNU `du` compatibility.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command forms

The supported form is:

```text
du [-sh] [--] name:/path
```

Exactly one mapped filesystem operand is REQUIRED. `-s` requests one total and
`-h` changes byte counts to the normalization layer's locked human-readable
format. Short-option tokens MAY group `s` and `h` in either order, repeat them,
and appear before or after the operand while option parsing is active. `--`
ends option parsing.

Exact `--help` remains the framework-owned help spelling. `-h` is only the
human-readable size option and is never a help alias. A long option, an empty
short-option token, or a token containing any character other than `s` and `h`
is unsupported; the complete token is diagnosed.

The mapped-operand grammar and validation order are those of the shared command
toolkit. The command adds these arity diagnostics:

| Condition | Diagnostic |
| --- | --- |
| Zero operands | `du: missing mapped filesystem operand` |
| More than one operand | `du: extra operand` |

Every preflight failure completes with status `2`, empty stdout, exactly one
stable diagnostic, and no source factory or filesystem call.

## 2. Backend operation contract

After complete preflight, acquire the operand's mapped source once. Await
exactly one call:

```python
await filesystem._du(path, total=False)  # base form
await filesystem._du(path, total=True)   # -s form
```

The CLI MUST NOT call `_find`, `_info`, `_ls`, or another filesystem hook. It
MUST NOT call a synchronous filesystem facade, branch on backend type, retry,
consult a capability registry, or reconstruct disk usage itself. Internal work
performed by the backend's `_du` implementation is backend-owned and does not
change the one-call CLI contract.

In fsspec 2026.6.0, the inherited `_du` implementation recursively finds files
and reads each file's info before returning. A backend MAY override that
implementation, but the CLI does not detect or select an implementation.

## 3. Accepted results

Without `-s`, the result MUST be a mapping. Every key MUST be exactly a `str`
with no NUL or newline, and every value MUST have `type(value) is int` with
`value >= 0`. Booleans, floats, missing sizes, negative sizes, and malformed
paths are incompatible. An empty mapping is compatible and produces no output.

With `-s`, the result MUST have `type(result) is int` with `result >= 0`.
Mappings, booleans, floats, and negative integers are incompatible.

The complete result is validated and formatted before stdout is touched. One
invalid entry rejects the whole result with status `1`, empty stdout, and:

```text
du: <operand>: incompatible result
```

The renderer MUST NOT coerce, default, drop, or partially emit invalid values.

## 4. Standard output

Each output record has one byte-count field, one horizontal tab, one path, and
one newline:

```text
<size>\t<path>\n
```

Without `-h`, `<size>` is the exact base-10 byte count. With `-h`, every size is
passed to the shared 1024-base `format_size` helper. `-h` changes no other
field.

The base form sorts mapping entries by returned path using locale order with the
raw string as a deterministic tie-break, then renders each backend-returned path
exactly. It does not add the mapped source name or rewrite a path. The `-s` form
has no returned path, so it renders the operand path portion exactly as spelled
after the first colon. These choices match the existing command family's
path-only stdout while retaining the backend's authoritative detailed names.

```text
2\t/docs/a.txt
1536\t/docs/sub/report.bin
```

## 5. Failure and lifecycle behavior

Backend exceptions use the shared stable per-operand diagnostics. Ordinary
backend, incompatible-result, output, source-entry, and source-exit failures
produce status `1`. A `BrokenPipeError` is silent for the output failure itself.
Other output failures use the shared `du: output: output failure ...`
diagnostic. All complete output is buffered before the command's single stdout
write.

Source ownership, same-loop acquisition and cleanup, exception information,
control-flow propagation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 6. Recursive-cost caveat

`du` is recursive. The inherited fsspec 2026.6.0 implementation can perform a
recursive traversal plus one metadata read per discovered file. Remote sources
may therefore make many service requests and may be slow or costly. `-s`
changes only the returned shape and rendered output; it does not promise a
cheaper traversal.

## 7. Evidence

Hermetic golden and call-shape tests MUST exercise the public
`App(sources).typer_app` seam across:

- adapted async Local;
- adapted async Memory; and
- native async `vosfs` with a mocked transport and no network access.

Focused tests additionally lock option grouping and interspersion, `-h` not
being help, exact `--help`, the `--` terminator, one `_du` call with the correct
`total` argument, detailed and summarized rendering, atomic malformed-result
rejection, backend diagnostics, output failure, and invocation-owned cleanup.

This ticket records its new matrix rows as `unverified` until immutable
qualifying evidence exists. It makes no live OpenCADC claim.
