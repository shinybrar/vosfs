# Technical Requirements Document — `vosfs`

**The VOSpace ↔ fsspec layer.** v0.2 · Target: `shinybrar/vosfs` → `opencadc/vosfs`

Requirement levels per RFC 2119: **MUST**, **MUST NOT**, **SHOULD**, **MAY**.

> **The contract is the IVOA VOSpace 2.1 specification.**
> `vosfs` implements the *specification*, not any particular server. opencadc `cavern` and `vault` are
> two observed implementations among many deployed worldwide. Every behaviour we measured on them is
> **evidence**, never a requirement. Implementation-specific behaviour MUST be **discovered at
> runtime**, and every optional capability MUST have a portable fallback.
>
> Normative requirements appear in §3–§14. Observations from real deployments are collected in
> **Appendix A, which is informative and non-normative.**

---

## 1. Purpose, scope, consumers

`vosfs` is a faithful, high-quality **fsspec `AsyncFileSystem` implementation of IVOA VOSpace**. It is
the base component on which `vostools` and the `canfar storage` CLI will be rebuilt, and the door
through which the PyData ecosystem reaches any VOSpace service.

| Consumer | What it demands |
|---|---|
| Any VOSpace deployment | conformance to the spec; graceful degradation on optional features |
| `vostools` / `canfar storage` | complete namespace + transfer ops; precise exceptions |
| pandas | registered protocol; `storage_options` == constructor kwargs; working `open('rb')` |
| dask | **the filesystem instance must be picklable** (§6) |
| zarr v3 | **`async_impl=True`** (hard `TypeError` otherwise); `_cat_file(start,end)`, `_cat_ranges` |
| pyarrow / Parquet | **seekable, ranged** `open('rb')`; a correct `size` from `info()` |
| `fsspec.fuse` | `info`/`ls`/`mkdir`/`rmdir`/`open(rb,wb)`/`touch`/`rm`; optional `chmod` |

### 1.1 Non-goals

`vosfs` **MUST NOT** contain: a CLI; IVOA-registry / `resource_id` / `vos-config` shortname
resolution; credential *acquisition* (`cadc-get-cert`, OIDC device flow); legacy `vos.Client`
compatibility shims; `cadcutils` or `requests` as dependencies. Those belong to layers above.

---

## 2. Design principles

1. **Spec-first.** Implement VOSpace 2.1. Never hardcode a vendor's behaviour.
2. **Capability negotiation over assumption.** Discover what a service supports (§4); degrade
   gracefully; never assume an extension exists.
3. **Auth-agnostic, server-agnostic.** The caller supplies an endpoint and a credential. `vosfs`
   discovers nothing about identity.
4. **Async core, sync facade.** Write `async def _method`; let fsspec generate the sync API. Not
   optional — zarr v3 rejects sync-only backends.
5. **Picklable by construction.** The instance must rebuild itself on a dask worker from
   `storage_options` alone.
6. **Fail loudly.** Several HTTP/VOSpace interactions have silent-corruption modes (§8.3). They MUST
   raise, never quietly succeed.

---

## 3. Normative VOSpace surface

### 3.1 Identifiers and node model

- **R3.1** `vosfs` **MUST** understand the VOSURI form `vos://<authority>/<path>` and **MUST** send
  authority-qualified URIs in every document that carries one (node creation, property update,
  transfer targets/directions). A bare `vos://<path>` is invalid.
- **R3.2** It **MUST** discover the service authority at runtime (e.g. from the `uri` of any node it
  reads) and **SHOULD** cache it per instance. It **MUST NOT** hardcode an authority.
- **R3.3** It **MUST** support the node types `ContainerNode`, `DataNode` (incl. `StructuredDataNode`
  / `UnstructuredDataNode`), and `LinkNode` (§11).
- **R3.4** Node properties are keyed by IVOA property URI (`ivo://ivoa.net/vospace/core#length`,
  `#date`, `#MD5`, `#contentType`, …). `vosfs` **MUST** preserve unknown properties verbatim and
  **MUST NOT** assume a fixed set. Some properties are server-computed and read-only.

### 3.2 Node operations

- **R3.5** `vosfs` **MUST** implement, per spec: `GET /nodes/<path>` (getNode / listNodes),
  `PUT /nodes/<path>` (createNode), `POST /nodes/<path>` (setNode), `DELETE /nodes/<path>`
  (deleteNode).
