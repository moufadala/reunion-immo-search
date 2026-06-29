# Réunion Immo Search

Portail et pipeline de veille immobilière à La Réunion.

Ce dépôt contient le code applicatif, les scripts de génération, les audits QA et le déploiement statique. Les bases de données, logs, caches, images, artefacts publics volumineux et secrets ne sont pas versionnés.

## Qualité / sécurité

Avant publication ou déploiement, exécuter les audits disponibles dans `tests/` depuis l'environnement VPS configuré.

Chemin de publication sûr :

```bash
IMMO_STAGE_DB=1 /opt/data/scripts/immo_daily_public_refresh.sh
```

Le daily travaille sur une DB stage, applique les gates volume/source, promeut la DB puis l'app seulement après QA, et exécute des drills rollback DB/app.

## Données et GitHub public

Les données réelles vivent hors dépôt sur le VPS (`/opt/data/data`, `/opt/data/artifacts`) et ne doivent pas être poussées sur GitHub.

En particulier, `artifacts/app/` est un **résultat généré de production** servi par le VPS/Traefik, pas une source Git. Il est ignoré par `.gitignore` et bloqué par `tests/audit_github_public_safety.py` pour éviter de publier des contacts, exports scrape, bases, caches ou secrets dans le dépôt public.

Le dépôt public doit rester limité à : code source, scripts, tests, documentation, configuration exemple et fixtures explicitement anonymisées.

## Monitoring

Le watchdog public silencieux est disponible comme template dans `scripts/immo_public_monitor.py` et opéré sur le VPS via cron Hermes `immo-public-monitor-30m`.
