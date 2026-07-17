# `fsspec-cli` verified cross-source two-operand `cp` command profile

Status: **Locked command semantics and async execution contract**

Question: [Add verified cross-source two-operand copy](https://github.com/shinybrar/vosfs/issues/138)
Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

## Scope

```text
cp [--] source_a:/file source_b:/target
```

Distinct configured source names select this profile. Backend class, object
identity, and protocol do not select behavior. Recursive copy, move,
multi-source copy, retries, metadata preservation, and implicit local operands
remain outside scope.

## Execution

Command validates both mapped operands before source entry. It then acquires
source and destination once, in operand order, before backend I/O or output.

Source `_info` must report `type == "file"` and non-negative integer `size`.
Destination resolution, existing-parent requirement, replacement rules, and
diagnostics match [verified same-source `cp`](fsspec-cli-same-source-cp-command-profile.md).

Command creates one secure local temporary, downloads source through
`_get_file`, closes it, uploads through destination `_put_file(...,
mode="overwrite")`, then requires destination `_info` file type and original
source size. It re-downloads destination into same temporary and compares
streamed SHA-256 content proofs. Memory remains bounded by transfer chunks plus
one staged object.

Successful status `0` proves source retention, destination type, byte count,
and content. Failed upload or later verification reports destination residue
may remain. Command never deletes destination to simulate rollback and never
claims atomicity.

## Evidence

Local-to-Memory and Memory-to-Local need hermetic positive gates. Native
`vosfs` directions remain `unverified` until independently qualified.
