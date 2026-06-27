#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

FORBIDDEN_RE = re.compile(r"(/opt/data|Traceback|sqlite3\.OperationalError|SECRET_KEY|api_key=|password=|token=|Authorization:|Bearer\s+)", re.I)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): scrub_value(v) for k, v in value.items() if k not in {"app", "db", "db_path", "path", "backup", "backup_path", "candidate", "target"}}
    if isinstance(value, list):
        return [scrub_value(v) for v in value[:200]]
    if isinstance(value, str):
        value = FORBIDDEN_RE.sub("[REDACTED]", value)
        value = re.sub(r"/[^\s'\"]+/(?:projects|artifacts|data|scripts)/[^\s'\"]+", "[path-redacted]", value)
        return value[:500]
    return value



def collect_run_history(base: Path, days: int = 7) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    runs=[]
    if not base.exists():
        return []
    for d in sorted([x for x in base.iterdir() if x.is_dir()], key=lambda x: x.name, reverse=True)[:80]:
        try:
            stamp = d.name
            dt = None
            if re.match(r"\d{8}T\d{6}Z$", stamp):
                dt = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if dt and dt < cutoff:
                continue
            statuses=[]
            for st in sorted(d.glob("*.status")):
                txt=st.read_text(encoding="utf-8", errors="replace").strip()
                m=re.search(r"rc=(\d+).*duration_s=(\d+)", txt)
                statuses.append({"step":st.stem,"rc":int(m.group(1)) if m else None,"duration_s":int(m.group(2)) if m else None})
            summary=read_json(d/"daily_summary"/"summary.json", {})
            failed=[x for x in statuses if x.get("rc") not in (0, None)]
            runs.append({
                "name": d.name,
                "generated_at": dt.isoformat() if dt else None,
                "ok": not failed and bool(statuses),
                "steps": len(statuses),
                "failed_steps": [x["step"] for x in failed],
                "duration_s": sum(int(x.get("duration_s") or 0) for x in statuses),
                "summary_status": summary.get("status") or summary.get("ok"),
                "warnings": (summary.get("warnings") or [])[:10] if isinstance(summary, dict) else [],
            })
        except Exception as exc:
            runs.append({"name": d.name, "ok": False, "error": str(exc)[:160]})
    return runs[:14]

def latest_sprint(artifacts: Path) -> dict[str, Any]:
    """Return a short, sanitized pointer to the latest sprint report artifact."""
    candidates = sorted(artifacts.glob("product_v*_sprint_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {"available": False}
    p = candidates[0]
    text = p.read_text(encoding="utf-8", errors="replace")[:3000]
    title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.strip().startswith("#")), p.name)
    bullets = [line.strip("- ").strip() for line in text.splitlines() if line.strip().startswith("-")][:8]
    return scrub_value({"available": True, "file": p.name, "title": title, "bullets": bullets})


def collect_pipeline_status(statuses: list[dict[str, Any]], run_summary: dict[str, Any]) -> dict[str, Any]:
    gates = {
        "build": ["build_technical_app", "build_clean_portal", "product_hardening_v5", "slim_public_listings"],
        "data": ["realestate_refresh", "seloger_import", "source_detail_enrichment", "source_health_audit"],
        "promotion": ["promote_db_candidate", "rollback_db_drill", "promote_app_candidate", "rollback_app_drill"],
        "qa": ["clean_portal_audit", "public_qa", "public_user_search_audit", "ops_quality_audit"],
    }
    by_step = {s.get("step"): s for s in statuses}
    groups = {}
    for name, wanted in gates.items():
        present = [by_step[x] for x in wanted if x in by_step]
        failed = [x for x in present if x.get("rc") not in (0, None)]
        if not present:
            groups[name] = {"ok": None, "present": 0, "expected": len(wanted), "failed": [], "note": "non mesuré sur ce run"}
        else:
            groups[name] = {"ok": not failed, "present": len(present), "expected": len(wanted), "failed": [x.get("step") for x in failed]}
    return scrub_value({"ok": all(x["ok"] for x in groups.values()) if statuses else None, "groups": groups, "summary_ok": run_summary.get("ok") if isinstance(run_summary, dict) else None})


