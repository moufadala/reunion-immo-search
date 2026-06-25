# Immo RUN — hardening, optimisation et QA parallèle

Date UTC: 2026-06-25T15:05:59Z

## Résultat court

Passe non destructive terminée avec publication locale/montée active vérifiée côté public.

- Public mobile smoke: PASS sur `https://immo.148.230.103.174.sslip.io/` et `https://immo.srv1723523.hstgr.cloud/`.
- Portail courant: 587 annonces, 581 photos principales locales, 208 galeries multi-photo, 587/587 opportunités.
- Storage corrompu testé en navigateur réel: le portail charge 120 cartes, pas d'erreur visible, pas de fuite `/opt/data`.
- Stage DB / clean-stage dry-run: PASS avec DB temporaire et cache photos seedé.
- Fuite technique `/opt/data` retirée des pages/payloads publics `source_health` et `changes`.
- `photo_cache` optimisé: seed depuis cache courant + mode `PHOTO_FETCH_MISSING=0` pour QA rapide sans réseau long.

## Changements principaux

### Daily refresh `/opt/data/scripts/immo_daily_public_refresh.sh`

- Utilise des chemins explicites `--db`, `--out`, `--app` pour les étapes compatibles stage.
- Garde le rollback DB autour de `source_detail_enrichment`.
- Garde l'auto-restore `artifacts/app` si un gate échoue après swap.
- Ajoute `seed_photo_cache` avant `photo_cache` pour éviter de retélécharger tous les thumbs dans le stage.
- Ajoute les gates après swap avant publish:
  - `audit_public_quality_budget.py`
  - `audit_public_storage_state.py`

### Stage DB / chemins paramétrables

- `src/build_app.py`: support `--db` et `--out/--app`, meta publique ne divulgue plus le chemin serveur.
- `scripts/enrich_listing_galleries.py`: support `--db` et `--app`.
- `tests/audit_db_enrichment.py`: support `--db`.
- `tests/audit_filters_v3.py`: support `--db` et `--app`.

### Qualité publique / sécurité

- `src/listing_changes.py`: chemins thumbs `/thumbs/...` normalisés en `thumbs/...`; meta DB publique réduite au nom du fichier.
- `src/source_health.py`: `latest_smoke_summary` public sanitizé (`run_id`, `generated_at`, counts) au lieu de chemins/commandes serveur.
- `scripts/build_clean_portal_v1.py`: parsing `localStorage` / `sessionStorage` protégé par `safeJsonArray`.
- `scripts/cache_listing_images.py`: `PHOTO_FETCH_MISSING=0` pour tests rapides sans réseau; daily réel garde fetch actif.

### Nouveaux audits

- `tests/audit_public_quality_budget.py`
  - pages/fichiers requis présents;
  - budget `index.html` et `listings.json`;
  - pas de `Traceback`, `/opt/data`, tokens sensibles évidents;
  - URLs dangereuses et images locales manquantes détectées.
- `tests/audit_public_storage_state.py`
  - storage favoris/session robuste;
  - pas d'ancien état persistant `immoHiddenIds`;
  - reset présent;
  - normalisation recherche présente.
- `tests/audit_public_mobile_smoke.py`
  - étendu: vérifie pages annexes + JSON publics sur les deux hosts.

## Preuves QA exécutées

### QA locale/prod artifact

- `CLEAN_PORTAL_AUDIT PASS`
- `DESCRIPTION_QUALITY_AUDIT PASS`
  - 587 listings
  - 586 descriptions source
  - 1 fallback
  - 0 vide
- `DB_ENRICHMENT_AUDIT PASS`
  - active rows: 626
  - city coverage: 96.3%
  - duplicate groups: 35
- `SOURCE_HEALTH_AUDIT PASS`
- `AUDIT_FILTERS_V3 AUDIT_OK`
- `PUBLIC_QUALITY_BUDGET_AUDIT PASS`
- `PUBLIC_STORAGE_STATE_AUDIT PASS`

### QA publique mobile

