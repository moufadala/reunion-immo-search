# Immo RUN — Master plan pour une version propre, fonctionnelle et maintenable

Date UTC: 2026-06-25T16:23:16Z

## 0. Verdict sans complaisance

L'UI publique est maintenant revenue vers une V1 propre: recherche, filtres classiques, cartes, photos, analyse, source. Les ajouts visibles prématurés ont été retirés (`Recherches rapides famille`, `Copier recherche`).

La meilleure prochaine étape n'est PAS d'ajouter des boutons, cartes, comptes utilisateur, nouvelle source ou carte géographique.

Le vrai risque est ailleurs: pipeline de données, robustesse des imports, déduplication, garde-fous de volume, rollback, observabilité, et définition exacte de ce que veut dire “annonce fiable”.

Claude Code a confirmé le point critique: SeLoger représente 270/587 annonces, soit environ 46% du catalogue. Si cette source casse, le portail peut devenir amputé. Il faut donc des guards et une stratégie de dégradation avant d'empiler des features.

## 1. État actuel vérifié

### Public

URL canonique:

- https://immo.148.230.103.174.sslip.io/

Alias:

- https://immo.srv1723523.hstgr.cloud/

UI visible actuelle:

- recherche texte;
- filtres ville / budget max / type / surface min / pièces min / tri;
- chips région simples;
- cartes photo-first;
- boutons Source, Analyse, Masquer, Favori;
- pages secondaires: veille, sources, doublons, opportunités, localisation, alertes.

Retiré:

- bloc `Recherches rapides famille`;
- bouton `Copier recherche`;
- handlers JS associés visibles.

Vérification navigateur publique après retrait:

```json
{
  "title": "Recherche immo RUN — portail propre",
  "hasCopy": false,
  "hasPresets": false,
  "buttons": ["Rechercher", "Réinitialiser"]
}
```

### Données publiques actuelles

Depuis `artifacts/app/listings.json`:

- total annonces: 587;
- photos principales locales: 581/587;
- galeries multi-photo locales: 208;
- descriptions source: 586/587;
- description fallback: 1/587;
- `description_analysis`: 587/587;
- `opportunity_analysis`: 587/587.

Répartition sources:

- SeLoger: 270;
- Zimo: 78;
- Bien'ici: 47;
- OFIM: 28;
- OFIM RSS: 26;
- Domimmo: 24;
- Citya: 24;
- Locamoi: 21;
- FNAIM: 20;
- Superimmo: 15;
- Immo974: 15;
- Alter: 10;
- 97immo: 9.

Répartition villes principales:

- Saint-Denis: 178;
- Saint-Pierre: 113;
- Saint-Paul: 65;
- Le Tampon: 56;
- Saint-Leu: 25;
- Non précisée: 23;
- Sainte-Marie: 17.

### Pipeline actuel

Script principal:

- `/opt/data/scripts/immo_daily_public_refresh.sh`

Chaîne actuelle:

1. `realestate_watch.py --refresh` vers `/opt/data/data/reunion_watch.db`;
2. collecte CDP SeLoger via `/opt/data/scripts/seloger_multi_page.py`;
3. import SeLoger via `src/import_seloger_multipage.py`;
4. backup DB avant enrichissement détails;
5. enrichissement détails source via `scripts/enrich_source_details_v3.py`;
6. audit DB enrichment;
7. build app technique en stage;
8. audit source health;
9. enrichissement galeries;
10. seed/cache photos;
11. intelligence layers;
12. build clean portal;
13. gates locaux;
14. swap `artifacts/app` avec backup;
15. audits post-swap;
16. publication Traefik;
17. QA publique.

Gates actuels:

- `audit_clean_portal.py`;
- `audit_description_quality.py`;
- `audit_public_quality_budget.py`;
- `audit_public_storage_state.py`;
- `audit_public_perf_index.py`;
- `audit_public_seo.py`;
- `audit_public_mobile_smoke.py`;
- `deploy/qa-public.sh`.

Crons liés:

- `scraper-quick-gate-before-run-watch` à 16:20;
- `RUN Watch quotidien immo+vols async launcher` à 16:30;
- `immo-saved-search-alerts-after-run-watch` à 16:45;
- `immo-source-health-alerts-6h` toutes les 6h;
- `RUN Watch quotidien completion notifier` toutes les 5 min.

## 2. Contrat d'acceptation final

Une version vraiment “propre et fonctionnelle” doit satisfaire les critères suivants.

### UX

- Homepage simple, aucun bloc de debug, aucun raccourci personnel prématuré.
- Premier écran compréhensible en moins de 5 secondes sur mobile.
- Recherche texte + filtres classiques suffisent pour explorer.
- Les actions secondaires restent discrètes: Source, Analyse, Favori, Masquer.
- Les pages opérationnelles restent séparées: sources, doublons, veille, alertes, opportunités, localisation.

