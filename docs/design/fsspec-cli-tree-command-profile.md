# `fsspec-cli` recursive-tree command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Question: [Add the `tree` command](https://github.com/shinybrar/vosfs/issues/199)

Client baseline: **fsspec 2026.6.0**

This profile admits a backend-neutral Unicode tree rendered from fsspec's
async `_walk` hook. It does not call fsspec's synchronous, preformatted
`tree()` facade and does not claim compatibility with every option of the
standalone `tree(1)` utility.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope and command form

The supported form is:

```text
tree [--maxdepth N] [--] name:/path
```

Exactly one mapped filesystem root is REQUIRED. `--maxdepth N` uses only the
separate-token spelling, MAY appear before or after the operand while option
parsing is active, and MAY repeat with the final value winning. `--` ends
option parsing. `N` contains one or more ASCII decimal digits, is non-negative,
and MAY contain leading zeros. A value beyond Python's configured integer
conversion ceiling is an invalid depth rather than an internal error.

The operand is depth zero and its direct children are depth one. Omission is
unbounded. `--maxdepth=`, `-L`, sizes, summaries, display limits, ASCII mode,
directory-only filtering, multiple roots, standard input, and every other
option are unsupported. Exact `--help` remains framework-owned.

The shared mapped-operand validation order applies. The command adds these
stable preflight diagnostics:

| Condition | Diagnostic |
| --- | --- |
| Zero operands | `tree: missing mapped filesystem operand` |
| More than one operand | `tree: extra operand` |
| Missing depth value | `tree: --maxdepth: option requires an argument` |
| Invalid depth | `tree: <value>: invalid --maxdepth value` |

Every preflight failure has status `2`, empty stdout, one diagnostic, and no
source acquisition.

## 2. Backend operation and return-shape normalization

After preflight, acquire the root's mapped source once and invoke exactly one
top-level hook:

```python
filesystem._walk(path, maxdepth=N, detail=False, on_error="raise")
```

Omitted depth passes `None`; positive depths pass unchanged. Because fsspec
2026.6.0 rejects zero, depth zero invokes `_walk(..., maxdepth=1)` and the
renderer retains only the root.

Two pinned hook shapes are supported. Native `AsyncFileSystem._walk` returns an
async iterator, which the command consumes asynchronously. An
`AsyncFileSystemWrapper` instance exposes an awaitable `_walk`; the command
awaits it once, requires the resolved value to be a synchronous iterator, and
materializes that lazy iterator in one invocation-owned worker thread and task
off the invocation event loop. The synchronous materializer catches every
`BaseException` and returns a typed value-or-error outcome as data; iterator
control flow never directly crosses the child-task boundary. Any other shape
is incompatible. Ordinary invocation, await, or iteration exceptions are
backend failures; escaping `BaseException` control flow is preserved as the
exact original object.

The worker task is shielded only while that iterator is active. If the
invocation task is cancelled or otherwise interrupted, it drains and retrieves
the worker outcome before re-raising the unchanged outer control flow and
beginning same-task source cleanup. A source is never exited while its iterator
is still running. This narrow adapter adds no public runner, background loop,
timeout, retry, or source-cleanup shield.

The command MUST NOT call public `tree`, `_find`, `_ls`, `_info`, metadata
hooks, or another synchronous facade. It MUST NOT retry, fall back, branch on
backend type, or consult a capability registry. Recursive internal hook calls
made by the backend remain backend-owned.

## 3. Accepted walk rows

The complete walk is collected before validation or output. Each yielded value
MUST be an exact three-tuple `(root, dirs, files)`. `root` MUST be an exact
string. `dirs` and `files` MUST be exact lists of backend basename strings.
Strings contain neither NUL nor newline; child basenames are non-empty and
contain no slash. Names within each list are unique and a name cannot occur in
both lists.

The first row identifies the requested root after trailing-slash
normalization. Every later row MUST be reachable as a directory named by its
parent row; duplicate roots and orphan or impossible relationships are
incompatible. A file-root row is represented only by the inherited fsspec
sentinel `(root, [], [""])`; no other empty child name is accepted. Empty
directory and file roots both render only the requested root line.

Malformed rows, incompatible hook shapes, and impossible relationships produce
status `1`, empty stdout, and:

```text
tree: <operand>: incompatible result
```

The command does not coerce, default, deduplicate, partially emit, or fabricate
a row or child.

## 4. Standard output

The first line is the exact mapped operand's path component. Children render
as basename-only Unicode tree lines using `├──` and `└──` connectors followed
by one space, with `│` plus three spaces and four-space continuation
indentation. Each directory's children are
grouped as directories before files; each group is locale-sorted with raw
spelling as the deterministic tie-break. No size, type, summary, trailing
decoration, mapped source name, or path rewriting is added.

```text
/docs
├── sub
│   └── b.txt
└── a.txt
```

The requested maximum depth is enforced while rendering even if a backend
over-yields. The complete output is buffered and uses the shared
single-operand text-output path exactly once.

## 5. Failure and lifecycle behavior

Backend exceptions use the shared per-operand diagnostics. Ordinary backend,
incompatible-result, output, source-entry, and source-exit failures have status
`1`. `BrokenPipeError` remains silent for the output failure itself; other
output failures use the shared `tree: output: output failure ...` diagnostic.

Source ownership, same-loop acquisition and cleanup, exception information,
control-flow propagation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 6. Recursive-cost caveat

Unbounded `tree` consumes the complete subtree. A remote backend may perform
one listing request for every reached directory, so the single top-level CLI
hook invocation is not a one-request promise. `--maxdepth` bounds displayed
depth and the requested backend traversal depth.

## 7. Evidence

Hermetic golden and call-shape tests exercise the public
`App(sources).typer_app` seam across adapted async Local, adapted async Memory,
and native async `vosfs` with a strict mocked transport and no network.
Focused tests lock both return shapes, worker-thread materialization, the exact
top-level call, depth zero/one/unbounded behavior, ordering and connectors,
file and empty roots, parser/help behavior, atomic hostile-result rejection,
iteration failures, output failures, control flow, and cleanup.

This ticket records its matrix rows as `unverified` until immutable qualifying
evidence exists. It makes no live OpenCADC claim.
