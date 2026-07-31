"""I13: turnover-band boundary semantics and N100m threshold alignment tests.

REAL: validates pack YAML on disk. Covers the audit findings:
  * adjacent bands must be contiguous (band[n].max == band[n+1].min)
  * boundary_semantics declared as min_inclusive_max_exclusive so a shared
    boundary value belongs to exactly one band
  * presumptive exit (gte N100m) must align with VAT registration (gte N100m)
  * VAT registration threshold must be date-split (N25m pre-2026, N100m from 2026)
"""
from __future__ import annotations

from pathlib import Path

import yaml

PACKS = Path(__file__).resolve().parent.parent / "packs"


def load(pack_id: str) -> dict:
    return yaml.safe_load((PACKS / pack_id / "1.0.0.yaml").read_text())


def rule(pack: dict, rule_id: str) -> dict:
    for r in pack["rules"]:
        if r["id"] == rule_id:
            return r
    raise AssertionError(f"rule {rule_id} not found in {pack['id']}")


def band_rule(pack: dict, band: str) -> dict:
    for r in pack["rules"]:
        if r.get("when", {}).get("band") == band:
            return r["then"]["threshold"]
    raise AssertionError(f"band {band} not found")


BANDS = ["micro", "small", "lower_medium", "medium", "upper_medium", "large_informal"]


def test_boundary_semantics_declared():
    pack = load("rp-turnover-bands")
    assert pack.get("boundary_semantics") == "min_inclusive_max_exclusive"


def test_bands_contiguous_no_gap_no_overlap():
    pack = load("rp-turnover-bands")
    ths = [band_rule(pack, b) for b in BANDS]
    assert ths[0]["min_kobo"] == 0
    for prev, nxt in zip(ths, ths[1:]):
        assert prev["max_kobo"] == nxt["min_kobo"], (
            f"gap/overlap between bands: {prev} vs {nxt}")


def test_shared_boundary_belongs_to_higher_band():
    """With [min, max) semantics, turnover == boundary lands in the upper band only."""
    pack = load("rp-turnover-bands")
    assert pack["boundary_semantics"] == "min_inclusive_max_exclusive"
    ths = [band_rule(pack, b) for b in BANDS]
    boundary = ths[0]["max_kobo"]  # N1m
    members = [b for b, t in zip(BANDS, ths) if t["min_kobo"] <= boundary < t["max_kobo"]]
    assert members == ["small"]


def test_presumptive_exit_aligns_with_vat_registration():
    """NTAA 2025 s.147: "N100,000,000 or less" is small/exempt — registration and
    presumptive exit trigger only ABOVE N100m (gt), never at exactly N100m."""
    bands = load("rp-turnover-bands")
    vat = load("rp-vat-rates")
    exit_when = rule(bands, "band.exit.register")["when"]["annual_turnover_kobo"]
    vat_thr = rule(vat, "vat.registration.threshold")["then"]["threshold"]["annual_taxable_supplies_kobo"]
    assert exit_when.get("gt") == 10_000_000_000, "presumptive exit must be gt N100m"
    assert vat_thr.get("gt") == 10_000_000_000, "VAT registration must be gt N100m"
    assert "gte" not in vat_thr, "gte at exactly N100m contradicts NTAA s.147"


def test_vat_threshold_date_split():
    vat = load("rp-vat-rates")
    legacy = rule(vat, "vat.registration.threshold-legacy")
    current = rule(vat, "vat.registration.threshold")
    assert legacy["then"]["threshold"]["annual_taxable_supplies_kobo"]["gte"] == 2_500_000_000
    assert legacy.get("effective_to") == "2025-12-31"
    assert current.get("effective_from") == "2026-01-01"


def test_minimum_wage_exemption_is_840k():
    edu = load("rp-education-ng")
    mw = rule(edu, "pit.minimum-wage-exempt")
    assert mw["when"]["annual_income_kobo"]["lte"] == 84_000_000
