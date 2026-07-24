# `fsspec-cli` trailing-byte command profile

<!-- pyml disable line-length -->

> Current interface ownership: [ADR 0005](../adr/0005-define-typer-owned-commands-and-callback-extensions.md).

Status: **Locked command semantics and async execution contract**

Question: [Add the `tail` command](https://github.com/shinybrar/vosfs/issues/198)

Client baseline: **fsspec 2026.6.0**

This profile admits the shell-experience specification's backend-neutral
trailing-byte read over fsspec's native async `_info` and `_cat_file` hooks. It
does not claim POSIX or GNU `tail(1)` compatibility.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command form

The supported form is:

```text
tail -c N [--] name:/path
```

Exactly one byte-count selector and one mapped filesystem operand are REQUIRED.
`-c N` supplies a non-negative integer count. The annotated callback and Typer
own option syntax, argument arity, integer conversion, `--`, help, and framework
usage errors. Exact framework diagnostic wording is not compatibility surface.
Callback-owned mapped-operand validation then runs before event-loop entry or
source acquisition. Every preflight failure has status `2`, empty stdout, and
no source or filesystem work.

## 2. Backend operation contract

After complete preflight, acquire the operand's mapped source once. Await
exactly these two calls in order:

```python
info = await filesystem._info(path)
await filesystem._cat_file(path, start=info["size"] - N, end=None)
```

The metadata result MUST be a mapping whose `size` value has exact type `int`
and is non-negative. Booleans, floats, strings, missing sizes, negative sizes,
and non-mappings are incompatible and prevent the read call.

The suffix start is passed through even when negative; negative starts are
fsspec's native from-end spelling. `N == 0` passes `start=size`. The CLI never
passes both `start=None` and `end=None`, and therefore never requests an
unbounded or whole-object hook call.

The CLI MUST NOT make another metadata or read call. It MUST NOT use
`_get_file`, `open`, another filesystem hook, a synchronous facade, or a retry.
It MUST NOT stage the result, branch on backend type, consult a capability
registry, or clamp a negative start.

## 3. Accepted result and output

The byte result MUST have exact type `bytes` and its length MUST NOT exceed
`N`. Byte arrays, memory views, text, other values, and overlong byte results
are incompatible. Both backend results are validated before stdout is touched.

Compatible bytes are written unchanged to binary stdout. The command adds no
newline, encoding, decoding, or text conversion. An empty result writes
nothing. A short write or flush failure is an output failure.

An incompatible metadata or byte result produces status `1`, empty stdout,
and:

```text
tail: <operand>: incompatible result
```

## 4. Failure and lifecycle behavior

Backend exceptions use the shared stable per-operand diagnostics. Ordinary
backend, incompatible-result, output, source-entry, and source-exit failures
produce status `1`. A sole `BrokenPipeError` is silent and produces status
`141`, matching mapped-file `cat`; a source-exit failure still takes precedence
and produces status `1`. Other output failures use the shared
`tail: output: output failure ...` diagnostic. No bytes are emitted after a
metadata, read, or result-validation failure.

Source ownership, same-loop acquisition and cleanup, exception information,
control-flow propagation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 5. Physical-transfer caveat

The bounded suffix `_cat_file` call shape constrains what the CLI asks of a
backend; it does not guarantee a ranged physical transfer. A backend may
implement the hook by downloading a whole object and slicing locally. In
particular, the [`vosfs` read contract](trd.md#8-read-contract) states that
OpenCADC Cavern lacks HTTP Range support: `VOSpaceFileSystem._cat_file`
performs one negotiated whole-object GET, sends no `Range` header, and applies
the requested suffix slice locally. This profile does not claim remote Range
or network-efficient random access.

## 6. Evidence

Hermetic binary-output and call-shape tests MUST exercise the public
`App(sources).typer_app` seam across:

- adapted async Local;
- adapted async Memory; and
- native async `vosfs` with a mocked transport and no network access.

Focused tests additionally lock Typer count conversion, option placement,
help, the `--` terminator, source-free preflight, exact metadata and suffix
call shapes, negative suffix starts, exact bytes, strict result validation,
backend diagnostics, short writes, flush failures, broken pipes, and
invocation-owned cleanup. Native `vosfs` evidence MUST observe its truthful
whole-object GET without a `Range` header while separately asserting that the
CLI's `_cat_file` request is bounded.

This ticket records its matrix rows as `unverified` until immutable qualifying
evidence exists. Evidence remains limited to the named source forms.
