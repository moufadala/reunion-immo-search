#!/usr/bin/env python3
"""Source health exporter for the Réunion immo dashboard.

Reads the production SQLite database and latest smoke-run SUMMARY.json, then
publishes a small admin contract:
- source_health.json: machine-readable freshness/status per source
- source_health.html: human-readable source health board

It is intentionally non-destructive: no scraper is run and no DB row is mutated.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.environ.get("IMMO_DB_PATH", "/opt/data/data/reunion_watch.db"))
DEFAULT_SMOKE_ROOT = Path("/opt/data/artifacts/scraper-smoke-runs")
DEFAULT_OUT = ROOT / "artifacts" / "app" / "source_health.json"
# Do not write the human/admin HTML page to artifacts/app by default: that
# directory is the public product root. Pipeline callers that want an HTML proof
# must pass --html-out explicitly or export IMMO_RUN_DIR. The JSON sidecar stays
# public because product/health checks consume it; the legacy/admin HTML page
# does not belong in the served app.
_DEFAULT_HTML_DIR = Path(os.environ.get("IMMO_RUN_DIR", str(ROOT / "artifacts" / "source-health")))
DEFAULT_HTML_OUT = Path(os.environ.get("IMMO_SOURCE_HEALTH_HTML_OUT", str(_DEFAULT_HTML_DIR / "source_health.html")))
CRITICAL_SOURCES = {
    "seloger", "zimo", "bienici", "ofim_rss", "ofim", "domimmo", "fnaim",
    "citya", "immo974", "locamoi", "97immo", "alter", "superimmo",
    "leboncoin", "adrezio",
}
# Conservative freshness thresholds for rental listings. Some portals do not
# change every hour; stale here means "needs attention", not "delete rows".
FRESH_HOURS = 36
STALE_HOURS = 96
RECENT_COVERAGE_HOURS = 24
FULL_SOURCE_COVERAGE_THRESHOLD = 0.90
PARTIAL_SOURCE_COVERAGE_THRESHOLD = 0.50
PARTIAL_SOURCES = {"zimo", "domimmo", "immo974"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_reference_time(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = parse_dt(value)
    if dt is None:
        raise ValueError(f"Invalid --reference-time: {value!r}  (expected ISO-8601 with timezone)")
    return dt


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def find_latest_summary(root: Path = DEFAULT_SMOKE_ROOT) -> Path | None:
    if not root.exists():
        return None
    summaries = sorted(root.glob("*/SUMMARY.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return summaries[0] if summaries else None


def load_smoke_summary(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {"path": None, "results": [], "by_source": {}, "tested_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(path), "error": str(exc), "results": [], "by_source": {}, "tested_ids": []}
    by_source: dict[str, Any] = {}
    tested_ids: list[str] = []
    for result in data.get("results", []) or []:
        rid = result.get("id")
        if rid:
            tested_ids.append(str(rid))
        summary = result.get("summary") or {}
        if isinstance(summary.get("by_source"), dict):
            by_source.update(summary["by_source"])
    return {
        "path": str(path),
        "generated_at": data.get("generated_at"),
        "run_dir": data.get("run_dir"),
        "results": data.get("results", []),
        "by_source": by_source,
        "tested_ids": tested_ids,
    }


def db_source_stats(db_path: Path, reference_time: datetime | None = None) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(rental_listings)").fetchall()}
        fetched_expr = "MAX(fetched_at)" if "fetched_at" in cols else "NULL"
        cutoff = (reference_time or now_utc()) - timedelta(hours=RECENT_COVERAGE_HOURS)
        rows = con.execute(
            f"""
            SELECT source_site,
                   COUNT(*) AS total_rows,
                   SUM(CASE WHEN COALESCE(is_active,1)=1 THEN 1 ELSE 0 END) AS active_rows,
                   MAX(seen_last_at) AS last_seen_at,
                   {fetched_expr} AS last_fetched_at,
                   SUM(CASE WHEN COALESCE(is_active,1)=1 AND image_url IS NOT NULL AND TRIM(image_url)!='' THEN 1 ELSE 0 END) AS active_with_image,
                   SUM(CASE WHEN COALESCE(is_active,1)=1 AND julianday(seen_last_at) >= julianday(?) THEN 1 ELSE 0 END) AS active_recent_rows
            FROM rental_listings
            GROUP BY source_site
            ORDER BY source_site
            """,
            (cutoff.isoformat(),),
        ).fetchall()
    finally:
        con.close()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        source = str(r["source_site"] or "unknown")
        out[source] = {
            "source": source,
            "total_rows": int(r["total_rows"] or 0),
            "active_rows": int(r["active_rows"] or 0),
            "active_with_image": int(r["active_with_image"] or 0),
            "active_recent_rows": int(r["active_recent_rows"] or 0),
            "last_seen_at": r["last_seen_at"],
            "last_fetched_at": r["last_fetched_at"],
        }
    return out


def classify_source(source: str, stats: dict[str, Any], smoke: dict[str, Any], now: datetime) -> dict[str, Any]:
    last_dt = parse_dt(stats.get("last_seen_at") or stats.get("last_fetched_at"))
    age_h = None
    if last_dt:
        age_h = round((now - last_dt).total_seconds() / 3600, 2)
    active = int(stats.get("active_rows") or 0)
    recent = int(stats.get("active_recent_rows") or 0)
    coverage_ratio = round(recent / active, 4) if active else None
    coverage_threshold = PARTIAL_SOURCE_COVERAGE_THRESHOLD if source in PARTIAL_SOURCES else FULL_SOURCE_COVERAGE_THRESHOLD
    smoke_by_source = smoke.get("by_source") or {}
    smoke_seen = source in smoke_by_source
    tested_ids = " ".join(smoke.get("tested_ids") or [])
    directly_tested = bool(re.search(re.escape(source), tested_ids, re.I)) or smoke_seen

    if active == 0:
        status = "empty"
        severity = "medium"
        reason = "aucune annonce active en DB"
    elif coverage_ratio is not None and coverage_ratio < coverage_threshold:
        status = "coverage-low"
        severity = "high"
        reason = f"couverture {recent}/{active} ({coverage_ratio:.0%}), seuil {coverage_threshold:.0%}"
    elif age_h is None:
        status = "unknown"
        severity = "medium"
        reason = "date last_seen/fetched absente"
    elif age_h <= FRESH_HOURS:
        status = "fresh"
        severity = "ok"
        reason = f"vu il y a {age_h:.1f}h"
    elif age_h <= STALE_HOURS:
        status = "aging"
        severity = "warning"
        reason = f"vu il y a {age_h:.1f}h"
    else:
        status = "stale"
        severity = "high" if source in CRITICAL_SOURCES else "warning"
        reason = f"vu il y a {age_h:.1f}h"

    if source in {"seloger", "bienici"} and status in {"aging", "stale"}:
        severity = "high"

    if not directly_tested:
        smoke_status = "not-tested"
    elif smoke_seen:
        smoke_status = "covered-by-db-report"
    else:
        smoke_status = "directly-tested"

    return {
        **stats,
        "status": status,
        "severity": severity,
        "reason": reason,
        "age_hours": age_h,
        "smoke_status": smoke_status,
        "smoke_count": smoke_by_source.get(source),
        "active_coverage_ratio": coverage_ratio,
        "coverage_threshold": coverage_threshold,
        "coverage_hours": RECENT_COVERAGE_HOURS,
        "is_critical": source in CRITICAL_SOURCES,
    }


def public_smoke_summary(smoke: dict[str, Any], smoke_path: Path | None) -> dict[str, Any]:
    results = smoke.get("results") if isinstance(smoke, dict) else []
    if not isinstance(results, list):
        results = []
    return {
        "run_id": smoke_path.parent.name if smoke_path else None,
        "generated_at": smoke.get("generated_at") if isinstance(smoke, dict) else None,
        "result_count": len(results),
        "ok_count": sum(1 for r in results if isinstance(r, dict) and r.get("ok")),
    }


def build_payload(db_path: Path = DEFAULT_DB, smoke_summary: Path | None = None, reference_time: datetime | None = None) -> dict[str, Any]:
    now = reference_time if reference_time is not None else now_utc()
    smoke_path = smoke_summary or find_latest_summary()
    smoke = load_smoke_summary(smoke_path)
    stats = db_source_stats(db_path, reference_time=now)
    sources = sorted(set(stats) | CRITICAL_SOURCES)
    items = [classify_source(src, stats.get(src, {"source": src}), smoke, now) for src in sources]
    counts = Counter(i["status"] for i in items)
    severity_counts = Counter(i["severity"] for i in items)
    stale_critical = [i["source"] for i in items if i.get("is_critical") and i["status"] in {"aging", "stale", "unknown", "empty"}]
    coverage_low = [i["source"] for i in items if i["status"] == "coverage-low"]
    return {
        "ok": not coverage_low,
        "generated_at": now.isoformat(),
        "db_path": db_path.name,
        "latest_smoke_summary": public_smoke_summary(smoke, smoke_path),
        "summary": {
            "source_count": len(items),
            "status_counts": dict(counts),
            "severity_counts": dict(severity_counts),
            "stale_or_attention_critical": stale_critical,
            "coverage_below_threshold": coverage_low,
            "coverage_hours": RECENT_COVERAGE_HOURS,
            "fresh_hours": FRESH_HOURS,
            "stale_hours": STALE_HOURS,
        },
        "sources": items,
    }


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def render_html(payload: dict[str, Any], out_path: Path = DEFAULT_HTML_OUT) -> None:
    sources = payload.get("sources", [])
    generated = payload.get("generated_at", "")
    rows = []
    for s in sources:
        badge = s.get("severity", "ok")
        rows.append(f"""
        <article class="source {esc(badge)}" data-status="{esc(s.get('status'))}" data-source="{esc(s.get('source'))}">
          <div><strong>{esc(s.get('source'))}</strong><span>{esc(s.get('status'))} · {esc(s.get('smoke_status'))}</span></div>
          <div class="nums"><b>{esc(s.get('active_rows',0))}</b><span>actives</span><b>{esc(s.get('active_with_image',0))}</b><span>photos</span></div>
          <p>{esc(s.get('reason'))}</p>
          <small>Dernier vu: {esc(s.get('last_seen_at') or 'n.c.')} · smoke count: {esc(s.get('smoke_count') if s.get('smoke_count') is not None else 'n.c.')}</small>
        </article>""")
    counts = payload.get("summary", {}).get("status_counts", {})
    html_doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Santé sources immo Réunion</title>
<style>
:root{{--bg:#faf8f4;--ink:#211f1d;--muted:#716b64;--card:#fff;--line:#e8e1d8;--ok:#067647;--warn:#b54708;--bad:#b42318;--accent:#ff385c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink)}}main{{max-width:1120px;margin:auto;padding:18px 18px 60px}}header{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:14px}}h1{{font-size:clamp(28px,5vw,46px);line-height:.98;margin:0;letter-spacing:-1.2px}}.sub{{color:var(--muted);max-width:680px;line-height:1.45}}.actions{{display:flex;gap:8px;flex-wrap:wrap}}a,button{{border:1px solid var(--line);background:#fff;border-radius:999px;padding:10px 13px;color:inherit;text-decoration:none;font-weight:750;cursor:pointer}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:12px 0}}.kpi{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:11px}}.kpi b{{display:block;font-size:22px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.source{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:13px;box-shadow:0 2px 8px rgba(0,0,0,.035)}}.source.high{{border-color:rgba(180,35,24,.35);background:#fff7f5}}.source.warning{{border-color:rgba(181,71,8,.30);background:#fffaf3}}.source.ok{{border-color:rgba(6,118,71,.20)}}.source>div:first-child{{display:flex;justify-content:space-between;gap:10px;align-items:start}}.source strong{{font-size:18px}}.source span,.source p,.source small{{color:var(--muted)}}.source p{{margin:.55rem 0}}.nums{{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:6px;align-items:baseline;margin-top:10px}}.nums b{{font-size:22px}}.toolbar{{position:sticky;top:0;background:rgba(250,248,244,.94);backdrop-filter:blur(12px);padding:8px 0;z-index:2;display:flex;gap:7px;flex-wrap:wrap}}.toolbar input{{flex:1;min-width:220px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:10px 13px;font:inherit}}@media(max-width:760px){{main{{padding:10px 10px 50px}}header{{display:block;margin-bottom:8px}}.actions{{overflow:auto;flex-wrap:nowrap;padding-bottom:2px}}.actions a{{white-space:nowrap;min-height:44px}}h1{{font-size:28px;letter-spacing:-.7px}}.sub{{font-size:13px}}.cards{{display:flex;gap:7px;overflow:auto;margin:8px 0}}.kpi{{min-width:118px;padding:8px 10px}}.kpi b{{font-size:18px}}.kpi span{{font-size:11px}}.grid{{grid-template-columns:1fr;gap:8px}}a,button{{min-height:44px}}.toolbar{{top:0;border:1px solid var(--line);border-radius:15px;padding:8px;margin-bottom:9px;overflow:auto;flex-wrap:nowrap}}.toolbar input{{min-width:180px;min-height:44px}}.toolbar button{{white-space:nowrap;padding:8px 11px}}.source{{border-radius:14px;padding:11px}}.source strong{{font-size:16px}}.source p,.source small{{font-size:12px}}}}
</style></head><body><main><header><div><h1>Santé sources immo</h1><p class="sub">Vue non destructive de la fraîcheur par source. Une source stale ne supprime rien: elle indique seulement qu'il faut investiguer le scraper avant de polir le produit.</p></div><div class="actions"><a href="/">Dashboard</a><a href="/source_health.json">JSON</a><a href="/changes.html">Changements</a></div></header><section class="cards"><div class="kpi"><b>{esc(payload.get('summary',{}).get('source_count'))}</b><span>sources</span></div><div class="kpi"><b>{esc(counts.get('fresh',0))}</b><span>fresh</span></div><div class="kpi"><b>{esc(counts.get('aging',0)+counts.get('stale',0))}</b><span>à surveiller</span></div><div class="kpi"><b>{esc(len(payload.get('summary',{}).get('stale_or_attention_critical',[])))}</b><span>critiques attention</span></div></section><div class="toolbar"><input id="sourceQ" placeholder="Filtrer source…"><button data-filter="all">Tout</button><button data-filter="fresh">Fresh</button><button data-filter="aging">Aging</button><button data-filter="stale">Stale</button><button data-filter="not-tested">Non testées</button></div><section class="grid" id="grid">{''.join(rows)}</section><p class="sub">Généré: {esc(generated)} · smoke run: {esc(payload.get('latest_smoke_summary',{}).get('run_id') or 'n.c.')}</p></main><script>let currentFilter='all';const q=document.getElementById('sourceQ');function norm(s){{return String(s||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase()}}function apply(){{const query=norm(q?.value);document.querySelectorAll('.source').forEach(c=>{{const st=c.dataset.status, smoke=c.textContent.includes('not-tested'), okFilter=currentFilter==='all'||st===currentFilter||(currentFilter==='not-tested'&&smoke), okQ=!query||norm(c.textContent).includes(query);c.style.display=(okFilter&&okQ)?'block':'none'}})}}document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{{currentFilter=b.dataset.filter;apply()}});q?.addEventListener('input',apply);apply();</script></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--smoke-summary", type=Path)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--html-out", type=Path, default=DEFAULT_HTML_OUT)
    ap.add_argument(
        "--reference-time",
        metavar="ISO8601",
        help="Override the 'now' reference for freshness calculations (ISO-8601 with timezone). "
             "Intended for tests and reproducible audits only.",
    )
    args = ap.parse_args()
    ref_time = parse_reference_time(args.reference_time)
    payload = build_payload(args.db, args.smoke_summary, reference_time=ref_time)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(payload, args.html_out)
    print(json.dumps({"ok": True, "out": str(args.out), "html": str(args.html_out), "summary": payload["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
