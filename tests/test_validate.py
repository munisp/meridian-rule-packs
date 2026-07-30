"""Tests for the rule-pack validator (schema + signature checks)."""
import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from rpcommon import (  # noqa: E402
    PACKS_DIR, SCHEMA_PATH, canonical_bytes, load_pack_file, load_verify_key,
)
from validate import validate_schema, verify_signature  # noqa: E402

VALIDATOR = jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def all_pack_files():
    return sorted(PACKS_DIR.glob("rp-*/*.yaml"))


def test_pack_count_and_names():
    expected = {
        "rp-ubl-bis", "rp-mbs-business-rules", "rp-wht-2024", "rp-tp-2018",
        "rp-etr-nta", "rp-etr-scope", "rp-etr-cfc", "rp-globe-oecd", "rp-gir-schema",
        "rp-carf-schema", "rp-nta-vasp-duties", "rp-nta-digital-assets",
        "rp-sec-vasp-rules", "rp-asset-taxonomy", "rp-vat-rates",
        "rp-vat-exempt-basket", "rp-vat-zerorated-basket", "rp-vat-attribution-mode",
        "rp-platform-collectors", "rp-presumptive-federal", "rp-presumptive-lagos",
        "rp-presumptive-kano", "rp-turnover-bands", "rp-exemption-nta",
        "rp-attribution-formula", "rp-fmt-lagos", "rp-fmt-fct",
        "rp-identity-match-thresholds", "rp-procedure-ombud", "rp-procedure-tat",
        "rp-ntaa-penalties", "rp-deposit-20pct", "rp-education-ng",
        "rp-disclosure-control", "rp-bank-thresholds",
    }
    found = {p.parent.name for p in all_pack_files()}
    assert expected <= found, f"missing packs: {expected - found}"
    assert len(found) >= 35


@pytest.mark.parametrize("path", all_pack_files(), ids=lambda p: p.parent.name)
def test_pack_validates_against_schema(path):
    pack = load_pack_file(path)
    errs = validate_schema(pack, VALIDATOR, path.parent.name, path.name)
    assert errs == []


@pytest.mark.parametrize("path", all_pack_files(), ids=lambda p: p.parent.name)
def test_pack_signature_verifies(path):
    pack = load_pack_file(path)
    assert pack["status"] == "published"
    assert verify_signature(pack) is None


@pytest.mark.parametrize("path", all_pack_files(), ids=lambda p: p.parent.name)
def test_pack_governance_flags(path):
    pack = load_pack_file(path)
    assert pack["subject_to_regazette"] is True
    assert pack["provenance"]["as_passed"]
    assert pack["provenance"]["source_citation"]
    assert "as_gazetted" in pack["provenance"]


@pytest.mark.parametrize("path", all_pack_files(), ids=lambda p: p.parent.name)
def test_rules_have_content(path):
    pack = load_pack_file(path)
    assert len(pack["rules"]) >= 3, "packs must carry rich rule content"
    for r in pack["rules"]:
        assert r["when"], f"rule {r['id']} empty when"
        assert r["then"], f"rule {r['id']} empty then"


def test_tampered_signature_detected():
    path = PACKS_DIR / "rp-wht-2024" / "1.0.0.yaml"
    pack = load_pack_file(path)
    tampered = copy.deepcopy(pack)
    tampered["rules"][0]["then"]["rate_bps"] = 9999
    vk = load_verify_key(tampered["signed"]["key_id"])
    with pytest.raises(Exception):
        vk.verify(canonical_bytes(tampered), bytes.fromhex(tampered["signed"]["signature"]))


def test_schema_rejects_bad_pack():
    bad = {"id": "not-rp", "version": "1", "status": "weird",
           "subject_to_regazette": True, "provenance": {}, "rules": []}
    errs = list(VALIDATOR.iter_errors(bad))
    assert errs, "schema must reject malformed packs"


def test_wht_pack_domain_rules():
    pack = load_pack_file(PACKS_DIR / "rp-wht-2024" / "1.0.0.yaml")
    ids = {r["id"] for r in pack["rules"]}
    for needed in ("wht.no-tin.double-rate", "wht.small-co.carveout",
                   "wht.identity.nin-acceptable", "wht.exempt.direct-debit",
                   "wht.exempt.broker-securities", "wht.trigger.due-date"):
        assert needed in ids, f"missing WHT rule {needed}"
    rates = {r["id"]: r["then"].get("rate_bps") for r in pack["rules"]}
    assert rates["wht.rate.dividend.company"] == 1000
    assert rates["wht.rate.goods-supply.any"] == 200


def test_education_pack_bands():
    pack = load_pack_file(PACKS_DIR / "rp-education-ng" / "1.0.0.yaml")
    bands = {r["id"]: r["then"].get("rate_bps") for r in pack["rules"] if r["id"].startswith("pit.band")}
    assert bands == {"pit.band.1": 0, "pit.band.2": 1500, "pit.band.3": 1800,
                     "pit.band.4": 2100, "pit.band.5": 2300, "pit.band.6": 2500}


def test_yaml_is_loadable_yaml():
    for p in all_pack_files():
        with open(p) as f:
            yaml.safe_load(f)  # must not raise
