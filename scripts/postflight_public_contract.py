#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Final publication contract. This is intentionally stricter than
# immo_public_monitor.py: monitor freshness is a 36h watchdog, postflight is a
# same-run gate and defaults to 2h.
LEGACY = (
    "veille.html",
    "sources.html",
    "doublons.html",
    "opportunites.html",
    "localisation.html",
    "alertes.html",
    "dedup.html",
)
LEGACY_PUBLISHED_FILES = (
    "ops.html",
    "source_health.html",
    "locations.html",
    "locations.json",
    "opportunity.html",
    "opportunity.json",
    "dedup_groups.json",
    "alertes_cours.html",
    "changes.html",
)
REQUIRED_IN_CONTAINER = (
    "index.html",
    "feed.json",
    "v2/index.html",
    "v2/feed.json",
)


def parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def add_check(checks: list[dict[str, object]], name: str, ok: bool, message: str, evidence: object = None) -> None:
    item: dict[str, object] = {"check": name, "ok": bool(ok), "message": message}
    if evidence is not None:
        item["evidence"] = evidence
    checks.append(item)


def container_check(container: str, required: tuple[str, ...], checks: list[dict[str, object]]) -> None:
    quoted = " ".join(required)
    cmd = (
        "set -eu; count=$(find /usr/share/nginx/html -maxdepth 2 -type f -size +0c | wc -l); "
        "echo file_count=$count; [ \"$count\" -gt 0 ] || exit 10; "
        f"for f in {quoted}; do [ -s /usr/share/nginx/html/$f ] || "
        "{ echo missing_or_empty=$f >&2; exit 11; }; done"
    )
    proc = subprocess.run(
        ["docker", "exec", container, "sh", "-lc", cmd],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    evidence = {"rc": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip(), "required": required}
    add_check(
        checks,
        "container_files_visible",
        proc.returncode == 0,
        "le conteneur voit les fichiers publics requis" if proc.returncode == 0 else "le conteneur ne voit pas les fichiers publics requis",
        evidence,
    )


def public_http_paths(app: Path) -> tuple[str, ...]:
    paths = ["/index.html", "/feed.json"]
    try:
        html = (app / "index.html").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        html = ""
    for raw in re.findall(r"(?:src|href)=[\"']([^\"']+\.(?:js|css))(?:\?[^\"']*)?[\"']", html):
        parsed = urlparse(raw)
        if parsed.scheme or raw.startswith("//") or raw.startswith("data:"):
            continue
        path = urljoin("/index.html", raw)
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def container_http_check(container: str, paths: tuple[str, ...], checks: list[dict[str, object]]) -> None:
    urls = ["http://127.0.0.1" + p for p in paths]
    quoted_urls = " ".join(shlex.quote(u) for u in urls)
    cmd = (
        "set -eu; "
        f"for u in {quoted_urls}; do "
        "code=$(wget -q -S -O /dev/null \"$u\" 2>&1 | sed -n 's/.*HTTP\\/[0-9.]* \\([0-9][0-9][0-9]\\).*/\\1/p' | tail -1); "
        "printf '%s -> HTTP %s\\n' \"$u\" \"${code:-NO_CODE}\"; "
        "[ \"${code:-}\" = 200 ] || exit 12; "
        "done"
    )
    proc = subprocess.run(
        ["docker", "exec", container, "sh", "-lc", cmd],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=45,
    )
    evidence = {"rc": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip(), "paths": paths}
    add_check(
        checks,
        "container_http_served",
        proc.returncode == 0,
        "nginx sert réellement index/feed/assets en HTTP 200" if proc.returncode == 0 else "nginx ne sert pas tous les fichiers publics requis en HTTP 200",
        evidence,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="Chemin artifacts/app final")
    ap.add_argument("--container", default=os.environ.get("IMMO_PUBLIC_CONTAINER", "immo-dashboard"))
    ap.add_argument("--max-feed-age-hours", type=float, default=float(os.environ.get("IMMO_MAX_FEED_AGE_H", "2")))
    ap.add_argument("--json-out")
    args = ap.parse_args()

    app = Path(args.app)
    checks: list[dict[str, object]] = []
    checked_at = datetime.now(timezone.utc)

    legacy_present = [name for name in (*LEGACY, *LEGACY_PUBLISHED_FILES) if (app / name).exists()]
    add_check(
        checks,
        "no_legacy_published_files",
        not legacy_present,
        "aucun fichier legacy publié" if not legacy_present else f"{len(legacy_present)} fichier(s) legacy publié(s)",
        legacy_present,
    )

    feed_path = app / "feed.json"
    try:
        feed = json.loads(feed_path.read_text(encoding="utf-8"))
        meta = feed.get("meta") if isinstance(feed.get("meta"), dict) else {}
        raw = meta.get("genere_le") if isinstance(meta, dict) else None
        generated_at = parse_dt(raw)
        if generated_at is None:
            add_check(checks, "feed_freshness", False, "feed.json meta.genere_le absent ou invalide", {"genere_le": raw})
        else:
            age_h = max(0.0, (checked_at - generated_at).total_seconds() / 3600)
            add_check(
                checks,
                "feed_freshness",
                age_h <= args.max_feed_age_hours,
                f"feed âge {age_h:.2f}h <= {args.max_feed_age_hours:.2f}h" if age_h <= args.max_feed_age_hours else f"feed périmé: âge {age_h:.2f}h > {args.max_feed_age_hours:.2f}h",
                {"genere_le": raw, "age_hours": round(age_h, 3), "max_age_hours": args.max_feed_age_hours},
            )
    except Exception as exc:
        add_check(checks, "feed_freshness", False, f"feed.json illisible: {type(exc).__name__}: {exc}")

    try:
        container_check(args.container, REQUIRED_IN_CONTAINER, checks)
    except Exception as exc:
        add_check(checks, "container_files_visible", False, f"docker exec impossible: {type(exc).__name__}: {exc}")

    try:
        container_http_check(args.container, public_http_paths(app), checks)
    except Exception as exc:
        add_check(checks, "container_http_served", False, f"vérification HTTP conteneur impossible: {type(exc).__name__}: {exc}")

    failures = [c for c in checks if not c["ok"]]
    result = {
        "ok": not failures,
        "checked_at": checked_at.isoformat(),
        "app": str(app),
        "container": args.container,
        "checks": checks,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
