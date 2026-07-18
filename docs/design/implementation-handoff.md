# Implementation handoff

<!-- pyml disable line-length -->

Status: **Reusable execution contract.** System-agnostic. This document is
handed to a subagent together with a **spec**; it defines *how* to take one
ticket from that spec to a merged change and continue. It hard-codes no project,
path, or command — the spec and the project's own contributing guide supply
those.

## What you have been given

- A **spec** — the source of truth for *what* to build and the invariants it must
  hold. It wins on every question of behavior.
- One **assigned ticket** from that spec's epic.
- **This handoff** — the *how*. It wins on process only.

Read the spec and the ticket first. Do not restate spec detail here; keep it as
the single source.

## The loop

Run this cycle for the assigned ticket, then take the next one.

```text
/implement  →  review (parallel, once, at the end)  →  fix until green  →  merge  →  continue
```

### 1. `/implement`

- Branch off the current default branch. **One ticket = one branch = one PR.**
  Never batch tickets.
- Invoke **`/implement`** for the ticket. Build to the spec — honor its
  invariants, write the tests/profiles it requires, and keep the project's
  validation gate (lint, types, tests, docs build, and any release/live gate the
  spec names) green as you go.
- Stay in scope. An out-of-scope finding becomes a tracked follow-up ticket, not
  a fold-in.

### 2. Review — parallel subagents, once, at the end

When the PR's implementation is complete and the gate is green, spawn these
reviews **in parallel, each as its own subagent**, run **once** over the finished
change (not interleaved per commit):

- `/deslop`
- `/thermo-nuclear-code-quality-review`
- `/improve-codebase-architecture`
- `/ponytail-review`
- `/simplify`

Collect every finding into one list. (Run whichever of these the environment
provides; note any that were unavailable.)

### 3. Fix until green

- Address the findings, then re-run the affected reviews **and** the validation
  gate until the review pass is clean and the gate is green.
- A finding that is genuinely out of scope becomes a follow-up ticket — never a
  silent skip.

### 4. Merge

- Validation gate green, CI green, reviews green, hooks never bypassed.
- Open/merge the PR; reference the ticket and its epic; tick the epic's
  checklist item.

### 5. Continue

- Sync the default branch. Take the next ticket in the epic's order. Repeat the
  loop.

## Rules of engagement

- **Spec wins on *what*; this handoff wins on *how*.** If they seem to conflict,
  the spec is right about behavior — fix the process, not the spec, unless the
  spec is demonstrably wrong (then raise it, don't silently deviate).
- **One ticket, one branch, one PR** — small and reviewable.
- **Reviews run once, at the end, in parallel** — after implementation is done,
  not per commit.
- **Never bypass hooks or gates**, and never weaken a test to make it pass.
- **Out-of-scope findings become follow-up tickets**, never silent scope creep.
- **Behavior-preserving refactors must prove it**: the test suite is unchanged
  and green before and after.
