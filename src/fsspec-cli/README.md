# fsspec-cli

`fsspec-cli` is a library-only package for composing POSIX-shaped commands
over host-configured async fsspec filesystems.

Hosts embed its Typer application through the sole behavioral seam,
`App(sources).typer_app`. Each configured value is an
`AsyncFilesystemSource`: a callable that returns a fresh async context manager
for one command invocation.

The current command surface covers plain `ls`, source-free `basename string`
with optional `suffix`, mapped-file `cat`, base `mkdir`, base `rmdir`, and XSI
`unlink`. Commands share source-free argument preflight and the synchronous
Typer-to-asyncio boundary; `ls`, `cat`, `mkdir`, `rmdir`, and `unlink` also
use invocation-owned source lifecycle. The `basename` slice accepts one or two
argv tokens, no options, POSIX Issue 8 basename semantics with optional suffix
removal after base extraction, zero source entry, and deterministic stdout with
one trailing newline. Every valid `ls` operand awaits `_info`; directories then
await `_ls(path, detail=False)` and strictly validate, filter, and locale-sort
immediate child names. Mapped-file `cat` awaits `_info`, requires fsspec
`type == "file"`, stages each object through `_get_file` into one secure
temporary, and forwards exact binary chunks to stdout with no text conversion.
Stdin and `-` remain outside the first `cat` profile. Base `mkdir` awaits
`_mkdir(path, create_parents=False)` and post-verifies `_info(path)` requires
`type == "directory"`. Successful `mkdir` invocations emit no stdout, continue
after ordinary per-operand failure, and disclose that passing rows claim only
source-default creation semantics, not POSIX mode or umask behavior. Base
`rmdir` removes one or more empty directories through `_info`, exact `_rmdir`,
and a distinguishable post-removal absence proof. It rejects configured source
roots and final dot components before source entry, emits no stdout, and
continues after ordinary operand failure without claiming rollback. XSI `unlink`
awaits `_info`, `_rm_file`, and a distinguishable absence proof for exactly one
source-reported file. It rejects root and final dot components before source
entry and never aliases recursive `rm` behavior. Rendering is deterministic,
ordinary operand failures continue with stable diagnostics, and output failures
preserve their accepted-byte boundary. Backend compatibility claims remain
`unverified` until their source-form gates run. The package has no console entry
point or module executable.

Package-owned hermetic probes now exercise adapted Local, adapted Memory, and
native async `vosfs` sources through that same public seam. They block name
resolution and high-level connection attempts, give `vosfs` a strict mocked
transport, and verify lifecycle, awaited calls, raw result shapes, output,
diagnostics, and exit behavior. The canonical row-scoped matrix records current
classifications and immutable evidence. Release-candidate readiness still
requires the isolated-wheel command-matrix gate. Native `vosfs` `cat` remains
`unverified` until the live OpenCADC gate supplements its hermetic evidence;
base `rmdir` does not require live OpenCADC evidence in v1. The live
observation harness captures only classification, package, platform,
source-mode, call-shape, cleanup, commit, and immutable-run metadata; it never
publishes directory entries, file bytes, or credential material.
