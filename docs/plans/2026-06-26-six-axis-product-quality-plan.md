# Immo Réunion — Plan 6 axes qualité produit

> **Pour Hermes:** utiliser `reunion-immo-active-watch` + `professional-project-delivery`; pour exécution lourde, faire valider les choix structurants par Claude Code puis implémenter axe par axe avec QA publique.

**Date:** 2026-06-26T15:05:31Z  
**Projet:** `/opt/data/projects/reunion-immo-search`  
**Branche observée:** `feat/parallel-hardening-20260626`  
**État git observé:** seul non-suivi attendu `artifacts/HANDOFF_AFTER_WAVE3_20260626.md`  
**URL publique canonique QA:** `https://immo.148.230.103.174.sslip.io/`

## Objectif

Passer d'un portail qui “répond aux exemples connus” à un produit immobilier fiable: chaque résultat affiché doit être justifié par les données sources, les filtres compris doivent être vérifiables, et le pipeline doit empêcher une publication qui dégrade fraîcheur, couverture, sémantique ou affichage.

## Principe directeur

Ne pas multiplier les patches UI. Les 6 axes ci-dessous ajoutent d'abord des contrats d'acceptation et des audits, puis seulement les corrections. Chaque axe doit finir par:

- un test/oracle exécutable;
- une preuve sur `artifacts/app` ou URL publique;
- un rapport court daté;
- un commit atomique si code modifié.

---

## Axe 1 — Renforcer la partie tests: matrice d'acceptation complète

**But:** transformer les tests existants en matrice produit couvrant recherche, localisation, détails, sources, mobile, changements, alertes.

**Constat actuel:** les familles de tests existent déjà: `audit_search_oracle_v2.py`, `audit_user_search_cases.py`, `audit_natural_search_contract.py`, `audit_data_truth_public.py`, `audit_public_deep_user_quality.py`, `audit_source_health.py`, `audit_hard_regression_suite.py`, etc. Le risque n'est pas l'absence de tests, mais leur dispersion et leur capacité à laisser passer une régression visible.

**Travaux:**
1. Créer un manifeste unique `tests/product_quality_matrix.json` listant chaque gate, son rôle, son périmètre, ses prérequis et son niveau bloquant (`P0 bloque publication`, `P1 bloque release`, `P2 rapport`).
2. Ajouter un runner `tests/run_product_quality_matrix.py` qui exécute les gates par famille et sort un JSON + Markdown.
3. Classer les tests existants dans 6 familles:
   - recherche/oracle;
   - vérité sémantique listing;
   - qualité sources/pipeline;
   - affichage public/mobile;
   - géo/photo;
   - ops/dedup/alertes.
4. Ajouter au moins 20 cas metamorphiques par famille search/location: accents, tirets, ordre des mots, `T/F`, budget, meublé/non-meublé, quartiers exacts, zéro résultat.
5. Faire échouer le runner si une page publique annonce une donnée que le JSON ne justifie pas.

**Commandes de validation:**

```bash
cd /opt/data/projects/reunion-immo-search
python3 tests/run_product_quality_matrix.py --app artifacts/app --public https://immo.148.230.103.174.sslip.io/ --json-out artifacts/quality-matrix/latest.json --md-out artifacts/quality-matrix/latest.md
python3 tests/audit_hard_regression_suite.py artifacts/app
```

**Critère d'acceptation:** un seul rapport permet de savoir si le produit est publiable, avec pass/fail par axe et logs exploitables.

---

## Axe 2 — Sémantique: vérité métier des annonces et des requêtes

**But:** une annonce ne doit pas afficher ou filtrer sur une information non prouvée. Les champs `meublé`, chambres, SDB/WC, étage, duplex/RDC, quartier, score doivent avoir une preuve ou rester absents.

**Travaux:**
1. Définir un schéma `semantic_facts` par annonce:
   - `fact`: ex. `non_meuble`, `bedrooms=3`, `district=Moufia`;
   - `confidence`: `haute/moyenne/faible`;
   - `evidence`: extrait source ou champ structuré;
   - `source_field`: titre, description, localisation, source metadata;
   - `display_allowed`: bool.
2. Refactorer l'extraction pour que cartes, modal détail, filtres et chips consomment ce même contrat au lieu de recalculs JS dispersés.
3. Renforcer `tests/audit_listing_semantic_truth.py` pour interdire:
   - badge `Meublé` si `non_meuble` prouvé;
   - quartier exact sans alias présent ou intelligence localisation;
   - SDB/WC/chambres affichés sans evidence;
   - fragments techniques visibles (`serp_view`, query brute, chemins internes).
