# `fsspec-cli` exact-size command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add the `size` command](https://github.com/shinybrar/vosfs/issues/197)

Client baseline: **fsspec 2026.6.0**

This profile admits the shell-experience specification's exact-byte query over
fsspec's native async `_size` and `_sizes` hooks. It does not claim GNU
`stat(1)` or `wc(1)` compatibility.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command form

The supported form is:

```text
size [--] name:/path...
```

At least one mapped filesystem operand is REQUIRED. No option is supported.
`--` ends option parsing; every preceding token beginning with `-`, other than
exact framework-owned `--help`, is an unsupported option. After `--`, every
token is an operand. The mapped-operand grammar and validation order are those
of the shared command toolkit.

Zero operands produce `size: missing mapped filesystem operand`. Every
preflight failure completes with status `2`, empty stdout, exactly one stable
diagnostic, and no source factory or filesystem call.

## 2. Backend operation contract

For exactly one operand, acquire its mapped source once and await exactly one:

```python
await filesystem._size(path)
```

For two or more operands, group operands by mapped source name in the order
each source first appears. Acquire every distinct referenced source once in
that order, then process groups sequentially in the same order. Await exactly
one call per reached group:

```python
await filesystem._sizes([path, ...])
```

The list preserves that source's operand order and duplicates. The CLI MUST NOT
call `_size` per item on this multi-operand path. Calls made by fsspec's
inherited `_sizes` implementation are backend-owned and do not change that CLI
contract.

Processing is fail-fast. A backend or incompatible-result failure in one group
prevents later groups from running. No stdout is written unless every reached
result needed for the complete invocation is valid.

The CLI MUST NOT call `_info`, another filesystem hook, a synchronous facade,
or a retry. It MUST NOT branch on backend type, consult a capability registry,
or fabricate a size.

## 3. Accepted results

`_size` MUST return an object whose exact type is `int` and whose value is
non-negative. A multi-operand `_sizes` result MUST have exact type `list`, have
the same length as the group's submitted path list, and contain only exact
non-negative integers. Booleans, floats, strings, `None`, negative values,
other sequence types, and short or long result lists are incompatible.

The complete result is validated before relevant output. A malformed scalar,
list shape, or list length is diagnosed against the affected operand (the
group's first operand when no element can be identified). A malformed element
is diagnosed against its corresponding operand:

```text
size: <operand>: incompatible result
```

The invocation completes with status `1` and empty stdout. No value is coerced,
defaulted, dropped, or partially emitted.

## 4. Standard output

One record is emitted per original operand, including duplicates, in original
invocation order:

```text
<bytes>\t<mapped-operand>\n
```

`<bytes>` is the exact base-10 count. `<mapped-operand>` is the complete
validated `name:/path` spelling supplied by the user. Retaining the source name
makes equal paths from different mapped filesystems unambiguous.

```text
5\tmemory:/docs/a.txt
7\tlocal:/tmp/b.bin
5\tmemory:/docs/a.txt
```

There is no human-readable mode and no `wc` or `stat` alias.

## 5. Failure and lifecycle behavior

A batch backend exception is diagnosed against the first operand in that
source group. Backend exceptions otherwise use the shared stable per-operand
diagnostics. Ordinary backend, incompatible-result, output, source-entry, and
source-exit failures produce status `1`. A `BrokenPipeError` is silent for the
output failure itself. Other output failures use the shared
`size: output: output failure ...` diagnostic. The complete output is buffered
before the command's single stdout write.

Source ownership, same-loop acquisition and cleanup, reverse-order release,
exception information, control-flow propagation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 6. Evidence

Hermetic golden and call-shape tests MUST exercise the public
`App(sources).typer_app` seam across:

- adapted async Local;
- adapted async Memory; and
- native async `vosfs` with a mocked transport and no network access.

Focused tests additionally lock exact `--help`, the `--` terminator, source-free
preflight, the single `_size` call, per-source `_sizes` grouping, duplicates,
cross-source output association, strict result validation, atomic failures,
backend diagnostics, output failure, and invocation-owned cleanup.

This ticket records its matrix rows as `unverified` until immutable qualifying
evidence exists. It makes no live OpenCADC claim.
