# Contributing to vosfs

This guide defines the contribution workflow for humans and automated agents.
Agents must also follow `AGENTS.md`; repository configuration and required CI
checks are the executable enforcement of this policy. If they disagree, fix
the policy and configuration together in the same pull request.

## Ground rules

- Start from a GitHub issue or sub-issue and keep the change within its scope.
- Work on a branch. Do not push changes directly to `main`.
- External contributors should fork the repository, push their branch to the
  fork, and open a pull request against this repository. Maintainers and agents
  may branch in a canonical clone.
- Keep each pull request focused and link it to its issue.
- Never bypass hooks with `--no-verify`.
- Use `uv` for Python versions, environments, dependencies, and project
  commands. Do not maintain a parallel `pip`, Conda, or requirements-file
  workflow.

## Set up the repository

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), clone the
repository, and run:

```bash
uv sync --locked
uv run pre-commit install --install-hooks \
  --hook-type pre-commit \
  --hook-type commit-msg
```

The `dev` dependency group must contain every contributor tool used by the
hooks or CI. The hook configuration must cover Ruff formatting and linting,
ty type checks, Commitizen message validation, general file-safety checks, and
Markdown checks.

## Make a change

- Support the Python versions declared by `project.requires-python`.
- Keep package code under `src/vosfs/` and add type annotations to public APIs.
- Use Ruff as the only Python formatter and linter, and ty as the type checker.
- Add or update pytest tests for observable behavior. Unit tests must be
  deterministic and offline; mark integration tests that require a service.
- Maintain at least 90% overall branch coverage across `src/vosfs`.
- Update user-facing Markdown in the same pull request as behavior changes.
  Do not document commands or APIs that do not exist.
- Do not edit `uv.lock` by hand. Use `uv add`, `uv add --dev`, `uv remove`, or
  `uv lock`, and commit `pyproject.toml` and `uv.lock` together.

All Markdown files must pass the configured Markdown lint, trailing-whitespace,
and end-of-file checks. Documentation under `docs/` must also pass a strict
Zensical build. Do not commit generated site output.

## Validate the change

Run focused tests while working. Before opening or updating a pull request, run
the complete local gate:

```bash
uv lock --check
uv run pre-commit run --all-files
uv run pytest
uv run zensical build --strict
```

If a hook changes files, review the changes, stage them, and run the gate again.
The pull request must pass the same required CI checks before merge.

Pull-request CI validates code, tests, Markdown, and the strict Zensical build.
After merge, a separate deployment workflow publishes the validated docs
artifact to GitHub Pages. Publication supplements PR validation; it never
replaces or weakens it.

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
it was validated. Link the issue with `Closes #<number>` or the equivalent
sub-issue reference. Include documentation and lockfile changes when required,
then wait for required CI and review before merge.
