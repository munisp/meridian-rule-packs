# meridian-rule-packs

**Signed versioned rule packs (rp-*): VAT baskets, presumptive bands, ETR formulas, procedure rules — gazette-proof config, not code.**

## Purpose
All tax law parameters live here as versioned, signed rule packs (`rp-*`). Suites consume packs through the registry consumer in `meridian-core-platform`; no law is hard-coded in application code. A gazette change = a new signed pack version, not a redeploy.

## Plane mapping
- **Control plane:** pack authoring, review, signing, versioning
- **Data plane (consumed):** VAT baskets, presumptive bands, ETR formulas, procedure rules

## Layout
| Path | Contents |
|------|----------|
| `packs/` | Rule pack definitions (one directory per `rp-*` pack, versioned) |
| `schemas/` | JSON Schema for each pack type; CI validates every pack against schema |
| `signatures/` | Detached signatures & public keyring for pack verification |

## Signing ceremony
1. **Draft** — pack author opens a PR adding/updating a pack under `packs/rp-<name>/<version>/`.
2. **Validate** — CI validates the pack against its schema in `schemas/` and runs regression tests against golden cases.
3. **Review** — dual review: a tax-domain reviewer (legal correctness vs. gazette text) and an engineering reviewer (schema/semantics).
4. **Sign** — on merge, a quorum of key holders (2-of-3 offline keys) signs the pack manifest; the detached signature is committed to `signatures/`.
5. **Publish** — tagged release `rp-<name>@<version>`; registries pull only packs with valid signatures from the trusted keyring.
6. **Rollback** — consumers pin versions; rollback = repointing to a prior signed version. No unsigned or expired pack is ever served.

## Sibling repositories
- [meridian-core-platform](https://github.com/munisp/meridian-core-platform)
- [meridian-compliance-suite](https://github.com/munisp/meridian-compliance-suite)
- [meridian-inclusion-suite](https://github.com/munisp/meridian-inclusion-suite)
- [meridian-gov-enclave](https://github.com/munisp/meridian-gov-enclave)
- [meridian-docs](https://github.com/munisp/meridian-docs)

**Status:** scaffold
