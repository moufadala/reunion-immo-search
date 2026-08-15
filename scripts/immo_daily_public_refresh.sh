#!/usr/bin/env bash
set -euo pipefail

# Daily non-agent product refresh for the public Réunion immo dashboard.
# Success is JSON on stdout. Detailed logs go to the run directory.

umask 077
# Keep the project runtime hermetic. A previous run inherited a Python 3.13
# user-site path while executing the project Python 3.12 venv, which broke the
# native greenlet._greenlet extension used by Playwright.
export PYTHONNOUSERSITE=1
unset PYTHONPATH
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache-hermes}"
export PLAYWRIGHT_BROWSERS_PATH="${IMMO_PLAYWRIGHT_BROWSERS_PATH:-/opt/data/.cache/ms-playwright}"

PROJECT="/opt/data/projects/reunion-immo-search"
PY="${IMMO_PROJECT_PYTHON:-$PROJECT/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  printf 'IMMO_PROJECT_PYTHON is not executable: %s\n' "$PY" >&2
  exit 64
fi
export PY
CLEAN_PROJECT="/opt/data/projects/reunion-immo-clean-app"
ROOT="/opt/data"
export IMMO_DATA_ROOT="/opt/data"
STAMP="${IMMO_REFRESH_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${IMMO_REFRESH_RUN_DIR:-/opt/data/artifacts/immo-public-refresh/${STAMP}}"
export IMMO_REFRESH_RUN_DIR="$RUN_DIR"
PROD_DB="${IMMO_DB_PATH:-/opt/data/data/reunion_watch.db}"
STAGE_DB_MODE="${IMMO_STAGE_DB:-1}"
STAGE_DB="$RUN_DIR/reunion_watch.stage.db"
DB="$PROD_DB"
export IMMO_DB_PATH="$DB"
SELOGER_ARTIFACT="/opt/data/artifacts/realestate/seloger_multipage_results.json"
mkdir -p "$RUN_DIR"

"$PY" - "$RUN_DIR/scrapling_probe.json" <<'PYCODE'
import json, os, pathlib, sys
sys.path.insert(0, '/opt/data/scripts')
out = {
    'interpreter': sys.executable,
    'scrapling_mode_env': os.environ.get('SCRAPLING_MODE', 'auto'),
    'scrapling_active': False,
    'probe': {'available': False, 'error': 'probe_not_run'},
}
try:
    import scrapling_fetch as sf
    out['probe'] = sf.probe_scrapling()
    out['scrapling_active'] = bool(out['probe'].get('available')) and out['scrapling_mode_env'].lower() not in {'off','no','fallback','none','no-scrapling','0','false'}
except Exception as exc:
    out['probe'] = {'available': False, 'error': f'{type(exc).__name__}: {exc}'}
pathlib.Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(out, ensure_ascii=False))
PYCODE

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

report_step() {
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
  printf '%s rc=%s duration_s=%s report_only=1\n' "$name" "$rc" "$((end-start))" >"$status"
  if [ "$rc" -ne 0 ]; then
    printf 'REPORT-ONLY legacy/V2 audit failed: %s rc=%s\n' "$name" "$rc" >&2
    tail -n 40 "$stderr" >&2 || true
    tail -n 40 "$stdout" >&2 || true
  fi
  return 0
}

run_step preflight_disk_guard "$PY" - "$PROJECT" "${IMMO_MIN_FREE_GB:-15}" <<'PY'
import json, shutil, sys
from pathlib import Path
project = Path(sys.argv[1])
min_gb = float(sys.argv[2])
usage = shutil.disk_usage(project)
free_gb = usage.free / (1024 ** 3)
payload = {
    'ok': free_gb >= min_gb,
    'free_gb': round(free_gb, 2),
    'min_free_gb': min_gb,
    'path': str(project),
}
print(json.dumps(payload, ensure_ascii=False))
if not payload['ok']:
    raise SystemExit(f"disk guard failed: free_gb={free_gb:.2f} < min_free_gb={min_gb:.2f}")
PY

