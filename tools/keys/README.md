# tools/keys — verification keys only (NO private key material ever)

## Active key

`governance-board-2026-r2.ed25519.public` — **current** public verification key
(key_id `governance-board-2026-r2`) for the governance ceremony signature on
every published pack. It must stay in the repo so `tools/validate.py` and
consumer loaders can verify signatures. All packs were re-signed under this
key_id in the R4-9a rotation (2026-09-18); the matching private key was
generated in the ceremony environment, used once to re-sign, and handed to
secrets-vault custody — it is NOT in this repo and must never be committed.

## REVOKED key — `governance-board-2026` (BURNED)

The former dev private key (`governance-board-2026.ed25519.private`) was
committed here by mistake and **signed every "published" pack** up to the
R4-9a rotation. It was removed from the tree (R4 trust-root fix) but remains
in git history, so it must be considered **compromised/burned**:

- it is listed in `revoked-keys.json` as **REVOKED**;
- `governance-board-2026.ed25519.public` is retained ONLY for historical
  verification of packs archived before the rotation;
- **policy: treat any signature from `governance-board-2026` created after
  2026-09-18T19:55:00Z as invalid.** Since git history cannot be rewritten,
  the burned key material will exist publicly forever — pin
  `governance-board-2026-r2` in consumers and enforce the revocation date;
- it must never be re-committed or reused;
- production signing uses HSM custody (see GOVERNANCE.md) — no private key
  material ever lives in this repo.

`tools/rpcommon.ensure_dev_keypair` **refuses to silently regenerate** a
keypair when the public key exists but the private key is missing —
re-creating a different private key under the same `key_id` would fork the
trust root. New ceremony keys are generated only through the documented
key-ceremony rotation in GOVERNANCE.md, under a NEW key_id, with the old
key_id retired in consumer `signing_keys.json` pins.
