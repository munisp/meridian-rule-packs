"""LCE engine-drift suite (SPEC-LCE §2.3/§2.4).

Compares a live WHT engine against the canonical reference matcher for every
wht-area case, one pytest node per case: test_engine_drift[<case-id>].
Skipped unless LCE_WHT_ENGINE is set ("inproc" or an http base URL).
HTTP binding is additionally marked integration.

    LCE_WHT_ENGINE=inproc pytest tests/test_engine_drift.py -q
"""
from __future__ import annotations

import os

import pytest
import conformance

ENGINE = os.environ.get("LCE_WHT_ENGINE", "")

pytestmark = pytest.mark.skipif(not ENGINE, reason="LCE_WHT_ENGINE not set")

WHT_CASES = [c for c in conformance.load_case_sets() if c["area"] == "wht"]


@pytest.fixture(scope="module")
def adapter():
    try:
        return conformance.get_adapter(ENGINE)
    except conformance.AdapterUnavailable as e:
        pytest.skip(f"engine adapter unavailable: {e}")


@pytest.mark.parametrize("case", WHT_CASES, ids=[c["id"] for c in WHT_CASES])
def test_engine_drift(case, adapter):
    if ENGINE.startswith("http"):
        pytest.importorskip("httpx")
    result = conformance.compare_engine(case, adapter)
    assert result["pass"], "; ".join(result["failures"])
