# vosfs — implementation handoff

<!-- pyml disable line-length -->

Status: **Handoff.** Execution wrapper around the normative contract. The TRD is
normative; this drives delivery around it.

- Normative contract: [`trd.md`](trd.md)
- Tribal knowledge: [`fsspec-backend-tribal-knowledge.md`](../research/fsspec-backend-tribal-knowledge.md)
- IVOA VOSpace 2.1 fidelity evaluation: [`vosfs-ivoa-2.1-evaluation.md`](../research/vosfs-ivoa-2.1-evaluation.md)
- Domain language: [`CONTEXT.md`](../../CONTEXT.md)
- Epic + tickets: [#205](https://github.com/shinybrar/vosfs/issues/205) → #113, #66, #65, #63, #201, #202, #203

## 1. Goals (north star)

- **Exact OpenCADC VOSpace profile fidelity — not generic IVOA 2.1 conformance.**
  IVOA 2.1 is the reference vocabulary only.
- **Boundary correctness**: paths, node metadata, containment, error types,
  staged-file outcomes, partial completion, and cache state stay truthful across
  success, failure, cancellation, and malformed server responses.
- Prove the published contract with **executable gates** (hermetic + live
  OpenCADC), not a coverage percentage.
- **Deliberately narrow**: unsupported operations (ranges, retries, transactions,
  append, multipart, native jobs) stay rejected until a server-backed contract
  approves them.

## 2. Order of work

**Already merged (`cf1d1ff`):** IVOA fidelity fixes — prefer `#mtime`→`#date`,
complete the fault vocabulary, label `#MD5`/`#contenttype` as OpenCADC
extensions, add server-computed timestamps to the update deny-list.

Correctness & hardening (in scope now), recommended order:

1. **#113 — package-wide hardening tracker.** It carries its own vertical
   child-issue plan (contract truth → destructive/cache correctness → bounded
   I/O & lifecycle → compatibility/release). **Follow its ordering**; each child
   is a tracer-bullet PR with production behavior + an executable gate.
   Highest-value first slice: capability/skip-ledger truth, direct-303/HEAD
   negotiation, destructive containment, public error/partial-completion gates,
   then bounded `_cat_ranges`.
2. **#63 — LinkNode read/move + the F1 §6 reconciliation.** **Maintainer decision
   required**: reword TRD §6 so a LinkNode move materializes bytes (matches the
   tested code) *or* raises `NotImplementedError`. Pick one; update the TRD **and**
   tests in the same PR. True link recreation is #202 (roadmap), not this ticket.
3. **#66 — text-mode staged-write abort.** Prefer fsspec's `AbstractBufferedFile`
   commit/discard model so a text abort issues no PUT.
4. **#65 — wire the private node-update POST primitive**, including the live-gate
   step.

**Roadmap** (each needs a **new published capability contract + OpenCADC server
support** — do not start one without that): #201 native async move, #202
LinkNode creation, #203 `/pkg` + `/async-delete` + public property API.

## 3. The loop (the point of this document)

Run this cycle for **every** ticket. Do not skip a stage.

### 3a. /implement

- `git switch -c fix/<n>-<slug> origin/main`.
- Preserve the module boundaries (config / paths / xmlio / nodes / negotiate /
  transport / staging / errors / filesystem) unless a concrete failing contract
  needs a smaller seam. **No new public class, retry API, metrics API, or
  capability registry; no widening of the OpenCADC profile.**
- Tests assert **observable behavior** — returned value, exception type + fields,
  remote request sequence, confirmed bytes, callbacks, cache visibility,
  temp-file lifetime, partial-completion evidence — through the public
  filesystem / `vos://` entry points against the stateful VOSpace simulator via
  the injected transport. Pure translators (path/xml/node/config/errors) keep
  focused unit tests because malformed untrusted input is their boundary.
- Keep the gate green:

  ```bash
  uv run ruff format src/vosfs
  uv run ruff check src/vosfs
  uv run ty check src/vosfs
  uv run pytest -q
  ```

### 3b. Reviews (run BEFORE opening the PR; fix findings; re-run)

- **`/code-review`** — Standards + Spec against the ticket and the relevant TRD
  section. Mandatory.
- **`/security-review`** — **mandatory** for anything touching credentials,
  redirects, negotiation, transport, or serialization (most vosfs work). Prove:
  no auth on anonymous/pre-authorized endpoints, no cross-origin bearer over
  `http`, redaction holds, no negotiated-URL caching.
- **`/ponytail-review` + `/deslop`** — trim over-engineering/slop, but never at
  the cost of a contract guarantee.
- **Adversarial verification** (multi-agent **`/code-review ultra`** where
  warranted) for destructive containment, cache poisoning, partial completion,
  and cancellation — the failure boundaries where vosfs bugs hide.
- **Reusable fsspec abstract suites**: zero unexplained skips; each allowed skip
  names its Unsupported matrix row.

### 3c. Merge

- Hooks + full gate green (**never `--no-verify`**). Full local gate:

  ```bash
  uv lock --check
  uv run pre-commit run --all-files
  uv run pytest
  uv run --package fsspec-cli pytest src/fsspec-cli/tests
  uv run zensical build --strict --clean
  uv build --no-sources --package vosfs
  ```

- **Live OpenCADC gate is part of release.** A tag MUST NOT proceed without a
  successful exact-commit run. The live suite creates a unique namespace,
  exercises node/byte/copy/move/delete + the node-update primitive + all five
  scientific-stack gates, and cleans up leaves-first in `finally`:

  ```bash
  export VOSFS_CERT_FILE=/absolute/path/to/cadcproxy.pem
  export VOSFS_TEST_ROOT=/home/<cadc-username>
  uv run pytest --no-cov -m integration
  ```

- PR references the ticket and #205; tick the umbrella checkbox.

### 3d. Continue

- Sync off `main`; take the next ticket per #113's internal order, then #63 / #66
  / #65.

## 4. Non-negotiable invariants

- **OpenCADC profile only.** MUST NOT claim IVOA 2.1 conformance.
- Every **Unsupported** call raises (`NotImplementedError` or a precise built-in)
  **before** any remote mutation.
- **No automatic replay** of capabilities/node/negotiation/mutation/byte
  requests. HTTPX transport retries = 0.
- **Whole-object** staged reads/writes; never send `Range`; use
  `Accept-Encoding: identity`.
- Precise exception mapping (TRD §13) plus one `VOSpaceError(OSError)` for the
  rest; error bodies bounded to 8 KiB, redacted, original cause chained.
- Every successful **or uncertain** mutation invalidates target + descendants +
  parents; **no partial/failed listing enters dircache as complete.**
- Serialization = primitive constructor policy only; reconstruction rebuilds live
  state and re-resolves environment credentials; a forked live instance fails
  fast.
- `exists()`/predicates suppress **only** genuine absence — never
  401/403/429/5xx/timeout/parse/integrity/cancellation.

## 5. Definition of done (per ticket)

- [ ] Behavior matches the TRD section; every fsspec skip mapped to an
  Unsupported row.
- [ ] Hermetic tests (simulator via injected transport) cover success + failure +
  cancellation + malformed-response.
- [ ] Security review clean (credential / redirect / transport / serialization
  changes).
- [ ] `/code-review`, `/ponytail-review`, `/deslop` clean.
- [ ] Full gate green; live OpenCADC gate green for release commits.
- [ ] TRD/research docs updated in the **same PR**; #205 checkbox ticked; `main`
  synced.

## 6. Gotchas

- The TRD title still names **v0.3.0** as the *contract* version; the package is
  v0.4.x. The contract governs shipped releases unchanged since v0.3.0 — do not
  "fix" the label to a package version.
- **F1**: the §6 LinkNode-move clause is currently unimplementable (no link
  writer). Reconcile the wording in #63; do not leave the contract asserting
  impossible behavior.
- Do not import object-store patterns (pseudo-directories, multipart, S3 retry
  lists) — vosfs has real ContainerNodes and different semantics.
- Background subagents run in the **main checkout**, not your worktree — do
  load-bearing edits in-tree yourself.
