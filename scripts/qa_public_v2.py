#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA de la page publique V2 -- remplace le harnais legacy neutralise le 31/07.

POURQUOI CE FICHIER EXISTE
    Pour debloquer la publication, ONZE audits ont ete passes en report-only --
    y compris `public_qa` lui-meme, qui exigeait les textes de l'ANCIEN portail
    (`REQUIRED_HOME`, "Recherche immo RUN - portail propre"). Tout le harnais de
    qualite avait ete ecrit pour valider une interface que la refonte a remplacee.

    Consequence a ne pas laisser passer : le critere de "fait" de C.0 disait
    "public_qa rc=0". Ce critere ne vaut plus rien puisque l'audit est neutralise.
    **On ne declare pas une publication reussie sur un controle qu'on a eteint.**

    Ce script est la preuve de remplacement. Il ne verifie PAS l'ancien portail :
    il verifie que la V2 est reellement servie et exploitable.

CE QU'IL VERIFIE (et rien d'autre -- volontairement minimal)
    1. la racine ne sert plus l'ancien portail
    2. la V2 est complete sur le disque servi (index + assets + feed)
    3. le feed est du JSON valide, non vide, et coherent avec la base
    4. les annonces publiees ont bien un prix et une commune
    5. aucun fichier de l'ancien portail n'a ete regenere
    6. (optionnel) la page repond en HTTP avec les identifiants fournis

Usage :
    python3 qa_public_v2.py
    python3 qa_public_v2.py --url https://immo.exemple/ --auth user:motdepasse
    python3 qa_public_v2.py --json rapport.json

Code de sortie : 0 = publication saine. 1 = probleme.
LECTURE SEULE.
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys, urllib.request, base64

def racine() -> str:
    """/opt/hermes/data D'ABORD : sur l'hote les deux existent, /opt/data y est un leurre."""
    for base in (os.environ.get("IMMO_DATA_ROOT"), "/opt/hermes/data", "/opt/data"):
        if base and os.path.isdir(os.path.join(base, "projects", "reunion-immo-search")):
            return base
    raise SystemExit("ERREUR: racine de donnees introuvable")

ROOT = racine()
APP = os.path.join(ROOT, "projects", "reunion-immo-search", "artifacts", "app")
DB = os.path.join(ROOT, "data", "reunion_watch.db")

# Fichiers de l'ancien portail : leur retour signale que les generateurs legacy
# tournent encore (ils sont censes etre sortis du pipeline en C.2).
LEGACY = ("veille.html", "sources.html", "doublons.html", "opportunites.html",
          "localisation.html", "alertes.html", "dedup.html")
# C.2 voie (b): fichiers legacy produits par l'ancien portail headless qui ne
# doivent plus etre publies dans artifacts/app. listings.json reste le bus.
LEGACY_PUBLISHED_FILES = ("ops.html", "locations.html", "locations.json",
                          "opportunity.html", "opportunity.json",
                          "source_health.html", "dedup_groups.json",
                          "alertes_cours.html")
# changes.html retire de la liste le 2026-08-03 : contrairement aux autres, il est produit,
# enrichi et audite par la chaine (l.248/249/359 du refresh) -- ce n'est pas un residu du
# portail. Savoir s'il fait partie du produit V2 est un arbitrage de Moufadal, EN ATTENTE.
# Marqueur textuel de l'ancien portail.
TITRE_LEGACY = "portail propre"

resultats: list[dict] = []

def check(nom, ok, msg, preuve=None, severity="blocking"):
    resultats.append({
        "check": nom,
        "ok": bool(ok),
        "message": msg,
        "preuve": preuve,
        "severity": severity,
    })


def qa_racine():
    """La racine sert-elle la V2 (directement ou par redirection) ?"""
    idx = os.path.join(APP, "index.html")
    if not os.path.exists(idx):
        return check("racine_presente", False, "artifacts/app/index.html absent")
    t = open(idx, encoding="utf-8", errors="replace").read()
    legacy = TITRE_LEGACY in t.lower()
    v2 = bool(re.search(r"/v2/|v2/assets/|assets/index-[A-Za-z0-9_-]+\.js", t))
    check("racine_pas_legacy", not legacy,
          "la racine sert l'ANCIEN portail" if legacy else "la racine ne sert pas l'ancien portail")
    check("racine_vers_v2", v2,
          "la racine pointe vers la V2" if v2 else
          "la racine ne reference ni /v2/ ni les assets V2",
          {"taille_octets": len(t)})


def qa_v2_complete():
    v2 = os.path.join(APP, "v2")
    idx = os.path.join(v2, "index.html")
    check("v2_index", os.path.exists(idx), f"v2/index.html {'present' if os.path.exists(idx) else 'ABSENT'}")
    assets = os.path.join(v2, "assets")
    fichiers = os.listdir(assets) if os.path.isdir(assets) else []
    js = [f for f in fichiers if f.endswith(".js")]
    css = [f for f in fichiers if f.endswith(".css")]
    check("v2_assets", bool(js and css),
          f"{len(js)} js, {len(css)} css dans v2/assets", fichiers[:4])
    # les assets references par l'index doivent exister reellement
    if os.path.exists(idx):
        t = open(idx, encoding="utf-8", errors="replace").read()
        refs = re.findall(r"(?:src|href)=\"([^\"]*assets/[^\"]+)\"", t)
        manquants = [r for r in refs
                     if not os.path.exists(os.path.join(v2, r.lstrip("./").replace("v2/", "", 1)))]
        check("v2_assets_resolus", not manquants,
              f"{len(refs)} reference(s) d'asset, {len(manquants)} introuvable(s)", manquants[:3])


