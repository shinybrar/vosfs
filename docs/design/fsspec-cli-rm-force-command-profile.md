# `fsspec-cli` `rm -f` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#133](https://github.com/shinybrar/vosfs/issues/133)

Client baseline: **fsspec 2026.6.0**

## 1. Scope

This profile adds exact force behavior to base file-only `rm`:

```text
rm -f [-f...] [--] [name:/file...]
```

`-f` is required. Repeated and grouped tokens containing only `f` are
idempotent before operands. Zero operands succeed without source entry or
output. Base file-only `rm` without `-f` remains a separate profile and still
requires one mapped filesystem operand.

`--` ends option parsing. `-f` after an operand, `-i`, `-d`, `-R`/`-r`, `-v`,
`-fv`/`-vf`, mixed groups, and long options are unsupported. Unsupported option
tokens fail with status `2` before source entry.

## 2. Backend operation semantics

This profile reuses base `rm`'s confirmed `_info`, `_rm_file`, and absence
boundary. A pre-mutation `FileNotFoundError` is a successful no-op. This
classification uses the exception class only; it never parses messages or
suppresses a generic false result.

All other base failures remain visible: permission, service, containment,
incompatible-result, directory or other type, diagnostic, cleanup, and
uncertain-mutation failures still fail. Ordinary operand processing continues
in original order, so missing operands do not stop later removals.

## 3. Output and exit status

Successful invocations emit no stdout or stderr. Status `0` means all operands
either completed confirmed removal or were pre-mutation missing, and cleanup
succeeded. Status `1` preserves base `rm` runtime and cleanup failure rules.
Status `2` preserves base `rm` preflight failure rules.

## 4. Evidence

Hermetic tests exercise source-free zero-operand and option-rejection paths,
plus adapted async Local and Memory and native async `vosfs` through
`App(sources).typer_app`. Installed-wheel CI runs the same tests outside the
workspace.
