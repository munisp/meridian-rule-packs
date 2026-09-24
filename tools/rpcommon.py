"""Shared rule-pack helpers: canonicalisation, keys, ULID, event envelope.

Canonical form: the pack mapping *without* the `signed` block, serialised with
yaml.safe_dump(sort_keys=True, allow_unicode=True, default_flow_style=False,
width=10**6) encoded UTF-8. Deterministic across runs and machines.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import yaml

# libyaml-backed loader/dumper are ~5-8x faster and produce identical results
# for the data shapes used here; fall back to the pure-Python ones when the C
# extension is unavailable. canonical_bytes() byte-equality between SafeDumper
# and CSafeDumper is verified for all shipped packs (tests/test_canonical_bytes.py).
_CSafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_CSafeDumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = REPO_ROOT / "packs"
SCHEMA_PATH = REPO_ROOT / "schemas" / "rulepack.schema.json"
KEYS_DIR = REPO_ROOT / "tools" / "keys"
OUTBOX_DIR = REPO_ROOT / "outbox"
ARCHIVE_DIR = REPO_ROOT / "signatures" / "archive"

# R4-9a rotation (2026-09-18): governance-board-2026 is REVOKED/burned (dev
# private key was committed; see tools/keys/README.md and revoked-keys.json).
# All packs re-signed under governance-board-2026-r2.
DEFAULT_KEY_ID = "governance-board-2026-r2"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(now_ms: int | None = None) -> str:
    """Minimal ULID (48-bit time ms + 80-bit randomness, Crockford base32)."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rand = secrets.randbits(80)
    chars = []
    for shift in range(45, -1, -5):  # 10 time chars
        chars.append(_CROCKFORD[(now_ms >> shift) & 0x1F])
    for shift in range(75, -1, -5):  # 16 random chars
        chars.append(_CROCKFORD[(rand >> shift) & 0x1F])
    return "".join(chars)


def trace_id() -> str:
    return secrets.token_hex(16)


def load_pack_file(path: os.PathLike | str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f, Loader=_CSafeLoader)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: pack document must be a mapping")
    return data


def canonical_bytes(pack: dict) -> bytes:
    """Canonical YAML bytes of everything EXCEPT the `signed` block (SPEC §1.4)."""
    body = {k: v for k, v in pack.items() if k != "signed"}
    return yaml.dump(
        body, Dumper=_CSafeDumper, sort_keys=True, allow_unicode=True,
        default_flow_style=False, width=10**6,
    ).encode("utf-8")


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def key_paths(key_id: str = DEFAULT_KEY_ID) -> tuple[Path, Path]:
    priv = KEYS_DIR / f"{key_id}.ed25519.private"
    pub = KEYS_DIR / f"{key_id}.ed25519.public"
    return priv, pub


def ensure_dev_keypair(key_id: str = DEFAULT_KEY_ID):
    """Load the dev ed25519 keypair; generate one only for a pristine key_id.

    FAIL CLOSED: if the public key exists but the private key is missing, the
    key_id is burned/rotated (see tools/keys/README.md). Silently generating a
    new private key under the same key_id would fork the trust root, so we raise
    instead. Rotate via the documented ceremony in GOVERNANCE.md under a NEW
    key_id.
    """
    from nacl.signing import SigningKey

    priv_path, pub_path = key_paths(key_id)
    if priv_path.exists():
        # keys stored as hex text (git/GitHub friendly)
        sk = SigningKey(bytes.fromhex(priv_path.read_text().strip()))
        return sk, sk.verify_key
    if pub_path.exists():
        raise RuntimeError(
            f"signing key_id {key_id!r} has a public key but no private key — "
            "the key is burned/rotated; refusing to silently regenerate under "
            "the same key_id (see tools/keys/README.md, GOVERNANCE.md)"
        )
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    sk = SigningKey.generate()
    priv_path.write_text(bytes(sk).hex() + "\n")
    os.chmod(priv_path, 0o600)
    pub_path.write_text(bytes(sk.verify_key).hex() + "\n")
    return sk, sk.verify_key


@functools.lru_cache(maxsize=None)
def _verify_key_cached(keys_dir: str, key_id: str):
    from nacl.signing import VerifyKey

    pub_path = Path(keys_dir) / f"{key_id}.ed25519.public"
    return VerifyKey(bytes.fromhex(pub_path.read_text().strip()))


def load_verify_key(key_id: str = DEFAULT_KEY_ID):
    # Cache per KEYS_DIR so sandboxed key dirs (tests/ceremony) never see a
    # stale key cached for the real key dir.
    return _verify_key_cached(str(KEYS_DIR), key_id)


def event_envelope(event_type: str, source: str, data: dict,
                   rule_pack_version: str = "", tenant_id: str = "") -> dict:
    """SPEC §1.1 event envelope."""
    return {
        "id": ulid(),
        "type": event_type,
        "source": source,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tenant_id": tenant_id,
        "trace_id": trace_id(),
        "rule_pack_version": rule_pack_version,
        "data": data,
    }


def write_json(path: os.PathLike | str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")
