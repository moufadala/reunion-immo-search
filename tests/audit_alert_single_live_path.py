#!/usr/bin/env python3
"""Audit alert emission paths for private watch safety.

The private profile matcher must remain dry-run only. Existing live-capable alert
scripts are allowed, but private profiles must not be wired into any cron wrapper
or Telegram transport until an explicit activation decision.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_SCRIPTS = [
    ROOT / "src" / "private_profile_matching.py",
    ROOT / "src" / "search_alerts.py",
    ROOT / "src" / "source_health_alerts.py",
]
WRAPPERS = [
    Path("/opt/data/scripts/immo_saved_search_alerts.sh"),
    Path("/opt/data/scripts/immo_source_health_alerts.sh"),
    Path("/opt/data/scripts/reunion_watch_daily_notify.sh"),
]
errors: list[str] = []

private = (ROOT / "src" / "private_profile_matching.py").read_text(encoding="utf-8")
if "send_message" in private or "browser_" in private or "cronjob(" in private:
    errors.append("private_profile_matching.py must not call outbound Hermes transports")
if re.search(r"add_argument\([^\n]*--live", private):
    errors.append("private_profile_matching.py must not expose --live")
if "--dry-run is required" not in private:
    errors.append("private_profile_matching.py must require --dry-run")

for wrapper in WRAPPERS:
    if not wrapper.exists():
        continue
    text = wrapper.read_text(encoding="utf-8")
    if "private_profile_matching.py" in text:
        errors.append(f"{wrapper}: private profiles must not be wired into cron/live wrapper yet")

live_capable = []
for path in PROJECT_SCRIPTS:
    text = path.read_text(encoding="utf-8")
    if "--live" in text or "source_health_alerts.py" in str(path):
        live_capable.append(str(path.relative_to(ROOT)))

if "src/private_profile_matching.py" in live_capable:
    errors.append("private matcher incorrectly classified as live-capable")

if errors:
    print("ALERT_SINGLE_LIVE_PATH_AUDIT FAIL")
    for e in errors:
        print("-", e)
    raise SystemExit(1)
print({"ok": True, "private_profiles_live_capable": False, "existing_live_capable": live_capable})
print("ALERT_SINGLE_LIVE_PATH_AUDIT PASS")