- `PUBLIC_MOBILE_SMOKE_AUDIT PASS`
- Hosts vérifiés:
  - `https://immo.148.230.103.174.sslip.io/`
  - `https://immo.srv1723523.hstgr.cloud/`
- Pages/JSON vérifiés HTTP 200:
  - `index.html`, `listings.json`, `veille.html`, `sources.html`, `doublons.html`, `opportunites.html`, `localisation.html`, `alertes.html`, `changes.json`, `source_health.json`, `dedup_groups.json`, `opportunity.json`, `locations.json`, `coverage.json`.

### Smoke navigateur réel

Après injection volontaire de JSON corrompu dans:

- `localStorage.immo_clean_favs`
- `sessionStorage.immo_clean_hidden_session`

Résultat navigateur:

```json
{"title":"Recherche immo RUN — portail propre","cards":120,"errorsVisible":false,"resultText":"120 affichés sur 587 correspondances"}
```

### Simulation stage clean accélérée

DB copiée en `/tmp`, clean-stage temporaire, cache thumbnails seedé, réseau manquant désactivé:

```json
{"clean_stage_fastqa": true, "listings": 587, "local_primary": 581, "multi_gallery": 208, "opportunity": 587}
```

## Recherche / standards retenus

Sources consultées:

- Schema.org `RealEstateListing`: type de page de listing immobilier; utile mais encore zone “new”.
- Google Search Central structured data: JSON-LD recommandé, données visibles sur la page, validation Rich Results Test.
- web.dev Core Web Vitals: LCP <= 2.5s, INP <= 200ms, CLS <= 0.1, mesurés au 75e percentile mobile/desktop.
- web.dev performance budgets: budget explicite sur poids, requêtes, images/scripts et métriques utilisateur.

## Backlog priorisé

### P0/P1 — Sécurité livraison

1. Créer un vrai repo/export propre hors racine `/opt/data` avant tout commit/push.
   - Racine Git actuelle confirmée: `/opt/data`, trop large.
   - Ne pas committer depuis cette racine.
2. Ajouter un script d'export allowlist:
   - `src/`, `scripts/`, `tests/`, `deploy/`, docs utiles;
   - exclure DB, logs, artifacts volumineux, secrets, caches.
3. Secret scan obligatoire avant GitHub.

### P1 — Daily durable

1. Lancer un daily complet surveillé à un créneau choisi pour valider `seed_photo_cache` en conditions réelles.
2. Ajouter un watchdog de durée `photo_cache` avec résumé `cached/fetched/failed`.
3. Faire échouer le daily si le public artifact contient `/opt/data` ou `Traceback`.

### P2 — Produit/UX

1. Recherche famille: presets lisibles (`Nord <=1200`, `T3+`, `2 chambres`, etc.) et liens partageables.
2. Alertes sauvegardées côté serveur ou Telegram avec unsubscribe clair.
3. Page détail dédiée par annonce ou modal URL-addressable pour SEO/partage.
4. JSON-LD prudent: commencer par `ItemList`/`WebPage` visible, puis tester `RealEstateListing` sur pages détail si créées.

### P2 — Performance

1. Mesurer Lighthouse/Chrome réel mobile; objectifs: LCP <=2.5s, INP <=200ms, CLS <=0.1.
2. Garder `index.html` léger; éviter de ré-embarquer des JSON trop gros.
3. Évaluer split `listings.json` par défaut + chargement progressif détails/galeries.

### P3 — Qualité données

1. Résoudre le dernier fallback description (`domimmo:266245107626917`) si possible.
2. Réduire `Région non précisée` / city missing: 23 lignes.
3. Surveiller sources fraîches mais faible couverture photos/descriptions.

## Limites restantes

- Daily complet non lancé dans cette passe pour éviter mutation/scraping inutile en journée.
- `photo_cache` réseau complet peut dépasser 10 minutes si beaucoup d'URLs distantes non cache; mitigation ajoutée par seed cache, mais à valider sur prochain daily complet.
- Inline event handlers restent présents comme warnings, tolérés pour fallback image actuel; à refactorer plus tard vers listeners JS si priorité sécurité stricte.
- Git/export propre reste le plus gros risque avant partage GitHub.
