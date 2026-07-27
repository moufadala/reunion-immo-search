# -*- coding: utf-8 -*-
"""Gazetteer quartiers/communes, SCOPE STRICT aux 4 communes Nord+Est du projet
(Saint-Denis, Sainte-Marie, Sainte-Suzanne, Saint-André).

Fusionne deux sources historiques qui ne se recouvraient pas :
- src/immo_intelligence_layers.py (DISTRICTS) : riche en alias pour Saint-Denis/
  Sainte-Marie (dont des repères comme "universite du moufia"), mais ZERO
  entree pour Sainte-Suzanne et Saint-André.
- scripts/scope/purge_nord_est.py (QUARTIERS) : couvre les 4 communes mais
  seulement des tokens bruts, sans alias ni repères.

Volontairement SANS coordonnees ici : ce module sert a ameliorer le LIBELLE
(quartier affiche), pas la carte (qui vient de listing_detail / geocodage
separé). Volontairement sans alias generiques courts ("fac", "campus"...) :
un alias trop court fait des faux positifs des qu'il apparait comme
sous-chaine dans un autre mot une fois les accents/tirets normalises.
"""
from __future__ import annotations

import re
import unicodedata


def norm(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFD', str(s)).encode('ascii', 'ignore').decode()
    s = s.lower().replace(chr(39), ' ')
    s = re.sub(r'\bst\b', 'saint', s)
    s = re.sub(r'\bste\b', 'sainte', s)
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


# commune -> {libelle affiche: [alias en texte libre, le plus specifique en tete]}
QUARTIERS = {
    'Saint-Denis': {
        'La Bretagne': ['la bretagne', 'bretagne'],
        'Bois de Nèfles': ['bois de nèfles sainte clotilde', 'bois de nefles sainte clotilde',
                            'bois de nèfles ste clotilde', 'bois de nefles ste clotilde',
                            'sainte clotilde bois de nèfles', 'sainte clotilde bois de nefles',
                            'quartier bois de nèfles', 'quartier bois de nefles',
                            'bois de nèfles', 'bois de nefles'],
        'Sainte-Clotilde': ['sainte clotilde', 'ste clotilde', 'sainte coltilde', 'ste coltilde'],
        'Moufia': ['universite du moufia', 'université du moufia', 'campus du moufia', 'moufia'],
        'Domenjod': ['domenjod'],
        'Primat': ['primat'],
        'Le Chaudron': ['le chaudron', 'chaudron'],
        'Montgaillard': ['montgaillard'],
        'Bellepierre': ['bellepierre'],
        'Champ Fleuri': ['champ fleuri', 'champ-fleuri'],
        'Les Camélias': ['les camelias', 'camelia', 'camélias', 'camelias'],
        'La Source': ['quartier la source', 'secteur la source'],
        'Providence': ['providence'],
        'Bas de la Rivière': ['bas de la rivière', 'bas de la riviere'],
        'La Montagne': ['la montagne'],
        'Le Brûlé': ['le brule', 'le brûlé', 'brule', 'brûlé'],
        'Foucherolles': ['foucherolles'],
        'Vauban': ['vauban'],
        'Le Barachois': ['barachois', 'le barachois'],
    },
    'Sainte-Marie': {
        'Duparc': ['duparc', 'du parc', 'centre commercial duparc', 'cc duparc'],
        'Les Cafés': ['les cafés', 'les cafes', 'quartier les cafés', 'quartier les cafes'],
        'La Grande Montée': ['la grande montée', 'grande montée', 'la grande montee', 'grande montee'],
        # generique deliberement ecarte des alias : "beau séjour" apparait dans
        # des descriptions comme "beau séjour" (salon) et faisait de faux
        # positifs Sainte-Marie sur des annonces d'ailleurs sur l'ile.
        'Beauséjour': ['quartier beauséjour', 'quartier beausejour', 'beauséjour sainte-marie',
                       'beausejour sainte-marie', 'beauséjour', 'beausejour'],
        'La Convenance': ['la convenance', 'convenance'],
        'La Ressource': ['la ressource'],
        'Ravine des Chèvres': ['ravine des chèvres', 'ravine des chevres'],
        'Gillot': ['gillot', 'aeroport gillot', 'aéroport gillot'],
        'Rivière des Pluies': ['rivière des pluies', 'riviere des pluies', 'rivières des pluies',
                               'rivieres des pluies'],
        'Bois Rouge': ['bois rouge'],
        'Grand Prado': ['grand prado'],
    },
    'Sainte-Suzanne': {
        'Bagatelle': ['bagatelle'],
        'Quartier Français': ['quartier français', 'quartier francais'],
        'Commune Carron': ['commune carron'],
        'Deux-Rives': ['deux rives', 'deux-rives'],
        'Village Desprez': ['village desprez'],
        'Le Bocage': ['bocage', 'le bocage'],
    },
    'Saint-André': {
        'Cambuston': ['cambuston'],
        'Champ-Borne': ['champ borne', 'champ-borne'],
        'Petit-Bazar': ['petit bazar', 'petit-bazar'],
        'Rivière du Mât': ['rivière du mât', 'riviere du mat', 'rivière du mat'],
        'La Cressonnière': ['la cressonnière', 'la cressonniere', 'cressonnière', 'cressonniere'],
        'Bras des Chevrettes': ['bras des chevrettes'],
        'Ravine Creuse': ['ravine creuse'],
        'Mille Roches': ['mille roches'],
        'Le Colosse': ['le colosse', 'colosse'],
    },
}

# code postal -> commune (seulement les codes verifies)
CP_COMMUNE = {
    '97400': 'Saint-Denis', '97490': 'Saint-Denis', '97417': 'Saint-Denis', '97495': 'Saint-Denis',
    '97438': 'Sainte-Marie',
    '97441': 'Sainte-Suzanne',
    '97440': 'Saint-André',
}

# alias commune elle-meme (variantes st/ste, sans accent...) -> nom canonique
_COMMUNE_ALIASES = {
    'Saint-Denis': ['saint denis', 'st denis', 'saint-denis'],
    'Sainte-Marie': ['sainte marie', 'ste marie', 'sainte-marie'],
    'Sainte-Suzanne': ['sainte suzanne', 'ste suzanne', 'sainte-suzanne'],
    'Saint-André': ['saint andre', 'st andre', 'saint-andre', 'saint-andré'],
}

TARGET_COMMUNES = list(QUARTIERS.keys())

# index alias normalise -> (commune, libelle), le plus long alias en premier
# pour qu'un alias plus specifique gagne sur un alias plus court inclus dedans.
_QUARTIER_INDEX = []
for _commune, _quartiers in QUARTIERS.items():
    for _label, _aliases in _quartiers.items():
        for _a in [_label] + _aliases:
            _n = norm(_a)
            if _n:
                _QUARTIER_INDEX.append((_n, _commune, _label))
_QUARTIER_INDEX.sort(key=lambda x: len(x[0]), reverse=True)

_COMMUNE_INDEX = []
for _commune, _aliases in _COMMUNE_ALIASES.items():
    for _a in [_commune] + _aliases:
        _COMMUNE_INDEX.append((norm(_a), _commune))
_COMMUNE_INDEX.sort(key=lambda x: len(x[0]), reverse=True)

# quartier normalise -> commune proprietaire (pour deduire la commune a partir
# d'un champ "district"/"zone" qui contient deja un nom de quartier precis)
_QUARTIER_TO_COMMUNE = {n: c for n, c, _ in _QUARTIER_INDEX}


def infer_commune(*fields):
    """Cherche un signal de commune NON AMBIGU dans les champs fournis
    (city brut, district brut, zone_normalized, titre, description...).
    Rend None si rien de fiable n'est trouve -- jamais de devinette.

    Volontairement PAS de repli sur les noms de quartier ici : un quartier
    peut avoir un homonyme hors perimetre (ex. "Beauséjour" existe aussi a
    Saint-Paul). Deviner la commune a partir d'un seul nom de quartier a
    deja produit un faux positif teste sur les vraies donnees (alter,
    meme code postal 97435 que le cas confirme Saint-Paul) -- retire."""
    blob = norm(' '.join(str(f) for f in fields if f))
    if not blob:
        return None
    for alias_n, commune in _COMMUNE_INDEX:
        if alias_n and alias_n in blob:
            return commune
    for code, commune in CP_COMMUNE.items():
        if code in blob:
            return commune
    return None


def infer_quartier(commune, *text_fields):
    """Cherche un quartier precis DANS la commune deja etablie. Si la commune
    est inconnue, ne devine rien (evite les homonymes inter-communes comme
    Beauséjour, qui existe aussi hors perimetre)."""
    if not commune or commune not in QUARTIERS:
        return None
    blob = norm(' '.join(str(f) for f in text_fields if f))
    if not blob:
        return None
    for alias_n, com, label in _QUARTIER_INDEX:
        if com == commune and alias_n in blob:
            return label
    return None