run_step pipeline_invariants "$PY" "$PROJECT/tests/test_pipeline_invariants.py"
run_step mapping_golden_regression "$PY" "$PROJECT/tests/test_mapping_golden.py"
run_step runtime_script_contracts "$PY" "$PROJECT/tests/audit_pipeline_script_contracts.py" --runtime-check
run_step browser_runtime_import_gate "$PY" - <<'PY'
import importlib
import json
import site
import sys

minor = f"python{sys.version_info.major}.{sys.version_info.minor}"
foreign_user_site = [
    path for path in sys.path
    if "/.local/lib/python" in path and minor not in path
]
if foreign_user_site:
    raise SystemExit(
        "foreign Python user-site on sys.path for runtime %s: %s"
        % (minor, foreign_user_site)
    )

playwright = importlib.import_module("playwright.sync_api")
greenlet_ext = importlib.import_module("greenlet._greenlet")
print(json.dumps({
    "ok": True,
    "executable": sys.executable,
    "version": sys.version.split()[0],
    "enable_user_site": site.ENABLE_USER_SITE,
    "playwright": getattr(playwright, "__file__", None),
    "greenlet_ext": getattr(greenlet_ext, "__file__", None),
}, ensure_ascii=False, indent=2))
PY

# Roll back source-detail/stage DB and public-app mutations if a downstream gate fails.
ENRICHMENT_DB_BACKUP=""
ENRICHMENT_DB_KEEP=0
DB_PROMOTE_DONE=0
DB_PROMOTE_KEEP=0
DB_PROMOTE_BACKUP=""
APP_SWAP_DONE=0
APP_KEEP=0
BACKUP_APP=""
LOCAL_AUDIT_PID=""
restore_on_failure() {
  local rc=$?
  if [ -n "${LOCAL_AUDIT_PID:-}" ]; then kill "$LOCAL_AUDIT_PID" >/dev/null 2>&1 || true; fi
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
    # Recreate nginx after replacing the bind-mounted directory. Otherwise
    # Docker can keep serving the removed inode and public checks see an empty
    # /usr/share/nginx/html (403 on /, 404 on files) until a manual publish.
    # Best-effort only: keep the original failure as the script exit code.
    bash "$PROJECT/deploy/publish-traefik.sh" >&2 || true
  fi
  exit "$rc"
}
trap restore_on_failure EXIT

if [ "$STAGE_DB_MODE" = "1" ]; then
  run_step stage_db_init "$PY" -c '
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
  "$PY" "$ROOT/scripts/realestate_watch.py" \
    --refresh \
    --db "$DB" \
    --run-dir "$RUN_DIR/realestate_watch" \
    --max-price 1200 \
    --min-price 450 \
    --min-rooms 2 \
    --limit 15

# SeLoger is a separate CDP collector: collect a fresh artifact, then import it into DB.
# Use the repo-controlled collector, not the legacy /opt/data/scripts copy: the
# latter used to prepend a Python 3.13 user-site to this Python 3.12 venv and
# broke greenlet._greenlet at runtime.
run_step playwright_import_gate "$PY" "$PROJECT/tests/audit_runtime_playwright_import.py"
run_step seloger_cdp_collect "$PY" "$PROJECT/scripts/seloger_multi_page.py"
run_step seloger_import "$PY" "$PROJECT/src/import_seloger_multipage.py" --db "$DB" --artifact "$SELOGER_ARTIFACT" --max-age-hours 6

# Recover source-detail descriptions before building the public artifact.
# This is deliberately idempotent and guarded: it snapshots DB internally and
# updates only when the recovered detail text is richer/cleaner than the current row.
ENRICHMENT_DB_BACKUP="$RUN_DIR/reunion_watch.before-source-detail.db"
run_step source_detail_db_backup "$PY" -c '
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
' "$DB" "$ENRICHMENT_DB_BACKUP"
ENRICH_ARGS=("$PROJECT/scripts/enrich_source_details_v3.py" --db "$DB" --sleep 0.08 --report "$RUN_DIR/source_detail_enrichment.jsonl")
if [ "${IMMO_ENABLE_LLM:-1}" = "1" ] && [ -n "${OPENROUTER_API_KEY:-}" ]; then
  ENRICH_ARGS+=(--llm --llm-provider openrouter --llm-limit "${IMMO_LLM_LIMIT:-200}" --llm-report "$RUN_DIR/llm_extraction.jsonl")
