# tools/keys — verification key only

`governance-board-2026.ed25519.public` is the **public verification key** for the
governance ceremony signature on every published pack. It must stay in the repo so
`tools/validate.py` and consumer loaders can verify signatures.

## SECURITY — the dev private key is BURNED

The matching dev private key (`governance-board-2026.ed25519.private`) was committed
here by mistake and **signed every "published" pack**. It has been removed from the
repo (R4 trust-root fix) and must be considered **compromised/burned**:

- it must be rotated OUT of any deployment that ever trusted it;
- it must never be re-committed or reused;
- production signing uses HSM custody (see GOVERNANCE.md) — no private key material
  ever lives in this repo.

`tools/rpcommon.ensure_dev_keypair` now **refuses to silently regenerate** a keypair
when the public key exists but the private key is missing — re-creating a different
private key under the same `key_id` would fork the trust root. New ceremony keys are
generated only through the documented key-ceremony rotation in GOVERNANCE.md, under a
NEW key_id, with the old key_id retired in consumer `signing_keys.json` pins.
