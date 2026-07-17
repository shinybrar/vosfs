# Task 132 Report

Status: **DONE**

## Summary

Implemented base file-only `rm` for `fsspec-cli` on branch `feat/issue-132`.
Command removes one or more source-reported files through the exact confirmed
`_rm_file` + absence boundary proven by XSI `unlink`, with whole-argv root and
final-dot guards, all-source acquisition before mutation, and sequential
continuation after ordinary operand failures.

## Deliverables

| Artifact | Path |
| --- | --- |
| Implementation | `src/fsspec-cli/src/fsspec_cli/_rm.py` |
| Registration | `src/fsspec-cli/src/fsspec_cli/_app.py` |
| Hermetic tests | `src/fsspec-cli/tests/test_rm.py` |
| Matrix probes | `src/fsspec-cli/tests/test_command_matrix.py`, `test_vosfs_command_matrix.py` |
| Wheel gate | `src/fsspec-cli/tests/_installed_wheel_gate.py` |
| Profile | `docs/design/fsspec-cli-base-rm-command-profile.md` |
| Matrix rows | `docs/design/fsspec-cli-tested-command-matrix.md` |
| README / changelog | `src/fsspec-cli/README.md`, `src/fsspec-cli/CHANGELOG.md` |

## Behavior locked

- Form: `rm [--] name:/file...`; one or more mapped file operands; no options.
- Whole-argv destructive guards reject every root and final `.` / `..` before any
  source factory.
- Reuses `_unlink._confirmed_rm_file` without broadening unlink.
- Missing path is runtime failure; directories fail before `_rm_file`.
- Continue after ordinary independent operand failures; no stdout; no rollback.
- `-f`/`-d`/`-R`/`-r`/`-v`/`-i` and grouped/long forms are source-free
  unsupported options.
- Profile documents `type == "file"` as fsspec's common type shape and that
  implicit permission-based POSIX prompting is unavailable.

## Validation

Full CONTRIBUTING gate passed locally:

```text
uv lock --check
uv run pre-commit run --all-files
uv run pytest                         -> 405 passed, 50 skipped
uv run --package fsspec-cli pytest src/fsspec-cli/tests -> 552 passed, 8 skipped
uv run zensical build --strict --clean
uv build --package vosfs
uv build --package fsspec-cli
```

## Git

- Commit: (filled after commit)
- PR: (filled after PR)

## Out of scope honored

- No `-f`/`-d`/`-R`/`-v`/`-i` profiles.
- No recursive `_rm`, rollback, retries, or concurrency.
- Unlink remains exactly-one-operand.

## Concerns

- Merge gate #108 / `fsspec-cli-v0.1.0` tag requirement unchanged per epic policy.
- Matrix evidence rows currently cite hermetic tests on this change; promote to
  immutable CI evidence IDs after the PR CI run lands.
