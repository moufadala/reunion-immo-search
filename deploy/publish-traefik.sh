#!/usr/bin/env bash
set -euo pipefail

# Publie l'app immo statique via Traefik + nginx container.
# Important: ce script est lancé depuis Hermes container, mais Docker daemon
# interprète les volumes côté host. /opt/data dans Hermes == /opt/hermes/data côté host.

# Canonique validé par Moufadal: portail propre avec galeries + pages de veille/intelligence,
# pas l'ancien moteur visuel "Filtres / Canonique / Suspects".
# artifacts/app est le chemin stable publié; il doit être remplacé seulement après QA stage.
HERMES_APP_DIR=${HERMES_APP_DIR:-/opt/data/projects/reunion-immo-search/artifacts/app}
HOST_APP_DIR=${HOST_APP_DIR:-/opt/hermes/data/projects/reunion-immo-search/artifacts/app}
HERMES_NGINX_CONF=${HERMES_NGINX_CONF:-/opt/data/projects/reunion-immo-search/deploy/nginx-immo-static.conf}
HOST_NGINX_CONF=${HOST_NGINX_CONF:-/opt/hermes/data/projects/reunion-immo-search/deploy/nginx-immo-static.conf}
CONTAINER_NAME=${CONTAINER_NAME:-immo-dashboard}
IMMO_HOSTNAME=${IMMO_HOSTNAME:-immo.srv1723523.hstgr.cloud}
IMMO_ALT_HOSTNAME=${IMMO_ALT_HOSTNAME:-immo.148.230.103.174.sslip.io}
NETWORK=${NETWORK:-hermes_default}
IMAGE=${IMAGE:-nginx@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752}

BASICAUTH_FILE=${BASICAUTH_FILE:-/opt/data/projects/reunion-immo-search/deploy/basicauth.users}

# Acces prive: le site expose feed.json (profils, budgets, temps de trajet vers un point prive).
# Aucune publication sans basic auth. Le hash vit uniquement ici, jamais dans le vault.
if [ ! -s "$BASICAUTH_FILE" ]; then
  echo "ERROR: basic auth users file missing: $BASICAUTH_FILE" >&2
  exit 1
fi
BASICAUTH_USERS=$(cat "$BASICAUTH_FILE")

if [ ! -s "$HERMES_APP_DIR/index.html" ] || [ ! -s "$HERMES_APP_DIR/feed.json" ] || [ ! -s "$HERMES_APP_DIR/v2/index.html" ]; then
  echo "ERROR: app files missing under Hermes path: $HERMES_APP_DIR" >&2
  exit 1
fi

# Verify the Docker daemon can see the host path before recreating the app.
if ! docker run --rm -v "$HOST_APP_DIR:/check:ro" alpine:3.20 sh -lc 'test -s /check/index.html && test -s /check/feed.json && test -s /check/v2/index.html' >/dev/null 2>&1; then
  echo "ERROR: Docker daemon cannot see app files under host path: $HOST_APP_DIR" >&2
  exit 1
fi

if [ ! -s "$HERMES_NGINX_CONF" ]; then
  echo "ERROR: nginx config missing under Hermes path: $HERMES_NGINX_CONF" >&2
  exit 1
fi

if ! docker run --rm -v "$HOST_NGINX_CONF:/check/default.conf:ro" alpine:3.20 sh -lc 'test -s /check/default.conf' >/dev/null 2>&1; then
  echo "ERROR: Docker daemon cannot see nginx config under host path: $HOST_NGINX_CONF" >&2
  exit 1
fi

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "ERROR: Docker network not found: $NETWORK" >&2
  exit 1
fi

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --network "$NETWORK" \
  --read-only \
  --tmpfs /var/cache/nginx:rw,noexec,nosuid,size=32m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  -v "$HOST_APP_DIR:/usr/share/nginx/html:ro" \
  -v "$HOST_NGINX_CONF:/etc/nginx/conf.d/default.conf:ro" \
  --label 'traefik.enable=true' \
  --label "traefik.docker.network=$NETWORK" \
  --label "traefik.http.routers.$CONTAINER_NAME.rule=Host(\`$IMMO_HOSTNAME\`) || Host(\`$IMMO_ALT_HOSTNAME\`)" \
  --label "traefik.http.routers.$CONTAINER_NAME.entrypoints=websecure" \
  --label "traefik.http.routers.$CONTAINER_NAME.tls=true" \
  --label "traefik.http.routers.$CONTAINER_NAME.tls.certresolver=letsencrypt" \
  --label "traefik.http.services.$CONTAINER_NAME.loadbalancer.server.port=80" \
  --label "traefik.http.middlewares.$CONTAINER_NAME-auth.basicauth.users=$BASICAUTH_USERS" \
  --label "traefik.http.middlewares.$CONTAINER_NAME-auth.basicauth.realm=Immo Nord-Est (prive)" \
  --label "traefik.http.routers.$CONTAINER_NAME.middlewares=$CONTAINER_NAME-auth@docker" \
  "$IMAGE"

sleep 2
docker ps --filter "name=^/$CONTAINER_NAME$" --format 'container={{.Names}} status={{.Status}} ports={{.Ports}}'
docker exec "$CONTAINER_NAME" sh -lc 'test -s /usr/share/nginx/html/index.html && test -s /usr/share/nginx/html/feed.json && test -s /usr/share/nginx/html/v2/index.html && echo FILES_OK'
docker exec "$CONTAINER_NAME" nginx -t
echo "URL=https://$IMMO_HOSTNAME/"
echo "ALT_URL=https://$IMMO_ALT_HOSTNAME/"
