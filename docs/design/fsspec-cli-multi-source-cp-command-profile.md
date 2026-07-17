# `fsspec-cli` verified multi-source file `cp` command profile

Status: **Locked command semantics and async execution contract**

Question: [Add multi-source file copy into an existing directory](https://github.com/shinybrar/vosfs/issues/139)
Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

## Scope

```text
cp [--] source:/file [source:/file ...] destination:/directory
```

This profile requires at least two source operands and one final destination.
The destination exists and source-reports `type == "directory"`; it is never
created. Each source source-reports `type == "file"`. Directory and other
source types are not recursively copied. Move, recursion, globbing, retries,
concurrent transfer, metadata preservation, and rollback remain outside scope.

Two operands retain their existing same-source or cross-source profiles.

## Execution

The command validates option syntax, every operand grammar, and every mapped
name before source entry. It acquires every distinct configured name exactly
once in first argv appearance order, before filesystem work. It then checks the
final destination directory and processes sources sequentially in argv order.

For each source, the target is the destination path joined with its basename.
The existing same-configured-name transfer path applies when source and
destination names match; otherwise the existing cross-source staging, upload,
and byte-verification path applies. Configured names select this behavior, not
backend class or object identity.

Existing target files are replaced. Duplicate basenames are deterministically
replaced by later argv operands after each prior target is fully verified.
Ordinary later failure stops processing with status `1`; earlier verified
targets and failed-target residue remain. Sources are never deleted.

## Diagnostics and evidence

Diagnostics and exit statuses use the referenced two-operand transfer profile.
Missing or non-directory final destinations fail before any copy. Unsupported
options and malformed or unknown operands fail during preflight without source
entry.

Hermetic public-`App` tests cover same-source, cross-source, mixed-source,
repeated-name, duplicate-basename, existing-target, empty, binary, and large
file paths; destination and source-type negatives; transfer, verification,
cleanup, cancellation, and lifecycle failures; and partial-state retention.
Adapted Local and Memory evidence is source-form-scoped. Native `vosfs` stays
unverified until its multi-source profile has qualifying evidence. The
isolated-wheel command-matrix gate is required before claiming a passing
release row.
