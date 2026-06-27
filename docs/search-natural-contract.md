# Contrat d’acceptation — Recherche naturelle Immo RUN

Date: 2026-06-26
Produit: portail immo Réunion public — `https://immo.148.230.103.174.sslip.io/`

## Objectif utilisateur

Permettre à Moufadal de taper une recherche naturelle proche d’un moteur immo moderne: quartier, commune, typologie, budget, meublé/non meublé, équipements, sans apprendre une syntaxe stricte.

Le champ de recherche ne doit pas seulement chercher du texte: il doit produire une intention structurée, afficher les critères compris, expliquer les zéros, et filtrer les annonces sans faux positifs silencieux.

## Source de vérité

Le pipeline doit respecter cette chaîne:

1. texte utilisateur → parser → intention structurée;
2. intention structurée → chips + ligne “critères compris”;
3. intention structurée → filtrage/ranking;
4. zéros → explication de combinaison, pas message trompeur;
5. public QA sur URL publiée.

Pas de logique parallèle différente entre champ, chips, ligne de critères et résultats.

## Familles obligatoires

### Quartiers / zones

Doivent être reconnus avec accents, sans accents et variantes usuelles:

- `rivière des pluies`, `riviere des pluies`, `rivières des pluies`
- `beauséjour`, `beausejour`
- `grande montée`, `grande montee`, `la grande montée`
- `duparc`
- `bretagne`, `la bretagne`

Règle: si le quartier exact n’existe pas dans les données, ne pas élargir silencieusement à toute la commune. Afficher zéro avec explication claire.

### Typologie / pièces

Doivent être synonymes fonctionnels:

- `T4`, `F4`, `T 4`, `F 4`, `4 pièces`, `4p`
- idem pour T1/F1 à T5/F5 quand pertinent
- `studio` = Studio/T1

### Meublé

- `meublé` → inclut les biens explicitement meublés
- `non meublé`, `pas meublé`, `sans meublé`, `location nue`, `vide` → excluent les biens explicitement meublés
- `non meublé` ne doit jamais générer un chip positif `Meublé`

### Budget

- `moins 900`, `moins de 900`, `max 900`, `<900`, `<=900` → `≤ 900 €`
- `plus 900`, `plus de 900`, `min 900`, `>900`, `>=900` → `≥ 900 €`
- `= 900`, `900 exact` → égalité avec tolérance faible
- `autour de 900`, `environ 900` → fourchette autour du prix
- `entre 800 et 1000` → intervalle
- `900` seul → ambigu, doit afficher les boutons `≤`, `≥`, `=`, `autour`, sans imposer `≤` par défaut

### Combinaisons métier prioritaires

- `F4 non meublé rivière des pluies moins de 900`
  - attendu: comprend tous les critères; si zéro, explique la combinaison et donne le détail isolé; ne dit pas faussement que le quartier n’existe pas.
- `T5 meublé rivière des pluies autour de 1700`
  - attendu: retrouve l’annonce connue si elle existe encore.
- `T4 grande montée`
  - attendu: retrouve l’annonce T4/4 pièces connue si elle existe encore.
- `beauséjour non meublé moins de 1000`
  - attendu: retourne les annonces cohérentes, pas toute la base.

## Ce qui ne doit pas arriver

- Retourner toute la base quand un quartier est reconnu mais sans match.
- Interpréter `F4` différemment de `T4`.
- Interpréter `non meublé` comme `meublé`.
- Interpréter `900` seul comme `≤900` sans choix explicite.
- Dire “aucun résultat exact pour le quartier” quand le quartier existe mais que la combinaison des critères est trop restrictive.
- Avoir des chips, une ligne critères et des résultats désynchronisés.

## Gates exécutables

- `python3 tests/audit_search_oracle_v2.py`
- `python3 tests/audit_user_search_cases.py`
- `python3 tests/audit_changes_page_filters.py`
- `python3 tests/audit_changes_decision_public.py`
- `bash deploy/qa-public.sh`

Une livraison n’est acceptable que si ces gates passent sur l’URL publique.

## Limites assumées

`Duparc` et `Bretagne` peuvent retourner zéro tant que les données sources ne contiennent pas de biens explicitement localisés dans ces quartiers. Le produit doit alors proposer un élargissement explicite à la commune dans une étape ultérieure, pas élargir automatiquement.
