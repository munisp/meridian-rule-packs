# Engine adapter contract (SPEC-LCE §2.3)

An adapter lets the conformance harness run the same declarative cases through
a live engine and compare the result against the canonical reference matcher
(`tools/refmatch.py`) — the drift ratchet that forces engines to reach pack
parity.

```python
class EngineAdapter(Protocol):
    name: str
    def translate_facts(self, case: dict) -> dict: ...   # case facts -> engine request
    def evaluate(self, request: dict) -> dict: ...       # raw engine call
    def normalise(self, raw: dict) -> dict: ...
        # must yield: {"rate_bps": int, "wht_kobo": int, "rule_ids": [str],
        #              "outcome": str, "citations": [{"pack", "rule_id", "citation"}]}
```

## Bindings (WHT — the only engine adapter this session)

- **in-proc (default, CI):** `LCE_WHT_ENGINE=inproc`. The meridian-compliance-suite
  repo must be checked out as a sibling (`../meridian-compliance-suite`,
  override with `LCE_COMPLIANCE_SUITE_PATH`). The adapter appends
  `packages/py` and `services/wht` to `sys.path` and calls
  `app.engine.evaluate_wht(request)`.
- **HTTP (integration):** `LCE_WHT_ENGINE=http://localhost:8091` →
  `POST /v1/wht/evaluate` with a dev JWT (`packages/py/meridian_py/dev_jwt`).
  Marked `@pytest.mark.integration`; skipped by default.

## Fact translation (wht)

Case facts map onto the engine request schema:
`payment_type, beneficiary, supplier_tin, supplier_nin, amount_kobo,
payment_date (= facts.date), monthly_amount_kobo (= transaction_month_value_kobo),
payer_annual_turnover_kobo, payer_size, beneficiary_residence, source,
construction_type`. Rate-only cases get a default `amount_kobo` of N1m.

Facts a given engine build does not honour are still passed through in the
request body; the drift test asserts engine result == reference result, and
where the engine ignores a fact the failure message names it
(`ignored facts: ...`). Known first-day drift is recorded in the attestation
report with named failures — exit 1 naming them is the correct state, never
fake green.

## Non-WHT areas

VAT/CIT/NTAA case sets run in reference mode only this session; engine drift
mode filters to `area: wht` cases.
