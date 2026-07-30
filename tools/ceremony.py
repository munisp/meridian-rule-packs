#!/usr/bin/env python3
"""Rule-pack governance ceremony (SPEC §9.1): draft -> review -> simulate -> sign -> publish -> WORM archive.

    python tools/ceremony.py <pack-id> <version>     # one pack
    python tools/ceremony.py --all                   # every pack dir/version

Stages
------
1. draft      load packs/<id>/<version>.yaml, validate schema (status must be draft|review|simulation|published)
2. review     board-review checks: provenance complete, subject_to_regazette flag present, provenance review logged
3. simulate   simulation hook: internal smoke evaluation of the rule grammar; or external hook via
              env SIMULATE_HOOK (executable receiving the pack path; non-zero exit aborts)
4. sign       ed25519 over canonical YAML bytes of everything above `signed:` (tools/rpcommon.canonical_bytes),
              dev keypair in tools/keys/ (auto-generated), key_id governance-board-2026
5. publish    status -> published, signed block embedded, pack file rewritten; outbox event
              outbox/nrs.rulepacks.published.v1/<id>-<version>.json per SPEC §1.1 envelope
6. archive    WORM record signatures/archive/<id>-<version>.json (sha256 + worm_uri + event id)

The ceremony is idempotent for already-published packs whose signature verifies.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from rpcommon import (
    ARCHIVE_DIR, DEFAULT_KEY_ID, OUTBOX_DIR, PACKS_DIR, canonical_bytes,
    ensure_dev_keypair, event_envelope, load_pack_file, load_verify_key,
    sha256_hex, write_json,
)

RECOGNISED_KINDS = {"rate_bps", "threshold", "table", "formula", "decision",
                    "rate_multiplier_bps", "deadline_day_of_month", "frequency",
                    # any rule must at least narrate its effect
                    "narrate"}


# ---------------------------------------------------------------- stages
def stage_draft(pack_path: Path) -> dict:
    pack = load_pack_file(pack_path)
    status = pack.get("status")
    if status not in ("draft", "review", "simulation", "published"):
        raise CeremonyError(f"pack status {status!r} cannot enter ceremony")
    return pack


def stage_review(pack: dict) -> list[str]:
    """Governance-board review checks; returns review log lines."""
    log = []
    prov = pack.get("provenance", {})
    for field in ("as_passed", "source_citation"):
        if not prov.get(field):
            raise CeremonyError(f"review: provenance.{field} missing")
    log.append(f"provenance OK (as_gazetted={'set' if prov.get('as_gazetted') else 'null — awaiting CTC'})")
    if pack.get("subject_to_regazette") is not True:
        log.append("NOTE: subject_to_regazette is not true — board must record why")
    else:
        log.append("G1 flag subject_to_regazette=true acknowledged (pack may change on gazette CTC)")
    ids = [r["id"] for r in pack["rules"]]
    if len(ids) != len(set(ids)):
        raise CeremonyError("review: duplicate rule ids")
    log.append(f"{len(ids)} rules, ids unique")
    return log


def stage_simulate(pack: dict, pack_path: Path) -> dict:
    """Simulation hook. External hook via SIMULATE_HOOK, else internal smoke."""
    hook = os.environ.get("SIMULATE_HOOK")
    if hook:
        res = subprocess.run([hook, str(pack_path)], capture_output=True, text=True)
        if res.returncode != 0:
            raise CeremonyError(f"simulation hook failed: {res.stderr.strip()}")
        return {"hook": hook, "output": res.stdout.strip()}
    # internal smoke: grammar sanity + narrative coverage
    total = with_narrate = with_kind = 0
    for r in pack["rules"]:
        total += 1
        if not isinstance(r.get("when"), dict) or not isinstance(r.get("then"), dict):
            raise CeremonyError(f"simulate: rule {r.get('id')} malformed when/then")
        if set(r["then"]) & RECOGNISED_KINDS:
            with_kind += 1
        if r["then"].get("narrate"):
            with_narrate += 1
    if with_kind < total:
        raise CeremonyError(f"simulate: {total - with_kind} rules carry no recognised decision kind")
    return {"hook": "internal-smoke", "rules": total,
            "with_decision_kind": with_kind, "with_narrate": with_narrate}


def stage_sign(pack: dict, key_id: str) -> dict:
    sk, _ = ensure_dev_keypair(key_id)
    sig = sk.sign(canonical_bytes(pack)).signature
    pack["signed"] = {"algorithm": "ed25519", "key_id": key_id, "signature": sig.hex()}
    return pack


def stage_publish(pack: dict, pack_path: Path) -> dict:
    # status was already flipped to "published" BEFORE signing (signature covers status)
    # rewrite pack file: header comment + YAML (signed block now embedded)
    text = yaml.safe_dump(pack, sort_keys=False, allow_unicode=True, width=120,
                          default_flow_style=False)
    header = (f"# {pack['id']} v{pack['version']} — Meridian rule pack (SPEC §1.4 format)\n"
              f"# {pack['title']}\n")
    pack_path.write_text(header + text, encoding="utf-8")
    # verify round-trip signature
    vk = load_verify_key(pack["signed"]["key_id"])
    reloaded = load_pack_file(pack_path)
    vk.verify(canonical_bytes(reloaded), bytes.fromhex(reloaded["signed"]["signature"]))
    event = event_envelope(
        "nrs.rulepacks.published.v1", source="rulepack-ceremony",
        rule_pack_version=f"{pack['id']}@{pack['version']}",
        data={
            "pack_id": pack["id"], "version": pack["version"],
            "title": pack["title"],
            "effective_from": pack["effective_from"],
            "effective_to": pack.get("effective_to"),
            "subject_to_regazette": pack["subject_to_regazette"],
            "status": "published",
            "rule_count": len(pack["rules"]),
            "sha256": sha256_hex(pack_path.read_bytes()),
            "key_id": pack["signed"]["key_id"],
            "provenance": pack["provenance"],
        })
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    ev_path = OUTBOX_DIR / "nrs.rulepacks.published.v1" / f"{pack['id']}-{pack['version']}.json"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(ev_path, event)
    return {"event": event, "event_path": ev_path}


def stage_archive(pack: dict, pack_path: Path, publish_info: dict) -> dict:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    sha = sha256_hex(pack_path.read_bytes())
    rec = {
        "pack_id": pack["id"], "version": pack["version"],
        "sha256": sha,
        "worm_uri": f"worm://meridian-dev-worm/rulepacks/{pack['id']}/{pack['version']}/{sha[:16]}.yaml",
        "archived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "key_id": pack["signed"]["key_id"],
        "signature": pack["signed"]["signature"],
        "publish_event_id": publish_info["event"]["id"],
        "immutable": True,
    }
    rec_path = ARCHIVE_DIR / f"{pack['id']}-{pack['version']}.json"
    write_json(rec_path, rec)
    return {"record": rec, "path": rec_path}


class CeremonyError(Exception):
    pass


def already_published(pack_path: Path, key_id: str) -> bool:
    try:
        pack = load_pack_file(pack_path)
    except Exception:
        return False
    if pack.get("status") != "published" or "signed" not in pack:
        return False
    if pack["signed"].get("key_id") != key_id:
        return False
    try:
        vk = load_verify_key(key_id)
        vk.verify(canonical_bytes(pack), bytes.fromhex(pack["signed"]["signature"]))
    except Exception:
        return False
    return (ARCHIVE_DIR / f"{pack['id']}-{pack['version']}.json").exists()


def run_ceremony(pack_id: str, version: str, key_id: str = DEFAULT_KEY_ID) -> dict:
    pack_path = PACKS_DIR / pack_id / f"{version}.yaml"
    if not pack_path.exists():
        raise CeremonyError(f"pack file not found: {pack_path}")
    if already_published(pack_path, key_id):
        return {"pack": f"{pack_id}@{version}", "result": "already-published (signature verifies, archive present)"}

    pack = stage_draft(pack_path)
    review_log = stage_review(pack)
    sim = stage_simulate(pack, pack_path)
    pack["status"] = "published"  # flip BEFORE signing so the signature covers final content
    pack = stage_sign(pack, key_id)
    pub = stage_publish(pack, pack_path)
    arc = stage_archive(pack, pack_path, pub)
    return {
        "pack": f"{pack_id}@{version}", "result": "published",
        "review": review_log, "simulation": sim,
        "event_id": pub["event"]["id"],
        "worm_uri": arc["record"]["worm_uri"],
        "sha256": arc["record"]["sha256"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pack_id", nargs="?", help="pack id e.g. rp-wht-2024")
    ap.add_argument("version", nargs="?", help="version e.g. 1.0.0")
    ap.add_argument("--all", action="store_true", help="run ceremony for every pack")
    ap.add_argument("--key-id", default=DEFAULT_KEY_ID)
    args = ap.parse_args(argv)

    targets: list[tuple[str, str]] = []
    if args.all:
        for d in sorted(PACKS_DIR.iterdir()):
            if d.is_dir():
                for vf in sorted(d.glob("*.yaml")):
                    targets.append((d.name, vf.name.removesuffix(".yaml")))
    elif args.pack_id and args.version:
        targets.append((args.pack_id, args.version))
    else:
        ap.error("give <pack-id> <version> or --all")

    failures = 0
    for pid, ver in targets:
        try:
            res = run_ceremony(pid, ver, args.key_id)
            print(f"[ceremony] {res['pack']}: {res['result']}")
            if res["result"] == "published":
                print(f"           event={res['event_id']} worm={res['worm_uri']}")
        except CeremonyError as e:
            failures += 1
            print(f"[ceremony] {pid}@{ver}: FAILED — {e}", file=sys.stderr)
    print(f"\n[ceremony] {len(targets) - failures}/{len(targets)} packs published")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
