#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PUBLIC_BASE_URL = "https://immo.148.230.103.174.sslip.io/"
DEFAULT_EDITION_ROOT = Path("/opt/data/artifacts/immo-editions")
DEFAULT_PUBLIC_APP = Path("/opt/data/projects/reunion-immo-search/artifacts/app")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def read_json(path: Path, default: Any = None, *, allow_concatenated: bool = False) -> Any:
    if not path.exists():
        return default
    text = read_text(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if not allow_concatenated:
            return default
    if not allow_concatenated:
        return default
    decoder = json.JSONDecoder()
    idx = 0
    objects: list[Any] = []
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        objects.append(obj)
        idx = end
    return objects[-1] if objects else default


def first_json_object(path: Path) -> dict[str, Any] | None:
    text = read_text(path).strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_date_from_path(run_dir: Path) -> str:
    match = re.search(r"(\d{8})T\d{6}Z", run_dir.name)
    if match:
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return datetime.now(timezone.utc).date().isoformat()


def status_rc(path: Path) -> int | None:
    text = read_text(path)
    match = re.search(r"\brc=(\d+)\b", text)
    return int(match.group(1)) if match else None


def normalize_public_base(url: str) -> str:
    return url.rstrip("/") + "/"


def public_url(public_base_url: str, rel: str) -> str:
    return normalize_public_base(public_base_url) + rel.lstrip("/")


def list_failed_sources(*payloads: dict[str, Any] | None) -> tuple[list[str], list[dict[str, Any]]]:
    sources: dict[str, str] = {}
    details: list[dict[str, Any]] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for src in payload.get("failed_sources") or []:
            sources[str(src)] = str(src)
        for detail in payload.get("failed_source_details") or []:
            if not isinstance(detail, dict):
                continue
            src = str(detail.get("source") or "").strip()
            if src:
                sources[src] = src
            details.append(detail)
        gate = payload.get("source_gate")
        if isinstance(gate, dict):
            for src in gate.get("failed_sources") or []:
                sources[str(src)] = str(src)
            for detail in gate.get("failed_source_details") or []:
                if isinstance(detail, dict):
                    src = str(detail.get("source") or "").strip()
                    if src:
                        sources[src] = src
                    details.append(detail)
    return sorted(sources), details


def count_stage_active(report: dict[str, Any] | None) -> int | None:
    if not isinstance(report, dict):
        return None
    db_summary = report.get("db_summary")
    if not isinstance(db_summary, dict):
        return None
    for key in ("active", "actives", "active_count", "total_active"):
        value = db_summary.get(key)
        if isinstance(value, int):
            return value
    quality = db_summary.get("quality")
    if isinstance(quality, dict):
        for key in ("active", "actives", "active_count", "total_active"):
            value = quality.get(key)
            if isinstance(value, int):
                return value
    return None


def render_html(manifest: dict[str, Any]) -> str:
    status = str(manifest["status"])
    label = {
        "fresh": "PUBLIE FRAIS",
        "degraded": "PUBLIE DEGRADE",
        "stale_only": "PUBLIE STALE",
        "hard_blocked": "NON PUBLIE HARD BLOCK",
    }.get(status, status.upper())
    failed = manifest.get("failed_sources") or []
    blockers = manifest.get("hard_blockers") or []
    counts = manifest.get("counts") or {}
    dashboard_url = manifest.get("public_dashboard_url")
    rows = []
    for src, state in sorted((manifest.get("source_states") or {}).items()):
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(src))}</td>"
            f"<td>{html.escape(str(state.get('state', 'unknown')))}</td>"
            f"<td>{html.escape(str(state.get('count', 'n/a')))}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Immo quotidien - {html.escape(label)}</title>
