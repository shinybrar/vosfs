# Task 123 Report

Status: **DONE**

## Summary

Implemented source-free `basename string` lexical command for `fsspec-cli` on
branch `feat/issue-123`. Command applies POSIX Issue 8 basename algorithm to
one argv token, never acquires a source, and writes exactly one result line.

## Deliverables

| Artifact | Path |
| --- | --- |
| Implementation | `src/fsspec-cli/src/fsspec_cli/_basename.py` |
| Registration | `src/fsspec-cli/src/fsspec_cli/_app.py` |
| Hermetic tests | `src/fsspec-cli/tests/test_basename.py` |
| Process tests | `src/fsspec-cli/tests/test_basename_process.py`, `_basename_process_child.py` |
| Matrix evidence | `src/fsspec-cli/tests/test_command_matrix.py::test_basename_string_is_source_free` |
| Wheel gate | `src/fsspec-cli/tests/_installed_wheel_gate.py` |
| Profile | `docs/design/fsspec-cli-basename-command-profile.md` |
| Matrix row | `docs/design/fsspec-cli-tested-command-matrix.md` |
| README / changelog | `src/fsspec-cli/README.md`, `src/fsspec-cli/CHANGELOG.md` |

## Behavior locked

- Form: `basename string` only; optional suffix rejected as `extra operand`.
- Options: none supported; `--` ends option parsing; framework `--help` preserved.
- Algorithm: Issue 8 with deterministic all-slash result `/` (covers `//`, `///`).
- NUL: usage error `basename: <operand>: invalid operand`.
- Embedded newline: processed as data; subprocess test proves TTY/redirect bytes match.
- Source-looking tokens: lexical only (`memory:/docs/a.txt` -> `a.txt`).
- Diagnostics: empty stdout, one escaped `basename:` line, exit `2` on preflight failure.

## Validation

Full CONTRIBUTING gate passed locally:

```text
uv lock --check
uv run pre-commit run --all-files
uv run pytest                         -> 405 passed, 50 skipped
uv run --package fsspec-cli pytest src/fsspec-cli/tests -> 226 passed, 6 skipped
uv run zensical build --strict --clean
uv build --package vosfs
uv build --package fsspec-cli
```

## Git

- Commit: `afc7a1a` — `feat(fsspec-cli): add source-free basename string command`
- PR: [shinybrar/vosfs#153](https://github.com/shinybrar/vosfs/pull/153)

## Out of scope honored

- No optional suffix profile (#124).
- No public export changes (`App`, `AsyncFilesystemSource` only).
- No filesystem calls or source acquisition on any path.

## Concerns

- Merge gate #108 / `fsspec-cli-v0.1.0` tag requirement unchanged per epic policy.

## Review-fix pass (Important findings)

Status: **DONE**

Fixed all four Important review findings from `task-123-review.md`.

### Changes

1. Exact `--help`: `test_basename_leaves_exact_help_to_the_framework` now asserts full Typer stdout (`_EXACT_BASENAME_HELP`), exit `0`, empty stderr, zero source calls.
2. Negative matrix row: added `command preflight` / `not entered` / `unsupported` basename option/operand rejection row; split `test_basename_option_rejection_is_source_free` from positive `test_basename_string_is_source_free`.
3. Immutable evidence: replaced mutable-test evidence with `H-2026-07-17-29564531624` from CI run [29564531624](https://github.com/shinybrar/vosfs/actions/runs/29564531624) (`headSha` `afc7a1a`), including job IDs, runner 2.335.1, image versions from logs, fsspec-cli 0.1.1, fsspec 2026.6.0, Typer 0.27.0, commit-pinned `uv.lock`/tests. Noted three installed-wheel legs omitted Python patch in logs; reused matching hermetic-leg patch versions.
4. Network blocking: `_block_network` autouse in `test_basename.py`; `_basename_process_child.py` installs `_block_network` before `App` invoke.

### Review-fix validation

```text
uv run --package fsspec-cli pytest \
  src/fsspec-cli/tests/test_basename.py \
  src/fsspec-cli/tests/test_basename_process.py \
  src/fsspec-cli/tests/test_command_matrix.py -q --no-cov
-> 40 passed

uv run --package fsspec-cli pytest src/fsspec-cli/tests -q --no-cov
-> 227 passed, 6 skipped

uv run --all-packages pre-commit run --all-files
-> Passed
```

### Review-fix git

- Commit: `5623f38` — `test(fsspec-cli): lock basename help and matrix evidence`
- Pushed: `feat/issue-123` (`afc7a1a..5623f38`) updates PR #153

## CI fix pass (Rich ANSI help)

Status: **DONE**

### CI problem

CI run [29564828668](https://github.com/shinybrar/vosfs/actions/runs/29564828668) failed installed-wheel gate and matrix legs: Typer/Rich emitted ANSI codes (`^[[1m`, `^[[2m`) on `--help`, so `test_basename_leaves_exact_help_to_the_framework` diffed colored vs plain `_EXACT_BASENAME_HELP`.

### CI fix

`_invoke_basename` passes `env={"NO_COLOR": "1", "TERM": "dumb"}` to `CliRunner().invoke`. Exact help assertion unchanged.

### CI validation

```text
TERM=xterm-256color FORCE_COLOR=1 uv run --package fsspec-cli pytest \
  src/fsspec-cli/tests/test_basename.py \
  src/fsspec-cli/tests/test_distribution.py -q
-> 33 passed, 6 skipped

TERM=xterm-256color FORCE_COLOR=1 uv run python \
  src/fsspec-cli/tests/_installed_wheel_gate.py
-> 44 passed, 1 skipped (core); 6 passed, 1 skipped (vosfs)

uv run pre-commit run --files src/fsspec-cli/tests/test_basename.py
-> Passed
```

### CI git

- Commit: `2ede3bf` — `test(fsspec-cli): disable color for basename help assertions`
- Pushed: `feat/issue-123` updates PR #153

## CI fix pass (Rich box corners)

Status: **DONE**

### Box-corner CI problem

CI run [29565564489](https://github.com/shinybrar/vosfs/actions/runs/29565564489) failed installed-wheel gate on Windows: Rich `--help` used square box corners (`┌┐└┘`) while golden help locked rounded corners (`╭╮╰╯`). `NO_COLOR`/`TERM=dumb` from `2ede3bf` fixed ANSI only.

### Box-corner CI fix

`test_basename_leaves_exact_help_to_the_framework` normalizes square box corners to rounded via `_normalize_box_drawing()` before comparing to `_EXACT_BASENAME_HELP`. Exit `0`, empty stderr, zero source calls unchanged. `NO_COLOR`/`TERM=dumb` kept.

### Box-corner CI validation

```text
uv run --package fsspec-cli pytest \
  src/fsspec-cli/tests/test_basename.py \
  src/fsspec-cli/tests/test_distribution.py -q
-> 33 passed, 6 skipped
```

### Box-corner CI git

- Commit: `32d3ba4` — `test(fsspec-cli): normalize box corners in basename help lock`
- Pushed: `feat/issue-123` updates PR #153
