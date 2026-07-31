"""tests for tools/watch.py — offline, fixture-driven (SPEC-LCE §4 / W1).

[REAL] change detection, hash stability, SLA aging, report rendering.
[GUARDED] live HTTP path is error-checked but not network-tested here.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import watch  # noqa: E402

CONFIG = REPO / "watch_sources.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "watch"


@pytest.fixture()
def state_dir(tmp_path):
    return tmp_path / "watch_state"


def run_cli(state_dir, *args, diffs_dir=None):
    diffs_dir = diffs_dir or (Path(str(state_dir)) / "diffs")
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "watch.py"), "--state-dir", str(state_dir),
         "--diffs-dir", str(diffs_dir), *args], cwd=REPO, capture_output=True, text=True)


# ------------------------------------------------------------- business-day math

def test_add_business_days_skips_weekends():
    friday = date(2026, 8, 14)  # Friday
    assert watch.add_business_days(friday, 1) == date(2026, 8, 17)  # Monday
    assert watch.add_business_days(friday, 20) == date(2026, 9, 11)
    assert watch.add_business_days(date(2026, 8, 17), 0) == date(2026, 8, 17)


def test_business_days_between():
    mon = date(2026, 8, 10)
    assert watch.business_days_between(mon, mon) == 0
    assert watch.business_days_between(mon, date(2026, 8, 17)) == 5  # next Monday
    assert watch.business_days_between(date(2026, 8, 17), mon) == -5
    assert watch.business_days_between(mon, date(2026, 9, 7)) == 20  # SLA boundary


def test_sla_deadline_is_detected_plus_20_business_days():
    config = yaml.safe_load(CONFIG.read_text())
    events, errors = watch.detect_changes(config, Path("/nonexistent-state"), live=False,
                                          today=date(2026, 8, 10))
    assert not errors
    assert events
    for e in events:
        assert e["sla_deadline"] == watch.add_business_days(date(2026, 8, 10), 20).isoformat()


# ------------------------------------------------------------- change detection

def test_first_run_detects_all_enabled_sources(state_dir):
    res = run_cli(state_dir, "--check", "--today", "2026-08-10")
    assert res.returncode == 1
    assert "4 unacknowledged change(s) pending" in res.stdout
    for sid in ("nrs-circulars", "nrs-einvoice-docs", "fgn-gazette", "cbn-circulars"):
        assert f"CHANGE {sid}:" in res.stdout


def test_second_check_without_content_change_reports_same_pending(state_dir):
    run_cli(state_dir, "--check", "--today", "2026-08-10")
    res = run_cli(state_dir, "--check", "--today", "2026-08-11")
    assert res.returncode == 1
    assert "CHANGE" not in res.stdout.splitlines()[0]  # no NEW change lines
    pending = json.loads((state_dir / "pending.json").read_text())
    assert len(pending) == 4  # not duplicated


def test_hash_stability_same_content_same_digest():
    config = yaml.safe_load(CONFIG.read_text())
    src = next(s for s in config["sources"] if s["id"] == "nrs-circulars")
    content, _ = watch.fetch_source(src, live=False)
    d1 = watch.hashlib.sha256(watch.normalise(content, src["parser"]).encode()).hexdigest()
    d2 = watch.hashlib.sha256(watch.normalise(content, src["parser"]).encode()).hexdigest()
    assert d1 == d2


def test_normalise_html_links_ignores_text_changes():
    a = b'<a href="/x.pdf">Circular One</a>'
    b = b'<a href="/x.pdf">Circular ONE retitled</a>'
    assert watch.normalise(a, "html-links") == watch.normalise(b, "html-links")


def test_normalise_html_links_detects_new_link():
    a = b'<a href="/x.pdf">One</a>'
    b = a + b'<a href="/y.pdf">Two</a>'
    assert watch.normalise(a, "html-links") != watch.normalise(b, "html-links")


def test_normalise_feed_sorted_stable():
    content = (FIXTURES / "fgn-gazette.xml").read_bytes()
    n1 = watch.normalise(content, "feed")
    assert "gaz-2024-106" in n1 and "gaz-2025-nta" in n1
    assert n1 == watch.normalise(content, "feed")


def test_change_after_snapshot_detected(tmp_path):
    """Modify a fixture copy -> --check flags a new change event."""
    work = tmp_path / "fx"
    shutil.copytree(FIXTURES, work)
    config = yaml.safe_load(CONFIG.read_text())
    for s in config["sources"]:
        s["fixture"] = str(work / Path(s["fixture"]).name)
    cfg = tmp_path / "watch_sources.yaml"
    cfg.write_text(yaml.safe_dump(config))
    state = tmp_path / "state"

    base = [sys.executable, str(REPO / "tools" / "watch.py"), "--config", str(cfg),
            "--state-dir", str(state), "--diffs-dir", str(tmp_path / "diffs"),
            "--today", "2026-08-10"]
    subprocess.run(base + ["--check"], capture_output=True)
    subprocess.run(base + ["--snapshot"], capture_output=True)
    res = subprocess.run(base + ["--check"], capture_output=True, text=True)
    assert res.returncode == 0  # acknowledged, no new changes
    assert "no unacknowledged changes" in res.stdout

    # counsel's source publishes a new circular
    html = (work / "nrs-circulars.html").read_text()
    (work / "nrs-circulars.html").write_text(
        html.replace("</ul>", '<li><a href="/circulars/2026-09-presumptive-framework.pdf">'
                              'Presumptive tax framework</a></li></ul>'))
    res = subprocess.run(base + ["--check"], capture_output=True, text=True)
    assert res.returncode == 1
    assert "CHANGE nrs-circulars: content changed" in res.stdout


def test_diff_proposal_skeleton_written(tmp_path):
    diffs = tmp_path / "diffs"
    res = run_cli(tmp_path / "st", "--check", "--today", "2026-08-10", diffs_dir=diffs)
    assert res.returncode == 1
    proposals = sorted(diffs.glob("2026-08-10-*.yaml"))
    assert len(proposals) == 4
    doc = yaml.safe_load(proposals[0].read_text().replace(
        "# SKELETON diff proposal emitted by tools/watch.py — counsel completes.\n", ""))
    assert doc["detected_at"] == "2026-08-10"
    assert doc["sla_deadline"] == "2026-09-07"  # +20 business days
    assert doc["counsel"]["signed_off_at"] is None
    assert doc["excerpt_sha256"]


# ------------------------------------------------------------- snapshot / report

def test_snapshot_acknowledges_and_check_goes_green(state_dir):
    run_cli(state_dir, "--check", "--today", "2026-08-10")
    res = run_cli(state_dir, "--snapshot", "--today", "2026-08-12")
    assert res.returncode == 0
    res = run_cli(state_dir, "--check", "--today", "2026-08-12")
    assert res.returncode == 0
    assert "no unacknowledged changes" in res.stdout
    stored = json.loads((state_dir / "nrs-circulars.json").read_text())
    assert stored["last_seen"] == "2026-08-12"
    assert len(stored["sha256"]) == 64


def test_report_aging_and_overdue(state_dir):
    run_cli(state_dir, "--check", "--today", "2026-08-10")
    res = run_cli(state_dir, "--report", "--today", "2026-08-21")
    assert res.returncode == 1
    assert "11bd remaining" in res.stdout  # 9 business days elapsed of 20
    res = run_cli(state_dir, "--report", "--today", "2026-09-14")
    assert "OVERDUE (+5bd)" in res.stdout  # 25bd elapsed
    res = run_cli(state_dir, "--snapshot", "--today", "2026-09-14")
    assert res.returncode == 0
    res = run_cli(state_dir, "--report", "--today", "2026-09-14")
    assert res.returncode == 0
    assert "No pending changes." in res.stdout


def test_report_no_state_is_clean(tmp_path):
    res = run_cli(tmp_path / "empty", "--report", "--today", "2026-08-10")
    assert res.returncode == 0
    assert "No pending changes." in res.stdout


# ------------------------------------------------------------- live guard

def test_offline_mode_never_uses_url_even_without_fixture(tmp_path):
    """A source with no fixture and no --live is a clear error, exit 2."""
    config = {"sources": [{"id": "x", "title": "x", "url": "https://example.invalid/x",
                           "parser": "html-text", "enabled": True}]}
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))
    res = subprocess.run(
        [sys.executable, str(REPO / "tools" / "watch.py"), "--config", str(cfg),
         "--state-dir", str(tmp_path / "st"), "--check"], capture_output=True, text=True)
    assert res.returncode == 2
    assert "no fixture and --live was not given" in res.stderr
