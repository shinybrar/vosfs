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

Backend-specific commands are opt-in extensions. For example, add `sign` only
when the host wants to expose a filesystem's signed-URL capability:

```python
from fsspec_cli.extensions import sign

signed_app = typer.Typer()
signed_app.add_typer(
    App({"data": data_source}, extensions=[sign]).typer_app,
    name="fs",
)
```

`sign data:/path` calls the selected filesystem's `sign` capability. A source
without that capability exits nonzero with one `unsupported operation`
diagnostic and no traceback. The extension does not infer support from backend
type or protocol.

## Commands

| Command | Summary |
| --- | --- |
| `ls`, `ll` | Names-only `ls`; adaptive long `ls -l` / `-lh`; inherent-long `ll` |
| `du` | Recursive exact-byte usage; `-s` total only, `-h` human-readable |
| `find` | Recursive file paths; `--maxdepth N`, `--type f\|d` |
| `size` | Exact bytes for one or more mapped paths; batched by source |
| `test` | Silent `-e`, `-d`, or `-f` predicate with shell-style status |
| `head`, `tail` | Exact leading or trailing bytes via `-c N` |
| `tree` | Unicode recursive tree; optional `--maxdepth N` |
| `info` | One normalized metadata dictionary plus backend-specific `extra` values |
| `sign` (opt-in) | Backend-signed URL when the selected source implements `sign` |
| `cat` | Concatenate mapped files (and stdin `-`) to stdout |
| `cp` | Metadata-verified file copy; verified two-operand directory copy with `-R` / `-r` |
| `mv` | Metadata-verified same-source file move, single or multi-file into a directory |
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

`tree` renders one buffered Unicode hierarchy from a backend `_walk`. It is
recursive unless `--maxdepth N` bounds it; remote sources may perform one
listing request for every reached directory. One top-level `_walk` invocation
does not mean one remote request.

`cp -R source:/directory destination:/target` and `cp -r` copy one directory
through a bounded 10,000-entry manifest and one-file host-local staging. The
command supports same-source and cross-source routes, preserves empty
directories, rejects links and special entries before mutation, and verifies
the source manifest plus destination metadata before success. It does not
promise a snapshot, transaction, rollback, exact mirror, or POSIX metadata
preservation.

`info [--] name:/path` awaits one backend `_info` call and pretty-prints every
normalized metadata field plus backend-specific values under `extra`. Sparse
fields remain `None`; bytes, datetimes, tuples, and mappings keep their Python
representation instead of being forced through JSON. The existing `stat`
command remains the stricter reduced BSD/macOS-shaped, Local-rich view and is
behaviorally unchanged.

`head -c N` and `tail -c N` make bounded `_cat_file` hook requests, but that
does not promise a ranged physical transfer. Backend implementations may read a
whole object and slice locally; in particular, `vosfs` does so because OpenCADC
Cavern does not support HTTP Range.

Each command locks an observable compatibility profile. The exhaustive
per-command semantics, diagnostics, and tested-source evidence live in the
design docs under [`docs/design/`](../../docs/design/), with the architecture
decisions in [`docs/adr/`](../../docs/adr/). The package has no console entry
point or module executable.

## License

`fsspec-cli` is distributed under the terms of the
[BSD 3-Clause License](LICENSE) (BSD-3-Clause).
