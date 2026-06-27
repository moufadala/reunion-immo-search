#!/usr/bin/env bash
set -euo pipefail
PROJECT=/opt/data/projects/reunion-immo-search
LOGDIR="$PROJECT/artifacts/sprint_logs"
mkdir -p "$LOGDIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
REPORT="$PROJECT/artifacts/product_v2_sprint_${STAMP}.md"
BACKUP_APP="$PROJECT/artifacts/app.pre-sprint-${STAMP}"
KEEP_APP=0
cd "$PROJECT"
PY="${IMMO_PROJECT_PYTHON:-$PROJECT/.venv/bin/python}"
if [ ! -x "$PY" ]; then PY=python3; fi

restore_on_failure(){
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "${KEEP_APP:-0}" != "1" ] && [ -d "$BACKUP_APP" ]; then
    echo "[$(date -u +%H:%M:%S)] RESTORE artifacts/app from $BACKUP_APP after rc=$rc" | tee -a "$REPORT" >&2 || true
    rm -rf "$PROJECT/artifacts/app"
    cp -a "$BACKUP_APP" "$PROJECT/artifacts/app"
    chmod -R a+rX "$PROJECT/artifacts/app"
    if [ -x "$PROJECT/deploy/publish-traefik.sh" ]; then
      bash "$PROJECT/deploy/publish-traefik.sh" >/tmp/immo-sprint-rollback-publish.log 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap restore_on_failure EXIT

if [ ! -s "$PROJECT/artifacts/app/index.html" ] || [ ! -s "$PROJECT/artifacts/app/listings.json" ]; then
  echo "artifacts/app baseline missing or invalid" >&2
  exit 2
fi
rm -rf "$BACKUP_APP"
cp -a "$PROJECT/artifacts/app" "$BACKUP_APP"

step(){
  local name="$1"; shift
  echo "[$(date -u +%H:%M:%S)] START $name"
  "$@"
  echo "[$(date -u +%H:%M:%S)] OK $name"
}

{
  echo "# Product V2 sprint — $STAMP"
  echo
  echo "## Progress log"
} > "$REPORT"

run_and_log(){
  local name="$1"; shift
  local out="$LOGDIR/${STAMP}_${name}.log"
  echo "[$(date -u +%H:%M:%S)] START $name" | tee -a "$REPORT"
  if "$@" >"$out" 2>&1; then
    echo "[$(date -u +%H:%M:%S)] OK $name" | tee -a "$REPORT"
    echo "- $name: PASS — $out" >> "$REPORT"
  else
    rc=$?
    echo "[$(date -u +%H:%M:%S)] FAIL $name rc=$rc" | tee -a "$REPORT"
    echo "- $name: FAIL rc=$rc — $out" >> "$REPORT"
    tail -80 "$out" | sed 's/^/    /' >> "$REPORT"
    exit $rc
  fi
}

run_and_log phase_a_inventory_oracle python3 scripts/generate_domain_inventory_and_oracle_v2.py --app artifacts/app
run_and_log phase_b_product_hardening_v5 python3 scripts/patch_product_hardening_v5.py --app artifacts/app
run_and_log phase_b_natural_search_contract python3 tests/audit_natural_search_contract.py artifacts/app
run_and_log phase_d_regen_changes python3 src/listing_changes.py --limit 80
run_and_log phase_d_enhance_changes python3 scripts/enhance_changes_decision_view.py --app artifacts/app
run_and_log phase_e_wave2_detail_geo_photo python3 scripts/patch_wave2_lot_c_detail_geo_photo.py --app artifacts/app
run_and_log phase_e_ops_cockpit python3 scripts/generate_ops_cockpit.py --app artifacts/app --run-dir "$LOGDIR"
run_and_log audit_search_alerts python3 tests/audit_search_alerts.py
run_and_log audit_ops_cockpit python3 tests/audit_ops_cockpit.py artifacts/app
run_and_log audit_ops_cockpit_browser_static "$PY" tests/audit_ops_cockpit_browser_static.py artifacts/app
run_and_log audit_detail_geo_photo_prudent python3 tests/audit_detail_geo_photo_prudent.py artifacts/app
run_and_log pre_publish_delta_guard python3 scripts/audit_public_delta_guard.py --baseline "$BACKUP_APP" --candidate artifacts/app --json-out "$LOGDIR/${STAMP}_pre_publish_delta_guard.json"
run_and_log pre_publish_rollback_drill python3 scripts/rollback_public_app.py --backup "$BACKUP_APP" --target artifacts/app --json-out "$LOGDIR/${STAMP}_pre_publish_rollback_drill.json" --qa-cmd "python3 tests/audit_clean_portal.py"
run_and_log phase_publish bash deploy/publish-traefik.sh
run_and_log audit_search_oracle_v2 "$PY" tests/audit_search_oracle_v2.py
run_and_log audit_semantic_truth python3 tests/audit_listing_semantic_truth.py
run_and_log audit_data_truth_public "$PY" tests/audit_data_truth_public.py
run_and_log audit_mobile_public "$PY" tests/audit_mobile_public.py
run_and_log audit_changes_decision "$PY" tests/audit_changes_decision_public.py
run_and_log audit_changes_filters "$PY" tests/audit_changes_page_filters.py
run_and_log audit_hard_regression "$PY" tests/audit_hard_regression_suite.py artifacts/app
run_and_log qa_public bash deploy/qa-public.sh
KEEP_APP=1

{
  echo
  echo "## Summary"
  echo "- URL canonique: https://immo.148.230.103.174.sslip.io/"
  echo "- Inventaire: artifacts/domain_inventory_v2.json"
  echo "- Oracle V2: tests/acceptance_search_oracle_v2.json"
  echo "- Rapport vérité sémantique: artifacts/listing_semantic_truth_v2.json"
  echo "- Logs: artifacts/sprint_logs/${STAMP}_*.log"
  echo "- Tous les audits ci-dessus sont PASS."
} >> "$REPORT"

echo "SPRINT_DONE report=$REPORT"