### Données

- Aucune annonce publiée sans prix ou titre utile, sauf explicitement labellisée incomplète.
- 100% des annonces ont une description non vide.
- 100% des annonces ont au moins une image locale OU un fallback visuel honnête.
- Les descriptions synthétiques sont comptées séparément et ne sont jamais présentées comme source.
- Les villes “Non précisée” sont suivies comme dette de qualité.
- Les doublons inter-sources sont identifiés sans supprimer l'original.

### Pipeline

- Un build incomplet ne peut pas remplacer la production.
- Chute catalogue > 15% vs build précédent = rejet automatique sauf override explicite.
- Chute source critique, notamment SeLoger < seuil défini, = rejet ou mode dégradé documenté.
- Rollback `artifacts/app` possible en moins de 2 minutes.
- DB mutation à risque précédée d'un backup et rollback testé.
- Chaque daily produit un run dir exploitable avec statuts, logs, métriques et verdict.

### Ops

- Échec daily notifié à Moufadal.
- Succès silencieux ou résumé court seulement si utile.
- Source health doit distinguer: source cassée, source vide, source stale, source OK.
- GitHub contient le code propre exporté, sans secrets, et pas `/opt/data` entier.

## 3. Ce qu'il ne faut PAS faire maintenant

1. Ne pas ajouter de nouvelles sources tant que les 12 existantes ne sont pas maîtrisées.
2. Ne pas ajouter compte utilisateur, backend dynamique, login ou DB web.
3. Ne pas ajouter carte interactive générale: géocodage trop fragile pour l'instant.
4. Ne pas complexifier la homepage avec alertes, presets, cockpit ou métriques techniques.
5. Ne pas migrer vers Postgres maintenant: le problème est la qualité pipeline/produit, pas SQLite.
6. Ne pas rendre les heuristiques “opportunité” dominantes: utile en tri, pas vérité marché.
7. Ne pas créer d'alertes Telegram bruyantes sans bootstrap anti-spam vérifié.

## 4. Priorités P0 — rendre la publication défensive

### P0.1 — Guard de volume global avant swap

Objectif: empêcher un build amputé de remplacer la prod.

Fichiers:

- modifier: `/opt/data/scripts/immo_daily_public_refresh.sh`;
- créer: `/opt/data/projects/reunion-immo-search/tests/audit_public_delta_guard.py`;
- artefacts: `RUN_DIR/delta_guard.json`.

Règle:

- comparer `CLEAN_STAGE/listings.json` à `artifacts/app/listings.json` courant;
- rejeter si total chute de plus de 15%;
- rejeter si photos locales chutent de plus de 15%;
- rejeter si descriptions source chutent sous 580;
- autoriser un override explicite via env `IMMO_ALLOW_LARGE_DELTA=1`, loggé.

Vérification:

```bash
python3 tests/audit_public_delta_guard.py --current artifacts/app --candidate /tmp/stage
```

Critère d'acceptation:

- test fixture avec candidate -20% échoue;
- test fixture avec candidate stable passe;
- daily appelle ce gate avant `swap_clean_app`.

### P0.2 — Guard par source critique

Objectif: éviter que SeLoger ou une source majeure tombe à zéro sans bloquer.

Fichiers:

- créer: `tests/audit_source_delta_guard.py`;
- modifier: daily avant swap.

Seuils initiaux proposés:

- SeLoger: minimum 200 annonces ou max baisse 25% vs précédent;
- total sources actives: minimum 8 sources;
- `Non précisée`: alerte si > 10% du catalogue.

Critère d'acceptation:

- si SeLoger = 0 dans candidate, build rejeté;
- si une petite source tombe à 0 mais total stable, warning non bloquant.

### P0.3 — Déduplication auditée, non destructive

Objectif: savoir combien de doublons existent et ne pas mentir sur le nombre réel d'opportunités.

Fichiers:

- vérifier/renforcer: `src/immo_intelligence_layers.py`;
- vérifier/renforcer: `artifacts/app/dedup_groups.json`;
- créer: `tests/audit_dedup_quality.py`.

Règles:

- groupe probable si même ville/secteur + prix proche + surface proche + titre/type similaire;
- ne jamais supprimer les annonces originales;
- exposer `canonical_candidate_id`, `duplicate_score`, `source_urls`;
- la homepage peut garder les annonces séparées au début, mais la page doublons doit être claire.

Critère d'acceptation:

- audit affiche: nombre groupes, nombre annonces groupées, top 20 groupes suspects;
- zéro groupe avec sources perdues;
- chaque annonce du groupe conserve URL source.

