#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checks d'observabilite du pipeline immo -- P1.3 du Plan P1.

PRINCIPE DIRECTEUR, paye le 2026-07-24..31 :
    Ce qui BLOQUE doit etre un defaut de CHAINE, jamais un defaut de contenu
    d'une annonce isolee. Un gate `max_boilerplate: 0` viole par UNE annonce a
    arrete la publication pendant 7 jours sans que personne ne le voie.

    -> severite BLOCK  : la chaine est cassee. Le run s'arrete, personne ne publie.
    -> severite WARN   : des donnees sont douteuses. On publie, on alerte.

Usage :
    python3 immo_health_checks.py                      # tous les checks
    python3 immo_health_checks.py --json rapport.json  # + rapport machine
    python3 immo_health_checks.py --warn-only          # n'echoue jamais (observation)

Code de sortie : 0 = aucun BLOCK. 1 = au moins un BLOCK.
LECTURE SEULE : n'ecrit que le rapport JSON demande.
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys, time
from datetime import datetime, timezone

# --- Chemins. PIEGE PROUVE le 31/07 : /opt/data existe AUSSI sur l'hote et
# --- contient une base leurre de 362 annonces. Depuis l'hote c'est /opt/hermes/data.
def _racine() -> str:
    """ORDRE CRITIQUE : /opt/hermes/data D'ABORD.

    Sur l'hote, les DEUX chemins existent -- /opt/data y est un repertoire leurre
    contenant une base de 362 annonces (la vraie en a 2525). Tester /opt/data en
    premier ferait tourner tous les checks sur les mauvaises donnees, sans erreur.
    Dans le conteneur, /opt/hermes/data n'existe pas : on retombe sur /opt/data,
    qui y designe le bon dossier. L'ordre resout donc les deux cas.
    """
    for base in (os.environ.get("IMMO_DATA_ROOT"), "/opt/hermes/data", "/opt/data"):
        if base and os.path.isdir(os.path.join(base, "projects", "reunion-immo-search")):
            return base
    raise SystemExit("ERREUR: racine de donnees introuvable (IMMO_DATA_ROOT ?)")

ROOT = _racine()
PROJET = os.path.join(ROOT, "projects", "reunion-immo-search")
DB = os.environ.get("IMMO_DB_PATH") or os.path.join(ROOT, "data", "reunion_watch.db")
FEED = os.environ.get("IMMO_FEED_PATH") or os.path.join(PROJET, "artifacts", "app", "feed.json")
RUNS = os.environ.get("IMMO_ASYNC_RUNS_DIR") or os.path.join(ROOT, "artifacts", "reunion-watch-async")
ETAT = os.path.join(PROJET, "artifacts", "health_state.json")   # volumes du run precedent

PERIMETRE = ("Saint-Denis", "Sainte-Marie", "Sainte-Suzanne", "Saint-André")

# Seuils. Chacun est justifie -- pas de constante magique (cf. loi d'Ousterhout).
FRAICHEUR_MAX_H = float(os.environ.get("IMMO_FRAICHEUR_MAX_H", "36"))
CHUTE_VOLUME_MAX = 0.40     # -40 % de lignes d'une source d'un jour a l'autre = collecte cassee
LOYER_MIN, LOYER_MAX = 200, 8000        # hors de ces bornes : contenu douteux -> WARN
LOYER_VENTE_MIN = 100000                 # prix de vente dans le champ loyer -> contenu douteux -> WARN
SURFACE_MIN, SURFACE_MAX = 8.0, 400.0   # 974 m2 et 1 m2 ont ete publies le 30/07
PART_ABERRANTE_MAX = 0.02   # >2 % d'aberrations = probleme de mapping, pas de saisie
FRAICHEUR_PART_BLOCK = 0.50 # >50 % des sources muettes = defaut de chaine, sinon WARN

