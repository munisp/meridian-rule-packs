"""Tax-law spec-parity regression tests (2026-07-31 audit, findings #1-#13).

REAL: evaluates the pack YAML on disk through a reference rule matcher that
mirrors the Meridian engine semantics (all `when` facts must match; operators
in/not_in/lte/gte/gt/lt/present/null; last-match-wins with explicit
`precedence` so specific categories cannot be clobbered by generic ones;
rule-level effective_from/effective_to date dispatch).

Covers:
  T1/T2  WHT directors' fees 15% resident / 20% non-resident; winnings 5%/15%
  T3     generic "other services" 2% resident split from professional fees 5%;
         royalty NR individual 5%; construction NR 5%/10%, resident other 5%
  T4     legacy pre-2026 CIT 0/20/30% effective-dated <= 2025-12-31
  T5     small-company carve-out: payer small AND txn <= N2m/month AND supplier TIN
  T6     medical services & tuition exempt -> zero-rated from 2026-01-01 (NTA s.187)
  T7     no-TIN double rate scoped to active income only
  T8     VAT registration threshold gt N100m (NTAA s.147 "N100m or less" exempt)
  T9     fiscalisation penalty vs access-refusal penalty split
  T10    full WHT rate matrix table test vs the audit's citation list
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PACKS = Path(__file__).resolve().parent.parent / "packs"


def load(pack_id: str) -> dict:
    return yaml.safe_load((PACKS / pack_id / "1.0.0.yaml").read_text())


# ---------------------------------------------------------------- mini engine
def _match_value(cond, fact) -> bool:
    if isinstance(cond, dict):
        for op, operand in cond.items():
            if op == "in":
                if fact not in operand:
                    return False
            elif op == "not_in":
                if fact in operand:
                    return False
            elif op == "lte":
                if fact is None or not fact <= operand:
                    return False
            elif op == "gte":
                if fact is None or not fact >= operand:
                    return False
            elif op == "gt":
                if fact is None or not fact > operand:
                    return False
            elif op == "lt":
                if fact is None or not fact < operand:
                    return False
            elif op == "present":
                if (fact is not None) != bool(operand):
                    return False
            else:
                raise AssertionError(f"unknown operator {op}")
        return True
    if cond is None:
        return fact is None
    return fact == cond


def rule_active(rule: dict, date: str | None) -> bool:
    if date is None:
        return True
    ef, et = rule.get("effective_from"), rule.get("effective_to")
    if ef and date < ef:
        return False
    if et and date > et:
        return False
    return True


def matching_rules(pack: dict, facts: dict) -> list[dict]:
    """Matching rules in file order; effective-date dispatched on facts['date']."""
    date = facts.get("date")
    out = []
    for r in pack["rules"]:
        if not rule_active(r, date):
            continue
        when = r.get("when", {})
        if "date" in when and "date" not in facts:
            continue  # date-gated rules need a date fact
        if all(_match_value(c, facts.get(k)) for k, c in when.items()):
            out.append(r)
    return out


def resolve(pack: dict, facts: dict, kind: str = "rate_bps"):
    """Last-match-wins by (precedence, file order) — explicit precedence means a
    specific category rule always beats a generic one (audit finding #7)."""
    cands = [(i, r) for i, r in enumerate(matching_rules(pack, facts))
             if kind in r.get("then", {})]
    if not cands:
        return None
    cands.sort(key=lambda t: (t[1]["then"].get("precedence", 0), t[0]))
    return cands[-1][1]


def wht_rate_bps(pack: dict, **facts) -> int | None:
    """Behavioral WHT rate: base rate with no-TIN double-rate multiplier applied."""
    rule = resolve(pack, facts)
    if rule is None:
        return None
    rate = rule["then"]["rate_bps"]
    mult = resolve(pack, facts, kind="rate_multiplier_bps")
    if mult is not None:
        rate = rate * mult["then"]["rate_multiplier_bps"] // 10000
    return rate


WHT = load("rp-wht-2024")


# ---------------------------------------------------------------- T1: directors' fees
def test_t1_directors_fees_resident_individual_15pct():
    assert wht_rate_bps(WHT, payment_type="directors_fees", beneficiary="individual",
                        beneficiary_residence="resident", date="2025-06-01",
                        supplier_tin="TIN") == 1500


def test_t1_directors_fees_non_resident_20pct():
    assert wht_rate_bps(WHT, payment_type="directors_fees", beneficiary="individual",
                        beneficiary_residence="non_resident", date="2025-06-01",
                        supplier_tin="TIN") == 2000


# ---------------------------------------------------------------- T2: winnings
def test_t2_winnings_resident_5pct_not_exempt():
    assert wht_rate_bps(WHT, payment_type="winnings", source="lottery",
                        beneficiary="individual", date="2025-01-15",
                        supplier_tin="TIN") == 500


def test_t2_winnings_non_resident_15pct():
    assert wht_rate_bps(WHT, payment_type="winnings", source="gaming",
                        beneficiary="individual", beneficiary_residence="non_resident",
                        date="2025-01-15", supplier_tin="TIN") == 1500


def test_t2_winnings_rules_effective_2024_10_01():
    for rid in ("wht.rate.winnings.resident", "wht.rate.winnings.non-resident"):
        r = next(r for r in WHT["rules"] if r["id"] == rid)
        assert r["effective_from"] == "2024-10-01"


# ---------------------------------------------------------------- T3: services split / royalty / construction
def test_t3_other_services_resident_company_2pct():
    assert wht_rate_bps(WHT, payment_type="services", beneficiary="company",
                        date="2025-03-01", supplier_tin="TIN1") == 200


def test_t3_professional_fees_stay_5pct():
    for pt in ("professional", "consultancy", "technical", "management"):
        assert wht_rate_bps(WHT, payment_type=pt, beneficiary="company",
                            date="2025-03-01", supplier_tin="TIN1") == 500, pt


def test_t3_other_services_non_resident_5pct():
    assert wht_rate_bps(WHT, payment_type="services", beneficiary="company",
                        beneficiary_residence="non_resident",
                        date="2025-03-01", supplier_tin="TIN1") == 500


def test_t3_royalty_non_resident_individual_5pct_not_clobbered():
    """Audit finding #7: last-match-wins used to let the 10% NR rule overwrite 5%."""
    assert wht_rate_bps(WHT, payment_type="royalty", beneficiary="individual",
                        beneficiary_residence="non_resident",
                        date="2025-03-01", supplier_tin="TIN1") == 500


def test_t3_royalty_non_resident_company_10pct():
    assert wht_rate_bps(WHT, payment_type="royalty", beneficiary="company",
                        beneficiary_residence="non_resident",
                        date="2025-03-01", supplier_tin="TIN1") == 1000


def test_t3_construction_matrix():
    core = dict(payment_type="construction", construction_type="roads",
                beneficiary="company", date="2025-03-01", supplier_tin="TIN1")
    other = dict(core, construction_type="other")
    assert wht_rate_bps(WHT, **core) == 200                      # resident core 2%
    assert wht_rate_bps(WHT, **other) == 500                     # resident other 5%
    assert wht_rate_bps(WHT, **core, beneficiary_residence="non_resident") == 500
    assert wht_rate_bps(WHT, **other, beneficiary_residence="non_resident") == 1000


# ---------------------------------------------------------------- T5: small-company carve-out
CARVEOUT_BASE = dict(payment_type="services", beneficiary="company",
                     payer_size="small", payer_annual_turnover_kobo=2_000_000_000,
                     transaction_month_value_kobo=150_000_000,
                     date="2025-03-01")


def test_t5_carveout_granted_with_tin():
    assert wht_rate_bps(WHT, **CARVEOUT_BASE, supplier_tin="SUP-TIN") == 0


def test_t5_carveout_denied_without_supplier_tin():
    """Regression: carve-out was granted with no TIN. Regs require a valid supplier TIN.
    Without a TIN the 0% carve-out must never fire — the plain 2% other-services rate
    applies, and is DOUBLED (active income) under the no-TIN rule."""
    rate = wht_rate_bps(WHT, **CARVEOUT_BASE, supplier_tin=None)
    assert rate != 0, "carve-out must never be granted without a supplier TIN"
    assert rate == 400  # 2% other-services x 2 (no-TIN double rate, active income)


def test_t5_carveout_denied_when_payer_not_small():
    assert wht_rate_bps(WHT, **{**CARVEOUT_BASE, "payer_size": "large",
                                "supplier_tin": "SUP-TIN"}) == 200


def test_t5_carveout_denied_when_transaction_over_2m_month():
    assert wht_rate_bps(WHT, **{**CARVEOUT_BASE,
                                "transaction_month_value_kobo": 200_000_001,
                                "supplier_tin": "SUP-TIN"}) == 200


# ---------------------------------------------------------------- T7: no-TIN double rate scope
def test_t7_no_tin_doubles_active_income():
    assert wht_rate_bps(WHT, payment_type="services", beneficiary="company",
                        date="2025-03-01", supplier_tin=None, supplier_nin=None) == 400


@pytest.mark.parametrize("pt", ["dividend", "interest", "royalty", "rent"])
def test_t7_no_tin_does_not_double_passive_income(pt):
    assert wht_rate_bps(WHT, payment_type=pt, beneficiary="company",
                        date="2025-03-01", supplier_tin=None, supplier_nin=None) == 1000


# ---------------------------------------------------------------- T4: legacy CIT
CIT_LEGACY = load("rp-cit-legacy")
EDU = load("rp-education-ng")


@pytest.mark.parametrize("turnover,expected", [
    (2_000_000_000, 0),      # <= N25m small
    (5_000_000_000, 2000),   # N25m-N100m medium
    (15_000_000_000, 3000),  # > N100m large
])
def test_t4_legacy_cit_rates_for_2025_period(turnover, expected):
    facts = dict(tax="CIT", entity="company", gross_turnover_kobo=turnover, date="2025-06-30")
    assert resolve(CIT_LEGACY, facts)["then"]["rate_bps"] == expected


def test_t4_legacy_cit_pack_effective_window():
    assert CIT_LEGACY["effective_to"] == "2025-12-31"
    for r in CIT_LEGACY["rules"]:
        assert r.get("effective_to") == "2025-12-31"


def test_t4_effective_date_dispatch_legacy_rules_silent_in_2026():
    """2026-dated assessment must NOT match legacy CIT rules; NTA pack applies."""
    facts = dict(tax="CIT", entity="company", gross_turnover_kobo=15_000_000_000, date="2026-06-30")
    assert resolve(CIT_LEGACY, facts) is None
    assert resolve(EDU, dict(tax="CIT", entity="company", size="large",
                             date="2026-06-30"))["then"]["rate_bps"] == 3000


# ---------------------------------------------------------------- T6: VAT baskets
EXEMPT = load("rp-vat-exempt-basket")
ZERO = load("rp-vat-zerorated-basket")


@pytest.mark.parametrize("item,zero_rule", [
    ("medical_services", "vat.zero.medical-services"),
    ("educational_services", "vat.zero.education-tuition"),
])
def test_t6_medical_and_tuition_zero_rated_from_2026(item, zero_rule):
    pre = resolve(EXEMPT, dict(item_class=item, date="2025-06-01"), kind="decision")
    assert pre is not None and pre["then"]["decision"] == "exempt"  # legacy exempt pre-2026
    post_zero = resolve(ZERO, dict(item_class=item, date="2026-06-01"))
    assert post_zero["id"] == zero_rule and post_zero["effective_from"] == "2026-01-01"
    post_exempt = resolve(EXEMPT, dict(item_class=item, date="2026-06-01"), kind="decision")
    assert post_exempt is None


def test_t6_legacy_exempt_rules_expire_2025_12_31():
    for rid in ("vat.exempt.medical-services-legacy", "vat.exempt.education-services-legacy"):
        r = next(r for r in EXEMPT["rules"] if r["id"] == rid)
        assert r["effective_to"] == "2025-12-31"


# ---------------------------------------------------------------- T8: VAT registration threshold
VATR = load("rp-vat-rates")
EXEMPTION = load("rp-exemption-nta")
BANDS = load("rp-turnover-bands")


def _rule(pack, rid):
    return next(r for r in pack["rules"] if r["id"] == rid)


def test_t8_exactly_100m_not_required_to_register():
    thr = _rule(VATR, "vat.registration.threshold")["then"]["threshold"]["annual_taxable_supplies_kobo"]
    assert "gte" not in thr, "NTAA s.147: 'N100m or less' is exempt — gte is the audit bug"
    assert thr["gt"] == 10_000_000_000
    assert (10_000_000_000 > thr["gt"]) is False     # exactly N100m: exempt
    assert (10_000_000_100 > thr["gt"]) is True      # above N100m: register


def test_t8_consistent_with_exemption_pack_and_band_exit():
    small = _rule(EXEMPTION, "exempt.small-company")
    assert small["when"]["gross_turnover_kobo"]["lte"] == 10_000_000_000
    exit_when = _rule(BANDS, "band.exit.register")["when"]["annual_turnover_kobo"]
    assert exit_when["gt"] == 10_000_000_000


# ---------------------------------------------------------------- T9: penalty split
PEN = load("rp-ntaa-penalties")


def test_t9_fiscalisation_and_access_refusal_are_distinct():
    ids = {r["id"] for r in PEN["rules"]}
    assert "pen.fiscalisation.nonuse" in ids
    assert "pen.technology.access-refusal" in ids
    fisc = _rule(PEN, "pen.fiscalisation.nonuse")
    assert fisc["then"]["penalty_kobo"] == 20_000_000  # N200,000
    assert "tax_due" in fisc["then"]["formula"]        # + 100% of tax due + MPR interest
    access = _rule(PEN, "pen.technology.access-refusal")
    tbl = {row["period"]: row["penalty_kobo"] for row in access["then"]["table"]}
    assert tbl == {"first_day": 100_000_000, "each_subsequent_day": 1_000_000}  # N1m + N10k/day
    for r in (fisc, access):
        assert r.get("citation"), f"{r['id']} must carry a citation"


# ---------------------------------------------------------------- T10: full WHT matrix table test
# (payment_type, beneficiary, residence, extra_facts, expected_bps) — from the
# audit's citation list (KPMG First Schedule table / PwC / UUBO / SHQ Legal).
TIN = {"supplier_tin": "TIN-1", "date": "2025-06-01"}
WHT_MATRIX = [
    ("dividend", "company", None, {}, 1000),
    ("dividend", "individual", None, {}, 1000),
    ("dividend", "company", "non_resident", {}, 1000),
    ("interest", "company", None, {}, 1000),
    ("interest", "company", "non_resident", {}, 1000),
    ("rent", "individual", None, {}, 1000),
    ("rent", "company", "non_resident", {}, 1000),
    ("royalty", "company", None, {}, 1000),
    ("royalty", "individual", None, {}, 500),
    ("royalty", "company", "non_resident", {}, 1000),
    ("royalty", "individual", "non_resident", {}, 500),
    ("supply_of_goods_materials", "company", None, {}, 200),
    ("construction", "company", None, {"construction_type": "roads"}, 200),
    ("construction", "company", None, {"construction_type": "other"}, 500),
    ("construction", "company", "non_resident", {"construction_type": "buildings"}, 500),
    ("construction", "company", "non_resident", {"construction_type": "other"}, 1000),
    ("consultancy", "company", None, {}, 500),
    ("professional", "individual", None, {}, 500),
    ("technical", "company", "non_resident", {}, 1000),
    ("management", "company", "non_resident", {}, 1000),
    ("commission", "individual", None, {}, 500),
    ("commission", "company", "non_resident", {}, 1000),
    ("services", "company", None, {}, 200),
    ("services", "individual", None, {}, 200),
    ("services", "company", "non_resident", {}, 500),
    ("directors_fees", "individual", None, {}, 1500),
    ("directors_fees", "individual", "non_resident", {}, 2000),
    ("winnings", "individual", None, {"source": "lottery"}, 500),
    ("winnings", "individual", "non_resident", {"source": "reality_show"}, 1500),
]


@pytest.mark.parametrize("pt,beneficiary,residence,extra,expected", WHT_MATRIX)
def test_t10_wht_rate_matrix_matches_audit_citations(pt, beneficiary, residence, extra, expected):
    facts = dict(TIN, payment_type=pt, beneficiary=beneficiary, **extra)
    if residence:
        facts["beneficiary_residence"] = residence
    assert wht_rate_bps(WHT, **facts) == expected
