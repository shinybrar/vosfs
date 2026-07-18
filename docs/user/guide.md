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

Install `vosfs` first:

```bash
uv add git+https://github.com/shinybrar/vosfs@main
```

```python
import fsspec

fs = fsspec.filesystem("vos", endpoint_url="https://staging.canfar.net/arc")
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

For a CADC proxy certificate, pass an absolute path to the combined
certificate-chain and private-key PEM file, or set the equivalent environment
variable before constructing the filesystem:

```bash
export VOSFS_CERT_FILE=/absolute/path/to/cadcproxy.pem
```

```python
fs = fsspec.filesystem(
    "vos",
    endpoint_url="https://staging.canfar.net/arc",
    certfile="/absolute/path/to/cadcproxy.pem",
)
```

Do not pass both forms: an explicit credential option ignores all credential
environment variables. Paths beginning with `~` are not expanded; use an
absolute path.

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
local `read`/`readinto`/`readline`/iteration/`tell`/`seek`. `open("wb")` and
`open("w")` disk-stage writes and upload once only after a successful close or
clean context exit. A write-block exception or local close failure before `PUT`
uploads nothing. Once `PUT` begins, failure is uncertain and may have truncated
the destination.

## Scientific Python stack

Because `vosfs` registers the `vos` protocol, any fsspec-aware tool can read and
write `vos://` URLs directly. Pass the endpoint (and at most one credential) as
`storage_options`, or construct a `VOSpaceFileSystem` and hand it to the tool.

!!! tip "`storage_options` mirrors the constructor"

    Every keyword accepted by `VOSpaceFileSystem` — `endpoint_url`, `token`,
    `tokenfile`, `certfile`, `timeouts` — is valid inside `storage_options`.
    Omit the credential to resolve it from `VOSFS_TOKEN`, `VOSFS_TOKEN_FILE`, or
    `VOSFS_CERT_FILE` in the environment.

=== "pandas"

    ```python
    import pandas as pd

    storage_options = {"endpoint_url": "https://staging.canfar.net/arc"}

    frame = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    frame.to_csv("vos://project/data.csv", index=False, storage_options=storage_options)

    restored = pd.read_csv("vos://project/data.csv", storage_options=storage_options)
    ```

=== "NumPy"

    Save and load through an fsspec file object: `.npy` and `.npz` round-trip,
    and delimited text is read with `np.loadtxt`. Any NumPy reader or writer
    that accepts a file object works the same way.

    ```python
    import fsspec
    import numpy as np

    storage_options = {"endpoint_url": "https://staging.canfar.net/arc"}
    array = np.arange(12, dtype="int64").reshape(3, 4)

    with fsspec.open("vos://project/a.npy", "wb", **storage_options) as handle:
        np.save(handle, array)

    with fsspec.open("vos://project/a.npy", "rb", **storage_options) as handle:
        restored = np.load(handle)
    ```

=== "Dask"

    ```python
    import dask.dataframe as dd

    storage_options = {"endpoint_url": "https://staging.canfar.net/arc"}

    # blocksize=None: Cavern has no byte ranges, so each file is one partition.
    lazy = dd.read_csv("vos://project/d.csv", storage_options=storage_options, blocksize=None)
    result = lazy.compute()
    ```

=== "Zarr"

    Zarr v3 reads and writes each chunk as a whole object. Use an asynchronous
    filesystem for the store (requires Python 3.11+).

    ```python
    import numpy as np
    import zarr

    from vosfs import VOSpaceFileSystem

    fs = VOSpaceFileSystem(
        endpoint_url="https://staging.canfar.net/arc",
        asynchronous=True,
    )
    store = zarr.storage.FsspecStore(fs, path="/project/array.zarr")

    root = zarr.open_group(store=store, mode="w")
    data = root.create_array("data", shape=(10,), dtype="int32")
    data[:] = np.arange(10, dtype="int32")

    reopened = zarr.open_group(store=store, mode="r")
    assert reopened["data"][3:6].tolist() == [3, 4, 5]
    ```

=== "PyArrow"

    Wrap the filesystem in a `PyFileSystem` and address paths without the
    `vos://` prefix.

    ```python
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow.fs import FSSpecHandler, PyFileSystem

    from vosfs import VOSpaceFileSystem

    fs = VOSpaceFileSystem(endpoint_url="https://staging.canfar.net/arc")
    pa_fs = PyFileSystem(FSSpecHandler(fs))

    table = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    pq.write_table(table, "/project/data.parquet", filesystem=pa_fs)
    restored = pq.read_table("/project/data.parquet", filesystem=pa_fs)
    ```

!!! note "Whole-object transfer"

    Cavern serves no byte ranges, so every consumer reads whole objects. Formats
    that rely on random access still work — `vosfs` stages each object (or Zarr
    chunk) to a local temporary file — but there is no server-side range
    optimization. For Dask, pass `blocksize=None` so each file is a single
    partition.

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

Close a filesystem deterministically when its work is finished:

```python
fs = fsspec.filesystem("vos", endpoint_url="https://staging.canfar.net/arc")
try:
    data = fs.cat_file("/project/data.csv")
finally:
    fs.close()
```
