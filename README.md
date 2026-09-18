# meridian-rule-packs

Rule-pack registry content for the **Meridian TaxTech platform** (Nigerian NRS unified
tax platform). All packs follow the SPEC §1.4 YAML grammar
(`schemas/rulepack.schema.json`), carry provenance + `subject_to_regazette: true`
(G1 gate until CTCs are confirmed), and are published through the §9.1 governance
ceremony with real ed25519 signatures.

## Layout

```
packs/<pack-id>/<version>.yaml   # 40 packs (39 dirs + versions), signed & published; +4 dirs / +11 versions in the R4 wave
schemas/rulepack.schema.json     # JSON Schema (draft 2020-12) for the §1.4 grammar
tools/validate.py                # schema + ed25519 signature + WORM archive validation
tools/ceremony.py                # §9.1 ceremony: draft→review→simulate→sign→publish→archive
tools/rpcommon.py                # canonicalisation, keys, ULID, §1.1 event envelope
tools/keys/                      # PUBLIC verification key only — private key burned (see tools/keys/README.md)
tests/                           # pytest suite (validator + ceremony + boundaries)
signatures/archive/              # WORM archive records (sha256 + worm_uri per pack)
outbox/nrs.rulepacks.published.v1/  # publish events (SPEC §1.1 envelope)
GOVERNANCE.md                    # ceremony definition and change management
ci/workflows/validate.yml        # CI: validate + test on every change
```

## Packs (40 on main; 44 after the R4 fix wave)

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
| rp-stamp-duty | Stamp duties: EMTL ₦50 on transfers ≥₦10k, ad valorem (agreements 0.1%, share capital/securities 0.75%, mortgages 0.375%, conveyance 1.5%), 30-day stamping deadline, adjudication |
| rp-cgt | Capital gains: legacy 10% flat (to 2025-12-31), NTA alignment from 2026 (30% medium/large companies, 0% small companies, PIT marginal for individuals), residence/compensation ₦50m/gov-securities reliefs |
| rp-paye-pitra-legacy | Pre-2026 PAYE/PIT: PITA bands 7/11/15/19/21/24%, CRA (higher of ₦200k or 1% + 20% of gross), exempt deductions (pension/NHF/NHIS/life), 1% minimum tax, PAYE remit 10th |
| rp-fmt-federal | Federal filing calendar: VAT 21st, WHT 21st (companies)/30th (individuals), PAYE 10th, CIT 6 months after year-end, DevLevy with CIT, stamp duty 30 days, e-invoice clearance before issuance |
| **R4 additions** | |
| rp-paye-nta | NTA 2025 PAYE bands 0/15/18/21/23/25 with ₦800k zero band, rent relief, no CRA — canonical pack for 2026+ (rp-paye-pitra-legacy kept for back periods) |
| rp-commissions-ng | Canonical signed agent commission table (250/100/50 bps by hierarchy level) — supersedes the inclusion-suite embedded JSON |
| rp-excise-ng | Excise (honest minimal): ₦10/litre sweetened beverages (FA2021), telecom 5% abolished 2025-08-19; full schedule UNSOURCED-gated |
| rp-levies-legacy | Legacy NASENI 0.25% / Police Trust Fund 0.005% levies ≤2025-12-31, superseded_by rp-education-ng (4% Development Levy from 2026) |
| v1.1.0 corrections | rp-cit-legacy (TET 2%/2.5%/3% bands), rp-attribution-formula (50/20/30 equality/population/consumption), rp-etr-nta/rp-etr-scope/rp-globe-oecd/rp-gir-schema (engine-ID aliases), rp-paye-pitra-legacy (retirement metadata) |

## Retirement / sunset convention (R4)

Packs superseded by consolidation or new law are **never deleted** — they carry
documented sunset metadata so back periods still compute under old law:

- `effective_to` — last date the pack's rules apply (already pack/rule-level);
- `superseded_by` — id of the pack governing from `effective_to` (schema-supported, optional);
- `retirement_note` — free-text why/what-replaces note;
- `status: retired` — terminal lifecycle state (a retired pack stays in the WORM archive
  and remains signature-verifiable; `latest_local()` consumers must not select it for
  new filings).

Applied at R4: `rp-paye-pitra-legacy` 1.1.0 (`superseded_by: rp-paye-nta` from 2026),
`rp-levies-legacy` (`superseded_by: rp-education-ng` from 2026).

## Provisioned-ahead packs (honest consumer status)

`rp-fmt-lagos`, `rp-fmt-fct` and `rp-sec-vasp-rules` currently have **zero runtime
consumers** (confirmed by cross-repo reference audit, R4-S4 §1). They are retained
deliberately as **provisioned-ahead** content, not deleted:

- `rp-fmt-lagos` / `rp-fmt-fct` — intended consumers: state filing-calendar surfaces
  (compliance-suite filings calendar + taxpayer PWA deadline views) once state matrices
  are wired like `rp-fmt-federal` is.
- `rp-sec-vasp-rules` — intended consumer: compliance-suite vasp-carf service rule pack
  set (SEC VASP obligations alongside CARF schema packs).

Until those consumers land, treat these packs as signed reference content; do not
interpret their presence as wired coverage.

## Money convention

All monetary amounts are **integer kobo** (SPEC §1.3; fields suffixed `_kobo`).
EUR scope thresholds use `_eur`. Rates are basis points (`*_bps`: 1000 bps = 10%).

## Boundary convention (I13)

