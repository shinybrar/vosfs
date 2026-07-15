# Portable fsspec capability floor for plain `ls`

<!-- pyml disable line-length -->

Researched: 2026-07-15

Question: [shinybrar/vosfs#76](https://github.com/shinybrar/vosfs/issues/76)

Client contract: fsspec 2026.6.0

Status: **Decision evidence.** This document fixes the portable backend floor
needed by the plain-`ls` command compatibility profile. Issue #79 owns the
observable command semantics, and issue #80 owns the executable backend gate.

## Answer

A generic plain `ls` is viable without branching on filesystem type. Its
portable floor is two public, synchronous fsspec operations:

1. `info` classifies the operand at `path`.
2. `ls` with `detail=False` lists `path` only when the returned type is
   `"directory"`.

For a non-directory operand, the command uses the `name` returned by `info`.
This avoids relying on `ls(file)` behavior, which works in the initial tested
command matrix but is not promised by the abstract fsspec `ls` contract.

The command must pass `detail=False` explicitly. `LocalFileSystem` defaults it
to false, while `MemoryFileSystem`, `AbstractFileSystem`, and
`VOSpaceFileSystem` default it to true. The command owns output ordering and
presentation; backend order and optional metadata are not portable.

## Required operation contract

Given a mapped path whose filesystem portion has already been resolved to
`path`, the backend interaction is equivalent to:

```python
entry = filesystem.info(path)
if entry["type"] == "directory":
    names = filesystem.ls(path, detail=False)
else:
    names = [entry["name"]]
```

Both calls must complete synchronously. A coroutine result or an
`asynchronous=True` filesystem instance is outside the initial profile. In
particular, the normal `VOSpaceFileSystem` constructor provides fsspec's
synchronous mirror, while an asynchronous instance requires `_info` and `_ls`
to be awaited.

No other filesystem operation belongs in the floor:

- Do not preflight with `exists`, `isdir`, or `isfile`. Their fsspec defaults
  derive from `info`, add no capability, and can collapse backend failures to
  `False`.
- Do not call private normalization or coroutine hooks such as
  `_strip_protocol`, `_info`, or `_ls`.
- Do not fall back to `walk`, `find`, globbing, or recursive traversal.
- Do not retry with a backend-specific signature or inspect the backend class,
  protocol, or module.

The extra `info` call is intentional. A names-only `ls(path, detail=False)` is
the cheapest directory-listing primitive and succeeds for file operands on all
three initial backends, but the abstract contract only says that `ls` lists the
objects *at* a path. POSIX-shaped operand handling must distinguish an operand
that is itself a file from a directory whose contents should be listed. The
documented `info` contract supplies that distinction without type branching.

## Required result contract

fsspec 2026.6.0 defines detailed entries with `name`, `size`, and `type`:

| Field | fsspec contract | Plain-`ls` use |
| --- | --- | --- |
| `name` | Full filesystem path without protocol. | Required as a string. Treat it as an opaque backend path until the command's presentation step. |
| `type` | `"file"`, `"directory"`, or another backend type. | Required as a string. Only exact `"directory"` triggers a child listing; every other value denotes the operand itself. |
| `size` | Byte size, or `None` when unknown. | Guaranteed by compliant detailed results but deliberately unused by plain `ls`. Directory sizes are not comparable across backends. |

`ls(path, detail=False)` returns a list of full, protocol-free path strings.
The plain profile needs no per-child metadata. It validates that the result is
a list of strings before presentation. An empty directory is the valid empty
list.

All additional fields are backend extensions. Local metadata includes mode,
UID, GID, and timestamps; Memory metadata does not; VOSpace metadata may add a
URI, modification date, checksum, content type, properties, and link details.
Plain `ls` ignores all of them. Their presence must not change output.

Paths also remain backend-owned. Local paths become absolute POSIX-style
paths, Memory uses an empty-string root with leading slashes elsewhere, and
VOSpace normalizes accepted paths to a leading slash. The command passes the
resolver's filesystem path unchanged and does not substitute `"."` for an
empty root.

## Capability detection

`AbstractFileSystem` has no portable feature registry for commands. Static
checks are insufficient: every subclass has a callable `ls` attribute because
the base method exists, but that method raises `NotImplementedError` by
default. The real operation is therefore the capability check.

For a known backend/version pair, the tested command matrix records prior
proof. At runtime:

1. invoke `info` and, for a directory, `ls` using the public signatures above;
2. validate only the result shape consumed by this profile;
3. classify `NotImplementedError` from either operation as the profile being
   unavailable for that filesystem;
4. classify a malformed result as an incompatible implementation, never as
   permission denial or a missing path; and
5. never retry through backend-specific behavior or fabricate a result.

This is behavioral capability detection, not a separate probe. A probe would
duplicate I/O and could race with the real listing.

## Error floor

The abstract `ls` documentation does not standardize exception types. The
initial backends do agree that a missing operand raises `FileNotFoundError`,
and Local and VOSpace expose `PermissionError` for access denial. They do not
agree on every invalid-path case: a descendant below a file can be
`NotADirectoryError` locally but `FileNotFoundError` in Memory and VOSpace.

The portable command layer may therefore recognize standard exceptions when a
backend supplies them, but it must not infer meaning from an errno or message:

| Backend result | Portable classification |
| --- | --- |
| `NotImplementedError` | Command compatibility profile unavailable for this filesystem. |
| `FileNotFoundError` | Operand not found. |
| `PermissionError` | Access denied. |
| `NotADirectoryError` | Invalid path through a non-directory, when distinguished by the backend. |
| Other `OSError`, timeout, connection, parse, or backend exception | Backend operation failed; preserve the cause for diagnostics. |
| Wrong return shape | Backend does not satisfy this profile's consumed fsspec contract. |

Exact diagnostics, continuation across multiple operands, stdout buffering,
and exit status remain command-profile decisions for issue #79.

## Initial backend evidence

Hermetic probes used Python 3.13.5 and fsspec 2026.6.0. Local and Memory used
temporary in-process data. VOSpace used the repository's `respx`/
`MockTransport` simulator, whose unmatched requests fail rather than reaching
the network.

| Behavior | Local | Memory | VOSpace |
| --- | --- | --- | --- |
| `info` identifies file/directory and supplies `name`, `type`, `size` | Yes | Yes | Yes |
| `ls(directory, detail=False)` returns immediate full paths | Yes | Yes | Yes |
| `ls(file, detail=False)` returns the file as a singleton | Yes | Yes | Yes |
| Empty directory returns `[]` | Yes | Yes | Yes |
| Missing operand raises `FileNotFoundError` | Yes | Yes | Yes |
| Backend listing order is a command guarantee | No | No | No |
| Default instance offers synchronous `info`/`ls` | Yes | Yes | Yes |

The ordering probe deliberately inserted unsorted names. Local preserved its
`os.scandir` order, Memory sorted `detail=False` but preserved insertion order
for `detail=True`, and VOSpace preserved the server document order. The command
must sort after collecting results if its command semantics require sorting.

Directory size was nonzero and OS-dependent locally but zero in Memory and
VOSpace. Memory also represented `created` differently between `info` and
detailed `ls`. These are concrete reasons not to consume optional or
unnecessary metadata in the plain profile.

A VOSpace listing fetches a capabilities document on first I/O, then one XML
node document when uncached. `detail=False` changes only the returned
projection, not the remote work. A cached directory listing performs no HTTP.
No live OpenCADC call was required for this research decision; the narrow live
listing remains part of the executable matrix gate in issue #80.

## Consequences

- Issue #79 can define POSIX Issue 8 presentation and failure behavior over a
  stable `info` plus names-only `ls` seam.
- Issue #80 should test this exact algorithm, including file operands, empty
  directories, missing paths, malformed result doubles, backend ordering, and
  synchronous-instance rejection.
- Detailed metadata is not part of the plain profile. Issue #78 must determine
  the honest field floor for `ls -l` independently.
- Successful results apply only to the recorded backend/version/profile rows.
  They do not turn a filesystem into a universally supported backend.

## Primary sources

- fsspec 2026.6.0 [`AbstractFileSystem.ls`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L326-L365) and [`info`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L682-L714)
- fsspec 2026.6.0 [`LocalFileSystem.ls`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L59-L76) and [`MemoryFileSystem.ls`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L43-L105)
- vosfs [`_info`, `_ls`, and `_fetch_listing`](https://github.com/shinybrar/vosfs/blob/a5d31ee3c47d105ca22c3e51b09d046db207b04d/src/vosfs/filesystem.py#L245-L277) and [`to_info`](https://github.com/shinybrar/vosfs/blob/a5d31ee3c47d105ca22c3e51b09d046db207b04d/src/vosfs/nodes.py#L173-L206)
- vosfs [error mapping](https://github.com/shinybrar/vosfs/blob/a5d31ee3c47d105ca22c3e51b09d046db207b04d/src/vosfs/errors.py#L243-L312), [path normalization](https://github.com/shinybrar/vosfs/blob/a5d31ee3c47d105ca22c3e51b09d046db207b04d/src/vosfs/paths.py#L20-L56), and [hermetic transport boundary](https://github.com/shinybrar/vosfs/blob/a5d31ee3c47d105ca22c3e51b09d046db207b04d/tests/conftest.py#L1-L7)
