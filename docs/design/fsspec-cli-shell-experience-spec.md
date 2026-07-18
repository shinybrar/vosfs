# fsspec-cli shell-experience specification

<!-- pyml disable line-length -->

Status: **Proposal (normative once accepted).** This is the **fsspec-cli** spec —
one of two: the `vosfs` backend is governed by its own spec
([`trd.md`](trd.md)), which is separately receiving IVOA VOSpace 2.1-based
improvements. This document consolidates the command direction for `fsspec-cli`.
It supersedes the core/extension classification in
[`fsspec-cli-extension-roadmap.md`](fsspec-cli-extension-roadmap.md) (kept as
inventory evidence) and supersedes the strict-POSIX stance that locked-rejected
long listings (see §9). Capability claims were introspected against fsspec
**2026.6.0** and validated with a working renderer prototype.

## 1. Framing shift: a shell-compatible experience, not strict POSIX

`fsspec-cli` began as a POSIX-compatibility surface and therefore *rejected*
anything it could not implement completely (for example `ls -l`, because no
single backend supplies every POSIX column). That bar is now relaxed.

**`fsspec-cli` provides a shell-compatible *experience*.** It gives users what a
backend can actually back, in the shape a shell user expects, and **omits what
the backend cannot supply** — rather than refusing the command or fabricating a
value. It is not POSIX, GNU, or BSD `stat(1)`/`ls(1)`; it is "the useful subset,
honestly rendered."

Two rules make this safe:

1. **Never fabricate.** A field that a backend does not report is omitted or
   shown as a neutral `-`. A `0`, a fake mode, or a substituted timestamp is
   never invented.
2. **Adaptive richness.** Output is as rich as the backend allows and no richer.
   The same command against Local shows a full POSIX-like row; against an
   object store it shows type + size + mtime; against a bare backend it shows
   type + size. The command does not change — only the data does.

## 2. The info-normalization layer (the core mechanism)

Every listing/metadata command is powered by **one pure adapter** that turns an
fsspec `info(detail=True)` / `ls(detail=True)` dict — whose *shape differs per
backend* — into a normalized `ListingRow`. This is the single place that knows
how to read heterogeneous fsspec metadata; every command renders from it.

```python
@dataclass(frozen=True)
class ListingRow:
    name: str                 # basename
    kind: str                 # "file" | "dir" | "link" | "other"
    size: int | None          # bytes, or None (unknown -> "-")
    mtime: float | None       # epoch seconds, normalized from any source
    mode: int | None          # st_mode, if the backend reports it
    nlink: int | None
    owner: str | int | None   # uid or resolved name
    group: str | int | None
    link_target: str | None
    extra: Mapping[str, object]  # backend-specific values, verbatim (for `info`)
```

### 2.1 Field mapping (grounded in the prototype)

| ListingRow | fsspec info key(s) | Present on |
| --- | --- | --- |
| `name` | `name` (basename of) | all |
| `kind` | `type` (+ `islink`) | all (floor) |
| `size` | `size` (may be `None`) | all (floor; `None` allowed) |
| `mtime` | `mtime` / `LastModified` / `last_modified` | Local, object stores, vosfs |
| `mode` | `mode` (st_mode int) | Local only |
| `nlink` | `nlink` | Local only |
| `owner` | `uid` (opt. resolved via pwd) | Local only |
| `group` | `gid` (opt. resolved via grp) | Local only |
| `link_target` | `destination` / `target` | Local, vosfs (backend-specific) |
| `extra` | every other key (`ETag`, `md5`, `uri`, `StorageClass`, …) | backend-specific |

**Time normalization is part of the layer.** Backends report time as an epoch
float (Local), a `datetime` (Memory `created`), or an ISO-8601 string
(vosfs `mtime`, S3 `LastModified`). The adapter coerces all of these to epoch
seconds; an unparseable or absent time yields `None`. Precedence:
`mtime` → `LastModified`/`last_modified`; `created` is **not** substituted for
`mtime` (labeled differently in a shell). Timezone-less ISO strings and naive
`datetime` values are interpreted as UTC by convention, independent of backend.

