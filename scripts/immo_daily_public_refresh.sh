#!/usr/bin/env bash
set -euo pipefail

# Daily non-agent product refresh for the public Réunion immo dashboard.
# Success is JSON on stdout. Detailed logs go to the run directory.

umask 077
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache-hermes}"

PROJECT="/opt/data/projects/reunion-immo-search"
CLEAN_PROJECT="/opt/data/projects/reunion-immo-clean-app"
ROOT="/opt/data"
STAMP="${IMMO_REFRESH_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${IMMO_REFRESH_RUN_DIR:-/opt/data/artifacts/immo-public-refresh/${STAMP}}"
PROD_DB="${IMMO_DB_PATH:-/opt/data/data/reunion_watch.db}"
STAGE_DB_MODE="${IMMO_STAGE_DB:-1}"
STAGE_DB="$RUN_DIR/reunion_watch.stage.db"
DB="$PROD_DB"
export IMMO_DB_PATH="$DB"
SELOGER_ARTIFACT="/opt/data/artifacts/realestate/seloger_multipage_results.json"
mkdir -p "$RUN_DIR"

run_step() {
  local name="$1"; shift
  local stdout="$RUN_DIR/${name}.stdout"
  local stderr="$RUN_DIR/${name}.stderr"
  local status="$RUN_DIR/${name}.status"
  local start end rc
  start=$(date +%s)
  set +e
  "$@" >"$stdout" 2>"$stderr"
  rc=$?
  set -e
  end=$(date +%s)
  printf '%s rc=%s duration_s=%s\n' "$name" "$rc" "$((end-start))" >"$status"
  if [ "$rc" -ne 0 ]; then
    printf 'Step failed: %s rc=%s\n' "$name" "$rc" >&2
    printf 'stdout: %s\nstderr: %s\n' "$stdout" "$stderr" >&2
    tail -n 80 "$stderr" >&2 || true
    tail -n 80 "$stdout" >&2 || true
    exit "$rc"
  fi
}

# Roll back source-detail/stage DB and public-app mutations if a downstream gate fails.
ENRICHMENT_DB_BACKUP=""
ENRICHMENT_DB_KEEP=0
DB_PROMOTE_DONE=0
DB_PROMOTE_KEEP=0
DB_PROMOTE_BACKUP=""
APP_SWAP_DONE=0
APP_KEEP=0
BACKUP_APP=""
restore_on_failure() {
  local rc=$?
  if [ "$rc" -ne 0 ] \
    && [ -n "${ENRICHMENT_DB_BACKUP:-}" ] \
    && [ "${ENRICHMENT_DB_KEEP:-0}" != "1" ] \
    && [ -s "$ENRICHMENT_DB_BACKUP" ]; then
    printf 'Restoring DB before source_detail_enrichment after failure rc=%s\n' "$rc" >&2
    printf 'backup: %s\n' "$ENRICHMENT_DB_BACKUP" >&2
    printf 'db: %s\n' "$DB" >&2
    cp -p "$ENRICHMENT_DB_BACKUP" "$DB"
  fi
  if [ "$rc" -ne 0 ] \
    && [ "${DB_PROMOTE_DONE:-0}" = "1" ] \
    && [ "${DB_PROMOTE_KEEP:-0}" != "1" ] \
    && [ -s "${DB_PROMOTE_BACKUP:-}" ]; then
    printf 'Restoring promoted DB after failed downstream gate rc=%s\n' "$rc" >&2
    printf 'backup_db: %s\n' "$DB_PROMOTE_BACKUP" >&2
    printf 'prod_db: %s\n' "$PROD_DB" >&2
    cp -p "$DB_PROMOTE_BACKUP" "$PROD_DB"
  fi
  if [ "$rc" -ne 0 ] \
    && [ "${APP_SWAP_DONE:-0}" = "1" ] \
    && [ "${APP_KEEP:-0}" != "1" ] \
    && [ -d "${BACKUP_APP:-}" ]; then
    printf 'Restoring artifacts/app after failed post-swap gate rc=%s\n' "$rc" >&2
    printf 'backup_app: %s\n' "$BACKUP_APP" >&2
    rm -rf "$PROJECT/artifacts/app"
    cp -a "$BACKUP_APP" "$PROJECT/artifacts/app"
    chmod -R a+rX "$PROJECT/artifacts/app"
  fi
  exit "$rc"
}
trap restore_on_failure EXIT

if [ "$STAGE_DB_MODE" = "1" ]; then
  run_step stage_db_init python3 -c '
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
' "$PROD_DB" "$STAGE_DB"
  DB="$STAGE_DB"
  export IMMO_DB_PATH="$DB"
fi

# Refresh API/RSS/HTML sources already supported by realestate_watch.
run_step realestate_refresh \
  python3 "$ROOT/scripts/realestate_watch.py" \
    --refresh \
    --db "$DB" \
    --run-dir "$RUN_DIR/realestate_watch" \
    --max-price 1200 \
    --min-price 450 \
    --min-rooms 2 \
    --limit 15

