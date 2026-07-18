# fsspec-cli

[![CI](https://github.com/shinybrar/vosfs/actions/workflows/ci.yml/badge.svg)](https://github.com/shinybrar/vosfs/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue)](LICENSE)

`fsspec-cli` is a library-only package that turns host-configured async
[`fsspec`](https://github.com/fsspec/filesystem_spec) filesystems into
POSIX-shaped [Typer](https://typer.tiangolo.com/) commands you embed in your own
CLI.

It exposes a supported subset of file utilities plus a separately named reduced
BSD/macOS-shaped `stat`. It does not claim POSIX, GNU, BSD/macOS, or all-fsspec
compatibility. Supported host platforms are Linux and macOS.

## Install

```bash
uv add "git+https://github.com/shinybrar/vosfs@main#subdirectory=src/fsspec-cli"
```

## Quickstart

The sole stable seam is `App(sources).typer_app`. Each source is an
`AsyncFilesystemSource`: a callable returning a fresh async context manager that
yields one `AbstractFileSystem` per command invocation. The host owns source
configuration and cleanup; the library owns the yielded filesystem only for one
invocation.

```python
from contextlib import asynccontextmanager

import fsspec
import typer
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec_cli import App


@asynccontextmanager
async def data_source():
    # Yield one async-capable filesystem for a single command invocation.
    yield AsyncFileSystemWrapper(fsspec.filesystem("memory"))


app = typer.Typer()
app.add_typer(App({"data": data_source}).typer_app, name="fs")

if __name__ == "__main__":
    app()
```

Name a configured source as `name:/path` when running a command:

```bash
python app.py fs ls data:/
```

## Commands

| Command | Summary |
| --- | --- |
| `ls`, `ll` | Names-only `ls`; adaptive long `ls -l` / `-lh`; inherent-long `ll` |
| `du` | Recursive exact-byte usage; `-s` total only, `-h` human-readable |
| `find` | Recursive file paths; `--maxdepth N`, `--type f\|d` |
| `size` | Exact bytes for one or more mapped paths; batched by source |
| `test` | Silent `-e`, `-d`, or `-f` predicate with shell-style status |
| `cat` | Concatenate mapped files (and stdin `-`) to stdout |
| `cp` | Verified same-source, cross-source, and multi-source file copy (no `-R`) |
| `mv` | Same-source file move, single or multi-file into a directory |
| `mkdir` | Create directories; `-p` creates parents |
| `rmdir` | Remove empty directories |
| `rm` | Remove files; `-d` empty dirs, `-f` force, `-v` verbose |
| `unlink` | XSI single-file removal |
| `stat` | Reduced BSD/macOS-shaped file status |
| `basename`, `dirname` | Source-free path-string slicing |

`du` is recursive. On fsspec implementations that inherit the default async
hook, it can traverse the complete subtree and read metadata for every file;
remote sources may therefore make many requests. `-s` changes only the output,
not the traversal cost.

`find` is recursive unless `--maxdepth N` bounds it. It awaits one backend
`_find` operation; inherited implementations may still walk directories and
read metadata internally. `find` does not provide predicates, globbing, or
`-exec`.

Each command locks an observable compatibility profile. The exhaustive
per-command semantics, diagnostics, and tested-source evidence live in the
design docs under [`docs/design/`](../../docs/design/), with the architecture
decisions in [`docs/adr/`](../../docs/adr/). The package has no console entry
point or module executable.

## License

`fsspec-cli` is distributed under the terms of the
[BSD 3-Clause License](LICENSE) (BSD-3-Clause).
