# `fsspec-cli` leading-byte command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add the `head` command](https://github.com/shinybrar/vosfs/issues/198)

Client baseline: **fsspec 2026.6.0**

This profile admits the shell-experience specification's backend-neutral
leading-byte read over fsspec's native async `_cat_file` hook. It does not
claim POSIX or GNU `head(1)` compatibility.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command form

The supported form is:

```text
head -c N [--] name:/path
```

Exactly one byte-count selector and one mapped filesystem operand are REQUIRED.
Only the separate-token `-c N` selector is supported. The selector MAY appear
before or after the operand while option parsing is active. `--` ends option
parsing.

`N` MUST contain one or more ASCII decimal digits. It is non-negative and MAY
contain leading zeros. Signs, suffixes, whitespace, fractions, empty values,
and non-ASCII digits are invalid. Inline or grouped selectors, repeated
selectors, `-n`, default counts, stdin, multiple operands, and every other
option are unsupported.

Exact `--help` remains the framework-owned help spelling. The mapped-operand
grammar and validation order are those of the shared command toolkit. The
command adds these stable preflight diagnostics:

| Condition | Diagnostic |
| --- | --- |
| Missing or repeated selector | `head: exactly one byte-count selector is required` |
| Selector without a following token | `head: -c: option requires an argument` |
| Invalid count | `head: <value>: invalid byte count` |
| Zero operands after a valid selector | `head: missing mapped filesystem operand` |
| More than one operand | `head: extra operand` |

Every preflight failure completes with status `2`, empty stdout, exactly one
stable diagnostic, and no source factory or filesystem call.

## 2. Backend operation contract

After complete preflight, acquire the operand's mapped source once and await
exactly one call:

```python
await filesystem._cat_file(path, start=0, end=N)
```

This is a bounded CLI hook request, including when `N` is zero. The CLI MUST
NOT request `_cat_file(path, start=None, end=None)` or otherwise make an
unbounded or whole-object hook call. It MUST NOT call `_info`, `_get_file`,
`open`, another filesystem hook, a synchronous facade, or a retry. It MUST NOT
stage the result, branch on backend type, or consult a capability registry.

## 3. Accepted result and output

The result MUST have exact type `bytes` and its length MUST NOT exceed `N`.
Byte arrays, memory views, text, other values, and overlong byte results are
incompatible. The complete result is validated before stdout is touched.

Compatible bytes are written unchanged to binary stdout. The command adds no
newline, encoding, decoding, or text conversion. An empty result writes
nothing. A short write or flush failure is an output failure.

An incompatible result produces status `1`, empty stdout, and:

```text
head: <operand>: incompatible result
```

## 4. Failure and lifecycle behavior

Backend exceptions use the shared stable per-operand diagnostics. Ordinary
backend, incompatible-result, output, source-entry, and source-exit failures
produce status `1`. A sole `BrokenPipeError` is silent and produces status
`141`, matching mapped-file `cat`; a source-exit failure still takes precedence
and produces status `1`. Other output failures use the shared
`head: output: output failure ...` diagnostic.

Source ownership, same-loop acquisition and cleanup, exception information,
control-flow propagation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 5. Physical-transfer caveat

The bounded `_cat_file` call shape constrains what the CLI asks of a backend;
it does not guarantee a ranged physical transfer. A backend may implement the
hook by downloading a whole object and slicing locally. In particular, the
[`vosfs` read contract](trd.md#8-read-contract) states that OpenCADC Cavern
lacks HTTP Range support: `VOSpaceFileSystem._cat_file` performs one negotiated
whole-object GET, sends no `Range` header, and applies the requested slice
locally. This profile does not claim remote Range or network-efficient random
access.

## 6. Evidence

Hermetic binary-output and call-shape tests MUST exercise the public
`App(sources).typer_app` seam across:

- adapted async Local;
- adapted async Memory; and
- native async `vosfs` with a mocked transport and no network access.

Focused tests additionally lock strict count parsing, option ordering, exact
`--help`, the `--` terminator, source-free preflight, the single bounded
`_cat_file` call, exact bytes, strict result validation, backend diagnostics,
short writes, flush failures, broken pipes, and invocation-owned cleanup.
Native `vosfs` evidence MUST observe its truthful whole-object GET without a
`Range` header while separately asserting the bounded CLI hook request.

This ticket records its matrix rows as `unverified` until immutable qualifying
evidence exists. It makes no live OpenCADC claim.