- **R3.6** Node documents **MUST** validate against the VOSpace XML schema. The node content model
  requires its full element sequence even when empty; a partial document is a client error.
- **R3.7** Container listing **MUST** support paging via `limit` and a start-boundary `uri`, and
  **MUST** de-duplicate the boundary node (the boundary is **inclusive**). `detail=min` **SHOULD** be
  used for traversal; `detail=max` is materially more expensive (per-child access evaluation).

### 3.3 Byte transfer — negotiation is the spec's path

- **R3.8** The **specified** mechanism for moving bytes is **transfer negotiation**: submit a
  transfer document with direction `pullFromVoSpace` (read) or `pushToVoSpace` (write) and one or
  more `<protocol>` elements; the service returns concrete `<endpoint>` URLs; the **client** then
  performs the byte transfer against those endpoints.
- **R3.9** `vosfs` **MUST** implement negotiated transfer as its **portable default**: synchronous
  negotiation where advertised, else the asynchronous `/transfers` (UWS) job.
- **R3.10** A negotiation response **MAY** return **multiple endpoints** (replicas). `vosfs`
  **SHOULD** try them in order on transient failure.
- **R3.11** A direct byte endpoint (`/files/<path>`) is an **optional extension**, not ratified IVOA
  (its standardID is a `-proto`). `vosfs` **MAY** use it as a fast path **only when advertised**
  (§4), and **MUST** fall back to negotiation otherwise.
- **R3.12** In VOSpace 2.1 the third-party directions (`pushFromVoSpace`, `pullToVoSpace`) are
  **client-orchestrated**. `vosfs` **MUST NOT** rely on a service moving bytes on its behalf between
  services; cross-service copy/move **MUST** be a client relay (§10.4).

### 3.4 Move and copy

- **R3.13** `moveNode` and `copyNode` are expressed as a `/transfers` job whose `<target>` is the
  source VOSURI, `<direction>` is the **destination VOSURI**, and `<keepBytes>` distinguishes copy
  (`true`) from move (`false`). `vosfs` **MUST** implement `_mv` this way where supported.
- **R3.14** `copyNode` is **specified**, but a service **MAY** not implement it (returning e.g.
  `405`). `vosfs` **MUST** detect this and **MUST** fall back to a client-side relay (§10.3). It
  **MUST NOT** assume server-side copy exists, and **MUST NOT** assume it is absent.

### 3.5 Deletion

- **R3.15** Per spec, deleting a `ContainerNode` deletes its contents. **A conforming client MUST
  NOT assume this holds**: a service **MAY** refuse to delete a non-empty container. `_rm(recursive=True)`
  **MUST** attempt the direct delete and, on a "not empty"-class failure, **MUST** fall back to a
  **leaves-first** client-side traversal.
- **R3.16** Where a recursive-delete extension is advertised (§4), `vosfs` **SHOULD** prefer it, and
  **MUST** poll the job to a **terminal phase**, distinguishing `COMPLETED` from a partial-failure
  terminal state. HTTP 2xx on job submission is not success.

---

## 4. Capability discovery and negotiation — **first-class**

Because "there are many VOSpace backends in the world," nothing beyond §3 may be assumed.

- **R4.1** `vosfs` **MUST** read the service's VOSI **`capabilities`** document and index the
  advertised `standardID`s to determine which endpoints exist (nodes, transfers, synchronous
  transfer, direct-files, recursive-delete, recursive-setprops, packaging, …).
- **R4.2** Capabilities carrying prototype/extension standardIDs **MUST** be treated as **optional**.
- **R4.3** Behaviours that are **not advertised** and cannot be inferred **MUST** be probed at
  runtime and the result cached per service. At minimum:
  - **byte-range support** on the byte endpoint (`HEAD` → `Accept-Ranges: bytes`),
  - presence of an aggregate size property on containers (§12.1),
  - whether a non-empty container can be deleted directly (§3.15),
  - whether `copyNode` is implemented (§3.14).
- **R4.4** **Every optional capability MUST have a portable fallback**, and the fallback **MUST** be
  correct (if slower). A missing capability is never an error.
- **R4.5** Probe results **MUST** be cached but **MUST NOT** be baked into `storage_options` (they
  are not part of the picklable identity, §6).
