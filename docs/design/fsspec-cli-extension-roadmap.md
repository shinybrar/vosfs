# fsspec-cli extension architecture and improvement inventory

<!-- pyml disable line-length -->

> **Partially superseded** by
> [`fsspec-cli-shell-experience-spec.md`](fsspec-cli-shell-experience-spec.md):
> the core-vs-extension classification moved there (the ubiquitous commands are
> now CORE, not extensions). This document is retained as the verified command
> **inventory** and as background for the backend-specific extension seam.

Status: **Proposal / roadmap.** This is not a locked command compatibility
profile. It records a way to grow `fsspec-cli` beyond its ubiquitous core and an
inventory of the storage commands worth adding, each mapped to a **verified**
fsspec capability. Any command adopted from here gets its own profile and tests
before it ships.

Client baseline for the capability claims below: fsspec **2026.6.0**, method
surface introspected live in this repository (not recalled).

## 1. Principle: core is ubiquitous, extensions are rich or backend-specific

- **Core commands** (`ls`, `cat`, `cp`, `mv`, `mkdir`, `rmdir`, `rm`, `unlink`,
  `stat`, `basename`, `dirname`) use *only* the ubiquitous fsspec interface and
  **never branch on backend type**. Their behavior is a locked, backend-neutral
  compatibility profile. This is the stable, portable base.
- **Extensions** are commands a host opts into. They MAY be richer (more calls,
  more output) and MAY branch on backend (`vosfs`, `s3fs`, …). They cannot change
  core behavior: a host that adds no extensions gets exactly today's surface.

The `stat` command is the precedent — it is explicitly *"Reduced BSD/macOS-shaped
file status over fsspec `_info` (not POSIX)."* Extensions follow that honesty:
serve the useful subset a backend can actually back, and say so.

## 2. The extension seam

One additive, backward-compatible constructor parameter:

```python
class CommandExtension(Protocol):
    def register(
        self,
        app: typer.Typer,
        sources: Mapping[str, AsyncFilesystemSource],
    ) -> None: ...


class App:
    def __init__(
        self,
        sources: Mapping[str, AsyncFilesystemSource],
        *,
        extensions: Sequence[CommandExtension] = (),
    ) -> None:
        ...
        self._register_commands()            # core (unchanged, ubiquitous)
        for extension in extensions:         # opt-in
            extension.register(self.typer_app, self._sources)
```

Extensions register their own Typer commands and reuse the **shared command
toolkit** (source lifecycle, mapped-operand parsing, diagnostics, binary stdout).
That toolkit must first be consolidated into one importable module — see the
scaffolding-consolidation issue — so extensions build on a stable seam instead of
copy-pasting internals. **That consolidation is a prerequisite for a clean
extension API.**

## 3. Two extension flavors

1. **New backend-neutral commands** a host opts into but that are still portable:
   `du`, `find`, `head`, `tail`, `size`, `test`, `tree`, and a rich long listing
   `ll`. These may later graduate into core; they start as extensions so core
   stays minimal while their profiles settle.
2. **Backend-specific commands** that only some backends can serve: presigned-URL
   `share` (s3fs/gcsfs `sign`), object versions, storage class, `vosfs` VOSpace
   properties. These **must feature-detect** and degrade with a clear diagnostic
   on backends that lack the capability — never assume a backend.

### Backend richness for free: the `info` pattern

`fs.info(path)` returns a dict whose *extra* keys are backend-specific (`ETag`,
`StorageClass`, `md5`, `uri`, `mtime`, …). An `info` extension that pretty-prints
that dict is automatically backend-rich **with no per-backend code**: the
ubiquitous path stays generic and quirks surface as *data*, not branches. This is
the model backend-aware extensions should imitate — render what the backend
reports, do not hardcode it.

## 4. Making `ls` better without touching core `ls`

### 4.1 Historical constraint and current resolution

`ls -l` as a complete **POSIX** option was locked-rejected in
[`fsspec-cli-ls-long-rejection-profile.md`](fsspec-cli-ls-long-rejection-profile.md):
no tested backend (Local, Memory, vosfs) can supply a **complete** POSIX long row
(none exposes allocated-block `total`; Memory and vosfs lack mode/nlink/owner).
The later shell-experience specification deliberately stopped claiming complete
POSIX semantics and admitted adaptive `ls -l` / `-lh` plus `ll` under the
[long-listing profile](fsspec-cli-ls-long-command-profile.md). The rejection
profile remains the rationale for never describing those adaptive rows as
POSIX long output.

### 4.2 A separate, honest `ll`

Serve the demand with a distinct non-POSIX command built on a **single
`_ls(detail=True)` call per directory — the same call count as bare `ls`**.

