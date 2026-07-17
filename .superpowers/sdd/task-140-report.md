# Task 140 report

Status: complete.
Verdict: reject same-source `_copy(..., recursive=True)` admission; same/cross-source `cp -R` remain unsupported.
Commit: `b5f18cf docs(fsspec-cli): reject recursive cp composite`.
Tests: TDD help red/green; focused 103 passed; full `fsspec-cli` 831 passed, 8 skipped; hooks and strict docs build passed.
Concerns: research observed macOS/CPython 3.13 only; fsspec composite lacks tree proof, containment/dot consistency, link/special policy, bounded work, cancellation, and residue semantics.
Report: `.superpowers/sdd/task-140-report.md`.

Fix note: pinned Issue #140 fsspec source links to immutable commit
`a2457004d03e0312f715f90f58873de5ab195a37`; matrix evidence now identifies
existing hermetic negative test, with no positive recursive implementation.
