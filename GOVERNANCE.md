# GOVERNANCE.md — Rule-pack governance ceremony

Ceremony per **§9.1 of the source governance doc** (NRS Unified Platform Implementation),
implemented by `tools/ceremony.py`. Every `rp-*` pack version MUST pass through this
ceremony before any Meridian service may load it as `published`.

## Actors

| Role | Responsibility |
|---|---|
| Rule-registry engineer | Authors pack YAML (status `draft`), runs the ceremony |
| Governance board (`key_id: governance-board-2026`) | Reviews provenance, owns the ed25519 signing key |
| rp-registry service (core-platform) | Serves published packs to consumers, tracks stale consumers |
| reg-watch service | Holds gates (G1 CTCs confirmed, G8 presumptive reg, carf.transmit_enabled, …) |

## Ceremony stages (draft → review → simulate → sign → publish → archive)

1. **draft** — Author edits `packs/<pack-id>/<version>.yaml` per §1.4 grammar with
   `status: draft`, full `provenance` (`as_passed`, `as_gazetted` or null,
   `source_citation`) and `subject_to_regazette: true` while CTCs are unconfirmed (G1).
2. **review** — Governance-board checks (automated gate in the tool): provenance
   completeness, unique rule ids, regazette flag acknowledged. A missing `as_gazetted`
   is allowed but recorded as "awaiting CTC".
3. **simulate** — Simulation hook runs BEFORE signing. Default: internal smoke
   evaluation of the rule grammar (every rule carries a decision payload or narration).
   An external hook can be injected via `SIMULATE_HOOK=/path/to/executable` — it
   receives the pack path and must exit 0.
4. **sign** — ed25519 signature over the canonical YAML bytes of everything above the
   `signed:` block (see `tools/rpcommon.canonical_bytes`). Dev keypair lives in
   `tools/keys/` (auto-generated on first ceremony). **Dev key is NOT production
   key material.** `status` is flipped to `published` BEFORE signing so the signature
   covers the final content.
5. **publish** — Pack file rewritten with the embedded `signed` block; an outbox event
   `nrs.rulepacks.published.v1` (SPEC §1.1 envelope) is written to
   `outbox/nrs.rulepacks.published.v1/<pack-id>-<version>.json` for the outbox relay
   to publish to the `nrs-rulepacks` topic family.
6. **archive** — A WORM record is written to `signatures/archive/<pack-id>-<version>.json`
   containing `sha256` of the signed pack file, a `worm://` URI, the publish event id,
   key id and signature. The record is immutable by convention; the ceremony is
   idempotent and will not republish a pack whose signature verifies and archive exists.

## Running it

```bash
pip install -r requirements.txt
python tools/ceremony.py rp-wht-2024 1.0.0   # single pack
python tools/ceremony.py --all               # every pack
python tools/validate.py                     # schema + signature + archive check
pytest                                       # full suite
```

## Change management

- Any content change requires a NEW version directory entry (`1.1.0`, `2.0.0`, …) —
  published files are immutable. Hot fixes in place are re-signed by the ceremony and
  produce a new archive record, but consumers pin versions via the registry lockfile.
- Regazette: when CTCs arrive (G1 flipped by the board via reg-watch), provenance
  `as_gazetted` is updated in a new patch version and `subject_to_regazette` may be
  set `false`.
- Production: replace the dev keypair with HSM-backed key custody; `key_id` rotation
  is recorded in this file and in the archive records.
