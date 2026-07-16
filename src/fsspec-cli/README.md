# fsspec-cli

`fsspec-cli` is a library-only package for composing POSIX-shaped commands
over host-configured async fsspec filesystems.

Hosts embed its Typer application through the sole behavioral seam,
`App(sources).typer_app`. Each configured value is an
`AsyncFilesystemSource`: a callable that returns a fresh async context manager
for one command invocation.

The current `ls` slice implements source-free argument preflight, the
synchronous Typer-to-asyncio boundary, invocation-owned source lifecycle, and
a one-operand file and directory tracer. Every valid operand awaits `_info`;
directories then await `_ls(path, detail=False)` and strictly validate, filter,
and locale-sort immediate child names before writing them. Complete
several-operand rendering and backend compatibility claims remain future work.
The package has no console entry point or module executable.
