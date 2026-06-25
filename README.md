# Réunion Immo Search

Portail et pipeline de veille immobilière à La Réunion.

Ce dépôt contient le code applicatif, les scripts de génération, les audits QA et le déploiement statique. Les bases de données, logs, caches, images et secrets ne sont pas versionnés.

## Qualité / sécurité

Avant publication ou déploiement, exécuter les audits disponibles dans `tests/` depuis l'environnement VPS configuré.

## Données

Les données réelles vivent hors dépôt sur le VPS (`/opt/data/data`, `/opt/data/artifacts`) et ne doivent pas être poussées sur GitHub.
