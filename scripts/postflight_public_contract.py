#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.public_feed_dedup import deduplicate_public_feed
from src.photo_gallery import audit_public_galleries

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
PUBLICATION_MIN_SURFACE_M2 = 65.0
PUBLICATION_MAX_RENT_EUR = 1700
EXCLUDED_SAINT_DENIS_QUARTIERS = ("providence", "saint-francois")


def norm(value: object) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFD", str(value)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\bst\b", "saint", text)
    text = re.sub(r"\bste\b", "sainte", text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def saint_denis_excluded_quartier(item: dict[str, object]) -> str | None:
    if norm(item.get("commune")) != norm("Saint-Denis"):
        return None
    hay = norm(" ".join(str(item.get(k) or "") for k in ("quartier", "location_label", "title")))
    for needle in EXCLUDED_SAINT_DENIS_QUARTIERS:
        if needle in hay:
            return needle
    return None


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


def validate_group_summaries(listings: list[dict[str, object]], published: dict[str, object]) -> list[str]:
    allowed = {"group_id", "canonical_display_id", "member_count", "sources", "reason"}
    cards: dict[str, dict[str, object]] = {}
    errors: list[str] = []
    for item in listings:
        group_id = item.get("dedup_group_id")
        if not group_id:
            continue
        if not isinstance(group_id, str) or group_id in cards:
            errors.append(f"duplicate_or_invalid_card_group:{group_id}")
            continue
        cards[group_id] = item
    summaries = published.get("group_summaries")
    if not isinstance(summaries, list):
        return errors + ["group_summaries_not_list"]
    seen: set[str] = set()
    for summary in summaries:
        if not isinstance(summary, dict) or set(summary) != allowed:
            errors.append("summary_keys_or_type")
            continue
        group_id = summary.get("group_id")
        sources = summary.get("sources")
        if not isinstance(group_id, str) or group_id in seen:
            errors.append(f"duplicate_or_invalid_summary_group:{group_id}")
            continue
        seen.add(group_id)
        card = cards.get(group_id)
        if card is None:
            errors.append(f"summary_without_card:{group_id}")
            continue
        links = card.get("also_on") if isinstance(card.get("also_on"), list) else []
        expected = {
            "group_id": group_id,
            "canonical_display_id": card.get("canonical_display_id"),
            "member_count": len(links),
            "sources": sorted({str(link.get("source")) for link in links if isinstance(link, dict) and link.get("source")}),
            "reason": card.get("dedup_reason"),
        }
        if summary != expected or not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
            errors.append(f"summary_mismatch:{group_id}")
    if seen != set(cards):
        errors.append("group_set_mismatch")
    if published.get("groups") != len(cards):
        errors.append("group_count_mismatch")
    return errors

def visible_content_violations(
    listings: list[dict[str, object]],
) -> dict[str, list[str]]:
    violations = {"missing_description": [], "missing_photo": []}
    for item in listings:
        identity = str(item.get("id") or "").strip() or "<missing-id>"
        if not str(item.get("description") or "").strip():
            violations["missing_description"].append(identity)
        quality = item.get("description_quality")
        quality = quality if isinstance(quality, dict) else {}
        raw_markers = quality.get("markers")
        markers = raw_markers if isinstance(raw_markers, (list, tuple, set)) else [raw_markers]
        proven_incomplete = {"expand_prompt", "truncated_ellipsis", "synthetic_fallback", "empty"}
        evidence = {
            str(value or "").strip().lower()
            for value in (*markers, quality.get("status"), item.get("description_status"))
        }
        if evidence & proven_incomplete:
            violations.setdefault("invalid_description", []).append(identity)

        raw_images = item.get("images")
        gallery = raw_images if isinstance(raw_images, list) else []
        has_photo = bool(str(item.get("image") or "").strip()) or any(
            bool(str(value or "").strip()) for value in gallery
        )
        if not has_photo:
            violations["missing_photo"].append(identity)
    return violations



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
        listings = feed.get("listings") if isinstance(feed.get("listings"), list) else []
        active_count = sum(1 for item in listings if isinstance(item, dict) and item.get("active"))
        rent_violations = []
        surface_violations = []
        quartier_violations = []
        scope_violations = []
        active_listings: list[dict[str, object]] = []
        hidden_duplicate_rows = 0
        dedup_trace_violations = []
        for item in listings:
            if not isinstance(item, dict) or not item.get("active"):
                continue
            active_listings.append(item)
            if item.get("display_canonical") is False:
                hidden_duplicate_rows += 1
            in_group = bool(item.get("dedup_group_id"))
            trace_ok = (
                item.get("canonical") is True
                and item.get("display_canonical") is True
                and item.get("canonical_display_id") == item.get("id")
                and item.get("dedup_decision") == "canonical"
                and bool(item.get("also_on")) == in_group
            )
            if not trace_ok:
                dedup_trace_violations.append({"id": item.get("id"), "in_group": in_group})
            rent = as_int(item.get("rent"))
            surface = as_float(item.get("surface"))
            if rent is None or rent > PUBLICATION_MAX_RENT_EUR:
                rent_violations.append({"id": item.get("id"), "rent": item.get("rent")})
            if surface is None or surface < PUBLICATION_MIN_SURFACE_M2:
                surface_violations.append({"id": item.get("id"), "surface": item.get("surface")})
            commune_norm = norm(item.get("commune"))
            if commune_norm not in {norm("Saint-Denis"), norm("Sainte-Marie")}:
                scope_violations.append({"id": item.get("id"), "commune": item.get("commune")})

            excluded_quartier = saint_denis_excluded_quartier(item)
            if excluded_quartier:
                quartier_violations.append({
                    "id": item.get("id"),
                    "quartier": item.get("quartier"),
                    "location_label": item.get("location_label"),
                    "rule": excluded_quartier,
                })
        add_check(
            checks,
            "publication_rent_contract",
            not rent_violations,
            "aucune annonce active avec loyer inconnu ou >1700" if not rent_violations else f"{len(rent_violations)} annonce(s) violent le contrat loyer",
            {"max_rent_eur": PUBLICATION_MAX_RENT_EUR, "violations": rent_violations[:20]},
        )
        add_check(
            checks,
            "publication_surface_contract",
            not surface_violations,
            "aucune annonce active avec surface inconnue ou <65" if not surface_violations else f"{len(surface_violations)} annonce(s) violent le contrat surface",
            {"min_surface_m2": PUBLICATION_MIN_SURFACE_M2, "violations": surface_violations[:20]},
        )
        add_check(
            checks,
            "publication_commune_scope_contract",
            not scope_violations,
            "toutes les annonces actives sont a Saint-Denis ou Sainte-Marie" if not scope_violations else f"{len(scope_violations)} annonce(s) hors perimetre",
            {"allowed": ["Saint-Denis", "Sainte-Marie"], "violations": scope_violations[:20]},
        )

        add_check(
            checks,
            "publication_saint_denis_quartier_contract",
            not quartier_violations,
            "aucune annonce active Saint-Denis Providence/Saint-François" if not quartier_violations else f"{len(quartier_violations)} annonce(s) violent le contrat quartier Saint-Denis",
            {"excluded": EXCLUDED_SAINT_DENIS_QUARTIERS, "violations": quartier_violations[:20]},
        )
        content_violations = visible_content_violations(active_listings)
        content_ok = not any(content_violations.values())
        add_check(
            checks,
            "publication_visible_content_contract",
            content_ok,
            "toutes les annonces visibles ont une description et une photo"
            if content_ok
            else "annonce(s) visible(s) sans description ou photo",
            {
                key: values[:20]
                for key, values in content_violations.items()
            },
        )
        photo_integrity = audit_public_galleries(active_listings, app_root=app)
        add_check(
            checks,
            "publication_photo_gallery_integrity",
            photo_integrity["ok"],
            "galeries sans doublon et aucune photo placeholder réutilisée massivement"
            if photo_integrity["ok"]
            else f"{len(photo_integrity['violations'])} violation(s) d'intégrité photo publique",
            photo_integrity,
        )
        remaining_visible, remaining_dedup = deduplicate_public_feed(
            active_listings, photo_root=app
        )
        duplicate_rows = remaining_dedup.get("hidden_duplicates", 0)
        add_check(
            checks,
            "publication_strong_dedup_contract",
            duplicate_rows == 0 and hidden_duplicate_rows == 0,
            "le feed est un point fixe du moteur de déduplication public" if duplicate_rows == 0 and hidden_duplicate_rows == 0 else f"{duplicate_rows} doublon(s) fort(s) détecté(s) par le moteur public ou {hidden_duplicate_rows} ligne(s) masquée(s) encore servie(s)",
            {"policy": "same conservative engine as export_feed; doubt stays visible", "remaining_dedup": remaining_dedup, "remaining_visible": len(remaining_visible), "hidden_rows_still_served": hidden_duplicate_rows},
        )
        published_dedup = meta.get("deduplication") if isinstance(meta.get("deduplication"), dict) else {}
        summaries = published_dedup.get("group_summaries") if isinstance(published_dedup.get("group_summaries"), list) else []
        summary_errors = validate_group_summaries(active_listings, published_dedup)
        expected_groups = published_dedup.get("groups", 0)
        trace_ok = not dedup_trace_violations and not summary_errors
        add_check(
            checks, "publication_dedup_trace_contract", trace_ok,
            "chaque carte visible est canonique et les groupes sont tracés sans URL" if trace_ok else "traçabilité canonique publique incohérente",
            {"violations": dedup_trace_violations[:20], "groups": expected_groups, "summaries": len(summaries), "summary_errors": summary_errors[:20]},
        )
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
        try:
            coverage = json.loads((app / "coverage.json").read_text(encoding="utf-8"))
            coverage_count = coverage.get("count")
            add_check(
                checks,
                "coverage_matches_feed_active",
                coverage_count == active_count,
                f"coverage.count={coverage_count} et feed actives={active_count}"
                if coverage_count == active_count else
                f"coverage.count={coverage_count} contredit feed actives={active_count}",
                {"coverage_count": coverage_count, "feed_active_count": active_count},
            )
        except Exception as exc:
            add_check(checks, "coverage_matches_feed_active", False, f"coverage.json illisible: {type(exc).__name__}: {exc}")
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
