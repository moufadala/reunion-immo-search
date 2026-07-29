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

export_feed_once() {
  # Ne PAS piper directement dans `head` : sous `set -o pipefail`, la fermeture
  # anticipee du tube par head declenche un BrokenPipeError cote Python, qui
  # fait echouer toute la ligne -- et donc tout le script AVANT la suite.
  FEED_LOG=$(mktemp)
  IMMO_FEED_OUT="$APP/feed.json" "$PY" "$PROJECT/scripts/export_feed.py" > "$FEED_LOG"
  head -3 "$FEED_LOG"
  rm -f "$FEED_LOG"
}

echo "== 1/6 extraction galeries JSON =="
"$PY" "$PROJECT/scripts/enrich_listing_photos.py"

# promote_app_candidate recrée artifacts/app depuis le clean-stage, donc feed.json
# n'existe plus ici. refresh_domimmo_photo_urls cible les annonces Domimmo du
# feed public courant : il lui faut un feed préliminaire avant son passage.
echo "== 2/6 feed preliminaire =="
export_feed_once

echo "== 3/6 rafraichissement cible domimmo =="
"$PY" "$PROJECT/scripts/refresh_domimmo_photo_urls.py"

echo "== 4/6 photos chez nous =="
echo "mode photos: offline=${PHOTO_OFFLINE:-0} max_par_annonce=${PHOTO_MAX_PER_LISTING:-20}"
IMMO_APP_PATH="$APP" "$PY" "$PROJECT/scripts/cache_photos.py"

echo "== 5/6 feed final =="
export_feed_once

echo "== 6/6 interface =="
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
  # Vite emet des chemins RELATIFS (./assets/...), pas absolus : depuis la
  # racine ca pointerait vers artifacts/app/assets/ (inexistant, seul
  # v2/assets/ existe). Verifie le 27/07 : sans ce correctif la racine
  # chargeait (200) mais le JS/CSS etait en 404 -- page blanche silencieuse,
  # aucune erreur visible sur l'index lui-meme. On garde un seul jeu d'assets.
  "$PY" "$PROJECT/scripts/_rewrite_root_asset_paths.py" "$APP/index.html"
fi

chmod 755 "$APP/v2" 2>/dev/null || true
find "$APP/v2" -type f -exec chmod 644 {} + 2>/dev/null || true

echo "== 6/6 QA produit =="
"$PY" "$PROJECT/scripts/audit_product_v2.py" "$APP"

echo "OK  feed=$(stat -c%s "$APP/feed.json") o  v2=$(ls "$APP/v2/assets" | wc -l) assets  racine=$V2_RACINE"
