#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
checks = [
    ['tests/audit_clean_portal.py'],
    ['tests/audit_natural_search_contract.py', str(APP)],
    ['tests/audit_geo_photo_quality.py', str(APP)],
    ['tests/audit_inline_csp_sidepages.py', str(APP)],
    ['tests/audit_public_quality_budget.py', str(APP)],
    ['tests/audit_public_storage_state.py', str(APP)],
    ['tests/audit_public_perf_index.py', str(APP)],
    ['tests/audit_public_seo.py', str(APP)],
    ['tests/audit_ops_cockpit.py', str(APP)],
]
root=Path('/opt/data/projects/reunion-immo-search')
results=[]
for cmd in checks:
    p=subprocess.run([sys.executable, *cmd], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    results.append({'cmd':' '.join(cmd),'rc':p.returncode,'stdout':p.stdout[-1200:],'stderr':p.stderr[-1200:]})
errors=[r for r in results if r['rc']]
print(json.dumps({'ok':not errors,'results':results}, ensure_ascii=False, indent=2))
if errors:
    raise SystemExit(1)
print('HARD_REGRESSION_SUITE PASS')