- **R4.6** `vosfs` **SHOULD** expose the resolved capability set (e.g. `fs.capabilities`) so callers
  and tests can reason about what a deployment supports.
- **R4.7 (security methods)** Interfaces in the capabilities document advertise the IVOA SSO
  `securityMethod` standardIDs they accept (e.g. TLS-with-client-certificate, cookie, OAuth/token).
  `vosfs` **SHOULD** read these and use them to validate or select the caller's credential (§6.15),
  and **MUST** tolerate services that advertise none.

---

## 5. fsspec conformance

- **R5.1** **MUST** subclass `fsspec.asyn.AsyncFileSystem` with `async_impl = True`.
- **R5.2** **MUST** set `protocol = "vos"`, keep `cachable = True`, implement `_strip_protocol`, and
  register via an `fsspec.specs` entry point so `pandas.read_csv("vos://…")` and dask resolve it
  without an explicit import.
- **R5.3** **MUST** implement the async hooks that have no working default: `_info`, `_ls`,
  `_cat_file`, `_get_file`, `_put_file`, `_pipe_file`, `_rm_file`, `_cp_file`, `_mkdir`, `_makedirs`.
  > `AsyncFileSystem._info` does **not** derive from `_ls` (unlike the sync base). It is the linchpin:
  > `_exists`, `_isdir`, `_isfile`, `_size` all route through it.
- **R5.4** **MUST** override `_rm` (§3.15). **SHOULD** override `_du` (§12.1), `checksum` (§12.5),
  and implement `modified()` (not free — the base raises).
- **R5.5** **MUST NOT** let `open('wb')` silently discard bytes. fsspec's default
  `_initiate_upload`/`_upload_chunk` are **no-ops**; a buffered file returned for `"wb"` without
  those hooks drops data while reporting success.

### 5.1 `info` / `ls(detail=True)` schema

- **R5.6** Every entry **MUST** carry:
  - `name` — the **full, protocol-stripped path** (not a basename),
  - `size` — `int` for data nodes, `None` where unknowable; **MUST** be correct for data nodes
    (`AbstractBufferedFile` reads the `size` returned by `fs.info(path)`; random access breaks
    otherwise),
  - `type` — `"file"` | `"directory"` | `"other"` (see §11).
- **R5.7** Entries **SHOULD** additionally carry `mtime`, `md5`, `content_type`, `uri`, `properties`,
  and (for links) `islink`/`target`.

### 5.2 Paths and URL grammar

- **R5.8** `_strip_protocol` **MUST** normalize `vos://` URLs, leading/trailing slashes, and root.
- **R5.9 (locked)** The **service is selected via `storage_options`, never via the URL authority** —
  the s3fs model, where `endpoint_url` picks the server. The entire post-`vos://` remainder is a
  **path**. This keeps `_strip_protocol` stable if a later layer adds registry/shortname resolution
  as *additional storage options*. Moving the service into the URL authority afterwards would be a
  breaking change.

---

## 6. Serialization, lifecycle, credentials — **highest-risk requirement**

`AbstractFileSystem.__reduce__` returns `make_instance, (type(self), storage_args, storage_options)`.
Pickling **discards all live state and re-runs `__init__` on the dask worker**.

- **R6.1** Every value in `storage_options` **MUST** be picklable. Credentials **MUST** be passed as
  *configuration* (a certificate **path**, a token **string** or token-file path, a provider
  reference), never as live objects.
- **R6.2** `vosfs` **MUST NOT** accept a live HTTP client/session/auth object as a supported
  constructor argument. It breaks dask silently and risks serializing secrets.
- **R6.3** The HTTP client **MUST** be created **lazily on first use**, never stored as a constructor
  arg, and **MUST** be torn down via `weakref.finalize`.
- **R6.4** A `get_client` factory kwarg **MAY** exist but **MUST** be a module-level (picklable)
  callable — never a lambda or closure. Injecting a pre-built client for tests **MUST** require
  `skip_instance_cache=True` and **MUST NOT** be the documented path.
- **R6.5** Constructor kwargs **are** the public API (they are `storage_options`); renaming one is a
  breaking change.
- **R6.6** **MUST** expose `endpoint_url`; **SHOULD** expose `asynchronous`, `loop`, `client_kwargs`,
  `default_block_size`, `default_cache_type`.

### 6.1 Authentication — extensible, minimum four methods

