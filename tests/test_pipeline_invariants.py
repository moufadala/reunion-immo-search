#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Invariants du pipeline immo -- P1.3 du Plan P1.

Ces invariants sont aujourd'hui garantis par des COMMENTAIRES. Chacun d'eux, s'il
est viole, casse la production silencieusement. Ils sont ici rendus executables.

Verifie le SCRIPT REELLEMENT EXECUTE, pas la copie versionnee -- la divergence
entre les deux (8 hunks au 31/07) est precisement l'un des invariants testes.

Usage :
    python3 test_pipeline_invariants.py            # verifie, code retour 0/1
    python3 test_pipeline_invariants.py --verbose  # detaille chaque invariant

LECTURE SEULE : n'ecrit rien.
"""
from __future__ import annotations
import os, re, subprocess, sys

def racine() -> str:
    """/opt/hermes/data D'ABORD : sur l'hote les deux existent, /opt/data y est un leurre."""
    for base in (os.environ.get("IMMO_DATA_ROOT"), "/opt/hermes/data", "/opt/data"):
        if base and os.path.isdir(os.path.join(base, "projects", "reunion-immo-search")):
            return base
    raise SystemExit("ERREUR: racine de donnees introuvable")

ROOT = racine()
PROJET = os.path.join(ROOT, "projects", "reunion-immo-search")
EXECUTE = os.path.join(ROOT, "scripts", "immo_daily_public_refresh.sh")
VERSIONNE = os.path.join(PROJET, "scripts", "immo_daily_public_refresh.sh")

echecs: list[str] = []
verbose = "--verbose" in sys.argv


def verifie(nom: str, ok: bool, explication: str):
    if not ok:
        echecs.append(f"{nom}: {explication}")
    if verbose or not ok:
        print(f"[{'OK  ' if ok else 'ECHEC'}] {nom}" + ("" if ok else f" -- {explication}"))


def lire(p: str) -> str:
    return open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""


def main() -> int:
    src = lire(EXECUTE)
    if not src:
        print(f"ECHEC: script execute introuvable : {EXECUTE}")
        return 1

    # --- 1. promote_app_candidate AVANT build_product_v2 -----------------------
    # promote_app_candidate.py:70 fait shutil.rmtree(artifacts/app). Si
    # build_product_v2 tourne AVANT, la v2 (feed.json, /v2/, photos_manifest)
    # est detruite chaque soir a 16:30 et l'interface disparait.
    i_promote = src.find("run_step promote_app_candidate")
    i_build = src.find("run_step build_product_v2")
    verifie("ordre_promote_avant_build",
            i_promote != -1 and i_build != -1 and i_promote < i_build,
            f"promote_app_candidate (pos {i_promote}) doit preceder build_product_v2 (pos {i_build}) "
            "-- promote fait un rmtree de artifacts/app")

    # --- 2. IMMO_V2_AS_ROOT=1 garanti ----------------------------------------
    # build_product_v2.sh:20 lit V2_RACINE="${IMMO_V2_AS_ROOT:-0}". Sans definition,
    # le defaut est 0 : la v2 n'est pas servie en racine et l'ancien portail revient.
    env_ok = bool(re.search(r"IMMO_V2_AS_ROOT\s*=\s*['\"]?1", src)) or \
             os.environ.get("IMMO_V2_AS_ROOT") == "1"
    verifie("v2_en_racine", env_ok,
            "IMMO_V2_AS_ROOT=1 n'est defini ni dans le pipeline ni dans l'environnement "
            "-- le defaut 0 remet l'ancien portail en racine")

    # --- 3. Aucun audit de CONTENU dans le chemin bloquant --------------------
    # La lecon des 7 jours perdus : un gate a tolerance zero sur le contenu d'UNE
    # annonce a bloque toute la publication. Un audit de qualite editoriale doit
    # rapporter, pas interrompre.
    bloquants = []
    for m in re.finditer(r"^\s*run_step\s+(\S*description_quality\S*)", src, re.M):
        nom = m.group(1)
        ligne = src[:m.start()].count("\n") + 1
        # tolere si la ligne porte une neutralisation explicite
        contexte = src[m.start():m.start() + 300]
        if not re.search(r"\|\|\s*true|report[_-]?only|REPORT[_-]?ONLY|--warn-only|exit\s+0", contexte, re.I):
            bloquants.append(f"{nom} (l.{ligne})")
    verifie("audit_contenu_non_bloquant", not bloquants,
            f"audit(s) de contenu encore bloquant(s) : {', '.join(bloquants)} "
            "-- un defaut de contenu ne doit jamais arreter la chaine")

    # --- 4. Le script execute ne diverge pas du versionne --------------------
    # Tant qu'ils divergent, tout correctif commite est sans effet en production.
    if os.path.exists(VERSIONNE):
        d = subprocess.run(["diff", "-q", EXECUTE, VERSIONNE],
                           capture_output=True, text=True)
        verifie("execute_egal_versionne", d.returncode == 0,
                "le script execute diverge du script versionne "
                "-- tout correctif commite reste sans effet en production")
    else:
        verifie("execute_egal_versionne", False, f"script versionne introuvable : {VERSIONNE}")

    # --- 5. Les etapes de publication sont bien presentes --------------------
    for etape in ("build_product_v2", "publish_clean_static", "promote_app_candidate"):
        verifie(f"etape_presente_{etape}", f"run_step {etape}" in src,
                f"l'etape {etape} a disparu du pipeline")

    print()
    if echecs:
        print(f"{len(echecs)} invariant(s) viole(s) :")
        for e in echecs:
            print(f"  - {e}")
        return 1
    print("Tous les invariants du pipeline sont respectes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
