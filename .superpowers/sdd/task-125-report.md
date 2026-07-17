# Task 125 Report — Source-free `dirname string`

## Status

**Complete.** PR open; merge blocked by #108 gate per brief (not start blocker).

## Deliverables

| Item | Path / reference |
| --- | --- |
| Implementation | `src/fsspec-cli/src/fsspec_cli/_dirname.py` |
| App registration | `src/fsspec-cli/src/fsspec_cli/_app.py` (`_register_source_free_command`) |
| Profile | `docs/design/fsspec-cli-dirname-command-profile.md` |
| Matrix rows | `docs/design/fsspec-cli-tested-command-matrix.md` (source-free + preflight) |
| Hermetic tests | `test_dirname.py`, `test_dirname_process.py`, matrix probes |
| Wheel gate | `_installed_wheel_gate.py` includes `test_dirname.py` |

## Commit

```text
8857f0e feat(fsspec-cli): add source-free dirname string lexical command (#125)
```

## Pull request

<https://github.com/shinybrar/vosfs/pull/160>

## Validation

Full CONTRIBUTING gate passed locally:

- `uv lock --check`
- `uv run pre-commit run --all-files`
- `uv run pytest` — 405 passed, 50 skipped
- `uv run --package fsspec-cli pytest src/fsspec-cli/tests` — 508 passed, 7 skipped
- `uv run zensical build --strict --clean`
- `uv build --package vosfs` / `fsspec-cli`

## Algorithm lock

POSIX Issue 8 dirname with deterministic all-slash → `/` (matches GNU dirname for goldens). No filesystem semantics; embedded newline is data; NUL rejected at preflight.

## Concerns

- Matrix evidence cites “this change” until CI assigns immutable run id (same pattern as recent cat rows).
- `_app.py` gained `_register_source_free_command` to satisfy Ruff C901 after seventh command; basename/dimname share it.
- Merge gate #108 / `fsspec-cli-v0.1.0` tag still open per #120 program.

## Scope notes

Out of scope respected: no `os.path.dirname`, no multi-operand, no GNU zero-delimited modes, no source entry on any path.

---

## Review fix pass (2026-07-17)

**Status:** Important review findings addressed; matrix evidence attached.

**Commits:**

- `8857f0e` — initial dirname implementation
- `f4faba8` — preflight order, algorithm goldens, profile/matrix honesty
- evidence commit `docs(...)` — H-2026-07-17-29586387337 matrix evidence

**Fixes:**

1. Preflight detects second operand before later unsupported-option or NUL checks (`dirname a b -z`, `dirname a 'bad\0name'` → `extra operand`).
2. Direct `_posix_dirname_string` goldens plus App-seam interior repeated-slash vectors (`a//b`, `/a//b`, `a///b/`).
3. Matrix rows cite [H-2026-07-17-29586387337](https://github.com/shinybrar/vosfs/actions/runs/29586387337) after green CI on `f4faba8`.
4. Profile example corrected: `.. -> .`.

**Tests:** 74 dirname-focused cases (54 unit + process/matrix); full CI green on run 29586387337.

**Concerns:** Merge gate #108 / `fsspec-cli-v0.1.0` tag still open per #120 program.