- **R6.7** `vosfs` **MUST** support at minimum: **X.509 client certificate (mTLS)**, **Bearer
  token**, **OIDC access token**, and **anonymous**.
- **R6.8** Authentication **MUST** be modelled as an **extensible credential interface** (a
  `Protocol`/ABC), not a frozen set of constructor kwargs, so further methods (cookie, netrc, HTTP
  Basic, future IVOA SSO methods) can be added without an API break. The interface **MUST** expose
  two distinct hooks, because the methods attach at different layers:
  - a **transport hook**, applied when the lazy client is constructed — the X.509 client certificate
    (a TLS-layer concern), cookie jars, custom SSL context;
  - a **per-request hook**, applied to every outgoing request — `Authorization` headers — so a token
    can be rotated **without rebuilding the client**.
- **R6.9** Every credential implementation **MUST** be picklable (R6.1) and **MUST** re-establish
  itself on a deserialized instance.
- **R6.10** Token credentials **MUST** accept a literal string, and **SHOULD** additionally accept a
  **path to a token file** or a **module-level picklable provider callable** returning a currently
  valid token. **The provider seam is how token refresh is supported without `vosfs` owning the
  identity flow.**
- **R6.11** `vosfs` **MUST NOT** perform interactive or delegated credential *acquisition* (OIDC
  device-code flows, certificate minting). Acquisition belongs to the layer above; `vosfs`
  **consumes** credentials. Refresh, where required, is delegated to the caller's provider (R6.10).
- **R6.12** On `401`, where a provider is configured, `vosfs` **SHOULD** re-invoke it once and retry
  the request **exactly once**; on repeat failure raise `PermissionError`. A static token **MUST NOT**
  be retried.
  > This matters disproportionately for dask: a short-lived OIDC access token pickled into
  > `storage_options` will be stale by the time a worker uses it hours later. Only the provider (or
  > token-file) indirection survives that.
- **R6.13 (secrets in pickles)** `vosfs` **SHOULD** prefer indirection — a certificate path, a
  token-file path, a provider reference — over an inline secret, so a filesystem pickled to a worker
  does not carry the credential. It **MUST** document that an inline token **is** serialized into the
  pickle.
- **R6.14** `vosfs` **MUST NOT** send vendor-specific delegation headers (some deployments reject
  them); bearer and OIDC credentials belong in `Authorization: Bearer`.
- **R6.15** Where the service advertises `securityMethod`s (R4.7), the configured credential
  **SHOULD** be validated against them and a mismatch **SHOULD** fail fast with an actionable error
  rather than surfacing as an opaque `401` later.
- **R6.16 (per-endpoint credentials)** The credential for the node service **MAY NOT** be the
  credential for a negotiated byte endpoint: a negotiation response can return an endpoint that is
  **pre-authorized/anonymous**, or that demands a different security method. `vosfs` **MUST** honour
  the `securityMethod` attached to the chosen protocol/endpoint — **including sending no credential
  at all** when the endpoint is pre-authorized.

---

## 7. Read path

- **R7.1** Reads **MUST** resolve a byte endpoint per §3.8–§3.11 (negotiate; use the direct endpoint
  only when advertised).
- **R7.2** `vosfs` **MUST** follow `3xx` redirects on reads, re-applying request headers (notably
  `Range`) to the final request.
- **R7.3** **Byte ranges are an optional capability** and **MUST** be feature-detected (§4.3).
  - Where supported, `_fetch_range(start, end)` **MUST** issue `Range: bytes=start-end`, expect
    `206`, honour `Content-Range`, and handle single-range-only services and out-of-bounds responses.
  - Where **not** supported, `_fetch_range` **MUST** degrade explicitly (fetch once into a buffer /
    local cache) and **SHOULD** warn once. It **MUST NOT** silently re-download the whole object per
    range: fsspec's default turns a Parquet-footer read of a 100 GB object into a 100 GB transfer.
- **R7.4** `_open(path, "rb")` **MUST** return an `AbstractBufferedFile` subclass implementing
  `_fetch_range`, yielding `seek`/`read`/`readinto`. This is what makes Parquet footers, FITS
  headers, `blockcache::`, and pyarrow's `open_input_file` work.
- **R7.5** `_cat_ranges` **SHOULD** be implemented with bounded concurrency — zarr v3 and
  `fsspec.parquet` depend on it.