| Column | fsspec source (verified) | Portable? |
| --- | --- | --- |
| name | `entry['name']` | Yes (abstract floor) |
| type indicator (`/`, `@`, file) | `entry['type']` + `entry.get('islink')` | Yes |
| size (`None` → explicit `-`) | `entry['size']` | Yes (`None` allowed) |
| human size (`1.2K`) | derived from `entry['size']` | Yes (presentation) |
| mtime | `entry.get('mtime')` | No — Local & vosfs only; print only when present |
| symlink target | backend `destination` / target URI | No — backend-specific |
| mode / nlink / owner / group | `entry.get('mode')`, … | No — Local only |

The honest cross-backend column set is **type + size (+ human) + conditional
mtime + conditional link target**. Never fabricate a `0` size or substitute
`created` for `mtime`. Bare `ls` stays `_ls(detail=False)`, names only, one call
— untouched and with no backend branch. Explicit `ls -l` and inherent-long
`ll` share the adaptive runner defined by the normative profile. Cost is not
the blocker; honesty is.

## 5. Verified fsspec method surface

The decisive property is the **async hook**: whether `AsyncFileSystem` defines a
native `_<name>` coroutine, awaitable directly at the fsspec-cli seam. Methods
without one are sync-only convenience and must be composed from async primitives.

| fsspec method | Async hook | Notes |
| --- | --- | --- |
| `ls(path, detail=True)` | `_ls` | detail floor: `name`, `type`, `size` |
| `info(path)` | `_info` | same floor as one `ls` entry |
| `du(path, total, maxdepth, withdirs)` | `_du` | `total=True`→int; `total=False`→`{path: size}` |
| `find(path, maxdepth, withdirs, detail)` | `_find` | recursive file list |
| `glob(path, maxdepth)` | `_glob` | wildcard expansion |
| `walk(path, maxdepth, topdown)` | `_walk` | async generator of `(root, dirs, files)` |
| `size` / `sizes` | `_size` / `_sizes` | int bytes; `_sizes` batched |
| `cat_file(path, start, end)` | `_cat_file` | **byte ranges** — the honest async head/tail primitive |
| `cat_ranges(paths, starts, ends)` | `_cat_ranges` | scatter/gather reads |
| `exists` / `isdir` / `isfile` | `_exists` / `_isdir` / `_isfile` | predicates |
| `expand_path(path, recursive, maxdepth)` | `_expand_path` | glob + recursive expansion |
| `pipe_file` / `pipe` | `_pipe_file` / `_pipe` | write bytes |
| `head(path, size)` | none | compose `_cat_file(0, size)` |
| `tail(path, size)` | none | `_info` size then `_cat_file(size-n)` |
| `tree(...)` | none | returns a preformatted string; reimplement over `_walk` |
| `checksum` / `ukey` | none | default is a weak size/mtime hash unless overridden |
| `modified` / `created` | none | backend-conditional `datetime` |
| `touch(path, truncate)` | none | create-empty via `_pipe_file(path, b"")` |
| `sign(path, expiration)` | none | **default raises `NotImplementedError`** — see §7 |

## 6. Master mapping and classification

Classes: **IN-CORE-NOW**, **CORE-CANDIDATE** (ubiquitous, hooked, cheap),
**EXTENSION** (rich / multi-call / non-ubiquitous), **BACKEND-SPECIFIC**,
**OUT-OF-SCOPE** (no portable fsspec surface).

