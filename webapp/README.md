# webapp — interface de la veille locative Nord + Est

Remplace la page qui était **accrétée par vagues de patchs** (`patch_wave2_lot_c_*.py`
et consorts injectaient du CSS/JS dans un `index.html` généré). Désormais :
le pipeline produit des **données** (`artifacts/app/feed.json`), cette app les **lit**.

> **Ne jamais revenir** à des scripts qui réécrivent le HTML servi. C'était la cause
> racine du « ça part dans tous les sens » (8 pages, doublons de fichiers).

## Construire et déployer

```bash
docker exec -u 10000 hermes-gateway bash -lc "
  cd /opt/data/projects/reunion-immo-search/webapp &&
  npm ci && npm run build &&
  rm -rf ../artifacts/app/v2/* && cp -r dist/* ../artifacts/app/v2/ &&
  ln -sf ../feed.json ../artifacts/app/v2/feed.json"
```

## Régénérer les données

```bash
cd /opt/data/projects/reunion-immo-search
python3 scripts/detail_enrich.py --all --delay 4 --exclude seloger,zimo,superimmo
python3 scripts/detail_enrich.py --filter-agencies   # neutralise les adresses d'agences
python3 scripts/export_feed.py
```

`detail_enrich.py --reparse` ré-extrait depuis les pages **déjà en cache**
(`artifacts/detail_pages/`) — permet d'ajouter des critères sans redemander
quoi que ce soit aux portails.

## Où sont les critères de recherche

Un seul endroit : **`scripts/profils.py`**. L'interface ne redéfinit rien, elle
affiche le score calculé côté serveur. Pour changer un budget, une zone ou une
pondération, c'est là — pas dans le JSX.

## Pièges déjà payés, à ne pas repayer

- **Tailwind v4 : `@theme` à l'intérieur d'un `@media` ne fonctionne pas.** Les valeurs
  sont émises inconditionnellement et gagnent par ordre de source → la page restait
  sombre en permanence. Les surcharges de thème doivent être de simples propriétés
  CSS sur `:root`.
- **Tailwind ne voit que les classes écrites en toutes lettres.** `text-${couleur}` est
  supprimé au build → table de correspondance littérale obligatoire.
- **Pas de `:hover` pour une information utile.** La cible est un smartphone Android :
  les critères secondaires vivent dans une bande qui défile au doigt.
- **Une apparition au scroll doit avoir un filet.** Dans un onglet en arrière-plan,
  Chrome gèle les transitions et l'IntersectionObserver ne se déclenche pas : sans
  garde-fou (1,2 s), la page reste blanche.
- **Les photos zimo sont mortes à la source** (URL signée → placeholder de 266 o,
  original sur leur S3 → 404). Seules les vignettes déjà téléchargées dans
  `artifacts/app/thumbs/` survivent → `export_feed.py` les préfère à l'URL distante.
- **Chemins d'images en ABSOLU** (`/thumbs/...`) : l'app est servie depuis `/v2/`
  aujourd'hui et depuis `/` demain, un chemin relatif casserait à la bascule.
