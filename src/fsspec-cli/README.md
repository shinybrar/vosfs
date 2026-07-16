# fsspec-cli

`fsspec-cli` is a library-only package for composing POSIX-shaped commands
over host-configured async fsspec filesystems.

Hosts embed its Typer application through the sole behavioral seam,
`App(sources).typer_app`. Each configured value is an
`AsyncFilesystemSource`: a callable that returns a fresh async context manager
for one command invocation.

The current `ls` slice implements only source-free argument preflight and the
synchronous Typer-to-asyncio boundary. It does not yet enter a source, call a
filesystem, or render a successful listing. The package has no console entry
point or module executable.
