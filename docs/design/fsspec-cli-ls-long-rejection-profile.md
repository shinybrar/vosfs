# `fsspec-cli` `ls -l` strict rejection profile

<!-- pyml disable line-length -->

Status: **Locked strict rejection**

Question: [Define `ls -l` profiles and incompatibility behavior](https://github.com/shinybrar/vosfs/issues/82)

Client baseline: **fsspec 2026.6.0**

Evidence: [Honest long-listing viability across Local, Memory, and `vosfs`](../research/fsspec-cli-ls-long-viability.md)

## Decision

V1 defines no long-listing profile. `-l` remains an unsupported option because
no tested filesystem can provide complete POSIX Issue 8 long-listing facts
through the pinned fsspec interface. A reduced Local row or sparse row with
unknown-value placeholders would assign non-POSIX semantics to a POSIX option.

The supported command interface remains:

```text
ls [-A] [--] name:/path...
```

This decision adds no long-listing renderer, metadata-normalization interface,
backend capability registry, backend-type branch, or new adapter at the
`App(sources).typer_app` seam.

## Required future admission evidence

Adding a POSIX `-l` profile later requires authoritative values with complete
semantics for every selected entry:

1. entry type, all nine permission positions, and known alternate-access state;
2. hard-link count;
3. owner and owning group, with numeric fallback when names are unavailable;
4. logical size or appropriate character/block device information;
5. last data-modification time;
6. displayed pathname;
7. correct no-`-L` symbolic-link metadata, stored-target size, and target form;
   and
8. authoritative allocated space and unit for every displayed directory entry,
   sufficient to calculate the required 512-byte-unit `total N` line.

Missing facts cannot be replaced with logical byte totals, creation time,
zeroes, question marks, dashes, empty strings, inferred identities, or omitted
columns. Support must be established through one source-independent consumed
shape and real version-tested calls, not concrete filesystem branches.

## Locked `-l` rejection delta

The plain command's [option preflight](fsspec-cli-plain-ls-command-profile.md#21-option-and-operand-preflight)
and [exit-status](fsspec-cli-plain-ls-command-profile.md#7-exit-status)
contracts apply unchanged:

- `-l`, `-ll`, grouped short-option tokens containing `l`, and `--long` report
  the complete token as unsupported;
- rejection emits one diagnostic, writes no stdout, enters no async filesystem
  source, performs no filesystem operation, and exits `2`; and
- after `--`, `-l` is an operand and follows mapped-filesystem operand grammar.

There is no successful output grammar, fallback to plain output, per-operand
continuation, or backend capability probe. Adapted async Local, adapted async
Memory, and native async `vosfs` are all unsupported at the pinned baseline;
the [viability evidence](../research/fsspec-cli-ls-long-viability.md) records
why, while the tested command matrix owns exact status and version vocabulary.
No live long-listing gate is required because rejection completes before source
entry.

## Rejected alternatives

- **Local-only reduced `-l`:** useful metadata, but incomplete POSIX semantics
  and a backend-specific compatibility surface.
- **Sparse fixed columns:** `?`, `-`, or blank cells expose uncertainty but
  redefine `-l` as best-effort details.
- **Variable per-backend columns:** output meaning would depend on the selected
  source and become impossible to consume consistently.
- **Silent plain-listing fallback:** hides that the requested option was not
  honored.
- **Named long-profile selectors:** add interface before any tested filesystem
  can satisfy the base POSIX profile.

Sparse authoritative metadata may later justify a deliberately non-POSIX
`stat`-like interface. That is a separate command decision and does not weaken
this rejection profile.

## Implementation handoff

No dedicated long-listing implementation slice is required. The production
plain-`ls` parser tests the locked complete-token diagnostic, zero-source-work
invariant, empty stdout, and exit `2` for `-l`, grouped `l`, and `--long`.
