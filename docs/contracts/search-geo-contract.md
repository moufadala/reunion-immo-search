# Contrat recherche naturelle + géographie Réunion

## Objectif

Centraliser les règles métier qui doivent rester identiques entre:

- génération de l'oracle de recherche;
- filtres/alertes sauvegardées;
- intelligence de localisation;
- tests navigateur et QA produit.

Source exécutable: `src/reunion_geo_search_contract.py`.

## Principes

1. Les variantes utilisateur sont générées, pas codées seulement à partir des exemples de Moufadal.
2. Les accents, tirets, pluriels et abréviations `saint/st`, `sainte/ste` sont normalisés.
3. Un quartier exact ne doit pas être remplacé par une commune large sans preuve dans le titre/URL/description/source text.
4. Les alias dangereux restent qualifiés. Exemple: `La Source` seul est interdit; utiliser `La Source Saint-Denis` ou `quartier la source saint denis`.
5. `Beauséjour` doit être interprété comme Sainte-Marie seulement si aucun contexte source ne pointe clairement vers Saint-Paul/Saint-Gilles.
6. `Les Cafés` est un quartier Sainte-Marie reconnu pour les profils privés/dry-run, sans activation live Telegram par ce contrat.

## Familles couvertes

- Communes: Saint-Denis, Sainte-Marie, Sainte-Suzanne, Saint-André, Saint-Paul, Saint-Pierre, Bras-Panon, Saint-Benoît.
- Quartiers/lieux-dits critiques: Rivière des Pluies, Beauséjour, Grande Montée, Duparc, La Convenance, Les Cafés, La Bretagne, Moufia, Bois de Nèfles Sainte-Clotilde, Bois de Nèfles Saint-Paul, etc.
- Contextes négatifs: Beauséjour Saint-Paul/Saint-Gilles, `source:` générique pour éviter les faux `La Source`.

## Contrôle exécutable

- `tests/audit_reunion_geo_search_contract.py` vérifie le contrat pur.
- `scripts/generate_domain_inventory_and_oracle_v2.py` importe ce contrat pour éviter une copie locale divergente.
- Le générateur accepte `--inventory-out` et `--oracle-out`; les audits doivent utiliser des sorties temporaires pour rester réellement read-only.
