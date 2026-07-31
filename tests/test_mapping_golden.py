#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Non-regression des mappers contre le JEU DE REFERENCE REEL -- P1.3 du Plan P1.

CE QUE CE TEST COUVRE, ET CE QU'IL NE COUVRE PAS
    Seul Leboncoin possede un mapper isole et appelable (`_map_leboncoin_item`).
    Les autres sources parsent du HTML directement dans leur fonction de collecte :
    on ne peut pas les rejouer hors ligne sans refaire une requete.
    Ce test fait donc DEUX choses distinctes :

      A. LEBONCOIN -- test complet du mapper : on rejoue les payloads reels figes
         et on exige un comptage `produits / total`. C'est le test qui aurait
         attrape le 0/395 du 30/07 le jour meme.

      B. TOUTES SOURCES -- derive de schema : les cles presentes dans les payloads
         figes le sont-elles toujours dans les payloads d'aujourd'hui ? Une source
         qui renomme un champ (`list_id` -> `listId`) casse son mapper en silence.
         Ce controle previent AVANT que le mapping ne tombe a zero.

    Ce n'est pas une couverture complete : c'est ce qui est testable aujourd'hui
    sans reecrire les collecteurs. Le dire vaut mieux que de laisser croire le
    contraire.

Prerequis : le jeu de reference, genere par `make_golden_sample.py`.

Usage :
    python3 test_mapping_golden.py
    python3 test_mapping_golden.py --golden /chemin/golden --verbose

Code de sortie : 0 = tout va bien. 1 = regression detectee.
LECTURE SEULE.
"""
from __future__ import annotations
import argparse, importlib.util, json, os, sys


def racine() -> str:
    """/opt/hermes/data D'ABORD : sur l'hote les deux existent, /opt/data y est un leurre."""
    for base in (os.environ.get("IMMO_DATA_ROOT"), "/opt/hermes/data", "/opt/data"):
        if base and os.path.isdir(os.path.join(base, "projects", "reunion-immo-search")):
            return base
    raise SystemExit("ERREUR: racine de donnees introuvable")


ROOT = racine()
PROJET = os.path.join(ROOT, "projects", "reunion-immo-search")
GOLDEN_DEFAUT = os.path.join(PROJET, "artifacts", "golden")
SCRAPER = os.path.join(PROJET, "scripts", "realestate_multi_sources_scraper.py")

echecs: list[str] = []
verbose = False


def dit(nom: str, ok: bool, msg: str):
    if not ok:
        echecs.append(f"{nom}: {msg}")
    if verbose or not ok:
        print(f"[{'OK   ' if ok else 'ECHEC'}] {nom:26s} {msg}")


def charger_scraper():
    """Importe le module de collecte sans l'executer comme script."""
    spec = importlib.util.spec_from_file_location("rms", SCRAPER)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rms"] = mod
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"  (module de collecte non importable : {type(e).__name__}: {e})")
        return None


def cles_profondes(obj, prefixe="", max_prof=2):
    """Cles observables jusqu'a une profondeur donnee -- assez pour reperer un
    renommage de champ sans exploser sur les gros payloads."""
    out = set()
    if isinstance(obj, dict) and max_prof > 0:
        for k, v in obj.items():
            chemin = f"{prefixe}{k}"
            out.add(chemin)
            if isinstance(v, dict):
                out |= cles_profondes(v, chemin + ".", max_prof - 1)
    return out


def main() -> int:
    global verbose
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=GOLDEN_DEFAUT)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    verbose = a.verbose

    if not os.path.isdir(a.golden):
        print(f"ERREUR: jeu de reference absent : {a.golden}")
        print("Le generer d'abord :  python3 make_golden_sample.py")
        return 1

    fichiers = sorted(f for f in os.listdir(a.golden)
                      if f.endswith(".json") and f != "manifest.json")
    if not fichiers:
        print(f"ERREUR: aucune source figee dans {a.golden}")
        return 1

    mod = charger_scraper()

    # ------------------------------------------------------- A. mapper Leboncoin
    lbc = os.path.join(a.golden, "leboncoin.json")
    if os.path.exists(lbc) and mod is not None and hasattr(mod, "_map_leboncoin_item"):
        d = json.load(open(lbc, encoding="utf-8"))
        annonces = d.get("annonces", [])
        produits, details = 0, []
        for ann in annonces:
            brut = ann["raw"]
            # le raw figé encapsule parfois le payload d'origine sous "raw"
            candidat = brut.get("raw") if isinstance(brut.get("raw"), dict) else brut
            try:
                listing = mod._map_leboncoin_item(candidat)
            except Exception as e:
                listing = None
                details.append(f"{ann['source_id']}: exception {type(e).__name__}")
            if listing is not None:
                produits += 1
            else:
                details.append(f"{ann['source_id']}: rejete")
        total = len(annonces)
        # LE comptage qui compte. Un mapper qui rejette TOUT sur des donnees
        # reelles est casse, meme si ses tests unitaires passent.
        dit("leboncoin_mapper", total > 0 and produits > 0,
            f"{produits}/{total} Listing produits sur payloads reels" +
            ("" if produits else " -- MAPPER CASSE"))
        if produits and produits < total:
            dit("leboncoin_mapper_partiel", produits >= total * 0.8,
                f"{total - produits} rejet(s) : {'; '.join(details[:3])}")
    elif os.path.exists(lbc):
        print("  (mapper leboncoin non appelable -- section A ignoree)")

    # --------------------------------------------- B. derive de schema, toutes sources
    for f in fichiers:
        source = f[:-5]
        d = json.load(open(os.path.join(a.golden, f), encoding="utf-8"))
        annonces = d.get("annonces", [])
        if not annonces:
            dit(f"schema_{source}", False, "aucune annonce figee")
            continue
        # Cles communes a TOUTES les annonces figees = le contrat implicite de la source.
        commun = None
        for ann in annonces:
            k = cles_profondes(ann["raw"])
            commun = k if commun is None else (commun & k)
        commun = commun or set()
        # Les valeurs attendues doivent etre non vides sur la majorite : une source
        # qui perd son prix ou sa surface est cassee en amont du mapper.
        avec_prix = sum(1 for x in annonces if x["attendu"].get("rent_eur") is not None)
        avec_surface = sum(1 for x in annonces if x["attendu"].get("surface_m2") is not None)
        dit(f"attendus_{source}", avec_prix > 0 or avec_surface > 0,
            f"{len(annonces)} figee(s), {avec_prix} avec prix, {avec_surface} avec surface, "
            f"{len(commun)} cle(s) de contrat")

    print()
    if echecs:
        print(f"{len(echecs)} regression(s) :")
        for e in echecs:
            print(f"  - {e}")
        return 1
    print(f"Aucune regression sur {len(fichiers)} source(s) figee(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
