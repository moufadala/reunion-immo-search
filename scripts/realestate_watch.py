#!/usr/bin/env python3
"""Business orchestrator for La Réunion rental scrapers.

Wraps existing source scrapers, keeps SQLite as source of truth, and emits
notification/report-ready outputs. Non destructive: before refresh, the DB is
copied to a timestamped backup when it exists.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/opt/data')
DB_DEFAULT = ROOT / 'data/reunion_watch.db'
ARTIFACT_ROOT = ROOT / 'artifacts/realestate/watch_runs'
PARTIAL_SOURCE_NO_STALE = {'zimo', 'domimmo'}
MULTI_SCRAPER = ROOT / 'scripts/realestate_multi_sources_scraper.py'
# One source = one process, sequential SQLite writer. Timeouts are deliberately
# per source so a slow/broken source cannot starve the following ones.
SOURCE_JOBS = [
    {'source': 'bienici', 'script': ROOT / 'scripts/bienici_rental_scraper.py', 'timeout': 300, 'args': []},
    {'source': 'ofim', 'script': ROOT / 'scripts/ofim_rental_scraper.py', 'timeout': 300, 'args': []},
    {'source': 'domimmo', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'domimmo']},
    {'source': 'locamoi', 'script': MULTI_SCRAPER, 'timeout': 120, 'args': ['--only', 'locamoi']},
    {'source': 'citya', 'script': MULTI_SCRAPER, 'timeout': 360, 'args': ['--only', 'citya']},
    {'source': 'zimo', 'script': MULTI_SCRAPER, 'timeout': 360, 'args': ['--only', 'zimo']},
    {'source': 'immo974', 'script': MULTI_SCRAPER, 'timeout': 240, 'args': ['--only', 'immo974']},
    {'source': 'fnaim', 'script': MULTI_SCRAPER, 'timeout': 540, 'args': ['--only', 'fnaim']},
    {'source': '97immo', 'script': MULTI_SCRAPER, 'timeout': 300, 'args': ['--only', '97immo']},
    {'source': 'ofim_rss', 'script': MULTI_SCRAPER, 'timeout': 90, 'args': ['--only', 'ofim_rss']},
    {'source': 'alter', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'alter']},
    {'source': 'superimmo', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'superimmo']},
    {'source': 'leboncoin', 'script': MULTI_SCRAPER, 'timeout': 300, 'args': ['--only', 'leboncoin']},
    {'source': 'adrezio', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'adrezio']},
]
CRITICAL_REFRESH_SOURCES = {job['source'] for job in SOURCE_JOBS}
SOURCE_STATUS_ALIASES = {'leboncoin_apify_dataset': 'leboncoin'}

@dataclass
class RunnerResult:
    script: str
    ok: bool
    exit_code: int
    stdout_path: str
    stderr_path: str
    parsed_summary: dict[str, Any]
    source: str | None = None
    duration_sec: float | None = None
    timeout_sec: int | None = None
    status_path: str | None = None


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def ensure_schema(conn: sqlite3.Connection) -> None:
    # The individual scrapers already create the main table. Add/report indexes
    # here so every business query is fast and stable.
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rental_active_seen ON rental_listings(is_active, seen_last_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rental_source_seen ON rental_listings(source_site, seen_last_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rental_price_surface ON rental_listings(rent_eur, surface_m2)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rental_rooms ON rental_listings(rooms, bedrooms)')
    conn.commit()


def backup_db(db: Path, run_dir: Path) -> str | None:
    if not db.exists():
        return None
    backup_dir = run_dir / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f'{db.name}.bak.{now_tag()}'
    shutil.copy2(db, dest)
    return str(dest)


def parse_scraper_stdout(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        return {'json_ok': False, 'error': repr(e)}
    out: dict[str, Any] = {'json_ok': True}
    if isinstance(data, dict):
        if isinstance(data.get('events'), list):
            out['events'] = len(data['events'])
            by: dict[str, int] = {}
            for e in data['events']:
                src = e.get('source_site') or data.get('source') or 'unknown'
                by[src] = by.get(src, 0) + 1
            out['by_source'] = by
            out['new'] = sum(1 for e in data['events'] if e.get('status') == 'new')
            out['changed'] = sum(1 for e in data['events'] if e.get('status') == 'changed')
            out['seen'] = sum(1 for e in data['events'] if e.get('status') == 'seen')
        elif isinstance(data.get('events'), int):
            out['events'] = data.get('events')
            out['by_source'] = data.get('by_source', {})
            out['new'] = data.get('new', 0)
            out['changed'] = data.get('changed', 0)
            out['seen'] = data.get('seen', 0)
        for k in ['errors', 'fetched_items', 'rental_listings', 'new_or_changed', 'source_status']:
            if k in data:
                out[k] = data[k]
    return out


def _normalize_source_name(source: str) -> str:
    return SOURCE_STATUS_ALIASES.get(source, source)


def _normalized_source_status(parsed: dict[str, Any]) -> dict[str, Any]:
    status = parsed.get('source_status') or {}
    if not isinstance(status, dict):
        return {}
    out: dict[str, Any] = {}
    for src, value in status.items():
        out[_normalize_source_name(str(src))] = value
    return out


def _source_result_ok(parsed: dict[str, Any], source: str, exit_code: int) -> bool:
    if exit_code != 0 or parsed.get('json_ok') is not True:
        return False
    statuses = _normalized_source_status(parsed)
    if source in statuses:
        st = statuses[source]
        if not isinstance(st, dict):
            return False
        return bool(st.get('ok')) and int(st.get('count') or 0) > 0
    by_source = parsed.get('by_source') or {}
    if isinstance(by_source, dict) and int(by_source.get(source) or 0) > 0:
        return True
    # A critical source that returns JSON but no exploitable named status must
    # fail the refresh: job success is not source success.
    return source not in CRITICAL_REFRESH_SOURCES


def _annotate_source_status(parsed: dict[str, Any], source: str, *, ok: bool, duration_sec: float, timeout_sec: int, error: str | None = None) -> None:
    statuses = _normalized_source_status(parsed)
    if source not in statuses:
        count = int((parsed.get('by_source') or {}).get(source) or 0) if isinstance(parsed.get('by_source'), dict) else 0
        statuses[source] = {'ok': ok and count > 0, 'count': count}
    st = statuses[source]
    if isinstance(st, dict):
        st['duration_sec'] = round(duration_sec, 3)
        st['timeout_sec'] = timeout_sec
        if error:
            st['error'] = error
    parsed['source_status'] = statuses


def _aggregate_runner_results(results: list[RunnerResult], run_dir: Path) -> None:
    aggregate: dict[str, Any] = {
        'json_ok': True,
        'events': 0,
        'new': 0,
        'changed': 0,
        'seen': 0,
        'by_source': {},
        'source_status': {},
        'errors': [],
        'durations_sec': {},
    }
    for r in results:
        p = r.parsed_summary or {}
        for key in ['events', 'new', 'changed', 'seen']:
            aggregate[key] += int(p.get(key) or 0)
        if isinstance(p.get('by_source'), dict):
            for src, count in p['by_source'].items():
                src = _normalize_source_name(str(src))
                aggregate['by_source'][src] = aggregate['by_source'].get(src, 0) + int(count or 0)
        for src, st in _normalized_source_status(p).items():
            aggregate['source_status'][src] = st
        if isinstance(p.get('errors'), list):
            aggregate['errors'].extend(p['errors'])
        if r.source:
            aggregate['durations_sec'][r.source] = r.duration_sec
    (run_dir / 'realestate_scraper_aggregate.json').write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding='utf-8')


def run_scrapers(db: Path, run_dir: Path, dry_run: bool = False, timeout: int | None = None) -> list[RunnerResult]:
    results: list[RunnerResult] = []
    for job in SOURCE_JOBS:
        source = str(job['source'])
        script = Path(job['script'])
        env_key = f"IMMO_SOURCE_TIMEOUT_{source.upper().replace('-', '_')}"
        source_timeout = int(os.environ.get(env_key, '') or job['timeout'])
        if timeout is not None:
            source_timeout = int(timeout)
        name = f"{script.stem}__{source}"
        stdout_path = run_dir / f'{name}.json'
        stderr_path = run_dir / f'{name}.stderr'
        status_path = run_dir / f'{name}.status.json'
        raw_dir = run_dir / 'raw' / source
        raw_dir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(script), '--db', str(db), *[str(x) for x in job.get('args', [])]]
        if dry_run:
            cmd.append('--dry-run')
        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        env = os.environ.copy()
        env['IMMO_RAW_DIR'] = str(raw_dir)
        env['IMMO_REFRESH_SOURCE'] = source
        try:
            with stdout_path.open('w', encoding='utf-8') as out, stderr_path.open('w', encoding='utf-8') as err:
                proc = subprocess.run(cmd, stdout=out, stderr=err, timeout=source_timeout, cwd=str(ROOT), env=env)
            duration = time.monotonic() - t0
            parsed = parse_scraper_stdout(stdout_path)
            ok = _source_result_ok(parsed, source, proc.returncode)
            error = None if ok else f'source {source} returned no successful exploitable status (exit={proc.returncode})'
            _annotate_source_status(parsed, source, ok=ok, duration_sec=duration, timeout_sec=source_timeout, error=error)
            rr = RunnerResult(name, ok, proc.returncode, str(stdout_path), str(stderr_path), parsed, source, round(duration, 3), source_timeout, str(status_path))
        except subprocess.TimeoutExpired as e:
            duration = time.monotonic() - t0
            error = f'TIMEOUT after {source_timeout}s: source={source} cmd={cmd}'
            stderr_path.write_text(f'{error}\n{e}\n', encoding='utf-8')
            if not stdout_path.exists():
                stdout_path.write_text('', encoding='utf-8')
            parsed = {'json_ok': False, 'source_status': {source: {'ok': False, 'count': 0, 'duration_sec': round(duration, 3), 'timeout_sec': source_timeout, 'error': error}}, 'errors': [{'source': source, 'error': error}]}
            rr = RunnerResult(name, False, 124, str(stdout_path), str(stderr_path), parsed, source, round(duration, 3), source_timeout, str(status_path))
        status_payload = {
            'source': source,
            'script': str(script),
            'cmd': cmd,
            'started_at': started.isoformat(),
            'duration_sec': rr.duration_sec,
            'timeout_sec': source_timeout,
            'ok': rr.ok,
            'exit_code': rr.exit_code,
            'stdout_path': str(stdout_path),
            'stderr_path': str(stderr_path),
            'parsed_summary': rr.parsed_summary,
        }
        status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding='utf-8')
        results.append(rr)
    _aggregate_runner_results(results, run_dir)
    return results


def mark_stale_not_seen(db: Path, refresh_started_at: str, sources: list[str], seen_counts: dict[str, int] | None = None) -> dict[str, int]:
    """Mark old rows inactive after a successful refresh.

    We never delete rows. If a scraper no longer sees a listing during this run,
    it is marked inactive so business reports/alerts stay clean while history is
    preserved.
    """
    if not db.exists() or not sources:
        return {}
    conn = sqlite3.connect(db)
    try:
        ensure_schema(conn)
        out: dict[str, int] = {}
        seen_counts = seen_counts or {}
        for src in sources:
            if src in PARTIAL_SOURCE_NO_STALE:
                out[src] = 0
                continue
            active_before = conn.execute(
                'SELECT COUNT(*) FROM rental_listings WHERE source_site=? AND COALESCE(is_active,1)=1',
                (src,),
            ).fetchone()[0]
            seen_now = int(seen_counts.get(src) or 0)
            # Some sources expose only a partial first page even when the request is
            # technically successful. Do not turn a partial page into mass
            # disappearances: freshness can still be updated by new/seen rows, but
            # old rows stay active until a fuller source run confirms absence.
            if active_before >= 10 and seen_now > 0 and seen_now < max(5, int(active_before * 0.5)):
                out[src] = 0
                continue
            cur = conn.execute(
                'UPDATE rental_listings SET is_active=0 WHERE source_site=? AND datetime(seen_last_at) < datetime(?)',
                (src, refresh_started_at),
            )
            out[src] = cur.rowcount if cur.rowcount is not None else 0
        conn.commit()
        return out
    finally:
        conn.close()


def row_to_dict(cur: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def business_score(row: dict[str, Any]) -> dict[str, Any]:
    rent = row.get('rent_eur')
    surface = row.get('surface_m2')
    rooms = row.get('rooms') or 0
    price_m2 = round(rent / surface, 2) if rent and surface else None
    flags: list[str] = []
    score = 0
    if rent:
        if rent <= 700: score += 4; flags.append('budget<=700')
        elif rent <= 900: score += 3; flags.append('budget<=900')
        elif rent <= 1200: score += 1; flags.append('budget<=1200')
    if surface:
        if surface >= 50: score += 2; flags.append('surface>=50')
        elif surface >= 35: score += 1; flags.append('surface>=35')
    if rooms >= 3: score += 2; flags.append('T3+')
    elif rooms == 2: score += 1; flags.append('T2')
    if row.get('image_url'): score += 1
    if row.get('description'): score += 1
    if price_m2 is not None:
        if price_m2 <= 18: score += 2; flags.append('€/m²<=18')
        elif price_m2 <= 25: score += 1; flags.append('€/m²<=25')
    row['price_per_m2'] = price_m2
    row['business_score'] = score
    row['business_flags'] = flags
    return row


def query_listings(db: Path, *, max_price: int | None, min_price: int | None, min_rooms: int | None, city_like: str | None, limit: int, active_only: bool = True, residential_only: bool = True) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    where = []
    params: list[Any] = []
    if active_only:
        where.append('(is_active=1 OR is_active IS NULL)')
    if residential_only:
        # Exclude storage, offices, retail/commercial and land by default; they pollute
        # apartment/house alerts with cheap but irrelevant “rentals”.
        haystack = 'lower(coalesce(property_type, "") || " " || coalesce(title, "") || " " || coalesce(city, "") || " " || coalesce(description, ""))'
        for bad in ['bureau', 'bureaux', 'local commercial', 'commerce', 'fonds de commerce', 'terrain', 'box', 'garde meuble', 'parking']:
            where.append(f'{haystack} NOT LIKE ?')
            params.append('%' + bad + '%')
        title_city = 'lower(coalesce(title, "") || " " || coalesce(city, ""))'
        where.append(f"{title_city} NOT LIKE ?")
        params.append('%chambre meubl%')
        where.append(f"{title_city} NOT LIKE ?")
        params.append('%colocation%')
    if max_price is not None:
        where.append('(rent_eur IS NOT NULL AND rent_eur <= ?)')
        params.append(max_price)
    if min_price is not None:
        where.append('(rent_eur IS NOT NULL AND rent_eur >= ?)')
        params.append(min_price)
    if min_rooms is not None:
        where.append('(rooms IS NOT NULL AND rooms >= ?)')
        params.append(min_rooms)
    if city_like:
        where.append('(lower(coalesce(city, "") || " " || coalesce(title, "") || " " || coalesce(description, "")) LIKE ?)')
        params.append('%' + city_like.lower() + '%')
    sql = 'SELECT * FROM rental_listings'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY datetime(seen_last_at) DESC, COALESCE(rent_eur, 999999) ASC LIMIT ?'
    params.append(limit * 4)
    rows = [business_score(row_to_dict(conn.cursor(), r)) for r in conn.execute(sql, params)]
    rows.sort(key=lambda r: (r.get('business_score') or 0, -(r.get('rent_eur') or 999999)), reverse=True)
    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for r in rows:
        key = (
            (r.get('city') or '').strip().lower(),
            r.get('rent_eur'),
            round(float(r.get('surface_m2') or 0), 1),
            r.get('rooms') or None,
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(r)
    conn.close()
    return deduped[:limit]


def db_summary(db: Path) -> dict[str, Any]:
    if not db.exists():
        return {'exists': False}
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    by_source = {r['source_site']: r['n'] for r in conn.execute('SELECT source_site, count(*) n FROM rental_listings GROUP BY source_site ORDER BY source_site')}
    quality = dict(conn.execute('''SELECT
        count(*) total,
        sum(case when rent_eur is not null then 1 else 0 end) rent_filled,
        sum(case when city is not null and city != '' then 1 else 0 end) city_filled,
        sum(case when surface_m2 is not null then 1 else 0 end) surface_filled,
        sum(case when image_url is not null and image_url != '' then 1 else 0 end) image_filled
        FROM rental_listings''').fetchone())
    newest = [dict(r) for r in conn.execute('SELECT source_site, title, city, rent_eur, surface_m2, seen_last_at, url FROM rental_listings ORDER BY datetime(seen_last_at) DESC LIMIT 10')]
    conn.close()
    return {'exists': True, 'db': str(db), 'by_source': by_source, 'quality': quality, 'newest': newest}


def write_reports(run_dir: Path, db: Path, runner_results: list[RunnerResult], listings: list[dict[str, Any]], backup_path: str | None, stale_counts: dict[str, int], args: argparse.Namespace) -> dict[str, str]:
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'db_backup': backup_path,
        'stale_marked_inactive': stale_counts,
        'db_summary': db_summary(db),
        'runner_results': [asdict(r) for r in runner_results],
        'filters': {'max_price': args.max_price, 'min_price': args.min_price, 'min_rooms': args.min_rooms, 'city': args.city, 'limit': args.limit, 'residential_only': not args.include_commercial},
        'selected_count': len(listings),
        'selected': listings,
    }
    json_path = run_dir / 'realestate_watch_report.json'
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    md_path = run_dir / 'realestate_watch_report.md'
    lines = [
        '# Rapport immobilier Réunion', '',
        f'- Généré: {summary["generated_at"]}',
        f'- DB: `{db}`',
        f'- Backup DB: `{backup_path}`' if backup_path else '- Backup DB: non nécessaire',
        f'- Anciennes annonces marquées inactives: `{json.dumps(stale_counts, ensure_ascii=False)}`',
        '', '## Santé sources', ''
    ]
    for r in runner_results:
        status = 'OK' if r.ok else 'FAIL'
        lines.append(f'- {r.script}: {status}, exit={r.exit_code}, résumé={json.dumps(r.parsed_summary, ensure_ascii=False)}')
    lines += ['', '## Couverture DB', '']
    dbs = summary['db_summary']
    lines.append(f'- Par source: `{json.dumps(dbs.get("by_source", {}), ensure_ascii=False)}`')
    lines.append(f'- Qualité: `{json.dumps(dbs.get("quality", {}), ensure_ascii=False)}`')
    lines += ['', '## Top annonces métier', '']
    for i, l in enumerate(listings[:args.limit], 1):
        lines.append(f'{i}. **{l.get("title") or "Sans titre"}**')
        lines.append(f'   - Source: {l.get("source_site")} | Ville: {l.get("city")} | Loyer: {l.get("rent_eur")} € | Surface: {l.get("surface_m2")} m² | Score: {l.get("business_score")}')
        if l.get('price_per_m2') is not None:
            lines.append(f'   - Prix/m²: {l.get("price_per_m2")} €/m² | Flags: {", ".join(l.get("business_flags") or [])}')
        lines.append(f'   - URL: {l.get("url")}')
    md_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'json': str(json_path), 'markdown': str(md_path)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DB_DEFAULT))
    ap.add_argument('--refresh', action='store_true', help='Run all source scrapers and update DB')
    ap.add_argument('--dry-run-scrapers', action='store_true', help='Run scrapers without writing DB')
    ap.add_argument('--max-price', type=int, default=900)
    ap.add_argument('--min-price', type=int, default=450)
    ap.add_argument('--min-rooms', type=int)
    ap.add_argument('--city')
    ap.add_argument('--include-commercial', action='store_true', help='Include offices, storage, commercial premises and land in selected listings')
    ap.add_argument('--limit', type=int, default=25)
    ap.add_argument('--run-dir')
    args = ap.parse_args()

    db = Path(args.db)
    run_dir = Path(args.run_dir) if args.run_dir else ARTIFACT_ROOT / now_tag()
    run_dir.mkdir(parents=True, exist_ok=True)
    # Stable pointer for Morning Brief / Telegram handoff / manual inspection.
    latest = ARTIFACT_ROOT / 'latest'
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(run_dir, target_is_directory=True)
    except Exception:
        pass

    backup = backup_db(db, run_dir) if args.refresh and not args.dry_run_scrapers else None
    results: list[RunnerResult] = []
    stale_counts: dict[str, int] = {}
    refresh_started_at = datetime.now(timezone.utc).isoformat()
    if args.refresh or args.dry_run_scrapers:
        results = run_scrapers(db, run_dir, dry_run=args.dry_run_scrapers)
        if args.refresh and not args.dry_run_scrapers:
            # Non destructif par source: une annonce devient inactive seulement si
            # SA source a été vue avec succès pendant ce run. Un 403/timeout sur une
            # source ne doit jamais créer de fausse disparition.
            touched_sources = set()
            seen_counts: dict[str, int] = {}
            for r in results:
                for src, count in (r.parsed_summary.get('by_source') or {}).items():
                    touched_sources.add(src)
                    seen_counts[src] = seen_counts.get(src, 0) + int(count or 0)
                for src, st in (r.parsed_summary.get('source_status') or {}).items():
                    if isinstance(st, dict) and st.get('ok') and st.get('count', 0) > 0:
                        # Internal function names mostly match source_site; explicit mapping
                        # protects aliases like scrape_97immo -> source_site 97immo.
                        mapped = {'97immo': '97immo', 'ofim_rss': 'ofim_rss'}.get(src, src)
                        touched_sources.add(mapped)
                        seen_counts[mapped] = max(seen_counts.get(mapped, 0), int(st.get('count') or 0))
            stale_counts = mark_stale_not_seen(db, refresh_started_at, sorted(touched_sources), seen_counts)
    if db.exists():
        listings = query_listings(db, max_price=args.max_price, min_price=args.min_price, min_rooms=args.min_rooms, city_like=args.city, limit=args.limit, residential_only=not args.include_commercial)
    else:
        listings = []
    paths = write_reports(run_dir, db, results, listings, backup, stale_counts, args)
    print(json.dumps({'ok': all(r.ok for r in results) if results else True, 'run_dir': str(run_dir), 'reports': paths, 'db_summary': db_summary(db), 'selected_count': len(listings)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
