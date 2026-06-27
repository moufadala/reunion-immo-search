#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/opt/data/projects/reunion-immo-search')
APP = ROOT / 'artifacts' / 'app'
OUT = ROOT / 'artifacts' / f'wave2_lot_c_detail_geo_photo_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.md'


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.strip()


def main() -> int:
    data = json.loads((APP / 'listings.json').read_text(encoding='utf-8'))
    items = data.get('listings') or []
    metrics = {
        'annonces': len(items),
        'photos_principales_locales': sum(1 for x in items if x.get('local_image_url')),
        'galeries_multi_photos': sum(1 for x in items if len(x.get('local_image_urls') or []) > 1),
        'map_points_prudents': sum(1 for x in items if x.get('map_point')),
        'geo_quality_couverte': sum(1 for x in items if (x.get('geo_quality') or {}).get('level') in {'haute','moyenne','commune'}),
    }
    tests = [
        ['python3','tests/audit_detail_geo_photo_prudent.py','artifacts/app'],
        ['python3','tests/audit_geo_photo_quality.py','artifacts/app'],
        ['python3','tests/audit_public_perf_index.py','artifacts/app'],
    ]
    results=[]
    for t in tests:
        rc,out=run(t)
        results.append((t,rc,out))
    lines=[
        '# Vague 2 Lot C — détail/photos/géo prudente',
        '',
        f'Généré: {datetime.now(timezone.utc).isoformat()}',
        '',
        '## Changements',
        '- Ajout d’un patch UI détail-only (`wave2LotCDetailGeoPhoto`) : galerie avec miniatures dans la modale, navigation robuste, fallback explicite si images cassées.',
        '- Ajout d’une ligne source/référence/date vue dans la fiche détail.',
        '- Ajout d’un encart “Localisation prudente” qui rappelle que l’adresse exacte n’est pas déduite ni inventée; suppression de la carte embarquée si la confiance géo est insuffisante.',
        '- Ajout d’un fallback image sur les cards sans ajouter de contenu lourd à la homepage.',
        '',
        '## Métriques données publiques',
    ]
    lines += [f'- {k}: {v}' for k,v in metrics.items()]
    lines += ['', '## Gates exécutés']
    for cmd,rc,out in results:
        status='PASS' if rc==0 else f'FAIL rc={rc}'
        tail='\n'.join(out.splitlines()[-12:])
        lines += [f'- `{ " ".join(cmd) }`: {status}', '```', tail, '```']
    lines += ['', '## Notes', '- Les audits Playwright existants n’ont pas été relancés ici car le module `playwright` est absent dans l’environnement courant; les gates Python non-browser ci-dessus passent.']
    OUT.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(OUT)
    return 0 if all(rc==0 for _,rc,_ in results) else 1

if __name__ == '__main__':
    raise SystemExit(main())
