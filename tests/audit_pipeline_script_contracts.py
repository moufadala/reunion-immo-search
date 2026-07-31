#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, label: str, errors: list[str]) -> None:
    if needle not in text:
        errors.append(f"{label}: missing {needle!r}")


def require_any(text: str, needles: list[str], label: str, errors: list[str]) -> None:
    if not any(needle in text for needle in needles):
        errors.append(f"{label}: missing one of {needles!r}")


def main() -> int:
    errors: list[str] = []
    daily = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    sprint = (ROOT / "scripts" / "run_product_v2_sprint.sh").read_text(encoding="utf-8")

    require_any(daily, [
        'run_step public_delta_guard python3 "$PROJECT/scripts/audit_public_delta_guard.py" --baseline "$PROJECT/artifacts/app" --candidate "$CLEAN_STAGE"',
        'run_step public_delta_guard "$PY" "$PROJECT/scripts/audit_public_delta_guard.py" --baseline "$PROJECT/artifacts/app" --candidate "$CLEAN_STAGE"',
        'report_step public_delta_guard "$PY" "$PROJECT/scripts/audit_public_delta_guard.py" --baseline "$PROJECT/artifacts/app" --candidate "$CLEAN_STAGE"',
    ], "daily refresh", errors)
    require_any(daily, [
        'run_step promote_app_candidate python3 "$PROJECT/scripts/promote_app_candidate.py" --candidate "$CLEAN_STAGE"',
        'run_step promote_app_candidate "$PY" "$PROJECT/scripts/promote_app_candidate.py" --candidate "$CLEAN_STAGE"',
    ], "daily refresh", errors)
    require_any(daily, [
        'run_step rollback_app_drill python3 "$PROJECT/scripts/rollback_public_app.py" --backup "$BACKUP_APP"',
        'run_step rollback_app_drill "$PY" "$PROJECT/scripts/rollback_public_app.py" --backup "$BACKUP_APP"',
    ], "daily refresh", errors)
    require(daily, '--out "$CLEAN_STAGE/changes.json" --html-out "$CLEAN_STAGE/changes.html"', "daily refresh", errors)
    if 'run_step listing_changes python3 "$PROJECT/src/listing_changes.py" --limit 80\n' in daily:
        errors.append("daily refresh: listing_changes would write default artifacts/app before promotion")

    require(sprint, "trap restore_on_failure EXIT", "sprint", errors)
    require(sprint, "BACKUP_APP=", "sprint", errors)
    require(sprint, "pre_publish_delta_guard", "sprint", errors)
    require(sprint, "pre_publish_rollback_drill", "sprint", errors)
    require(sprint, "KEEP_APP=1", "sprint", errors)

    if errors:
        print("PIPELINE_SCRIPT_CONTRACTS FAIL")
        for err in errors:
            print(f" - {err}")
        return 1
    print("PIPELINE_SCRIPT_CONTRACTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