elif [ "${IMMO_ENABLE_LLM:-1}" = "1" ]; then
  echo "source_detail_enrichment: OPENROUTER_API_KEY missing; continuing without LLM" >&2
fi
run_step source_detail_enrichment "$PY" "${ENRICH_ARGS[@]}"

# Trouve le 27/07 (soir) : detail_enrich.py (adresse/etage/chambres/description
# complete -> table listing_detail) n'etait appele par AUCUN cron -- seulement
# a la main pendant des sessions passees (343/695 annonces couvertes, zimo et
# seloger a 0%). Cause zimo trouvee et corrigee : fetch() n'envoyait pas de
# Referer -> 403 systematique sur la page de detail elle-meme (verifie : avec
# Referer, 12/12 ok). seloger reste exclu : 403 meme avec Referer, il faudrait
# passer par chromium-cdp (deja utilise pour ses LISTES) -- pas fait cette
# session, chantier a part entiere. superimmo exclu (hCaptcha reel, cf.
# handoff 27/07d). Rythme volontairement prudent : round-robin deja integre
# au script (protection anti-bannissement), --limit bas, --only-active pour
# prioriser ce qui est montre. ~4-5 jours pour rattraper les 259 annonces
# actives actuelles, sans jamais marteler un site.
# MaJ 27/07 (soir, suite) : seloger route desormais via chromium-cdp dans
# fetch_cdp() (403 en HTTP simple, 200 confirme via CDP, meme pattern que
# seloger_multi_page.py) -- retire de l'exclusion. superimmo reste exclu
# (hCaptcha reel, aucun contournement tente).
run_step detail_enrich "$PY" "$PROJECT/scripts/detail_enrich.py" --limit 60 --delay 5 --only-active --exclude superimmo

# Recompute through a safe two-stage pipeline:
# 1) build the technical app in an isolated stage, not in artifacts/app served by nginx;
# 2) enrich/cache galleries and recompute intelligence in that stage;
# 3) build the family-facing clean portal with side pages + aliases;
# 4) swap artifacts/app only after local gates pass, then publish.
TECH_STAGE="$PROJECT/artifacts/daily-tech-stage-${STAMP}"
CLEAN_STAGE="$PROJECT/artifacts/daily-clean-stage-${STAMP}"
rm -rf "$TECH_STAGE" "$CLEAN_STAGE"
mkdir -p "$TECH_STAGE" "$CLEAN_STAGE"