# Espace disque. Premiere estimation du 31/07 : 10,8 Go -- SOUS-EVALUEE, corrigee
# le meme jour apres un 2e plantage disque. Le decompte reel :
#
#   STAGES DU RUN
#     daily-tech-stage                      2,2 Go
#     daily-clean-stage                     2,2 Go
#     .bak.product-hardening-v5             2,2 Go  <- scripts de l'ANCIEN portail
#     .bak.wave2-lot-c-detail-geo-photo     2,2 Go  <- idem  (41 % du total !)
#   PHASE FINALE -- l'app est copiee TROIS fois, c'est ce que j'avais rate :
#     promote_app_candidate : sauvegarde app.pre-promote   2,0 Go
#     promote_app_candidate : copie du candidat vers app   2,0 Go
#     rollback_app_drill    : snapshot supplementaire      2,0 Go
#   ------------------------------------------------------------------
#   TOTAL                                              ~14,8 Go
#
# Le run de 12h02 a demarre avec 12 Go : il ne POUVAIT pas aboutir. Il a scrape
# une heure avant de planter a 685 Mo libres. Refuser de demarrer coute une
# minute ; planter au milieu coute une heure et laisse un run.status vide.
DISQUE_MIN_GO = 15          # ~14,8 Go de besoin reel, arrondi au superieur
DISQUE_ALERTE_GO = 25       # en dessous : la place pour un seul run

resultats: list[dict] = []


def check(nom: str, severite: str, ok: bool, message: str, preuve=None):
    resultats.append({"check": nom, "severite": severite if not ok else "OK",
                      "ok": ok, "message": message, "preuve": preuve})


def _con():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# ---------------------------------------------------------------- 1. execution
def check_dernier_run():
    """Le run PROGRAMME est-il alle au bout ? 7 echecs consecutifs sont passes
    inapercus parce que personne ne regardait ce fichier."""
    if not os.path.isdir(RUNS):
        return check("run_termine", "BLOCK", False, f"repertoire de runs absent : {RUNS}")
    dirs = sorted(d for d in os.listdir(RUNS) if os.path.isdir(os.path.join(RUNS, d)))
    if not dirs:
        return check("run_termine", "BLOCK", False, "aucun run trouve")
    dernier = dirs[-1]
    f = os.path.join(RUNS, dernier, "run.status")
    if not os.path.exists(f):
        return check("run_termine", "BLOCK", False, f"run.status absent pour {dernier}")
    # Format reel du fichier (verifie le 31/07) : lignes cle=valeur, dont
    # `exit_code=1`. Un regex sur `exit=` ne matche RIEN et renvoyait -1 en
    # silence -- faux positif attrape en testant sur les vraies donnees.
    RE_CODE = re.compile(r"^exit_code=(-?\d+)", re.M)

    def _code(chemin):
        m = RE_CODE.search(open(chemin, encoding="utf-8", errors="replace").read())
        return int(m.group(1)) if m else None

    code = _code(f)
    if code is None:
        return check("run_termine", "BLOCK", False,
                     f"run.status de {dernier} illisible (pas de exit_code=)")
    # combien d'echecs consecutifs en remontant ?
    consecutifs = 0
    for d in reversed(dirs):
        p = os.path.join(RUNS, d, "run.status")
        if not os.path.exists(p):
            break
        c2 = _code(p)
        if c2 is None or c2 == 0:
            break
        consecutifs += 1
    check("run_termine", "BLOCK", code == 0,
          f"dernier run {dernier} : exit={code}" +
          (f" — {consecutifs} echec(s) consecutif(s)" if consecutifs else ""),
          {"run": dernier, "exit": code, "echecs_consecutifs": consecutifs})


