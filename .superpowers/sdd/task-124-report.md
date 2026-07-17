# Task 124 Report

Status: **DONE**

## Summary

Extended source-free `basename` with optional second-operand suffix on branch
`feat/issue-124`. Base one-operand behavior unchanged; suffix removal runs after
POSIX Issue 8 extraction per #124.

## Deliverables

| Artifact | Path |
| --- | --- |
| Implementation | `src/fsspec-cli/src/fsspec_cli/_basename.py` |
| Hermetic tests | `src/fsspec-cli/tests/test_basename.py` |
| Process tests | `src/fsspec-cli/tests/test_basename_process.py`, `_basename_process_child.py` |
| Matrix evidence | `test_basename_suffix_is_source_free`, `test_basename_extra_operand_rejection_is_source_free` |
| Suffix profile | `docs/design/fsspec-cli-basename-suffix-command-profile.md` |
| Base profile delta | `docs/design/fsspec-cli-basename-command-profile.md` |
| Matrix rows | `docs/design/fsspec-cli-tested-command-matrix.md` |
| README / changelog | `src/fsspec-cli/README.md`, `src/fsspec-cli/CHANGELOG.md` |

## Behavior locked

- Form: `basename string` unchanged; `basename string suffix` removes matching
  trailing suffix when suffix is not identical to entire extracted basename.
- Empty, absent, or nonmatching suffix leaves extracted basename unchanged.
- Third operand or unsupported option: exit `2`, empty stdout, one diagnostic,
  zero source calls.
- NUL in either operand: `invalid operand`.
- Suffix applied after base extraction (slashes, source-looking prefixes, Unicode).

## Validation

Full CONTRIBUTING gate passed locally:

```text
uv lock --check
uv run pre-commit run --all-files
uv run pytest                         -> 405 passed, 50 skipped
uv run --package fsspec-cli pytest src/fsspec-cli/tests -> 496 passed, 7 skipped
uv run zensical build --strict --clean
uv build --package vosfs
uv build --package fsspec-cli
```

## Out of scope honored

- No GNU multi-string modes, extension lists, pattern syntax, case folding,
  filesystem queries, or dirname changes.
- No public export changes (`App`, `AsyncFilesystemSource` only).

## Concerns

- Merge gate issue #108 and missing `fsspec-cli-v0.1.0` tag still block production
  merge under umbrella issue 120 policy; not a start blocker.
