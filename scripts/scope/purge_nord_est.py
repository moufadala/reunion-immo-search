# -*- coding: utf-8 -*-
"""Purge immo Nord+Est. Usage: purge_apply.py [--apply]
Sans --apply : rapport seul. Avec --apply : backup puis DELETE + VACUUM.
Regle: on ne supprime QUE le HORS PERIMETRE CERTAIN, plus les inactives >30j."""
import sqlite3
import unicodedata
import re
import os
import shutil
import sys
import datetime
import collections

WATCH_DB = '/opt/data/data/reunion_watch.db'
SOCLE_DB = '/opt/data/projects/reunion-immo-search/data/socle_p0.sqlite'
THUMBS = '/opt/data/projects/reunion-immo-search/artifacts/app/thumbs'
APPLY = '--apply' in sys.argv
APOS = chr(39)
TS = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def norm(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFD', str(s)).encode('ascii', 'ignore').decode()
    s = s.lower().replace(APOS, ' ')
    s = re.sub(r'\bst\b', 'saint', s)
    s = re.sub(r'\bste\b', 'sainte', s)
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


QUARTIERS = {
    'saint-denis': ['sainte-clotilde', 'le-chaudron', 'chaudron', 'moufia', 'le-moufia',
                    'bellepierre', 'la-montagne', 'saint-francois', 'bois-de-nefles',
                    'la-bretagne', 'bretagne', 'le-brule', 'camelias', 'les-camelias',
                    'providence', 'la-source', 'champ-fleuri', 'ruisseau-des-noirs',
                    'vauban', 'barachois', 'montgaillard', 'domenjod', 'le-butor', 'butor',
                    'la-trinite', 'sainte-genevieve', 'commune-prima', 'bas-de-la-riviere',
                    'saint-jacques', 'patinoire'],
    'sainte-marie': ['riviere-des-pluies', 'la-riviere-des-pluies', 'duparc', 'beausejour',
                     'la-confiance', 'terrain-elisa', 'grande-montee', 'beaumont',
                     'la-ressource', 'les-cafes'],
    'sainte-suzanne': ['bagatelle', 'quartier-francais', 'commune-carron', 'deux-rives',
                       'village-desprez'],
    'saint-andre': ['cambuston', 'champ-borne', 'petit-bazar', 'riviere-du-mat',
                    'la-cressonniere', 'bras-des-chevrettes', 'ravine-creuse',
                    'mille-roches', 'le-colosse'],
}
CP = {'97400': 'saint-denis', '97490': 'saint-denis', '97417': 'saint-denis',
      '97495': 'saint-denis', '97438': 'sainte-marie', '97441': 'sainte-suzanne',
      '97440': 'saint-andre'}
TARGET = {'saint-denis', 'sainte-marie', 'sainte-suzanne', 'saint-andre'}
Q2C = {q: com for com, qs in QUARTIERS.items() for q in qs}
OUT = {'saint-pierre', 'le-tampon', 'tampon', 'saint-paul', 'saint-leu', 'saint-louis',
       'la-possession', 'saint-joseph', 'petite-ile', 'le-port', 'saint-benoit',
       'bras-panon', 'salazie', 'la-plaine-des-palmistes', 'sainte-rose', 'saint-philippe',
       'les-avirons', 'etang-sale', 'l-etang-sale', 'entre-deux', 'cilaos', 'trois-bassins',
       'saint-gilles-les-bains', 'la-saline', 'la-saline-les-bains', 'saint-gilles-les-hauts',
       'l-hermitage', 'boucan-canot', 'plateau-caillou', 'la-chaloupe', 'piton-saint-leu',
       'la-riviere', 'ravine-des-cabris', 'bois-d-olives', 'terre-sainte', 'grand-bois',
       'manapany', 'langevin', 'la-chatoire', 'bellemene', 'saline-les-bains'}

ro = sqlite3.connect('file:%s?mode=ro' % WATCH_DB, uri=True)
enr = {}
for ss, si, cn, zn, reg in ro.execute(
        'select source_site, source_id, city_normalized, zone_normalized, region '
        'from listing_product_enrichment'):
    enr[(ss, si)] = (norm(cn), norm(zn), reg)


def classify(ss, si, city, district, title):
    # Bug trouve le 27/07 (perte confirmee : zimo 019f8ebb, Saint-Denis reel,
    # tue par city_normalized='Bras-Panon' invente par l'enrichissement) :
    # l'enrichissement etait cru aveuglement, avant meme de regarder le champ
    # brut du scraper. On verifie D'ABORD si le brut dit sans ambiguite une
    # commune cible -- l'enrichissement ne peut plus la contredire.
    for raw in (city, district):
        n = norm(raw)
        if n and any(n == t or n.startswith(t + '-') for t in TARGET):
            return 'IN'
    e = enr.get((ss, si))
    if e:
        cn, zn, _ = e
        if cn in TARGET:
            return 'IN'
        if Q2C.get(cn) in TARGET or Q2C.get(zn) in TARGET:
            return 'IN'
        if cn in OUT:
            return 'OUT'
    for raw in (city, district):
        n = norm(raw)
        if not n:
            continue
        for t in TARGET:
            if n == t or n.startswith(t + '-'):
                return 'IN'
        if Q2C.get(n) in TARGET:
            return 'IN'
        for qt, com in Q2C.items():
            if com in TARGET and qt in n:
                return 'IN'
        parts = n.split('-')
        if n in OUT or '-'.join(parts[:2]) in OUT or '-'.join(parts[:3]) in OUT:
            return 'OUT'
    blob = '%s %s' % (city or '', title or '')
    for code, com in CP.items():
        if code in blob:
            return 'IN' if com in TARGET else 'OUT'
    nt = norm(title)
    if nt:
        for t in TARGET:
            if t in nt:
                return 'IN'
        for qt, com in Q2C.items():
            if com in TARGET and qt in nt and len(qt) > 6:
                return 'IN'
        for o in OUT:
            if len(o) > 6 and o in nt:
                return 'OUT'
    return 'AMBIGU'


cut = (datetime.datetime.now(datetime.timezone.utc)
       - datetime.timedelta(days=30)).isoformat()
rows = ro.execute('select source_site, source_id, city, district, title, is_active, '
                  'seen_last_at, image_url from rental_listings').fetchall()

kill = []
keep_keys = set()
reason = collections.Counter()
for ss, si, city, district, title, active, seen, img in rows:
    v = classify(ss, si, city, district, title)
    if v == 'OUT':
        kill.append((ss, si))
        reason['hors perimetre'] += 1
    elif (seen or '') < cut and not active:
        kill.append((ss, si))
        reason['inactive >30j'] += 1
    else:
        keep_keys.add((ss, si))
ro.close()

print('=== PURGE IMMO Nord+Est  (mode: %s) ===' % ('APPLY' if APPLY else 'DRY-RUN'))
print('rental_listings avant   : %d' % len(rows))
for r, k in reason.most_common():
    print('  a supprimer - %-18s %d' % (r, k))
print('  TOTAL a supprimer     : %d' % len(kill))
print('  a conserver           : %d' % len(keep_keys))

# --- vignettes orphelines (rapport seulement) ---
orphan_files = 0
orphan_bytes = 0
if os.path.isdir(THUMBS):
    keep_tokens = set()
    for ss, si in keep_keys:
        keep_tokens.add('%s_%s' % (ss, si))
        keep_tokens.add(str(si))
    total_f = 0
    total_b = 0
    for fn in os.listdir(THUMBS):
        fp = os.path.join(THUMBS, fn)
        if not os.path.isfile(fp):
            continue
        sz = os.path.getsize(fp)
        total_f += 1
        total_b += sz
        base = os.path.splitext(fn)[0]
        if not any(tok in base for tok in keep_tokens):
            orphan_files += 1
            orphan_bytes += sz
    print('thumbs/ total          : %d fichiers, %.2f Go' % (total_f, total_b / 1e9))
    print('thumbs/ orphelines     : %d fichiers, %.2f Go  (NON supprimees a ce stade)'
          % (orphan_files, orphan_bytes / 1e9))

if not APPLY:
    print('\n(dry-run : aucune ecriture. Relancer avec --apply)')
    sys.exit(0)

# ---------------- APPLY ----------------
for db in (WATCH_DB, SOCLE_DB):
    bak = '%s.bak.before-nordest-purge.%s' % (db, TS)
    shutil.copy2(db, bak)
    print('BACKUP -> %s (%.1f Mo)' % (bak, os.path.getsize(bak) / 1e6))

w = sqlite3.connect(WATCH_DB)
w.executemany('delete from rental_listings where source_site=? and source_id=?', kill)
w.executemany('delete from listing_product_enrichment where source_site=? and source_id=?', kill)
w.commit()
after_rl = w.execute('select count(*) from rental_listings').fetchone()[0]
after_en = w.execute('select count(*) from listing_product_enrichment').fetchone()[0]
w.execute('vacuum')
w.close()

s = sqlite3.connect(SOCLE_DB)
s.execute('pragma foreign_keys=ON')
before_l = s.execute('select count(*) from listings').fetchone()[0]
before_p = s.execute('select count(*) from listing_photos').fetchone()[0]
s.executemany('delete from listing_photos where listing_id in '
              '(select id from listings where source=? and site_id=?)', kill)
s.executemany('delete from listings where source=? and site_id=?', kill)
s.commit()
after_l = s.execute('select count(*) from listings').fetchone()[0]
after_p = s.execute('select count(*) from listing_photos').fetchone()[0]
s.execute('vacuum')
s.close()

print('\n--- RESULTAT ---')
print('reunion_watch.db  rental_listings : %d -> %d' % (len(rows), after_rl))
print('reunion_watch.db  enrichment      : %d -> %d' % (len(enr), after_en))
print('socle_p0.sqlite   listings        : %d -> %d' % (before_l, after_l))
print('socle_p0.sqlite   listing_photos  : %d -> %d' % (before_p, after_p))
print('taille reunion_watch.db : %.1f Mo' % (os.path.getsize(WATCH_DB) / 1e6))
print('taille socle_p0.sqlite  : %.1f Mo' % (os.path.getsize(SOCLE_DB) / 1e6))
