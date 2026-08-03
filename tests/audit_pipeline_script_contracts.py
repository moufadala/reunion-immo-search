#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_CONTAINER_ROOT = Path("/opt/data/projects/reunion-immo-search")
RUNTIME_ROOT = Path("/opt/data/scripts")
NOT_APPLICABLE_RC = 78

RUNTIME_SCRIPT_CONTRACTS = [
    ("reunion_watch_pipeline.py", "scripts/reunion_watch_pipeline.py"),
    ("reunion_watch_daily.sh", "scripts/reunion_watch_daily.sh"),
    ("reunion_watch_daily_async.sh", "scripts/reunion_watch_daily_async.sh"),
    ("reunion_watch_daily_notify.sh", "scripts/reunion_watch_daily_notify.sh"),
    ("reunion_watch_post_refresh_alerts.sh", "scripts/reunion_watch_post_refresh_alerts.sh"),
    ("immo_p0_freshness_check.sh", "scripts/immo_p0_freshness_check.sh"),
    ("immo_saved_search_alerts.sh", "scripts/immo_saved_search_alerts.sh"),
    ("immo_daily_public_refresh.sh", "scripts/immo_daily_public_refresh.sh"),
    ("realestate_watch.py", "scripts/realestate_watch.py"),
]

RUNTIME_SCRIPT_ALLOWLIST: dict[str, str] = {
    # Keep this intentionally empty by default: every known executed script in the
    # daily chain must be a symlink to the repository or byte-identical to it.
    # Temporary exceptions must include a dated operational justification here.
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(text: str, needle: str, label: str, errors: list[str]) -> None:
    if needle not in text:
        errors.append(f"{label}: missing {needle!r}")


def require_any(text: str, needles: list[str], label: str, errors: list[str]) -> None:
    if not any(needle in text for needle in needles):
        errors.append(f"{label}: missing one of {needles!r}")


def runtime_contract_applicability_error() -> str | None:
    """Return why --runtime-check cannot judge this namespace, or None.

    The daily pipeline runs inside the Hermes container, where /opt/data is the
    bind mount that also contains this repository and /opt/data/scripts. On the
    VPS host, /opt/data is a known decoy/legacy path; reporting missing runtime
    files there would invite destructive "repairs" that recreate copies over the
    real container symlinks.
    """
    canonical = CANONICAL_CONTAINER_ROOT.resolve(strict=False)
    root = ROOT.resolve(strict=False)
    if root != canonical:
        return (
            "CONTROLE NON APPLICABLE ICI: ce dépôt n'est pas vu comme "
            f"{CANONICAL_CONTAINER_ROOT}. ROOT={ROOT}. /opt/data est probablement "
            "le leurre côté hôte. Lancer ce contrôle DANS le conteneur Hermes "
            "(ex.: docker exec hermes-gateway sh -lc 'cd /opt/data/projects/reunion-immo-search && "
            "python3 tests/audit_pipeline_script_contracts.py --runtime-check')."
        )
    if not RUNTIME_ROOT.exists() or not RUNTIME_ROOT.is_dir():
        return (
            "CONTROLE NON APPLICABLE ICI: /opt/data/scripts est absent dans ce point de vue. "
            "/opt/data est probablement le leurre côté hôte. Lancer ce contrôle DANS le conteneur Hermes."
        )
    return None


def audit_runtime_scripts(errors: list[str]) -> None:
    for runtime_name, repo_rel in RUNTIME_SCRIPT_CONTRACTS:
        runtime_path = RUNTIME_ROOT / runtime_name
        repo_path = ROOT / repo_rel
        label = f"runtime script {runtime_name}"
        if runtime_name in RUNTIME_SCRIPT_ALLOWLIST:
            reason = RUNTIME_SCRIPT_ALLOWLIST[runtime_name].strip()
            if not reason or len(reason) < 20:
                errors.append(f"{label}: allowlist entry must include a concrete justification")
            continue
        if not repo_path.exists():
            errors.append(f"{label}: repository version missing at {repo_path}")
            continue
        if not runtime_path.exists() and not runtime_path.is_symlink():
            errors.append(f"{label}: runtime file missing at {runtime_path}")
            continue
        if runtime_path.is_symlink():
            resolved = runtime_path.resolve()
            if resolved != repo_path.resolve():
                errors.append(f"{label}: symlink resolves to {resolved}, expected {repo_path.resolve()}")
            continue
        if sha256(runtime_path) != sha256(repo_path):
            errors.append(f"{label}: runtime copy diverges from repository version ({runtime_path} != {repo_path})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-check", action="store_true", help="also verify /opt/data/scripts against repository scripts")
    args = parser.parse_args(argv)

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
    require(daily, '--out "$CLEAN_STAGE/changes.json" --html-out "$RUN_DIR/changes.html"', "daily refresh", errors)
    if '--html-out "$CLEAN_STAGE/changes.html"' in daily:
        errors.append("daily refresh: changes.html must stay in RUN_DIR, not in the promoted clean stage")
    require(daily, 'run_step postflight_public_contract', "daily refresh", errors)
    if daily.rfind('run_step postflight_public_contract') < daily.rfind('run_step immo_health_state_save'):
        errors.append("daily refresh: postflight_public_contract must be after all public-app writers")
    if 'run_step ops_cockpit "$PY" "$PROJECT/scripts/generate_ops_cockpit.py" --app "$PROJECT/artifacts/app" --run-dir "$RUN_DIR"\n' in daily:
        errors.append("daily refresh: ops_cockpit writes to public app; it must write to RUN_DIR")
    if 'run_step listing_changes python3 "$PROJECT/src/listing_changes.py" --limit 80\n' in daily:
        errors.append("daily refresh: listing_changes would write default artifacts/app before promotion")

    require(sprint, "trap restore_on_failure EXIT", "sprint", errors)
    require(sprint, "BACKUP_APP=", "sprint", errors)
    require(sprint, "pre_publish_delta_guard", "sprint", errors)
    require(sprint, "pre_publish_rollback_drill", "sprint", errors)
    require(sprint, "KEEP_APP=1", "sprint", errors)

    require(daily, "run_step postflight_public_contract", "daily refresh", errors)
    order = [
        ("immo_health_state_save", daily.find("run_step immo_health_state_save")),
        ("postflight_public_contract", daily.find("run_step postflight_public_contract")),
        ("APP_KEEP", daily.find("APP_KEEP=1")),
    ]
    if any(pos < 0 for _, pos in order):
        errors.append(f"daily refresh: postflight ordering markers missing: {order!r}")
    elif not (order[0][1] < order[1][1] < order[2][1]):
        errors.append(f"daily refresh: postflight must run after immo_health_state_save and before APP_KEEP=1: {order!r}")

    if args.runtime_check:
        applicability_error = runtime_contract_applicability_error()
        if applicability_error:
            print("PIPELINE_SCRIPT_CONTRACTS NOT_APPLICABLE")
            print(applicability_error)
            return NOT_APPLICABLE_RC
        audit_runtime_scripts(errors)

    if errors:
        print("PIPELINE_SCRIPT_CONTRACTS FAIL")
        for err in errors:
            print(f" - {err}")
        return 1
    print("PIPELINE_SCRIPT_CONTRACTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
