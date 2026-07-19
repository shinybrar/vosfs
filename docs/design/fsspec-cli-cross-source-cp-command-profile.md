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
Expected size and recognized source tokens are frozen into an immutable proof
immediately after that validation, before destination resolution or mutation.

Command creates one secure local source staging temporary, downloads source
through `_get_file`, closes it, uploads through destination `_put_file(...,
mode="overwrite")`, then uses the shared metadata verifier. The staged source
size MUST match the pre-transfer source `_info`; the destination MUST be a file
of that exact size. Exact `str` or `bytes` metadata tokens under normalized
`ETag` / `etag`, `md5`, `content-md5` / `content_md5`, and `checksum` names MUST
match for every shared field. With no shared recognized token, exact type and
size are the truthful proof; no cryptographic strength is claimed.

The source temporary is the transfer bridge, not a verification download.
There is no destination download, FIFO, pipe, worker thread, synchronous open,
or second temporary. Source-temporary cleanup runs after success, ordinary
failure, and escaping control flow. An ordinary cleanup failure is reported
only when no transfer or verification failure already exists and never masks
escaping control flow. If both configured names yield the same filesystem
object and resolved path, command rejects `same path` before staging or upload.
Staging errors disclose only error class, never local temporary paths or source
content. Any future byte comparison requires a separately profiled explicit
opt-in and blocking comparison through `asyncio.to_thread`.

Successful status `0` proves source retention, destination type and byte count,
and agreement of every shared recognized metadata token. Failed upload or later
verification reports destination residue may remain. Command never deletes
destination to simulate rollback and never claims atomicity.

## Evidence

Hermetic Local-to-Memory and Memory-to-Local positive gates pass through the
public `App` seam. The installed-wheel gate runs those matrix tests from a
built `fsspec-cli` wheel. Native `vosfs` directions remain `unverified` until
independently qualified.