4. Ajouter un échantillon manuel de 30 annonces à forte valeur pour vérifier description source → facts → rendu.

**Commandes de validation:**

```bash
python3 tests/audit_listing_semantic_truth.py artifacts/app
python3 tests/audit_data_truth_public.py
python3 tests/audit_detail_geo_photo_prudent.py artifacts/app
```

**Critère d'acceptation:** toute information visible importante peut être retracée vers une preuve source; sinon elle est masquée ou marquée `non précisé` hors de la première carte.

---

## Axe 3 — Qualité des données sources: fraîcheur, complétude, couverture

**But:** ne pas confondre “553 annonces affichées” avec “sources fraîches et complètes”. Le pipeline doit mesurer source par source: dernières annonces, erreurs, champs manquants, descriptions courtes, photos, changements.

**Travaux:**
1. Centraliser un rapport `source_quality.json/html` avec par source:
   - active count;
   - latest seen / last checked;
   - taux prix/surface/pièces/ville/photo/description/source_url;
   - nombre descriptions synthétiques/fallback/courtes;
   - erreurs récentes et statut anti-bot.
2. Faire du gate source un bloqueur avant publication si:
   - volume global chute > seuil;
   - source critique chute trop;
   - descriptions vides ou fallback dépassent seuil;
   - source majeure stale sans label clair.
3. Vérifier que l'enrichissement détail source est dans le daily refresh avant rebuild public.
4. Ajouter un rapport “ce qui manque encore par source” plutôt qu'une promesse vague.

**Commandes de validation:**

```bash
/opt/data/scripts/immo_db_freshness_gate.py
python3 tests/audit_source_health.py artifacts/app
python3 tests/audit_description_quality.py artifacts/app
python3 tests/audit_public_delta_guard.py --candidate artifacts/app --target artifacts/app
```

**Critère d'acceptation:** la page publique et le rapport QA disent explicitement quelles sources sont fraîches, dégradées, ou incomplètes.

---

## Axe 4 — Adéquation données ↔ affiché: parité JSON, DOM, public, mobile

**But:** ce que l'utilisateur voit doit correspondre au `listings.json` et aux artefacts dérivés. Pas de compteur faux, pas de carte qui affiche une info absente, pas de filtre/chip qui ment.

**Travaux:**
1. Ajouter un audit DOM public qui compare:
   - compteur visible vs nombre filtré réel;
   - chips visibles vs intent parser;
   - cartes affichées vs IDs `listings.json`;
   - badges visibles vs `semantic_facts`;
   - changements visibles vs `changes.json`.
2. Tester deux états navigateur:
   - fresh state: localStorage/cache clear;
   - dirty state: anciens filtres, masqués, recherche précédente.
3. Ajouter snapshots mobile des parcours clés:
   - homepage sans overflow;
   - recherche naturelle;
   - modal détail;
   - galerie photo;
   - changes page filtres prix/disparu.
4. Vérifier première peinture HTML utile avant JS: pas de `0 résultat` transitoire trompeur.

**Commandes de validation:**

```bash
bash deploy/qa-public.sh
python3 tests/audit_public_deep_user_quality.py
python3 tests/audit_mobile_public.py
python3 tests/audit_public_storage_state.py
python3 tests/audit_changes_decision_public.py
```

**Critère d'acceptation:** une QA navigateur publique prouve console 0 erreur, compteurs corrects, mobile lisible, et parité visible/JSON.

---

## Axe 5 — Qualité produit: pertinence, scoring, déduplication, décision utilisateur

**But:** le portail doit aider à décider, pas seulement lister. Les scores/opportunités/doublons doivent être explicables, prudents, non destructifs.

**Travaux:**
1. Auditer `opportunity_analysis` sur un échantillon:
   - raisons compréhensibles;
   - score cohérent avec prix/m², complétude, fraîcheur, risque;
   - pas de faux “bonne affaire” sur donnée pauvre.
2. Renforcer déduplication:
   - garder tous les originaux;
   - afficher “vu aussi sur” uniquement si score de similarité justifié;
   - signaler les cas ambigus.
3. Améliorer la page changements pour prise de décision:
   - baisses de prix visibles sans fouiller;
   - disparitions/réapparitions séparées;
   - filtres et compteurs testés.
4. Définir un mini benchmark humain hebdomadaire: top 20 opportunités revues manuellement contre source.

