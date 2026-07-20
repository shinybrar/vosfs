# `fsspec-cli` recursive-find command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add the `find` command](https://github.com/shinybrar/vosfs/issues/196)

Client baseline: **fsspec 2026.6.0**

This profile admits the shell-experience specification's backend-neutral
recursive search over fsspec's native async `_find` hook. It does not claim
POSIX or GNU `find` compatibility and deliberately excludes predicates,
actions, globbing, and `-exec`.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command forms

The supported form is:

```text
find [--maxdepth N] [--type f|d] [--] name:/path
```

Exactly one mapped filesystem operand is REQUIRED. The default and `--type f`
forms render fsspec's recursive file-name result. `--type d` renders directory
paths. `--maxdepth N` bounds results to a non-negative depth where the operand
is depth zero, direct children are depth one, and so on.

Long-option values use only the separate-token spellings shown above.
`--maxdepth=N` and `--type=f` are unsupported rather than silently accepted as
alternate grammar. Options MAY appear before or after the operand while option
parsing is active. Repeating an option is allowed and the final value wins.
`--` ends option parsing.

`N` MUST contain one or more ASCII decimal digits. Leading zeros are accepted.
A sign, fraction, whitespace, empty value, or non-ASCII digit is invalid. The
only accepted type values are the lowercase single characters `f` and `d`.

Exact `--help` remains the framework-owned help spelling. Every other token
that starts with `-` while option parsing is active is unsupported; the
complete token is diagnosed.

The mapped-operand grammar and validation order are those of the shared command
toolkit. The command adds these stable preflight diagnostics:

| Condition | Diagnostic |
| --- | --- |
| Zero operands | `find: missing mapped filesystem operand` |
| More than one operand | `find: extra operand` |
| Missing option value | `find: <option>: option requires an argument` |
| Invalid depth | `find: <value>: invalid --maxdepth value` |
| Invalid type | `find: <value>: invalid --type value` |

Every preflight failure completes with status `2`, empty stdout, exactly one
stable diagnostic, and no source factory or filesystem call.

## 2. Backend operation contract

After complete preflight, acquire the operand's mapped source once. Await
exactly one of these calls:

```python
# default and --type f
await filesystem._find(path, maxdepth=N, withdirs=False, detail=False)

# --type d
await filesystem._find(path, maxdepth=N, withdirs=True, detail=True)
```

Omitted `--maxdepth` passes `None`. Positive depths pass through unchanged.
Fsspec 2026.6.0 rejects `maxdepth=0` before producing a result, so the valid
shell depth zero uses one `_find(..., maxdepth=1)` call and atomically retains
only a returned path equal to the operand root after trailing-slash
normalization. The retained backend spelling is rendered unchanged. This gives
file operands their file result, directory operands no default file result,
and `--type d` directory operands their root result without a second filesystem
call.

The CLI MUST NOT call `_walk`, `_info`, `_isdir`, `_isfile`, or another
filesystem hook. It MUST NOT call a synchronous filesystem facade, branch on
backend type, retry, consult a capability registry, or reconstruct a recursive
walk. Internal work performed by the backend's `_find` implementation is
backend-owned and does not change the one-call CLI contract. In particular,
fsspec 2026.6.0 implements `_find` using `_walk` and may use `_info`, `_isdir`,
or `_isfile` itself.

## 3. Accepted results

The default and `--type f` result MUST be an exact `list`. Every member MUST
have `type(path) is str` and contain neither NUL nor newline. Tuples,
generators, mappings, scalar strings, non-string members, and malformed paths
are incompatible. The name-list form is fsspec's file-like `_find` surface;
the CLI does not make additional metadata calls to reclassify its members.

The `--type d` result MUST be a mapping. Every key MUST have `type(path) is
str` and contain neither NUL nor newline. Every value MUST be a mapping whose
`type` member has `type(value) is str`. Entries whose type is exactly fsspec's
standard `directory` value are selected; other well-formed types are valid but
not rendered. A missing or non-string type is incompatible. The returned key
is authoritative for display; the CLI does not require or substitute a nested
`name` member.

An empty list or mapping is compatible and produces no output. The complete
result is validated, selected, sorted, and formatted before stdout is touched.
One invalid entry rejects the whole result with status `1`, empty stdout, and:

```text
find: <operand>: incompatible result
```

The renderer MUST NOT coerce, default, partially emit, or fabricate a path or
type.

## 4. Standard output

Each selected backend-returned path is one line:

```text
<path>\n
```

Paths are sorted using locale order with the raw string as a deterministic
tie-break. Each returned spelling is otherwise rendered exactly: the command
does not add the mapped source name, rewrite separators, deduplicate, quote, or
decorate paths.

Without `--type d`, an inherited fsspec `_find` lists recursive file-like
results and includes a file operand itself. With `--type d`, `withdirs=True`
means the inherited implementation includes a directory operand itself plus
descendant directories. Root inclusion by an overriding backend is accepted
only when that backend returns it; the CLI never invents it.

## 5. Failure and lifecycle behavior

Backend exceptions use the shared stable per-operand diagnostics. Ordinary
backend, incompatible-result, output, source-entry, and source-exit failures
produce status `1`. A `BrokenPipeError` is silent for the output failure itself.
Other output failures use the shared `find: output: output failure ...`
diagnostic. All complete output is buffered before the command's single stdout
write.

Source ownership, same-loop acquisition and cleanup, exception information,
control-flow propagation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 6. Recursive-cost caveat

The command is recursive unless `--maxdepth` bounds it. Fsspec's inherited
implementation walks every reached directory. `--type d` additionally requests
detail and root-directory inclusion, which can cause metadata requests inside
the backend implementation. The CLI promises one `_find` await, not one remote
service request. Remote sources may therefore be slow or costly.

## 7. Explicit exclusions

This profile does not accept `-exec`, expressions, predicates, names, globs,
regular expressions, logical operators, printing actions, deletion, multiple
roots, or backend-specific search. Unknown syntax fails closed during command
preflight.

## 8. Evidence

Hermetic golden and call-shape tests MUST exercise the public
`App(sources).typer_app` seam across:

- adapted async Local;
- adapted async Memory; and
- native async `vosfs` with a mocked transport and no network access.

Focused tests additionally lock option ordering and repetition, exact
`--help`, the `--` terminator, strict option values, one `_find` call with the
correct arguments, default/file/directory rendering, depth-zero adaptation,
root inclusion, locale sorting, atomic malformed-result rejection, backend
diagnostics, output failure, and invocation-owned cleanup.

This ticket records its new matrix rows as `unverified` until immutable
qualifying evidence exists. Evidence remains limited to the named source forms.