| Candidate | Shell equivalent(s) | fsspec method(s) | Class |
| --- | --- | --- | --- |
| list names | `ls`, `ls -A` | `_ls(detail=False)` | IN-CORE-NOW |
| concat to stdout | `cat`, `mc cat`, `hdfs -cat` | `_cat` / `_cat_file` | IN-CORE-NOW |
| copy / move / mkdir / rm / rmdir / unlink / stat / basename / dirname | POSIX | existing hooks | IN-CORE-NOW |
| **disk usage** | `du`, `du -s`, `gsutil du` | `_du` | CORE-CANDIDATE |
| **size of file(s)** | `wc -c`, `rclone size` | `_size` / `_sizes` | CORE-CANDIDATE |
| **recursive find** | `find`, `mc find`, `hdfs -find` | `_find` | CORE-CANDIDATE |
| **glob match** | shell globbing | `_glob` | CORE-CANDIDATE |
| **head bytes** | `head -c N`, `mc head` | `_cat_file(0, N)` | CORE-CANDIDATE |
| **tail bytes** | `tail -c N`, `hdfs -tail` | `_info` + `_cat_file(size-N)` | CORE-CANDIDATE |
| **exists / test** | `test -e/-d/-f`, `hdfs -test` | `_exists` / `_isdir` / `_isfile` | CORE-CANDIDATE |
| create empty file | `touch`, `hdfs -touchz` | `_pipe_file(path, b"")` | CORE-CANDIDATE (create-only) |
| write from stdin | `mc pipe`, `> file` | `_pipe_file` | CORE-CANDIDATE |
| **rich long listing** | `ls -l`, `rclone lsl`, `mc ls` | `_ls(detail=True)` | EXTENSION (see §4) |
| tree view | `tree`, `mc tree`, `rclone tree` | `_walk` / `_find` | EXTENSION |
| human-readable sizes | `du -h`, `ls -h` | derived from `_size` / `_du` | EXTENSION (presentation) |
| recursive copy | `cp -r`, `aws s3 cp --recursive` | `_expand_path` + N×`_cp_file` | EXTENSION (rejected for core `cp`) |
| sync / mirror | `aws s3 sync`, `rclone sync`, `mc mirror` | `_find` + diff + N×transfer | EXTENSION |
| checksum / hash | `md5sum`, `rclone hashsum` | `checksum` / `ukey` | EXTENSION (weak default) |
| modified / created time | `stat -c%y` | `modified` / `created` | EXTENSION (backend-conditional) |
| **presigned URL** | `aws s3 presign`, `gsutil signurl`, `mc share` | `sign` | BACKEND-SPECIFIC (s3fs/gcsfs; not vosfs) |
| storage class / versions / VOSpace props | `aws s3api …`, vosfs node props | backend `_info` extras | BACKEND-SPECIFIC |
| `df` (free space) | `df`, `hdfs -df` | none | OUT-OF-SCOPE |
| `chmod` / `chown` | `chmod`, `chown` | none (no portable mode/owner write) | OUT-OF-SCOPE |
| `truncate` | `truncate` | none (only full rewrite) | OUT-OF-SCOPE |
| `file` (type sniff) | `file` | none | OUT-OF-SCOPE |
| `wc -l` (lines) | `wc -l` | none (must stream+count) | OUT-OF-SCOPE (`-c` is CORE) |
| symlink create | `ln -s` | none portable | OUT-OF-SCOPE (create); BACKEND-SPECIFIC (read) |

Out-of-scope items share one cause: fsspec exposes no portable surface for them
(free space, mode/owner writes, partial truncate, content sniffing, line
counting). `chmod`/`chown`/`ls -l` all hit the same wall — the abstract contract
has no portable mode/owner, so they cannot be core.

## 7. Presigned URLs (`sign`) — confirmed backend-specific

`AbstractFileSystem.sign` exists but its default raises
`NotImplementedError("Sign is not implemented for this filesystem")`, there is no
`_sign` async hook, and it is **not** overridden by `vosfs`, Local, Memory, or
ftp in the installed environment. Only `s3fs`/`gcsfs` (and `abfs` via SAS)
implement it. A `share`/`presign` command is therefore strictly BACKEND-SPECIFIC
and must degrade to a clean "operation not supported by this filesystem"
diagnostic elsewhere — never a traceback.

## 8. Prioritized shortlist

| # | Addition | Class | fsspec call | Rationale |
| --- | --- | --- | --- | --- |
| 1 | `du` (`-s`, `-h`) | CORE-CANDIDATE | `_du` | Most-requested missing verb; 1 call; exact bytes on any fsspec. |
| 2 | `ll` / `ls-long` | EXTENSION | `_ls(detail=True)` | Biggest UX gap; near-free; serves `ls -l` demand honestly without touching core `ls`. |
| 3 | `find` | CORE-CANDIDATE | `_find` | Ubiquitous across every tool; async-native; 1 call. |
| 4 | `head -c` | CORE-CANDIDATE | `_cat_file(0, N)` | Cheap ranged read; pairs with `cat`. |
| 5 | `tail -c` | CORE-CANDIDATE | `_info` + `_cat_file` | Universally expected; range-read is the honest impl. |
| 6 | `size` / `wc -c` | CORE-CANDIDATE | `_size` / `_sizes` | Trivial, batched, exact; underpins human sizes. |
| 7 | `test` / `exists` | CORE-CANDIDATE | `_exists` / `_isdir` / `_isfile` | Scriptable predicate; exit-code only. |
| 8 | `tree` | EXTENSION | `_walk` / `_find` | High-visibility; must reimplement (fsspec `tree()` is a sync string). |
| 9 | `glob` | CORE-CANDIDATE | `_glob` | Client-side wildcard selection; async-native. |
| 10 | `presign` / `share` | BACKEND-SPECIFIC | `sign` | High value on s3/gcs; degrade cleanly elsewhere. |

Headline five: **`du`, `ll`, `find`, `head`, `tail`.**

## 9. Guardrails

- Core command modules import nothing backend-specific and contain no
  `isinstance(fs, …)` or `fs.protocol == …` branch (enforceable by a grep gate).
- Every extension command ships its own compatibility profile and tested-source
  matrix — same discipline as core.
- Feature-detect backend capabilities (`try … except NotImplementedError`); a
  missing capability yields one clear diagnostic and a nonzero exit, never a
  traceback.
- `-h` stays reserved (rejected today) so a future flag — plausibly `ll -h` for
  human-readable — can claim it rather than being spent on a help alias.
