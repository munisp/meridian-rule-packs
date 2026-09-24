"""Perf-related regression tests: canonical byte-equality and refmatch cache."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import refmatch  # noqa: E402
from rpcommon import PACKS_DIR, canonical_bytes, load_pack_file  # noqa: E402

PACK_FILES = sorted(PACKS_DIR.glob("*/*.yaml"))


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_canonical_bytes_c_dumper_byte_identical(path):
    """Signature validity depends on canonical bytes; CSafeDumper output must be
    byte-identical to the pure-Python SafeDumper output for every pack."""
    pack = load_pack_file(path)
    body = {k: v for k, v in pack.items() if k != "signed"}
    pure = yaml.dump(body, Dumper=yaml.SafeDumper, sort_keys=True,
                     allow_unicode=True, default_flow_style=False,
                     width=10**6).encode("utf-8")
    assert canonical_bytes(pack) == pure


def test_refmatch_load_cached_and_latest():
    refmatch.load.cache_clear()
    p1 = refmatch.load("rp-wht-2024")
    p2 = refmatch.load("rp-wht-2024")
    assert p1 is p2  # lru_cache: no re-parse per call
    # hardcoded 1.0.0 regression: default is the latest shipped version
    assert p1["version"] == refmatch._latest_version("rp-wht-2024")
    assert refmatch.load("rp-wht-2024", "1.0.0")["version"] == "1.0.0"