# ------------------------------------------------------------ 1 bis. disque
def check_espace_disque():
    """Refuser de demarrer plutot que de planter au milieu d'une promotion.

    Le 31/07 : disque a 100 %, plantage pendant promote_app_candidate en copiant
    ~2 Go d'app -> run.status vide, aucune preuve exploitable. Un run qui ne peut
    pas aller au bout ne doit pas commencer.

    Cause racine a traiter separement (P1.1) : `artifact_retention` est present
    dans le script VERSIONNE et absent du script EXECUTE, donc la retention n'a
    jamais tourne. Ce check est un garde-fou, pas le correctif.
    """
    st = os.statvfs(ROOT)
    libre_go = st.f_bavail * st.f_frsize / (1024 ** 3)
    total_go = st.f_blocks * st.f_frsize / (1024 ** 3)
    detail = {"libre_go": round(libre_go, 1), "total_go": round(total_go, 1),
              "besoin_run_go": 10.8}
    check("espace_disque", "BLOCK", libre_go >= DISQUE_MIN_GO,
          f"{libre_go:.1f} Go libres sur {total_go:.0f} — il en faut au moins "
          f"{DISQUE_MIN_GO} (un run en produit ~10,8)", detail)
    if libre_go >= DISQUE_MIN_GO:
        check("marge_disque", "WARN", libre_go >= DISQUE_ALERTE_GO,
              f"{libre_go:.1f} Go libres : de la place pour ~{int(libre_go // 10.8)} run(s) "
              f"seulement — la retention d'artefacts ne tourne pas", detail)


