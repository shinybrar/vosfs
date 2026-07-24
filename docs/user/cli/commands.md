# Command reference

Every command takes operands spelled `name:/path`. `name` is a key from the
source mapping you passed to `App`; `/path` is handed to that backend
literally.

`--` ends option parsing, so a path that begins with `-` is still reachable:

```bash
myapp fs ls -- data:/-weird-name
```

## At a glance

| Command | Does |
| --- | --- |
| [`ls`](#ls-ll) | List directory contents; `-l`/`ll` for long form |
| [`du`](#du) | Recursive byte usage |
| [`find`](#find) | Recursive path list |
| [`tree`](#tree) | Recursive tree drawing |
| [`size`](#size) | Exact byte counts |
| [`test`](#test) | Existence/type predicate as an exit status |
| [`info`](#info) | Full normalized metadata, including backend extras |
| [`stat`](#stat) | Reduced BSD/macOS-shaped status line |
| [`head` / `tail`](#head-tail) | Leading or trailing bytes |
| [`cat`](#cat) | Concatenate files (and stdin) to stdout |
| [`cp`](#cp) | Copy files, or one directory with `-R` |
| [`mv`](#mv) | Move or rename within one filesystem |
| [`mkdir`](#mkdir) | Create directories |
| [`rmdir`](#rmdir) | Remove empty directories |
| [`unlink`](#unlink) | Remove one file |
| [`rm`](#rm) | Remove files; `-d` empty dirs; guarded `-R` |
| [`basename` / `dirname`](#basename-dirname) | Path string slicing, no I/O |

## Listing

### `ls`, `ll` {#ls-ll}

```bash
myapp fs ls data:/project
myapp fs ls -A data:/project          # include dot entries
myapp fs ls -l data:/project          # long form
myapp fs ls -lh data:/project         # long form, human-readable sizes
myapp fs ll -h data:/project          # `ll` is always long
```

A file operand prints itself; a directory prints its sorted children. With
several operands, files come first, then one headed block per directory.

Long listing shows **only the columns the backend actually supplies**. Against
local disk you get a full POSIX-like row; against an object store you get type,
size, and mtime. Missing values show `-`; nothing is invented.

`-h` means human-readable, not help. `ll` does not accept `-l` — it is already
long.

### `du`

```bash
myapp fs du data:/project             # per-path usage
myapp fs du -s data:/project          # total only
myapp fs du -sh data:/project         # total, human-readable
```

!!! warning "`du` is recursive and can be expensive"

    On backends using fsspec's default hook, `du` walks the whole subtree and
    reads metadata for every file. Against a remote store that is many
    requests. **`-s` changes the output, not the traversal cost.**

### `find`

```bash
myapp fs find data:/project
myapp fs find --maxdepth 2 data:/project
myapp fs find --type d data:/project      # directories instead of files
```

No predicates, globbing, or `-exec`. Paths print exactly as the backend
returned them.

### `tree`

```bash
myapp fs tree data:/project
myapp fs tree --maxdepth 2 data:/project
```

One remote listing request per reached directory — bound it on large trees.

## Metadata

### `size`

```bash
myapp fs size data:/a.csv
myapp fs size data:/a.csv data:/b.csv archive:/c.csv
```

Prints `<bytes>\t<operand>`. With several operands it batches per filesystem
into one request each, rather than one request per file. Output keeps the
source name so identical paths on different filesystems stay distinguishable.

### `test`

```bash
if myapp fs test -e data:/project/output.csv; then
    echo "already done"
fi
```

`-e` exists, `-d` is a directory, `-f` is a regular file. Exactly one is
required. No output — the answer is the exit status.

### `info`

```bash
myapp fs info data:/a.csv
```

Everything the backend reports: normalized fields plus backend-specific values
under `extra` (checksums, ETags, storage class, VOSpace properties). Sparse
fields stay `None`.

### `stat`

```bash
myapp fs stat data:/a.csv
```

A reduced BSD/macOS-shaped line:

```text
-rw-r--r-- 1 brars staff 1497 "Jul 17 18:00:00 2026" /a.csv
```

Deliberately narrower than `info`: it needs the full local-rich field set
(`mode`, `nlink`, `uid`, `gid`, `size`, `mtime`). A backend that does not report
them reports `incompatible result`. Use `info` for remote backends.

## Reading bytes

### `head`, `tail` {#head-tail}

```bash
myapp fs head -c 512 data:/big.log
myapp fs tail -c 512 data:/big.log
```

Byte counts only — there is no `-n` line mode.

!!! note "Bounded request, not necessarily a bounded transfer"

    `-c N` bounds what the CLI *asks for*. Whether the backend transfers only
    those bytes is up to the backend. `vosfs` reads the whole object and slices
    locally, because OpenCADC Cavern serves no HTTP Range.

### `cat`

```bash
myapp fs cat data:/a.txt data:/b.txt > combined.txt
myapp fs cat data:/archive.tar.gz | tar -tz
cat local.txt | myapp fs cat - data:/appendix.txt
```

Binary-safe: no decoding, no separators, no newline insertion. A bare `-` (or
no operand) reads stdin.

Each remote object is staged through one local temporary at a time, so memory
stays bounded no matter how large the file is. Temporaries are always removed —
after success, failure, cancellation, or a broken pipe.

## Copying and moving

### `cp`

```bash
myapp fs cp data:/a.csv data:/backup/a.csv          # one file
myapp fs cp data:/a.csv data:/b.csv data:/backup/   # several into a directory
myapp fs cp local:/a.csv archive:/2026/a.csv        # across filesystems
myapp fs cp -R data:/project archive:/project       # a whole directory
```

Every copy verifies destination metadata after writing. Cross-filesystem copies
stage through one local temporary.

`cp -R` requires the `recursion.copy` capability (on by default). It freezes a
manifest of the source tree — bounded at 10,000 entries — before mutating
anything, preserves empty directories, and rejects symlinks and special entries
*before* any write.

It is **not** a snapshot, transaction, mirror, or rollback, and does not
preserve POSIX metadata.

### `mv`

```bash
myapp fs mv data:/draft.csv data:/final.csv
myapp fs mv data:/a.csv data:/b.csv data:/archive/
```

Within **one** filesystem only.

!!! failure "Cross-filesystem `mv` is rejected"

    ```text
    mv: cross-source move unsupported
    ```

    A correct move must delete the same version of the file it copied, and no
    tested backend supplies a change token strong enough to prove that. Rather
    than risk deleting a file that changed mid-move, `mv` refuses — before
    touching anything.

    Do it explicitly instead: `cp` then `rm`, so the delete is your decision.

## Namespace

### `mkdir`

```bash
myapp fs mkdir data:/newdir
myapp fs mkdir -p data:/a/b/c        # create parents
```

Silent on success.

### `rmdir`

```bash
myapp fs rmdir data:/emptydir
```

Empty directories only. `-p` is unsupported.

### `unlink`

```bash
myapp fs unlink data:/a.csv
```

Exactly one file. A directory operand is an error.

### `rm`

```bash
myapp fs rm data:/a.csv
myapp fs rm -f data:/maybe-missing.csv    # ignore missing
myapp fs rm -v data:/a.csv                # print what was removed
myapp fs rm -d data:/emptydir             # also remove empty directories
myapp fs rm -R data:/project              # recursive, if enabled
```

`-d` cannot be combined with other options; `-f` cannot be combined with `-v`.

!!! danger "`rm -R` is off unless the host enabled it"

    It requires the `recursion.remove` capability. Removal is **sequential and
    non-atomic** — a failure partway through leaves earlier removals done and
    the rest present or uncertain. There is no prompt, undo, or trash.

    Root paths and any path containing `.` or `..` are rejected outright.

## Path strings

### `basename`, `dirname` {#basename-dirname}

```bash
myapp fs basename /a/b/c.txt          # c.txt
myapp fs basename /a/b/c.txt .txt     # c
myapp fs dirname /a/b/c.txt           # /a/b
```

Pure string operations — no filesystem is contacted and no source is required.
`data:/x/y` is treated as ordinary text.

## When something fails

Failures print one line per operand on stderr:

```text
ls: data:/missing: not found
cp: data:/src: permission denied
rm: data:/f: uncertain mutation state
```

`uncertain` means the operation was *sent* but its outcome could not be
confirmed — it may have applied. Check the actual state before retrying.

Exit statuses are documented in
[Integration](integration.md#exit-statuses). The short version: `2` means
nothing was touched, `1` means something went wrong, `141` means a pipe closed.
