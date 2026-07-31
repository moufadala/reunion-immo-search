#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Constitue le JEU DE REFERENCE FIGE des mappers immo -- P1.3 du Plan P1.

POURQUOI
    Le 2026-07-30, un mapper Leboncoin passait 3 tests verts et produisait
    0 Listing sur 395 vraies annonces : ses fixtures decrivaient une forme de
    donnees qui n'existait pas. Un jeu de reference constitue a partir des
    PAYLOADS REELS rend ce piege impossible a reproduire.

REGLE DE CONSTITUTION (importante)
    On ne fige que les raw dont le `source_id` EXISTE EN BASE.
    Verifie le 31/07 : le repertoire des raw contient 369 fichiers leboncoin
    pour 366 lignes en base. Les 3 en trop sont les FIXTURES du test
    test_leboncoin_apify_mapping.py (ids 2712345678, 42, 998877), ecrites dans
    les artefacts de PRODUCTION en executant les tests. Les figer reviendrait a
    valider le mapper contre la fixture qu'il etait cense remplacer.

CONFIDENTIALITE -- A LIRE
    Le jeu contient des annonces reelles : noms de bailleurs, telephones,
    adresses de particuliers. Il RESTE SUR LE VPS.
    Ne jamais le commiter, ni dans le depot du projet, ni dans le vault (qui
    part sur GitHub). Le script ecrit un .gitignore a cote pour s'en premunir.

Usage (a lancer par Hermes, cote VPS) :
    python3 make_golden_sample.py                 # 5 annonces par source
    python3 make_golden_sample.py --par-source 8
    python3 make_golden_sample.py --sortie /chemin/golden

Ecrit : <sortie>/<source>.json, <sortie>/manifest.json, <sortie>/.gitignore
Ne modifie NI la base NI les artefacts existants.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, sys
from datetime import datetime, timezone


def racine() -> str:
    """/opt/hermes/data D'ABORD : sur l'hote les deux existent, /opt/data y est un leurre."""
    for base in (os.environ.get("IMMO_DATA_ROOT"), "/opt/hermes/data", "/opt/data"):
        if base and os.path.isdir(os.path.join(base, "projects", "reunion-immo-search")):
            return base
    raise SystemExit("ERREUR: racine de donnees introuvable")


ROOT = racine()
DB = os.path.join(ROOT, "data", "reunion_watch.db")
DEFAUT_SORTIE = os.path.join(ROOT, "projects", "reunion-immo-search", "artifacts", "golden")

GITIGNORE = """# Jeu de reference : annonces REELLES (noms, telephones, adresses).
# Ne jamais commiter. Ni ici, ni dans le vault.
*
"""


def chemin_hote(p: str) -> str:
    """Traduit un raw_json_path stocke en chemin conteneur vers le chemin hote."""
    if p and p.startswith("/opt/data/") and ROOT != "/opt/data":
        return ROOT + p[len("/opt/data"):]
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--par-source", type=int, default=5,
                    help="nombre d'annonces figees par source (defaut 5)")
    ap.add_argument("--sortie", default=DEFAUT_SORTIE)
    a = ap.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    c = con.cursor()

    sources = [r[0] for r in c.execute(
        "select distinct source_site from rental_listings where is_active=1 order by 1")]
    os.makedirs(a.sortie, exist_ok=True)
    open(os.path.join(a.sortie, ".gitignore"), "w", encoding="utf-8").write(GITIGNORE)

    manifest = {"genere_le": datetime.now(timezone.utc).isoformat(),
                "racine": ROOT, "par_source": a.par_source, "sources": {}}
    total_figes = total_manquants = 0

    for s in sources:
        # On prend des annonces VARIEES : la plus chere, la moins chere, et des
        # intermediaires. Un echantillon uniforme raterait les cas limites qui
        # cassent les mappers (surface nulle, prix aberrant, champs absents).
        lignes = list(c.execute(
            "select source_id, raw_json_path, title, rent_eur, surface_m2 "
            "from rental_listings where source_site=? and is_active=1 "
            "and raw_json_path is not null and raw_json_path<>'' "
            "order by (rent_eur is null) desc, rent_eur", (s,)))
        if not lignes:
            manifest["sources"][s] = {"figes": 0, "note": "aucun raw disponible"}
            continue
        n = min(a.par_source, len(lignes))
        idx = [round(i * (len(lignes) - 1) / max(n - 1, 1)) for i in range(n)]
        choisies, manquants = [], 0
        for i in dict.fromkeys(idx):
            r = lignes[i]
            p = chemin_hote(r["raw_json_path"])
            if not os.path.exists(p):
                manquants += 1
                continue
            try:
                brut = json.load(open(p, encoding="utf-8"))
            except Exception as e:
                manquants += 1
                continue
            choisies.append({
                "source_id": r["source_id"],
                # attendus verifiables : ce que le mapper DOIT retrouver
                "attendu": {"rent_eur": r["rent_eur"], "surface_m2": r["surface_m2"],
                            "titre_debut": (r["title"] or "")[:40]},
                "raw": brut,
            })
        if choisies:
            f = os.path.join(a.sortie, f"{s}.json")
            json.dump({"source": s, "annonces": choisies}, open(f, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            empreinte = hashlib.sha256(open(f, "rb").read()).hexdigest()[:16]
            manifest["sources"][s] = {"figes": len(choisies), "raw_manquants": manquants,
                                      "fichier": os.path.basename(f), "sha256_16": empreinte}
        else:
            manifest["sources"][s] = {"figes": 0, "raw_manquants": manquants,
                                      "note": "aucun raw lisible"}
        total_figes += len(choisies)
        total_manquants += manquants
        print(f"   {s:14s} {len(choisies):2d} figee(s)" +
              (f"  ({manquants} raw manquant(s))" if manquants else ""))

    manifest["total_figes"] = total_figes
    manifest["total_raw_manquants"] = total_manquants
    json.dump(manifest, open(os.path.join(a.sortie, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print()
    print(f"{total_figes} annonce(s) figee(s) sur {len(sources)} source(s) -> {a.sortie}")
    if total_manquants:
        print(f"ATTENTION : {total_manquants} raw reference(s) en base mais absent(s) du disque "
              f"— la retention d'artefacts les a peut-etre effaces.")
    print("Rappel : ce jeu contient des donnees personnelles. Il ne sort pas du VPS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
