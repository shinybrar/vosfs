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

## Option and operand preflight

The existing plain-`ls` preflight contract remains authoritative. In
particular:

- `-l`, `-ll`, and every grouped short-option token containing `l`, such as
  `-Al` or `-lA`, report the complete token as unsupported;
- `--long` is an unsupported GNU extension;
- unsupported-option preflight occurs before source construction, source entry,
  yielded-filesystem validation, backend calls, or command output; and
- after `--`, `-l` is an operand and follows the existing mapped-filesystem
  operand grammar instead of option handling.

Typer's framework-owned `--help` short circuit remains exempt from command
compatibility behavior.

## Observable rejection contract

For any single- or multiple-operand invocation containing unsupported `-l`, the
command:

1. writes no stdout;
2. emits exactly one escaped diagnostic for the first preflight error;
3. does not call or enter any async filesystem source;
4. performs no filesystem operation; and
5. exits `2`.

Examples:

```text
$ ls -l memory:/docs
ls: -l: unsupported option
```

```text
$ ls -Al local:/tmp vos:/science
ls: -Al: unsupported option
```

```text
$ ls -- -l
ls: -l: invalid mapped filesystem operand
```

The command never falls back to plain output, continues per operand, prints a
header or `total` line, or probes whether one selected filesystem could produce
an approximation. This is source-independent command-interface rejection, not
a runtime backend incompatibility.

## Initial compatibility result

| Filesystem form | `ls -l` result | Reason |
| --- | --- | --- |
| Adapted async Local | Unsupported | Reduced base-stat rows still lack allocation totals, authoritative alternate-access state, correct link rows, and device information. |
| Adapted async Memory | Unsupported | Mode, link count, owner, group, directory modification time, and allocation totals are absent. |
| Native async `vosfs` | Unsupported | Mode, link count, owning group, allocation totals, and unconditional size and modification-time facts are absent. |

The tested command matrix owns exact status vocabulary, backend versions, and
evidence gates. Because rejection completes before source entry, no live
long-listing gate is required.

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
