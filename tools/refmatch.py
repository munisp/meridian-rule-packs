"""Canonical reference rule matcher — SINGLE SOURCE OF MATCHING TRUTH.

Extracted verbatim from tests/test_taxlaw_parity.py (branch
feature/tax-law-parity). Both the parity suite and the conformance harness
(tools/conformance.py) import from here so reference-mode semantics can never
drift between the two.

Semantics (SPEC §0, "Inherited conventions"):
  * operators: in / not_in / lte / gte / gt / lt / present; scalar equality;
    None condition matches a None fact.
  * all `when` keys must match;
  * date-gated rules are skipped when no `date` fact is supplied;
  * rule-level effective_from / effective_to date dispatch (inclusive,
    YYYY-MM-DD string compare);
  * resolution is last-match-wins by (precedence, file order) — explicit
    `then.precedence` protects specific categories from generic clobbering
    (audit finding #7).
"""
from __future__ import annotations

from pathlib import Path

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
