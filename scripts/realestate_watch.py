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
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not (PROJECT_ROOT / 'src' / 'pipeline_reconciliation.py').exists():
    PROJECT_ROOT = Path('/opt/data/projects/reunion-immo-search')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline_reconciliation import (
    ManifestValidationError, SourceRunManifest, evaluate_source_manifests,
)

ROOT = Path('/opt/data')
DB_DEFAULT = ROOT / 'data/reunion_watch.db'
ARTIFACT_ROOT = ROOT / 'artifacts/realestate/watch_runs'
PARTIAL_SOURCE_NO_STALE = {'zimo', 'immo974'}
SOURCE_COVERAGE_FLOORS = {'zimo': 0.50, 'immo974': 0.50}
# A scope change retires legacy rows even when the new count is much smaller.
COMPLETE_SCOPE_SOURCES = {'domimmo'}
PARTIAL_SOURCE_STALE_GRACE_DAYS = 7
MULTI_SCRAPER = ROOT / 'scripts/realestate_multi_sources_scraper.py'
BIENICI_SCRAPER = PROJECT_ROOT / 'scripts/bienici_rental_scraper.py'
# One source = one process, sequential SQLite writer. Timeouts are deliberately
# per source so a slow/broken source cannot starve the following ones.
SOURCE_JOBS = [
    {'source': 'bienici', 'script': BIENICI_SCRAPER, 'timeout': 300, 'args': []},
    {'source': 'ofim', 'script': MULTI_SCRAPER, 'timeout': 540, 'args': ['--only', 'ofim']},
    {'source': 'domimmo', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'domimmo']},
    {'source': 'locamoi', 'script': MULTI_SCRAPER, 'timeout': 120, 'args': ['--only', 'locamoi']},
    {'source': 'citya', 'script': MULTI_SCRAPER, 'timeout': 360, 'args': ['--only', 'citya']},
    {'source': 'zimo', 'script': MULTI_SCRAPER, 'timeout': 360, 'args': ['--only', 'zimo']},
    {'source': 'immo974', 'script': MULTI_SCRAPER, 'timeout': 240, 'args': ['--only', 'immo974']},
    {'source': 'fnaim', 'script': MULTI_SCRAPER, 'timeout': 540, 'args': ['--only', 'fnaim']},
    {'source': '97immo', 'script': MULTI_SCRAPER, 'timeout': 300, 'args': ['--only', '97immo']},
    {'source': 'alter', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'alter']},
    {'source': 'superimmo', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'superimmo']},
    {'source': 'leboncoin', 'script': MULTI_SCRAPER, 'timeout': 300, 'args': ['--only', 'leboncoin']},
    {'source': 'adrezio', 'script': MULTI_SCRAPER, 'timeout': 180, 'args': ['--only', 'adrezio']},
]
def _env_source_set(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


NON_BLOCKING_REFRESH_SOURCES: set[str] = _env_source_set("IMMO_NON_BLOCKING_REFRESH_SOURCES")
CRITICAL_REFRESH_SOURCES = {job['source'] for job in SOURCE_JOBS} - NON_BLOCKING_REFRESH_SOURCES
SOURCE_STATUS_ALIASES = {'leboncoin_apify_dataset': 'leboncoin'}
DEFAULT_SOURCE_SCRAPE_BUDGET_SEC = 1500
DEFAULT_SOURCE_OK_THRESHOLD = 0.70

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
    source_manifest: dict[str, Any] | None = None


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
        for k in ['errors', 'fetched_items', 'rental_listings', 'new_or_changed', 'source_status', 'source_manifests']:
            if k in data:
                out[k] = data[k]
        # Dedicated collectors (currently Bien'ici) print their single strict
        # manifest directly. Normalize it to the same mapping emitted by the
        # multi-source collector so the watcher never downgrades it to legacy.
        if all(key in data for key in (
            'source', 'status', 'run_id', 'normalized_items', 'seen_ids'
        )):
            out['source_manifests'] = {str(data['source']): data}
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
    source_manifests = parsed.get('source_manifests')
    if isinstance(source_manifests, dict) and isinstance(source_manifests.get(source), dict):
        try:
            manifest = SourceRunManifest.from_dict(source_manifests[source])
        except (ManifestValidationError, TypeError):
            return False
        return manifest.source == source and manifest.status == 'complete'


    statuses = _normalized_source_status(parsed)
    if source in statuses:
        st = statuses[source]
        if not isinstance(st, dict):
            return False
        if bool(st.get('ok')) and int(st.get('count') or 0) > 0:
            return True
        return source not in CRITICAL_REFRESH_SOURCES
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


def db_active_counts(db: Path) -> dict[str, int]:
    if not db.exists():
        return {}
    conn = sqlite3.connect(db)
    try:
        return {
            str(source): int(count)
            for source, count in conn.execute(
                'SELECT source_site, COUNT(*) FROM rental_listings '
                'WHERE COALESCE(is_active,1)=1 GROUP BY source_site'
            )
        }
    finally:
        conn.close()


def _legacy_manifest(result: RunnerResult, run_id: str, previous_count: int | None) -> dict[str, Any]:
    source = str(result.source or result.script)
    parsed = result.parsed_summary or {}
    by_source = parsed.get('by_source') if isinstance(parsed.get('by_source'), dict) else {}
    statuses = _normalized_source_status(parsed)
    status_count = statuses.get(source, {}).get('count', 0) if isinstance(statuses.get(source), dict) else 0
    count = int(by_source.get(source) or status_count or 0)
    inserted = min(count, int(parsed.get('new') or 0))
    updated = min(count - inserted, int(parsed.get('changed') or 0))
    if result.ok and count > 0:
        status = 'partial'
        signals = ['legacy_manifest_missing']
        error = None
        normalized = count
    else:
        status = 'failed'
        signals = []
        error = _runner_failure_motif(result)
        normalized = 0
        inserted = updated = 0
    return {
        'run_id': run_id, 'source': source, 'status': status, 'attempted': True,
        'pages_attempted': 0, 'pages_succeeded': 0,
        'fetched_items': normalized, 'parsed_items': normalized,
        'unique_ids': normalized, 'normalized_items': normalized, 'rejected_items': 0,
        'inserted': inserted, 'updated': updated,
        'unchanged': normalized - inserted - updated,
        'withdrawn': 0, 'reappeared': 0,
        'expected_count': previous_count if status == 'partial' else None,
        'previous_count': previous_count, 'dataset_id': None, 'retries': 0,
        'truncation_signals': signals, 'error': error,
    }


def source_manifests_for_results(results: list[RunnerResult], *, run_id: str,
                                 active_before: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Normalize adapter evidence; missing evidence becomes explicit partial."""
    active_before = active_before or {}
    out: list[dict[str, Any]] = []
    for result in results:
        source = str(result.source or result.script)
        parsed_manifests = (result.parsed_summary or {}).get('source_manifests')
        raw = result.source_manifest
        if raw is None and isinstance(parsed_manifests, dict):
            raw = parsed_manifests.get(source)
        previous = active_before.get(source)
        if isinstance(raw, dict):
            item = dict(raw)
            child_run_id = str(item.get('run_id') or '').strip()
            if not child_run_id:
                item['run_id'] = run_id
                child_run_id = run_id
            item['source'] = source
            item.setdefault('previous_count', previous)
            item.setdefault('expected_count', None)
            item.setdefault('withdrawn', 0)
            item.setdefault('reappeared', 0)
            item.setdefault('dataset_id', None)
            item.setdefault('retries', 0)
            item.setdefault('truncation_signals', [])
            item.setdefault('error', None)
            if child_run_id != run_id:
                item['status'] = 'failed'
                item['withdrawn'] = 0
                item['error'] = (
                    f'run_id mismatch: child={child_run_id} expected={run_id}'
                )
                item['truncation_signals'] = [*item['truncation_signals'], 'run_id_mismatch']
            # Exhaustive pagination is the current-run authority. A large market
            # shrink remains visible as a warning, while the durable two-run
            # absence ledger prevents one anomalous snapshot from withdrawing
            # rows. Downgrading this proof to partial would deadlock forever:
            # old rows stay active, so every later exhaustive run stays below the
            # same historical denominator and can never advance the ledger.
            current = int(item.get('unique_ids') or 0)
            floor = SOURCE_COVERAGE_FLOORS.get(source, 0.90)
            if item.get('status') == 'complete' and previous and previous >= 10 and current / previous < floor:
                item['coverage_warning'] = f'below_previous_floor:{current}/{previous}'
            elif item.get('status') == 'partial' and item.get('expected_count') is None:
                item['expected_count'] = previous
        else:
            item = _legacy_manifest(result, run_id, previous)
        out.append(item)
    return out


def authoritative_withdrawal_sources(manifests: list[dict[str, Any]]) -> list[str]:
    sources = []
    for raw in manifests:
        try:
            item = SourceRunManifest.from_dict(raw)
        except (ManifestValidationError, TypeError):
            continue
        if item.authoritative_for_withdrawals:
            sources.append(item.source)
    return sorted(set(sources))


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
        'source_manifests': {},
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
        if r.source and r.source_manifest:
            aggregate['source_manifests'][r.source] = r.source_manifest
    (run_dir / 'realestate_scraper_aggregate.json').write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding='utf-8')


def _rotation_seed(run_dir: Path) -> str:
    return os.environ.get('IMMO_SOURCE_ROTATION_SEED') or (run_dir.parent.name if run_dir.name == 'realestate_watch' else run_dir.name) or now_tag()


def _rotated_source_jobs(run_dir: Path) -> list[dict[str, Any]]:
    if not SOURCE_JOBS:
        return []
    seed_text = _rotation_seed(run_dir)
    offset = int(hashlib.sha256(seed_text.encode('utf-8')).hexdigest()[:8], 16) % len(SOURCE_JOBS)
    return SOURCE_JOBS[offset:] + SOURCE_JOBS[:offset]


def _source_scrape_budget_sec() -> int | None:
    raw = os.environ.get('IMMO_SOURCE_SCRAPE_BUDGET_SEC')
    if raw is None or raw == '':
        return DEFAULT_SOURCE_SCRAPE_BUDGET_SEC
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SOURCE_SCRAPE_BUDGET_SEC
    return value if value > 0 else None


def _source_ok_threshold() -> float:
    raw = os.environ.get('IMMO_SOURCE_OK_THRESHOLD')
    if raw is None or raw == '':
        return DEFAULT_SOURCE_OK_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SOURCE_OK_THRESHOLD
    return value if 0.0 < value <= 1.0 else DEFAULT_SOURCE_OK_THRESHOLD


def _runner_failure_motif(result: RunnerResult) -> str:
    summary = result.parsed_summary or {}
    source = result.source or result.script
    if source_status := _normalized_source_status(summary).get(str(source)):
        if isinstance(source_status, dict):
            for key in ('error', 'warning', 'message', 'note'):
                value = source_status.get(key)
                if value:
                    return str(value)
    for key in ('error', 'warning', 'message', 'note'):
        value = summary.get(key)
        if value:
            return str(value)
    errors = summary.get('errors')
    if isinstance(errors, list) and errors:
        motifs = []
        for item in errors:
            if isinstance(item, dict):
                motifs.append(str(item.get('error') or item.get('message') or item))
            else:
                motifs.append(str(item))
        return '; '.join(motifs)
    if isinstance(errors, str) and errors:
        return errors
    return f'source {source} returned no successful exploitable status (exit={result.exit_code})'


def evaluate_source_gate(results: list[RunnerResult], source_manifests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    total = len(results)
    ok_results = [r for r in results if r.ok]
    failed_results = [r for r in results if not r.ok]
    ok_ratio = (len(ok_results) / total) if total else 1.0
    threshold = _source_ok_threshold()
    failed_details = [
        {
            'source': r.source or r.script,
            'script': r.script,
            'exit_code': r.exit_code,
            'motif': _runner_failure_motif(r),
            'status_path': r.status_path,
        }
        for r in sorted(failed_results, key=lambda item: item.source or item.script)
    ]
    failed_critical_sources = sorted({
        str(r.source or r.script)
        for r in failed_results
        if str(r.source or r.script) in CRITICAL_REFRESH_SOURCES
    })
    manifest_gate = None
    if source_manifests is not None:
        manifest_gate = evaluate_source_manifests(
            source_manifests, critical_sources=CRITICAL_REFRESH_SOURCES,
            coverage_floors=SOURCE_COVERAGE_FLOORS,
        )
    return {
        'ok': (
            (True if not total else ok_ratio >= threshold)
            and not failed_critical_sources
            and (manifest_gate is None or manifest_gate['ok'])
        ),
        'ok_count': len(ok_results),
        'total': total,
        'ok_ratio': round(ok_ratio, 4),
        'threshold': threshold,
        'failed_sources': [d['source'] for d in failed_details],
        'failed_source_details': failed_details,
        'failed_critical_sources': failed_critical_sources,
        'manifest_gate': manifest_gate,
    }


def _skipped_result(source: str, script: Path, run_dir: Path, reason: str, elapsed: float, budget: int | None) -> RunnerResult:
    name = f"{script.stem}__{source}"
    stdout_path = run_dir / f'{name}.json'
    stderr_path = run_dir / f'{name}.stderr'
    status_path = run_dir / f'{name}.status.json'
    parsed = {
        'json_ok': False,
        'source_status': {source: {'ok': False, 'count': 0, 'duration_sec': 0, 'timeout_sec': None, 'error': reason}},
        'errors': [{'source': source, 'error': reason}],
    }
    stdout_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding='utf-8')
    stderr_path.write_text(reason + '\n', encoding='utf-8')
    rr = RunnerResult(name, False, 125, str(stdout_path), str(stderr_path), parsed, source, 0, None, str(status_path))
    status_path.write_text(json.dumps({
        'source': source,
        'script': str(script),
        'cmd': None,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'duration_sec': 0,
        'timeout_sec': None,
        'budget_sec': budget,
        'elapsed_before_skip_sec': round(elapsed, 3),
        'ok': False,
        'exit_code': 125,
        'stdout_path': str(stdout_path),
        'stderr_path': str(stderr_path),
        'parsed_summary': parsed,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return rr


def run_scrapers(db: Path, run_dir: Path, dry_run: bool = False, timeout: int | None = None) -> list[RunnerResult]:
    results: list[RunnerResult] = []
    jobs = _rotated_source_jobs(run_dir)
    budget = _source_scrape_budget_sec()
    budget_started = time.monotonic()
    (run_dir / 'realestate_source_order.json').write_text(json.dumps({
        'budget_sec': budget,
        'rotation_seed': _rotation_seed(run_dir),
        'sources': [j['source'] for j in jobs],
        'timeouts_sec': {j['source']: j['timeout'] for j in jobs},
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    for idx, job in enumerate(jobs):
        source = str(job['source'])
        script = Path(job['script'])
        elapsed_budget = time.monotonic() - budget_started
        if budget is not None and elapsed_budget >= budget:
            skipped = [str(j['source']) for j in jobs[idx:]]
            reason = f"GLOBAL SCRAPE BUDGET EXCEEDED after {budget}s; sources not served: {', '.join(skipped)}"
            for skipped_job in jobs[idx:]:
                results.append(_skipped_result(str(skipped_job['source']), Path(skipped_job['script']), run_dir, reason, elapsed_budget, budget))
            break

        env_key = f"IMMO_SOURCE_TIMEOUT_{source.upper().replace('-', '_')}"
        source_timeout = int(os.environ.get(env_key, '') or job['timeout'])
        if timeout is not None:
            source_timeout = int(timeout)
        if budget is not None:
            remaining = max(1, int(budget - elapsed_budget))
            source_timeout = min(source_timeout, remaining)

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
        env['IMMO_RUN_ID'] = os.environ.get('IMMO_RUN_ID') or (run_dir.parent.name if run_dir.name == 'realestate_watch' else run_dir.name)
        try:
            with stdout_path.open('w', encoding='utf-8') as out, stderr_path.open('w', encoding='utf-8') as err:
                proc = subprocess.run(cmd, stdout=out, stderr=err, timeout=source_timeout, cwd=str(ROOT), env=env)
            duration = time.monotonic() - t0
            parsed = parse_scraper_stdout(stdout_path)
            ok = _source_result_ok(parsed, source, proc.returncode)
            error = None if ok else f'source {source} returned no successful exploitable status (exit={proc.returncode})'
            _annotate_source_status(parsed, source, ok=ok, duration_sec=duration, timeout_sec=source_timeout, error=error)
            raw_manifests = parsed.get('source_manifests')
            source_manifest = raw_manifests.get(source) if isinstance(raw_manifests, dict) else None
            rr = RunnerResult(name, ok, proc.returncode, str(stdout_path), str(stderr_path), parsed,
                              source, round(duration, 3), source_timeout, str(status_path),
                              source_manifest)
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
            'budget_sec': budget,
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


def mark_stale_not_seen(db: Path, refresh_started_at: str, sources: list[str],
                        seen_counts: dict[str, int] | None = None, *,
                        run_id: str | None = None, withdrawal_after: int = 2,
                        observed_ids: dict[str, set[str]] | None = None) -> dict[str, int]:
    """Persist complete-snapshot absences; deactivate at two distinct runs.

    Callers pass only sources whose manifest is ``complete``. Replaying one run
    is idempotent. Partial/failed sources are absent from ``sources`` and cannot
    advance the ledger. Listings positively observed in any run still clear an
    older absence, even when that run was partial and therefore non-authoritative
    for all listings it did not return.
    """
    observed_ids = observed_ids or {}
    if not db.exists() or (not sources and not observed_ids):
        return {}
    if withdrawal_after < 2:
        raise ValueError('withdrawal_after must be at least 2')
    run_id = str(run_id or refresh_started_at)
    conn = sqlite3.connect(db)
    try:
        ensure_schema(conn)
        conn.execute('''CREATE TABLE IF NOT EXISTS source_absence_state (
            source_site TEXT NOT NULL,
            source_id TEXT NOT NULL,
            successful_missing_runs INTEGER NOT NULL DEFAULT 0,
            last_complete_run_id TEXT,
            PRIMARY KEY(source_site, source_id)
        )''')
        for observed_source, source_ids in observed_ids.items():
            conn.executemany(
                '''DELETE FROM source_absence_state
                   WHERE source_site=? AND source_id=?''',
                ((str(observed_source), str(source_id)) for source_id in source_ids),
            )
        out: dict[str, int] = {}
        for src in sources:
            observed_for_source = {str(source_id) for source_id in observed_ids.get(src, set())}
            conn.execute('''DELETE FROM source_absence_state
                WHERE source_site=? AND source_id IN (
                    SELECT source_id FROM rental_listings
                    WHERE source_site=? AND datetime(seen_last_at) >= datetime(?)
                )''', (src, src, refresh_started_at))
            missing_ids = [str(row[0]) for row in conn.execute(
                '''SELECT source_id FROM rental_listings
                   WHERE source_site=? AND COALESCE(is_active,1)=1
                     AND datetime(seen_last_at) < datetime(?)''',
                (src, refresh_started_at),
            ) if str(row[0]) not in observed_for_source]
            for source_id in missing_ids:
                prior = conn.execute(
                    '''SELECT successful_missing_runs,last_complete_run_id
                       FROM source_absence_state WHERE source_site=? AND source_id=?''',
                    (src, source_id),
                ).fetchone()
                if prior is None:
                    conn.execute('INSERT INTO source_absence_state VALUES (?,?,1,?)',
                                 (src, source_id, run_id))
                elif prior[1] != run_id:
                    conn.execute('''UPDATE source_absence_state
                        SET successful_missing_runs=?,last_complete_run_id=?
                        WHERE source_site=? AND source_id=?''',
                        (int(prior[0]) + 1, run_id, src, source_id))
            cur = conn.execute('''UPDATE rental_listings SET is_active=0
                WHERE source_site=? AND COALESCE(is_active,1)=1
                  AND EXISTS (
                    SELECT 1 FROM source_absence_state s
                    WHERE s.source_site=rental_listings.source_site
                      AND s.source_id=rental_listings.source_id
                      AND s.successful_missing_runs>=?
                  )''', (src, withdrawal_after))
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


def write_reports(run_dir: Path, db: Path, runner_results: list[RunnerResult], listings: list[dict[str, Any]], backup_path: str | None, stale_counts: dict[str, int], args: argparse.Namespace, source_manifests: list[dict[str, Any]] | None = None) -> dict[str, str]:
    source_gate = evaluate_source_gate(runner_results, source_manifests=source_manifests)
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'db_backup': backup_path,
        'stale_marked_inactive': stale_counts,
        'source_gate': source_gate,
        'failed_sources': source_gate['failed_sources'],
        'failed_source_details': source_gate['failed_source_details'],
        'db_summary': db_summary(db),
        'runner_results': [asdict(r) for r in runner_results],
        'source_manifests': source_manifests or [],
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
    if source_gate['total']:
        percent = round(100 * source_gate['ok_ratio'])
        lines.append(f"- Couverture sources: {source_gate['ok_count']}/{source_gate['total']} OK ({percent}%), seuil={round(100 * source_gate['threshold'])}%")
    if source_gate['failed_source_details']:
        failed_text = ', '.join(f"{d['source']} ({d['motif']})" for d in source_gate['failed_source_details'])
        lines.append(f'- **Sources KO**: {failed_text}')
    lines.append('')
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
    source_manifests: list[dict[str, Any]] = []
    active_before = db_active_counts(db)
    stale_counts: dict[str, int] = {}
    refresh_started_at = datetime.now(timezone.utc).isoformat()
    if args.refresh or args.dry_run_scrapers:
        results = run_scrapers(db, run_dir, dry_run=args.dry_run_scrapers)
        logical_run_id = os.environ.get('IMMO_RUN_ID') or (run_dir.parent.name if run_dir.name == 'realestate_watch' else run_dir.name)
        source_manifests = source_manifests_for_results(
            results, run_id=logical_run_id, active_before=active_before,
        )
        if args.refresh and not args.dry_run_scrapers:
            # Non destructif par source: une annonce devient inactive seulement si
            # SA source a prouve un snapshot complet. Un succes partiel, 403 ou
            # timeout ne doit jamais creer de fausse disparition.
            authoritative = set(authoritative_withdrawal_sources(source_manifests))
            # A proven exhaustive snapshot containing zero listings is still
            # authoritative. Requiring a positive count here would make a
            # genuinely emptied portal unable to withdraw stale rows forever.
            touched_sources = set(authoritative)
            seen_counts: dict[str, int] = {}
            observed_ids: dict[str, set[str]] = {}
            for item in source_manifests:
                source = _normalize_source_name(str(item.get('source') or ''))
                if not source:
                    continue
                observed_ids.setdefault(source, set()).update(
                    str(source_id) for source_id in (item.get('seen_ids') or []) if str(source_id)
                )
            for r in results:
                if r.ok and r.source in authoritative:
                    for src, count in (r.parsed_summary.get('by_source') or {}).items():
                        mapped = _normalize_source_name(str(src))
                        touched_sources.add(mapped)
                        seen_counts[mapped] = seen_counts.get(mapped, 0) + int(count or 0)
                for src, st in _normalized_source_status(r.parsed_summary).items():
                    if src in authoritative and isinstance(st, dict) and st.get('ok') and st.get('count', 0) > 0:
                        # Only successful, non-empty sources can mark older rows stale.
                        mapped = _normalize_source_name(str(src))
                        touched_sources.add(mapped)
                        seen_counts[mapped] = max(seen_counts.get(mapped, 0), int(st.get('count') or 0))
            stale_counts = mark_stale_not_seen(
                db, refresh_started_at, sorted(touched_sources), seen_counts,
                run_id=logical_run_id, observed_ids=observed_ids,
            )
            for item in source_manifests:
                if item.get('status') == 'complete':
                    item['withdrawn'] = int(stale_counts.get(str(item.get('source'))) or 0)
        (run_dir / 'source_run_manifests.json').write_text(json.dumps({
            'run_id': logical_run_id,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sources': source_manifests,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    if db.exists():
        listings = query_listings(db, max_price=args.max_price, min_price=args.min_price, min_rooms=args.min_rooms, city_like=args.city, limit=args.limit, residential_only=not args.include_commercial)
    else:
        listings = []
    paths = write_reports(run_dir, db, results, listings, backup, stale_counts, args, source_manifests=source_manifests)
    source_gate = evaluate_source_gate(results, source_manifests=source_manifests if results else None)
    ok = source_gate['ok']
    print(json.dumps({'ok': ok, 'source_gate': source_gate, 'source_manifests': source_manifests, 'failed_sources': source_gate['failed_sources'], 'failed_source_details': source_gate['failed_source_details'], 'run_dir': str(run_dir), 'reports': paths, 'db_summary': db_summary(db), 'selected_count': len(listings)}, ensure_ascii=False, indent=2))
    if results and not ok:
        sys.exit(1)

if __name__ == '__main__':
    main()
