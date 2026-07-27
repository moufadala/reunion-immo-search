#!/usr/bin/env bash
set -euo pipefail

# Produit v2 : photos chez nous -> feed.json -> interface buildee.
#
# DOIT TOURNER APRES `promote_app_candidate.py`.
# Raison, verifiee le 2026-07-27 : promote_app_candidate fait un
# `shutil.rmtree(artifacts/app)` puis recopie le stage. Sans cette etape en
# fin de pipeline, le run quotidien DETRUIT feed.json, photos_manifest.json
# et /v2/ -- l'interface disparaitrait a 16:30 tous les jours.
#
# Le script ne telecharge que ce qui manque (cache_photos.py est idempotent)
# et ne supprime jamais une vignette.

PROJECT="${IMMO_PROJECT:-/opt/data/projects/reunion-immo-search}"
PY="${PY:-${IMMO_PROJECT_PYTHON:-python3}}"
APP="${IMMO_APP_PATH:-$PROJECT/artifacts/app}"
DIST="$PROJECT/webapp/dist"
# 1 = l'app v2 devient aussi la page d'accueil (/). 0 = seulement /v2/.
V2_RACINE="${IMMO_V2_AS_ROOT:-0}"

echo "== 1/3 photos chez nous =="
IMMO_APP_PATH="$APP" "$PY" "$PROJECT/scripts/cache_photos.py"

echo "== 2/3 feed =="
IMMO_FEED_OUT="$APP/feed.json" "$PY" "$PROJECT/scripts/export_feed.py" | head -3

echo "== 3/3 interface =="
if [ ! -s "$DIST/index.html" ]; then
  echo "ERREUR: build absent ($DIST/index.html). Lancer d'abord :" >&2
  echo "  cd $PROJECT/webapp && npm_config_cache=/tmp/npm-immo npm ci && npm run build" >&2
  exit 1
fi

mkdir -p "$APP/v2"
rm -rf "$APP/v2/assets"
cp -r "$DIST/." "$APP/v2/"
# feed servi depuis la racine de l'app : un seul fichier, pas de doublon de 1,3 Mo
ln -sfn ../feed.json "$APP/v2/feed.json"

if [ "$V2_RACINE" = "1" ]; then
  # bascule demandee : /v2/ devient la page principale. L'ancien portail reste
  # accessible sous /legacy/ tant qu'on ne l'a pas retire.
  if [ -s "$APP/index.html" ] && [ ! -e "$APP/legacy/index.html" ]; then
    mkdir -p "$APP/legacy"
    cp -a "$APP/index.html" "$APP/legacy/index.html"
  fi
  cp -a "$DIST/index.html" "$APP/index.html"
  # l'index buildee reference /v2/assets/... : on garde un seul jeu d'assets.
  "$PY" - "$APP/index.html" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding='utf-8')
# vite emet des chemins /assets/... : on les pointe vers /v2/assets/...
s2 = re.sub(r'(["\'])/assets/', r'\1/v2/assets/', s)
p.write_text(s2, encoding='utf-8')
print('index racine recable vers /v2/assets/ :', s != s2)
PY
fi

chmod 755 "$APP/v2" 2>/dev/null || true
find "$APP/v2" -type f -exec chmod 644 {} + 2>/dev/null || true

echo "OK  feed=$(stat -c%s "$APP/feed.json") o  v2=$(ls "$APP/v2/assets" | wc -l) assets  racine=$V2_RACINE"