# SeLoger is a separate CDP collector: collect a fresh artifact, then import it into DB.
run_step seloger_cdp_collect python3 "$ROOT/scripts/seloger_multi_page.py"
run_step seloger_import python3 "$PROJECT/src/import_seloger_multipage.py" --db "$DB" --artifact "$SELOGER_ARTIFACT" --max-age-hours 6

# Recover source-detail descriptions before building the public artifact.
# This is deliberately idempotent and guarded: it snapshots DB internally and
# updates only when the recovered detail text is richer/cleaner than the current row.
ENRICHMENT_DB_BACKUP="$RUN_DIR/reunion_watch.before-source-detail.db"
run_step source_detail_db_backup python3 -c '
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
' "$DB" "$ENRICHMENT_DB_BACKUP"
run_step source_detail_enrichment python3 "$PROJECT/scripts/enrich_source_details_v3.py" --db "$DB" --sleep 0.08 --report "$RUN_DIR/source_detail_enrichment.jsonl"

# Recompute through a safe two-stage pipeline:
# 1) build the technical app in an isolated stage, not in artifacts/app served by nginx;
# 2) enrich/cache galleries and recompute intelligence in that stage;
# 3) build the family-facing clean portal with side pages + aliases;
# 4) swap artifacts/app only after local gates pass, then publish.
TECH_STAGE="$PROJECT/artifacts/daily-tech-stage-${STAMP}"
CLEAN_STAGE="$PROJECT/artifacts/daily-clean-stage-${STAMP}"
rm -rf "$TECH_STAGE" "$CLEAN_STAGE"
mkdir -p "$TECH_STAGE" "$CLEAN_STAGE"

run_step db_enrichment_audit python3 "$PROJECT/tests/audit_db_enrichment.py" --db "$DB"
run_step build_technical_app bash -lc 'cd "$0" && python3 src/build_app.py --db "$1" --out "$2"' "$PROJECT" "$DB" "$TECH_STAGE"
run_step source_health_audit python3 "$PROJECT/tests/audit_source_health.py"
run_step gallery_enrichment bash -lc 'cd "$0" && python3 scripts/enrich_listing_galleries.py --db "$1" --app "$2" --report "$3" --manifest "$4"' "$PROJECT" "$DB" "$TECH_STAGE" "$RUN_DIR/gallery-enrichment-report.md" "$RUN_DIR/gallery-enrichment-manifest.json"
run_step seed_photo_cache bash -lc 'set -euo pipefail; project="$1"; stage="$2"; if [ -d "$project/artifacts/app/thumbs" ]; then mkdir -p "$stage/thumbs"; cp -an "$project/artifacts/app/thumbs/." "$stage/thumbs/"; fi' _ "$PROJECT" "$TECH_STAGE"
run_step photo_cache bash -lc 'cd "$0" && IMMO_APP_PATH="$1" PHOTO_WORKERS=8 PHOTO_TIMEOUT=18 python3 scripts/cache_listing_images.py' "$PROJECT" "$TECH_STAGE"
run_step intelligence_layers bash -lc 'cd "$0" && IMMO_DB_PATH="$1" IMMO_APP_PATH="$2" python3 src/immo_intelligence_layers.py' "$PROJECT" "$DB" "$TECH_STAGE"
run_step build_clean_portal bash -lc 'cd "$0" && IMMO_APP_PATH="$1" IMMO_OUT_PATH="$2" python3 scripts/build_clean_portal_v1.py' "$PROJECT" "$TECH_STAGE" "$CLEAN_STAGE"

# Keep files readable by nginx despite this wrapper's restrictive umask, and guard against homepage regressions.
run_step clean_stage_gate bash -lc '
  set -euo pipefail
  stage="$1"
  test -s "$stage/index.html"
  test -s "$stage/listings.json"
  for p in veille.html sources.html doublons.html opportunites.html localisation.html alertes.html changes.html source_health.html dedup.html opportunity.html locations.html alertes_cours.html; do
    test -s "$stage/$p"
  done
  chmod -R a+rX "$stage"
  ! grep -q "Recherche immo Réunion — moteur visuel" "$stage/index.html"
  ! grep -q "Canonique" "$stage/index.html"
  ! grep -q "Suspects" "$stage/index.html"
  ! grep -q "Match strict" "$stage/index.html"
  ! grep -q "source_health.html" "$stage/index.html"
  python3 - "$stage" <<"PY"
import json, sys
from pathlib import Path
app=Path(sys.argv[1])
data=json.loads((app/"listings.json").read_text())
items=data.get("listings") or []
assert len(items) >= 400, len(items)
assert sum(1 for x in items if x.get("local_image_url")) >= 350
assert sum(1 for x in items if isinstance(x.get("local_image_urls"), list) and len(x["local_image_urls"]) > 1) >= 100
assert sum(1 for x in items if x.get("opportunity_analysis")) == len(items)
PY
' "$PROJECT" "$CLEAN_STAGE"
run_step description_quality_stage_audit python3 "$PROJECT/tests/audit_description_quality.py" "$CLEAN_STAGE"
run_step public_delta_guard python3 "$PROJECT/scripts/audit_public_delta_guard.py" --baseline "$PROJECT/artifacts/app" --candidate "$CLEAN_STAGE" --json-out "$RUN_DIR/public_delta_guard.json"
run_step dedup_stage_audit python3 "$PROJECT/scripts/audit_dedup_public.py" --listings "$CLEAN_STAGE/listings.json" --json-out "$RUN_DIR/dedup_audit.json" --md-out "$RUN_DIR/dedup_audit.md"

