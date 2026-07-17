# Contributing to vosfs

This guide defines the contribution workflow for humans and automated agents.
Agents must also follow `AGENTS.md`; repository configuration and required CI
checks are the executable enforcement of this policy. If they disagree, fix
the policy and configuration together in the same pull request.

## Ground rules

- Work on a branch. Do not push changes directly to `main`.
- External contributors should fork the repository, push their branch to the
  fork, and open a pull request against this repository. Maintainers and agents
  may branch in a canonical clone.
- Keep each pull request focused.
- Never bypass hooks with `--no-verify`.
- Use `uv` for Python versions, environments, dependencies, and project
  commands. Do not maintain a parallel `pip`, Conda, or requirements-file
  workflow.

## Set up the repository

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), clone the
repository, and run:

```bash
uv sync --locked --all-packages
uv run pre-commit install --install-hooks \
  --hook-type pre-commit \
  --hook-type commit-msg
```

The `dev` dependency group must contain every contributor tool used by the
hooks or CI. The hook configuration must cover Ruff formatting and linting,
ty type checks, Commitizen message validation, general file-safety checks, and
Markdown checks.

## Make a change

- Support every actively supported Python version allowed by
  `project.requires-python`. CI tests that full range on Linux and tests the
  newest-minus-two Python release on macOS. Linux and macOS are the supported
  host platforms; other platforms are untested and unsupported. Advance the
  declared minimum and matrix together as Python's five-version support window
  moves.
- Keep the `vosfs` package under `src/vosfs/`. Each independently installable
  workspace member keeps its package under that member's `src/` directory. Add
  type annotations to public APIs.
- Use Ruff as the only Python formatter and linter, and ty as the type checker.
- Add or update pytest tests for observable behavior. Unit tests must be
  deterministic and offline; mark integration tests that require a service.
- Maintain at least 90% overall branch coverage across `src/vosfs`.
- Update user-facing Markdown in the same pull request as behavior changes.
  Do not document commands or APIs that do not exist.
- Do not edit `uv.lock` by hand. Use `uv add`, `uv add --dev`, `uv remove`, or
  `uv lock`, and commit `pyproject.toml` and `uv.lock` together.

All hand-authored Markdown files must pass the configured Markdown lint,
trailing-whitespace, and end-of-file checks. Public documentation under
`docs/user/` must also pass a strict Zensical build. The generated root
`CHANGELOG.md` is excluded from PyMarkdown; Release Please owns its formatting
from Commitizen-compatible Conventional Commit titles. General whitespace and
end-of-file hooks still apply. Do not commit generated site output to source
branches. Only the trusted Pages workflow may commit the complete generated
site to the machine-owned `gh-pages` branch.

## Validate the change

Run focused tests while working. Before opening or updating a pull request, run
the complete local gate:

```bash
uv lock --check
uv run pre-commit run --all-files
uv run pytest
uv run --package fsspec-cli pytest src/fsspec-cli/tests
uv run zensical build --strict --clean
uv build --no-sources --package vosfs
uv build --no-sources --package fsspec-cli
```

If a hook changes files, review the changes, stage them, and run the gate again.
The pull request must pass the same required CI checks before merge.

### Run the live OpenCADC gate

The live suite mutates OpenCADC staging inside a unique temporary namespace and
removes that namespace leaves-first. Set an existing writable home container
and exactly one credential source; never point the test root at a container the
suite owns or at production data.

```bash
export VOSFS_CERT_FILE=/absolute/path/to/cadcproxy.pem
export VOSFS_TEST_ROOT=/home/<cadc-username>
export VOSFS_TEST_ENDPOINT=https://staging.canfar.net/arc
uv run pytest --no-cov -m integration
```

`VOSFS_TEST_ENDPOINT` is optional and defaults to the value above. The test is
skipped unless both the root and a credential are configured. A failed cleanup
is itself a test failure and reports the unique `vosfs-it-*` namespace that may
need manual inspection. `--no-cov` is intentional: the focused live suite does
not execute enough package branches to satisfy the offline suite's 90% global
coverage threshold.

Pull-request CI validates code, tests, Markdown, and the strict Zensical build.
After successful `main` CI, Release Please dispatches the validated commit to
the Pages workflow, which publishes it as `dev`. When Release Please creates a
tag, a separate docs dispatch publishes that exact tag and points `latest` to
it. Publication supplements PR validation; it never replaces or weakens it.

## Commit messages

Every commit must follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```text
<type>(optional-scope): <imperative description>
```

Allowed types are `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, and `revert`. Use `!` and a `BREAKING CHANGE:` footer
for a breaking change.

Humans should use Commitizen's interactive prompt:

```bash
uv run cz commit
```

Agents and other non-interactive automation may create the message directly;
the `commit-msg` hook must still validate it. Keep commits small and logical,
and do not mix unrelated changes.

## Open the pull request

The pull request description must explain what changed, why it changed, and how
it was validated. Include documentation and lockfile changes when required,
then wait for required CI and review before merge.
