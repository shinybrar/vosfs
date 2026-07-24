# fsspec-cli

`fsspec-cli` turns filesystems you already use in Python into shell-shaped
commands inside **your own** command-line tool.

If you have an [fsspec](https://filesystem-spec.readthedocs.io/) filesystem —
`vosfs`, S3, GCS, local disk, or anything else — `fsspec-cli` gives you `ls`,
`cp`, `rm`, `du`, `find`, `cat`, and more against it, without writing any of
them yourself.

!!! info "It is a library, not a program"

    `fsspec-cli` installs no `fsspec-cli` executable. It hands your application
    a [Typer](https://typer.tiangolo.com/) app that you mount into your own CLI.
    You decide which filesystems are reachable and what they are called.

## Install

```bash
uv add "git+https://github.com/shinybrar/vosfs@main#subdirectory=src/fsspec-cli"
```

## The whole idea in one example

```python
from contextlib import asynccontextmanager

import fsspec
import typer
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec_cli import App


@asynccontextmanager
async def data_source():
    """Yield one async-capable filesystem for a single command invocation."""
    yield AsyncFileSystemWrapper(fsspec.filesystem("memory"))


app = typer.Typer()
app.add_typer(App({"data": data_source}).typer_app, name="fs")

if __name__ == "__main__":
    app()
```

That is a complete CLI. Every path is spelled `name:/path`, where `name` is a
key from the mapping you passed:

```bash
python app.py fs ls data:/
python app.py fs cp data:/report.csv data:/backup/report.csv
python app.py fs du -sh data:/project
```

The `name:` prefix is not decoration — it is how one command reaches two
different filesystems at once:

```bash
python app.py fs cp local:/results.csv archive:/2026/results.csv
```

## Where to go next

<div class="grid cards" markdown>

-   :material-power-plug:{ .lg .middle } __[Integration](integration.md)__

    ---

    Sources, lifecycle, capabilities, extensions, and exit statuses — what you
    need to embed this correctly.

-   :material-console:{ .lg .middle } __[Command reference](commands.md)__

    ---

    Every command, its options, and what it actually does against a remote
    backend.

-   :material-api:{ .lg .middle } __[API reference](api-reference.md)__

    ---

    `App`, `AppCapabilities`, `CommandContext`, and the rest of the public
    surface.

</div>

## What it does not claim

`fsspec-cli` is not POSIX, GNU, or BSD compatible, and does not work with every
fsspec backend. It provides a shell-compatible *experience*: it renders what a
backend can actually supply, in the shape a shell user expects, and omits the
rest rather than inventing values.

Supported host platforms are Linux and macOS. The commands and source forms
with test evidence behind them are listed in the
[tested command matrix](https://github.com/shinybrar/vosfs/blob/main/docs/design/fsspec-cli/matrix.md).
