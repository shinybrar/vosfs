# fsspec-cli

`fsspec-cli` is a library-only package for composing POSIX-shaped commands
over host-configured async fsspec filesystems.

Hosts embed its Typer application through the sole behavioral seam,
`App(sources).typer_app`. Each configured value is an
`AsyncFilesystemSource`: a callable that returns a fresh async context manager
for one command invocation.

The current `ls` slice implements source-free argument preflight, the
synchronous Typer-to-asyncio boundary, invocation-owned source lifecycle, and
a files-only tracer. A valid file operand awaits `_info` and writes its exact
mapped spelling; directory listing and backend compatibility claims remain
future work. The package has no console entry point or module executable.
