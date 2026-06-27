# Product Watch V1 — Immo Réunion

Date UTC: 2026-06-26
Projet: `/opt/data/projects/reunion-immo-search`
Mode: conservateur — pas d’activation Telegram live ni publication visible sans validation Moufadal.

## 1. Objectif

Construire un outil de veille immobilière Réunion fiable, d’abord sur la location Nord/Nord-Est, avec une option d’alerte Telegram.

Le produit doit servir deux usages distincts :

1. **Veille pro générique / vendable**
   - Utilisable pour un client sans les critères privés de Moufadal.
   - Orientée données, source health, historique, analyse, opportunités, risques.
   - Exportable en JSON/HTML sanitized.

2. **Profils privés Moufadal**
   - Recherches personnalisées pour Moufadal/famille/mère.
   - Critères privés et préférences de tri.
   - Alertes Telegram d’abord en dry-run, puis live uniquement après validation.

## 2. Périmètre V1

### Marché

- Location uniquement.

### Zones prioritaires

- Sainte-Marie.
- Sainte-Suzanne.
- Saint-Denis.
- Quartiers/lieux-dits privés prioritaires:
  - Duparc.
  - Les Cafés.
  - Rivière des Pluies.
  - La Bretagne.
  - Moufia pour la famille.

### Hors périmètre V1

- Cockpit complet avec authentification.
- Paiement/SaaS.
- Multi-client configurable via interface web.
- Activation Telegram live sans dry-run validé.
- Publication de critères privés dans un artefact public non protégé.
- Migration backend lourde si SQLite + app statique suffisent.

## 3. Séparation produit pro vs privé

### Couche pro vendable

Artefacts proposés :

- `artifacts/app/pro_watch.json`
- `artifacts/app/pro_watch.html`
- `tests/audit_pro_watch.py`

Contenu autorisé :

- résumé des sources ;
- fraîcheur et statut des sources ;
- annonces nouvelles/actives pertinentes ;
- changements prix/statut ;
- opportunités avec raisons ;
- risques et données manquantes ;
- méthodologie ;
- couverture géographique générique.

Contenu interdit :

- mention de `Moufadal`, `mère`, `maman`, `famille` comme critère client ;
- budgets/personas privés ;
- chemins internes `/opt/data/...` ;
- état Telegram brut ;
- secrets/tokens.

### Couche privée

Artefacts/configs proposés :

- `config/private_profiles.example.json` — exemple commit-safe.
- `config/private_profiles.local.json` — critères réels privés, à garder hors public si sensible.
- `config/saved_searches.json` — moteur existant à adapter ou à faire pointer vers profils.
- `artifacts/app/saved_searches_admin.json/html` — uniquement si sanitized.

Règles :

- chaque profil a `scope: private` ;
- chaque alerte explique pourquoi l’annonce matche ;
- chaque fallback est visible, pas silencieux ;
- les meublés sont acceptés en fallback mais labellisés ;
- les annonces 2 chambres famille sont affichées en fallback mais labellisées ;
- les étages > 1 pour la mère sont signalés sauf ascenseur.

## 4. Profils privés V1

### Profil mère — `mother_sainte_marie_rental`

Critères :

- Location.
- Appartement ou maison.
- Surface minimum: 60 m².
- Zones prioritaires:
  1. Duparc.
  2. Les Cafés.
  3. Rivière des Pluies.
  4. La Bretagne.
- Loyer: 850–1000 €.
- Minimum 1 chambre.
- Étage:
  - RDC ou 1er étage OK.
  - Au-dessus seulement si ascenseur.
  - Au-dessus sans ascenseur: afficher mais signaler comme défavorable si le reste matche fortement.
- Non meublé préféré.
- Meublé accepté en fallback, avec mention claire.

### Profil famille — `moufadal_family_rental`

Critères :

- Location.
- Maison ou appartement.
- Surface minimum: 115 m².
- Zones:
  - Duparc.
  - Les Cafés.
  - Rivière des Pluies.
  - La Bretagne.
  - Moufia.
- Loyer: 900–1400 €.
- 3 chambres minimum préféré.
- 2 chambres à afficher en fallback, clairement marqué.
- Non meublé préféré.
- Jardin préféré.

## 5. Alertes Telegram

### Politique d’événements

Si une annonce est dans les critères, Moufadal veut tout voir :

- nouveau bien ;
- baisse de prix ;
- réapparition ;
- annonce enrichie utile ;
- changement significatif.

### Anti-spam obligatoire

- Premier baseline silencieux.
- Dry-run d’abord.
- Un seul chemin d’envoi Telegram live.
- Déduplication par profil + annonce canonique + type événement.
- Message compact, avec raisons et warnings.
- Aucun live sans validation explicite.

## 6. Contrat de matching

Une annonce doit être classée en :

- `strict_match` — remplit les critères principaux.
- `fallback_match` — proche mais avec écart accepté explicitement.
- `reject` — hors critères.
- `needs_review` — données manquantes ou contradictoires.

Exemples fallback :

- mère: meublé alors que non meublé préféré ;
- mère: étage > 1 mais ascenseur indiqué ;
- famille: 2 chambres au lieu de 3 ;
- famille: jardin absent/inconnu mais autres critères forts.

## 7. QA gates à ajouter

- `pro_watch_contract` — pas de données privées dans veille pro.
- `private_profiles_contract` — profils parseables, critères explicités.
- `private_profile_matching` — strict/fallback/reject vérifié sur fixtures.
- `les_cafes_location_alias` — Les Cafés reconnu ou explicitement non couvert tant que non validé.
- `alerts_dry_run_no_spam` — dry-run sans émission live et baseline silencieux.
- `alert_trigger_single_path` — un seul chemin peut envoyer Telegram live.

## 8. Décisions ouvertes

- Valider l’orthographe et la commune exacte de `Les Cafés` dans les données Réunion/localisation.
- Décider si `Beauséjour` reste dans un profil privé ou sort du besoin actuel.
- Décider si les critères privés réels doivent rester uniquement dans `private_profiles.local.json` gitignored.

## 9. Prochaine phase

Lot 1 continue avec :

1. audit de séparation actuelle ;
2. statut de reconnaissance des quartiers ;
3. proposition de structure config privée ;
4. aucun changement d’alerte live.