def collect_alert_dry_run(project: Path, app: Path) -> dict[str, Any]:
    """Run saved-search alerts in dry-run mode and keep only safe counters/links."""
    script = project / "src" / "search_alerts.py"
    cfg = project / "config" / "saved_searches.json"
    if not script.exists() or not cfg.exists() or not (app / "listings.json").exists():
        return {"available": False, "reason": "missing alert script/config/listings"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--config", str(cfg), "--dry-run"],
            cwd=str(project), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
    except Exception as exc:
        return scrub_value({"available": False, "ok": False, "error": str(exc)[:160]})
    if proc.returncode != 0:
        return scrub_value({"available": True, "ok": False, "rc": proc.returncode, "stderr": proc.stderr[:240]})
    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        return scrub_value({"available": True, "ok": False, "error": f"invalid json: {exc}"})
    searches = data.get("searches") or []
    return scrub_value({
        "available": True,
        "ok": bool(data.get("ok") and data.get("dry_run")),
        "search_count": len(searches),
        "would_emit": bool(data.get("would_emit")),
        "new_total": data.get("new_total"),
        "event_total": data.get("event_total"),
        "bootstrap_silent": data.get("bootstrap_silent"),
        "top_searches": [{"id": s.get("id"), "name": s.get("name"), "matches": s.get("matches"), "new": s.get("new"), "search_url": s.get("search_url")} for s in searches[:6]],
    })


def summarize_freshness(source_health: dict[str, Any]) -> list[dict[str, Any]]:
    out=[]
    for src in source_health.get("sources", []) if isinstance(source_health, dict) else []:
        out.append({
            "source": src.get("source"),
            "status": src.get("status") or src.get("severity"),
            "active_rows": src.get("active_rows"),
            "age_hours": src.get("age_hours"),
            "last_seen": src.get("last_seen_at") or src.get("max_seen_at"),
            "note": src.get("note") or src.get("message"),
        })
    return out

def status_label(ok: bool, warnings: list[str]) -> tuple[str, str]:
    if not ok:
        return "Action requise", "bad"
    if warnings:
        return "À vérifier", "warn"
    return "OK", "ok"


def card(title: str, value: str, note: str = "", cls: str = "") -> str:
    return f"<article class='card {cls}'><div class='k'>{html.escape(title)}</div><div class='v'>{html.escape(value)}</div><p>{html.escape(note)}</p></article>"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a sanitized, unlinked ops cockpit for the immo portal")
    ap.add_argument("--app", default="artifacts/app")
    ap.add_argument("--run-dir", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    app = Path(args.app).resolve()
    project = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out).resolve() if args.out else app
    run_dir = Path(args.run_dir).resolve() if args.run_dir else None
    out_dir.mkdir(parents=True, exist_ok=True)

    listings_payload = read_json(app / "listings.json", {"listings": []})
    listings = listings_payload.get("listings", []) if isinstance(listings_payload, dict) else []
    source_health = read_json(app / "source_health.json", {})
    dedup = read_json(app / "dedup_groups.json", {})
    opp = read_json(app / "opportunity.json", {})
    locations = read_json(app / "locations.json", {})
    run_history = collect_run_history(Path("/opt/data/artifacts/immo-public-refresh"), days=7)
    freshness = summarize_freshness(source_health)

    run_summary = {}
    run_files: dict[str, Any] = {}
    if run_dir and run_dir.exists():
        run_summary = read_json(run_dir / "daily_summary" / "summary.json", {})
        for name in [
            "promote_db.json", "rollback_db_drill.json", "promote_app.json", "rollback_app_drill.json",
            "public_delta_guard.json", "dedup_audit.json",
        ]:
            run_files[name] = read_json(run_dir / name, {})

    statuses = []
    if run_dir and run_dir.exists():
        for p in sorted(run_dir.glob("*.status")):
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            m = re.search(r"rc=(\d+).*duration_s=(\d+)", text)
            statuses.append({"step": p.stem, "rc": int(m.group(1)) if m else None, "duration_s": int(m.group(2)) if m else None})

    sprint = latest_sprint(project / "artifacts")
    pipeline = collect_pipeline_status(statuses, run_summary)
    alert_dry_run = collect_alert_dry_run(project, app)
    qa_links = [
        {"label": "Accueil public", "href": "index.html"},
        {"label": "Veille", "href": "veille.html"},
        {"label": "Nouveautés", "href": "changes.html"},
        {"label": "Santé sources", "href": "source_health.html"},
        {"label": "Doublons", "href": "dedup.html"},
        {"label": "Opportunités", "href": "opportunity.html"},
        {"label": "Localisation", "href": "locations.html"},
        {"label": "Alertes cours", "href": "alertes_cours.html"},
    ]

    warnings: list[str] = []
    if run_summary.get("warnings"):
        warnings.extend(map(str, run_summary.get("warnings", [])))
    if not listings:
        warnings.append("listings.json vide ou illisible")
    if len(listings) < 500:
        warnings.append(f"volume public bas: {len(listings)} annonces")
    local_photos = sum(1 for x in listings if x.get("local_image_url"))
    desc = sum(1 for x in listings if x.get("description"))
    top_scores = (opp.get("top") or [])[:10] if isinstance(opp, dict) else []
    strong = sum(1 for x in top_scores if (x.get("score") or 0) >= 75)
    failed_steps = [s for s in statuses if s.get("rc") not in (0, None)]
    if failed_steps:
        warnings.append(f"{len(failed_steps)} étape(s) run non-zero")

    ok = not failed_steps and bool(listings)
    label, cls = status_label(ok, warnings)

    safe = scrub_value({
        "ok": ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": label,
        "public": {
            "listings": len(listings),
            "generated_at": listings_payload.get("generated_at") if isinstance(listings_payload, dict) else None,
            "local_primary": local_photos,
            "descriptions": desc,
            "dedup_groups": dedup.get("groups_count") if isinstance(dedup, dict) else None,
            "opportunity_top_count": len(opp.get("top") or []) if isinstance(opp, dict) else None,
            "location_quality": (locations.get("summary") or {}).get("quality") if isinstance(locations, dict) else None,
        },
        "source_health": {
            "status_counts": (source_health.get("status_counts") or source_health.get("summary") or {}) if isinstance(source_health, dict) else {},
            "freshness": freshness,
        },
        "run": {
            "name": run_dir.name if run_dir else None,
            "summary": run_summary,
            "steps": statuses,
            "files": run_files,
            "history_7d": run_history,
            "history_ok_count": sum(1 for r in run_history if r.get("ok")),
            "history_fail_count": sum(1 for r in run_history if not r.get("ok")),
        },
        "last_sprint": sprint,
        "pipeline": pipeline,
        "alert_dry_run": alert_dry_run,
        "qa_links": qa_links,
        "warnings": warnings,
        "notes": [
            "Cockpit ops séparé, non lié depuis la homepage.",
            "Données volontairement sanitizées: pas de chemins internes, logs bruts, secrets ou stack traces.",
            "Ce cockpit est un statut opérationnel, pas une page utilisateur/famille.",
        ],
    })

    json_text = json.dumps(safe, ensure_ascii=False, indent=2)
    if FORBIDDEN_RE.search(json_text):
        raise SystemExit("forbidden token remained in ops_status.json")
    (out_dir / "ops_status.json").write_text(json_text, encoding="utf-8")

    source_rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
        for k, v in sorted((run_summary.get("source_counts") or {}).items())
    ) or "<tr><td colspan='2'>Non disponible</td></tr>"
    step_rows = "".join(
        f"<tr><td>{html.escape(s['step'])}</td><td>{s.get('rc')}</td><td>{s.get('duration_s') or ''}s</td></tr>"
        for s in statuses[-40:]
    ) or "<tr><td colspan='3'>Aucun run lié</td></tr>"
    warning_items = "".join(f"<li>{html.escape(w)}</li>" for w in warnings) or "<li>Aucun warning bloquant.</li>"

    history_rows = "".join(
        f"<tr><td>{html.escape(str(r.get('name')))}</td><td>{'OK' if r.get('ok') else 'KO'}</td><td>{html.escape(str(r.get('steps') or ''))}</td><td>{html.escape(', '.join(r.get('failed_steps') or []) or '—')}</td><td>{html.escape(str(r.get('duration_s') or ''))}s</td></tr>"
        for r in run_history
    ) or "<tr><td colspan='5'>Aucun historique 7 jours trouvé</td></tr>"
    freshness_rows = "".join(
        f"<tr><td>{html.escape(str(x.get('source') or ''))}</td><td>{html.escape(str(x.get('status') or 'n.c.'))}</td><td>{html.escape(str(x.get('active_rows') or ''))}</td><td>{html.escape(str(x.get('age_hours') or ''))}</td><td>{html.escape(str(x.get('last_seen') or ''))}</td></tr>"
        for x in freshness
    ) or "<tr><td colspan='5'>Fraîcheur source non disponible</td></tr>"
    pipeline_rows = "".join(
        f"<tr><td>{html.escape(str(name))}</td><td>{'OK' if info.get('ok') else ('n.c.' if info.get('ok') is None else 'KO')}</td><td>{html.escape(str(info.get('present')))} / {html.escape(str(info.get('expected')))}</td><td>{html.escape(', '.join(info.get('failed') or []) or '—')}</td></tr>"
        for name, info in (pipeline.get("groups") or {}).items()
    ) or "<tr><td colspan='4'>Pipeline non disponible</td></tr>"
    qa_link_items = "".join(
        f"<li><a href=\"{html.escape(x['href'], quote=True)}\">{html.escape(x['label'])}</a></li>" for x in qa_links
    )
    dry_rows = "".join(
        f"<tr><td>{html.escape(str(x.get('name') or x.get('id') or ''))}</td><td>{html.escape(str(x.get('matches') or 0))}</td><td>{html.escape(str(x.get('new') or 0))}</td><td><a href=\"{html.escape(str(x.get('search_url') or '#'), quote=True)}\">QA recherche</a></td></tr>"
        for x in (alert_dry_run.get("top_searches") or [])
    ) or "<tr><td colspan='4'>Dry-run alertes non disponible</td></tr>"
    sprint_items = "".join(f"<li>{html.escape(str(x))}</li>" for x in (sprint.get("bullets") or [])[:6]) or "<li>Aucun résumé sprint détecté.</li>"

    html_text = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'">
