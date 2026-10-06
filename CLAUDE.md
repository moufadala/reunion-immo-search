# CLAUDE.md — reunion-immo-search

## Entrée obligatoire

Lis `AGENTS.md` en entier avant de toucher au dépôt. Il contient les 5 pièges qui ont chacun déjà coûté au moins une journée, notamment : namespace hôte/conteneur, runtime `/opt/data/scripts/` différent du dépôt, et danger de remplacer brutalement `artifacts/app`.

## Branche et état canonique

- Branche de travail actuelle : `stabilize/immo-single-chain`.
- HEAD vérifié le 2026-10-06 : `4de624002c7430c8d1d469988de12604eba8c8e5`.
- `main` est périmé pour ce chantier : au 2026-10-06, `origin/main..HEAD = 173` commits. Ne repars pas de `main` sans arbitrage explicite.

## Sur le PC de Moufadal

Ce dépôt porte le code, les tests et la documentation, pas l'état runtime complet du VPS.

- Setup recommandé : `uv sync --frozen --group dev`, puis `uv run pytest`.
- Les contrôles qui lisent `/opt/data/data/reunion_watch.db`, `/opt/data/artifacts/**`, `/opt/data/scripts/**` ou le conteneur de publication sont VPS-only.
- Un échec PC de type fichier absent sur `/opt/data/...` ne prouve pas une panne prod : c'est souvent un contrôle non applicable hors VPS.

## État métier à lire hors dépôt

Le dépôt ne suffit pas pour reprendre le projet. Lis dans le vault Obsidian :

1. `wiki/fraicheur-du-produit-public-immo.md`
2. `wiki/immo-public-monitor-30m.md`
3. `00-LifeOS/Handoff 2026-10-06 - immo dormant, reprise par session PC.md`

Résumé 2026-10-06 : code figé au 29/08, DB promue ponctuellement le 05/09, dernières publications/rapports le 14/09, crons immo principaux en pause depuis mi-septembre. La prochaine action n'est pas une promotion : c'est l'audit terrain des sources en chute, en commençant par `immo974`.

## Règle de preuve

Toute vérification de mapping, scraping ou ingestion doit produire un comptage `produits / total` sur données réelles. Un test vert sans comptage réel ne prouve rien.
