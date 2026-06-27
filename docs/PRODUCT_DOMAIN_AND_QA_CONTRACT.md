# Contrat produit & QA — Immo Réunion

Date: 2026-06-26
Statut: contrat de méthode vivant, à transformer en tests exécutables.

## Changement de posture

Moufadal ne doit pas lister tous les cas. Il donne le métier, les irritants et les exemples représentatifs. Hermes doit inférer les familles complètes de comportements, écrire les critères d'acceptation, créer les tests, corriger puis vérifier sur l'URL publique.

Les exemples utilisateur ne sont jamais traités comme une liste exhaustive. Chaque exemple déclenche une famille de cas.

## Objectif utilisateur

Permettre à un utilisateur non technique de trouver rapidement les bonnes annonces de location à La Réunion, avec une recherche proche d'un assistant immobilier:

- langage naturel français;
- quartiers, communes, zones et variantes locales;
- budget ambigu ou explicite;
- type de bien, pièces, chambres, surface;
- meublé / non meublé;
- équipements utiles famille;
- suivi de changements: nouvelles annonces, disparues, baisses/hausses, réapparues;
- données affichées fiables par rapport aux sources collectées.

## Règles de méthode non négociables

1. Avant tout changement significatif, créer ou mettre à jour les cas d'acceptation.
2. Pour chaque bug donné par Moufadal, générer au moins 5 cas voisins.
3. Les cas doivent couvrir: positif, négatif, ambigu, accent/typo, ordre des mots, zéro résultat, mobile.
4. L'UI visible, les chips, la ligne "critères compris", les compteurs et les cartes doivent dériver du même intent.
5. Une publication n'est valide que si les audits publics passent.
6. Les données affichées doivent être comparées à `listings.json`, `changes.json` ou `history.sqlite` selon la page.
7. Quand le système ne sait pas, il doit le dire clairement plutôt qu'élargir silencieusement.

## Familles de cas à couvrir par défaut

### Recherche naturelle

- Variantes accents: `beauséjour`, `beausejour`.
- Variantes pluriel/singulier: `rivière des pluies`, `rivieres des pluies`.
- Abréviations immo: `T4`, `F4`, `T 4`, `F 4`, `4 pièces`.
- Type bien: `maison`, `villa`, `appartement`, `appart`, `studio`.
- Ordre des mots variable: `F4 non meublé Beauséjour moins 900`, `Beauséjour F4 moins 900 non meublé`.
- Requête partielle: `900`, `F4`, `Beauséjour`.
- Zéro résultat exact: quartier reconnu mais absent des données.

### Budget

- Nombre seul: demander une précision visible `≤`, `≥`, `=`, `autour`.
- Moins que: `moins 900`, `<900`, `max 900`, `jusqu'à 900`.
- Plus que: `plus 900`, `>900`, `min 900`.
- Égal: `=900`, `900 exact`.
- Fourchette: `entre 800 et 1000`.
- Autour: `autour de 900`, tolérance explicitée.
- Ne pas interpréter un nombre de pièces ou surface comme budget si le contexte indique autre chose.

### Localisation

- Quartiers prioritaires: Rivière des Pluies, Bretagne, Duparc, Beauséjour, Grande Montée.
- Variantes sans accents et tirets.
- Ne pas élargir automatiquement un quartier à toute la commune sans indication visible.
- Si élargissement proposé: action utilisateur explicite, pas automatique.

### Meublé / non meublé

- `meublé` inclut uniquement les annonces explicitement meublées.
- `non meublé`, `pas meublé`, `sans meublé`, `location nue` excluent les annonces explicitement meublées.
- Si l'information est inconnue, comportement à définir explicitement: inclure comme inconnu ou exclure.

### Changements

- Page changements doit afficher séparément: baisse, hausse, disparue, réapparue.
- Les compteurs visibles doivent correspondre à `changes.json`.
- Les cartes baisse/hausse doivent montrer ancien prix → nouveau prix + delta.
- Les disparues ne doivent pas masquer les baisses.

### Vérité des données

Pour chaque carte et détail:

- prix affiché = `listings.json.price` ou valeur événement dans `changes.json`;
- localisation = champs source enrichis;
- surface/pièces/chambres = données collectées, pas hallucination;
- source/url/source_id présents;
- image locale existante si affichée;
- détails modale cohérents avec la carte.

### Mobile

- Recherche utilisable au pouce.
- Les boutons de précision budget visibles et tapables.
- Les cartes et modales ne doivent pas masquer les actions essentielles.
- Pas de dépendance au hover.

## Artefacts attendus

- `tests/acceptance_user_search_cases.json` ou équivalent.
- `tests/audit_public_deep_user_quality.py` pour navigateur public.
- `tests/audit_changes_page_filters.py` pour lifecycle.
- `tests/audit_data_truth.py` pour carte/détail/source.
- Rapport de QA daté sous `artifacts/qa-reports/` après gros lot.

## Contrat de livraison

Une réponse "c'est corrigé" doit inclure:

- URL publique;
- commandes QA exécutées;
- résultat brut PASS/FAIL;
- ce qui reste non couvert;
- décision produit si un comportement est ambigu.

## Décision méthodologique

La prochaine étape prioritaire n'est pas d'ajouter encore des micro-correctifs. C'est de transformer ce contrat en oracle de tests complet puis de faire passer l'app contre cet oracle.
