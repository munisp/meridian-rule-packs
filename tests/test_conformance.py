"""LCE conformance — reference mode (SPEC-LCE §2.4).

Runs every declarative case in conformance/cases/ against the packs on disk
via the canonical reference matcher (tools/refmatch.py). Always runs in CI.
Test node ids are test_conformance[<case-id>] so the coverage validator can
resolve conformance_tests entries against them.
"""
from __future__ import annotations

import pytest
import conformance

CASES = conformance.load_case_sets()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_conformance(case):
    result = conformance.compare_reference(case)
    assert result["pass"], "; ".join(result["failures"])
