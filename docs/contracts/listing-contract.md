# Contrat canonique Listing — Immo Réunion

## Objectif

Stabiliser une source de vérité entre les champs techniques issus des scrapers/DB et les champs publics rendus dans `artifacts/app/listings.json`.

Le contrat évite les doubles conventions implicites telles que `source_site` vs `source`, `rent_eur` vs `price`, `surface_m2` vs `surface`, `property_type` vs `type`.

## Niveaux

### TechnicalListing

Entrée interne/scraper. Champs acceptés et alias historiques:

- `id`: identifiant stable si disponible.
- `source_site` ou `source`: source portail/agence normalisée.
- `source_id`: identifiant source si disponible.
- `url` ou `source_url`: URL annonce source.
- `rent_eur` ou `price`: loyer/prix en euros.
- `surface_m2` ou `surface`: surface en m².
- `property_type` ou `type`: type logement.
- `commune`/`city`, `district`, `location`, `region`.
- `rooms`, `bedrooms`, `furnished`.
- `image_url`, `local_image_url`, `image_urls`, `local_image_urls`.
- `display_canonical`: booléen de display non destructif.
- `canonical_display_id`: identifiant canonique si duplicate fort.

### PublicListing

Sortie stable pour le portail:

- `id`
- `source`
- `source_id`
- `url`
- `price`
- `surface`
- `type`
- `city`
- `district`
- `location`
- `region`
- `rooms`
- `bedrooms`
- `furnished`
- `image_url`
- `local_image_url`
- `image_urls`
- `local_image_urls`
- `display_canonical`
- `canonical_display_id`

## Règles de mapping

1. Les champs publics privilégient le nom public, puis l'alias technique.
2. `source = source or source_site`.
3. `price = price or rent_eur`, converti en nombre si possible.
4. `surface = surface or surface_m2`, converti en nombre si possible.
5. `type = type or property_type`.
6. `url = url or source_url`.
7. `city = city or commune`.
8. `display_canonical` vaut `True` par défaut; `False` est préservé.
9. `local_image_urls` et `image_urls` sont toujours des listes, même si un seul champ primaire existe.
10. La fonction de mapping est pure: pas de DB, pas de réseau, pas d'écriture fichier.

## Exemples d'acceptation

### Exemple 1 — ligne technique complète

Input:

```json
{
  "id": "zimo:1",
  "source_site": "zimo",
  "source_id": "1",
  "source_url": "https://example.test/1",
  "rent_eur": "900",
  "surface_m2": "65.5",
  "property_type": "Appartement",
  "commune": "Saint-Denis"
}
```

Attendu public:

```json
{
  "id": "zimo:1",
  "source": "zimo",
  "source_id": "1",
  "url": "https://example.test/1",
  "price": 900,
  "surface": 65.5,
  "type": "Appartement",
  "city": "Saint-Denis",
  "display_canonical": true
}
```

### Exemple 2 — doublon fort non canonique

Input:

```json
{
  "id": "seloger:2",
  "source": "seloger",
  "url": "https://example.test/2",
  "price": 910,
  "surface": 66,
  "type": "Appartement",
  "display_canonical": false,
  "canonical_display_id": "zimo:1"
}
```

Attendu:

- La ligne reste présente dans JSON public.
- `display_canonical` reste `false`.
- Le portail peut la masquer du grid par défaut, mais les preuves/source restent disponibles.

### Exemple 3 — photos primaires et galeries

Input avec seulement `local_image_url`:

```json
{
  "id": "locamoi:3",
  "source_site": "locamoi",
  "url": "https://example.test/3",
  "local_image_url": "thumbs/a.webp",
  "image_url": "https://img.example/a.jpg"
}
```

Attendu:

- `local_image_urls = ["thumbs/a.webp"]`
- `image_urls = ["https://img.example/a.jpg"]`

## Contrôle exécutable

Le fichier `tests/test_listing_contracts.py` vérifie ces exemples contre `src/immo_contracts.py`.
