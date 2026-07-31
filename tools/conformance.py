#!/usr/bin/env python3
"""LCE conformance harness (SPEC-LCE §2) — case loader + runners + engine adapters.

Modes
-----
reference   Evaluate cases against packs on disk via the canonical reference
            matcher (tools/refmatch.py). Always runs in CI; no engine needed.
engine      Evaluate the same cases through a live engine adapter and compare
            against the reference result (drift ratchet). WHT-only today.

CLI (used by the attestation gate):
    python tools/conformance.py --mode reference --format json
    python tools/conformance.py --mode engine --engine inproc --format json

Exit codes: 0 = all pass, 1 = failures, 2 = misconfiguration (missing engine,
bad checkout).

Engine adapter contract (see conformance/adapters/README.md):
    class EngineAdapter(Protocol):
        name: str
        def translate_facts(self, case: dict) -> dict: ...   # case facts -> engine request
        def evaluate(self, request: dict) -> dict: ...       # raw engine call
        def normalise(self, raw: dict) -> dict: ...
            # {"rate_bps": int, "wht_kobo": int, "rule_ids": [str],
            #  "outcome": str, "citations": [{pack, rule_id, citation}]}

Environment:
    LCE_WHT_ENGINE            "inproc" or an http:// base URL (drift mode)
    LCE_COMPLIANCE_SUITE_PATH sibling checkout path (default ../meridian-compliance-suite)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Protocol

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import refmatch  # noqa: E402

CASES_DIR = REPO / "conformance" / "cases"
DEFAULT_SUITE_PATH = REPO.parent / "meridian-compliance-suite"

# Facts the reference matcher consumes; engine-only expect keys are ignored in
# reference mode (SPEC-LCE §2.2).
ENGINE_ONLY_EXPECT_KEYS = {"outcome", "net_payable_kobo"}

# Request keys the WHT in-proc adapter maps onto (facts NOT in this set are
# reported as "ignored facts" in drift failures — the regression ratchet that
# forces the engine to reach pack parity, SPEC-LCE §2.3).
WHT_REQUEST_KEYS = {
    "payment_type": "payment_type",
    "beneficiary": "beneficiary",
    "supplier_tin": "supplier_tin",
    "supplier_nin": "supplier_nin",
    "amount_kobo": "amount_kobo",
    "date": "payment_date",
    "transaction_month_value_kobo": "monthly_amount_kobo",
    "payer_annual_turnover_kobo": "payer_annual_turnover_kobo",
    "payer_size": "payer_size",
    "beneficiary_residence": "beneficiary_residence",
    "source": "source",
    "construction_type": "construction_type",
}
DEFAULT_ENGINE_AMOUNT_KOBO = 100_000_000  # N1m, for rate-only cases


# ----------------------------------------------------------------- case loading
def load_case_sets(cases_dir: Path = CASES_DIR) -> list[dict]:
    """Load every case file; each case inherits case_set.pack / default_facts,
    and may override the pack per case."""
    out: list[dict] = []
    for f in sorted(cases_dir.rglob("*.yaml")):
        doc = yaml.safe_load(f.read_text()) or {}
        cs = doc.get("case_set") or {}
        for case in doc.get("cases", []) or []:
            facts = dict(cs.get("default_facts") or {})
            facts.update(case.get("facts") or {})
            out.append({
                "id": case["id"],
                "case_set": cs.get("id"),
                "area": cs.get("area"),
                "pack": case.get("pack") or cs.get("pack"),
                "facts": facts,
                "expect": case.get("expect") or {},
                "source": str(f.relative_to(REPO)),
            })
    return out


# ----------------------------------------------------------------- reference mode
def reference_evaluate(case: dict) -> dict:
    """Evaluate one case against packs on disk via the canonical matcher."""
    pack = refmatch.load(case["pack"])
    facts = case["facts"]
    rate = refmatch.wht_rate_bps(pack, **facts)
    rate_rule = refmatch.resolve(pack, facts)
    decision_rule = refmatch.resolve(pack, facts, kind="decision")
    penalty_rule = refmatch.resolve(pack, facts, kind="penalty_kobo")
    winning = rate_rule or decision_rule or penalty_rule
    result = {
        "rate_bps": rate,
        "rule_id": winning["id"] if winning else None,
        "decision": (decision_rule["then"].get("decision") if decision_rule else None),
        "penalty_kobo": (penalty_rule["then"].get("penalty_kobo") if penalty_rule else None),
    }
    amount = facts.get("amount_kobo")
    if amount is not None and rate is not None:
        result["wht_kobo"] = amount * rate // 10_000
    return result


def compare_reference(case: dict) -> dict:
    """Run one case in reference mode; returns {id, pass, actual, failures}."""
    actual = reference_evaluate(case)
    failures: list[str] = []
    for key, want in case["expect"].items():
        if key in ENGINE_ONLY_EXPECT_KEYS:
            continue  # asserted in engine mode only
        got = actual.get(key)
        if got != want:
            failures.append(f"{key}: expected {want!r}, got {got!r}")
    return {"id": case["id"], "case_set": case["case_set"], "pass": not failures,
            "actual": actual, "failures": failures}


def run_reference(cases: list[dict]) -> dict:
    results = [compare_reference(c) for c in cases]
    return {"mode": "reference", "cases": len(results),
            "passed": sum(1 for r in results if r["pass"]),
            "failed": sum(1 for r in results if not r["pass"]),
            "results": results}


# ----------------------------------------------------------------- engine adapters
class EngineAdapter(Protocol):
    name: str

    def translate_facts(self, case: dict) -> dict: ...
    def evaluate(self, request: dict) -> dict: ...
    def normalise(self, raw: dict) -> dict: ...


class WhtInprocAdapter:
    """In-proc WHT adapter (default, CI). Imports the compliance-suite wht
    engine from a sibling checkout (LCE_COMPLIANCE_SUITE_PATH)."""
    name = "inproc"

    def __init__(self, suite_path: Path | None = None):
        self.suite_path = Path(
            os.environ.get("LCE_COMPLIANCE_SUITE_PATH") or DEFAULT_SUITE_PATH).resolve()
        for rel in ("packages/py", "services/wht"):
            p = self.suite_path / rel
            if not p.exists():
                raise AdapterUnavailable(f"sibling checkout path missing: {p}")
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        try:
            from app import engine  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            raise AdapterUnavailable(f"cannot import wht engine from {self.suite_path}: {e}")
        self._engine = engine

    def translate_facts(self, case: dict) -> dict:
        facts = case["facts"]
        req: dict = {}
        for fact_key, req_key in WHT_REQUEST_KEYS.items():
            if fact_key in facts and facts[fact_key] is not None:
                req[req_key] = facts[fact_key]
        req.setdefault("amount_kobo", DEFAULT_ENGINE_AMOUNT_KOBO)
        # Pass through facts the engine does not yet accept verbatim — the
        # drift test detects when they change (or fail to change) the result.
        for k, v in facts.items():
            if k not in WHT_REQUEST_KEYS and v is not None:
                req[k] = v
        return req

    def evaluate(self, request: dict) -> dict:
        return self._engine.evaluate_wht(request)

    def normalise(self, raw: dict) -> dict:
        return {
            "rate_bps": raw.get("rate_bps"),
            "wht_kobo": raw.get("wht_kobo"),
            "rule_ids": raw.get("matched_rules") or [],
            "outcome": raw.get("outcome"),
            "citations": raw.get("citations") or [],
        }


class WhtHttpAdapter:
    """HTTP WHT adapter (integration). POST /v1/wht/evaluate with a dev JWT.
    Selected with LCE_WHT_ENGINE=http://host:port. Skipped by default."""
    name = "http"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        try:
            import httpx  # noqa: F401, PLC0415
        except ImportError:
            raise AdapterUnavailable("httpx not installed")
        self._inner = WhtInprocAdapter.__new__(WhtInprocAdapter)  # reuse translate_facts only

    def translate_facts(self, case: dict) -> dict:
        return WhtInprocAdapter.translate_facts(self._inner, case)

    def _token(self) -> str:
        suite_path = Path(os.environ.get("LCE_COMPLIANCE_SUITE_PATH") or DEFAULT_SUITE_PATH).resolve()
        for rel in ("packages/py", "packages/shared"):
            p = suite_path / rel
            if p.exists() and str(p) not in sys.path:
                sys.path.insert(0, str(p))
        try:
            from meridian_py import dev_jwt  # noqa: PLC0415
            return dev_jwt.mint({"sub": "lce-conformance", "scope": "wht:evaluate"})
        except Exception:  # noqa: BLE001
            return ""

    def evaluate(self, request: dict) -> dict:
        import httpx  # noqa: PLC0415
        headers = {"content-type": "application/json"}
        tok = self._token()
        if tok:
            headers["authorization"] = f"Bearer {tok}"
        resp = httpx.post(f"{self.base_url}/v1/wht/evaluate", json=request,
                          headers=headers, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def normalise(self, raw: dict) -> dict:
        return WhtInprocAdapter.normalise(self._inner, raw)


class AdapterUnavailable(Exception):
    pass


def get_adapter(spec: str) -> EngineAdapter:
    if spec == "inproc":
        return WhtInprocAdapter()
    if spec.startswith("http://") or spec.startswith("https://"):
        return WhtHttpAdapter(spec)
    raise AdapterUnavailable(f"unknown engine spec {spec!r}")


# ----------------------------------------------------------------- engine drift mode
def compare_engine(case: dict, adapter: EngineAdapter) -> dict:
    """Compare engine result to reference result for one case."""
    ref = reference_evaluate(case)
    request = adapter.translate_facts(case)
    try:
        raw = adapter.evaluate(request)
    except Exception as e:  # noqa: BLE001
        return {"id": case["id"], "pass": False, "reference": ref,
                "engine": None, "ignored_facts": [],
                "failures": [f"engine raised: {e}"]}
    eng = adapter.normalise(raw)
    ignored = sorted(k for k, v in case["facts"].items()
                     if k not in WHT_REQUEST_KEYS and v is not None)
    failures: list[str] = []
    for key in ("rate_bps", "wht_kobo"):
        want = ref.get(key)
        got = eng.get(key)
        if want is not None and got != want:
            msg = f"{key}: reference {want!r} vs engine {got!r}"
            if ignored:
                msg += f" (ignored facts: {', '.join(ignored)})"
            failures.append(msg)
    for key in ENGINE_ONLY_EXPECT_KEYS:
        if key in case["expect"] and eng.get(key) != case["expect"][key]:
            failures.append(f"{key}: expected {case['expect'][key]!r}, got {eng.get(key)!r}")
    return {"id": case["id"], "pass": not failures, "reference": ref,
            "engine": eng, "ignored_facts": ignored, "failures": failures}


def run_engine(cases: list[dict], adapter: EngineAdapter) -> dict:
    """Drift mode — WHT-area cases only (the only engine adapter this session)."""
    wht_cases = [c for c in cases if c["area"] == "wht"]
    results = [compare_engine(c, adapter) for c in wht_cases]
    mismatches = [r for r in results if not r["pass"]]
    return {"mode": "engine", "engine": adapter.name, "cases": len(results),
            "passed": len(results) - len(mismatches), "failed": len(mismatches),
            "mismatches": mismatches, "results": results}


# ----------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["reference", "engine"], default="reference")
    ap.add_argument("--engine", default=os.environ.get("LCE_WHT_ENGINE", "inproc"))
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args(argv)

    cases = load_case_sets()
    if args.mode == "reference":
        report = run_reference(cases)
    else:
        try:
            adapter = get_adapter(args.engine)
        except AdapterUnavailable as e:
            print(f"configuration error: {e}", file=sys.stderr)
            return 2
        report = run_engine(cases, adapter)

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        for r in report["results"]:
            mark = "ok  " if r["pass"] else "FAIL"
            print(f"{mark} {r['id']}")
            for f in r.get("failures", []):
                print(f"     - {f}")
        print(f"\n{report['passed']}/{report['cases']} cases pass (mode={report['mode']})")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
