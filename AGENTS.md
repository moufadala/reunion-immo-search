# À lire avant de toucher à ce dépôt

Cinq pièges ont chacun coûté au moins une journée. Ils ne sont pas théoriques : ils ont tous frappé.

## 1. `/opt/data` n'est pas le même répertoire selon d'où on regarde

Dans le CONTENEUR Hermes, `/opt/data` EST le bind mount de `/opt/hermes/data` côté hôte. Sur l'HÔTE, `/opt/data` est un AUTRE répertoire, bien réel, qui contient de vieilles données.

Mesuré le 2026-08-03 :
- hôte `/opt/data/data/reunion_watch.db` : 368 annonces, inode différent, root:root ;
- hôte `/opt/hermes/data/data/reunion_watch.db` : 2 868 annonces dans la vraie base.

Une commande lancée du mauvais côté n'échoue pas : elle réussit sur les mauvaises données.

Depuis l'hôte, utiliser `/opt/hermes/data/...`. Depuis le conteneur Hermes, utiliser `/opt/data/...`. Avant de conclure, identifier le namespace réel.

Le contrôle `python3 tests/audit_pipeline_script_contracts.py --runtime-check` ne se lance QUE dans le conteneur Hermes, depuis `/opt/data/projects/reunion-immo-search`. Depuis l'hôte, `/opt/data` est un leurre : un résultat "runtime missing" n'est pas une divergence à réparer mais un contrôle non applicable.

## 2. Le code exécuté n'est pas forcément le code de ce dépôt

Des scripts du cœur de la chaîne existent sous `/opt/data/scripts/` et certains sont absents de ce dépôt ou peuvent diverger sous le même nom.

Avant de corriger un script, vérifier QUEL fichier est réellement appelé par cron, par le wrapper quotidien et par le conteneur. Un correctif poussé ici peut ne jamais atteindre la production.

## 3. Ne jamais remplacer brutalement le répertoire `artifacts/app`

`artifacts/app` est monté dans le conteneur `immo-dashboard`. Remplacer le répertoire peut orpheliner le bind mount : le conteneur peut continuer à servir l'ancien inode ou du vide même si les fichiers semblent corrects côté hôte.

La règle durable : vider/copier en préservant l'inode quand c'est possible, puis vérifier ce que le CONTENEUR voit réellement. Ne jamais rejouer une promotion isolée sans la publication et le postflight conteneur final.

## 4. Quand on retire un producteur, chercher TOUS ses exemplaires

Trois fois en trois jours : un producteur retiré, son jumeau oublié (`clean_stage_gate` ⇄ `promote_app_candidate`, sidecars HTML ⇄ sidecars JSON, `ops_cockpit_stage` ⇄ `ops_cockpit`).

Toujours chercher le nom du fichier produit dans TOUT le dépôt et dans `/opt/data/scripts/` avant de déclarer qu'un producteur est supprimé.

## 5. Un test vert ne prouve rien sans comptage sur données réelles

Le 2026-07-30, trois tests verts couvraient un pipeline qui produisait 0 annonce sur 395.

Toute vérification d'un mapping ou d'une ingestion doit produire un comptage `produits / total` sur un lot réel. Une porte de qualité doit être la DERNIÈRE chose du run : le 03/08, une QA certifiait « 0 fichier legacy » avant que des étapes ultérieures n'écrivent encore dans `artifacts/app`.
