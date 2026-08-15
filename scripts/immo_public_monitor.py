#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publication_policy import evaluate_publication

# Correctif 2026-07-27 :
#   1. Une basic auth Traefik protege desormais le site (feed.json contient des
#      donnees personnelles). Verifier le CONTENU en frappant directement le
#      conteneur (docker exec, contourne Traefik -> pas d'auth necessaire, et
#      surtout aucun mot de passe a stocker sur disque). On verifie EN PLUS,
#      cote externe, que la porte d'auth est toujours active (401 attendu sans
#      identifiants) -- ca detecte une regression si l'auth saute un jour.
#   2. Seuil MIN_SELOGER recalibre : la purge Nord-Est du 27/07 (2642->695
#      lignes) a fait tomber seloger a 91 actives ; l'ancien seuil (150)
#      datait du scraping toute-l'ile et aurait echoue a CHAQUE run.
#   3. Le fichier verifie est desormais feed.json (contrat unique), plus
#      listings.json (ancien pipeline, toujours ecrit en parallele mais plus
#      la source de verite produit).

PUBLIC_BASE = "https://immo.148.230.103.174.sslip.io/"
LOCAL_APP_DIR = Path(os.environ.get("IMMO_PUBLIC_MONITOR_APP_DIR", "/opt/data/projects/reunion-immo-search/artifacts/app"))
MIN_LISTINGS = 100
MIN_SELOGER = 60          # 91 actives au 27/07 ; marge sous le niveau observe, pas l'ancien seuil ile entiere
MIN_LOCAL_PHOTO_RATIO = 0.85
MAX_FEED_AGE_HOURS = 36  # meme seuil que la banniere client "feed perime"
TIMEOUT = 25


def parse_feed_datetime(value: object) -> datetime | None:
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


def check_feed_freshness(data: dict[str, object], evidence: dict[str, object], errors: list[str]) -> None:
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    generated_raw = meta.get("genere_le") if isinstance(meta, dict) else None
    evidence["feed_genere_le"] = generated_raw
    generated_at = parse_feed_datetime(generated_raw)
    if generated_at is None:
        errors.append("feed freshness unknown: meta.genere_le missing or invalid")
        return
    now = datetime.now(timezone.utc)
    age_hours = max(0.0, (now - generated_at).total_seconds() / 3600)
    evidence["feed_age_hours"] = round(age_hours, 2)
    evidence["feed_max_age_hours"] = MAX_FEED_AGE_HOURS
    if age_hours > MAX_FEED_AGE_HOURS:
        errors.append(
            f"feed périmé: meta.genere_le={generated_at.isoformat()} "
            f"âge={age_hours:.1f}h > {MAX_FEED_AGE_HOURS}h"
        )


def fetch_interne(path: str) -> tuple[int, bytes]:
    """Vérifie le contenu publié depuis le répertoire canonique local.

    Le watchdog tourne parfois hors du contexte Docker qui voit le conteneur
    immo-dashboard. Le contrôle public réel est déjà assuré par la porte
    BasicAuth externe ci-dessous; pour le contenu on lit donc les fichiers
    statiques promus sous artifacts/app, sans stocker de secret BasicAuth.
    """
    rel = "index.html" if path in ("", "/", "v2/") else path.lstrip("/")
    p = (LOCAL_APP_DIR / rel).resolve()
    if not str(p).startswith(str(LOCAL_APP_DIR.resolve())) or not p.is_file():
        return 404, b""
    return 200, p.read_bytes()


