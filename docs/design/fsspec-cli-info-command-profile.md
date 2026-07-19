# `fsspec-cli` normalized `info` command profile

<!-- pyml disable line-length -->

Status: **Locked backend-neutral command profile (production command shipped by #200)**

Question: [Implement `info` and reconcile it with `stat`](https://github.com/shinybrar/vosfs/issues/200)

Parent: [Shell-compatible experience epic](https://github.com/shinybrar/vosfs/issues/204)

Normative direction: [shell-experience specification](fsspec-cli-shell-experience-spec.md), especially sections 2, 4, 7, and 8

Related, behaviorally unchanged command: [reduced BSD/macOS-shaped `stat`](fsspec-cli-bsd-macos-stat-command-profile.md)

## 1. Boundary with `stat`

`info` is the backend-neutral, raw **normalized-dictionary** view of one fsspec
metadata mapping. It displays every `ListingRow` field and preserves
backend-specific values under `extra`. Sparse metadata is successful and is
shown honestly as `None`; no backend branch fills a gap.

Existing `stat` remains the reduced BSD/macOS-shaped, Local-rich view of one or
more operands. It requires its complete locked metadata shape, resolves local
owner/group presentation, and emits its fixed host-shaped line. This profile
does not alter `stat` argv, validation, rendering, continuation, or evidence.

Neither command is POSIX, GNU, full BSD/macOS, nor an all-fsspec compatibility
claim. `info` is not a backend-specific extension: backend keys are data, not a
dispatch mechanism.

## 2. Invocation and source-free preflight

Accepted argv is exactly:

```text
info [--] name:/path
```

- Exactly one mapped operand is REQUIRED.
- No command options are accepted. `--` ends option parsing.
- Framework-owned `--help` before `--` remains Typer's help short circuit.
- Preflight completes before any source factory, entry, or filesystem work.

Diagnostics and status are:

| Condition | Diagnostic | Status |
| --- | --- | --- |
| Missing operand | `info: missing mapped filesystem operand` | `2` |
| Unsupported option | `info: <token>: unsupported option` | `2` |
| Extra operand | `info: extra operand` | `2` |
| Malformed mapped operand | `info: <operand>: invalid mapped filesystem operand` | `2` |
| Unknown mapped name | `info: <operand>: unknown filesystem (known: <names>)` | `2` |

Inserted values use the shared diagnostic escaping algorithm. Pure preflight
failure has empty stdout and touches no source.

## 3. Operation and normalization

After successful preflight, the command:

1. acquires the one referenced source exactly once under ADR 0002/0003;
2. awaits exactly one `filesystem._info(path)` call;
3. requires the result to be a `Mapping` whose keys are exact `str` objects;
4. passes that same mapping once to the existing pure `to_listing` adapter; and
5. renders the resulting `ListingRow` without another metadata adapter.

It MUST NOT call `_ls`, retry `_info`, use a public synchronous facade, consult
another metadata primitive, or branch on backend type. A non-mapping,
non-string key, adapter rejection, or value that cannot be rendered into the
locked payload is an `incompatible result`.

Normalization semantics, including basename selection, kind mapping, optional
field validation, timestamp coercion, and the exact division between normalized
keys and `extra`, remain owned by the shell specification's single
`to_listing` layer. The command MUST NOT fabricate a missing value.

## 4. Exact output

The command creates this plain Python dictionary from the normalized row:

```python
{
    "name": row.name,
    "kind": row.kind,
    "size": row.size,
    "mtime": row.mtime,
    "mode": row.mode,
    "nlink": row.nlink,
    "owner": row.owner,
    "group": row.group,
    "link_target": row.link_target,
    "extra": dict(row.extra),
}
```

Before rendering, the command recursively copies `Mapping` values to plain
Python dictionaries while retaining every key/value, and recursively retains
exact built-in list and tuple shapes. Set and frozenset elements are recursively
rendered, sorted by those rendered strings, and presented with their native
`{...}` / `set()` / `frozenset({...})` spelling. Recursive container graphs are
incompatible results. This presentation-only canonicalization prevents a
read-only mapping's opaque `repr` and hash-randomized set iteration from
bypassing nested sorting; it does not reinterpret metadata.

The command then renders with
`pprint.pformat(value, width=80, sort_dicts=True)`, appends exactly one newline,
and encodes it as UTF-8. Dictionary keys and set values are therefore ordered by
Python's stdlib pretty-printer. Other values retain their stdlib Python
presentation; for example bytes stay bytes, datetimes stay datetimes, and
tuples stay tuples. Nothing is coerced through a JSON type system. The same
supported Python value graph and runtime therefore produce the same bytes.
Backend objects with unstable custom `repr` remain truthfully represented
backend data, not a cross-process byte-stability claim.

All nine normalized scalar fields are present even when their value is `None`.
`extra` is always present and contains every backend-specific string key/value
preserved by `to_listing`. No field is relabeled, dropped because it is sparse,
or replaced by a placeholder.

The complete payload is assembled before output. A successful command performs
one binary stdout `write(payload)` followed by one `flush()`. A short write is
an output failure; the command never retries or writes a partial remainder.

## 5. Failure, cleanup, and status

| Outcome | Behavior |
| --- | --- |
| Backend `FileNotFoundError` / ordinary error | Shared stable operand diagnostic; status `1` |
| Incompatible result | `info: <operand>: incompatible result`; status `1` |
| Short write / ordinary output error | Shared output diagnostic; status `1` |
| `BrokenPipeError` | No output-failure diagnostic; status `1` |
| Source cleanup error | Shared ADR 0003 diagnostic; status `1` |
| Escaping `BaseException` | Same-task cleanup, then original object propagates unchanged |
| Success | Status `0` |

The `_info` mapping and rendered payload are complete before stdout begins, so
there is no field-by-field output. Source cleanup always follows ADR 0003;
truthy exits cannot suppress command outcomes.

## 6. Evidence and claim boundary

Hermetic public-`App` evidence covers:

- adapted async Memory with sparse metadata and a `datetime` extra;
- adapted async Local with rich mode/identity metadata and Local-only extras;
- native async `vosfs` over a strict mocked HTTP transport, including `uri`,
  `md5`, `content_type`, and VOSpace `properties` extras; and
- source-free argv rejection, exact hook count, incompatible results, atomic
  output, broken pipe, cleanup, and control-flow precedence.

These tests establish only the named source forms and the locked command seam.
They are not live OpenCADC evidence, a completeness claim for arbitrary fsspec
backends, or evidence that every backend value has a stable custom `repr`.

## 7. Help and README language

Help:

```text
Usage: info [--] name:/path
Display normalized file information
```

README wording keeps the boundary visible: `info` is one normalized raw mapping
with backend extras; `stat` remains the stricter reduced BSD/macOS-shaped view.
