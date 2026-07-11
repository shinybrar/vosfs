# User Guide

`vosfs` is an asynchronous [`fsspec`](https://filesystem-spec.readthedocs.io/)
filesystem for the **OpenCADC Cavern VOSpace** service. It exposes the `vos`
protocol so fsspec-aware Python tools can read, write, inspect, and mutate
OpenCADC VOSpace paths.

`vosfs` targets the OpenCADC VOSpace profile only. It does **not** claim generic
IVOA VOSpace 2.1 conformance or compatibility with unrelated VOSpace
implementations.

## Constructing a filesystem

The service is selected only by `endpoint_url`, an absolute OpenCADC service
base URL. No registry or shortname lookup is performed.

```python
import fsspec

fs = fsspec.filesystem("vos", endpoint_url="https://staging.canfar.net/arc")
fs.ls("/")
```

The apparent authority after the `vos://` protocol marker is part of the
filesystem path, not a service selector: `vos://a/b`, `vos:///a/b`, `/a/b`, and
`a/b` all refer to the same path `/a/b`.

## Credentials

At most one credential source may be configured, and each is reread before
every authenticated request. `vosfs` consumes access tokens but never acquires
or refreshes them.

| Option | Meaning |
| --- | --- |
| `token` | A literal bearer token (including an OIDC access token). |
| `tokenfile` | A file whose bearer token is reread before every request. |
| `certfile` | A combined X.509 certificate-chain and private-key PEM file. |

If no credential option is given, `vosfs` falls back to exactly one of the
environment variables `VOSFS_TOKEN`, `VOSFS_TOKEN_FILE`, or `VOSFS_CERT_FILE`.
Providing any explicit credential option ignores all of these variables. With
no credential at all, access is anonymous.

`endpoint_url` must use `https` whenever a credential is configured.

> **Serialization note.** A literal `token` is included in fsspec pickle and
> JSON serialization. Prefer `tokenfile` or an environment source when the
> filesystem may be serialized.

```python
fs = fsspec.filesystem(
    "vos",
    endpoint_url="https://staging.canfar.net/arc",
    tokenfile="/run/secrets/vos-token",
)
```

## Reading and writing

OpenCADC Cavern does not implement HTTP byte ranges, so `vosfs` transfers whole
objects. Reads stream a whole object (to a local file or a disk-backed
temporary file for `open`), and writes issue one whole `PUT`.

```python
with fs.open("/project/data.csv", "rb") as handle:
    header = handle.readline()

fs.pipe_file("/project/notes.txt", b"hello")
fs.get("/project/data.csv", "local.csv")
fs.put("local.csv", "/project/copy.csv")
```

`open("rb")` downloads once into a disk-backed temporary file and then provides
local `read`/`readinto`/`readline`/iteration/`tell`/`seek`. `open("wb")` buffers
into a temporary file and uploads once when the file closes successfully.

## Capability summary

Every public behavior has one classification. The full, normative matrix lives
in the [capability contract](https://github.com/shinybrar/vosfs/blob/main/docs/design/trd.md).

**Supported** (native or client-derived): `info`/`ls`/`exists`/`isfile`/
`isdir`/`size`/`modified`; `walk`/`find`/`glob`/`du`/`tree`; `cat`/`cat_file`/
`cat_ranges`/`head`/`tail`/`read_block`/`get`/`get_file`; `pipe`/`pipe_file`/
`put`/`put_file`; `open("rb"/"r"/"wb"/"w"/"xb"/"x")`; `mkdir`/`makedirs`;
`rm`/`rmdir`/`rm_file` (empty-check or leaves-first client deletion);
`cp`/`copy`/`cp_file`; `mv`/`move`/`rename` (non-atomic, no overwrite);
`touch(truncate=True)`; `checksum`/`ukey`; pickle/`to_json`; and the
`simplecache::vos://` and `filecache::vos://` wrappers.

**Unsupported** (raise `NotImplementedError`, or are absent): `created`;
`open_async`; remote byte ranges / `Range` requests; append, `+`, offset,
atomic, resumable, and multipart writes; `touch(truncate=False)`;
`rm(..., maxdepth=...)`; server-side copy, move, search, sort, and pagination;
public property, permission, and link-creation APIs; the `blockcache::` and
`cached::` wrappers; and FUSE.

## Errors

Failures map to standard Python exceptions: `ValueError` for invalid input,
`PermissionError` for authentication or authorization, `FileNotFoundError` for a
missing node or parent, `FileExistsError` for a conflict, `NotImplementedError`
for an unsupported operation or a missing service binding, `OSError` with
`errno.ENOSPC` for quota exhaustion, `BlockingIOError` for a locked node,
`TimeoutError` for an HTTP timeout, and `ConnectionError` for a connection
failure. Every remaining OpenCADC, integrity, HTTP, and partial-completion
failure is raised as the single public `vosfs.VOSpaceError` (a subclass of
`OSError`), which carries the HTTP status, symbolic fault, retry guidance, and
completed and failed paths where applicable. Credentials and pre-authorized URL
tokens are redacted from every exception and log.

`vosfs` never retries automatically; callers own higher-level retry policy.

## Closing

`aclose()` (async) and `close()` (sync) release every HTTP client, evict the
instance from fsspec's cache, and make later I/O fail as closed.
