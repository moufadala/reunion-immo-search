#!/usr/bin/env python3
"""Unified RUN flights + Réunion rentals operational watch.

This is the bridge script while HAR/NetLog captures are pending:
- runs existing flight/immo watchers in bounded modes;
- writes one durable manifest;
- creates a Telegram-ready summary;
- renders a static HTML decision dashboard;
- maintains /opt/data/artifacts/reunion-watch/latest.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/opt/data')
ARTIFACT_ROOT = ROOT / 'artifacts/reunion-watch'
PY = sys.executable


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def run_cmd(cmd: list[str], *, cwd: Path = ROOT, timeout: int = 600) -> dict[str, Any]:
    t0 = time.time()
    p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
    return {
        'cmd': cmd,
        'exit_code': p.returncode,
        'duration_s': round(time.time() - t0, 2),
        'stdout': p.stdout,
        'stderr': p.stderr,
    }


def symlink_latest(target: Path) -> None:
    latest = ARTIFACT_ROOT / 'latest'
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(target, target_is_directory=True)
    except Exception:
        pass


def latest_child(root: Path) -> Path | None:
    if (root / 'latest').exists():
        try:
            return (root / 'latest').resolve()
        except Exception:
            pass
    if not root.exists():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)[0] if dirs else None


def load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return str(path)


def normalize_money(v: Any) -> str:
    if isinstance(v, dict):
        if 'total' in v:
            return f"{v.get('total')} {v.get('currency') or 'EUR'}"
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return 'n/a'
    return f"{v} EUR" if isinstance(v, (int, float)) else str(v)


def summarize_flights(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {'ok': False, 'items': [], 'by_status': {}, 'best_official': None}
    items=[]
    best_official=None
    for r in summary.get('results') or []:
        cls = r.get('classification') or {}
        ch = cls.get('cheapest')
        item = {
            'name': r.get('name'),
            'family': r.get('family'),
            'status': cls.get('status'),
            'duration_s': r.get('duration_s'),
            'offer_count': cls.get('offer_count'),
            'cheapest': ch,
            'cheapest_label': normalize_money(ch),
            'raw': str(Path(summary.get('run_dir', '')) / (re.sub(r'[^A-Za-z0-9_.-]+', '_', r.get('name','')).strip('_') + '.raw.json')),
        }
        if isinstance(ch, dict) and 'total' in ch:
            item['roundtrip_total'] = ch.get('total')
        elif isinstance(ch, (int, float)):
            item['oneway_or_min'] = ch
        items.append(item)
        if item['family'] in {'frenchbee', 'airaustral'} and item['status'] == 'prod-candidate' and best_official is None:
            best_official = item
    return {'ok': True, 'items': items, 'by_status': summary.get('by_status') or {}, 'best_official': best_official}


def summarize_immo(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {'ok': False, 'selected': [], 'sources': {}, 'quality': {}, 'source_gate': {}, 'failed_sources': [], 'failed_source_details': []}
    dbs = report.get('db_summary') or {}
    source_gate = report.get('source_gate') or {'ok': True, 'failed_sources': [], 'failed_source_details': []}
    return {
        'ok': bool(source_gate.get('ok', True)),
        'selected_count': report.get('selected_count', 0),
        'selected': (report.get('selected') or [])[:15],
        'sources': dbs.get('by_source') or {},
        'quality': dbs.get('quality') or {},
        'filters': report.get('filters') or {},
        'source_gate': source_gate,
        'failed_sources': report.get('failed_sources') or source_gate.get('failed_sources') or [],
        'failed_source_details': report.get('failed_source_details') or source_gate.get('failed_source_details') or [],
    }


def immo_failed_sources_alert_line(failed_source_details: list[dict[str, Any]]) -> str:
    if not failed_source_details:
        return ''
    details = '; '.join(f"{d.get('source')}: {d.get('motif')}" for d in failed_source_details)
    return f'Immo sources KO: {details}'


def render_dashboard(manifest: dict[str, Any]) -> str:
    flights = manifest['flight_summary']
    immo = manifest['immo_summary']
    flight_cards = []
    for item in flights.get('items') or []:
        cls = 'ok' if item.get('status') == 'prod-candidate' else 'warn'
        flight_cards.append(f"""
        <article class="card {cls}" data-testid="flight-card" data-family="{html.escape(str(item.get('family')))}">
          <div class="eyebrow">{html.escape(str(item.get('family')))} · {html.escape(str(item.get('status')))}</div>
          <h3>{html.escape(str(item.get('name')))}</h3>
          <p class="metric">{html.escape(item.get('cheapest_label','n/a'))}</p>
          <p>{html.escape(str(item.get('offer_count') or 0))} offres/vols · {html.escape(str(item.get('duration_s')))}s</p>
          <a href="file://{html.escape(item.get('raw',''))}">Raw JSON</a>
        </article>""")
    immo_cards = []
    for l in immo.get('selected') or []:
        title = html.escape(str(l.get('title') or 'Sans titre'))
        url = html.escape(str(l.get('url') or '#'))
        immo_cards.append(f"""
        <article class="listing" data-testid="immo-listing" data-source="{html.escape(str(l.get('source_site')))}">
          <div class="eyebrow">{html.escape(str(l.get('source_site')))} · score {html.escape(str(l.get('business_score')))}</div>
          <h3>{title}</h3>
          <p><b>{html.escape(str(l.get('rent_eur')))} €</b> · {html.escape(str(l.get('city')))} · {html.escape(str(l.get('surface_m2')))} m² · {html.escape(str(l.get('rooms')))} pièces</p>
          <a href="{url}">Ouvrir l’annonce</a>
        </article>""")
    sources = ''.join(f'<span>{html.escape(k)} <b>{v}</b></span>' for k, v in sorted((immo.get('sources') or {}).items()))
    generated = html.escape(str(manifest.get('generated_at')))
    run_dir = html.escape(str(manifest.get('run_dir')))
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RUN Watch — Immo & Vols</title>
<style>
:root {{ --bg:#fbf7ef; --ink:#1d1b16; --muted:#6d665b; --line:#e7dccb; --card:#fffdf8; --ok:#15803d; --warn:#b45309; --accent:#2563eb; }}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;line-height:1.45}} 
main{{max-width:1180px;margin:0 auto;padding:36px 20px 60px}} header{{display:grid;gap:18px;margin-bottom:28px}} h1{{font-size:clamp(34px,6vw,72px);line-height:.95;margin:0;letter-spacing:-.055em}} h2{{font-size:26px;margin:34px 0 14px}} h3{{margin:.25rem 0 .35rem;font-size:18px;letter-spacing:-.02em}} a{{color:var(--accent);font-weight:650;text-decoration:none}} .sub{{font-size:18px;color:var(--muted);max-width:800px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}} .card,.listing,.panel{{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:18px;box-shadow:0 8px 30px rgba(55,39,12,.06)}} .card.ok{{border-color:rgba(21,128,61,.28)}} .card.warn{{border-color:rgba(180,83,9,.28)}} .eyebrow{{text-transform:uppercase;letter-spacing:.08em;font-size:11px;color:var(--muted);font-weight:800}} .metric{{font-size:28px;font-weight:850;letter-spacing:-.04em;margin:.4rem 0}} .sources{{display:flex;gap:8px;flex-wrap:wrap}} .sources span{{background:#fff;border:1px solid var(--line);border-radius:999px;padding:7px 10px;color:var(--muted)}} .listings{{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:12px}} code{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:2px 6px}} .proof{{font-size:13px;color:var(--muted)}}
</style>
</head>
<body><main>
<header>
  <div class="eyebrow">Run vérifié · {generated}</div>
  <h1>RUN Watch<br>Immo & Vols</h1>
  <p class="sub">Surface de décision pendant qu’on attend les HAR/NetLog : sources officielles exploitables, fallback marché, et meilleures annonces immo filtrées.</p>
  <p class="proof">Run: <code>{run_dir}</code></p>
</header>
<section class="panel" data-testid="summary-panel">
  <h2>Résumé 1 minute</h2>
  <p data-testid="summary-text"><b>Vols:</b> {len([i for i in flights.get('items',[]) if i.get('status')=='prod-candidate'])} source(s) exploitables. <b>Immo:</b> {html.escape(str(immo.get('selected_count',0)))} annonces sélectionnées sur {html.escape(str(len(immo.get('sources') or {})))} sources.</p>
</section>
<section data-testid="flights-section"><h2>Vols</h2><div class="grid">{''.join(flight_cards) or '<p>Aucun run vol.</p>'}</div></section>
<section data-testid="immo-section"><h2>Immobilier</h2><div class="sources" data-testid="immo-sources">{sources}</div><div class="listings" data-testid="immo-listings">{''.join(immo_cards) or '<p>Aucune annonce.</p>'}</div></section>
</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir')
    ap.add_argument('--skip-flight', action='store_true')
    ap.add_argument('--skip-immo', action='store_true')
    ap.add_argument('--flight-quick', action='store_true', help='skip French Bee to keep the run short')
    ap.add_argument('--flight-dates', nargs='+', default=['2026-07-19'])
    ap.add_argument('--return-date', default='2026-07-26')
    ap.add_argument('--max-price', type=int, default=1200)
    ap.add_argument('--min-price', type=int, default=450)
    ap.add_argument('--min-rooms', type=int, default=2)
    ap.add_argument('--limit', type=int, default=15)
    ap.add_argument('--kiwi-proxy', default=os.environ.get('PLAYWRIGHT_PROXY', ''), help='Explicit proxy passed to flight_watch.py for Kiwi Playwright captures')
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else ARTIFACT_ROOT / now_tag()
    run_dir.mkdir(parents=True, exist_ok=True)

    commands: dict[str, Any] = {}
    flight_run_dir: Path | None = None
    immo_run_dir: Path | None = None

    if args.skip_flight:
        flight_run_dir = latest_child(ROOT / 'artifacts/flight-watch')
    else:
        flight_run_dir = ROOT / 'artifacts/flight-watch' / (run_dir.name + ('_flight_quick' if args.flight_quick else '_flight'))
        cmd = [PY, str(ROOT / 'scripts/flight_watch.py'), '--run-dir', str(flight_run_dir), '--dates', *args.flight_dates, '--return-date', args.return_date, '--air-austral-dest', 'CDG']
        if args.kiwi_proxy:
            cmd += ['--kiwi-proxy', args.kiwi_proxy]
        if args.flight_quick:
            cmd.append('--skip-frenchbee')
        commands['flight'] = run_cmd(cmd, timeout=600)
        write_text(run_dir / 'flight_cmd_stdout.txt', commands['flight']['stdout'])
        write_text(run_dir / 'flight_cmd_stderr.txt', commands['flight']['stderr'])

    if args.skip_immo:
        immo_run_dir = latest_child(ROOT / 'artifacts/realestate/watch_runs')
    else:
        immo_run_dir = ROOT / 'artifacts/realestate/watch_runs' / (run_dir.name + '_immo')
        cmd = [PY, str(ROOT / 'scripts/realestate_watch.py'), '--run-dir', str(immo_run_dir), '--max-price', str(args.max_price), '--min-price', str(args.min_price), '--min-rooms', str(args.min_rooms), '--limit', str(args.limit)]
        commands['immo'] = run_cmd(cmd, timeout=180)
        write_text(run_dir / 'immo_cmd_stdout.txt', commands['immo']['stdout'])
        write_text(run_dir / 'immo_cmd_stderr.txt', commands['immo']['stderr'])

    flight_json = load_json((flight_run_dir / 'SUMMARY.json')) if flight_run_dir else None
    immo_json = load_json((immo_run_dir / 'realestate_watch_report.json')) if immo_run_dir else None
    flight_summary = summarize_flights(flight_json)
    immo_summary = summarize_immo(immo_json)

    prod_flights = [i for i in flight_summary.get('items', []) if i.get('status') == 'prod-candidate']
    manifest = {
        'ok': True,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'run_dir': str(run_dir),
        'flight_run_dir': str(flight_run_dir) if flight_run_dir else None,
        'immo_run_dir': str(immo_run_dir) if immo_run_dir else None,
        'commands': {k: {kk: vv for kk, vv in v.items() if kk not in {'stdout','stderr'}} for k, v in commands.items()},
        'flight_summary': flight_summary,
        'prod_flights': prod_flights,
        'immo_summary': immo_summary,
    }
    # command failure does not erase usable previous artifacts, but makes manifest non-perfect
    manifest['ok'] = all(v.get('exit_code') == 0 for v in commands.values()) and (flight_summary.get('ok') or args.skip_flight) and (immo_summary.get('ok') or args.skip_immo)

    write_text(run_dir / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    html_path = Path(write_text(run_dir / 'dashboard.html', render_dashboard(manifest)))

    msg = [
        f"RUN Watch — {manifest['generated_at']}",
        f"Vols: {len(prod_flights)} prod-candidate / {len(flight_summary.get('items', []))} tâches",
    ]
    for item in prod_flights:
        label = item.get('family') or item.get('name') or 'source'
        offers = item.get('offer_count') if item.get('offer_count') is not None else 'n/a'
        msg.append(f"- {label}: {item.get('cheapest_label', 'n/a')} · {offers} offres · {item.get('duration_s')}s")
    immo_failed_line = immo_failed_sources_alert_line(immo_summary.get('failed_source_details') or [])
    if immo_failed_line:
        msg.append(immo_failed_line)
    msg.extend([
        f"Immo: {immo_summary.get('selected_count', 0)} annonces sélectionnées / {len(immo_summary.get('sources') or {})} sources",
        f"Dashboard: {html_path}",
        f"Manifest: {run_dir / 'manifest.json'}",
    ])
    write_text(run_dir / 'telegram_summary.txt', '\n'.join(msg) + '\n')
    if manifest['ok']:
        symlink_latest(run_dir)
    else:
        write_text(run_dir / 'NOT_LATEST_FAILED_RUN.txt', 'Run non promu vers latest car manifest.ok=false. Voir manifest.json et *_cmd_stderr.txt.\n')
    print(json.dumps({'ok': manifest['ok'], 'run_dir': str(run_dir), 'dashboard': str(html_path), 'telegram_summary': str(run_dir/'telegram_summary.txt')}, ensure_ascii=False, indent=2))
    return 0 if manifest['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