# ---------------------------------------------------------------- 2. fraicheur
def check_fraicheur():
    with _con() as c:
        c.row_factory = sqlite3.Row
        lignes = list(c.execute(
            "select source_site, count(*) n, max(seen_last_at) vu "
            "from rental_listings where is_active=1 group by 1"))
    if not lignes:
        return check("fraicheur", "BLOCK", False, "aucune source active en base")
    now = datetime.now(timezone.utc)
    muettes = []
    for r in lignes:
        try:
            d = datetime.fromisoformat(str(r["vu"]).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            h = (now - d).total_seconds() / 3600
        except Exception:
            h = 9999
        if h > FRAICHEUR_MAX_H:
            muettes.append({"source": r["source_site"], "heures": round(h, 1), "annonces": r["n"]})
    part_muet = len(muettes) / max(len(lignes), 1)
    severite = "BLOCK" if part_muet > FRAICHEUR_PART_BLOCK else "WARN"
    check("fraicheur", severite, not muettes,
          f"{len(muettes)}/{len(lignes)} source(s) muette(s) depuis plus de {FRAICHEUR_MAX_H:.0f} h",
          muettes[:10])


# ---------------------------------------------------------------- 3. volume
def check_volume():
    """Chute brutale = collecte cassee. Compare au run precedent, pas a un absolu."""
    with _con() as c:
        actuel = {r[0]: r[1] for r in c.execute(
            "select source_site, count(*) from rental_listings where is_active=1 group by 1")}
    precedent = {}
    if os.path.exists(ETAT):
        try:
            precedent = json.load(open(ETAT, encoding="utf-8")).get("volumes", {})
        except Exception:
            pass
    if not precedent:
        return check("volume", "WARN", True,
                     "pas d'etat precedent — reference posee pour le prochain run",
                     {"sources": len(actuel)})
    chutes = []
    for s, avant in precedent.items():
        maint = actuel.get(s, 0)
        if avant >= 10 and maint < avant * (1 - CHUTE_VOLUME_MAX):
            chutes.append({"source": s, "avant": avant, "maintenant": maint,
                           "chute_pct": round(100 * (1 - maint / avant))})
    check("volume", "BLOCK", not chutes,
          f"{len(chutes)} source(s) en chute de plus de {CHUTE_VOLUME_MAX:.0%}", chutes)


# ------------------------------------------------------- 4. coherence base/feed
def check_base_vs_feed():
    """Le trajet base -> feed perd-il des annonces ? Et le feed invente-t-il ?"""
    if not os.path.exists(FEED):
        return check("base_vs_feed", "BLOCK", False, f"feed absent : {FEED}")
    feed = json.load(open(FEED, encoding="utf-8"))
    items = feed.get("listings") or []
    # Le feed conserve volontairement les annonces desactivees (bloc `movements`,
    # 407 le 30/07). Comparer TOUT le feed aux seules actives de la base produisait
    # 407 faux "fantomes" -- faux positif attrape sur donnees reelles. On compare
    # donc actives a actives : une annonce active dans le feed DOIT exister et etre
    # active en base.
    ids_feed_actives = {str(x.get("id", "")) for x in items if x.get("active")}
    with _con() as c:
        ids_db = {f"{r[0]}:{r[1]}" for r in c.execute(
            "select source_site, source_id from rental_listings where is_active=1")}
    fantomes = ids_feed_actives - ids_db
    check("feed_sans_base", "BLOCK", not fantomes,
          f"{len(fantomes)} annonce(s) active(s) du feed absente(s) ou inactive(s) en base",
          sorted(fantomes)[:10])
    # compteurs meta recalcules
    meta = feed.get("meta") or {}
    actives = sum(1 for x in items if x.get("active"))
    ecarts = []
    if meta.get("actives") is not None and meta["actives"] != actives:
        ecarts.append({"compteur": "actives", "affiche": meta["actives"], "reel": actives})
    if meta.get("total") is not None and meta["total"] != len(items):
        ecarts.append({"compteur": "total", "affiche": meta["total"], "reel": len(items)})
    check("compteurs_meta", "WARN", not ecarts,
          f"{len(ecarts)} compteur(s) meta faux" if ecarts else "compteurs meta exacts", ecarts)


# ------------------------------------------------- 5. mapping a zero resultat
def check_mapping_non_vide():
    """LE check qui aurait attrape le 0/395 du 30/07 le jour meme.
    Une source presente en base mais qui ne produit AUCUNE ligne exploitable
    (ni prix, ni surface) a un mapping casse, meme si ses tests passent."""
    with _con() as c:
        c.row_factory = sqlite3.Row
        lignes = list(c.execute(
            "select source_site, count(*) n, "
            "sum(case when rent_eur is not null then 1 else 0 end) avec_prix, "
            "sum(case when surface_m2 is not null then 1 else 0 end) avec_surface "
            "from rental_listings where is_active=1 group by 1"))
    casses = [{"source": r["source_site"], "annonces": r["n"],
               "avec_prix": r["avec_prix"], "avec_surface": r["avec_surface"]}
              for r in lignes if r["n"] >= 5 and (r["avec_prix"] == 0 or r["avec_surface"] == 0)]
    check("mapping_non_vide", "BLOCK", not casses,
          f"{len(casses)} source(s) sans aucun prix ou aucune surface exploitable", casses)


# --------------------------------------------------------- 6. valeurs aberrantes
def check_valeurs():
    """WARN et non BLOCK : une annonce absurde ne doit pas arreter la publication
    du catalogue. C'est exactement l'erreur qui a coute 7 jours. En revanche une
    PROPORTION anormale d'aberrations trahit un probleme de mapping -> BLOCK."""
    with _con() as c:
        c.row_factory = sqlite3.Row
        lignes = list(c.execute(
            "select source_site, source_id, title, rent_eur, surface_m2 "
            "from rental_listings where is_active=1"))
    ab = []
    ventes_dans_loyer = []
    for r in lignes:
        pb = []
        if r["rent_eur"] is not None and r["rent_eur"] >= LOYER_VENTE_MIN:
            ventes_dans_loyer.append({"id": f"{r['source_site']}:{r['source_id']}",
                                      "loyer": r["rent_eur"],
                                      "titre": (r["title"] or "")[:48]})
        if r["rent_eur"] is not None and not (LOYER_MIN <= r["rent_eur"] <= LOYER_MAX):
            pb.append(f"loyer={r['rent_eur']}")
        if r["surface_m2"] is not None and not (SURFACE_MIN <= r["surface_m2"] <= SURFACE_MAX):
            pb.append(f"surface={r['surface_m2']}")
        if pb:
            ab.append({"id": f"{r['source_site']}:{r['source_id']}", "pb": ", ".join(pb),
                       "titre": (r["title"] or "")[:48]})
    check("prix_vente_dans_loyer", "WARN", not ventes_dans_loyer,
          f"{len(ventes_dans_loyer)} annonce(s) avec prix de vente dans rent_eur (>= {LOYER_VENTE_MIN})",
          ventes_dans_loyer[:10])
    part = len(ab) / max(len(lignes), 1)
    check("valeurs_aberrantes", "WARN", not ab,
          f"{len(ab)} annonce(s) hors bornes ({part:.1%})", ab[:10])
    check("part_aberrante", "BLOCK", part <= PART_ABERRANTE_MAX,
          f"{part:.1%} d'aberrations (seuil {PART_ABERRANTE_MAX:.0%}) — au-dela, "
          f"c'est le mapping qui est en cause, pas la saisie")


# ------------------------------------------------------------- 7. perimetre
def check_perimetre():
    feed = json.load(open(FEED, encoding="utf-8")) if os.path.exists(FEED) else {"listings": []}
    items = [x for x in feed.get("listings", []) if x.get("active")]
    hors = [x for x in items if x.get("commune") and x["commune"] not in PERIMETRE]
    sans = [x for x in items if not x.get("commune")]
    check("perimetre", "WARN", not hors,
          f"{len(hors)} annonce(s) active(s) hors perimetre dans le feed",
          [{"id": x.get("id"), "commune": x.get("commune")} for x in hors[:10]])
    check("commune_inconnue", "WARN", not sans,
          f"{len(sans)} annonce(s) active(s) sans commune", [x.get("id") for x in sans[:10]])


def sauver_etat():
    with _con() as c:
        volumes = {r[0]: r[1] for r in c.execute(
            "select source_site, count(*) from rental_listings where is_active=1 group by 1")}
    os.makedirs(os.path.dirname(ETAT), exist_ok=True)
    tmp = ETAT + ".tmp"
    json.dump({"horodatage": datetime.now(timezone.utc).isoformat(), "volumes": volumes},
              open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, ETAT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="ecrire le rapport machine ici")
    ap.add_argument("--warn-only", action="store_true", help="ne jamais echouer (observation)")
    ap.add_argument("--save-state", action="store_true", help="memoriser les volumes du run")
    ap.add_argument("--gate-chain", action="store_true",
                    help="mode daily bloquant: uniquement defauts de chaine, pas run/disque historique")
    a = ap.parse_args()

    checks = (check_fraicheur, check_volume, check_base_vs_feed,
              check_mapping_non_vide, check_valeurs, check_perimetre) if a.gate_chain else (
              check_dernier_run, check_espace_disque, check_fraicheur, check_volume, check_base_vs_feed,
              check_mapping_non_vide, check_valeurs, check_perimetre)
    for f in checks:
        try:
            f()
        except Exception as e:
            check(f.__name__, "BLOCK", False, f"check en erreur : {type(e).__name__}: {e}")

    blocs = [r for r in resultats if not r["ok"] and r["severite"] == "BLOCK"]
    warns = [r for r in resultats if not r["ok"] and r["severite"] == "WARN"]

    print(f"racine de donnees : {ROOT}")
    print("=" * 74)
    for r in resultats:
        marque = "OK   " if r["ok"] else ("BLOCK" if r["severite"] == "BLOCK" else "WARN ")
        print(f"[{marque}] {r['check']:22s} {r['message']}")
        if not r["ok"] and r["preuve"]:
            for p in (r["preuve"] if isinstance(r["preuve"], list) else [r["preuve"]])[:5]:
                print(f"          {p}")
    print("=" * 74)
    print(f"{len(blocs)} bloquant(s), {len(warns)} avertissement(s)")

    if a.json:
        json.dump({"horodatage": datetime.now(timezone.utc).isoformat(),
                   "racine": ROOT, "bloquants": len(blocs), "avertissements": len(warns),
                   "resultats": resultats},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.save_state:
        sauver_etat()
    return 0 if (a.warn_only or not blocs) else 1


if __name__ == "__main__":
    sys.exit(main())