- **R7.6** Authentication against a negotiated byte endpoint is governed by that endpoint's
  `securityMethod`, not by the node-service credential (R6.16) — it may be a different method, or
  none at all.

---

## 8. Write path

- **R8.1** Writes **MUST** resolve a byte endpoint per §3.8–§3.11.
- **R8.2** `vosfs` **MUST** set an explicit binary content type on uploads
  (`application/octet-stream`); some services reject or mishandle a defaulted type.
- **R8.3 (silent-corruption trap)** When a write endpoint responds with a redirect, an HTTP client
  that auto-follows will, per **RFC 7231 §6.4.4**, rewrite a `PUT`/`POST` as a `GET` — reporting
  success having transferred **zero bytes**. `vosfs` **MUST NOT** enable automatic redirect following
  on write requests; it **MUST** capture the `Location` and re-issue with **method and body
  preserved**, or fail loudly.
- **R8.4** The parent container **MUST** exist before an upload; a missing parent **MUST** surface as
  `FileNotFoundError`.
- **R8.5** `_open(path, "wb")` **SHOULD** be an `AbstractBufferedFile` with `_initiate_upload` /
  `_upload_chunk` / `_finalize_upload`. Chunked/segmented resumable upload is out of scope for 0.1,
  but this seam **MUST** be shaped so it can be added without an API break.
- **R8.6** `vosfs` **SHOULD** send an integrity digest on upload where the service supports it, and
  **SHOULD** verify the resulting node checksum.
- **R8.7** Authentication against a negotiated write endpoint is governed by that endpoint's
  `securityMethod` (R6.16), not by the node-service credential.

---

## 9. Namespace operations

- **R9.1** `_ls` **MUST** send an XML `Accept` header (a defaulted `Accept: application/json` breaks
  node retrieval on real services) and **MUST** implement paging per §3.7.
- **R9.2** `_mkdir` **MUST** send a schema-complete `ContainerNode` document (§3.6) whose `uri` is
  authority-qualified (§3.1).
- **R9.3** Services **do not** create missing parents. `_makedirs` **MUST** implement `mkdir -p`:
  discover the missing ancestors, create them top-down, and **tolerate a concurrent-creation
  conflict** — fsspec's recursive `_put` issues `_makedirs` for sibling directories **concurrently,
  in arbitrary order**.
- **R9.4** `_rm(recursive=True)` per §3.15/§3.16.
- **R9.5 (fsspec quirk)** fsspec's recursive `_get` **does not filter directories** out of the
  expanded path set — it calls `_get_file` on container nodes, after pre-creating the local tree.
  `_get_file` **MUST** skip when its local target is already a directory.

---

## 10. Transfers

- **R10.1** Same-service `mv` **MUST** use the `/transfers` move job (`keepBytes=false`) where
  supported — no bytes cross the client.
- **R10.2** Move faults to surface: missing source; missing/non-container destination parent;
  destination already exists; moving a container into its own descendant.
- **R10.3** `cp` **MUST** attempt server-side `copyNode` only if §4.3 says it is implemented, and
  **MUST** fall back to a client relay otherwise.
- **R10.4** Cross-service copy/move **MUST** be a client relay (§3.12).
- **R10.5** A relay **MUST** stream; it **MUST NOT** buffer the whole object in memory. It **SHOULD**
  verify a checksum end-to-end, and for a move **MUST** delete the source only after the destination
  is verified, failing closed (drop the partial destination, never touch the source) otherwise.

---

## 11. LinkNode policy — **resolved**

`vosfs` **MUST** mirror `fsspec.implementations.local.LocalFileSystem`, which never reports
`type="link"`: it re-stats following the symlink and reports the **target's** `type`/`size`, exposing
link identity through separate `islink()` / `lexists()`.

Rationale: `isdir`/`isfile` branch on the exact strings `"directory"`/`"file"`, so a `type="link"`
node would report **neither** — `walk` would bucket a link-to-directory as a file and never descend
it, and generic consumers guarding on `fs.isfile(p)` would break.

- **R11.1** `info()`/`ls()` **MUST** report a LinkNode's **resolved target** `type` and `size`, plus
  the extra keys `islink: True` and `target: <URI>` (fsspec ignores unknown keys; `vosfs`-aware
  callers use them, e.g. to render `→ target`).
- **R11.2** `vosfs` **MUST** provide `islink(path)` and `lexists(path)` as the non-following escape
  hatches.
