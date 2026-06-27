# Public build pipeline contract — Immo Réunion

## Pourquoi

Le deep audit a signalé un risque d'empilement: builder technique, clean portal, polish P0, hardening, slimming, side pages et audits post-build. La chaîne existe déjà dans `scripts/immo_daily_public_refresh.sh`; le contrat ci-dessous la rend explicite et testable.

## Chaîne actuelle observée

1. `src/build_app.py` construit un artefact technique dans `TECH_STAGE`.
2. `scripts/enrich_listing_galleries.py` enrichit les galeries.
3. `scripts/cache_listing_images.py` cache les images.
4. `src/immo_intelligence_layers.py` ajoute intelligence, source health, dédup, opportunités/localisation.
5. `scripts/build_clean_portal_v1.py` construit le portail public propre dans `CLEAN_STAGE`.
6. `scripts/p0_product_polish.py` applique les règles UX produit P0.
7. `scripts/patch_product_hardening_v5.py` ajoute les hardenings durables.
8. `scripts/generate_domain_inventory_and_oracle_v2.py` génère inventaire/oracle.
9. `scripts/slim_public_listings.py` réduit le payload public.
10. `src/listing_changes.py` écrit le journal changements dans le stage.
11. `scripts/enhance_changes_decision_view.py`, `scripts/patch_wave2_lot_c_detail_geo_photo.py`, `scripts/generate_opportunity_calibration.py`, `scripts/generate_ops_cockpit.py`, `src/saved_search_admin.py` ajoutent side pages et payloads.
12. Gates locaux puis `scripts/promote_app_candidate.py` remplacent `artifacts/app`.
13. `deploy/publish-traefik.sh` publie.
14. Public/browser/semantic audits valident.
15. `scripts/generate_build_manifest.py` doit écrire `artifacts/app/build_manifest.json`.

## Contrat cible

- Une seule chaîne officielle: `scripts/immo_daily_public_refresh.sh`.
- Chaque étape écrit dans un stage ou run dir, pas directement en prod avant promotion.
- Chaque étape a un nom stable dans un `.status`.
- Les outputs QA hors publication vont dans `RUN_DIR` ou `/tmp`, pas dans le repo par défaut.
- L'artefact public final inclut `build_manifest.json`.

## Critères d'acceptation

- `tests/audit_pipeline_script_contracts.py` prouve que la chaîne utilise staging + promotion + rollback.
- `tests/audit_build_manifest.py artifacts/app` passe après génération du manifest.
- `git status --short` ne change pas lorsqu'on exécute les audits read-only avec leurs défauts.