<title>Cockpit ops — Immo RUN</title>
<style>
:root{{--bg:#f7f4ee;--paper:#fffdf8;--ink:#1f211d;--muted:#6a665e;--line:#e5ded2;--ok:#087f5b;--warn:#b25b00;--bad:#b42318;--accent:#0f766e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.5}}main{{max-width:1180px;margin:auto;padding:28px 16px 72px}}.hero,.card,.panel{{background:var(--paper);border:1px solid var(--line);border-radius:24px;box-shadow:0 18px 50px rgba(44,35,20,.08)}}.hero{{padding:28px;margin-bottom:16px}}h1{{font-size:clamp(32px,6vw,64px);line-height:.96;letter-spacing:-.06em;margin:0 0 10px}}h2{{letter-spacing:-.03em}}.muted,.k,p{{color:var(--muted)}}.status{{display:inline-flex;align-items:center;gap:8px;border-radius:999px;padding:8px 12px;font-weight:800;background:#eef7f5;color:var(--ok)}}.status.warn{{background:#fff7ed;color:var(--warn)}}.status.bad{{background:#fef2f2;color:var(--bad)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:16px 0}}.card{{padding:16px}}.v{{font-size:30px;font-weight:850;letter-spacing:-.04em}}.panel{{padding:18px;margin:14px 0;overflow:auto}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}code{{background:#eee7db;border-radius:8px;padding:2px 6px}}a{{color:var(--accent)}}@media(max-width:680px){{main{{padding:16px 12px 56px}}.v{{font-size:24px}}}}
</style></head><body><main>
<section class="hero"><span class="status {cls}">{html.escape(label)}</span><h1>Cockpit ops Immo RUN</h1><p class="muted">Page séparée, non liée depuis l’accueil. Elle résume sprint, sources, alertes dry-run, pipeline et liens QA sans exposer logs bruts, chemins internes ni secrets.</p></section>
<div class="grid">
{card('Annonces publiques', str(len(listings)), 'Volume listings.json')}
{card('Photos locales', f'{local_photos}/{len(listings)}', 'Photos principales locales')}
{card('Descriptions', f'{desc}/{len(listings)}', 'Descriptions source disponibles')}
{card('Doublons doux', str(dedup.get('groups_count') if isinstance(dedup, dict) else 'n.c.'), 'Groupes non destructifs')}
{card('Top opportunités', str(len(opp.get('top') or []) if isinstance(opp, dict) else 'n.c.'), f'{strong} fortes dans le top 10')}
{card('Warnings', str(len(warnings)), 'À vérifier si >0', 'warn' if warnings else '')}
</div>
<section class="panel"><h2>Warnings</h2><ul>{warning_items}</ul></section>
<section class="panel"><h2>Dernier sprint</h2><p><strong>{html.escape(str(sprint.get('title') or 'Non disponible'))}</strong> <span class="muted">{html.escape(str(sprint.get('file') or ''))}</span></p><ul>{sprint_items}</ul></section>
<section class="panel"><h2>Pipeline</h2><table><thead><tr><th>Groupe</th><th>Statut</th><th>Étapes</th><th>Échecs</th></tr></thead><tbody>{pipeline_rows}</tbody></table></section>
<section class="panel"><h2>Alertes dry-run</h2><p class="muted">Dry-run non destructif: {html.escape('OK' if alert_dry_run.get('ok') else 'à vérifier')} · recherches {html.escape(str(alert_dry_run.get('search_count') or 0))} · nouvelles {html.escape(str(alert_dry_run.get('new_total') or 0))} · événements {html.escape(str(alert_dry_run.get('event_total') or 0))} · émission {html.escape('oui' if alert_dry_run.get('would_emit') else 'non')}.</p><table><thead><tr><th>Recherche</th><th>Matchs</th><th>Nouvelles</th><th>Lien QA</th></tr></thead><tbody>{dry_rows}</tbody></table></section>
<section class="panel"><h2>Liens QA</h2><ul class="qa-links">{qa_link_items}</ul></section>
<section class="panel"><h2>Sources actives</h2><table><thead><tr><th>Source</th><th>Annonces</th></tr></thead><tbody>{source_rows}</tbody></table></section>
<section class="panel"><h2>Historique 7 jours</h2><table><thead><tr><th>Run</th><th>Statut</th><th>Étapes</th><th>Échecs</th><th>Durée</th></tr></thead><tbody>{history_rows}</tbody></table></section>
<section class="panel"><h2>Fraîcheur par source</h2><table><thead><tr><th>Source</th><th>Statut</th><th>Actives</th><th>Âge h</th><th>Dernière vue</th></tr></thead><tbody>{freshness_rows}</tbody></table></section>
<section class="panel"><h2>Étapes du dernier run</h2><table><thead><tr><th>Étape</th><th>RC</th><th>Durée</th></tr></thead><tbody>{step_rows}</tbody></table></section>
<section class="panel"><h2>Contrat sécurité</h2><ul><li>Pas de lien depuis la homepage.</li><li><code>noindex,nofollow,noarchive</code>.</li><li>JSON sanitizé dans <code>ops_status.json</code>.</li><li>Pas de logs bruts, stack traces, chemins internes ou secrets.</li></ul></section>
</main></body></html>"""
    if FORBIDDEN_RE.search(html_text):
        raise SystemExit("forbidden token remained in ops.html")
    (out_dir / "ops.html").write_text(html_text, encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_dir / "ops.html"), "status": label, "warnings": warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