- **R11.3** A link whose target is **outside the space** (an arbitrary URI, which the spec permits)
  cannot resolve to a node → **MUST** report `type="other"` with `target` set.
- **R11.4** A broken/unreadable link **MUST NOT** fail a directory listing: report `type="other"`,
  `size=None` in `ls`; raise `FileNotFoundError` only from a direct `info()`.
- **R11.5** Link resolution **MUST** be depth-bounded; a cycle **MUST** raise `OSError(ELOOP)`.
- **R11.6** A `follow_links` option (default `True`) **SHOULD** exist; `ls` **SHOULD** resolve links
  with bounded concurrency (each follow costs an extra node read).
- Note: services resolve links appearing as *path components* server-side, so `open("a/link/b.csv")`
  works regardless. The follow decision only bites on `info()`/`ls()` of the link itself.

---

## 12. Optional capabilities and extensions

Each **SHOULD** be an `async def _x` + `sync_wrapper` pair, gated on §4 discovery, with a fallback.

- **R12.1 — `du` from the server.** Some services publish an aggregate subtree size on containers
  (an implementation-dependent optimization). `_du` **MAY** use it when present, **MUST**
  feature-detect, **MUST** fall back to traversal, and **MUST** document that such values may be
  **eventually consistent**. Never assume its presence or its semantics.
- **R12.2 — permissions.** fsspec has no permission API; backends add their own. VOSpace permissions
  are **group ACLs, not POSIX mode bits**, updated via `setNode` (`POST /nodes`) with a full node
  document whose `uri` is authority-qualified and whose node type is unchanged. `vosfs` **SHOULD**
  expose `set_permissions(path, *, read_groups, write_groups, public, inherit)` and **MUST** treat
  server-computed properties (size, checksum, dates, creator, quota) as read-only.
  > Property spellings, namespaces and value delimiters vary between deployments (Appendix A.4).
  > `vosfs` **MUST** drive them from the property URIs it reads back, not from hardcoded constants.
- **R12.3 — recursive property set / recursive delete.** Where advertised, `vosfs` **MAY** use the
  recursive job endpoints; it **MUST** poll to a terminal phase and treat partial failure as failure.
- **R12.4 — bulk export.** Where a packaging endpoint is advertised, `vosfs` **MAY** expose an
  archive download. It is **forward-only, no random access** — an export primitive, never a read path.
- **R12.5 — `checksum`.** fsspec's default `checksum()` returns a *token of the info dict*, not a
  content hash. `vosfs` **SHOULD** override it to return the node's checksum property when available.
- **R12.6 — signed/preauth URLs.** Where the service can mint an anonymous endpoint, `vosfs` **MAY**
  expose `sign(path, expiration)` (the s3fs/gcsfs convention).
- **R12.7 — `invalidate_cache(path)`** **SHOULD** be provided (dircache invalidation).

---

## 13. Error mapping

- **R13.1** A **single translation function** at the HTTP boundary **MUST** map VOSpace faults to
  stdlib exceptions (the s3fs `errors.py` / gcsfs `validate_response` pattern). fsspec, pandas and
  dask branch on these exact types. Mapping **MUST** key on the **fault name in the response body**
  where present, falling back to HTTP status — services differ in the statuses they choose.

| VOSpace fault | Typical HTTP | Raise |
|---|---|---|
| `PermissionDenied` | 403 | `PermissionError` |
| `NotAuthenticated` | 401 | `PermissionError` |
| `NodeNotFound` / `ContainerNotFound` / `UnreadableLinkTarget` | 404 | `FileNotFoundError` |
| `DuplicateNode` | 409 | `FileExistsError` |
| `InvalidURI` / `InvalidArgument` / `TypeNotSupported` / `ViewNotSupported` / `OptionNotSupported` | 400 | `ValueError` |
| container not empty | 400/409 | `OSError(ENOTEMPTY)` → triggers §3.15 fallback |
| `NodeLocked` | 423 | `PermissionError` |
| `NodeBusy` / `ServiceBusy` | 409/503 | retry (§14.2) |
| `QuotaExceeded` / entity too large | 413 | `OSError(ENOSPC)` |
| operation not implemented | 405/501 | capability=false (§4.3) + fallback; else `NotImplementedError` |

---

## 14. Concurrency, robustness, performance