run_step db_enrichment_audit "$PY" "$PROJECT/tests/audit_db_enrichment.py" --db "$DB"
export IMMO_MEDIA_COPY_MODE=hardlink
run_step build_technical_app bash -lc 'cd "$0" && "$PY" src/build_app.py --db "$1" --out "$2"' "$PROJECT" "$DB" "$TECH_STAGE"
run_step source_health_audit "$PY" "$PROJECT/tests/audit_source_health.py"
run_step gallery_enrichment bash -lc 'cd "$0" && "$PY" scripts/enrich_listing_galleries.py --db "$1" --app "$2" --report "$3" --manifest "$4"' "$PROJECT" "$DB" "$TECH_STAGE" "$RUN_DIR/gallery-enrichment-report.md" "$RUN_DIR/gallery-enrichment-manifest.json"
run_step seed_photo_cache bash -lc 'set -euo pipefail; project="$1"; stage="$2"; if [ -d "$project/artifacts/app/thumbs" ]; then "$PY" "$project/scripts/media_link_copy.py" "$project/artifacts/app/thumbs" "$stage/thumbs" --media-mode hardlink --dirs-exist-ok --existing skip; fi' _ "$PROJECT" "$TECH_STAGE"
run_step photo_cache bash -lc 'cd "$0" && IMMO_APP_PATH="$1" PHOTO_WORKERS=8 PHOTO_TIMEOUT=18 "$PY" scripts/cache_listing_images.py' "$PROJECT" "$TECH_STAGE"
run_step intelligence_layers bash -lc 'cd "$0" && IMMO_DB_PATH="$1" IMMO_APP_PATH="$2" "$PY" src/immo_intelligence_layers.py' "$PROJECT" "$DB" "$TECH_STAGE"
run_step build_clean_portal bash -lc 'cd "$0" && IMMO_APP_PATH="$1" IMMO_OUT_PATH="$2" "$PY" scripts/build_clean_portal_v1.py' "$PROJECT" "$TECH_STAGE" "$CLEAN_STAGE"
run_step p0_product_polish bash -lc 'cd "$0" && "$PY" scripts/p0_product_polish.py --app "$1"' "$PROJECT" "$CLEAN_STAGE"
run_step product_hardening_v5 bash -lc 'cd "$0" && "$PY" scripts/patch_product_hardening_v5.py --app "$1"' "$PROJECT" "$CLEAN_STAGE"
run_step domain_inventory_oracle_v2 "$PY" "$PROJECT/scripts/generate_domain_inventory_and_oracle_v2.py" --app "$CLEAN_STAGE"
run_step slim_public_listings "$PY" "$PROJECT/scripts/slim_public_listings.py" "$CLEAN_STAGE"
run_step listing_changes "$PY" "$PROJECT/src/listing_changes.py" --limit 80 --out "$CLEAN_STAGE/changes.json" --html-out "$RUN_DIR/changes.html"
report_step enhance_changes_decision bash -lc 'cp "$2" "$3/changes.json" && "$0" "$1" --app "$3"' "$PY" "$PROJECT/scripts/enhance_changes_decision_view.py" "$CLEAN_STAGE/changes.json" "$RUN_DIR"
run_step wave2_detail_geo_photo "$PY" "$PROJECT/scripts/patch_wave2_lot_c_detail_geo_photo.py" --app "$CLEAN_STAGE"
run_step opportunity_dedup_calibration "$PY" "$PROJECT/scripts/generate_opportunity_calibration.py" --app "$CLEAN_STAGE"
# P0 Privacy: generate saved_searches output to run_dir only (never to the public-served stage).
# The saved_searches_admin.json contains personal search criteria (name, budget, family).
# It must NOT be promoted to artifacts/app or served by nginx.
run_step saved_search_admin_stage "$PY" "$PROJECT/src/saved_search_admin.py" --listings "$CLEAN_STAGE/listings.json" --out "$RUN_DIR/saved_searches_admin.json" --html-out "$RUN_DIR/saved_searches.html"

# Keep files readable by nginx despite this wrapper's restrictive umask, and guard against homepage regressions.
run_step clean_stage_gate bash -lc '
  set -euo pipefail
  stage="$1"
  test -s "$stage/listings.json"
  for p in veille.html sources.html doublons.html opportunites.html localisation.html alertes.html dedup.html; do
    test ! -e "$stage/$p"
  done
  chmod -R a+rX "$stage"
  "$PY" - "$stage" <<"PY"
