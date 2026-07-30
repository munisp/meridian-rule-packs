"""Tests for the governance ceremony pipeline."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import rpcommon  # noqa: E402
from rpcommon import ARCHIVE_DIR, OUTBOX_DIR, PACKS_DIR, load_pack_file, load_verify_key, canonical_bytes  # noqa: E402
from ceremony import (  # noqa: E402
    CeremonyError, run_ceremony, stage_review, stage_simulate,
)

KEY_DIR = rpcommon.KEYS_DIR


def test_dev_keypair_exists_with_governance_key_id():
    priv, pub = rpcommon.key_paths("governance-board-2026")
    assert priv.exists() and pub.exists()
    assert len(bytes.fromhex(pub.read_text().strip())) == 32  # hex-encoded ed25519 key


def test_all_packs_published_with_archive_and_event():
    for vf in sorted(PACKS_DIR.glob("rp-*/*.yaml")):
        pid, ver = vf.parent.name, vf.name.removesuffix(".yaml")
        rec = ARCHIVE_DIR / f"{pid}-{ver}.json"
        ev = OUTBOX_DIR / "nrs.rulepacks.published.v1" / f"{pid}-{ver}.json"
        assert rec.exists(), f"missing archive record for {pid}"
        assert ev.exists(), f"missing outbox event for {pid}"
        recd = json.loads(rec.read_text())
        assert recd["worm_uri"].startswith("worm://")
        assert recd["immutable"] is True
        assert len(recd["sha256"]) == 64


def test_outbox_event_envelope_shape():
    ev = json.loads((OUTBOX_DIR / "nrs.rulepacks.published.v1" / "rp-wht-2024-1.0.0.json").read_text())
    for field in ("id", "type", "source", "time", "tenant_id", "trace_id", "rule_pack_version", "data"):
        assert field in ev, f"envelope missing {field}"
    assert ev["type"] == "nrs.rulepacks.published.v1"
    assert ev["rule_pack_version"] == "rp-wht-2024@1.0.0"
    assert ev["data"]["pack_id"] == "rp-wht-2024"
    assert ev["data"]["subject_to_regazette"] is True
    assert ev["data"]["rule_count"] >= 3


def test_ceremony_idempotent():
    res = run_ceremony("rp-vat-rates", "1.0.0")
    assert "already-published" in res["result"]


def test_signature_covers_status_published():
    pack = load_pack_file(PACKS_DIR / "rp-education-ng" / "1.0.0.yaml")
    assert pack["status"] == "published"
    vk = load_verify_key(pack["signed"]["key_id"])
    vk.verify(canonical_bytes(pack), bytes.fromhex(pack["signed"]["signature"]))


def test_review_rejects_missing_provenance():
    bad = {"id": "rp-x", "provenance": {"as_passed": "", "as_gazetted": None, "source_citation": ""},
           "subject_to_regazette": True, "rules": [{"id": "a.b"}]}
    with pytest.raises(CeremonyError):
        stage_review(bad)


def test_simulate_rejects_rule_without_kind():
    bad = {"rules": [{"id": "x.y", "when": {"a": 1}, "then": {"unrelated_key": True}}]}
    with pytest.raises(CeremonyError):
        stage_simulate(bad, Path("/dev/null"))


def test_ceremony_fresh_pack(tmp_path, monkeypatch):
    """End-to-end: draft a new pack in a sandboxed packs dir and run the ceremony."""
    import shutil
    import ceremony as cer

    fake_root = tmp_path
    (fake_root / "packs" / "rp-test-pack").mkdir(parents=True)
    draft = {
        "id": "rp-test-pack", "version": "9.9.9", "title": "Test pack",
        "effective_from": "2026-01-01", "effective_to": None, "status": "draft",
        "subject_to_regazette": True,
        "provenance": {"as_passed": "x", "as_gazetted": None, "source_citation": "y"},
        "rules": [{"id": "t.one", "when": {"a": 1},
                   "then": {"rate_bps": 100, "narrate": "test"}}],
    }
    import yaml as _yaml
    pack_file = fake_root / "packs" / "rp-test-pack" / "9.9.9.yaml"
    pack_file.write_text(_yaml.safe_dump(draft, sort_keys=False))

    monkeypatch.setattr(cer, "PACKS_DIR", fake_root / "packs")
    monkeypatch.setattr(cer, "OUTBOX_DIR", fake_root / "outbox")
    monkeypatch.setattr(cer, "ARCHIVE_DIR", fake_root / "signatures" / "archive")

    res = cer.run_ceremony("rp-test-pack", "9.9.9")
    assert res["result"] == "published"
    assert res["worm_uri"].startswith("worm://")

    pub = _yaml.safe_load(pack_file.read_text())
    assert pub["status"] == "published"
    assert pub["signed"]["algorithm"] == "ed25519"
    vk = load_verify_key(pub["signed"]["key_id"])
    vk.verify(canonical_bytes(pub), bytes.fromhex(pub["signed"]["signature"]))

    ev = json.loads((fake_root / "outbox" / "nrs.rulepacks.published.v1" / "rp-test-pack-9.9.9.json").read_text())
    assert ev["rule_pack_version"] == "rp-test-pack@9.9.9"
    rec = json.loads((fake_root / "signatures" / "archive" / "rp-test-pack-9.9.9.json").read_text())
    assert rec["sha256"] == res["sha256"]
    # idempotent second run
    assert "already-published" in cer.run_ceremony("rp-test-pack", "9.9.9")["result"]