### P0.4 — Rollback testé, pas seulement théorique

Objectif: prouver que rollback prod fonctionne.

Fichiers:

- créer: `tests/audit_rollback_restore.py` ou script `scripts/test_app_swap_rollback.sh`.

Approche:

- faire un test sur copies temporaires, pas sur prod;
- simuler swap, échec gate, restore backup;
- vérifier hash ou sentinel file.

Critère d'acceptation:

```bash
bash scripts/test_app_swap_rollback.sh
# ROLLBACK_TEST_PASS
```

## 5. Priorités P1 — qualité data et ops

### P1.1 — Résoudre la dernière description fallback

État: `domimmo:266245107626917`, 147 caractères, statut synthèse.

Objectif:

- soit récupérer une vraie description source;
- soit labelliser définitivement comme source indisponible avec raison.

Critère d'acceptation:

- 587/587 descriptions source si possible;
- sinon 586/587 source + 1 exception documentée dans `coverage.json`.

### P1.2 — Résoudre les 6 photos principales manquantes ou clarifier fallback

État: 581/587 photos principales locales.

Objectif:

- identifier les 6 annonces sans image locale;
- retenter cache;
- si source sans image, badge fallback propre.

Critère d'acceptation:

- rapport `missing_images.json` avec source/id/url/raison;
- aucune image cassée dans navigateur.

### P1.3 — Observabilité daily claire

Objectif: un humain doit pouvoir comprendre le run sans ouvrir 20 fichiers.

Fichiers:

- créer: `scripts/immo_run_summary.py`;
- produire: `RUN_DIR/summary.json` + `RUN_DIR/summary.md`.

Résumé attendu:

- total annonces;
- delta vs précédent;
- sources en hausse/baisse;
- descriptions/fallback;
- photos/galleries;
- top warnings;
- URL publique;
- résultat final: published / blocked / rolled back.

Critère d'acceptation:

- chaque run daily a un `summary.md` lisible;
- le notifier Telegram peut envoyer uniquement les warnings.

### P1.4 — Saved alerts: anti-spam et cohérence avec UI

État: cron `immo_saved_search_alerts.sh` existe.

Objectif:

- vérifier qu'il n'envoie pas des liens vers des filtres supprimés ou confus;
- bootstrap silencieux confirmé;
- alertes uniquement nouvelles correspondances.

Critère d'acceptation:

```bash
python3 src/search_alerts.py --dry-run
```

- affiche saved searches + top matches;
- ne modifie pas l'état en dry-run;
- URL générée ouvre des résultats corrects dans le navigateur public.

### P1.5 — Git propre source-of-truth

État: repo propre exporté sous `/opt/data/exports/reunion-immo-search-clean`, car `/opt/data` est un énorme repo/root dangereux.

Objectif:

- documenter officiellement que le GitHub synchronise uniquement l'export propre;
- script unique `scripts/export_clean_repo.py` ou `deploy/sync-clean-export.sh`;
- ne jamais commit `/opt/data` entier.

Critère d'acceptation:

- un nouveau contributeur peut faire: modifier projet → export propre → scan secrets → commit.

## 6. Priorités P2 — produit polish après robustesse

### P2.1 — Performance progressive réelle

État: `listings_index.json` existe et pèse environ 237 KB vs 2.4 MB pour `listings.json`.

Objectif:

- homepage charge `listings_index.json` d'abord;
- détail complet chargé à l'ouverture d'une annonce ou via un second payload.

Attention: ne pas faire avant P0. C'est utile mais pas le risque principal.

Critère d'acceptation:

- premier affichage mobile sans charger 2.4 MB;
- modale Analyse garde toutes les descriptions/galeries;
- tests navigateur sur recherche + modal.

### P2.2 — Page “sources” utile pour Moufadal

Objectif:

- transformer `sources.html` en vraie santé opérationnelle, pas seulement chiffres;
- indiquer dernière collecte, source bloquée, baisse suspecte, action recommandée.

Critère d'acceptation:

- en 10 secondes, Moufadal sait quelle source est cassée et si le portail est fiable.

### P2.3 — Page “doublons” orientée décision

Objectif:

- afficher les groupes probables avec toutes les sources;
- permettre de comparer prix/surface/photo/source;
- ne pas supprimer automatiquement.

Critère d'acceptation:

- top doublons compréhensibles;
- aucune URL source perdue.

### P2.4 — UX mobile polish strictement minimal

Objectif:

- améliorer lisibilité mobile sans ajouter de features.

Tests:

- pas d'overflow horizontal;
- header utilisable à une main;
- cartes lisibles;
- modal photo navigable.

## 7. Plan d'exécution recommandé

### Phase A — 1 session courte: fermeture UI + sauvegarde