def fetch_externe_sans_auth(path: str) -> int:
    """Verifie que la porte d'auth est toujours en place (401 attendu)."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(PUBLIC_BASE + path,
                                 headers={"User-Agent": "immo-public-monitor/1.1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def feed_contract_errors(data: dict[str, object]) -> list[str]:
    errors: list[str] = []
    rows = data.get("listings") if isinstance(data.get("listings"), list) else []
    policy_bad = [
        str(row.get("id") or "?")
        for row in rows
        if isinstance(row, dict) and row.get("active", True) and not evaluate_publication(row).eligible
    ]
    if policy_bad:
        errors.append(f"publication policy violated by {len(policy_bad)} listing(s): {policy_bad[:10]}")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    market = meta.get("marche") if isinstance(meta.get("marche"), dict) else {}
    movements = data.get("movements") if isinstance(data.get("movements"), dict) else {}
    retired = market.get("retirees_7j")
    disappeared = movements.get("disparues_7j")
    if retired is not None and disappeared is not None and int(retired) != int(disappeared):
        errors.append(
            f"movement counters disagree: meta.marche.retirees_7j={retired} movements.disparues_7j={disappeared}"
        )
    return errors

def main() -> int:
    errors: list[str] = []
    evidence: dict[str, object] = {"checked_at": datetime.now(timezone.utc).isoformat()}

    # --- 1. la porte d'acces prive est toujours active ---
    try:
        code = fetch_externe_sans_auth("feed.json")
        evidence["auth_gate_status"] = code
        if code != 401:
            errors.append(f"REGRESSION VIE PRIVEE: feed.json repond {code} sans identifiants (401 attendu)")
    except Exception as exc:
        errors.append(f"auth gate check failed: {type(exc).__name__}: {exc}")

    # --- 2. contenu, verifie en interne (pas besoin d'identifiants) ---
    try:
        status, body = fetch_interne("")
        evidence["index_status"] = status
        if status != 200:
            errors.append(f"index HTTP {status}")
        # Index shell marker for the current SPA v2 homepage. Do not check
        # legacy rendered text ("Recherche immo RUN" / "portail propre"):
        # it is now produced client-side and absent from the raw HTML fetched
        # by this watchdog. Keep this hash-agnostic so Vite rebuilds do not
        # break the monitor on every asset rename.
        if not (
            (b'id="root"' in body and (b'/v2/assets/' in body or b'/assets/' in body))
            or b'Recherche immo RUN' in body
            or b'portail propre' in body
        ):
            errors.append("index shell marker missing")
    except Exception as exc:
        errors.append(f"index fetch failed: {type(exc).__name__}: {exc}")

    try:
        status, body = fetch_interne("feed.json")
        evidence["feed_status"] = status
        data = json.loads(body.decode("utf-8"))
        check_feed_freshness(data, evidence, errors)
        errors.extend(feed_contract_errors(data))
        rows = data.get("listings") or []
        total = len(rows)
        seloger = sum(1 for x in rows if str(x.get("source", "")).lower() == "seloger")
        avec_photo = sum(1 for x in rows if x.get("image"))
        evidence.update({"total": total, "seloger": seloger, "avec_photo": avec_photo})
        if total < MIN_LISTINGS:
            errors.append(f"listing volume below threshold: {total} < {MIN_LISTINGS}")
        if seloger < MIN_SELOGER:
            errors.append(f"SeLoger volume below threshold: {seloger} < {MIN_SELOGER}")
        if total and avec_photo < int(total * MIN_LOCAL_PHOTO_RATIO):
            errors.append(f"local photo coverage low: {avec_photo}/{total}")
        distants = [x for x in rows if x.get("image") and not str(x["image"]).startswith("/thumbs/")]
        if distants:
            errors.append(f"{len(distants)} annonces avec une image DISTANTE (regle 'photos chez nous' violee)")
    except Exception as exc:
        errors.append(f"feed fetch/parse failed: {type(exc).__name__}: {exc}")

    try:
        status, _ = fetch_interne("v2/")
        evidence["v2_status"] = status
        if status != 200:
            errors.append(f"v2/ HTTP {status}")
    except Exception as exc:
        errors.append(f"v2/ failed: {type(exc).__name__}: {exc}")

    if errors:
        print("🚨 Immo public monitor: anomalie détectée")
        print(json.dumps({"ok": False, "errors": errors, "evidence": evidence}, ensure_ascii=False, indent=2))
        return 2
    # Silent success for cron/no_agent watchdogs.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
