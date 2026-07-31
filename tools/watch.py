#!/usr/bin/env python3
"""Legislation watch — content-hash diffing over the watch_sources.yaml registry.

Operationalises docs/LEGISLATION_WATCH.md §1/§4: each enabled source is
fetched (from its local fixture by default; live HTTP only behind --live),
normalised per its parser hint, sha256-hashed, and compared against the
snapshot store in watch_state/. A changed source emits a change event (JSON)
plus a diff-proposal skeleton YAML in outbox/legislation-diffs/ with a
suggested SLA deadline of detected_at + 20 business days (the "gazette date →
published pack" bound from the SLA table).

Modes:
    python tools/watch.py --check      # report changes; exit 1 if any
                                       # unacknowledged changes are pending
    python tools/watch.py --snapshot   # update the store after counsel review
                                       # (acknowledges pending events)
    python tools/watch.py --report     # pending changes + aging vs SLA

Network honesty: offline by default. Without --live every source is read from
its configured fixture file — CI and tests never touch the network. With
--live a fetch failure is a clear error for that source (recorded, reported,
and counted), never a silent pass.

Exit codes: --check/--report: 0 clean, 1 unacknowledged changes pending.
--snapshot: 0 on success.
"""
from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "watch_sources.yaml"
STATE_DIR = REPO / "watch_state"
DIFFS_DIR = REPO / "outbox" / "legislation-diffs"

SLA_BUSINESS_DAYS = 20  # LEGISLATION_WATCH.md §4: gazette date -> published pack


# ---------------------------------------------------------------- business days

def add_business_days(d: date, n: int) -> date:
    """d + n business days (Mon–Fri; public holidays are counsel's concern)."""
    cur, remaining = d, n
    while remaining:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            remaining -= 1
    return cur


def business_days_between(a: date, b: date) -> int:
    """Business days elapsed from a to b (negative if b precedes a)."""
    if a == b:
        return 0
    sign = 1
    if b < a:
        a, b, sign = b, a, -1
    n, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return sign * n


# ---------------------------------------------------------------- content fetch

class _LinkExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = dict(attrs)
        href = a.get("href")
        if href:
            self.links.append(href.strip())

    def handle_data(self, data):
        pass


