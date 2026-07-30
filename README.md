# meridian-rule-packs

Rule-pack registry content for the **Meridian TaxTech platform** (Nigerian NRS unified
tax platform). All packs follow the SPEC §1.4 YAML grammar
(`schemas/rulepack.schema.json`), carry provenance + `subject_to_regazette: true`
(G1 gate until CTCs are confirmed), and are published through the §9.1 governance
ceremony with real ed25519 signatures.

## Layout

```
packs/<pack-id>/<version>.yaml   # 35 packs, v1.0.0 each, signed & published
schemas/rulepack.schema.json     # JSON Schema (draft 2020-12) for the §1.4 grammar
tools/validate.py                # schema + ed25519 signature + WORM archive validation
tools/ceremony.py                # §9.1 ceremony: draft→review→simulate→sign→publish→archive
tools/rpcommon.py                # canonicalisation, keys, ULID, §1.1 event envelope
tools/keys/                      # DEV ed25519 keypair (governance-board-2026) — not prod
tests/                           # pytest suite (validator + ceremony, 154 tests)
signatures/archive/              # WORM archive records (sha256 + worm_uri per pack)
outbox/nrs.rulepacks.published.v1/  # publish events (SPEC §1.1 envelope)
GOVERNANCE.md                    # ceremony definition and change management
.github/workflows/validate.yml   # CI: validate + test on every change
```

## Packs (35)

| Pack | Domain |
|---|---|
| rp-wht-2024 | WHT Regulations 2024: rates per payment type/beneficiary (10% dividend/interest/rent, 5% services, 2% goods/construction), deduction at earlier of payment/settlement, no-TIN double rate, ≤₦2m/month small-company carve-out, NIN-acceptable identity, direct-debit/broker/imported-goods exemptions, remittance deadlines |
| rp-education-ng | T14 effective-dated tables: PIT bands 0% ≤₦800k → 25% >₦50m, rent relief 20% capped ₦500k, CIT 30%, small-co ₦100m/₦250m → 0%, VAT 7.5% |
| rp-tp-2018 | TP Regs 2018: ₦300m doc threshold, CbCR ₦160bn, 30%-of-EBITDA connected-party interest cap, methods, APA, penalties |
| rp-etr-nta / rp-etr-scope / rp-etr-cfc | 15% minimum ETR (€750m MNE / ₦50bn domestic), scope & de minimis, CFC deemed-distribution + GloBE push-down |
| rp-globe-oecd / rp-gir-schema | GloBE mechanics: jurisdictional ETR, substance carve-out (10%/8% transition → 5%/5%), IIR top-down ordering, UTPR, safe harbours; GIR datapoints & 15/18-month deadlines |
| rp-carf-schema / rp-nta-vasp-duties / rp-nta-digital-assets / rp-sec-vasp-rules / rp-asset-taxonomy | CARF message structure (CARF401-404, RCASP, reportable users, txn types), VASP duties, digital-asset chargeable gains + loss ring-fence, SEC VASP rules, asset taxonomy |
| rp-vat-rates / rp-vat-exempt-basket / rp-vat-zerorated-basket / rp-vat-attribution-mode / rp-platform-collectors | VAT 7.5% (+5% legacy), exempt & zero-rated baskets (basic food, medical, education, exports), federal/state attribution switch + NTAA sharing, platform collector rules |
| rp-presumptive-federal / rp-presumptive-lagos / rp-presumptive-kano / rp-turnover-bands / rp-exemption-nta | Presumptive band tables per turnover band (₦ amounts in kobo), band definitions, NTA exemptions |
| rp-attribution-formula / rp-fmt-lagos / rp-fmt-fct / rp-identity-match-thresholds | NTAA attribution formula (30% place-of-consumption factor), Lagos/FCT filing matrices, entity-resolution match thresholds |
| rp-procedure-ombud / rp-procedure-tat / rp-ntaa-penalties / rp-deposit-20pct | Tax Ombud procedure, TAT appeals, NTAA penalty table, 20% appeal deposit rule |
| rp-ubl-bis / rp-mbs-business-rules | UBL 2.1/Peppol BIS mandatory fields, MBS e-invoicing business validation (totals consistency, VAT arithmetic, IRN/stamp binding) |
| rp-disclosure-control / rp-bank-thresholds | k-anonymity k=5 disclosure control, bank reporting/cash thresholds |

## Money convention

All monetary amounts are **integer kobo** (SPEC §1.3; fields suffixed `_kobo`).
EUR scope thresholds use `_eur`. Rates are basis points (`*_bps`: 1000 bps = 10%).

## Quickstart

```bash
python3 -m venv venv && . venv/bin/activate   # or system python 3.12
pip install -r requirements.txt
python tools/validate.py     # 35/35 packs valid (schema + signature + archive)
pytest -q                    # 154 passed
python tools/ceremony.py --all   # idempotent re-run of the §9.1 ceremony
```

## Honesty tags (what is dev/simulated)

- `tools/keys/governance-board-2026.*` — **dev keypair**, auto-generated; production
  uses HSM custody (see GOVERNANCE.md).
- `worm://meridian-dev-worm/...` URIs — dev WORM scheme; production wires to the
  audit-evidence WORM object store (MinIO/compliance mode).
- Band amounts, presumptive schedules and NTAA sharing coefficients flagged
  `subject_to_regazette: true` — expected to change when gazettes/CTCs land.
- Some packs (rp-fmt-lagos, rp-presumptive-*) reference state circulars still in
  draft; provenance says so explicitly.
