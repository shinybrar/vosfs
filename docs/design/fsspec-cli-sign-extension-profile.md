# `fsspec-cli` signed-URL extension profile

<!-- pyml disable line-length -->

Status: **Locked backend-specific extension profile**

Question: [Add the backend-specific extension seam](https://github.com/shinybrar/vosfs/issues/191)

Client baseline: **fsspec 2026.6.0**

Normative direction: [shell-experience specification](fsspec-cli-shell-experience-spec.md), especially section 8

## 1. Opt-in boundary

The host opts into this command with the existing embedded-app seam:

```python
from fsspec_cli import App
from fsspec_cli.extensions import sign

app = App(sources, extensions=[sign]).typer_app
```

Without `sign` in `extensions`, the command is absent. `App` registers core
commands first, then registers each selected annotated callback in caller
order. The `sign` callback retrieves the public immutable source snapshot as
`CommandContext` through `typer.Context`. Extensions add commands; they do not
add a runner, source-lifecycle API, backend registry, or runtime matrix. Core
command modules do not import or branch on this extension.

The callback-and-context contract is accepted by
[ADR 0005](../adr/0005-define-typer-owned-commands-and-callback-extensions.md).
The integration point remains `App(...).typer_app`; no second host seam is
added.

## 2. Command form and preflight

The accepted form is:

```text
sign [--] name:/path
```

Exactly one mapped filesystem operand is required. No command option is
supported. Exact `--help` remains framework-owned; `--` ends option parsing.
The shared mapped-operand grammar, diagnostic escaping, and preflight ordering
apply. Missing, extra, malformed, unknown-source, and unsupported-option
failures produce status `2`, empty stdout, one diagnostic, and no source entry.

## 3. Capability call and result

After complete preflight, the command acquires the selected source once and
calls exactly once on the invocation event loop:

```python
filesystem.sign(path)
```

Omitting the `expiration` argument deliberately uses fsspec's version-pinned
default of 100 seconds. There is no expiration option in this profile.

The command feature-detects by making that call and catching
`NotImplementedError`. It does not inspect filesystem type or protocol, probe
the backend, consult a registry or matrix, retry, or fall back. An exact,
non-empty `str` is the only accepted result. The command writes that URL plus
one newline to stdout and exits `0`.

## 4. Failure and lifecycle behavior

`NotImplementedError` produces exactly:

```text
sign: <mapped-operand>: unsupported operation
```

The status is `1`, stdout is empty, and no traceback is rendered. Other backend
exceptions, incompatible results, output failures, active-event-loop refusal,
and source failures use the shared command diagnostics and status rules.

Source acquisition, validation, same-loop cleanup, exit ordering, exception
information, and control-flow precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

## 5. Tested-source matrix

Hermetic tests exercise the production extension through
`App(..., extensions=[sign]).typer_app`. Current branch tests are executable
evidence; immutable evidence remains `unverified` until CI records a commit.

| Source form | Capability observation | Expected command result | Evidence |
| --- | --- | --- | --- |
| Synthetic signing filesystem / native async | `sign` returns a URL | URL plus newline; status `0` | `test_sign_extension.py` |
| Local / adapted async | inherited `NotImplementedError` | One unsupported diagnostic; status `1` | `test_sign_extension_matrix.py` |
| Memory / adapted async | inherited `NotImplementedError` | One unsupported diagnostic; status `1` | `test_sign_extension_matrix.py` |
| vosfs / native async | inherited `NotImplementedError` | One unsupported diagnostic; status `1`; no HTTP probe | `test_sign_extension_matrix.py` |
| s3fs, gcsfs, and other signing backends | not exercised | `unverified` | — |

The matrix is documentation and test evidence only. Production code never
loads it. Evidence remains limited to the named source forms.
