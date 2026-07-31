# LEGISLATION_WATCH.md — Legislative watch process (SPEC-LCE §4)

**Operationalised by `tools/watch.py` + `watch_sources.yaml`** (previously process-only).
The tool content-hash diffs every registered source, stores snapshots in
`watch_state/` (gitignored), and emits change events + diff-proposal skeletons.
It is offline by default (fixtures); live fetching requires `--live` and only
the weekly advisory CI job (`ci/workflows/watch.yml`, continue-on-error) uses it.

```bash
python tools/watch.py --check      # report changes; exit 1 while any change is unacknowledged
python tools/watch.py --snapshot   # after counsel review: update store, acknowledge events
python tools/watch.py --report     # pending changes + aging vs the 20-business-day SLA
```

## 1. Source feeds (registered in `watch_sources.yaml`, checked weekly)

- Federal Republic of Nigeria Official Gazette feed (`fgn-gazette`; CTC requests via NRS).
- NRS (formerly FIRS) circulars & public notices page (`nrs-circulars`); NRS
  presumptive-tax framework (currently draft — unblocks finding #18 and flips
  `subject_to_regazette` flags).
- NRS e-invoicing (MBS) technical documentation (`nrs-einvoice-docs`).
- CBN circulars page (`cbn-circulars`; EMTL, thresholds).
- Federal Ministry of Finance press releases (WHT-Regs-style instruments) —
  add to `watch_sources.yaml` when a stable URL is confirmed.
- Secondary early-warning (clearly tagged non-authoritative): KPMG/PwC/BDO/Andersen
  Nigeria tax alerts, UUBO/SHQ Legal/Templars/Aluko & Oyebode briefings —
  tracked by the duty engineer; deliberately NOT in the registry (G1: no pack
  change on secondary-source-only reports).
- Tracking log: `docs/reg-watch-log.md` — date, source, instrument, action taken
  (the watch tool's `watch_state/pending.json` is the machine-readable log).

## 2. Diff proposal format

Every detected change is filed as a PR containing a **diff-proposal YAML** at
`outbox/legislation-diffs/<yyyy-mm-dd>-<slug>.yaml` plus the pack edits.
`tools/watch.py --check` writes the **skeleton** (instrument/statute/section
left for counsel, `excerpt_sha256` and `sla_deadline` = detected + 20 business
days pre-filled); counsel completes it:

```yaml
instrument: Nigeria Tax (Amendment) Act 2026
detected_at: '2026-08-14'
detected_via: gazette
sections_affected: [nta-2025:s.187.zero-rated-medical]
pack_changes:
  - pack: rp-vat-zerorated-basket
    change: amend
    rule_ids: [vat.zero.medical-services]
    effective_from: '2027-01-01'
retroactive: false
counsel: {name: '<external counsel>', signed_off_at: null}   # gate, see §3
sla_deadline: '2026-08-28'
```

## 3. Counsel sign-off inside the §9.1 ceremony

A documented (not coded) stage is added to GOVERNANCE.md between **review** and
**simulate**: **counsel-review** — for any pack change whose diff-proposal touches
`status: IMPLEMENTED` sections, external counsel must record sign-off in the
diff-proposal YAML (`counsel.signed_off_at`) before the governance board signs.
The ceremony tool is unchanged; the gate is procedural, enforced by the board
checklist.
(Future work: `ceremony.py` refuses `sign` when a diff-proposal in the changeset
lacks `counsel.signed_off_at`.)

**CTC verification is part of counsel-review.** When counsel sights the gazette
CTC for an instrument, they record it with `tools/ctc.py`:

```bash
python tools/ctc.py record-verification --statute wht-regs-2024 \
    --section first-schedule.dividend --document gazette-106-2024.pdf \
    --verifier "A. Counsel, External Chambers"
python tools/ctc.py --report    # per-statute verified/unverified/waived counts
```

This sha256-pins the exact document sighted, writes the `ctc:` block into the
coverage section, flips `citation_kind: secondary → primary`, and re-validates
(`coverage_validate.py` FAILs on a `verified` block missing verifier/date/source,
WARNs if the hash is absent). `tools/attest.py` reports CTC coverage
(`--ctc-threshold` arms the ratchet; default report-only). Baseline today:
**all sections unverified** — see `python tools/ctc.py --report`.

## 4. SLA

| Event | SLA |
|---|---|
| Gazetted change detected → diff-proposal PR open | 5 business days |
| Diff-proposal → counsel sign-off | 10 business days |
| Sign-off → pack published (ceremony complete) | 5 business days |
| **Gazette date → published pack** | **≤ 20 business days** |
| Change effective in < 20 days (emergency) | publish with `subject_to_regazette: true`, fast-track counsel review, attest daily |
| Secondary-source-only report (no gazette) | tracked in log; no pack change until gazette/CTC (G1) |

## 5. Retroactive vs prospective effective dating

- **Prospective:** new rules carry `effective_from` = commencement date; old rules get
  `effective_to` = day before. Boundary-date conformance cases (SPEC-LCE §2.2) are
  mandatory for every such split — this is the T4/T6 pattern.
- **Retroactive (law backdates effect):** add the new rules with the backdated
  `effective_from`, set `effective_to` on the superseded rules, and ship a
  **recomputation notice** in the diff proposal listing affected period ranges; engines
  re-evaluate open periods via the same packs (rule-level date dispatch already supports
  this — the reference matcher never "versions" rules, it date-dispatches).
- **Never mutate a published pack in place.** Any content change = new version through
  the full ceremony; the WORM archive keeps every prior version for back-period audits.
- Legacy regimes stay loadable forever (precedent: `rp-cit-legacy`,
  `rp-paye-pitra-legacy` with `effective_to: '2025-12-31'`; the missing pre-2025 WHT
  regime is logged as `wht-regs-2024:pre-2025.legacy-rates`, UNIMPLEMENTED).
