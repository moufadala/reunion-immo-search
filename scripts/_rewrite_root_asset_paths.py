"""Repointe les chemins d'assets de l'index.html copie a la racine.

Vite emet des chemins RELATIFS (./assets/...). Copie telle quelle a la
racine de l'app, cette page chargerait mais son JS/CSS resterait en 404
(seul artifacts/app/v2/assets/ existe, pas artifacts/app/assets/) -> page
blanche silencieuse, aucune erreur HTTP visible sur l'index lui-meme.
Bug trouve et corrige le 2026-07-27, lors de la bascule /v2/ -> /.
"""
import re
import sys
import pathlib

p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding='utf-8')
s2 = re.sub(r'((?:src|href)=["\'])\./assets/', r'\1./v2/assets/', s)
p.write_text(s2, encoding='utf-8')
print('index racine recable vers ./v2/assets/ :', s != s2)
if s == s2:
    raise SystemExit('ERREUR: aucun chemin assets trouve a reecrire -- '
                     'verifier le format de sortie de vite (a-t-il change ?)')
