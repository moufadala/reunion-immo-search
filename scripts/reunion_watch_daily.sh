#!/usr/bin/env bash
set -euo pipefail

umask 077
LOCK_FILE="/tmp/reunion_watch_daily.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "RUN Watch quotidien: un run est déjà en cours, sortie sans lancer un second scraping."
  exit 0
fi

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache-hermes}"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="/opt/data/artifacts/reunion-watch/${STAMP}_daily"
LOG_DIR="/opt/data/logs"
mkdir -p "$RUN_DIR"
mkdir -p "$LOG_DIR"

PIPELINE_JSON="$RUN_DIR/pipeline_result.json"
COHERENCE_JSON="$RUN_DIR/coherence_result.json"
IMMO_REFRESH_JSON="$RUN_DIR/immo_public_refresh_result.json"
PIPELINE_LOG="$LOG_DIR/reunion_watch_daily_${STAMP}.log"

python3 /opt/data/scripts/reunion_watch_pipeline.py \
  --run-dir "$RUN_DIR" \
  --skip-flight \
  --flight-quick \
  --flight-dates 2026-07-19 \
  --return-date 2026-07-26 \
  --kiwi-proxy socks5://127.0.0.1:1055 \
  --max-price 1200 \
  --min-price 450 \
  --min-rooms 2 \
  --limit 15 >"$PIPELINE_JSON" 2>"$PIPELINE_LOG"

# 2026-07-21 (decision Moufadal): scraping VOLS coupe -- projet vols-run en refonte.
# Reversible: retirer --skip-flight ci-dessus et restaurer reunion_watch_daily.sh.bak_20260721
FLIGHT_RUN_DIR=""
COHERENCE_STATUS="SKIPPED-VOLS-COUPES"

IMMO_REFRESH_RUN_DIR="$RUN_DIR/immo_public_refresh" \
  IMMO_REFRESH_STAMP="$STAMP" \
  IMMO_FRESHNESS_REPORT_ONLY=1 \
  /opt/data/scripts/immo_daily_public_refresh.sh >"$IMMO_REFRESH_JSON" 2>>"$PIPELINE_LOG" || {
    rc=$?
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] CRITICAL immo_daily_public_refresh failed rc=$rc json=$IMMO_REFRESH_JSON" >>"$PIPELINE_LOG"
    exit "$rc"
  }

python3 - <<PY
import json
from pathlib import Path


def load_json_file(path, default=None, *, allow_concatenated=False):
    path = Path(path)
    if not path.exists():
        return default
    text = path.read_text(encoding='utf-8')
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if not allow_concatenated:
            preview = text[:240].replace('\n', '\\n')
            raise RuntimeError(f'JSON parse failed file={path} bytes={len(text.encode())} line={exc.lineno} col={exc.colno} preview={preview!r}') from exc
    decoder = json.JSONDecoder()
    idx = 0
    objects = []
    n = len(text)
    try:
        while idx < n:
            while idx < n and text[idx].isspace():
                idx += 1
            if idx >= n:
                break
            obj, end = decoder.raw_decode(text, idx)
            objects.append(obj)
            idx = end
    except json.JSONDecodeError as exc:
        preview = text[max(0, idx-80):idx+240].replace('\n', '\\n')
        raise RuntimeError(f'JSON parse failed file={path} bytes={len(text.encode())} line={exc.lineno} col={exc.colno} preview={preview!r}') from exc
    if not objects:
        return default
    return objects[-1]

run=Path('$RUN_DIR')
manifest=load_json_file(run/'manifest.json')
coh=load_json_file(run/'coherence_air_austral_vs_kiwi.json', {'comparisons': [], 'aberrant_count': 'n/a'})
immo_refresh=load_json_file(Path('$IMMO_REFRESH_JSON'), {}, allow_concatenated=True)
fs=manifest['flight_summary']; ims=manifest['immo_summary']
print('RUN Watch quotidien')
prod=[i for i in fs.get('items', []) if i.get('status')=='prod-candidate']
print(f"Vols: {len(prod)}/{len(fs.get('items', []))} sources exploitables")
for item in prod:
    label=item.get('family') or item.get('name') or 'source'
    offers=item.get('offer_count') if item.get('offer_count') is not None else 'n/a'
    print(f"- {label}: {item.get('cheapest_label', 'n/a')} · {offers} offres · {item.get('duration_s')}s")
print(f"Cohérence Air Austral vs Kiwi: {len(coh.get('comparisons', []))} comparaison(s), aberrants={coh.get('aberrant_count')}")
for c in coh.get('comparisons', [])[:3]:
    print(f"- {c.get('flight_number')}: officiel {c.get('official_price_eur')}€ vs Kiwi {c.get('aggregator_price_eur')}€ → {c.get('delta_eur')}€ ({c.get('delta_pct')}%) {c.get('verdict')}")
print(f"Immo: {ims.get('selected_count', 0)} annonces sélectionnées / {len(ims.get('sources') or {})} sources")
if immo_refresh:
    sh=immo_refresh.get('source_health') or {}
    sel=immo_refresh.get('seloger') or {}
    print(f"Dashboard immo public: {immo_refresh.get('listings_exported', 'n/a')} annonces exportées · sources fresh={sh.get('status_counts', {}).get('fresh', 'n/a')}/{sh.get('source_count', 'n/a')} · SeLoger {sel.get('status', 'n/a')} {sel.get('active_rows', 'n/a')} actives")
print(f"Dashboard: {run/'dashboard.html'}")
print(f"Rapport cohérence: {run/'coherence_air_austral_vs_kiwi.md'}")
print(f"Log: {Path('$PIPELINE_LOG')}")
PY
