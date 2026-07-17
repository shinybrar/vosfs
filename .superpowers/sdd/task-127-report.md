# Task 127 Report — Binary stdin and `-` sequencing for `cat`

## Status

Complete. Sol Important review findings fixed. PR #162 updated.

## Commits

- `7d25c62` — `feat(fsspec-cli): add binary stdin and dash sequencing for cat`
- (pending) — `test(fsspec-cli): close cat stdin adversarial failure matrix`

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

## Sol review fix (Important)

Added adversarial failure-matrix tests only (production unchanged):

| Gap | Fix |
| --- | --- |
| Multi-source barrier around stdin | `test_cat_all_multi_source_context_entries_complete_before_stdin`; factory/entry failure with forbidden stdin at leading/middle/trailing `-` |
| Descriptor closed/partial pipe per `-` position | Process tests for broken pipe + prefix stdout failure at leading/middle/trailing; child tracking for source exit + TMPDIR sweep |

### Tests added (16)

**`test_cat.py` (10):**

- `test_cat_all_multi_source_context_entries_complete_before_stdin`
- `test_cat_stdin_untouched_when_later_source_factory_fails` ×3
- `test_cat_stdin_untouched_when_later_source_entry_fails` ×3
- `test_cat_stops_on_stdout_failure_during_stdin_at_each_position` ×3

**`test_cat_process.py` (6):**

- `test_public_seam_cat_broken_pipe_during_stdin_at_each_position_is_silent` ×3
- `test_public_seam_cat_prefix_stdout_failure_during_stdin_at_each_position` ×3

## Test summary

| Gate | Result |
| --- | --- |
| `uv run --package fsspec-cli pytest src/fsspec-cli/tests/test_cat*.py` | 85 passed |
| `uv run --package fsspec-cli pytest src/fsspec-cli/tests` | 508 passed, 7 skipped |
| `uv run --all-packages pre-commit` (changed files) | pass |

## Concerns

- Merge gate from #120: production merge still waits on #108 +
  `fsspec-cli-v0.1.0` tag when that gate remains policy.
- Native vosfs mapped-file `cat` remains `unverified` until live OpenCADC
  evidence; stdin/dash hermetic rows are memory/process-proven only.

## Non-goals preserved

No `-u`, named local files, terminal-device policy, retry, tee, progress,
concurrency, Range, or transport replay. Public exports still only `App` and
`AsyncFilesystemSource`.
