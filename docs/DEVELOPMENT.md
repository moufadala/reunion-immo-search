# Socle de développement Python

Le socle local est volontairement séparé des audits opérationnels et du runtime VPS.
Il n'écrit ni dans la base de production, ni dans les artefacts publiés.

## Installation reproductible

Prérequis : Python 3.11 ou 3.12 et `uv`.

```bash
uv sync --frozen --group dev
```

Pour les audits qui pilotent un navigateur :

```bash
uv sync --frozen --group dev --extra browser
uv run playwright install chromium
```

Les binaires Chromium ne sont pas inclus dans `uv.lock` et doivent être installés séparément.
Ils peuvent ajouter plusieurs centaines de Mo, plus les révisions conservées dans le cache
Playwright. Ne lancer cette installation que pour un audit navigateur explicite ; le socle
minimal ne la lance pas et son environnement `.venv` mesuré occupe environ 17,4 Mo sur Windows.

## Tests automatiques sûrs

```bash
uv run pytest
```

Pytest collecte uniquement les fichiers `test_*.py`. Les scripts `tests/audit_*.py`
restent des audits explicites : certains lisent les données réelles, contactent un
service, ouvrent un navigateur ou vérifient des artefacts générés. Ils ne doivent pas
être transformés implicitement en tests unitaires.

Trois fichiers historiques `test_*.py` sont eux aussi des audits runtime malgré leur nom :
`test_mapping_golden.py`, `test_pipeline_invariants.py` et `test_realestate_watch_source_gate.py`.
Ils restent exécutables explicitement sur le VPS configuré :

```bash
python tests/test_mapping_golden.py --golden /chemin/vers/artifacts/golden
python tests/test_pipeline_invariants.py
python tests/test_realestate_watch_source_gate.py
```

## Contrats de données

`src/listing_models.py` définit trois frontières Pydantic v2, toutes versionnées :

- `RawListing` conserve le payload source complet ;
- `NormalizedListing` porte les champs communs après mapping ;
- `PublishedListing` valide l'état et les dates d'historique avant publication.

`src/source_adapter.py` définit seulement les capacités communes `collect()` et
`normalize()`. Les scrapers historiques ne sont pas basculés en bloc : chaque source
peut migrer progressivement, avec comparaison champ par champ. Le contrat public
historique de `src/immo_contracts.py` reste inchangé.

Les descriptions ne sont jamais tronquées par ces modèles. Les galeries conservent
toutes les URL non vides et suppriment seulement les doublons en préservant l'ordre.
