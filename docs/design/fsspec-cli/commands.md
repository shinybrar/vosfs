# `fsspec-cli` command contracts

Status: **Normative.** Each section states one command's *delta* from
[`contract.md`](contract.md), which supplies operand parsing, diagnostics,
result validation, buffered output, exit status, and source lifecycle for all
of them.

Executable evidence is the test suite under `src/fsspec-cli/tests/`; per-row
status is in [`matrix.md`](matrix.md). Where prose and a passing gate disagree,
the gate wins.

## Summary

| Command | Form | Backend hooks awaited |
| --- | --- | --- |
| `ls` | `ls [-A] [--] name:/path...` | `_info`, `_ls` |
| `ls -l`, `ll` | `ls [-Alh] [--] name:/path...` | `_info`, `_ls(detail=True)` |
| `du` | `du [-sh] [--] name:/path` | `_du` |
| `find` | `find [--maxdepth N] [--type f\|d] [--] name:/path` | `_find` |
| `tree` | `tree [--maxdepth N] [--] name:/path` | `_walk` |
| `size` | `size [--] name:/path...` | `_size` or `_sizes` |
| `test` | `test -e\|-d\|-f [--] name:/path` | `_exists` / `_isdir` / `_isfile` |
| `info` | `info [--] name:/path` | `_info` |
| `stat` | `stat [--] name:/path...` | `_info` |
| `head` | `head -c N [--] name:/path` | `_cat_file` |
| `tail` | `tail -c N [--] name:/path` | `_info`, `_cat_file` |
| `cat` | `cat [--] name:/path\|-...` | `_info`, `_get_file` |
| `cp` | `cp [--] SOURCE... DESTINATION` | `_info`, `_cp_file` or `_get_file`+`_put_file` |
| `cp -R` | `cp -R\|-r [--] name:/dir name:/target` | `_info`, `_walk`, transfer hooks |
| `mv` | `mv [--] name:/path... name:/path` | `_info`, `_mv` |
| `mkdir` | `mkdir [-p] [--] name:/path...` | `_mkdir` or `_makedirs`, `_info` |
| `rmdir` | `rmdir [--] name:/path...` | `_info`, `_rmdir` |
| `unlink` | `unlink [--] name:/path` | `_info`, `_rm_file` |
| `rm` | `rm [-dfv] [-R\|-r] [--] [name:/path...]` | `_info`, `_rm_file`, `_rmdir` |
| `basename` | `basename string [suffix]` | none (source-free) |
| `dirname` | `dirname string` | none (source-free) |
| `sign` | `sign [--] name:/path` (opt-in) | `sign` |

## Listing

### `ls`

Names only. One operand: a file writes its exact mapped operand; a directory
writes its sorted immediate child basenames with no header; an empty directory
writes nothing.

Several operands: successful non-directories form the first block, one per
line. Each successful directory then forms a block headed by its exact mapped
operand plus `:`. Blocks are joined by exactly one empty line, with no leading
or trailing blank. An empty directory still writes its header.

```text
$ ls local:/a.txt memory:/docs
local:/a.txt

memory:/docs:
guide.md
```

`-A` includes entries beginning with a dot, excluding `.` and `..`.

### `ls -l` / `ls -lh` / `ll`

Long listing through the §10 normalization layer with adaptive columns. `ll` is
an inherent-long alias; it accepts `-A` and `-h` but **not** `-l`, which would
be redundant. `-h` requires a long listing:

```text
ls: -h: requires long listing
```

Grouping matches plain `ls`: files first, then one block per directory. A
malformed detailed list or a normalization failure produces no partial block
for that operand; other operands keep their complete results and the final
status is `1`.

```text
file  12  report.txt

memory:/docs:
file  1K  guide.md
dir    -  sub
```

### `du`

Recursive exact-byte usage from one `_du` call. `-s` passes `total=True` and
renders one record for the operand path as spelled; without `-s`, the returned
mapping is rendered as `<size>\t<path>` per entry, collated. `-h` renders sizes
through the shared 1024-base helper and changes no other field.

`du` is recursive on the default async hook: it can traverse the whole subtree
and read metadata for every file. `-s` changes the output, not the traversal
cost.

### `find`

Recursive paths, one per line, collated. `--type f` (default) awaits `_find`;
`--type d` awaits `_find(withdirs=True, detail=True)` and selects entries whose
`type` is `directory`. `--maxdepth 0` is implemented as `maxdepth=1` plus a
post-filter to the operand itself.

Each returned spelling is rendered exactly. The command adds no source name and
never invents a root entry a backend did not return. There are no predicates,
globbing, or `-exec`.

### `tree`

One buffered Unicode hierarchy rendered from `_walk`. `--maxdepth N` bounds
recursion. fsspec's own `tree()` returns a synchronous string, so this command
renders from `_walk` instead.

One top-level `_walk` invocation is not one remote request: remote sources may
perform one listing request per reached directory.

