# Changelog

All notable changes to `fsspec-cli` are documented in this file.

## 0.1.0

- Establish the independent package and workspace boundary.
- Add the embedded Typer boundary and source-free `ls` preflight contract.
- Add invocation-owned async source lifecycle and the one-file `ls` tracer.
- Add strict async directory listing, hidden-name selection, and runtime categories.
- Add deterministic multi-operand rendering, failure continuation, and output-failure handling.
- Add hermetic matrix probes for adapted Local, adapted Memory, native `vosfs`,
  and strict `ls -l` rejection while complete compatibility evidence remains
  pending.
- Require every supported CI leg to test the built wheel outside the workspace,
  with `vosfs` installed only as a separate integration wheel.
- Add a sanitized live OpenCADC plain-`ls` observation harness with truthful
  `pass`, `fail`, and `unverified` classifications.
