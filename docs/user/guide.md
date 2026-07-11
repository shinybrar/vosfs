# User Guide

`vosfs` is an asynchronous [`fsspec`](https://filesystem-spec.readthedocs.io/)
filesystem for the **OpenCADC Cavern VOSpace** service. It exposes the `vos`
protocol so fsspec-aware Python tools can read, write, inspect, and mutate
OpenCADC VOSpace paths.

!!! warning "OpenCADC profile only"

    `vosfs` targets the OpenCADC VOSpace profile. It does **not** claim generic
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
no credential at all, access is anonymous. `endpoint_url` must use `https`
whenever a credential is configured.

!!! note "Serialization"

    A literal `token` is included in fsspec pickle and JSON serialization.
    Prefer `tokenfile` or an environment source when the filesystem may be
    serialized.

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

Every public behavior carries exactly one classification.

!!! abstract "Normative source"

    The tables below are a reader-friendly digest. The full, authoritative
    capability matrix lives in the
    [capability contract](https://github.com/shinybrar/vosfs/blob/main/docs/design/trd.md);
    where the two differ, the contract wins.

### Supported

Native operations and the client-derived behaviors composed from them.

<div class="grid cards" markdown>

-   :material-magnify:{ .lg .middle } __Inspect__

    ---

    `info` · `ls` · `exists` · `isfile` · `isdir` · `size` · `modified`

-   :material-file-tree:{ .lg .middle } __Traverse__

    ---

    Client-derived from `ls`:

    `walk` · `find` · `glob` · `du` · `tree`

-   :material-download:{ .lg .middle } __Read__

    ---

    `cat` · `cat_file` · `cat_ranges` · `head` · `tail` · `read_block` · `get` · `get_file`

    Whole-object transfer; ranges are sliced client-side.

-   :material-upload:{ .lg .middle } __Write__

    ---

    `pipe` · `pipe_file` · `put` · `put_file`

    One whole `PUT` per file.

-   :material-file-document-edit-outline:{ .lg .middle } __Open__

    ---

    `open("rb" / "r" / "wb" / "w" / "xb" / "x")`

    Backed by a disk-staged temporary file.

-   :material-folder-plus-outline:{ .lg .middle } __Directories__

    ---

    `mkdir` · `makedirs`

-   :material-delete-outline:{ .lg .middle } __Delete__

    ---

    `rm` · `rmdir` · `rm_file`

    Empty-check or leaves-first client deletion.

-   :material-content-copy:{ .lg .middle } __Copy__

    ---

    `cp` · `copy` · `cp_file`

-   :material-swap-horizontal:{ .lg .middle } __Move__

    ---

    `mv` · `move` · `rename`

    Non-atomic, no overwrite.

-   :material-fingerprint:{ .lg .middle } __Identity & metadata__

    ---

    `touch(truncate=True)` · `checksum` · `ukey`

-   :material-package-variant:{ .lg .middle } __Serialization__

    ---

    `pickle` · `to_json`

-   :material-cached:{ .lg .middle } __Cache wrappers__

    ---

    `simplecache::vos://` · `filecache::vos://`

</div>

### Unsupported

!!! failure "These raise `NotImplementedError` before any remote mutation, or are absent entirely"

    The filesystem never silently degrades — an unsupported call fails fast.

| Operation / feature | Why |
| --- | --- |
| Remote byte ranges, `Range` requests | Cavern serves whole objects only |
| `blockcache::` / `cached::` wrappers | Require server-side byte ranges |
| Append, `+` mode, offset / atomic / resumable / multipart writes | One whole `PUT` per file |
| `touch(truncate=False)` | Would require a partial update |
| `rm(..., maxdepth=...)` | Bounded-depth deletion is not modeled |
| `created` timestamps, `open_async` | Not provided by the profile |
| Server-side copy, move, search, sort, pagination | Absent from the OpenCADC profile |
| Public property, permission, and link-creation APIs | Out of v0.3.0 scope |
| FUSE mounting | Not supported |

## Errors

Failures map to the closest standard Python exception. Anything without a
precise match is raised as the single public `vosfs.VOSpaceError`.

| Exception | Raised when |
| --- | --- |
| `ValueError` | Invalid input — a malformed path, bad option, or oversized/ill-formed XML |
| `PermissionError` | Authentication or authorization was refused |
| `FileNotFoundError` | A referenced node or its parent does not exist |
| `FileExistsError` | The target already exists and the operation will not overwrite |
| `NotImplementedError` | The operation is unsupported, or the service advertises no matching binding |
| `OSError` (`errno.ENOSPC`) | A storage quota is exhausted |
| `BlockingIOError` | The node is locked |
| `TimeoutError` | An HTTP request exceeded its timeout |
| `ConnectionError` | The underlying connection failed |
| `vosfs.VOSpaceError` | Every remaining OpenCADC, integrity, HTTP, and partial-completion failure |

!!! info "`vosfs.VOSpaceError`"

    A subclass of `OSError`. It carries the HTTP status, a symbolic fault code,
    retry guidance, and — for partial failures — the completed and failed paths.

!!! note "Redaction and retries"

    Credentials and pre-authorized URL tokens are redacted from every exception
    and log. `vosfs` never retries automatically; callers own higher-level retry
    policy.

## Closing

`aclose()` (async) and `close()` (sync) release every HTTP client, evict the
instance from fsspec's cache, and make later I/O fail as closed.
