# Implementation handoff

<!-- pyml disable line-length -->

Status: **Reusable execution contract.** System-agnostic. Handed to a subagent
together with a **spec**; it defines *how* to take a spec from its first ticket
to a fully merged, review-clean whole. It hard-codes no project, path, or
command — the spec and the project's own contributing guide supply those.

## What you have been given

- A **spec** — the source of truth for *what* to build and the invariants it must
  hold. It wins on every question of behavior.
- One or more **assigned tickets** from that spec's epic.
- **This handoff** — the *how*. It wins on process only.

Read the spec and the ticket first. Do not restate spec detail here.

## Two loops

The **per-ticket loop** stays light so tickets flow; the **heavy multi-reviewer
suite runs once**, as the spec-completion gate, after every sub-ticket is merged.
This split is deliberate — reviewing the finished whole once beats re-reviewing
each ticket N times.

```text
per ticket:   worktree → /implement → ONE deep /code-review → fix (blocking) → gate → merge → next
end of spec:  thermo + ponytail + deslop + code-review + improve-architecture + simplify → fix → repeat until ALL happy
```

## Per-ticket loop

### 1. Worktree (always)

**Always work in a git worktree** off the current default branch — never edit the
default checkout directly, and never reuse a worktree across tickets. One ticket
= one worktree = one branch = one PR. Independent tickets (no open blocker in the
epic's dependency graph) may run in **parallel worktrees**. Remove the worktree
after merge.

### 2. `/implement`

Invoke `/implement` for the ticket. Build to the spec — honor its invariants,
write the tests/profiles it requires, and keep the validation gate (lint, types,
tests, docs build, any live/release gate the spec names) green as you go. Apply
deterministic auto-fixes (formatter, lint `--fix`) inline — they are not review
findings. Stay in scope; an out-of-scope finding is a tracked follow-up ticket.

### 3. One deep `/code-review` (per ticket, exactly one round)

After `/implement`, run **exactly one** round of deep `/code-review`, scoped to
the ticket's diff (`<default-branch>..HEAD`, not the whole repo). **Take and
implement its feedback** — but **block the merge only on correctness, security,
and spec-conformance**. Roll style, simplification, and architecture nits
**forward to the end-of-spec gate**: do not perfect each ticket, and do not loop
`/code-review` per ticket. (For a purely mechanical ticket — a rename, a doc, a
config bump — a green gate is enough; skip the review round.)

### 4. Merge

Validation gate green, CI green, the one review's blocking findings addressed,
hooks never bypassed. Merge the PR; reference the ticket + epic; tick the epic's
checklist item; delete the worktree.

### 5. Continue

Sync the default branch; take the next unblocked ticket. Repeat.

## Spec-completion gate (once, after ALL sub-tickets are merged)

Only when **every** sub-ticket of the spec is merged, run the full suite as the
**last gate** — the single place the heavy, cross-cutting quality reviews live.

Spawn these **in parallel, each its own subagent**, over the spec's whole change:

- `/thermo-nuclear-code-quality-review`
- `/ponytail-review`
- `/deslop`
- `/code-review`
- `/improve-codebase-architecture`
- `/simplify`

Then **iterate until every reviewer is happy**: fix the findings, **re-run only
the reviewers that flagged something** (over the changed files), and repeat until
all pass and the gate is green. Converge — don't re-run the whole suite each
round. (Run whichever reviewers the environment provides; note any unavailable.)

## Keeping it fast

The loop is tuned for throughput; hold these so it does not bog down:

- **Scope every review to the diff**, never the whole repo.
- **Severity-gate per ticket**: block only on correctness/security/spec; defer
  every quality nit to the final gate.
- **Parallelize unblocked tickets** in separate worktrees, driven by the epic's
  dependency graph — don't serialize independent work.
- **Converge the final gate** by re-running only the reviewers that flagged, and
  **cap the rounds** (e.g. 3): remaining nits become follow-up tickets rather
  than an unbounded back-and-forth.
- **Auto-apply mechanical fixes** (format, lint `--fix`, deterministic
  deslop/simplify rewrites) without a deliberation round.
- **A single review-driver** owns the final gate's back-and-forth (collect all
  reviewers' findings, fix, re-run the failed ones) so convergence is one driven
  process, not a manual shuffle.

## Rules of engagement

- **Spec wins on *what*; this handoff wins on *how*.** If they conflict, the spec
  is right about behavior — fix the process, or raise the spec; never silently
  deviate.
- **Always a worktree; one ticket, one branch, one PR** — small and reviewable.
- **Per ticket: exactly one deep `/code-review` round**, blocking only on
  correctness/security/spec.
- **The heavy suite runs once, at the end of the whole spec**, and loops until
  every reviewer passes.
- **Never bypass hooks or gates; never weaken a test to make it pass.**
- **Out-of-scope findings become follow-up tickets**, never silent scope creep.
- **Behavior-preserving refactors must prove it**: the suite is unchanged and
  green before and after.
