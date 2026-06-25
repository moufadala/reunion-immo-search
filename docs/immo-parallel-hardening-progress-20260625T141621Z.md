# Immo Réunion — Parallel hardening progress

Date UTC: 2026-06-25
Scope: pipeline daily, detail enrichment, SeLoger import, QA public.

## Objectif

Exécuter en parallèle les axes décidés: planification, affinage, code review, optimisation. Priorité donnée au hardening non destructif plutôt qu'à une nouvelle UI.

## Changements appliqués

### Lane 1 — Domimmo/Keldom

Fichier modifié:

- `scripts/enrich_source_details_v3.py`

Changement:

- le fetcher Keldom accepte maintenant plusieurs schémas JSON:
  - liste directe;
  - objet unique;
  - `{items: [...]}`;
  - `{data: [...]}`.

Fichiers de test ajoutés:

- `tests/test_enrich_source_details_v3.py` — tests pytest futurs;
- `tests/audit_enrich_source_details_v3.py` — runner autonome sans dépendance pytest.

Cas couverts:

- ID exact interrogé en premier;
- arrêt après match exact;
- schémas JSON alternatifs;
- faux positif rejeté par score;
- footer légal Domimmo coupé;
- remplacement d'un texte boilerplate par texte propre.

### Lane 2 — SeLoger import

Fichier modifié:

- `src/import_seloger_multipage.py`

Changement:

- ajout de `mark_seloger_inactive_not_seen()`;
- petit volume: requête paramétrée existante;
- gros volume: table temporaire `tmp_seloger_seen_ids` pour éviter les limites SQLite et préserver la sémantique globale du `NOT IN`.

Fichier de test ajouté:

- `tests/audit_import_seloger_multipage.py`

Cas couvert:

- chemin gros volume forcé avec `batch_size=3`;
- seuls les SeLoger non vus deviennent inactifs;
- les autres sources ne sont pas touchées.

### Lane 3 — Daily rollback DB minimal

Fichier modifié:

- `/opt/data/scripts/immo_daily_public_refresh.sh`

Changement:

- backup SQLite via `sqlite3.Connection.backup()` juste avant `source_detail_enrichment`;
- `trap EXIT` qui restaure ce backup si un gate aval échoue;
- `ENRICHMENT_DB_KEEP=1` uniquement après `public_qa` réussi;
- gate qualité descriptions maintenu avant swap et après swap.

Raison:

- stage DB complet serait plus propre à long terme mais trop invasif maintenant, car plusieurs scripts ont la DB principale en dur.
- rollback minimal protège déjà contre la persistance d'un mauvais enrichissement source-detail si les gates échouent.

## Vérifications exécutées

```bash
python3 -m py_compile scripts/enrich_source_details_v3.py src/import_seloger_multipage.py tests/audit_enrich_source_details_v3.py tests/audit_import_seloger_multipage.py tests/audit_description_quality.py
bash -n /opt/data/scripts/immo_daily_public_refresh.sh
python3 tests/audit_enrich_source_details_v3.py
python3 tests/audit_import_seloger_multipage.py
python3 tests/audit_description_quality.py artifacts/app
python3 tests/audit_clean_portal.py
python3 tests/audit_db_enrichment.py
python3 tests/audit_source_health.py
bash deploy/qa-public.sh
```

Résultats observés:

- `ENRICH_SOURCE_DETAILS_V3_AUDIT PASS`
- `IMPORT_SELOGER_MULTIPAGE_AUDIT PASS`
- `DESCRIPTION_QUALITY_AUDIT PASS`
- `CLEAN_PORTAL_AUDIT PASS`
- `DB_ENRICHMENT_AUDIT PASS`
- `SOURCE_HEALTH_AUDIT PASS`
- `QA_CLEAN_LAYERS_OK` sur:
  - `immo.srv1723523.hstgr.cloud`
  - `immo.148.230.103.174.sslip.io`

Backup SQLite non destructif vérifié:

- DB principale: `PRAGMA integrity_check = ok`, `1266` lignes `rental_listings`.
- Copie backup temporaire: `PRAGMA integrity_check = ok`, `1266` lignes `rental_listings`.

## État public après vérification

- `587` annonces.
- `581` photos principales locales.
- `208` galeries multi-photos locales.
- `587/587` annonces avec opportunité.
- `586` descriptions source.
- `1` fallback restant: `domimmo:266245107626917`.
- QA publique: PASS sur les deux domaines.

## Limites restantes

1. Rollback DB protège la partie `source_detail_enrichment`, mais ne restaure pas automatiquement `artifacts/app` si un échec arrive après le swap local et avant/pendant publish. Il existe déjà un backup app, mais pas d'auto-restore app.
2. Stage DB complet reste l'architecture cible long terme.
3. `pytest` n'est pas installé globalement; un runner autonome a donc été ajouté pour ne pas dépendre d'une installation système.
4. Dernier fallback Domimmo non résolu; gain marginal.

## Prochaines actions recommandées

1. Ajouter auto-restore `artifacts/app` si échec après `swap_clean_app`.
2. Rendre la DB configurable via `IMMO_DB_PATH` dans les scripts de build/audit pour permettre stage DB complet.
3. Automatiser un smoke test navigateur mobile léger.
4. Garder le dernier fallback Domimmo en P2 seulement.
