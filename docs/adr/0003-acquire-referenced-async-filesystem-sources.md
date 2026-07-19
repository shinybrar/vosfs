# Acquire every referenced async filesystem source before filesystem work

Status: Accepted

Question: [Define async filesystem source failure behavior](https://github.com/shinybrar/vosfs/issues/94)

[ADR 0002](./0002-own-async-filesystems-per-invocation.md) makes each
yielded async filesystem invocation-owned. This decision fixes the remaining
cross-command behavior when one invocation needs several async filesystem
sources and factory, context entry, yielded-instance validation, or context
exit fails.

## Decision

The sole stable v1 seam remains `App(sources).typer_app`. Source lifecycle is
command orchestration behind that seam; it adds no public runner, lifecycle
policy, reporter, capability registry, or async invocation method.

After complete command preflight, the command derives distinct referenced
source names in first-operand-appearance order. It acquires those sources
sequentially in that order, exactly once per name, before any filesystem call
or stdout output. Repeated operands reuse the filesystem yielded for that
name. The same callable configured under two names is called once for each
referenced name. Unreferenced sources are untouched.

Each acquisition performs these stages in order:

1. Call the synchronous source factory once.
2. Require its result to implement the async context-manager protocol.
3. Await context entry.
4. Record the successful entry for later exit.
5. Validate the yielded value exactly as ADR 0002 requires: it is an
   `AsyncFileSystem`, `async_impl is True`, and `asynchronous is True`.

The first ordinary acquisition failure stops later acquisitions and all
command work. There is no retry, fallback, capability probe, concurrent
acquisition, or partial continuation through other sources. Already-entered
sources are still exited. A failed factory or context entry has no matching
exit; a successful entry is exited even when its yielded value fails
validation.

## Diagnostics

Lifecycle diagnostics have these exact shapes:

```text
ls: <name>: source factory failure (<class>): <message>
ls: <name>: source factory returned incompatible async context manager
ls: <name>: source entry failure (<class>): <message>
ls: <name>: source yielded incompatible async filesystem
ls: <name>: source exit failure (<class>): <message>
```

`<name>` is the configured source name, not a mapped filesystem operand.
`<class>` is `type(exception).__name__`; `<message>` is `str(exception)`,
including an empty message after the final colon and space. Inserted values
use the plain-`ls` diagnostic escaping rules. Lifecycle failures never
masquerade as operand `not found`, `permission denied`, or backend failures.

An acquisition failure emits its primary diagnostic first. Cleanup diagnostics
follow in reverse successful-entry order. No stdout is written because command
work never started.

## Cleanup and existing command outcomes

Every successfully entered context is offered exactly one exit, sequentially
in reverse-entry order, on the invocation loop. An exit that raises does not
prevent later exits. A truthy exit result cannot suppress an acquisition,
command, output, cancellation, or other control-flow outcome: async filesystem
sources declare cleanup, not command-control policy.

When command work has started:

- Existing complete successful stdout and ordered command diagnostics remain
  valid when command work or a later exit fails.
- Exit diagnostics follow command diagnostics in reverse-entry order.
- An exit failure after otherwise successful work retains stdout and changes
  the result to status `1`.
- Multiple exit failures are all diagnosed and still produce one status `1`.
- A stdout failure stops further output under the plain-`ls` rules, then source
  cleanup still runs. `BrokenPipeError` remains silent for the output failure
  itself, but an independent exit failure remains reportable.
- Bytes already accepted by stdout or stderr cannot be retracted. No ordering
  between the two streams is promised.

There is no last-exception-wins rule. Cleanup failures cannot replace earlier
command output, diagnostics, or failure state.

## Cancellation and control flow

`CancelledError`, `KeyboardInterrupt`, `SystemExit`, and other escaping
`BaseException` control flow are not rendered as source or backend failures
and are not converted to status `1`. The command stops new acquisition,
filesystem work, and normal output; begins reverse cleanup for every entered
source on the same task and loop; then propagates the original control flow
unchanged. An ordinary exit failure is diagnosed but cannot replace it, and a
truthy exit cannot suppress it.

Pinned fsspec 2026.6.0 requires one narrow exception before that same-task
cleanup. `tree` can await an `AsyncFileSystemWrapper._walk` hook that resolves
to a lazy synchronous iterator. The command owns one worker task and thread
only while materializing that iterator. The synchronous worker catches every
iterator `BaseException` and returns it as typed outcome data, so iterator
control flow never crosses the child-task boundary as task cancellation. The
invocation task shields and, when interrupted, drains that worker before
source cleanup. It then raises the exact iterator control-flow object, or the
unchanged outer control flow when the invocation itself was interrupted. A
source is therefore never exited while its invocation-owned iterator is still
running.

Apart from that tree-only adapter, V1 adds no numeric `130` contract, general
shield task, cleanup timeout, background loop, or general runner thread.
Source cleanup itself is not shielded. An exit that returns or raises permits
later exits; an exit that never returns can prevent cleanup completion and
propagation.

Outcome precedence is:

| Outcome | Result |
| --- | --- |
| Preflight failure | Status `2`; no source lifecycle begins. |
| Escaping `BaseException` control flow | Cleanup begins, then control flow propagates without a command status. |
| Any ordinary source, command, backend, result, or output failure | Status `1`; all applicable diagnostics are retained. |
| No failure | Status `0`. |

## Test surface

Callers and tests use only `App(sources).typer_app`. Recording fake async
filesystem sources exercise factory, entry, validation, exit, suppression,
ordering, and cancellation through that interface. Any private lifecycle
helper remains an implementation detail and does not become a second seam.

## Considered alternatives

- Continuing through healthy sources after acquisition failure would make one
  command partially initialized, duplicate shared failures across operands,
  and enlarge ordering and output states.
- Lazy or concurrent source acquisition would add scheduling and cancellation
  states before tracer evidence shows startup latency matters.
- Allowing context exits to suppress or replace failures would transfer command
  policy to host adapters and make outcomes backend-dependent.
- A general shield, timeout, or second runner would add lifecycle policy not
  supplied by `App(sources).typer_app`. The tree-only worker above instead
  terminates one pinned lazy iterator before the existing same-task cleanup.

## Consequences

- Startup is deterministic and produces no filesystem work or stdout until
  every referenced source is usable.
- Later-needed sources remain open for the invocation even if earlier command
  work fails.
- Sequential startup trades possible latency for a small, testable state
  machine and stable diagnostics.
- Source authors remain responsible for cleanup that terminates; v1 supplies
  no timeout policy.