if [ "$STAGE_DB_MODE" = "1" ]; then
  run_step promote_db_candidate python3 "$PROJECT/scripts/promote_db_candidate.py" --candidate "$DB" --target "$PROD_DB" --json-out "$RUN_DIR/promote_db.json"
  DB_PROMOTE_BACKUP="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("backup") or "")' "$RUN_DIR/promote_db.json")"
  DB_PROMOTE_DONE=1
  run_step rollback_db_drill python3 "$PROJECT/scripts/rollback_db_candidate.py" --backup "$DB_PROMOTE_BACKUP" --target "$PROD_DB" --json-out "$RUN_DIR/rollback_db_drill.json" --qa-cmd "python3 $PROJECT/tests/audit_db_enrichment.py --db $DB_PROMOTE_BACKUP"
fi

run_step promote_app_candidate python3 "$PROJECT/scripts/promote_app_candidate.py" --candidate "$CLEAN_STAGE" --target "$PROJECT/artifacts/app" --json-out "$RUN_DIR/promote_app.json"
BACKUP_APP="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("backup") or "")' "$RUN_DIR/promote_app.json")"
APP_SWAP_DONE=1
run_step rollback_app_drill python3 "$PROJECT/scripts/rollback_public_app.py" --backup "$BACKUP_APP" --target "$PROJECT/artifacts/app" --json-out "$RUN_DIR/rollback_app_drill.json" --qa-cmd "python3 $PROJECT/tests/audit_clean_portal.py"
run_step clean_portal_audit python3 "$PROJECT/tests/audit_clean_portal.py"
run_step description_quality_audit python3 "$PROJECT/tests/audit_description_quality.py" "$PROJECT/artifacts/app"
run_step public_quality_budget_audit python3 "$PROJECT/tests/audit_public_quality_budget.py" "$PROJECT/artifacts/app"
run_step public_storage_state_audit python3 "$PROJECT/tests/audit_public_storage_state.py" "$PROJECT/artifacts/app"
run_step public_perf_index_audit python3 "$PROJECT/tests/audit_public_perf_index.py" "$PROJECT/artifacts/app"
run_step public_seo_audit python3 "$PROJECT/tests/audit_public_seo.py" "$PROJECT/artifacts/app"
run_step publish_clean_static bash "$PROJECT/deploy/publish-traefik.sh"
run_step public_qa bash "$PROJECT/deploy/qa-public.sh"
run_step daily_summary python3 "$PROJECT/scripts/generate_daily_summary.py" --app "$PROJECT/artifacts/app" --out-dir "$RUN_DIR/daily_summary"
APP_KEEP=1
ENRICHMENT_DB_KEEP=1
DB_PROMOTE_KEEP=1

python3 - "$RUN_DIR" <<'PY'
import json, pathlib, sys
run_dir=pathlib.Path(sys.argv[1])
project=pathlib.Path('/opt/data/projects/reunion-immo-search')
app=project/'artifacts/app'
listings=json.loads((app/'listings.json').read_text())
health_summary={}
seloger={}
health_path=app/'source_health.json'
if health_path.exists():
    health=json.loads(health_path.read_text())
    health_summary=health.get('summary') or {}
    by={s.get('source'):s for s in health.get('sources', [])}
    seloger={k: by.get('seloger', {}).get(k) for k in ['status','active_rows','age_hours','severity']}
steps=[]
for p in sorted(run_dir.glob('*.status')):
    steps.append(p.read_text().strip())
print(json.dumps({
  'ok': True,
  'run_dir': str(run_dir),
  'steps': steps,
  'public_app': 'rich_static_gallery',
  'listings_exported': len(listings.get('listings') or []),
  'local_multi_galleries': sum(1 for x in (listings.get('listings') or []) if isinstance(x.get('local_image_urls'), list) and len(x['local_image_urls']) > 1),
  'opportunity_scored': sum(1 for x in (listings.get('listings') or []) if x.get('opportunity_analysis')),
  'suspects_excluded': len(listings.get('suspects') or []),
  'source_health': health_summary,
  'seloger': seloger,
  'stage_db_mode': True,
  'daily_summary': str(run_dir/'daily_summary'/'summary.json'),
  'promote_app': str(run_dir/'promote_app.json'),
  'promote_db': str(run_dir/'promote_db.json'),
  'rollback_app_drill': str(run_dir/'rollback_app_drill.json'),
  'rollback_db_drill': str(run_dir/'rollback_db_drill.json'),
  'public': 'https://immo.148.230.103.174.sslip.io/'
}, ensure_ascii=False, indent=2))
PY
