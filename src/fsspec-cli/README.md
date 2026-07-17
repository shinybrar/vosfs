# fsspec-cli

`fsspec-cli` is a library-only package for composing POSIX-shaped commands
over host-configured async fsspec filesystems.

Supported host platforms are Linux and macOS. Other platforms are untested and
unsupported.

Hosts embed its Typer application through the sole behavioral seam,
`App(sources).typer_app`. Each configured value is an
`AsyncFilesystemSource`: a callable that returns a fresh async context manager
for one command invocation.

The current command surface covers plain `ls`, source-free `basename string`
with optional `suffix`, source-free `dirname string`, mapped-file `cat`, verified same-source two-operand `cp`, base `mkdir`, parent-creating `mkdir -p`, base `rmdir`, base file-only `rm`, and XSI
`unlink`. Commands share source-free argument preflight and the synchronous
Typer-to-asyncio boundary; `ls`, `cat`, `cp`, `mkdir`, `rmdir`, `rm`, and `unlink` also
use invocation-owned source lifecycle. The `basename` slice accepts one or two
argv tokens, no options, POSIX Issue 8 basename semantics with optional suffix
removal after base extraction, zero source entry, and deterministic stdout with
one trailing newline. The `dirname` slice mirrors that source-free contract with
Issue 8 dirname semantics locked independently. Every valid `ls` operand awaits `_info`; directories then await
`_ls(path, detail=False)` and strictly validate, filter, and locale-sort
immediate child names. Mapped-file `cat` awaits `_info`, requires fsspec
`type == "file"`, stages each object through `_get_file` into one secure
temporary, and forwards exact binary chunks to stdout with no text conversion.
Operand-free `cat` and each `-` operand read the same binary stdin stream at
that argv position; mapped sources still acquire before any stdin byte when
files are present. `-u` remains source-free unsupported. Base `mkdir` awaits
`_mkdir(path, create_parents=False)` and post-verifies `_info(path)` requires
`type == "directory"`. Parent-creating `mkdir -p` awaits
`_makedirs(path, exist_ok=True)` and post-verifies the final path the same way,
delegating every missing ancestor to the backend composite rather than splitting
parents in CLI code. Successful `mkdir` invocations emit no stdout, continue
after ordinary per-operand failure, and disclose that passing rows claim only
source-default creation semantics, not POSIX mode or umask behavior. Base
`rmdir` removes one or more empty directories through `_info`, exact `_rmdir`,
and a distinguishable post-removal absence proof. It rejects configured source
roots and final dot components before source entry, emits no stdout, and
continues after ordinary operand failure without claiming rollback. Verified same-source `cp` awaits `_info`, resolves directory destinations,
rejects same-path before mutation, awaits `_cp_file` once, and byte-verifies the
destination through bounded disk staging. It never deletes the source; a passing
row proves target resolution, replacement, bytes, diagnostics, cleanup, and
partial state only — not POSIX mode, ownership, link identity, or timestamps.
Base file-only `rm` removes one or more source-reported files through the same confirmed `_rm_file` and absence boundary as XSI `unlink`, with whole-argv root and final-dot guards, all-source acquisition before mutation, and sequential continuation after ordinary operand failure. Exact `rm -f` accepts repeated or grouped force tokens before operands, succeeds source-free with zero operands, and treats only pre-mutation `FileNotFoundError` as a silent no-op; it never aliases recursive `_rm` or suppresses other failures. Base `rm` without `-f` rejects every option, including `-d`/`-R`/`-v`/`-i`. `type == "file"` is only fsspec's common type shape; implicit permission-based POSIX prompting is unavailable. XSI `unlink`
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
