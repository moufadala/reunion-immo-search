#!/usr/bin/env bash
set -euo pipefail

HOST_DATA="${HOST_DATA:-/opt/hermes/data}"
HOST_BACKUP_DIR="${HOST_BACKUP_DIR:-/opt/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="critical-$STAMP.tar.gz"
IMAGE="${BACKUP_IMAGE:-nginx:alpine}"

# Runs against host paths via Docker because Hermes itself is containerized.
docker run --rm \
  -v /:/host \
  "$IMAGE" sh -eu -c "mkdir -p /host$HOST_BACKUP_DIR && chown 10000:10000 /host$HOST_BACKUP_DIR"

docker run --rm \
  -v "$HOST_DATA:/src/data:ro" \
  -v "$HOST_BACKUP_DIR:/backups" \
  "$IMAGE" sh -eu -c '
    cd /src
    tar -czf /backups/'"$ARCHIVE"' \
      --exclude="data/projects/reunion-immo-search/.git" \
      --exclude="data/projects/reunion-immo-search/node_modules" \
      --exclude="data/projects/reunion-immo-search/webapp/node_modules" \
      --exclude="data/projects/reunion-immo-search/webapp/dist.bak.*" \
      --exclude="data/projects/reunion-immo-search/artifacts" \
      --exclude="data/projects/reunion-immo-search/.venv" \
      --exclude="data/projects/reunion-immo-search/.serena" \
      --exclude="data/projects/reunion-immo-search/__pycache__" \
      --exclude="data/projects/reunion-immo-search/scripts/__pycache__" \
      --exclude="data/projects/reunion-immo-search/tests/__pycache__" \
      --exclude="data/projects/reunion-immo-search/data/*.bak.*" \
      --exclude="data/data/*.pre-promote-*" \
      --exclude="data/data/*.bak.*" \
      --exclude="data/skills/.curator_backups" \
      --exclude="data/scripts/__pycache__" \
      data/.env data/config.yaml data/AGENTS.md data/scripts data/projects/reunion-immo-search \
      data/data data/memories data/knowledge data/skills data/auth.json data/bin data/cron data/SOUL.md
    tar -tzf /backups/'"$ARCHIVE"' > /backups/'"$ARCHIVE"'.manifest.txt
    sha256sum /backups/'"$ARCHIVE"' > /backups/'"$ARCHIVE"'.sha256
    find /backups -maxdepth 1 -type f \( -name "critical-*.tar.gz" -o -name "critical-*.tar.gz.sha256" -o -name "critical-*.tar.gz.manifest.txt" \) -mtime +'"$RETENTION_DAYS"' -delete
  '

sha="$(docker run --rm -v "$HOST_BACKUP_DIR:/backups:ro" "$IMAGE" sh -eu -c "cut -d' ' -f1 /backups/$ARCHIVE.sha256")"
printf 'ARCHIVE=%s/%s\n' "$HOST_BACKUP_DIR" "$ARCHIVE"
printf 'SHA256=%s\n' "$sha"
