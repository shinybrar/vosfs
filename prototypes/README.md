# Plain `ls` interoperability prototype

This directory is throwaway evidence for
[issue #80](https://github.com/shinybrar/vosfs/issues/80). It is not the
production `fsspec-cli` package and defines no installable shell command.

Run the seeded Memory filesystem demo from the repository root:

```console
uv run --locked --with typer==0.27.0 \
  python prototypes/fsspec_cli_plain_ls_demo.py ls memory:/docs
```

Expected output:

```text
guide.md
notes.txt
```

Run all executable prototype evidence:

```console
uv run --locked --with typer==0.27.0 \
  python -m pytest --no-cov -q tests/test_fsspec_cli_plain_ls_prototype.py
```

The prototype and its tests are deleted after the human interoperability
verdict is captured in the durable research report.