import json, os, sys
from pathlib import Path
app=Path(sys.argv[1])
data=json.loads((app/"listings.json").read_text())
items=data.get("listings") or []
min_public=int(os.environ.get("IMMO_MIN_PUBLIC_LISTINGS", "150"))
min_local_ratio=float(os.environ.get("IMMO_MIN_LOCAL_IMAGE_RATIO", "0.70"))
min_multi_ratio=float(os.environ.get("IMMO_MIN_MULTI_IMAGE_RATIO", "0.15"))
assert len(items) >= min_public, {"count": len(items), "min": min_public}
local=sum(1 for x in items if x.get("local_image_url"))
multi=sum(1 for x in items if isinstance(x.get("local_image_urls"), list) and len(x["local_image_urls"]) > 1)
assert local >= int(len(items) * min_local_ratio), {"local": local, "count": len(items), "min_ratio": min_local_ratio}
assert multi >= int(len(items) * min_multi_ratio), {"multi": multi, "count": len(items), "min_ratio": min_multi_ratio}
assert sum(1 for x in items if x.get("opportunity_analysis")) == len(items)
PY
' "$PROJECT" "$CLEAN_STAGE"
run_step description_quality_stage_audit bash -lc '"$0" "$1" "$2"; rc=$?; [ "$rc" -eq 0 ] || echo "REPORT-ONLY(Phase0) description_quality_stage_audit rc=$rc non-bloquant" >&2; exit 0' "$PY" "$PROJECT/tests/audit_description_quality.py" "$CLEAN_STAGE"
report_step public_delta_guard "$PY" "$PROJECT/scripts/audit_public_delta_guard.py" --baseline "$PROJECT/artifacts/app" --candidate "$CLEAN_STAGE" --json-out "$RUN_DIR/public_delta_guard.json"
run_step dedup_stage_audit "$PY" "$PROJECT/scripts/audit_dedup_public.py" --listings "$CLEAN_STAGE/listings.json" --json-out "$RUN_DIR/dedup_audit.json" --md-out "$RUN_DIR/dedup_audit.md"
run_step stage_source_freshness_gate "$PY" - "$CLEAN_STAGE/source_health.json" <<'PY'
import json, os, sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
summary = payload.get('summary') or {}
sources = payload.get('sources') or []
coverage_low = summary.get('coverage_below_threshold') or []
fresh = int((summary.get('status_counts') or {}).get('fresh') or 0)
critical_attention = summary.get('stale_or_attention_critical') or []
source_count = summary.get('source_count')
report_only = os.environ.get('IMMO_FRESHNESS_REPORT_ONLY', '0') == '1'
problems = []
if fresh < 11:
    problems.append(f'fresh={fresh}/{source_count} < 11')
if len(critical_attention) > 2:
    problems.append(f'too many critical sources need attention: {critical_attention}')
if coverage_low:
    problems.append(f'source coverage below threshold (coverage-low): {coverage_low}')
if problems and not report_only:
    raise SystemExit('stage source freshness gate failed: ' + '; '.join(problems))
print(json.dumps({
    'ok': not problems,
    'report_only': report_only,
    'problems': problems,
    'fresh_sources': fresh,
    'source_count': source_count,
    'critical_attention': critical_attention,
    'coverage_low': coverage_low,
    'last_seen': {s.get('source'): s.get('last_seen_at') for s in sources},
}, ensure_ascii=False))
PY

if [ "$STAGE_DB_MODE" = "1" ]; then
  run_step promote_db_candidate "$PY" "$PROJECT/scripts/promote_db_candidate.py" --candidate "$DB" --target "$PROD_DB" --max-drop-pct "${IMMO_MAX_DB_DROP_PCT:-15}" --json-out "$RUN_DIR/promote_db.json"
  DB_PROMOTE_BACKUP="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("backup") or "")' "$RUN_DIR/promote_db.json")"
  DB_PROMOTE_DONE=1
  run_step rollback_db_drill "$PY" "$PROJECT/scripts/rollback_db_candidate.py" --backup "$DB_PROMOTE_BACKUP" --target "$PROD_DB" --json-out "$RUN_DIR/rollback_db_drill.json" --qa-cmd "$PY $PROJECT/tests/audit_db_enrichment.py --db $DB_PROMOTE_BACKUP"
fi

run_step pre_promote_artifact_retention "$PY" "$PROJECT/scripts/artifact_retention.py" \
  --artifacts "$PROJECT/artifacts" \
  --keep-daily "${IMMO_RETENTION_KEEP_DAILY:-1}" \
  --keep-pre-promote "${IMMO_RETENTION_KEEP_PRE_PROMOTE:-1}" \
  --protect "$TECH_STAGE" \
  --protect "$CLEAN_STAGE" \
  --apply \
  --json-out "$RUN_DIR/pre_promote_artifact_retention.json"

export IMMO_MEDIA_COPY_MODE=hardlink
run_step promote_app_candidate "$PY" "$PROJECT/scripts/promote_app_candidate.py" --candidate "$CLEAN_STAGE" --target "$PROJECT/artifacts/app" --max-drop-pct "${IMMO_MAX_APP_DROP_PCT:-15}" --json-out "$RUN_DIR/promote_app.json"
BACKUP_APP="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("backup") or "")' "$RUN_DIR/promote_app.json")"
APP_SWAP_DONE=1
run_step rollback_app_drill "$PY" "$PROJECT/scripts/rollback_public_app.py" --backup "$BACKUP_APP" --target "$PROJECT/artifacts/app" --json-out "$RUN_DIR/rollback_app_drill.json"