class _TextExtractor(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if not self._depth:
            self.parts.append(data)


def normalise(content: bytes, parser_hint: str) -> str:
    """Reduce fetched content to a stable comparable form per parser hint."""
    if parser_hint == "feed":
        root = ET.fromstring(content.decode("utf-8", errors="replace"))
        entries = []
        for item in root.iter():
            tag = item.tag.rsplit("}", 1)[-1]
            if tag in ("item", "entry"):
                fields = []
                for child in item.iter():
                    ctag = child.tag.rsplit("}", 1)[-1]
                    if ctag in ("title", "guid", "id", "link", "pubDate", "updated"):
                        text = (child.text or child.attrib.get("href") or "").strip()
                        if text:
                            fields.append(f"{ctag}={text}")
                if fields:
                    entries.append("|".join(sorted(fields)))
        return "\n".join(sorted(entries))
    text = content.decode("utf-8", errors="replace")
    if parser_hint == "html-links":
        ex = _LinkExtractor()
        ex.feed(text)
        return "\n".join(sorted(set(ex.links)))
    if parser_hint == "html-text":
        ex = _TextExtractor()
        ex.feed(text)
        joined = " ".join(ex.parts)
        return re.sub(r"\s+", " ", joined).strip()
    raise ValueError(f"unknown parser hint: {parser_hint!r}")


def fetch_source(source: dict, live: bool) -> tuple[bytes, str | None]:
    """Return (content, etag). Offline: read fixture; live: HTTP GET."""
    if live:
        req = urllib.request.Request(source["url"], headers={"User-Agent": "lce-watch/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read(), resp.headers.get("ETag")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"live fetch failed for {source['url']}: {e}") from e
    fixture = source.get("fixture")
    if not fixture:
        raise RuntimeError(
            f"source {source['id']!r} has no fixture and --live was not given; "
            "offline mode requires a fixture path in watch_sources.yaml")
    path = REPO / fixture
    if not path.exists():
        raise RuntimeError(f"fixture missing for source {source['id']!r}: {fixture}")
    return path.read_bytes(), None


# ---------------------------------------------------------------- state store

def state_path(state_dir: Path, source_id: str) -> Path:
    return state_dir / f"{source_id}.json"


def load_state(state_dir: Path, source_id: str) -> dict | None:
    p = state_path(state_dir, source_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_state(state_dir: Path, source_id: str, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path(state_dir, source_id).write_text(json.dumps(state, indent=2) + "\n")


def pending_path(state_dir: Path) -> Path:
    return state_dir / "pending.json"


def load_pending(state_dir: Path) -> list[dict]:
    p = pending_path(state_dir)
    if not p.exists():
        return []
    return json.loads(p.read_text())


def save_pending(state_dir: Path, events: list[dict]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    pending_path(state_dir).write_text(json.dumps(events, indent=2) + "\n")


# ---------------------------------------------------------------- diff proposal

def diff_proposal_yaml(event: dict, source: dict) -> str:
    statute = source.get("statute_hint") or "<statute-id>"
    return (
        f"# SKELETON diff proposal emitted by tools/watch.py — counsel completes.\n"
        f"instrument: <title from {source['title']}>\n"
        f"detected_at: '{event['detected_at']}'\n"
        f"detected_via: {event['source_id']}\n"
        f"source_url: {source['url']}\n"
        f"excerpt_sha256: {event['sha256']}\n"
        f"sections_affected: [{statute}:<section-id>]\n"
        f"pack_changes: []\n"
        f"retroactive: false\n"
        f"counsel: {{name: '<external counsel>', signed_off_at: null}}\n"
        f"sla_deadline: '{event['sla_deadline']}'\n"
    )


def detect_changes(config: dict, state_dir: Path, live: bool, today: date) -> tuple[list[dict], list[str]]:
    """Fetch each enabled source, diff vs store. Returns (events, errors)."""
    events: list[dict] = []
    errors: list[str] = []
    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        sid = source["id"]
        try:
            content, etag = fetch_source(source, live)
            digest = hashlib.sha256(normalise(content, source["parser"]).encode()).hexdigest()
        except Exception as e:  # noqa: BLE001 — recorded, reported, never silent
            errors.append(f"{sid}: {e}")
            continue
        prev = load_state(state_dir, sid)
        if prev and prev.get("sha256") == digest:
            continue  # no change
        event = {
            "source_id": sid,
            "detected_at": today.isoformat(),
            "url": source["url"],
            "sha256": digest,
            "previous_sha256": prev.get("sha256") if prev else None,
            "etag": etag,
            "first_seen": prev is None,
            "acknowledged": False,
            "sla_deadline": add_business_days(today, SLA_BUSINESS_DAYS).isoformat(),
        }
        events.append(event)
    return events, errors


# ---------------------------------------------------------------- modes

def cmd_check(config, state_dir: Path, live: bool, today: date, diffs_dir: Path = DIFFS_DIR) -> int:
    events, errors = detect_changes(config, state_dir, live, today)
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    # merge with previously pending (unacknowledged) events
    pending = [p for p in load_pending(state_dir) if not p.get("acknowledged")]
    known = {(p["source_id"], p["sha256"]) for p in pending}
    new_events = [e for e in events if (e["source_id"], e["sha256"]) not in known]
    pending += new_events
    save_pending(state_dir, pending)
    if new_events:
        diffs_dir.mkdir(parents=True, exist_ok=True)
        src_by_id = {s["id"]: s for s in config["sources"]}
        for e in new_events:
            slug = f"{e['detected_at']}-{e['source_id']}.yaml"
            (diffs_dir / slug).write_text(diff_proposal_yaml(e, src_by_id[e["source_id"]]))
    for e in new_events:
        print(f"CHANGE {e['source_id']}: {'first snapshot' if e['first_seen'] else 'content changed'} "
              f"sha256={e['sha256'][:12]} sla_deadline={e['sla_deadline']}")
        print(f"  event: {json.dumps(e)}")
    unack = [p for p in pending if not p.get("acknowledged")]
    if unack:
        print(f"\n{len(unack)} unacknowledged change(s) pending — counsel review required, "
              f"then run: python tools/watch.py --snapshot")
        return 1
    print("no unacknowledged changes")
    return 0 if not errors else 2


def cmd_snapshot(config, state_dir: Path, live: bool, today: date) -> int:
    """Update the snapshot store after counsel review; acknowledges pending events."""
    events, errors = detect_changes(config, state_dir, live, today)
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    pending = load_pending(state_dir)
    by_source: dict[str, dict] = {}
    for p in pending + events:
        by_source[p["source_id"]] = p
    for sid, ev in by_source.items():
        save_state(state_dir, sid, {"sha256": ev["sha256"], "last_seen": today.isoformat(),
                                    "etag": ev.get("etag")})
    for p in pending:
        p["acknowledged"] = True
        p["acknowledged_at"] = today.isoformat()
    save_pending(state_dir, pending)
    n = len(by_source)
    print(f"snapshot updated for {n} source(s); {len(pending)} pending event(s) acknowledged")
    return 0 if not errors else 2


def render_report(state_dir: Path, today: date) -> str:
    pending = [p for p in load_pending(state_dir) if not p.get("acknowledged")]
    lines = ["# Legislation-watch report", f"generated_at: {today.isoformat()}", ""]
    if not pending:
        lines.append("No pending changes.")
        return "\n".join(lines) + "\n"
    lines.append("| source | detected_at | sla_deadline | business days elapsed | status |")
    lines.append("|---|---|---|---|---|")
    for p in sorted(pending, key=lambda x: x["detected_at"]):
        detected = date.fromisoformat(p["detected_at"])
        elapsed = business_days_between(detected, today)
        overdue = elapsed > SLA_BUSINESS_DAYS
        status = f"OVERDUE (+{elapsed - SLA_BUSINESS_DAYS}bd)" if overdue else f"{SLA_BUSINESS_DAYS - elapsed}bd remaining"
        lines.append(f"| {p['source_id']} | {p['detected_at']} | {p['sla_deadline']} | {elapsed} | {status} |")
    return "\n".join(lines) + "\n"


def cmd_report(state_dir: Path, today: date) -> int:
    text = render_report(state_dir, today)
    print(text, end="")
    pending = [p for p in load_pending(state_dir) if not p.get("acknowledged")]
    return 1 if pending else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--snapshot", action="store_true")
    mode.add_argument("--report", action="store_true")
    ap.add_argument("--live", action="store_true",
                    help="fetch over HTTP instead of fixtures (never default; CI never uses this)")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--state-dir", default=str(STATE_DIR))
    ap.add_argument("--diffs-dir", default=str(DIFFS_DIR),
                    help="where diff-proposal skeletons are written")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD; tests only)")
    args = ap.parse_args(argv)

    config = yaml.safe_load(Path(args.config).read_text())
    state_dir = Path(args.state_dir)
    today = date.fromisoformat(args.today) if args.today else date.today()

    if args.check:
        return cmd_check(config, state_dir, args.live, today, Path(args.diffs_dir))
    if args.snapshot:
        return cmd_snapshot(config, state_dir, args.live, today)
    return cmd_report(state_dir, today)


if __name__ == "__main__":
    sys.exit(main())
