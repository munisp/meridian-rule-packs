"""tests for tools/ctc.py + coverage validator CTC rules (SPEC-LCE G1 / W2).

[REAL] registry write/read, validator rules, report counts.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import ctc  # noqa: E402
import coverage_validate as cv  # noqa: E402

COVERAGE_DIR = REPO / "coverage"


@pytest.fixture()
def workdir(tmp_path):
    """Scratch coverage dir so tests never mutate the repo's matrix."""
    d = tmp_path / "coverage"
    d.mkdir()
    shutil.copy(COVERAGE_DIR / "wht-regs-2024.yaml", d / "wht-regs-2024.yaml")
    return d


def run_ctc(coverage_dir, *args):
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "ctc.py"), "--coverage-dir", str(coverage_dir),
         *args], cwd=REPO, capture_output=True, text=True)


def read_section(coverage_dir, statute, section_id):
    doc = yaml.safe_load((coverage_dir / f"{statute}.yaml").read_text())
    return next(s for s in doc["sections"] if s["section_id"] == section_id)


# ------------------------------------------------------------- record-verification

def test_record_verification_writes_ctc_block(workdir, tmp_path):
    doc = tmp_path / "gazette.pdf"
    doc.write_bytes(b"gazette bytes")
    digest = hashlib.sha256(b"gazette bytes").hexdigest()
    res = run_ctc(workdir, "record-verification", "--statute", "wht-regs-2024",
                  "--section", "first-schedule.dividend", "--document", str(doc),
                  "--verifier", "A. Counsel", "--date", "2026-08-14")
    assert res.returncode == 0, res.stderr + res.stdout
    sec = read_section(workdir, "wht-regs-2024", "first-schedule.dividend")
    assert sec["ctc"] == {"status": "verified", "verifier": "A. Counsel",
                          "verified_at": "2026-08-14", "doc_sha256": digest,
                          "source_doc": str(doc)}
    assert sec["citation_kind"] == "primary"  # flipped once CTC-backed


def test_record_verification_is_idempotent(workdir, tmp_path):
    doc = tmp_path / "g.pdf"
    doc.write_bytes(b"v1")
    for _ in range(2):
        res = run_ctc(workdir, "record-verification", "--statute", "wht-regs-2024",
                      "--section", "reg-3.deduction-trigger", "--document", str(doc),
                      "--verifier", "B. Counsel")
        assert res.returncode == 0
    raw = (workdir / "wht-regs-2024.yaml").read_text()
    assert raw.count("    ctc:") == 1  # replaced, not duplicated
    assert "verification event" not in raw  # no stray content


def test_record_verification_preserves_comments(workdir, tmp_path):
    before = (workdir / "wht-regs-2024.yaml").read_text()
    doc = tmp_path / "g.pdf"
    doc.write_bytes(b"x")
    run_ctc(workdir, "record-verification", "--statute", "wht-regs-2024",
            "--section", "reg-8.no-tin-double-rate", "--document", str(doc),
            "--verifier", "C. Counsel")
    after = (workdir / "wht-regs-2024.yaml").read_text()
    assert before.split("sections:")[0] == after.split("sections:")[0]  # header comments intact


def test_record_missing_document_errors(workdir):
    res = run_ctc(workdir, "record-verification", "--statute", "wht-regs-2024",
                  "--section", "first-schedule.dividend", "--document", "/nope.pdf",
                  "--verifier", "X")
    assert res.returncode == 2
    assert "document not found" in res.stderr


def test_record_unknown_section_errors(workdir, tmp_path):
    doc = tmp_path / "g.pdf"
    doc.write_bytes(b"x")
    res = run_ctc(workdir, "record-verification", "--statute", "wht-regs-2024",
                  "--section", "no.such.section", "--document", str(doc), "--verifier", "X")
    assert res.returncode != 0


def test_waive_records_waiver(workdir):
    res = run_ctc(workdir, "waive", "--statute", "wht-regs-2024",
                  "--section", "first-schedule.treaty-relief", "--verifier", "A. Counsel",
                  "--reason", "treaty table not yet gazetted", "--date", "2026-08-14")
    assert res.returncode == 0
    sec = read_section(workdir, "wht-regs-2024", "first-schedule.treaty-relief")
    assert sec["ctc"]["status"] == "waived"
    assert sec["ctc"]["doc_sha256"] is None
    assert sec["citation_kind"] == "secondary"  # waivers do not flip the citation kind