- **R14.1** Nothing **MUST** block the event loop: local file I/O and hashing go through
  `asyncio.to_thread`; job polling uses `asyncio.sleep`.
- **R14.2** Transient failures (`503`, `429`, `408`, connection resets, timeouts) **SHOULD** be
  retried with capped exponential backoff **plus jitter**; `404`/`403`/`400` **MUST NOT** be retried;
  request bodies **MUST** be rewound before any replay.
- **R14.3** Batch operations **MUST** use fsspec's `_run_coros_in_chunks` / `batch_size`, not
  unbounded `gather`.
- **R14.4** UWS jobs **MUST** be polled to a **terminal phase** and **MUST** distinguish success from
  partial-failure terminal states.
- **R14.5 (forward-compat seam)** Resumable/segmented upload, `.part`+`Range` download resume, and
  replica failover are out of 0.1 scope, but §7/§8 **MUST** be shaped so they can be added behind
  `_fetch_range` / `_initiate_upload` / `_upload_chunk` without an API break.

---

## 15. Testing

There is **no requirement to stand up a VOSpace server in CI** — the customer is the spec.

- **R15.1** The primary gate **MUST** be a **spec-conformance unit suite**: `respx`-mocked
  interactions asserting the *wire contract* (documents, headers, status handling, fault mapping),
  deterministic and offline, on every PR.
- **R15.2** `vosfs` **MUST** ship a **reusable, endpoint-agnostic conformance suite** that can be
  pointed at **any** live VOSpace deployment with a credential
  (`pytest --vospace-endpoint=… --cert=…`, opt-in, marked `integration`). This is the project's real
  answer to "there are many backends in the world," and it is a contribution to the ecosystem in its
  own right: it tells any operator whether their deployment conforms, and which optional
  capabilities they expose.
- **R15.3** The conformance suite **MUST** be capability-aware: a test for an optional feature
  **skips** (not fails) when §4 discovery says the service does not advertise it.
- **R15.4** A **picklability test** (`pickle.loads(pickle.dumps(fs))`, then use it) **MUST** exist —
  R6 is silently violable.
- **R15.5** The suite **MUST** cover both facades: `asynchronous=True` instances and the generated
  sync API.
- **R15.6** Interop tests **SHOULD** assert `pandas.read_csv("vos://…", storage_options=…)`,
  `fsspec.get_mapper`, and a `blockcache::vos://…` round-trip (the last only where ranges exist).
- **R15.7** A credential-gated live smoke **MAY** run on a schedule against whatever deployments the
  project has access to, purely to detect drift.

---

## 16. Packaging & API stability

- Python ≥3.10; `pyproject.toml` + `uv`/`uv_build`; `src/vosfs/`; ruff + ruff-format + ty +
  pre-commit; pytest + respx; conventional commits; zensical docs.
- Runtime deps: `fsspec`, an async HTTP client (`httpx`), `defusedxml`. **No `cadcutils`, no
  `requests`.**
- FUSE via the optional extra `vosfs[fuse]`.
- **R16.1** Constructor kwargs are the public API (they are `storage_options`); changing them is
  breaking. Pin a minimum `fsspec` and test against its latest.

---

## 17. Acceptance criteria (0.1)

- [ ] `fsspec.filesystem("vos", endpoint_url=…, cert=…)` performs `ls/info/cat/get/put/rm/mkdir` against a live deployment
- [ ] capability discovery works; every optional feature has an exercised fallback path
- [ ] byte transfer works via **negotiation** on a service that does not advertise a direct byte endpoint
- [ ] `pickle.loads(pickle.dumps(fs))` yields a working filesystem (dask-safe)
- [ ] all four auth methods exercised end-to-end: **X.509 mTLS**, **Bearer**, **OIDC access token via a provider**, **anonymous**
- [ ] a **pickled** filesystem re-authenticates in a fresh process via path/provider indirection, carrying **no inline secret**
- [ ] a `401` triggers exactly one provider refresh + retry, then `PermissionError`; a static token is not retried
- [ ] a **pre-authorized** negotiated endpoint is used **without** sending credentials (R6.16)
- [ ] a third-party credential type can be added without changing `vosfs`'s public API (R6.8)
- [ ] `open('rb')` is seekable and issues ranged GETs where supported; degrades explicitly where not
- [ ] `pandas.read_csv("vos://…", storage_options=…)` reads a real file
- [ ] `_rm(recursive=True)` succeeds on a service that refuses non-empty container deletes
- [ ] `cp` falls back to relay on a service without `copyNode`; relay streams (no whole-object buffering)
- [ ] LinkNodes resolve per §11; `islink`/`lexists` behave
- [ ] every fault in §13 maps to the specified exception
- [ ] spec-conformance suite green; conformance suite runs clean against ≥1 live deployment
- [ ] published as `vosfs 0.1.0a0`

