# Task 129 Report

## Status

Complete. `mkdir -p` delegates `_makedirs(path, exist_ok=True)`, post-verifies final
path with `_info`, and keeps base mkdir unchanged without `-p`.

## Implementation

- `_mkdir.py`: parse `-p` only (long options rejected); branch to `_makedirs` vs base `_mkdir`.
- Tests: hermetic unit, matrix (Local/Memory/vosfs), wheel gate includes
  `test_mkdir.py`.
- Docs: locked `fsspec-cli-mkdir-p-command-profile.md`, matrix rows, README,
  CHANGELOG.

## Validation

Full CONTRIBUTING gate passed locally:

- `uv lock --check`
- `uv run pre-commit run --all-files`
- `uv run pytest` (405 passed)
- `uv run --package fsspec-cli pytest src/fsspec-cli/tests` (495 passed)
- `uv run zensical build --strict --clean`
- `uv build --package vosfs`
- `uv build --package fsspec-cli`

## Concerns

- Memory adapted async: existing file under `-p` may pass `_makedirs` then fail
  post-verify (`uncertain state`) rather than `file exists`; matrix accepts both.
- Matrix rows remain `unverified` until CI run on merge commit.

## Review fixes (2026-07-17)

Important review findings addressed:

- Reject `--parents` and other long options source-free; only `-p` admitted.
- Reject `-p` after first operand; no retroactive `create_parents` on earlier paths.
- Direct `-p` lifecycle tests: multi-source ordering, repeated operands,
  cancellation during `_makedirs`, reverse cleanup.
- Mode/umask divergence note added to matrix rows and CHANGELOG.

Validation after fixes:

- `uv run pre-commit run --all-files` passed
- `uv run pytest` (405 passed)
- `uv run --package fsspec-cli pytest src/fsspec-cli/tests` (503 passed)

## Merge gate

Blocked by #108 / `fsspec-cli-v0.1.0` tag per #129 merge gate (start blocker only).