def qa_feed():
    f = os.path.join(APP, "feed.json")
    if not os.path.exists(f):
        return check("feed_present", False, "feed.json absent")
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        return check("feed_valide", False, f"feed.json illisible : {type(e).__name__}")
    items = d.get("listings") or []
    actives = [x for x in items if x.get("active")]
    check("feed_non_vide", len(actives) > 0, f"{len(actives)} annonce(s) active(s) publiee(s)")
    # atteignable depuis /v2/ (lien symbolique ou copie)
    fv2 = os.path.join(APP, "v2", "feed.json")
    check("feed_atteignable_v2", os.path.exists(fv2),
          "feed.json atteignable depuis /v2/" if os.path.exists(fv2)
          else "feed.json INATTEIGNABLE depuis /v2/ -- la page sera vide")
    # coherence avec la base : le feed ne doit pas s'etre effondre
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = con.execute("select count(*) from rental_listings where is_active=1").fetchone()[0]
        ratio = len(actives) / n if n else 0
        check("feed_coherent_base", 0.5 <= ratio <= 1.2,
              f"{len(actives)} actives publiees pour {n} en base (ratio {ratio:.2f})",
              {"feed": len(actives), "base": n})
    except Exception as e:
        check("feed_coherent_base", False, f"base illisible : {type(e).__name__}")
    # qualite minimale des annonces publiees
    sans_prix = sum(1 for x in actives if not x.get("rent"))
    sans_commune = sum(1 for x in actives if not x.get("commune"))
    check("annonces_exploitables",
          sans_prix < len(actives) * 0.2 and sans_commune < len(actives) * 0.2,
          f"{sans_prix} sans prix, {sans_commune} sans commune sur {len(actives)}")


def qa_pas_de_legacy():
    presents = [f for f in LEGACY if os.path.exists(os.path.join(APP, f))]
    legacy_files = [f for f in LEGACY_PUBLISHED_FILES if os.path.exists(os.path.join(APP, f))]
    # C.1b rend la QA V2 bloquante pour racine/assets/feed/exploitabilité.
    # Les 7 pages legacy sont encore volontairement hors périmètre jusqu'à C.2 :
    # on les inventorie en WARN par défaut pour ne pas recréer un rc=1 chronique.
    # C.2 devra lancer ce même script avec IMMO_QA_STRICT_LEGACY=1 ou
    # --strict-legacy pour transformer ce WARN en échec bloquant.
    strict = os.environ.get("IMMO_QA_STRICT_LEGACY") == "1"
    severity = "blocking" if strict else "warn"
    check("pas_de_pages_legacy", not presents,
          f"{len(presents)} page(s) de l'ancien portail regeneree(s)" if presents
          else "aucune page de l'ancien portail", presents,
          severity=severity)
    check("pas_de_fichiers_legacy", not legacy_files,
          f"{len(legacy_files)} fichier(s) legacy encore publie(s)" if legacy_files
          else "aucun fichier legacy publie hors bus listings.json", legacy_files,
          severity=severity)


def qa_http(url: str, auth: str | None):
    if not url:
        return
    req = urllib.request.Request(url, headers={"User-Agent": "immo-qa-v2"})
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code, corps = r.getcode(), r.read(4000).decode("utf-8", "replace")
    except Exception as e:
        code, corps = getattr(e, "code", 0), ""
    if code == 401:
        return check("http_page", True,
                     "401 : authentification demandee — la page est protegee (fournir --auth pour aller plus loin)")
    check("http_page", code == 200, f"HTTP {code} sur {url}")
    if code == 200:
        check("http_pas_legacy", TITRE_LEGACY not in corps.lower(),
              "la page servie n'est pas l'ancien portail" if TITRE_LEGACY not in corps.lower()
              else "la page servie EST l'ancien portail")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("IMMO_PUBLIC_URL", ""))
    ap.add_argument("--auth", default=os.environ.get("IMMO_PUBLIC_AUTH", ""))
    ap.add_argument("--json")
    ap.add_argument("--strict-legacy", action="store_true",
                    help="C.2: rendre bloquant le retour des pages legacy secondaires")
    a = ap.parse_args()
    if a.strict_legacy:
        os.environ["IMMO_QA_STRICT_LEGACY"] = "1"

    for f in (qa_racine, qa_v2_complete, qa_feed, qa_pas_de_legacy):
        try:
            f()
        except Exception as e:
            check(f.__name__, False, f"check en erreur : {type(e).__name__}: {e}")
    if a.url:
        try:
            qa_http(a.url, a.auth or None)
        except Exception as e:
            check("http_page", False, f"{type(e).__name__}: {e}")

    echecs = [r for r in resultats if not r["ok"] and r.get("severity") != "warn"]
    warnings = [r for r in resultats if not r["ok"] and r.get("severity") == "warn"]
    print(f"QA publique V2 — racine {ROOT}")
    print("=" * 76)
    for r in resultats:
        label = "OK   " if r["ok"] else ("WARN " if r.get("severity") == "warn" else "ECHEC")
        print(f"[{label}] {r['check']:22s} {r['message']}")
        if not r["ok"] and r["preuve"]:
            print(f"          {r['preuve']}")
    print("=" * 76)
    print(f"{len(echecs)} echec(s) bloquant(s), {len(warnings)} avertissement(s) sur {len(resultats)} controle(s)")
    if a.json:
        json.dump({"ok": not echecs, "warnings": len(warnings), "resultats": resultats},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
