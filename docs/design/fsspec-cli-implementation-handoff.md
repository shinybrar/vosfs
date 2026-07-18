# fsspec-cli shell-experience — implementation handoff

<!-- pyml disable line-length -->

Status: **Handoff.** Execution wrapper around the normative spec. Read the spec
first, then use this to drive delivery.

- Normative spec: [`fsspec-cli-shell-experience-spec.md`](fsspec-cli-shell-experience-spec.md)
- Inventory evidence: [`fsspec-cli-extension-roadmap.md`](fsspec-cli-extension-roadmap.md)
- Domain language: [`CONTEXT.md`](../../CONTEXT.md)
- Epic + tickets: [#204](https://github.com/shinybrar/vosfs/issues/204) → #187, #193–#200, #186, #191

## 1. Goals (north star)

- `fsspec-cli` is a shell-compatible **experience**, not strict POSIX. Give users
  what a backend can actually back, in a shell shape, and **omit what it can't —
  never fabricate**.
- One **info-normalization layer** over `fs.info(detail=True)` powers every
  metadata command. Richness is data-driven, not per-backend code.
- **Core is backend-neutral**; backend-specific behavior lives behind the
  extension seam (#191).
- Every command is independently **profiled + hermetically tested across Memory
  (sparse), Local (rich), and vosfs (remote)**.

## 2. Order of work (dependency-ordered — do NOT reorder)

| Phase | Ticket | Note |
| --- | --- | --- |
| 0 | #187 scaffolding consolidation | **Merge first** ([PR #206](https://github.com/shinybrar/vosfs/pull/206)); everything builds on `_command`. Retiring cat `_CatOwnership` is a separate small PR under the same issue. |
| 1 | #193 info-normalization layer | Pure, no I/O. Unblocks all listing/metadata commands. |
| 2 | #194 `ls -l`/`ll` · #195 `du` · #196 `find` · #197 `size`+`test` | Each its own PR. #194 is the layer's first consumer. |
| 3 | #198 `head`+`tail` · #199 `tree` · #200 `info`+`stat` reconcile | |
| 4 | #186 cp/mv verify redesign | Harden existing. |
| ext | #191 backend-specific seam | Only after core commands + the `_command` toolkit are stable. |

**One ticket = one branch = one PR. Never batch.** Stack on an in-review branch
only when truly dependent (e.g. a command on the not-yet-merged #193 layer);
otherwise branch off `main`.

## 3. The loop (the point of this document)

Run this cycle for **every** ticket. Do not skip a stage to "save time" — the
loop is what keeps each merge safe and the epic continuously shippable.

### 3a. /implement

- `git switch -c feat/<n>-<slug> origin/main` (or stack on the dependency branch
  if it is not yet merged).
- **Profile first.** Write/adjust the command compatibility profile in
  `docs/design/`, then hermetic tests, then code. The profile is the contract.
- Reuse the shared `_command` toolkit. Never re-copy scaffolding; a new shared
  need extends `_command`, it does not fork it.
- Honor the invariants (§4).
- Keep the gate green continuously:

  ```bash
  uv run ruff format src/fsspec-cli/src/fsspec_cli
  uv run ruff check src/fsspec-cli/src/fsspec_cli src/fsspec-cli/tests
  uv run --package fsspec-cli ty check
  uv run --package fsspec-cli pytest src/fsspec-cli/tests -q
  ```

### 3b. Reviews (run BEFORE opening the PR; fix findings; re-run until clean)

- **`/code-review`** — the two-axis Standards + Spec review against the ticket
  and its profile. Mandatory, every ticket.
- **`/ponytail-review`** — over-engineering: speculative flexibility, reinvented
  stdlib, a backend branch that leaked into core. Cut it.
- **`/deslop`** — restating comments, ceremonial docstrings, dead defensiveness.
- **Adversarial verification** for the normalization layer and anything parsing
  untrusted paths/args: prove the edge cases with tests (absent size, missing
  mode, epoch/`datetime`/ISO time, link rows, backend `extra` keys), not
  assertions of intent. For #193, #194, and #186, use the multi-agent
  **`/code-review ultra`** for adversarial depth.
- **Abstract/matrix suites** — every skip maps to an explicit unsupported row.

### 3c. Merge

- All hooks green (**never `--no-verify`**), full gate green, every review
  finding resolved.
- PR body: what / why / how-validated; references the ticket and #204.
- Tick the ticket's checkbox in the #204 umbrella.

### 3d. Continue

- **Sync**: after merge, branch the next ticket off the updated `main`.
- Take the next ticket in phase order. Repeat.

## 4. Non-negotiable invariants

- **Never fabricate** a metadata field. Absent → omit or `-`. No fake `0` size,
  no invented mode, no `created`-as-`mtime`.
- **Adaptive columns**: render only what some row supports; drop fully-absent
  columns rather than showing placeholders.
- `-h` = human-readable size; `--help` = help. `-h` stays exit-2 outside the
  size-bearing commands until `ls -l`/`du` land.
- **Core command modules contain no `isinstance(fs, ...)` or
  `fs.protocol == ...` branch.** Backend-specific → extension only.
- **Bare `ls` is untouched** (names only, one call). `ls -l`/`ll` is a distinct
  mode.
- Every command ships its own profile + hermetic tests across Memory/Local/vosfs.
  Docs change in the **same PR** as behavior.
- Diagnostics use the shared control-char escaping; no raw ANSI from untrusted
  input.

## 5. Definition of done (per command ticket)

- [ ] Command profile written/updated in `docs/design/`.
- [ ] Hermetic tests pass across Memory, Local, and vosfs source forms.
- [ ] Normalization edge cases covered (where the command renders metadata).
- [ ] ruff + ty + pytest + abstract suites green; every skip mapped.
- [ ] `/code-review`, `/ponytail-review`, `/deslop` clean.
- [ ] PR merged; #204 checkbox ticked; `main` synced for the next ticket.

## 6. Gotchas

- Background subagents run in the **main checkout**, not your worktree — do
  load-bearing edits in-tree yourself and verify the working directory.
- Tests monkeypatch `_binary_stdout` on the command module — keep it **imported
  into** the module so the name stays patchable.
- `_StatCommand` subclasses `_RawCommand` to keep a custom usage line — the
  pattern for any command needing bespoke `--help`/usage.
- The scaffolding consolidation (#187) is the base for every new command; do not
  start Phase 1+ against `main` until #187 is merged.