**Commandes de validation:**

```bash
python3 tests/audit_opportunity_v2.py artifacts/app
python3 tests/audit_opportunity_dedup_calibration.py artifacts/app
python3 tests/audit_dedup_display.py artifacts/app
python3 tests/audit_changes_page_filters.py
```

**Critère d'acceptation:** chaque score/doublon/changement affiché a une justification et ne supprime jamais une annonce originale.

---

## Axe 6 — Qualité pipeline/opérations: refresh, promotion, rollback, observabilité

**But:** empêcher le daily refresh ou une publication manuelle de casser silencieusement le portail validé.

**Travaux:**
1. Vérifier que `IMMO_STAGE_DB=1 /opt/data/scripts/immo_daily_public_refresh.sh` utilise bien stage DB → gates → promotion DB/app → QA publique.
2. Ajouter un artefact run complet à chaque refresh:
   - `summary.json`;
   - `summary.md`;
   - source quality;
   - quality matrix;
   - publish hash;
   - rollback backup path.
3. Tester le rollback public et DB sur un backup non destructif.
4. Vérifier cron/watchdogs: pas de doublons, silence succès, alertes utiles seulement.
5. Ajouter une règle: aucune promotion publique si la matrice P0 échoue.

**Commandes de validation:**

```bash
IMMO_STAGE_DB=1 /opt/data/scripts/immo_daily_public_refresh.sh
python3 tests/audit_pipeline_script_contracts.py
python3 tests/audit_stage_db_env.py
python3 tests/audit_db_promotion_tools.py
/opt/data/scripts/immo_public_monitor.py
```

**Critère d'acceptation:** un refresh raté ne remplace ni DB ni app publique; un refresh réussi laisse un dossier de preuve complet.

---

## Ordre recommandé d'exécution

1. **Axe 1 d'abord** — unifie les gates; sans ça on continue à avoir des PASS dispersés.
2. **Axe 4 ensuite** — c'est la preuve utilisateur: affiché = données.
3. **Axe 2** — sémantique durable; plus long mais fondamental.
4. **Axe 3** — fraîcheur/complétude sources; nécessaire avant d'élargir alertes et scoring.
5. **Axe 6** — verrouille le pipeline après les corrections.
6. **Axe 5** — améliore la valeur décisionnelle une fois les données fiables.

## Découpage sprint proposé

### Sprint A — 1 journée: matrice QA + parité affichée
- Créer `tests/product_quality_matrix.json`.
- Créer `tests/run_product_quality_matrix.py`.
- Brancher tests existants sans changer le produit.
- Ajouter audit public DOM sur 5 requêtes utilisateur.
- Livrer rapport `artifacts/quality-matrix/latest.md`.

### Sprint B — 1 journée: semantic facts + affichage honnête
- Schéma `semantic_facts`.
- Adapter extraction/JS pour consommer le schéma.
- Audits truth renforcés.
- QA navigateur cartes + modal.

### Sprint C — 1 journée: source quality + pipeline gates
- Rapport source qualité unifié.
- Gate pré-publication source/freshness/description.
- Vérifier daily refresh stage/promotion/rollback.

### Sprint D — 0.5-1 journée: scoring/dedup décisionnel
- Calibration scoring.
- Dedup explications.
- Page changes orientée décision.

## Risques et arbitrages

- **Risque:** trop élargir et refaire tout le portail.  
  **Décision:** ne pas toucher l'UX principale tant qu'un audit démontre un mensonge visible ou une friction P0.

- **Risque:** tests trop nombreux mais pas bloquants.  
  **Décision:** matrice avec niveaux P0/P1/P2, et publication bloquée uniquement par P0.

- **Risque:** source anti-bot stale confondue avec bug produit.  
  **Décision:** labels `stale`, `blocked-antibot`, `needs-hardening` visibles dans source quality; ne pas prétendre complétude.

- **Risque:** scoring perçu comme vérité.  
  **Décision:** score = heuristique de tri, jamais valuation; afficher raisons et confiance.

## Définition de “prêt à publier” après cette phase

Une version est publiable seulement si:

- `run_product_quality_matrix.py` P0 PASS;
- `deploy/qa-public.sh` PASS sur les hosts publics;
- QA navigateur publique: console 0 erreur;
- recherche naturelle: oracle PASS;
- parité DOM/JSON PASS;
- source quality sans chute critique non expliquée;
- rollback backup existant et testé au moins une fois dans le sprint;
- rapport daté écrit sous `artifacts/`.