<style>
body{{margin:0;background:#f7f3ec;color:#1d1b16;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.45}}
main{{max-width:920px;margin:0 auto;padding:32px 18px 56px}}
.badge{{display:inline-block;padding:6px 10px;border:1px solid #d5c7b3;background:#fffdf8;font-weight:800;font-size:13px}}
h1{{font-size:clamp(32px,7vw,62px);line-height:1;margin:18px 0 12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:20px 0}}
.metric,.panel{{background:#fffdf8;border:1px solid #e0d4c2;padding:14px}}
.metric b{{display:block;font-size:26px}}
table{{width:100%;border-collapse:collapse;background:#fffdf8;border:1px solid #e0d4c2}}
td,th{{text-align:left;border-bottom:1px solid #e0d4c2;padding:8px}}
a{{color:#1d4ed8;font-weight:700}}
</style>
</head>
<body>
<main>
<span class="badge">{html.escape(label)}</span>
<h1>Edition immo du {html.escape(str(manifest["date"]))}</h1>
<p>Statut quotidien genere le {html.escape(str(manifest["generated_at"]))}. Le flux public principal peut rester ancien ; cette page publie l'etat maximal fiable du run.</p>
<div class="grid">
  <div class="metric"><span>Selection</span><b>{html.escape(str(counts.get("selected_count", 0)))}</b></div>
  <div class="metric"><span>Sources</span><b>{html.escape(str(counts.get("source_count", 0)))}</b></div>
  <div class="metric"><span>Stage actives</span><b>{html.escape(str(counts.get("stage_active", "n/a")))}</b></div>
  <div class="metric"><span>Refresh public</span><b>{'oui' if manifest.get("public_feed_refreshed") else 'non'}</b></div>
</div>
<section class="panel">
<h2>Sources KO</h2>
<p>{html.escape(", ".join(failed) if failed else "aucune source KO connue")}</p>
<h2>Blocages</h2>
<p>{html.escape("; ".join(blockers) if blockers else "aucun hard block connu")}</p>
{f'<p><a href="{html.escape(str(dashboard_url))}">Ouvrir le dashboard du run</a></p>' if dashboard_url else ''}
</section>
<h2>Etats sources</h2>
<table><thead><tr><th>Source</th><th>Etat</th><th>Count</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</main>
</body>
</html>
"""


def telegram_summary(manifest: dict[str, Any]) -> str:
    status = str(manifest["status"])
    title = {
        "fresh": "IMMO SOIR - PUBLIE FRAIS",
        "degraded": "IMMO SOIR - PUBLIE DEGRADE",
        "stale_only": "IMMO SOIR - PUBLIE STALE",
        "hard_blocked": "IMMO SOIR - NON PUBLIE HARD BLOCK",
    }.get(status, f"IMMO SOIR - {status.upper()}")
    counts = manifest.get("counts") or {}
    failed = ", ".join(manifest.get("failed_sources") or []) or "aucune"
    blockers = "; ".join(manifest.get("hard_blockers") or []) or "aucun"
    lines = [
        title,
        f"Date: {manifest['date']}",
        f"Statut public: {manifest.get('public_status_url', 'n/a')}",
        f"Flux public rafraichi: {'oui' if manifest.get('public_feed_refreshed') else 'non'}",
        f"Selection run: {counts.get('selected_count', 0)} annonces",
        f"Sources: {counts.get('source_count', 0)} total, KO: {failed}",
        f"Stage actives: {counts.get('stage_active', 'n/a')}",
        f"Blocages: {blockers}",
        f"Run: {manifest.get('run_dir', 'n/a')}",
    ]
    if manifest.get("public_dashboard_url"):
        lines.append(f"Dashboard run: {manifest['public_dashboard_url']}")
    return "\n".join(lines) + "\n"


def build_daily_edition(
    *,
    run_dir: Path,
    public_app: Path = DEFAULT_PUBLIC_APP,
    edition_root: Path = DEFAULT_EDITION_ROOT,
    immo_refresh_exit_code: int | None = None,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    public_app = public_app.resolve()
    edition_root = edition_root.resolve()
    date = run_date_from_path(run_dir)
    generated_at = utcnow()

    manifest = read_json(run_dir / "manifest.json", {}, allow_concatenated=False) or {}
    immo_summary = manifest.get("immo_summary") if isinstance(manifest, dict) else {}
    if not isinstance(immo_summary, dict):
        immo_summary = {}

    immo_refresh_json = read_json(run_dir / "immo_public_refresh_result.json", {}, allow_concatenated=True) or {}
    if not isinstance(immo_refresh_json, dict):
        immo_refresh_json = {}

    immo_dir = run_dir / "immo_public_refresh"
    realestate_report = read_json(immo_dir / "realestate_watch" / "realestate_watch_report.json", {}, allow_concatenated=False)
    if not isinstance(realestate_report, dict):
        realestate_report = {}
    disk_payload = first_json_object(immo_dir / "preflight_disk_guard.stdout")
    realestate_rc = status_rc(immo_dir / "realestate_refresh.status")
    if immo_refresh_exit_code is None:
        immo_refresh_exit_code = realestate_rc

    failed_sources, failed_details = list_failed_sources(immo_summary, realestate_report)
    sources = immo_summary.get("sources") if isinstance(immo_summary.get("sources"), dict) else {}
    if not sources and isinstance(realestate_report.get("db_summary"), dict):
        by_source = realestate_report["db_summary"].get("by_source")
        if isinstance(by_source, dict):
            sources = by_source

    selected_count = int(immo_summary.get("selected_count") or realestate_report.get("selected_count") or 0)
    source_count = len(sources)
    stage_db = immo_dir / "reunion_watch.stage.db"
    stage_active = count_stage_active(realestate_report)
    dashboard = run_dir / "dashboard.html"

    hard_blockers: list[str] = []
    if isinstance(disk_payload, dict) and disk_payload.get("ok") is False:
        hard_blockers.append(
            f"disk guard: free_gb={disk_payload.get('free_gb')} < min_free_gb={disk_payload.get('min_free_gb')}"
        )
    if immo_refresh_exit_code not in (None, 0):
        hard_blockers.append(f"immo_public_refresh rc={immo_refresh_exit_code}")
    if realestate_rc not in (None, 0):
        hard_blockers.append(f"realestate_refresh rc={realestate_rc}")

    public_feed_refreshed = immo_refresh_exit_code == 0 and realestate_rc in (None, 0)
    if public_feed_refreshed and not failed_sources:
        status = "fresh"
    elif selected_count > 0 or dashboard.exists() or stage_db.exists():
        status = "degraded"
    elif (public_app / "listings.json").exists() or (public_app / "feed.json").exists():
        status = "stale_only"
    else:
        status = "hard_blocked"

    source_states = {
        str(source): {
            "state": "failed" if str(source) in failed_sources else ("fresh" if public_feed_refreshed else "stale_or_partial"),
            "count": count,
        }
        for source, count in sorted(sources.items())
    }
    for source in failed_sources:
        source_states.setdefault(source, {"state": "failed", "count": 0})

    public_status_rel = "daily-status/index.html"
    public_manifest_rel = "daily-status/edition_manifest.json"
    public_dashboard_rel = "daily-status/dashboard.html"
    versioned_rel = f"editions/{date}/index.html"

    result: dict[str, Any] = {
        "ok": status != "hard_blocked",
        "date": date,
        "status": status,
        "generated_at": generated_at,
        "run_dir": str(run_dir),
        "public_feed_refreshed": public_feed_refreshed,
        "public_status_url": public_url(public_base_url, public_status_rel),
        "public_manifest_url": public_url(public_base_url, public_manifest_rel),
        "public_dashboard_url": public_url(public_base_url, public_dashboard_rel) if dashboard.exists() else None,
        "versioned_status_url": public_url(public_base_url, versioned_rel),
        "failed_sources": failed_sources,
        "failed_source_details": failed_details,
        "hard_blockers": hard_blockers,
        "counts": {
            "selected_count": selected_count,
            "source_count": source_count,
            "stage_active": stage_active,
            "stage_db_exists": stage_db.exists(),
            "stage_db_size_bytes": stage_db.stat().st_size if stage_db.exists() else 0,
        },
        "source_states": source_states,
        "published": {"status_page": False, "manifest": False, "dashboard": False},
    }

    html_text = render_html(result)
    private_dir = edition_root / date
    public_daily_dir = public_app / "daily-status"
    public_versioned_dir = public_app / "editions" / date

    for target_dir in (private_dir, public_daily_dir, public_versioned_dir):
        write_json(target_dir / "edition_manifest.json", result)
        write_text_file(target_dir / "index.html", html_text)
        if dashboard.exists():
            shutil.copy2(dashboard, target_dir / "dashboard.html")

    result["published"] = {
        "status_page": (public_daily_dir / "index.html").exists(),
        "manifest": (public_daily_dir / "edition_manifest.json").exists(),
        "dashboard": (public_daily_dir / "dashboard.html").exists(),
    }
    for target_dir in (private_dir, public_daily_dir, public_versioned_dir):
        write_json(target_dir / "edition_manifest.json", result)

    summary = telegram_summary(result)
    write_text_file(run_dir / "telegram_summary.txt", summary)
    write_text_file(private_dir / "telegram_summary.txt", summary)

    pipeline_path = run_dir / "pipeline_result.json"
    pipeline = read_json(pipeline_path, {}, allow_concatenated=True) or {}
    if isinstance(pipeline, dict):
        pipeline["immo_public_refresh_exit_code"] = immo_refresh_exit_code
        pipeline["daily_edition"] = {
            "status": result["status"],
            "ok": result["ok"],
            "public_status_url": result["public_status_url"],
            "public_feed_refreshed": result["public_feed_refreshed"],
        }
        if immo_refresh_exit_code not in (None, 0):
            pipeline["ok"] = False
        write_json(pipeline_path, pipeline)

    return result


def check_daily_publication(
    *,
    public_app: Path = DEFAULT_PUBLIC_APP,
    expected_date: str | None = None,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
) -> tuple[bool, str]:
    expected_date = expected_date or datetime.now(timezone.utc).date().isoformat()
    manifest_path = public_app / "daily-status" / "edition_manifest.json"
    payload = read_json(manifest_path, {}, allow_concatenated=False)
    if not isinstance(payload, dict) or not payload:
        return (
            False,
            "ALERTE IMMO: publication quotidienne absente "
            f"pour {expected_date}. Aucun manifest {manifest_path}. "
            f"URL attendue: {public_url(public_base_url, 'daily-status/index.html')}\n",
        )
    if str(payload.get("date")) != expected_date:
        return (
            False,
            "ALERTE IMMO: publication quotidienne absente "
            f"pour {expected_date}. Derniere edition={payload.get('date')} "
            f"status={payload.get('status')}. URL: {payload.get('public_status_url') or public_url(public_base_url, 'daily-status/index.html')}\n",
        )
    published = payload.get("published") if isinstance(payload.get("published"), dict) else {}
    if not published.get("status_page"):
        return (
            False,
            "ALERTE IMMO: edition du jour presente mais page publique non prouvee "
            f"pour {expected_date}. URL: {payload.get('public_status_url') or public_url(public_base_url, 'daily-status/index.html')}\n",
        )
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or check the daily immo edition status page.")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--public-app", type=Path, default=Path(os.environ.get("IMMO_PUBLIC_APP", str(DEFAULT_PUBLIC_APP))))
    parser.add_argument("--edition-root", type=Path, default=Path(os.environ.get("IMMO_EDITION_ROOT", str(DEFAULT_EDITION_ROOT))))
    parser.add_argument("--immo-rc", type=int, default=None)
    parser.add_argument("--public-base-url", default=os.environ.get("IMMO_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL))
    parser.add_argument("--check-canary", action="store_true")
    parser.add_argument("--expected-date")
    args = parser.parse_args()

    if args.check_canary:
        ok, message = check_daily_publication(
            public_app=args.public_app,
            expected_date=args.expected_date,
            public_base_url=args.public_base_url,
        )
        if message:
            print(message, end="")
        return 0 if ok else 2

    if args.run_dir is None:
        parser.error("--run-dir is required unless --check-canary is used")
    result = build_daily_edition(
        run_dir=args.run_dir,
        public_app=args.public_app,
        edition_root=args.edition_root,
        immo_refresh_exit_code=args.immo_rc,
        public_base_url=args.public_base_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] != "hard_blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())

