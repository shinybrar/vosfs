# fsspec-cli

[![CI](https://github.com/shinybrar/vosfs/actions/workflows/ci.yml/badge.svg)](https://github.com/shinybrar/vosfs/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue)](LICENSE)

`fsspec-cli` is a **library-only** package that turns host-configured async
[`fsspec`](https://github.com/fsspec/filesystem_spec) filesystems into
POSIX-shaped [Typer](https://typer.tiangolo.com/) commands you embed in your own
CLI. It installs no executable and no module entry point.

## Install

```bash
uv add "git+https://github.com/shinybrar/vosfs@main#subdirectory=src/fsspec-cli"
```

## Quickstart

```python
from contextlib import asynccontextmanager

import fsspec
import typer
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec_cli import App


@asynccontextmanager
async def data_source():
    # One fresh async-capable filesystem per command invocation.
    yield AsyncFileSystemWrapper(fsspec.filesystem("memory"))


app = typer.Typer()
app.add_typer(App({"data": data_source}).typer_app, name="fs")

if __name__ == "__main__":
    app()
```

Operands are spelled `name:/path`, naming one configured source:

```bash
python app.py fs ls data:/
python app.py fs cp local:/results.csv archive:/2026/results.csv
```

`memory` keeps the example runnable with no setup; a real host maps the
filesystems it serves. See the
[Overview](https://shinybrar.github.io/vosfs/cli/) for the same app wired to
local disk plus a remote VOSpace archive.

## Commands

`ls` · `du` · `find` · `tree` · `size` · `test` · `info` · `stat` ·
`head` · `tail` · `cat` · `cp` · `mv` · `mkdir` · `rmdir` · `unlink` · `rm`.

Recursive `cp -R` is on by default; recursive `rm -R` is **off** by default and
must be enabled explicitly:

```python
App({"data": data_source}, capabilities={"recursion": {"remove": True}})
```

## Public API

`App`, `AppCapabilities`, `RecursionCapabilities`, `AsyncFilesystemSource`,
`CommandCallback`, and `CommandContext`.

## Documentation

| For | Read |
| --- | --- |
| Embedding it: sources, lifecycle, capabilities, exit statuses | [Integration guide](https://shinybrar.github.io/vosfs/cli/integration/) |
| What each command does | [Command reference](https://shinybrar.github.io/vosfs/cli/commands/) |
| The public API | [API reference](https://shinybrar.github.io/vosfs/cli/api-reference/) |
| The normative contract and its rationale | [`docs/design/fsspec-cli/`](../../docs/design/fsspec-cli/) |
| Architecture decisions | [`docs/adr/`](../../docs/adr/) |

## Scope

`fsspec-cli` provides a shell-compatible *experience*: it renders what a backend
can actually supply, in the shape a shell user expects, and omits the rest
rather than inventing values. It does **not** claim POSIX, GNU, BSD/macOS, or
all-fsspec compatibility. Supported host platforms are Linux and macOS.

## License

`fsspec-cli` is distributed under the terms of the
[BSD 3-Clause License](LICENSE) (BSD-3-Clause).
