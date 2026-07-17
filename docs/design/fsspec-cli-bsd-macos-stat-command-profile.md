# `fsspec-cli` reduced BSD/macOS `stat` command profile

<!-- pyml disable line-length -->

Status: **Locked reduced compatibility profile (research only; no production command yet)**

Question: [Define a reduced BSD and macOS stat compatibility profile](https://github.com/shinybrar/vosfs/issues/145)

Parent: [Issue #120](https://github.com/shinybrar/vosfs/issues/120)

Implements-next: [Implement the reduced BSD and macOS stat profile](https://github.com/shinybrar/vosfs/issues/146)
(T25 frontier below)

Evidence: [BSD/macOS `stat` viability over fsspec `_info`](../research/fsspec-cli-bsd-macos-stat-viability.md)

Fixture vectors: [stat fixture vectors](../research/fsspec-cli-bsd-macos-stat-fixture-vectors.md)

Client baseline: **fsspec 2026.6.0** at
[`a2457004d03e0312f715f90f58873de5ab195a37`](https://github.com/fsspec/filesystem_spec/tree/a2457004d03e0312f715f90f58873de5ab195a37)

## Non-POSIX boundary

`stat` is **not** a POSIX Issue 8 utility. This profile MUST NEVER be described
as POSIX `stat`, POSIX conformance, full BSD conformance, full macOS
conformance, or GNU `stat` compatibility. It is a **reduced BSD/macOS-shaped
compatibility profile** over authoritative fsspec `_info` fields only.

This profile MUST NOT be cited as evidence for `ls -l` or any other metadata
command. Long listing remains rejected under
[the `ls -l` rejection profile](fsspec-cli-ls-long-rejection-profile.md).

## Post-profile async constraint

Production CLI orchestration and filesystem calls remain async-only. Source
acquisition, cleanup, cancellation, and failure precedence follow
[ADR 0002](../adr/0002-own-async-filesystems-per-invocation.md) and
[ADR 0003](../adr/0003-acquire-referenced-async-filesystem-sources.md).

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are interpreted as described by
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) only when capitalized.

## 1. Scope (T24 argv)

Accepted argv:

```text
stat [--] name:/path...
```

- One or more mapped filesystem operands are REQUIRED.
- Zero options are accepted on the command path. `--` ends option parsing.
- Typer's framework-owned `--help` short circuit is exempt from this
  compatibility profile.

This is deliberately smaller than macOS/BSD `stat(1)`. It is **not** the host
default format. Reference behavior for naming and field semantics is macOS
15.7.7 `/usr/bin/stat` (SHA-256
`57c7e2742cee418bad76bba59ebce7679755a02cffe94f58e4993912d698bebc`) and Apple
`file_cmds` commit
[`659a8a301e2acf0343f8b8673a154a2ca4d07084`](https://github.com/apple-oss-distributions/file_cmds/tree/659a8a301e2acf0343f8b8673a154a2ca4d07084),
with FreeBSD `LS_FORMAT` / `TIME_FORMAT` agreement at path commit
[`1d386b48a555f61cb7325543adbbb5c3f3407a66`](https://github.com/freebsd/freebsd-src/commit/1d386b48a555f61cb7325543adbbb5c3f3407a66).
Exact Apple tag ↔ host build pairing remains **unverified**.

### 1.1 Rejected flags and shapes (source-free)

Every token below MUST reject during command preflight with status `2`, empty
stdout, exactly one `stat: <token>: unsupported option` diagnostic (or the
locked operand diagnostic), zero source factories, and zero filesystem work.

| Rejected surface | Reference meaning on macOS/BSD `stat(1)` | Why rejected here |
| --- | --- | --- |
| Default multi-field host line without this profile's grammar | `DEF_FORMAT` including `%d %i … %Sa %Sc %SB %k %b %#Xf` | Requires device, atime, ctime, birth, blocks, flags, rdev absent from common `_info` |
| `-f` / format language | Custom format strings | Out of scope; fixed grammar only |
| `-l`, `-F` | `LS_FORMAT` / `LSF_FORMAT` (`ls -lT` shape) | Needs honest link/`%Z` device semantics; must not smuggle `ls -l` |
| `-L` | Follow symlinks via `stat(2)` | Local `_info` follow behavior is not a clean link policy |
| `-x` | Linux-style verbose block | Needs fields absent from `_info` |
| `-r`, `-s`, `-n`, `-q`, `-t` | Raw, shell, newline, quiet, timefmt | Not in the reduced surface |
| GNU long options / `--printf` / `--format` | GNU coreutils | Different utility family; not blended |
| Operand-free invocation | Host `stat` may use stdin FD | Mapped operands are mandatory |
| Bare paths / protocol URLs / default source | — | Mapped `name:/path` only |

### 1.2 Operand preflight

Operand grammar matches plain `ls`: `<name>:/<path>` with leading `/` on the
path portion. Diagnostics:

| Condition | Diagnostic |
| --- | --- |
| Zero operands | `stat: missing mapped filesystem operand` |
| Unsupported option token | `stat: <option token>: unsupported option` |
| Malformed operand | `stat: <operand>: invalid mapped filesystem operand` |
| Unknown mapped name | `stat: <operand>: unknown filesystem (known: <name>, ...)` |

## 2. Operation

After preflight, acquire every distinct referenced source once in first-argv
order. For each operand, await exactly `_info(path)` once. No `_ls`, no native
`os.stat` bypass, no backend-type branch, and no second metadata primitive
unless a future profile revises this lock.

## 3. Accepted entry types and mandatory `_info` shape

An operand is renderable only when `_info` returns a mapping that satisfies
**all** of:

1. `type` is exactly `"file"` or `"directory"` (`str`);
2. `islink` is absent or exactly `False` (`bool` if present);
3. `name` is `str`;
4. `size` is `int` and `>= 0` (not `None`);
5. `mode` is `int`;
6. `nlink` is `int` and `>= 1`;
7. `uid` is `int` and `>= 0`;
8. `gid` is `int` and `>= 0`;
9. `mtime` is `int` or `float` (epoch seconds, finite, not NaN).

Any missing key, wrong Python type, `islink is True`, `type` outside
`{file, directory}`, `size is None`, or non-finite `mtime` is an **incompatible
result**. The renderer MUST NOT emit `?`, `0` placeholders, dashes, host
identity guesses, VOSpace properties relabeled as POSIX mode/owner, or silently
omitted columns.

Extra keys (`ino`, `created`, `destination`, `uri`, `properties`, …) MUST be
ignored by the renderer and MUST NOT change the locked grammar.

Links, `other` types, character/block devices, and incomplete Memory/`vosfs`
shapes are incompatible under this profile.

## 4. Deterministic output grammar

One successful operand produces exactly one line:

```text
<mode> <nlink> <owner> <group> <size> "<mtime>" <pathname>\n
```

Field rules (BSD/macOS-shaped, reduced):

| Field | Rule |
| --- | --- |
| `<mode>` | `stat.filemode(mode)` ten-character form (same role as `%Sp`) |
| `<nlink>` | Decimal `nlink` with no padding (same role as `%l`) |
| `<owner>` | `pwd.getpwuid(uid).pw_name` when resolvable; otherwise decimal `uid` string (same role as `%Su`) |
| `<group>` | `grp.getgrgid(gid).gr_name` when resolvable; otherwise decimal `gid` string (same role as `%Sg`) |
| `<size>` | Decimal byte `size` (same role as `%z` for non-devices; devices unsupported) |
| `<mtime>` | Local timezone, fixed C-locale month abbreviations, format `%b %e %H:%M:%S %Y` matching BSD `TIME_FORMAT` `%b %e %T %Y`, always wrapped in double quotes like default-format times |
| `<pathname>` | The operand path portion exactly as spelled after the first `:` (including leading `/`) |

Separators are single ASCII spaces between fields. No column alignment beyond
what `stat.filemode` and the numeric/string conversions naturally produce.
Multiple successful operands emit one line each in argv order.

This grammar is **not** host `DEF_FORMAT` and **not** `LS_FORMAT` (no `%Z`
device form, no `%SY` link arrow, quoted mtime). It is intentionally recognizable
as BSD/macOS field order for mode/links/owner/group/size/time/name while staying
truthful.

### 4.1 Locale, numeric, timezone, units

- Size and link count are decimal bytes and counts; no block units.
- Timestamps use the process local timezone; no UTC conversion unless the host
  TZ is UTC.
- Month abbreviations and numeric fields MUST render under an effective `C`
  locale for time formatting so golden vectors stay deterministic across
  Linux/macOS CI.
- Owner/group name lookup is host presentation for numeric ids already returned
  by `_info`; it is not a remote identity resolver.

## 5. Failures, continuation, status, cleanup

| Class | Behavior |
| --- | --- |
| Source-free preflight failure | Status `2`; empty stdout; one diagnostic; no sources |
| Missing path / ordinary backend `FileNotFoundError` | Per-operand diagnostic `stat: <operand>: <escaped message>`; continue; final status `1` if any operand failed |
| Incompatible / malformed `_info` | `stat: <operand>: incompatible result`; continue; status `1` |
| Other ordinary backend/result errors | Same continuation model with stable escaped diagnostics |
| Stdout short write / broken pipe after accepted bytes | Stop further operand output; preserve accepted bytes; status `1`; still run cleanup |
| Cancellation / control-flow `BaseException` | Propagate after source cleanup per ADR 0003 |
| Cleanup failures | Append diagnostics; force status `1` without erasing earlier ordinary failure; BaseException precedence unchanged |

Successful invocations that render every operand exit `0`. Partial success exits
`1`. No stdout on pure rejection paths.

Buffer one complete success line before writing it. Do not interleave partial
field writes.

## 6. Explicit reduced-profile divergences

Relative to macOS/BSD `stat(1)`:

1. No default `DEF_FORMAT` line.
2. No `-f` format language.
3. No link, device, or flag fields.
4. No atime/ctime/birth/blocks/device/inode in the locked line (even when Local
   exposes `ino`/`created`).
5. Mapped operands only; no stdin-FD operand.
6. Incompatible metadata fails closed instead of printing placeholders.
7. Never marketed as POSIX.

Relative to fsspec:

1. Abstract `info` floor alone is insufficient; the locked complete shape is
   required.
2. Adapted Memory and native `vosfs` cannot pass until they supply that shape
   without fabrication.

## 7. Matrix plan

Planned rows (implementation ticket lands evidence; research leaves them
`unverified`):

| Command profile | Scope | Source form | Planned status after #146 gates | Required gates |
| --- | --- | --- | --- | --- |
| Reduced BSD/macOS `stat` | source | `local / adapted async` | `pass` only if Local-rich shape + golden vectors hold | Hermetic App-seam + isolated wheel |
| Reduced BSD/macOS `stat` | source | `memory / adapted async` | expect incompatible/`fail` or remain non-pass; not a silent sparse pass | Hermetic |
| Reduced BSD/macOS `stat` | source | `vosfs / native async` | expect incompatible/`fail` or remain non-pass without inventing mode/owner | Hermetic (live optional, sanitized) |
| Reduced `stat` option/operand rejection | command preflight | `not entered` | `unsupported` | Hermetic negative rejection |

Canonical matrix rows for planning are recorded in
[the tested command matrix](fsspec-cli-tested-command-matrix.md) as `unverified`
until #146 supplies qualifying evidence. Missing evidence stays `unverified`,
never `pass`.

## 8. README / help / changelog language (drafts for #146)

Do not publish these in user docs until the production command exists
(`CONTRIBUTING.md`: do not document commands that do not exist). Issue #146 MUST
apply equivalent wording when the command ships.

**README draft (fragment):**

> Reduced BSD/macOS-shaped `stat` accepts only `stat [--] name:/path...`, awaits
> `_info` once per operand, and prints one fixed line per successful
> non-link file or directory when the locked Local-rich metadata shape is
> present. It is not POSIX `stat`, not GNU `stat`, and not full macOS/BSD
> `stat(1)`. Options and incomplete metadata reject or fail closed without
> placeholders. This command is not evidence for `ls -l`.

**Help draft:**

```text
Usage: stat [--] name:/path...
Reduced BSD/macOS-shaped file status over fsspec _info (not POSIX).
```

**Changelog draft (Release Please / conventional commit body, not a hand edit):**

> Add reduced BSD/macOS-shaped `stat` over authoritative `_info` fields. Not
> POSIX; not full host `stat(1)`.

## 9. T25 implementation frontier (#146)

Issue #146 MAY merge production code only after this profile stays locked and
the merge gate (`fsspec-cli-v0.1.0` / #108) is satisfied per issue text.

Required implementation work:

1. Private `_stat.py` + `App` registration only through `App(sources).typer_app`.
2. Exact T24 argv; reject every Section 1.1 surface source-free.
3. Await `_info` once per operand; validate Section 3 before render.
4. One source-independent renderer; golden vectors byte-for-byte.
5. Continuation, output-failure, cleanup, and status per Section 5.
6. Matrix evidence for Local/Memory/vosfs rows; Memory/vosfs must not fake a
   pass.
7. README/help/changelog agree with this profile; still never claim POSIX.
8. No reuse as `ls -l` justification.

Out of scope for T25: `-f` language, JSON, recursion, capacity, GNU blend,
backend branches, placeholder metadata, directory traversal.

## 10. Research deliverable boundary

This ticket MUST NOT land production `_stat.py` or a Typer `stat` command.
Fixture vectors and matrix planning are documentation and future-test inputs,
not runtime capability negotiation.
