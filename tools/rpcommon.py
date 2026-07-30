"""Shared rule-pack helpers: canonicalisation, keys, ULID, event envelope.

Canonical form: the pack mapping *without* the `signed` block, serialised with
yaml.safe_dump(sort_keys=True, allow_unicode=True, default_flow_style=False,
width=10**6) encoded UTF-8. Deterministic across runs and machines.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = REPO_ROOT / "packs"
SCHEMA_PATH = REPO_ROOT / "schemas" / "rulepack.schema.json"
KEYS_DIR = REPO_ROOT / "tools" / "keys"
OUTBOX_DIR = REPO_ROOT / "outbox"
ARCHIVE_DIR = REPO_ROOT / "signatures" / "archive"

DEFAULT_KEY_ID = "governance-board-2026"

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
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: pack document must be a mapping")
    return data


def canonical_bytes(pack: dict) -> bytes:
    """Canonical YAML bytes of everything EXCEPT the `signed` block (SPEC §1.4)."""
    body = {k: v for k, v in pack.items() if k != "signed"}
    return yaml.safe_dump(
        body, sort_keys=True, allow_unicode=True,
        default_flow_style=False, width=10**6,
    ).encode("utf-8")


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def key_paths(key_id: str = DEFAULT_KEY_ID) -> tuple[Path, Path]:
    priv = KEYS_DIR / f"{key_id}.ed25519.private"
    pub = KEYS_DIR / f"{key_id}.ed25519.public"
    return priv, pub


def ensure_dev_keypair(key_id: str = DEFAULT_KEY_ID):
    """Generate the dev ed25519 keypair if absent. Returns (signing_key, verify_key)."""
    from nacl.signing import SigningKey

    priv_path, pub_path = key_paths(key_id)
    if priv_path.exists():
        # keys stored as hex text (git/GitHub friendly)
        sk = SigningKey(bytes.fromhex(priv_path.read_text().strip()))
        return sk, sk.verify_key
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    sk = SigningKey.generate()
    priv_path.write_text(bytes(sk).hex() + "\n")
    os.chmod(priv_path, 0o600)
    pub_path.write_text(bytes(sk.verify_key).hex() + "\n")
    return sk, sk.verify_key


def load_verify_key(key_id: str = DEFAULT_KEY_ID):
    from nacl.signing import VerifyKey

    _, pub_path = key_paths(key_id)
    return VerifyKey(bytes.fromhex(pub_path.read_text().strip()))


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
