# Agent skills

## Issue tracker

Issues are tracked in GitHub; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

## Triage labels

Uses `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

## Domain docs

Single-context layout. See `docs/agents/domain.md`.

## Cursor Cloud specific instructions

This repo is a `uv` workspace with two library packages (`vosfs` under `src/vosfs`,
`fsspec-cli` under `src/fsspec-cli`). There are no long-running services, servers,
or databases; "running" the products means exercising the libraries.

- Sync with `uv sync --locked --all-packages`. Plain `uv sync --locked` omits the
  `fsspec-cli` member's deps (e.g. `typer`), which makes the `ty` type-check hook
  fail with unresolved `typer` imports. CI runs hooks via `uv run --all-packages`.
- The full local validation gate (lint/format/type/test/docs/build) is documented
  in `CONTRIBUTING.md`; run those exact commands rather than re-deriving them.
- Offline tests (`uv run pytest`, `uv run --package fsspec-cli pytest
  src/fsspec-cli/tests`) are deterministic and need no network or credentials.
- Live OpenCADC integration tests (`uv run pytest --no-cov -m integration`) are
  deselected by default and require CADC credentials plus network; they are only
  expected to run in the trusted `main`/dispatch CI job.
- `uv run zensical build --strict --clean` builds docs to `site/`; do not commit
  `site/` or `dist/` (both are gitignored).
