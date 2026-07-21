# `fsspec-cli` file-predicate command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add the `test` command](https://github.com/shinybrar/vosfs/issues/197)

Client baseline: **fsspec 2026.6.0**

This profile admits a deliberately small shell-shaped predicate command over
fsspec's native async `_exists`, `_isdir`, and `_isfile` hooks. It is not a
general shell expression language.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command form

The supported form is:

```text
test -e|-d|-f [--] name:/path
```

Exactly one selector and one mapped filesystem operand are REQUIRED. The exact
selector tokens mean:

| Selector | Predicate |
| --- | --- |
| `-e` | path exists |
| `-d` | path is a directory |
| `-f` | path is a file |

The selector MAY appear before or after the operand while option parsing is
active. `--` ends option parsing. Repeating the same selector or supplying two
different selectors is invalid. Grouped tokens such as `-ed` and every other
token beginning with `-` are unsupported options. There is no negation,
compound predicate, expression language, or multiple-operand form.

Exact `--help` remains framework owned. The mapped-operand grammar and
validation order are those of the shared command toolkit. The command adds
these stable diagnostics:

| Condition | Diagnostic |
| --- | --- |
| Zero selectors, or a repeated/second selector | `test: exactly one predicate selector is required` |
| Selector but no operand | `test: missing mapped filesystem operand` |
| More than one operand | `test: extra operand` |

Every preflight failure completes with status `2`, empty stdout, exactly one
stable diagnostic, and no source factory or filesystem call.

## 2. Backend operation contract

After complete preflight, acquire the operand's mapped source once and await
exactly one matching call:

```python
await filesystem._exists(path)  # -e
await filesystem._isdir(path)   # -d
await filesystem._isfile(path)  # -f
```

The CLI MUST NOT call `_info`, another predicate hook, another filesystem hook,
or a synchronous facade. It MUST NOT retry, branch on backend type, consult a
capability registry, or infer the result itself. Internal `_info` work performed
by fsspec's inherited predicate implementations is backend-owned and does not
change the one-call CLI contract.

## 3. Accepted result and exit status

The awaited hook MUST return exact `bool`. Truthy or falsey integers, strings,
containers, `None`, and boolean-like objects are incompatible.

For a compatible result:

| Result | Exit status | Stdout | Stderr |
| --- | --- | --- | --- |
| `True` | `0` | empty | empty |
| `False` | `1` | empty | empty |

A false predicate is an ordinary answer, not a diagnostic failure. An
incompatible result produces status `1`, empty stdout, and:

```text
test: <operand>: incompatible result
```

The CLI never renders predicate output.

## 4. Failure and lifecycle behavior

Backend exceptions use the shared stable per-operand diagnostics and produce
status `1`. Source-entry and source-exit failures also produce status `1` under
the shared diagnostics. A false result remains silent unless cleanup itself
fails. There is no output-failure surface because the command never writes
stdout.

Source ownership, same-loop acquisition and cleanup, exception information,
control-flow propagation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 5. Evidence

Hermetic call-shape tests MUST exercise the public `App(sources).typer_app` seam
across:

- adapted async Local;
- adapted async Memory; and
- native async `vosfs` with a mocked transport and no network access.

Focused tests additionally lock exact `--help`, interspersed selectors, the
`--` terminator, selector repetition and grouping, source-free preflight,
exactly one matching hook, strict boolean validation, silent true and false
results, backend diagnostics, and invocation-owned cleanup.

This ticket records its matrix rows as `unverified` until immutable qualifying
evidence exists. Evidence remains limited to the named source forms.
