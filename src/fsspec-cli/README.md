# fsspec-cli

`fsspec-cli` is a library-only package for composing POSIX-shaped commands
over host-configured async fsspec filesystems.

Hosts embed its Typer application through the sole behavioral seam,
`App(sources).typer_app`. Each configured value is an
`AsyncFilesystemSource`: a callable that returns a fresh async context manager
for one command invocation.

The current `ls` slice implements source-free argument preflight, the
synchronous Typer-to-asyncio boundary, invocation-owned source lifecycle, and
the locked plain-`ls` behavior for one or more mapped operands. Every valid
operand awaits `_info`; directories then await `_ls(path, detail=False)` and
strictly validate, filter, and locale-sort immediate child names. Rendering is
deterministic across files and directory blocks, while ordinary operand
failures continue with stable diagnostics and output failures preserve their
accepted-byte boundary. Backend compatibility claims remain `unverified`
until their source-form gates run. The package has no console entry point or
module executable.
