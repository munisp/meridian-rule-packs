#!/usr/bin/env python3
"""LCE coverage-matrix validator (SPEC-LCE §1.3).

Validates coverage/*.yaml against schemas/coverage.schema.json and cross-checks
references against packs on disk, collected pytest node ids, and declarative
conformance case ids.

Usage:
    python tools/coverage_validate.py                 # validate all statutes
    python tools/coverage_validate.py --format json   # machine-readable result
    python tools/coverage_validate.py --statute wht-regs-2024

Exit 0 = no violations (warnings printed). Exit 1 = one or more FAIL
conditions, one line per violation.

FAIL conditions (SPEC-LCE §1.3):
  1. implementing_rules references a pack with no packs/<id>/1.0.0.yaml, or a
     rule_id absent from that pack's rules[].
  2. conformance_tests references an id that is neither a collected pytest
     node id nor a declarative case id in conformance/cases/.
  3. A section has empty implementing_rules and status is not
     UNIMPLEMENTED/UNSOURCED.
  4. An implementing_rules-referenced pack rule carries no citation: field.
  5. Schema violation (schemas/coverage.schema.json, draft 2020-12).
  6. Duplicate section_id within a statute file.

WARN (exit 0): status IMPLEMENTED with zero conformance_tests;
citation_kind: secondary on any section (echoes G1 — working citations, not
gazette URLs, until CTCs land).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import yaml

REPO = Path(__file__).resolve().parent.parent
COVERAGE_DIR = REPO / "coverage"
SCHEMA_PATH = REPO / "schemas" / "coverage.schema.json"
PACKS_DIR = REPO / "packs"
CASES_DIR = REPO / "conformance" / "cases"

ACK_STATUSES = {"UNIMPLEMENTED", "UNSOURCED"}


def load_pack_index() -> dict[str, dict]:
    """pack_id -> {"rules": {rule_id: rule}, "missing": bool}"""
    idx: dict[str, dict] = {}
    for d in sorted(PACKS_DIR.iterdir()):
        f = d / "1.0.0.yaml"
        if not f.exists():
            continue
        pack = yaml.safe_load(f.read_text())
        idx[d.name] = {"rules": {r["id"]: r for r in pack.get("rules", [])}}
    return idx


def collect_pytest_ids() -> list[str]:
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO, capture_output=True, text=True)
    return [ln.strip() for ln in res.stdout.splitlines() if "::" in ln]


def collect_case_ids() -> set[str]:
    ids: set[str] = set()
    if not CASES_DIR.exists():
        return ids
    for f in sorted(CASES_DIR.rglob("*.yaml")):
        doc = yaml.safe_load(f.read_text()) or {}
        for case in doc.get("cases", []) or []:
            cid = case.get("id")
            if cid:
                ids.add(cid)
    return ids


def validate_file(path: Path, schema_validator, pack_index, pytest_ids,
                  case_ids) -> tuple[list[str], list[str], dict]:
    violations: list[str] = []
    warnings: list[str] = []
    rel = path.relative_to(REPO)
    doc = yaml.safe_load(path.read_text())

    # FAIL 5 — schema
    errs = sorted(schema_validator.iter_errors(doc), key=lambda e: e.json_path)
    for e in errs:
        violations.append(f"{rel}: schema: {e.json_path or '<root>'}: {e.message}")
    if errs:
        return violations, warnings, {"statute": None, "sections": 0}

    statute_id = doc["statute"]["id"]
    seen: set[str] = set()
    for sec in doc["sections"]:
        sid = sec["section_id"]
        loc = f"{rel}:{sid}"

        # FAIL 6 — duplicate section_id
        if sid in seen:
            violations.append(f"{loc}: duplicate section_id in {rel}")
        seen.add(sid)

        # FAIL 1 — pack/rule references
        for ref in sec["implementing_rules"]:
            pack_id, rule_id = ref.split(":", 1)
            entry = pack_index.get(pack_id)
            if entry is None:
                violations.append(f"{loc}: implementing_rules pack {pack_id!r} has no packs/{pack_id}/1.0.0.yaml")
                continue
            rule = entry["rules"].get(rule_id)
            if rule is None:
                violations.append(f"{loc}: rule {rule_id!r} absent from pack {pack_id!r}")
                continue
            # FAIL 4 — rule citation required
            if not rule.get("citation"):
                violations.append(f"{loc}: rule {ref} carries no citation: field in pack YAML")

        # FAIL 2 — conformance test references
        for t in sec["conformance_tests"]:
            if t in case_ids:
                continue
            if any(t in nid for nid in pytest_ids):
                continue
            violations.append(f"{loc}: conformance_tests id {t!r} not found in pytest collection or conformance/cases/")

        # FAIL 3 — empty rules without acknowledgement
        if not sec["implementing_rules"] and sec["status"] not in ACK_STATUSES:
            violations.append(f"{loc}: empty implementing_rules but status is {sec['status']} "
                              f"(must be UNIMPLEMENTED/UNSOURCED or add rules)")

        # WARN
        if sec["status"] == "IMPLEMENTED" and not sec["conformance_tests"]:
            warnings.append(f"{loc}: IMPLEMENTED with zero conformance_tests")
        if sec.get("citation_kind") == "secondary":
            warnings.append(f"{loc}: citation_kind secondary (working citation, not gazette URL — G1)")

    return violations, warnings, {"statute": statute_id, "sections": len(doc["sections"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--statute", help="validate only this statute id")
    args = ap.parse_args(argv)

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    pack_index = load_pack_index()
    pytest_ids = collect_pytest_ids()
    case_ids = collect_case_ids()

    files = sorted(COVERAGE_DIR.glob("*.yaml"))
    if args.statute:
        files = [f for f in files if f.stem == args.statute]
        if not files:
            print(f"no coverage file for statute {args.statute!r}", file=sys.stderr)
            return 1

    violations: list[str] = []
    warnings: list[str] = []
    statutes: list[dict] = []
    for f in files:
        v, w, meta = validate_file(f, validator, pack_index, pytest_ids, case_ids)
        violations += v
        warnings += w
        statutes.append(meta)

    if args.format == "json":
        print(json.dumps({
            "statutes": statutes,
            "violations": violations,
            "warnings": warnings,
            "ok": not violations,
        }, indent=2))
    else:
        for v in violations:
            print(f"FAIL {v}")
        for w in warnings:
            print(f"WARN {w}")
        print(f"\n{len(statutes)} statutes, {sum(s['sections'] for s in statutes)} sections, "
              f"{len(violations)} violations, {len(warnings)} warnings")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