### 2.2 Adaptive columns

A long listing renders **only the columns some row in the result supports**. If
no entry has `mode`, the mode/owner/group/nlink columns are dropped entirely
(not shown as `?????????`); a per-row gap in an otherwise-present column shows
`-`. So Local yields a full row, Memory yields `kind size mtime name`, and a
mixed listing shows the union that at least one row backs.

### 2.3 Human-readable sizes

`-h` renders sizes as `1K`, `34K`, `1.2M`, … (1024-base, matching `ls -h`/`du
-h`). Without `-h`, exact byte counts. `-h` never applies to non-size columns.

## 3. Flag conventions (repo-wide)

- **`--help`** shows help (unchanged). **`-h` now means human-readable** in the
  size-bearing commands (`ls -l`, `du`) — this is the reserved-flag decision
  from the prior review arriving. `-h` is *not* a global help alias.
- `-l` long listing, `-a`/`-A` include-all (existing `ls` semantics), `-r`
  reverse, `-t` sort by mtime, `-S` sort by size (listing).
- `-c N` byte count for `head`/`tail`; `-s` summarize for `du`.
- Unknown options still fail closed with the stable `<cmd>: <tok>: unsupported
  option` diagnostic.

## 4. Command set (all CORE, backend-neutral)

None of these branch on backend type; each uses a verified async fsspec hook and
the normalization layer where metadata is involved. Backend-*specific* commands
(presigned URLs, versions) remain the extension seam's job (§8).

| Command | Shell shape | fsspec hook | Best-effort semantics |
| --- | --- | --- | --- |
| `ls -l` / `-lh`, `ll` | `ls -l`, `ls -lh`, `ll` | `_ls(detail=True)` | Long listing via §2; adaptive columns; 1 call per dir. `ll` is a convenience alias for `ls -l` (`ll -h` for human). Core bare `ls` (names only) is unchanged. |
| `du` / `-s` / `-h` | `du`, `du -sh` | `_du` | Exact bytes; `-s` total only; `-h` human. |
| `find` | `find` (no `-exec`) | `_find` | Recursive file list; `--maxdepth`, `--type f/d`. |
| `head -c N` | `head -c` | `_cat_file(0, N)` | Ranged read; `-n` lines = read+split (bytes fetched). |
| `tail -c N` | `tail -c` | `_info` + `_cat_file(size-N)` | 2 calls; no whole-object transfer. |
| `size` | `wc -c`, `stat -c%s` | `_size` / `_sizes` | Exact size; batched for many operands. |
| `test` | `test -e/-d/-f` | `_exists`/`_isdir`/`_isfile` | Exit-code predicate; no stdout. |
| `tree` | `tree` | `_walk` (reimplement) | Depth-limited; fsspec `tree()` is a sync string, so render from `_walk`. |
| `info` | (no direct shell twin) | `_info` | Pretty-print the full normalized row + `extra`; auto backend-rich. |

`stat` (existing) is retained; it and `info` overlap and should be reconciled so
`stat` is the single-path detailed view and `info` the raw dict, or `info`
becomes `stat --long`. (Resolved during implementation.)

## 5. What stays out (no portable fsspec surface)

`df` (no free-space API), `chmod`/`chown` (no portable mode/owner *write*),
`truncate` (no partial truncate), `file` (content sniff), `wc -l`/`-w` (needs
streaming+counting), symlink *creation*. These fail closed with a clear
"unsupported" diagnostic if requested.

## 6. Rendering examples (from the prototype)

```text
# Local (rich) — ls -l
-rw-r--r--  1  brars  staff   1497  Jul 17 18:00  LICENSE
# Local — ls -lh
-rw-r--r--  1  brars  staff     1K  Jul 17 18:00  LICENSE
# Memory (sparse) — ls -lh: mode/owner columns dropped (no row supports them)
file    11B  -  a.txt
dir      -   -  sub
```

