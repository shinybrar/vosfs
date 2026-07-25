# Validate Range support from HTTP 206

Status: Accepted

Question: Some OpenCADC byte backends (minoc behind `vault`) honour `Range`
with a correct `206`. Others (Cavern) ignore `Range` and return `200` with the
whole object. How should `vosfs` expose partial reads without corrupting
Cavern results or trusting advisory headers?

## Decision

**Send `Range` for partial `cat_file` / `cat_ranges` reads when the bounds map
to one HTTP range. Keep the partial body only on a validated `206`. Treat
`200`/`204` as a whole-object fallback and slice locally.**

Do not treat `Accept-Ranges: bytes` as proof of support. Do not assume a `200`
body is a partial. Staged `open` stays whole-object. `blockcache::` /
`cached::` remain unsupported until a separate contract claims them.

## Consequences

- `vault`/minoc partial reads transfer only the requested bytes.
- Cavern keeps correct results via whole-object fallback (same as before for
  correctness; `cat_ranges` still stages to disk on `200`).
- Callers must not infer block-cache safety from this ADR alone.

## Notes

Normative detail lives in [`docs/design/trd.md`](../design/trd.md) §8.
Informative deployment probes:
[`docs/research/vosfs-vault-minoc-byte-range-capability.md`](../research/vosfs-vault-minoc-byte-range-capability.md).