# ------------------------------------------------------------- validator rules

def test_validator_verified_requires_all_fields():
    sec = {"ctc": {"status": "verified", "verifier": None, "verified_at": "2026-08-14",
                   "doc_sha256": "a" * 64, "source_doc": "gazette.pdf"}}
    v, w = cv.validate_ctc(sec, "loc")
    assert any("verifier" in x for x in v)


def test_validator_verified_missing_hash_warns_not_fails():
    sec = {"ctc": {"status": "verified", "verifier": "A", "verified_at": "2026-08-14",
                   "doc_sha256": None, "source_doc": "gazette.pdf"}}
    v, w = cv.validate_ctc(sec, "loc")
    assert v == []
    assert any("doc_sha256" in x for x in w)


def test_validator_complete_verified_clean():
    sec = {"ctc": {"status": "verified", "verifier": "A", "verified_at": "2026-08-14",
                   "doc_sha256": "a" * 64, "source_doc": "gazette.pdf"}}
    assert cv.validate_ctc(sec, "loc") == ([], [])


def test_validator_absent_ctc_is_silent():
    assert cv.validate_ctc({}, "loc") == ([], [])


def test_schema_accepts_ctc_block(workdir, tmp_path):
    """Round-trip through record-verification keeps the file schema-valid."""
    doc = tmp_path / "g.pdf"
    doc.write_bytes(b"x")
    res = run_ctc(workdir, "record-verification", "--statute", "wht-regs-2024",
                  "--section", "first-schedule.rent", "--document", str(doc),
                  "--verifier", "A. Counsel")
    assert res.returncode == 0  # revalidate inside ctc.py enforces schema


# ------------------------------------------------------------- report counts

def test_report_counts(workdir, tmp_path):
    doc = tmp_path / "g.pdf"
    doc.write_bytes(b"x")
    run_ctc(workdir, "record-verification", "--statute", "wht-regs-2024",
            "--section", "first-schedule.dividend", "--document", str(doc), "--verifier", "A")
    run_ctc(workdir, "waive", "--statute", "wht-regs-2024",
            "--section", "first-schedule.treaty-relief", "--verifier", "A", "--reason", "r")
    res = run_ctc(workdir, "--report", "--format", "json")
    data = json.loads(res.stdout)
    wht = data["statutes"]["wht-regs-2024"]
    assert wht["verified"] == 1 and wht["waived"] == 1
    assert wht["unverified"] == wht["sections"] - 2
    assert data["totals"]["verified"] == 1


def test_report_baseline_all_unverified():
    """Repo baseline: every section unverified (honest starting point)."""
    data = ctc.report(COVERAGE_DIR)
    assert data["totals"]["verified"] == 0
    assert data["totals"]["unverified"] == sum(data["totals"].values())
    assert data["totals"]["unverified"] == 61


# ------------------------------------------------------------- attest integration

# attest.py shells the full pytest suite; these tests would recurse without the guard.
ATTEST_GUARD = pytest.mark.skipif(os.environ.get("LCE_ATTEST_RUNNING") == "1",
                                  reason="inside attestation pipeline (recursion guard)")


@ATTEST_GUARD
def test_attest_includes_ctc_section(tmp_path):
    out = tmp_path / "att"
    res = subprocess.run([sys.executable, str(REPO / "tools" / "attest.py"),
                          "--skip-engine", "--out", str(out)],
                         cwd=REPO, capture_output=True, text=True)
    report = json.loads(Path(str(out) + ".json").read_text())
    assert report["ctc"]["report_only"] is True
    assert report["ctc"]["ok"] is True
    assert report["ctc"]["totals"]["unverified"] == 61
    assert "CTC verification" in Path(str(out) + ".md").read_text()
    # report-only CTC must not change the exit code
    assert res.returncode == report["exit_code"]


@ATTEST_GUARD
def test_attest_ctc_threshold_fails_when_armed(tmp_path):
    out = tmp_path / "att"
    res = subprocess.run([sys.executable, str(REPO / "tools" / "attest.py"),
                          "--skip-engine", "--ctc-threshold", "0.5", "--out", str(out)],
                         cwd=REPO, capture_output=True, text=True)
    report = json.loads(Path(str(out) + ".json").read_text())
    assert report["ctc"]["ok"] is False  # 0% verified < 50% threshold
    assert res.returncode == 1