## Metadata

### `size`

For exactly one operand, awaits one `_size`. For two or more, groups operands
by source in first-appearance order and awaits one `_sizes(paths)` per group,
preserving that group's operand order and duplicates. The CLI MUST NOT call
`_size` per item on the multi-operand path.

`_size` MUST return an exact non-negative `int`. A `_sizes` result MUST be an
exact `list` of the same length as the submitted paths, containing only exact
non-negative ints.

Output retains the source name so equal paths from different filesystems stay
unambiguous:

```text
5\tmemory:/docs/a.txt
7\tlocal:/tmp/b.bin
```

### `test`

Exactly one *distinct* predicate is required:

```text
test: exactly one predicate selector is required
```

Repeating the **same** selector (`test -e -e`) is idempotent and still selects
one predicate; two **different** selectors, or none, is a usage error.

Awaits `_exists`, `_isdir`, or `_isfile` respectively. The result MUST be an
exact `bool`. No stdout; the answer is the exit status.

### `info`

Awaits one `_info` and pretty-prints every normalized field plus
backend-specific values under `extra`. Sparse fields stay `None`; bytes,
datetimes, tuples, and mappings keep their Python representation rather than
being forced through JSON.

Mapping traversal snapshots `len(mapping)`, enumerates through `for key in
mapping`, and retrieves through `mapping[key]` — it does **not** consult an
overridden `items()`. If the enumerated count and final size disagree with the
snapshot, the whole result is incompatible.

### `stat`

The stricter reduced BSD/macOS-shaped, Local-rich view. One line per operand:

```text
<mode> <nlink> <owner> <group> <size> "<mtime>" <pathname>
```

`<mode>` is `stat.filemode(mode)`; `<owner>`/`<group>` resolve through the local
`pwd`/`grp` databases, falling back to the numeric id; `<mtime>` uses fixed
C-locale month abbreviations in `%b %e %H:%M:%S %Y`, always double-quoted.
Fields are separated by single ASCII spaces.

This is **not** host `DEF_FORMAT` or `LS_FORMAT`. It requires the Local-rich
field set (`mode`, `nlink`, `uid`, `gid`, `size`, `mtime`); a backend missing
any of them yields `incompatible result`. `info` is the backend-neutral view;
`stat` is deliberately narrower and unchanged.

## Reading bytes

### `head` / `tail`

`head -c N` awaits one `_cat_file(path, 0, N)`. `tail -c N` awaits `_info` for
the size, then one bounded `_cat_file(path, size - N)`.

The result MUST be exact `bytes` no longer than `N`. Byte arrays, memory views,
and text are incompatible. Compatible bytes are written unchanged to binary
stdout with no newline, encoding, or text conversion.

A bounded `_cat_file` request is **not** a promise of a ranged physical
transfer. Backends may read a whole object and slice locally — `vosfs` does,
because OpenCADC Cavern serves no HTTP Range.

### `cat`

Concatenates mapped files and stdin to binary stdout. A bare `-` operand, or no
operand at all, reads binary stdin.

Each remote object is staged through exactly one secure local temporary at a
time, so peak CLI memory stays bounded independently of object size. Bytes are
forwarded in bounded chunks with no decoding, headers, separators, or newline
insertion.

Per-operand atomicity covers validation and staging: no bytes from a failed
staging operand reach stdout, though earlier successfully forwarded bytes
remain emitted. Temporaries are always closed and removed — after success,
failure, cancellation, broken pipe, or cleanup failure — and never appear in
diagnostics.

## Copying and moving

### `cp` (files)

Metadata-verified file copy. Same-source copies await `_cp_file`; cross-source
copies stage through one host-local temporary and await `_get_file` then
`_put_file(..., mode="overwrite")`. Multiple sources require an existing
destination directory.

If both configured names resolve to the same filesystem object *and* the same
path, the command rejects `same path` **before** staging or upload.

#### The metadata verification proof

Expected size and recognized source tokens are frozen into an immutable proof
immediately after source validation, before destination resolution or mutation.
After the transfer, the shared verifier requires:

1. the staged source size matches the pre-transfer source `_info`;
2. the destination is a file of that exact size; and
3. every **shared** recognized metadata token matches exactly, under the
   normalized names `ETag` / `etag`, `md5`, `content-md5` / `content_md5`, and
   `checksum`. Tokens must be exact `str` or `bytes`.

With no shared recognized token, exact type and size are the truthful proof.
**No cryptographic strength is claimed** — this is an agreement check on
whatever both ends happen to report, not a content hash.

The source temporary is the transfer bridge, **not** a verification download.
There is no destination download, FIFO, pipe, worker thread, synchronous open,
or second temporary. Any future byte comparison would require a separately
profiled explicit opt-in and a blocking comparison through
`asyncio.to_thread`.

