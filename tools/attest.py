#!/usr/bin/env python3
"""LCE attestation gate (SPEC-LCE §3).

Pipeline (each stage recorded, all stages run even on failure — report then exit):
  1. Pack validity          — tools/validate.py (schema + ed25519 + WORM archive)
  2. Signature verification — per-pack ed25519 vs tools/keys/governance-board-2026,
                              status == published
  3. Coverage validation    — tools/coverage_validate.py --format json
  4. Conformance (reference)— tools/conformance.py --mode reference --format json
  5. Engine drift           — tools/conformance.py --mode engine (only with --engine)
  6. Per-section roll-up    — PASS / FAIL / UNIMPLEMENTED / UNSOURCED

Usage:
    python tools/attest.py --out out/attestation     # writes .md + .json
    python tools/attest.py --engine inproc           # include engine-drift section
    python tools/attest.py --skip-engine             # reference-only attestation

Exit codes: 0 = no FAIL; 1 = any FAIL section or validator violation;
2 = tool misconfiguration (missing engine, bad checkout).

First-day state: engine drift against the current wht engine produces named
failures (stale embedded pack + engine facts not yet honoured). Exit 1 naming
them is the CORRECT state — do not fake green. The KNOWN_DRIFT allowlist below
annotates expected drift with a mandatory expiry; it never suppresses the
exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import rpcommon  # noqa: E402

COVERAGE_DIR = REPO / "coverage"
DEFAULT_SUITE_PATH = REPO.parent / "meridian-compliance-suite"

# Named-failure allowlist (SPEC-LCE §7 risk mitigation): expected engine-drift
# failures with mandatory expiry. Annotation only — the gate still exits 1
# while any entry matches. Expired entries are flagged in the report.
KNOWN_DRIFT = [
    {"case_prefix": "wht.rates-matrix.", "reason": "engine embedded rp-wht-2024 copy predates tax-law-parity fixes (services/directors-fees/winnings/construction rates)",
     "expires": "2026-09-30"},
    {"case_prefix": "wht.carveout.", "reason": "engine E5 TIN-validity gate + stale embedded pack rates",
     "expires": "2026-09-30"},
    {"case_prefix": "wht.no-tin-double.", "reason": "engine doubles passive income when TIN absent (not_in scoping not honoured) — stale embedded pack",
     "expires": "2026-09-30"},
]
HONESTY_BANNER = ("dev ed25519 keypair (prod uses HSM custody) · dev worm:// URIs · "
                  "packs flagged subject_to_regazette may change when CTCs/gazettes land · "
                  "citation_kind: secondary = working citation, not a gazette URL (G1)")


def sh(cmd: list[str], cwd: Path = REPO) -> tuple[int, str]:
    env = {**os.environ, "LCE_ATTEST_RUNNING": "1"}  # guards against pytest->attest recursion
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    return res.returncode, (res.stdout + res.stderr)


def git_sha(path: Path) -> str | None:
    if not (path / ".git").exists() and not (path / ".git").is_file():
        # worktrees have a .git file; plain dirs may not
        pass
    code, out = sh(["git", "-C", str(path), "rev-parse", "HEAD"])
    return out.strip() if code == 0 else None


def stage_pack_validity() -> dict:
    code, out = sh([sys.executable, "tools/validate.py"])
    last = out.strip().splitlines()[-1] if out.strip() else ""
    return {"stage": "pack-validity", "ok": code == 0, "detail": last, "exit": code}


def stage_signatures() -> dict:
    verified, failed = 0, []
    for d in sorted((REPO / "packs").iterdir()):
        f = d / "1.0.0.yaml"
        if not f.exists():
            continue
        pack = rpcommon.load_pack_file(f)
        signed = pack.get("signed") or {}
        err = None
        if pack.get("status") != "published":
            err = f"status {pack.get('status')!r} != published"
        elif signed.get("algorithm") != "ed25519":
            err = "signed.algorithm != ed25519"
        else:
            try:
                vk = rpcommon.load_verify_key(signed["key_id"])
                vk.verify(rpcommon.canonical_bytes(pack), bytes.fromhex(signed["signature"]))
            except Exception as e:  # noqa: BLE001
                err = f"signature verify failed: {e}"
        if err:
            failed.append({"pack": d.name, "error": err})
        else:
            verified += 1
    return {"stage": "signatures", "ok": not failed, "verified": verified, "failed": failed}


def stage_coverage() -> dict:
    code, out = sh([sys.executable, "tools/coverage_validate.py", "--format", "json"])
    try:
        data = json.loads(out[: out.rindex("}") + 1])
    except Exception:  # noqa: BLE001
        return {"stage": "coverage", "ok": False, "error": out[-500:]}
    return {"stage": "coverage", "ok": code == 0,
            "violations": data["violations"], "warnings": data["warnings"]}


def stage_conformance_reference() -> dict:
    code, out = sh([sys.executable, "tools/conformance.py", "--mode", "reference", "--format", "json"])
    try:
        data = json.loads(out[out.index("{"):])
    except Exception:  # noqa: BLE001
        return {"stage": "conformance-reference", "ok": False, "error": out[-500:]}
    return {"stage": "conformance-reference", "ok": code == 0,
            "cases": data["cases"], "passed": data["passed"], "results": data["results"]}


def stage_ctc(threshold: float) -> dict:
    """CTC verification coverage (tools/ctc.py --report).

    Honest ratchet: threshold 0.0 (default) is REPORT-ONLY — the section is
    recorded in the attestation but never fails the gate. Set --ctc-threshold
    (e.g. 0.5) to require at least that fraction of sections CTC-verified;
    only then does the stage affect the exit code. Ratchet up as CTCs land.
    """
    code, out = sh([sys.executable, "tools/ctc.py", "--report", "--format", "json"])
    try:
        data = json.loads(out[out.index("{"):])
    except Exception:  # noqa: BLE001
        return {"stage": "ctc-coverage", "ok": False, "error": out[-500:]}
    frac = data["verified_fraction"]
    gated = threshold > 0.0
    ok = (frac >= threshold) if gated else True
    return {"stage": "ctc-coverage", "ok": ok, "threshold": threshold,
            "report_only": not gated, "verified_fraction": frac,
            "totals": data["totals"], "statutes": data["statutes"]}


def stage_pytest() -> dict:
    code, out = sh([sys.executable, "-m", "pytest", "-q", "--tb=no"])
    last = out.strip().splitlines()[-1] if out.strip() else ""
    return {"stage": "pytest-suite", "ok": code == 0, "detail": last, "exit": code}


def stage_engine_drift(engine: str) -> dict:
    code, out = sh([sys.executable, "tools/conformance.py", "--mode", "engine",
                    "--engine", engine, "--format", "json"])
    if code == 2:
        raise Misconfig(out.strip())
    try:
        data = json.loads(out[out.index("{"):])
    except Exception:  # noqa: BLE001
        return {"stage": "engine-drift", "ok": False, "error": out[-500:]}
    today = time.strftime("%Y-%m-%d")
    for m in data["mismatches"]:
        entry = next((k for k in KNOWN_DRIFT if m["id"].startswith(k["case_prefix"])), None)
        m["known_drift"] = bool(entry)
        if entry:
            m["known_drift_reason"] = entry["reason"]
            m["known_drift_expires"] = entry["expires"]
            m["known_drift_expired"] = entry["expires"] < today
    return {"stage": "engine-drift", "ok": code == 0, "engine": engine,
            "cases": data["cases"], "passed": data["passed"],
            "mismatches": data["mismatches"]}


class Misconfig(Exception):
    pass


def load_sections() -> list[dict]:
    secs = []
    for f in sorted(COVERAGE_DIR.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text())
        for s in doc["sections"]:
            secs.append({"statute": doc["statute"]["id"], **s})
    return secs


def rollup(sections, coverage, reference, pytest_stage, drift) -> list[dict]:
    case_fail = {r["id"] for r in reference.get("results", []) if not r["pass"]}
    drift_fail = {m["id"] for m in (drift or {}).get("mismatches", [])}
    violated = set()
    for v in coverage.get("violations", []):
        # violation lines start "<path>:<section_id>: ..."
        try:
            violated.add(v.split(":", 2)[1])
        except IndexError:
            pass
    rows = []
    for s in sections:
        sid = s["section_id"]
        full = f"{s['statute']}:{sid}"
        status = s["status"]
        failures = []
        if status in ("UNIMPLEMENTED", "UNSOURCED"):
            rows.append({"section": full, "status": status, "result": status,
                         "rules": s["implementing_rules"],
                         "cases": s["conformance_tests"], "failures": []})
            continue
        for t in s["conformance_tests"]:
            if t in case_fail:
                failures.append(f"reference case failed: {t}")
            if t in drift_fail:
                failures.append(f"engine drift: {t}")
        if sid in violated:
            failures.append("coverage validator violation touches this section")
        if not pytest_stage["ok"] and any("." not in t for t in s["conformance_tests"]):
            failures.append(f"pytest suite red: {pytest_stage['detail']}")
        result = "PASS" if status == "IMPLEMENTED" and not failures else ("FAIL" if failures else "PASS")
        if status == "PARTIAL" and not failures:
            result = "PASS"  # partial, currently green — notes carry the gap
        rows.append({"section": full, "status": status, "result": result,
                     "rules": s["implementing_rules"],
                     "cases": s["conformance_tests"], "failures": failures})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/attestation", help="output path prefix (.md/.json)")
    ap.add_argument("--engine", help="engine spec for drift stage (e.g. inproc)")
    ap.add_argument("--skip-engine", action="store_true", help="reference-only attestation")
    ap.add_argument("--ctc-threshold", type=float, default=0.0,
                    help="fraction of sections that must be CTC-verified; 0.0 (default) = report-only")
    args = ap.parse_args(argv)

    engine = None if args.skip_engine else (args.engine or os.environ.get("LCE_WHT_ENGINE"))

    stages = []
    stages.append(stage_pack_validity())
    stages.append(stage_signatures())
    coverage = stage_coverage(); stages.append({k: v for k, v in coverage.items() if k in ("stage", "ok")})
    reference = stage_conformance_reference(); stages.append({"stage": "conformance-reference", "ok": reference["ok"]})
    pytest_stage = stage_pytest(); stages.append(pytest_stage)
    ctc = stage_ctc(args.ctc_threshold)
    stages.append({"stage": "ctc-coverage", "ok": ctc["ok"]})
    drift = None
    if engine:
        try:
            drift = stage_engine_drift(engine)
            stages.append({"stage": "engine-drift", "ok": drift["ok"], "engine": engine})
        except Misconfig as e:
            print(f"misconfiguration: {e}", file=sys.stderr)
            return 2

    sections = load_sections()
    rows = rollup(sections, coverage, reference, pytest_stage, drift)
    summary = {
        "pass": sum(1 for r in rows if r["result"] == "PASS"),
        "fail": sum(1 for r in rows if r["result"] == "FAIL"),
        "unimplemented": sum(1 for r in rows if r["result"] == "UNIMPLEMENTED"),
        "unsourced": sum(1 for r in rows if r["result"] == "UNSOURCED"),
    }
    tool_fail = not all(s["ok"] for s in stages)
    exit_code = 1 if (summary["fail"] or coverage.get("violations") or tool_fail) else 0

    regaz = []
    for d in sorted((REPO / "packs").iterdir()):
        f = d / "1.0.0.yaml"
        if f.exists():
            p = rpcommon.load_pack_file(f)
            if p.get("subject_to_regazette"):
                regaz.append(d.name)

    suite_path = Path(os.environ.get("LCE_COMPLIANCE_SUITE_PATH") or DEFAULT_SUITE_PATH)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repos": {"rule_packs": git_sha(REPO),
                  "compliance_suite": git_sha(suite_path) if suite_path.exists() else None},
        "honesty": {"dev_keys": True, "dev_worm": True, "regazette_pending": regaz},
        "stages": stages,
        "signatures": {"verified": stages[1]["verified"], "failed": stages[1]["failed"]},
        "coverage": {"violations": coverage.get("violations", []),
                     "warnings": len(coverage.get("warnings", []))},
        "conformance_reference": {"cases": reference.get("cases"), "passed": reference.get("passed")},
        "ctc": {"threshold": ctc["threshold"], "report_only": ctc["report_only"],
                "verified_fraction": ctc.get("verified_fraction"),
                "totals": ctc.get("totals"), "ok": ctc["ok"]},
        "sections": rows,
        "engine_drift": ({"engine": drift["engine"], "cases": drift["cases"],
                          "mismatches": drift["mismatches"]} if drift else None),
        "summary": summary,
        "exit_code": exit_code,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    out.with_suffix(".md").write_text(render_md(report))
    print(f"attestation written: {out.with_suffix('.md')} / {out.with_suffix('.json')}")
    print(f"summary: {summary} exit={exit_code}")
    if summary["fail"]:
        print("FAIL sections:")
        for r in rows:
            if r["result"] == "FAIL":
                print(f"  - {r['section']}: {'; '.join(r['failures'])[:160]}")
    return exit_code


def render_md(report: dict) -> str:
    L = []
    L.append("# LCE Attestation Report")
    L.append(f"\n- generated_at: {report['generated_at']}")
    L.append(f"- rule_packs sha: `{report['repos']['rule_packs']}`")
    L.append(f"- compliance_suite sha: `{report['repos']['compliance_suite']}`")
    L.append(f"- packs: {report['signatures']['verified']} verified, "
             f"{len(report['signatures']['failed'])} failed")
    L.append(f"\n> **Honesty tags:** {HONESTY_BANNER}")
    L.append(f"- subject_to_regazette packs: {', '.join(report['honesty']['regazette_pending'])}")
    L.append("\n## Stages\n")
    L.append("| stage | result |")
    L.append("|---|---|")
    for s in report["stages"]:
        L.append(f"| {s['stage']} | {'ok' if s['ok'] else 'FAIL'} |")
    L.append("\n## Statute sections\n")
    L.append("| section | status | rules | cases | result |")
    L.append("|---|---|---|---|---|")
    for r in report["sections"]:
        L.append(f"| {r['section']} | {r['status']} | {len(r.get('rules', []))} | "
                 f"{len(r['cases'])} | {r['result']} |")
    ctc = report.get("ctc")
    if ctc:
        t = ctc["totals"]
        mode = "report-only (gate off — ratchet not yet armed)" if ctc["report_only"] else f"threshold {ctc['threshold']:.0%}"
        L.append(f"\n## CTC verification (G1 registry) — {mode}\n")
        L.append(f"verified {t['verified']} / unverified {t['unverified']} / waived {t['waived']} "
                 f"({ctc['verified_fraction']:.0%} verified) — result: {'PASS' if ctc['ok'] else 'FAIL'}")
    ed = report.get("engine_drift")
    if ed:
        L.append(f"\n## Engine drift ({ed['engine']}) — {len(ed['mismatches'])}/{ed['cases']} mismatches\n")
        L.append("| case | reference | engine | ignored facts | known drift |")
        L.append("|---|---|---|---|---|")
        for m in ed["mismatches"]:
            ref = m.get("reference", {})
            eng = m.get("engine") or {}
            kd = (f"yes (expires {m['known_drift_expires']}"
                  f"{' — EXPIRED' if m.get('known_drift_expired') else ''})"
                  if m.get("known_drift") else "no — NEW")
            L.append(f"| {m['id']} | {ref.get('rate_bps')} | {eng.get('rate_bps')} | "
                     f"{', '.join(m.get('ignored_facts', []))} | {kd} |")
    L.append("\n## Summary\n")
    L.append(f"```\n{json.dumps(report['summary'], indent=2)}\n```")
    L.append(f"\nexit_code: **{report['exit_code']}**")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
