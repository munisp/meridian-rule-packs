#!/usr/bin/env python3
"""CTC (Certified True Copy) citation-verification registry (SPEC-LCE G1 / W2).

Every coverage citation starts life as `citation_kind: secondary` (working
citation — firm commentary, not a gazette URL). This tool records the
verification event when counsel sights a CTC/gazette document: it sha256-hashes
the provided document, writes the `ctc:` block into the section of the right
coverage file, flips `citation_kind` to `primary`, and re-validates.

Usage:
    python tools/ctc.py record-verification --statute wht-regs-2024 \
        --section first-schedule.dividend --document gazette-106-2024.pdf \
        --verifier "A. Counsel, External Chambers" [--date 2026-08-14]
    python tools/ctc.py waive --statute nta-2025 --section presumptive-framework \
        --verifier "A. Counsel" --reason "no CTC issued; UNSOURCED row"
    python tools/ctc.py --report [--format json]

Registry semantics (enforced by tools/coverage_validate.py FAIL 7 / WARN):
  - verified requires verifier, verified_at, doc_sha256, source_doc;
  - verified without doc_sha256 warns (re-record to pin the hash);
  - absent ctc block == unverified.

Honesty: the sha256 pins exactly the document counsel sighted. It does NOT
prove the document is a genuine gazette CTC — that judgement is the verifier's
and is recorded by name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
COVERAGE_DIR = REPO / "coverage"

CTC_FIELDS = ("status", "verifier", "verified_at", "doc_sha256", "source_doc")


# ---------------------------------------------------------------- file editing

def coverage_path(coverage_dir: Path, statute: str) -> Path:
    p = coverage_dir / f"{statute}.yaml"
    if not p.exists():
        raise SystemExit(f"no coverage file: {p}")
    return p


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    dumped = yaml.safe_dump(v, default_flow_style=True).strip()
    if dumped.endswith("\n..."):
        dumped = dumped[: -len("\n...")].strip()
    return dumped


def _section_span(lines: list[str], section_id: str) -> tuple[int, int]:
    """Line span [start, end) of a '- section_id: <id>' block in a coverage file."""
    start = None
    for i, ln in enumerate(lines):
        m = re.match(r"^  - section_id: (\S+)\s*$", ln)
        if m:
            if start is not None:
                return start, i
            if m.group(1) == section_id:
                start = i
    if start is None:
        raise SystemExit(f"section {section_id!r} not found in coverage file")
    return start, len(lines)


def write_ctc_block(path: Path, section_id: str, ctc: dict, flip_citation_kind: bool) -> None:
    """Insert or replace the ctc: block inside one section, preserving the rest
    of the file byte-for-byte (comments included)."""
    lines = path.read_text().splitlines()
    start, end = _section_span(lines, section_id)

    # locate existing ctc block within the section
    block = None
    for i in range(start, end):
        if re.match(r"^    ctc:\s*$", lines[i]):
            j = i + 1
            while j < end and re.match(r"^      \S", lines[j]):
                j += 1
            block = (i, j)
            break

    ctc_lines = ["    ctc:"] + [f"      {k}: {_yaml_scalar(ctc.get(k))}" for k in CTC_FIELDS]

    if block:
        lines[block[0]:block[1]] = ctc_lines
    else:
        lines[end:end] = ctc_lines  # append at end of the section block
        end += len(ctc_lines)

    if flip_citation_kind:
        # recompute span after edit
        start, end = _section_span(lines, section_id)
        for i in range(start, end):
            if re.match(r"^    citation_kind: secondary\s*$", lines[i]):
                lines[i] = "    citation_kind: primary"
    path.write_text("\n".join(lines) + "\n")


def revalidate(statute: str, coverage_dir: Path) -> None:
    """Re-run the coverage validator on the touched statute; die on violations."""
    if coverage_dir == COVERAGE_DIR:
        res = subprocess.run([sys.executable, str(REPO / "tools" / "coverage_validate.py"),
                              "--statute", statute], cwd=REPO, capture_output=True, text=True)
        sys.stdout.write(res.stdout)
        if res.returncode != 0:
            raise SystemExit("coverage validation FAILED after ctc write — fix before committing")
    else:
        # custom coverage dir (tests): schema + ctc rules only, in-process
        sys.path.insert(0, str(REPO / "tools"))
        import jsonschema

        import coverage_validate as cv
        schema = json.loads((REPO / "schemas" / "coverage.schema.json").read_text())
        doc = yaml.safe_load((coverage_dir / f"{statute}.yaml").read_text())
        errs = list(jsonschema.Draft202012Validator(schema).iter_errors(doc))
        if errs:
            raise SystemExit(f"schema violation after ctc write: {errs[0].message}")
        for sec in doc["sections"]:
            v, _ = cv.validate_ctc(sec, sec["section_id"])
            if v:
                raise SystemExit(f"ctc rule violation after write: {v[0]}")


# ---------------------------------------------------------------- commands

def cmd_record(args, coverage_dir: Path) -> int:
    doc_path = Path(args.document)
    if not doc_path.exists():
        print(f"document not found: {doc_path}", file=sys.stderr)
        return 2
    digest = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    when = args.date or date.today().isoformat()
    ctc = {"status": "verified", "verifier": args.verifier, "verified_at": when,
           "doc_sha256": digest, "source_doc": str(doc_path)}
    path = coverage_path(coverage_dir, args.statute)
    write_ctc_block(path, args.section, ctc, flip_citation_kind=True)
    print(f"recorded verification: {args.statute}:{args.section}")
    print(f"  doc_sha256={digest}  verifier={args.verifier}  verified_at={when}")
    print("  citation_kind flipped secondary -> primary (citation now CTC-backed)")
    revalidate(args.statute, coverage_dir)
    return 0


def cmd_waive(args, coverage_dir: Path) -> int:
    when = args.date or date.today().isoformat()
    ctc = {"status": "waived", "verifier": args.verifier, "verified_at": when,
           "doc_sha256": None, "source_doc": args.reason}
    path = coverage_path(coverage_dir, args.statute)
    write_ctc_block(path, args.section, ctc, flip_citation_kind=False)
    print(f"recorded waiver: {args.statute}:{args.section} ({args.reason})")
    revalidate(args.statute, coverage_dir)
    return 0


def report(coverage_dir: Path) -> dict:
    per_statute = {}
    totals = {"verified": 0, "unverified": 0, "waived": 0}
    for f in sorted(coverage_dir.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text())
        counts = {"verified": 0, "unverified": 0, "waived": 0}
        for sec in doc["sections"]:
            status = (sec.get("ctc") or {}).get("status", "unverified")
            counts[status] += 1
            totals[status] += 1
        per_statute[doc["statute"]["id"]] = {"sections": len(doc["sections"]), **counts}
    return {"statutes": per_statute, "totals": totals,
            "verified_fraction": (totals["verified"] / sum(totals.values()))
            if sum(totals.values()) else 0.0}


def cmd_report(coverage_dir: Path, fmt: str) -> int:
    data = report(coverage_dir)
    if fmt == "json":
        print(json.dumps(data, indent=2))
        return 0
    t = data["totals"]
    print("CTC verification report (G1 registry)")
    print("| statute | sections | verified | unverified | waived |")
    print("|---|---|---|---|---|")
    for sid, c in data["statutes"].items():
        print(f"| {sid} | {c['sections']} | {c['verified']} | {c['unverified']} | {c['waived']} |")
    print(f"\ntotals: {t['verified']} verified / {t['unverified']} unverified / {t['waived']} waived "
          f"({data['verified_fraction']:.0%} verified)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coverage-dir", default=str(COVERAGE_DIR))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    sub = ap.add_subparsers(dest="cmd")

    rec = sub.add_parser("record-verification")
    rec.add_argument("--statute", required=True)
    rec.add_argument("--section", required=True)
    rec.add_argument("--document", required=True, help="gazette/CTC document to hash")
    rec.add_argument("--verifier", required=True)
    rec.add_argument("--date", help="YYYY-MM-DD (default: today)")

    wv = sub.add_parser("waive")
    wv.add_argument("--statute", required=True)
    wv.add_argument("--section", required=True)
    wv.add_argument("--verifier", required=True)
    wv.add_argument("--reason", required=True)
    wv.add_argument("--date", help="YYYY-MM-DD (default: today)")

    args = ap.parse_args(argv)
    coverage_dir = Path(args.coverage_dir)
    if args.report:
        return cmd_report(coverage_dir, args.format)
    if args.cmd == "record-verification":
        return cmd_record(args, coverage_dir)
    if args.cmd == "waive":
        return cmd_waive(args, coverage_dir)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
