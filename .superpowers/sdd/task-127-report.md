# Task 127 Report — Binary stdin and `-` sequencing for `cat`

## Status

Complete. PR opened against `main`.

## Commits

- `7d25c62` — `feat(fsspec-cli): add binary stdin and dash sequencing for cat`

## PR

[PR #162](https://github.com/shinybrar/vosfs/pull/162)

## What landed

- `_cat.py`: admit operand-free and `-` stdin operands; acquire mapped sources
  before any stdin/file bytes; forward stdin in bounded binary chunks with the
  same stdout failure rules; stdin read failures continue as staging diagnostics
  labeled `-`; `-u` remains source-free unsupported.
- Profile: `docs/design/fsspec-cli-cat-stdin-command-profile.md` (base mapped-file
  profile updated to point here).
- Tests: `test_cat.py` stdin matrix; `test_cat_process.py` real-pipe order and
  repeated-dash EOF; matrix hermetic rows for mixed stdin and `-u` rejection.
- Matrix/README/changelog updated. Wheel gate already runs `test_cat*`.

## Test summary

| Gate | Result |
| --- | --- |
| `uv lock --check` | pass |
| `uv run --all-packages pre-commit run --all-files` | pass |
| `uv run pytest` | 405 passed, 50 skipped |
| `uv run --package fsspec-cli pytest src/fsspec-cli/tests` | 492 passed, 7 skipped |
| `uv run zensical build --strict --clean` | pass |
| `uv build --package vosfs` / `fsspec-cli` | pass |

## Concerns

- Merge gate from #120: production merge still waits on #108 +
  `fsspec-cli-v0.1.0` tag when that gate remains policy.
- Native vosfs mapped-file `cat` remains `unverified` until live OpenCADC
  evidence; stdin/dash hermetic rows are memory/process-proven only.
- One local gate pass hit transient `OSError: out of pty devices` in unrelated
  basename/output TTY process tests; re-run of `fsspec-cli` suite was green.

## Non-goals preserved

No `-u`, named local files, terminal-device policy, retry, tee, progress,
concurrency, Range, or transport replay. Public exports still only `App` and
`AsyncFilesystemSource`.
