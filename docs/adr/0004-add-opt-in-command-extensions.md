# Add opt-in command extensions through `App`

Status: Accepted

Question: [Add the backend-specific extension seam](https://github.com/shinybrar/vosfs/issues/191)

## Decision

The sole stable v1 host seam is extended from `App(sources).typer_app` to:

```python
App(sources, *, extensions=[...]).typer_app
```

Omitting `extensions` preserves the core command surface. `App` snapshots the
source mapping, registers core commands first, then calls each selected
`CommandExtension.register(typer_app, sources)` with the same Typer app and an
immutable view of that snapshot.

An extension registers commands only. It adds no public runner, lifecycle
policy, backend registry, capability metadata, or async invocation seam.
Mapped-source extension commands use the existing internal command toolkit and
invocation-owned source lifecycle. Each command detects its capability by
calling it; backend type and protocol do not select commands or behavior.

This decision amends only the exact `App(sources).typer_app` constructor wording
in [ADR 0002](0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](0003-acquire-referenced-async-filesystem-sources.md). Their source
ownership, validation, acquisition, cleanup, diagnostics, control-flow, and
single-host-seam decisions remain accepted.

The first proof is the opt-in `sign` extension. It calls `filesystem.sign(path)`
and converts `NotImplementedError` into one unsupported-operation diagnostic
and status `1` without a traceback.

## Considered alternatives

- A string registry or entry-point discovery would add naming, loading, and
  conflict policy before another consumer exists.
- Backend inspection or protocol dispatch would make core behavior depend on
  backend identity instead of the called capability.
- A public async runner or lifecycle object would create a second host seam and
  contradict the invocation-owned lifecycle.

## Consequences

- Hosts opt into backend-specific commands explicitly at app construction.
- Third-party extension authors receive only Typer registration and immutable
  source configuration; source instances remain invocation-owned.
- Each extension command owns a compatibility profile and tested-source matrix.
- Command-name conflict policy remains Typer's existing behavior until a real
  conflict requires a narrower rule.
