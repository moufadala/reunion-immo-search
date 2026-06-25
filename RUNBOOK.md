# RUNBOOK — Immo Réunion search/dashboard

## Boundary
Project-specific. Do not mix with LapubRe catalogues, vols, or Morning Brief except when explicitly producing a portfolio synthesis.

## Trigger aliases
immo, immobilier, OFIM, SeLoger, FNAIM, Superimmo, locations Réunion, `reunion-immo-search`.

## Required workflow
1. Load project registry entry `immo_reunion`.
2. Load skills: `web-scraping-reality`, `web-scraping-tools`, `professional-project-delivery`; add `dogfood` for UI/public QA.
3. Run quick gate before claiming scraper health:
   ```bash
   /opt/data/scripts/scraper_quick_gate.sh
   ```
4. Check DB freshness, not only row count. Compare latest DB observed date with at least one live/source probe.
5. For static dashboard publication via Docker, remember host path mapping: Hermes `/opt/data/...` appears as Docker host `/opt/hermes/data/...`.

## QA gates
- Quick gate prod-candidates pass.
- DB rows, latest date, prices, cities, surfaces, images reported.
- Public URL tested with browser/console/screenshot if dashboard published.
- Daily refresh canonical path:
  ```bash
  IMMO_STAGE_DB=1 /opt/data/scripts/immo_daily_public_refresh.sh
  ```
  It must build against a stage DB first, then promote DB/app only after gates pass. The tracked template lives at `scripts/immo_daily_public_refresh.sh`.
- Before replacing `artifacts/app`, promote candidates via:
  ```bash
  python3 scripts/promote_app_candidate.py --candidate <candidate_app> --target artifacts/app
  ```
  This runs `scripts/audit_public_delta_guard.py` and blocks if global volume drops >15% or critical source SeLoger drops >35%.
- Before replacing the production SQLite DB, promote the stage DB via:
  ```bash
  python3 scripts/promote_db_candidate.py --candidate <stage_db> --target /opt/data/data/reunion_watch.db --json-out <run_dir>/promote_db.json
  python3 scripts/rollback_db_candidate.py --backup <backup_from_promote_db.json> --target /opt/data/data/reunion_watch.db --json-out <run_dir>/rollback_db_drill.json
  ```
  This runs `PRAGMA integrity_check`, global active-row guard, and critical-source guard before replacing prod DB.
- Run non-destructive intelligence/ops gates after build:
  ```bash
  python3 scripts/audit_dedup_public.py --listings <candidate_app>/listings.json
  python3 scripts/generate_daily_summary.py --app artifacts/app --out-dir <run_dir>/daily_summary
  python3 scripts/rollback_public_app.py --backup <known_good_backup> --target artifacts/app --qa-cmd 'python3 tests/audit_clean_portal.py'
  ```
- Public watchdog is script-only and silent on success:
  ```bash
  /opt/data/scripts/immo_public_monitor.py
  ```
  Cron job: `immo-public-monitor-30m`. It alerts only on public URL/listing/source degradation.

## Known traps
- A green row count can hide stale data.
- OFIM city/zone normalization requires hyphen replacement.
- Do not overwrite prod/public dashboard without snapshot/rollback.
- Public UI invariant: `artifacts/app/index.html` must be the clean V1 portal (`Recherche immo RUN — portail propre`), not the technical `build_app.py` workbench (`moteur visuel`). The daily refresh must run technical audits before overlay, then publish `scripts/build_clean_portal_v1.py` output and run `tests/audit_clean_portal.py`.
- Publication runs with restrictive `umask 077`; after copying public static files, force `chmod -R a+rX artifacts/app artifacts/app_clean_v1` or nginx returns `403 Permission denied` even when files exist.
