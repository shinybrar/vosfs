# `fsspec-cli` cross-source `mv` strict-rejection profile

Status: **Locked source-free unsupported behavior**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) /
[#142](https://github.com/shinybrar/vosfs/issues/142)

## Scope

```text
mv [--] source:/file destination:/target
```

After option and operand grammar plus mapped-operand validation identify
distinct configured source names, `mv` rejects before either source factory is
called. It writes no stdout, writes exactly:

```text
mv: cross-source move unsupported
```

to stderr, and exits `2`. No filesystem calls or mutation occur. Configured
source names define the boundary; shared backend classes, protocols, or
instances do not make a cross-source move supported.

## Safety boundary

Cross-source `mv` must not fall back to copy then delete. Byte equality alone
cannot prove selected metadata preservation, immutable source generation
identity, conditional deletion of copied generation, destination-change
handling, source-deletion uncertainty, cancellation safety, or atomic rename.
Directory and multi-operand moves remain outside this profile.

## Evidence

The production `App(sources).typer_app` seam has a hermetic negative test with
separate recording factories. It observes independent source and destination
state, asserting both remain unchanged and neither factory is entered. The
matrix records one `command preflight` / `not entered` unsupported row. The
isolated-wheel gate runs this negative profile with `test_mv.py`.