---

## 18. Resolved questions

No open questions remain. Recorded for provenance:

- **Independent deployments to validate against** — *out of scope for 0.1.* The conformance suite
  (§15.2) is written against the spec and is endpoint-agnostic; sourcing third-party deployments is a
  later concern.
- **Publishing the conformance suite as an installable extra** (`vosfs[conformance]`) — *not needed,
  out of scope.* It ships in the test tree.
- **Capabilities present elsewhere but absent from the implementations we observed** — *none known.*
  §4's discovery-plus-fallback rule is what protects against over-fitting, not an enumeration of
  vendors.
- **License** — governed by the repository (`AGPL-3.0`), not by this document.

---

## Appendix A — Observed implementation behaviour (INFORMATIVE, NON-NORMATIVE)

Measured against opencadc `cavern` (live) and read from the `opencadc/vos` +
`opencadc/storage-inventory` sources. **These are two data points, not the contract.** They are
recorded because they are the concrete evidence behind several requirements above — and because every
one of them was invisible to mocked tests.

**A.1 Byte endpoints.** `cavern` serves `/files/<path>` in-process; `vault` **redirects** it to a
separate storage host. `vault`'s storage host supports `Range` (`206`, single range only,
`Accept-Ranges: bytes`); `cavern` supports **none** (streams the whole file, never inspects `Range`,
sets no `Accept-Ranges`; empty files return `204`). → §4.3, §7.3.

**A.2 Writes.** Uploading without an explicit binary content type returned `500` on cavern. On vault,
`PUT /files` returns a `303` to the storage host — auto-following it downgrades the `PUT` to a `GET`
and reports a successful upload of zero bytes. → §8.2, §8.3.

**A.3 Namespace.** `mkdir` required a schema-complete `ContainerNode` document *and* an
authority-qualified `uri` (`vos://cadc.nrc.ca~arc/…`); a bare `vos://<path>` returned
`400 InvalidURI: vos URI mismatch`. Existing node → `409 DuplicateNode`; missing parent →
`404 ContainerNotFound`; parents are not auto-created. **`DELETE` of a non-empty container returned
`400 "container … is not empty"`, deviating from the spec's recursive-delete semantics.** → §3.15, §9.2, §9.3.

**A.4 Permissions.** Group ACLs are carried as `#groupread`/`#groupwrite` whose values are
**space-delimited** GMS `GroupURI`s; the public flag is spelled `#ispublic` (the spec says
`#publicread`); `inheritPermissions` and `islocked` live in a **vendor namespace**, not `ivoa.net`.
Size, checksum, dates, creator and quota are server-owned and immutable. → §12.2.

**A.5 Transfers.** `copyNode` (`keepBytes=true`) returns `405 "copyNode is not implemented"` on both
cavern and vault; the persistence layer has no `copy()` at all. Third-party transfer directions are
hard-stubbed. Same-service `mv` works server-side. → §3.14, §10.3, §10.4.

**A.6 Aggregate size.** On CephFS-backed cavern, a container's `#length` is the **recursive** subtree
size (read from the `ceph.dir.rbytes` extended attribute) — a free server-side `du`. It is
**eventually consistent**, and **absent entirely** on deployments not using that quota plugin.
Vault aggregates differently. → §12.1.

**A.7 Listing.** The paging start-boundary `uri` is **inclusive** (the boundary node repeats).
`sort`/`order` are unimplemented. `detail=max` performs a per-child access-control evaluation and is
materially more expensive than `detail=min`. → §3.7.

**A.8 Extension endpoints.** `/files`, `/async-delete`, `/async-setprops` and the packaging endpoint
carry prototype (`-proto`) standardIDs — opencadc extensions, not ratified IVOA. `/synctrans` is
implemented as a suspend/redirect UWS job rather than the spec's one-shot URL-parameter form. → §3.11, §4.2.