# Bug trouve le 27/07 (soir) : promote_app_candidate ci-dessus fait un rmtree
# puis recopie CLEAN_STAGE, qui ne contient ni v2/ ni feed.json ni
# photos_manifest.json (produits par une chaine SEPAREE). build_product_v2.sh
# existait deja depuis le matin du 27/07 mais n'etait jamais appele ici : le
# premier run quotidien reel apres sa creation aurait donc efface /v2/ en
# silence. DOIT rester APRES promote_app_candidate (l'ordre est l'invariant).
run_step build_product_v2 env IMMO_V2_AS_ROOT=1 IMMO_MAX_ACTIVES_SANS_PHOTO_RATIO=0.08 bash "$PROJECT/scripts/build_product_v2.sh"

run_step product_v2_gate env IMMO_MAX_ACTIVES_SANS_PHOTO_RATIO=0.08 "$PY" "$PROJECT/scripts/audit_product_v2.py" "$PROJECT/artifacts/app"
run_step immo_health_gate "$PY" "$PROJECT/scripts/immo_health_checks.py" --gate-chain --json "$RUN_DIR/immo_health_checks.json"

run_step clean_portal_audit bash -lc '"$0" "$1"; rc=$?; [ "$rc" -eq 0 ] || echo "REPORT-ONLY(Phase0/V2-root) clean_portal_audit rc=$rc non-bloquant: audit legacy racine incompatible avec IMMO_V2_AS_ROOT=1" >&2; exit 0' "$PY" "$PROJECT/tests/audit_clean_portal.py"
run_step description_quality_audit bash -lc '"$0" "$1" "$2"; rc=$?; [ "$rc" -eq 0 ] || echo "REPORT-ONLY(Phase0) description_quality_audit rc=$rc non-bloquant" >&2; exit 0' "$PY" "$PROJECT/tests/audit_description_quality.py" "$PROJECT/artifacts/app"
report_step public_quality_budget_audit "$PY" "$PROJECT/tests/audit_public_quality_budget.py" "$PROJECT/artifacts/app"
report_step public_storage_state_audit bash -lc '"$0" "$1"; rc=$?; [ "$rc" -eq 0 ] || echo "REPORT-ONLY(Phase0/V2-root) public_storage_state_audit rc=$rc non-bloquant: audit legacy homepage incompatible avec IMMO_V2_AS_ROOT=1" >&2; exit 0' "$PY" "$PROJECT/tests/audit_public_storage_state.py" "$PROJECT/artifacts/app"
report_step public_perf_index_audit "$PY" "$PROJECT/tests/audit_public_perf_index.py" "$PROJECT/artifacts/app"
report_step public_seo_audit "$PY" "$PROJECT/tests/audit_public_seo.py" "$PROJECT/artifacts/app"
run_step build_manifest "$PY" "$PROJECT/scripts/generate_build_manifest.py" --app "$PROJECT/artifacts/app" --run-dir "$RUN_DIR" --db "$PROD_DB"
report_step build_manifest_audit "$PY" "$PROJECT/tests/audit_build_manifest.py" "$PROJECT/artifacts/app"
run_step public_app_permissions bash -lc '
  set -euo pipefail
  app="$1"
  find "$app" -type d -exec chmod 755 {} +
  find "$app" -type f -exec chmod 644 {} +