Déjà fait pour cette session:

- supprimer `Recherches rapides famille`;
- supprimer `Copier recherche`;
- QA publique;
- push GitHub.

Reste:

- créer un rapport unique consolidé de ces retraits si besoin.

### Phase B — P0 data guards

1. Créer fixtures `tests/fixtures/delta_guard/` avec current/candidate mini JSON.
2. Écrire `tests/audit_public_delta_guard.py`.
3. Vérifier échec sur -20% total.
4. Vérifier passage sur delta stable.
5. Brancher dans daily avant `swap_clean_app`.
6. Relancer syntaxe daily.
7. Faire run stage non destructif.
8. Publier seulement si gate passe.
9. Commit export propre.

### Phase C — P0 source guards

1. Écrire audit source delta.
2. Ajouter seuils configurables dans `config/public_quality_thresholds.json`.
3. Rejeter SeLoger 0 / source critique chute forte.
4. Générer `source_delta_report.json`.
5. Brancher daily.
6. QA fixtures.
7. Commit.

### Phase D — P0 dedup audit

1. Inspecter `dedup_groups.json` actuel.
2. Écrire `tests/audit_dedup_quality.py`.
3. Vérifier conservation des URLs source.
4. Produire métriques dans `coverage.json` ou `dedup_summary.json`.
5. Améliorer page doublons si nécessaire.
6. QA publique.
7. Commit.

### Phase E — P1 observabilité daily

1. Créer `scripts/immo_run_summary.py`.
2. Faire consommer `RUN_DIR/*.status`, `artifacts/app/*.json`.
3. Produire `summary.json` et `summary.md`.
4. Modifier notifier pour envoyer seulement erreurs/warnings importants.
5. Tester avec un run dir fixture.
6. Commit.

### Phase F — P1 détails restants

1. Lister les 1 description fallback et 6 photos manquantes.
2. Tenter récupération ciblée.
3. Ajouter exceptions documentées si source réellement vide.
4. QA navigateur image fallback.
5. Commit.

### Phase G — P2 performance progressive

À faire seulement après P0/P1:

1. Séparer payload index vs détails.
2. Charger index au boot.
3. Charger détails à la modale.
4. Garder fallback si fetch détail échoue.
5. QA mobile/perf.
6. Commit.

## 8. Commandes de vérification standard après chaque phase

Depuis `/opt/data/projects/reunion-immo-search`:

```bash
python3 -m py_compile scripts/build_clean_portal_v1.py src/*.py tests/*.py
IMMO_APP_PATH=artifacts/app python3 tests/audit_clean_portal.py
python3 tests/audit_description_quality.py artifacts/app
python3 tests/audit_public_quality_budget.py artifacts/app
python3 tests/audit_public_storage_state.py artifacts/app
python3 tests/audit_public_perf_index.py artifacts/app
python3 tests/audit_public_seo.py artifacts/app
python3 tests/audit_public_mobile_smoke.py
bash -n /opt/data/scripts/immo_daily_public_refresh.sh
bash deploy/qa-public.sh
```

Browser QA minimale:

```js
(() => ({
  title: document.title,
  hasCopy: document.body.innerText.includes('Copier recherche') || !!document.querySelector('#copySearchBtn'),
  hasPresets: document.body.innerText.includes('Recherches rapides famille') || !!document.querySelector('[data-preset]'),
  buttons: [...document.querySelectorAll('header button')].map(b => b.textContent.trim())
}))()
```

Résultat attendu:

```json
{"hasCopy": false, "hasPresets": false, "buttons": ["Rechercher", "Réinitialiser"]}
```

## 9. Décision de priorisation

Ordre recommandé:

1. P0.1 delta guard global;
2. P0.2 guard source critique;
3. P0.3 dedup audit;
4. P0.4 rollback testé;
5. P1.3 observabilité daily;
6. P1.1/P1.2 détails restants;
7. P1.4 alertes saved-search;
8. P2 performance progressive;
9. P2 pages sources/doublons polish.

Pourquoi cet ordre:

- il protège la production avant de l'améliorer;
- il réduit le risque SeLoger;
- il rend les chiffres honnêtes;
- il évite de réintroduire le fouillis UI;
- il crée des preuves testables à chaque étape.

## 10. Résumé exécutif

Le portail est utilisable, propre visuellement, et bien publié. Mais il ne faut pas confondre “UI propre” avec “produit robuste”.

Le reste du travail doit maintenant être traité comme une chaîne de production de données:

- empêcher les mauvais builds;
- savoir quand une source casse;
- maîtriser les doublons;
- expliquer la qualité des données;
- garder l'interface simple.

La prochaine tâche concrète recommandée est P0.1: `audit_public_delta_guard.py` branché avant swap.