Packs defining adjacent turnover/income bands declare
`boundary_semantics: min_inclusive_max_exclusive`: every band is `[min, max)` —
a value exactly on a shared boundary belongs to the **higher** band. The
presumptive ceiling (`rp-turnover-bands` band.exit.register, `gte` ₦100m) is
aligned with the VAT registration threshold (`rp-vat-rates`, `gte` ₦100m from
2026-01-01; legacy ₦25m before).

## Legislative Compliance Engine (LCE)

The LCE keeps the pack corpus provably aligned with the statute book. Components:

```
coverage/*.yaml                # 9 statute coverage files (one per statute); sections
                               # reference implementing rules + conformance tests,
                               # with explicit IMPLEMENTED/PARTIAL/UNIMPLEMENTED/UNSOURCED status
schemas/coverage.schema.json   # JSON Schema (draft 2020-12) for coverage files
tools/refmatch.py              # canonical reference matcher (extracted verbatim from
                               # tests/test_taxlaw_parity.py — single matching truth)
tools/coverage_validate.py     # coverage validator (6 FAIL conditions, SPEC-LCE §1.3)
tools/conformance.py           # declarative case loader + reference/engine runners
conformance/cases/             # 56 seed cases: 29-row WHT matrix + boundary pairs,
                               # carve-out kobo arithmetic, no-TIN split, VAT baskets,
                               # legacy CIT dispatch, NTAA registration threshold
conformance/adapters/README.md # engine adapter contract (in-proc default, HTTP stub)
tools/attest.py                # attestation gate: validate → signatures → coverage →
                               # conformance → drift → CTC coverage → PASS/FAIL roll-up
ci/workflows/compliance.yml    # CI compliance-gate job (additive to validate.yml)
docs/LEGISLATION_WATCH.md      # legislation-watch process (feeds, diff proposals, SLAs)
watch_sources.yaml             # legislation-watch source registry (URL, parser hint, fixture)
tools/watch.py                 # content-hash diffing over the registry; --check/--snapshot/
                               # --report; offline (fixtures) by default, --live explicit
ci/workflows/watch.yml         # weekly advisory watch job (continue-on-error, report artifact)
tools/ctc.py                   # CTC citation-verification registry: record-verification
                               # (sha256-pins the sighted gazette doc), waive, --report
```

```bash
python tools/coverage_validate.py            # coverage vs packs + tests + cases
pytest tests/test_conformance.py -q          # reference-mode conformance (always green)
LCE_WHT_ENGINE=inproc pytest tests/test_engine_drift.py -q   # engine drift ratchet
python tools/attest.py --engine inproc --out out/attestation # md + JSON report
```

The in-proc engine adapter expects `meridian-compliance-suite` checked out as a
sibling (`../meridian-compliance-suite`; override with
`LCE_COMPLIANCE_SUITE_PATH`). The CI job checks both repos out side by side under
the workspace for the same reason.

### Honest scope (read before quoting the report)

- `citation_kind: secondary` — coverage citations pointing at firm commentary
  (KPMG/PwC/UUBO/SHQ Legal/Forvis Mazars) are **working citations, not gazette
  URLs**. The G1 verification workflow is now operational: counsel sights the
  gazette CTC and runs `tools/ctc.py record-verification`, which sha256-pins
  the sighted document into the section's `ctc:` block and flips
  `citation_kind` to `primary`. **Current baseline: 0 of 61 sections verified**
  (`python tools/ctc.py --report`); `tools/attest.py` reports CTC coverage
  report-only until the ratchet is armed with `--ctc-threshold`.
- Known gaps are first-class rows, not omissions: WHT treaty relief (#16),
  co-location/brokerage/entertainers/loss-of-employment schedule rates (#15),
  pre-2025 legacy WHT regime (#14), presumptive framework UNSOURCED (#18),
  PSC register (identity-gaps), NDPA sections pending privacy audit.
- **Known engine drift (first-day state):** the wht service's embedded pack
  predates the tax-law-parity fixes, so engine-drift cases fail on
  services/directors-fees/winnings/construction rates, the carve-out, and
  no-TIN passive-income doubling. `tools/attest.py --engine inproc` exits 1
  naming these (allowlisted in `KNOWN_DRIFT` with expiry 2026-09-30) — that is
  the correct state until the engine reaches pack parity; the drift suite is
  the regression ratchet.

## Quickstart

```bash
python3 -m venv venv && . venv/bin/activate   # or system python 3.12
pip install -r requirements.txt
python tools/validate.py     # 40/40 on main; 51/51 pack versions once the R4 fix wave merges
pytest -q                    # test suite
python tools/ceremony.py --all   # idempotent re-run of the §9.1 ceremony
```

## Honesty tags (what is dev/simulated)

- `tools/keys/governance-board-2026.ed25519.public` — **public verification key only**.
  **SECURITY:** the matching dev private key was previously committed here and signed
  every published pack; it is **burned** (removed in the R4 trust-root fix) and must be
  rotated out of any deployment that trusted it. No private key material is ever
  committed; production uses HSM custody (see GOVERNANCE.md), and
  `tools/rpcommon.ensure_dev_keypair` refuses to silently regenerate a burned key_id.
- `worm://meridian-dev-worm/...` URIs — dev WORM scheme; production wires to the
  audit-evidence WORM object store (MinIO/compliance mode).
- Band amounts, presumptive schedules and NTAA sharing coefficients flagged
  `subject_to_regazette: true` — expected to change when gazettes/CTCs land.
- Some packs (rp-fmt-lagos, rp-presumptive-*) reference state circulars still in
  draft; provenance says so explicitly.