' _ "$PROJECT/artifacts/app"
run_step publish_clean_static bash "$PROJECT/deploy/publish-traefik.sh"
run_step public_v2_qa env IMMO_QA_STRICT_LEGACY="${IMMO_QA_STRICT_LEGACY:-1}" "$PY" "$PROJECT/scripts/qa_public_v2.py" --json "$RUN_DIR/qa_public_v2.json"
report_step public_qa bash "$PROJECT/deploy/qa-public.sh"
LOCAL_AUDIT_PORT="${IMMO_LOCAL_AUDIT_PORT:-18089}"
(cd "$PROJECT/artifacts/app" && "$PY" -m http.server "$LOCAL_AUDIT_PORT" --bind 127.0.0.1 >"$RUN_DIR/local_audit_server.stdout" 2>"$RUN_DIR/local_audit_server.stderr") &
LOCAL_AUDIT_PID=$!
sleep 1
report_step public_user_search_audit env IMMO_PUBLIC_URL="http://127.0.0.1:$LOCAL_AUDIT_PORT/" "$PY" "$PROJECT/tests/audit_user_search_cases.py"
report_step public_changes_filter_audit env IMMO_CHANGES_URL="http://127.0.0.1:$LOCAL_AUDIT_PORT/changes.html?rev=changes-audit" "$PY" "$PROJECT/tests/audit_changes_page_filters.py"
run_step daily_summary "$PY" "$PROJECT/scripts/generate_daily_summary.py" --app "$PROJECT/artifacts/app" --out-dir "$RUN_DIR/daily_summary"
run_step ops_cockpit "$PY" "$PROJECT/scripts/generate_ops_cockpit.py" --app "$PROJECT/artifacts/app" --run-dir "$RUN_DIR" --out "$RUN_DIR/ops_cockpit"
# P0 Privacy: saved_search_admin writes to run_dir only; do not promote to public app.
run_step saved_search_admin "$PY" "$PROJECT/src/saved_search_admin.py" --out "$RUN_DIR/saved_searches_admin_final.json" --html-out "$RUN_DIR/saved_searches_final.html"
report_step ops_quality_audit "$PY" "$PROJECT/tests/audit_ops_cockpit.py" "$RUN_DIR/ops_cockpit"
report_step ops_browser_static_audit "$PY" "$PROJECT/tests/audit_ops_cockpit_browser_static.py" "$RUN_DIR/ops_cockpit"
report_step search_alerts_audit "$PY" "$PROJECT/tests/audit_search_alerts.py"
report_step detail_geo_photo_prudent_audit "$PY" "$PROJECT/tests/audit_detail_geo_photo_prudent.py" "$PROJECT/artifacts/app"
report_step opportunity_v2_audit "$PY" "$PROJECT/tests/audit_opportunity_v2.py" "$PROJECT/artifacts/app"
report_step dedup_display_audit "$PY" "$PROJECT/tests/audit_dedup_display.py" "$PROJECT/artifacts/app"
report_step public_dedup_canonical_display_audit "$PY" "$PROJECT/tests/audit_public_dedup_canonical_display.py" "$PROJECT/artifacts/app"
run_step public_v2_qa_final env IMMO_QA_STRICT_LEGACY="${IMMO_QA_STRICT_LEGACY:-1}" "$PY" "$PROJECT/scripts/qa_public_v2.py" --json "$RUN_DIR/qa_public_v2_final.json"
run_step artifact_retention "$PY" "$PROJECT/scripts/artifact_retention.py" --artifacts "$PROJECT/artifacts" --keep-daily "${IMMO_RETENTION_KEEP_DAILY:-1}" --keep-pre-promote "${IMMO_RETENTION_KEEP_PRE_PROMOTE:-1}" --apply --json-out "$RUN_DIR/artifact_retention.json"
run_step immo_health_state_save "$PY" "$PROJECT/scripts/immo_health_checks.py" --warn-only --save-state --json "$RUN_DIR/immo_health_state_save.json"
# P1 final postflight: this must remain the last blocking publication gate.
# Keep it after every producer/audit that can touch artifacts/app, but before
# APP_KEEP/DB_PROMOTE_KEEP so a failure still triggers the rollback trap.
run_step postflight_public_contract env IMMO_MAX_FEED_AGE_H="${IMMO_MAX_FEED_AGE_H:-2}" "$PY" "$PROJECT/scripts/postflight_public_contract.py" --app "$PROJECT/artifacts/app" --container "${IMMO_PUBLIC_CONTAINER:-immo-dashboard}" --json-out "$RUN_DIR/postflight_public_contract.json"
APP_KEEP=1
ENRICHMENT_DB_KEEP=1
DB_PROMOTE_KEEP=1

"$PY" - "$RUN_DIR" <<'PY'
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