Staging cleanup runs after success, ordinary failure, and escaping control
flow. An ordinary cleanup failure is reported only when no transfer or
verification failure already exists, and never masks escaping control flow.
Staging errors disclose only the error class — never local temporary paths or
source content.

Status `0` proves source retention, destination type and byte count, and
agreement of every shared recognized token. A failed upload or later
verification reports that **destination residue may remain**. The command never
deletes the destination to simulate rollback and never claims atomicity.

### `cp -R` / `cp -r`

Verified two-operand directory copy, available only when
`capabilities.recursion.copy` is enabled (default on).

Builds a **frozen manifest** of the source tree, bounded at **10,000 entries**,
before any mutation. Preserves empty directories. Rejects links and special
entries *before* mutating. Verifies the source manifest and destination
metadata before reporting success. Supports same-source and cross-source
routes through one backend-neutral runner over required async hooks.

Dot segments and a source root operand are rejected up front:

```text
cp: <operand>: dot segment unsupported
cp: <operand>: source root unsupported
```

It does **not** promise a snapshot, transaction, rollback, exact mirror, or
POSIX metadata preservation.

### `mv`

Metadata-verified move on **one** mapped filesystem: single file to a target,
or several files into an existing directory. Requires at least one source and
one destination.

**Cross-source `mv` is rejected**, source-free:

```text
mv: cross-source move unsupported
```

See [`lessons.md` §5](lessons.md#5-cross-source-mv-is-rejected-because-deletion-cannot-be-proven).

## Namespace mutation

### `mkdir`

Awaits `_mkdir(create_parents=False)`, or `_makedirs(exist_ok=True)` with `-p`,
then verifies with `_info` that the path is a directory. Successful
invocations emit **no stdout** — no banner, confirmation, or created-path
listing. A post-mutation verification failure is reported as uncertain state.

### `rmdir`

Removes empty directories only. Verifies the operand is a directory via
`_info`, awaits `_rmdir`, then re-checks: absence proves success, including
after a mutation-call exception. Presence after a mutation exception is
confirmed failure. `ENOTEMPTY` renders as `directory not empty`. No stdout on
success. `-p` is unsupported.

### `unlink`

XSI single-file removal. Verifies the operand is a file, awaits `_rm_file`,
then requires an absence check. A directory operand renders `is a directory`.

### `rm`

Removes files. `-d` also removes empty directories, `-f` ignores missing
operands, `-v` prints each removed operand. Option combinations are validated
before any work:

```text
rm: -v: may be supplied once
rm: -d: cannot combine with other options
rm: -f: cannot combine with -v
rm: missing mapped filesystem operand
```

Repeated and grouped `-f` flags are idempotent (`rm -f -f` is `rm -f`), and
`-f` with zero operands succeeds without entering a source or writing output.
`-v` may be supplied only once. Typer accepts a registered option before or
after operands.

`rm -R` / `-r` requires `capabilities.recursion.remove` (default **off**). It
builds a bounded complete manifest through `_info` and `_ls(detail=True)`,
rejects roots, dot segments, links, special entries, and containment failures,
then removes leaves-first through `_rm_file` and `_rmdir`. Success requires an
absence check after every primitive plus a final root-absence proof.

Recursive removal is **sequential and non-atomic**: failure or cancellation can
leave earlier confirmed removals in place and the rest present or uncertain.
There is no prompt, rollback, retry, trash, or recovery. A root operand and any
dot segment are rejected as `rejected path`.

## Source-free lexical commands

### `basename` / `dirname`

Apply the POSIX Issue 8 string algorithms to exactly one argv token. They never
interpret the token as a mapped operand, validate a source name, acquire a
source, or perform filesystem work. `basename` accepts an optional suffix
operand to strip.

A NUL byte in the operand is rejected as `invalid operand`; an embedded newline
is data, not an error. `memory:/docs/a.txt` is ordinary lexical data.

```text
dirname a       -> .        basename a/b        -> b
dirname a/b     -> a        basename /a/b.txt .txt -> b
dirname /a/b    -> /a       basename /          -> /
dirname //      -> /        basename a/b/       -> b
```

Neither expands `~`, resolves dot segments, nor infers a default source.
Multi-operand and zero-delimited GNU extensions are out of scope and are
rejected by Typer.

## Extensions

### `sign` (opt-in)

Calls the selected filesystem's `sign` capability. A source without it exits
nonzero with one `unsupported operation` diagnostic and no traceback. The
extension does not infer support from backend type or protocol.

## Deliberately out of scope

No portable fsspec surface exists for these, so they are not provided:

`df` (no free-space API) · `chmod` / `chown` (no portable mode or owner
*write*) · `truncate` (no partial truncate) · `file` (content sniffing) ·
`wc -l` / `-w` (needs streaming and counting) · symlink *creation* ·
`rmdir -p` · cross-source `mv`.

The package ships no console entry point and no module executable: it is a
library that hands the host a `typer.Typer` to mount.