## 7. Testing discipline (unchanged rigor)

Every new command ships its own **command compatibility profile** and hermetic
tests exercised across at least **Memory (sparse), Local (rich), and vosfs
(remote)** source forms, proving the adaptive rendering on each. The
normalization layer gets focused unit tests over synthetic info dicts covering:
every time representation, absent size, absent mode, link rows, and
backend-specific `extra` keys. Golden output is asserted per source shape.

## 8. Relationship to the extension seam

The core command set above needs **no** backend branching, so it does **not**
use the extension mechanism — it goes straight into the base app. The extension
seam ([#191](https://github.com/shinybrar/vosfs/issues/191), to be narrowed)
covers only genuinely **backend-specific** commands: presigned `share`/`sign`
(s3fs/gcsfs), object versions, storage class, vosfs VOSpace properties. The
`info` command demonstrates the boundary: it stays core and simply *renders*
backend-specific `extra` keys as data, without a per-backend branch.

## 9. Supersedes

- **`fsspec-cli-ls-long-rejection-profile.md`** — `ls -l` is no longer rejected;
  it is supported best-effort per §2 and the normative
  [`fsspec-cli-ls-long-command-profile.md`](fsspec-cli-ls-long-command-profile.md).
  Retain the rejection profile only as historical rationale.
- The strict "MUST NOT quote/decorate/select columns" language in the plain-`ls`
  profile applies to bare `ls` only; `ls -l` is a distinct, documented mode.

## 10. Ticket reconciliation

Every open ticket, evaluated against this spec:

| Issue | Disposition under this spec |
| --- | --- |
| [#187](https://github.com/shinybrar/vosfs/issues/187) scaffolding consolidation | **Phase 0 / prerequisite.** The shared command toolkit must land first so 8 new commands don't 8× the copy-paste. Rewrite to reference this spec as its motivation. |
| [#191](https://github.com/shinybrar/vosfs/issues/191) extension epic | **Supersede + narrow.** Split: core shell commands move here (§4); #191 keeps only the backend-specific seam (§8). Rewrite. |
| [#186](https://github.com/shinybrar/vosfs/issues/186) cp/mv verify redesign | **Fold in (Phase 3).** "Harden existing commands" step of this spec. Improve to cross-link. |
| [#188](https://github.com/shinybrar/vosfs/issues/188) PyPI publishing | Orthogonal infra; keep. Referenced as the release path for the expanded CLI. |
| [#189](https://github.com/shinybrar/vosfs/issues/189) research archive | Orthogonal docs hygiene; keep. |
| #190 license (BSD) | Closed. |
| [#63](https://github.com/shinybrar/vosfs/issues/63) / [#65](https://github.com/shinybrar/vosfs/issues/65) / [#66](https://github.com/shinybrar/vosfs/issues/66) / [#113](https://github.com/shinybrar/vosfs/issues/113) | **Governed by the separate `vosfs` spec** ([`trd.md`](trd.md)), not this CLI spec. That spec is receiving IVOA VOSpace 2.1-based improvements (evaluation in progress); these tickets are reconciled there. |

## 11. Implementation phases

0. **Scaffolding consolidation (#187)** — one shared command toolkit
   (parse-args, mapped-operand parsing, diagnostics, binary stdout, source
   lifecycle). Prerequisite.
1. **Normalization layer** — `ListingRow` + `to_listing(info)` + time coercion +
   human sizes + adaptive-column renderer. Unit-tested standalone.
2. **First commands** — `du`, `find`, `size`, `test` (pure hooks, no rendering
   layer) and `ls -l`/`-lh` (the layer's first consumer).
3. **Second wave** — `head`, `tail`, `tree`, `info`; reconcile `stat`/`info`.
4. **Harden existing** — fold #186 (cp/mv verify).

Each phase: profile + hermetic tests across Memory/Local/vosfs, then wire into
the app.
