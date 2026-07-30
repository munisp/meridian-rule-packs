#!/usr/bin/env python3
"""Validate every rule pack against schemas/rulepack.schema.json and verify
ed25519 signatures (SPEC §1.4 / §6).

Usage:
    python tools/validate.py            # validate all packs
    python tools/validate.py --no-sig   # schema only (drafts)
Exit code 0 = all valid.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema

from rpcommon import (
    ARCHIVE_DIR, PACKS_DIR, SCHEMA_PATH, canonical_bytes, load_pack_file,
    load_verify_key, sha256_hex,
)


def iter_pack_files(packs_dir: Path):
    for pack_dir in sorted(packs_dir.iterdir()):
        if not pack_dir.is_dir():
            continue
        for vf in sorted(pack_dir.glob("*.yaml")):
            yield pack_dir.name, vf


def validate_schema(pack: dict, validator: jsonschema.Draft202012Validator,
                    dir_name: str, file_name: str) -> list[str]:
    errs = [f"{e.json_path or '<root>'}: {e.message}" for e in validator.iter_errors(pack)]
    if pack.get("id") != dir_name:
        errs.append(f"pack id {pack.get('id')!r} != directory {dir_name!r}")
    if pack.get("version") != file_name.removesuffix(".yaml"):
        errs.append(f"pack version {pack.get('version')!r} != file name {file_name!r}")
    # rule id uniqueness
    ids = [r.get("id") for r in pack.get("rules", [])]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errs.append(f"duplicate rule ids: {sorted(dupes)}")
    return errs


def verify_signature(pack: dict) -> str | None:
    """Return error string, or None when signature OK."""
    signed = pack.get("signed")
    if signed is None:
        return "missing signed block (pack not through ceremony)"
    try:
        vk = load_verify_key(signed["key_id"])
    except FileNotFoundError:
        return f"unknown key_id {signed['key_id']!r}"
    try:
        vk.verify(canonical_bytes(pack), bytes.fromhex(signed["signature"]))
    except Exception:
        return "signature verification FAILED"
    return None


def verify_archive_record(pack: dict, rel_path: str) -> str | None:
    rec_path = ARCHIVE_DIR / f"{pack['id']}-{pack['version']}.json"
    if not rec_path.exists():
        return f"missing WORM archive record {rec_path}"
    rec = json.loads(rec_path.read_text())
    file_sha = sha256_hex(Path(rel_path).read_bytes())
    if rec.get("sha256") != file_sha:
        return "archive sha256 mismatch vs pack file"
    if not rec.get("worm_uri", "").startswith("worm://"):
        return "archive record missing worm:// URI"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sig", action="store_true", help="skip signature/archive checks")
    ap.add_argument("--packs-dir", default=str(PACKS_DIR))
    args = ap.parse_args(argv)

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)

    total = bad = 0
    for dir_name, vf in iter_pack_files(Path(args.packs_dir)):
        total += 1
        errors: list[str] = []
        try:
            pack = load_pack_file(vf)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {vf}: unparseable YAML: {e}")
            bad += 1
            continue
        errors += validate_schema(pack, validator, dir_name, vf.name)
        if not args.no_sig:
            for fn in (verify_signature, lambda p: verify_archive_record(p, str(vf))):
                err = fn(pack)
                if err:
                    errors.append(err)
        if errors:
            bad += 1
            print(f"FAIL {vf.relative_to(PACKS_DIR.parent)}")
            for e in errors:
                print(f"   - {e}")
        else:
            print(f"ok   {vf.relative_to(PACKS_DIR.parent)}"
                  + (" (schema only)" if args.no_sig else ""))
    print(f"\n{total - bad}/{total} packs valid" + ("" if args.no_sig else " (schema + signature + archive)"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
