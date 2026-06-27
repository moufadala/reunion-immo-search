# Contrat Qualité Sémantique & Sources — Lot C

> **Fichier:** `docs/quality/semantic_source_quality_contract.md`
> **Script gate:** `tests/audit_semantic_source_quality_matrix.py`
> **Date:** 2026-06-26

## 1. Objet

Ce contrat définit les critères minimums que `listings.json` doit satisfaire
pour que les champs affichables dans l'UI soient considérés comme **justifiés
par les données sources**. Il consolide la qualité sémantique (descriptions,
statuts, fragments techniques) et la qualité source (couverture, distribution,
fraîcheur).

## 2. Gates P0 (bloquent la publication)

| Gate | Seuil | Raison |
|---|---|---|
| **listings.json existe** | — | Pas d'artefact → pas de produit |
| **listings.json valide JSON** | — | Parse échoué → zéro listings |
| **Listings count** | > 0 | 0 annonces ne peut pas être publié |
| **source_url (`url`)** | >= 50% | Sans URL source, l'utilisateur ne peut pas accéder à l'annonce originale |
| **Descriptions vides** | == 0 | Une annonce sans description est invalide |

## 3. Gates P1 / Report (non-bloquants)

| Check | Rôle |
|---|---|
| **Source distribution** | Rapport des sources et de leur poids |
| **Price coverage** | Mesure la complétude des prix (attendue ~100%) |
| **Surface coverage** | Mesure la complétude des surfaces |
| **City coverage** | Mesure la complétude des villes |
| **Bedrooms / Rooms coverage** | Mesure la complétude du nb de pièces |
| **Description_status distribution** | Vérifie `Description source` vs `Synthétique` vs fallback |
| **Fallback descriptions** | Compte les descriptions générées, pas sources |
| **Court descriptions** | Alert sur descriptions < 30 caractères |
| **Boilerplate** | Détecte textes génériques (favoris, publiée le, proposée par…) |
| **Fragments techniques** | Détecte `serp_view`, `raw_query`, chemins internes dans titres/descriptions |
| **Fragments HTML** | `<`, `>` non échappés dans les descriptions |
| **Photos — image_url** | Image principale présente |
| **Photos — local_image_url** | Image locale en cache |
| **Photos — galerie (multi)** | Listings avec > 1 photo |
| **Photos — gallery_status** | Distribution `missing` / `single` / `multi` |
| **Location intelligence** | Distribution des confiances et qualités de localisation |
| **Map point** | Présence de `map_point` avec coordonnées |
| **Geo quality levels** | Niveaux `haute` / `commune` / `faible` |
| **Opportunity analysis** | Couverture et distribution des labels/score |
| **Technical fragments** | Aucun pattern technique dans titres + descriptions |

## 4. Champs critiques par source de preuve

| Champ UI attendu | Champ(s) source dans listings.json |
|---|---|
| Prix | `price` |
| Surface | `surface` |
| Ville | `city` (ou `location_intelligence.commune_inferred`) |
| Quartier | `district` (ou `location_intelligence.district_best`) |
| Type de bien | `type` |
| Nb pièces | `rooms` |
| Nb chambres | `bedrooms` |
| Meublé / Non meublé | `furnished` (validé par `description_analysis.property_state.furnished`) |
| Description | `description` (status `description_status` doit être `"Description source"`) |
| Lien source | `url` |
| Photos | `image_url` (principale), `local_image_urls[]` (galerie) |
| Carte | `map_point` (lat/lon/zoom) |
| Score opportunité | `opportunity_analysis.score` |
| Label opportunité | `opportunity_analysis.label` |

## 5. Anomalies définies

Les anomalies sont classées par sévérité (nombre de raisons cumulées):

1. **Missing source_url** — `url` absent ou vide
2. **Empty / very short description** — description absente ou < 30 chars
3. **No price** — `price` manquant
4. **No city** — `city` manquant
5. **No surface** — `surface` manquant
6. **No image** — `image_url` absent
7. **Gallery missing** — `gallery_status == "missing"`
8. **Low location confidence** — `location_intelligence.confidence < 0.5`
9. **Furnished without evidence** — `furnished` défini mais `description_analysis.property_state.furnished` absent

## 6. Utilisation

```bash
# Exécution standard read-only: rapports dans /tmp/immo-qa-*.
python3 tests/audit_semantic_source_quality_matrix.py --app artifacts/app

# Rapport durable explicite: opt-in seulement, idéalement dans un run-dir daté.
RUN_DIR=/tmp/immo-qa-semantic-source-$(date -u +%Y%m%dT%H%M%SZ)
python3 tests/audit_semantic_source_quality_matrix.py \
    --app artifacts/app \
    --json-out "$RUN_DIR/semantic_source_quality.json" \
    --md-out "$RUN_DIR/semantic_source_quality.md"

# Avec seuil personnalisé
python3 tests/audit_semantic_source_quality_matrix.py \
    --app artifacts/app \
    --max-empty-descriptions 2
```

**Exit code:** `0` = pass (pas de P0), `1` = fail (un ou plusieurs P0 détectés).

**Contrat anti-side-effect:** la matrice produit ne doit pas écrire par défaut dans des artefacts suivis par Git. Les sorties persistantes sont autorisées uniquement via `--json-out` / `--md-out` explicites.