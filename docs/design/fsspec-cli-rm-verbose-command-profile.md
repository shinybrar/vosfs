# `fsspec-cli` `rm -v` command profile

<!-- pyml disable line-length -->

Status: **Locked command semantics and async execution contract**

Part of [#120](https://github.com/shinybrar/vosfs/issues/120) / [#136](https://github.com/shinybrar/vosfs/issues/136)

Client baseline: **fsspec 2026.6.0**

## 1. Scope

This profile adds confirmed-removal verbose output to base file-only `rm`:

```text
rm -v [--] name:/file...
```

`-v` is required exactly once. Typer accepts the registered short option in a
combined group and before or after operands. Base file-only `rm` without `-v`
remains a separate silent profile and still requires one mapped filesystem
operand.

Repeated `-v`, non-recursive `-f`, and `-d` combinations are semantic usage
errors. Recursive `-f` and `-v` composition belongs to the guarded recursive
profile. Unregistered options and long aliases are Typer usage errors. Every
usage failure completes before source entry; zero operands remain a usage
error.

## 2. Backend operation semantics

This profile reuses base `rm`'s confirmed `_info`, `_rm_file`, and absence
boundary, including file-only type floor, all-source acquisition before
mutation, sequential operand order, continuation after ordinary operand
failure, and partial-state retention without rollback.

## 3. Output and exit status

After each confirmed absence, write the exact original mapped operand spelling
plus one newline to stdout. Never print before mutation, for failed removals,
or for uncertain post-state.

When stdout fails after a confirmed removal, retain that removal, stop further
safe mutation and success output, run cleanup, and exit with status `1`. Broken
pipe preserves accepted bytes and emits no output-fault diagnostic. Other
stdout failures use the stable escaped
`rm: output: output failure (...): ...` diagnostic.

Mixed successes and failures keep argv order for both success lines and
operand diagnostics. Status `0` requires every operand to complete confirmed
removal and cleanup to succeed. Status `1` and `2` preserve base `rm` runtime,
output, cleanup, and preflight rules.

## 4. Evidence

Hermetic tests exercise confirmed one/many removals, missing/directory/other
types, uncertain post-state, mixed sources, earlier output then later failure,
short writes, broken pipe, non-broken output errors, diagnostic failure,
cancellation, cleanup failure, and source-free unsupported combinations,
zero-operand, root/final-dot, and malformed-mapping paths. Adapted async Local
and Memory plus native async `vosfs` run through `App(sources).typer_app`.
Installed-wheel CI runs the same tests outside the workspace.
