#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FORBIDDEN = re.compile(r"(/opt/data|Traceback|sqlite3\.OperationalError|SECRET_KEY|api_key=|password=|Authorization:|Bearer\s+)", re.I)


def fail(msg: str) -> None:
    print(f"OPS_COCKPIT_AUDIT_FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    html_path = app / "ops.html"
    json_path = app / "ops_status.json"
    index_path = app / "index.html"
    if not html_path.exists():
        fail("ops.html missing")
    if not json_path.exists():
        fail("ops_status.json missing")
    html = html_path.read_text(encoding="utf-8")
    raw_json = json_path.read_text(encoding="utf-8")
    data = json.loads(raw_json)
    if FORBIDDEN.search(html) or FORBIDDEN.search(raw_json):
        fail("forbidden internal token leaked")
    if 'name="robots" content="noindex,nofollow,noarchive"' not in html:
        fail("missing noindex/noarchive robots meta")
    if "Content-Security-Policy" not in html:
        fail("missing CSP meta")
    if not data.get("public", {}).get("listings"):
        fail("missing public listings count")
    if "Cockpit ops" not in html:
        fail("wrong title/content")
    for token in ["Historique 7 jours", "Fraîcheur par source", "Dernier sprint", "Pipeline", "Alertes dry-run", "Liens QA"]:
        if token not in html:
            fail(f"missing cockpit section: {token}")
    run = data.get("run", {})
    if "history_7d" not in run or not isinstance(run.get("history_7d"), list):
        fail("missing run.history_7d")
    if "freshness" not in data.get("source_health", {}):
        fail("missing source freshness payload")
    if not isinstance(data.get("pipeline", {}).get("groups"), dict):
        fail("missing pipeline groups payload")
    if not data.get("last_sprint"):
        fail("missing last sprint payload")
    dry = data.get("alert_dry_run", {})
    if dry.get("available") and dry.get("ok") is not True:
        fail("alert dry-run available but not ok")
    qa_links = data.get("qa_links") or []
    if len(qa_links) < 5 or not all(str(x.get("href", "")).endswith((".html", "/")) for x in qa_links if isinstance(x, dict)):
        fail("missing safe QA links")
    if index_path.exists():
        idx = index_path.read_text(encoding="utf-8", errors="replace")
        if "ops.html" in idx or "ops_status.json" in idx:
            fail("ops cockpit linked from homepage")
    print("OPS_COCKPIT_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
