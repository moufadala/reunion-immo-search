#!/usr/bin/env bash
set -euo pipefail
IMMO_HOSTNAME=${IMMO_HOSTNAME:-immo.srv1723523.hstgr.cloud}
IMMO_ALT_HOSTNAME=${IMMO_ALT_HOSTNAME:-immo.148.230.103.174.sslip.io}
python3 - <<PY
import urllib.request, ssl, json, socket

HOSTS = ['$IMMO_HOSTNAME', '$IMMO_ALT_HOSTNAME']
ctx=ssl.create_default_context()

FORBIDDEN_HOME = [
    'Recherche immo Réunion — moteur visuel',
    'Canonique',
    'Suspects',
    'Match strict',
    'source_health.html',
    'saved_searches.html',
]
REQUIRED_HOME = [
    'Recherche immo RUN — portail propre',
    'Un portail immo simple, alimenté par notre base scrapée.',
    'veille.html', 'sources.html', 'doublons.html',
    'opportunites.html', 'localisation.html', 'alertes.html',
]
REQUIRED_PATHS = [
    'veille.html', 'sources.html', 'doublons.html', 'opportunites.html', 'localisation.html', 'alertes.html',
    'changes.html', 'source_health.html', 'dedup.html', 'opportunity.html', 'locations.html', 'alertes_cours.html',
    'changes.json', 'source_health.json', 'dedup_groups.json', 'opportunity.json', 'locations.json', 'coverage.json',
]

def resolve_report(host):
    print('DNS', host)
    for fam in (socket.AF_INET, socket.AF_INET6):
        try:
            addrs=socket.getaddrinfo(host, 443, fam, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            print(' ', fam.name, [a[-1][0] for a in addrs])
        except Exception as e:
            print(' ', fam.name, 'NONE', str(e))

def fetch(url, force_ipv4=False):
    if force_ipv4:
        orig = socket.getaddrinfo
        socket.getaddrinfo = lambda host, port, family=0, type=0, proto=0, flags=0: orig(host, port, socket.AF_INET, type, proto, flags)
    try:
        r=urllib.request.urlopen(url, timeout=25, context=ctx)
        data=r.read()
        print(url, 'STATUS', r.status, 'CT', r.headers.get('content-type'), 'LEN', len(data), 'FINAL', r.geturl())
        return r, data
    finally:
        if force_ipv4:
            socket.getaddrinfo = orig

def qa_host(host, force_ipv4=False):
    r, html_raw = fetch(f'https://{host}/', force_ipv4=force_ipv4)
    assert r.status == 200
    html = html_raw.decode('utf-8', 'ignore')
    missing=[x for x in REQUIRED_HOME if x not in html]
    leaked=[x for x in FORBIDDEN_HOME if x in html]
    assert not missing, ('missing_home', missing)
    assert not leaked, ('forbidden_home', leaked)

    rj, raw = fetch(f'https://{host}/listings.json', force_ipv4=force_ipv4)
    data=json.loads(raw)
    items=data.get('listings') or []
    assert len(items) >= 500, len(items)
    local_primary=sum(1 for x in items if x.get('local_image_url'))
    local_multi=sum(1 for x in items if isinstance(x.get('local_image_urls'), list) and len(x.get('local_image_urls')) > 1)
    opp=sum(1 for x in items if x.get('opportunity_analysis') or x.get('opportunity_score') is not None)
    assert local_primary >= max(350, int(len(items) * 0.98)), (local_primary, len(items))
    assert local_multi >= max(100, int(len(items) * 0.30)), (local_multi, len(items))
    assert opp == len(items), (opp, len(items))

    for path in REQUIRED_PATHS:
        rp, body = fetch(f'https://{host}/{path}', force_ipv4=force_ipv4)
        assert rp.status == 200 and len(body) > 100, (path, rp.status, len(body))

    cov=json.loads(fetch(f'https://{host}/coverage.json', force_ipv4=force_ipv4)[1])
    assert cov.get('gallery_photos', 0) >= max(100, int(len(items) * 0.30)), cov
    assert cov.get('count') == len(items), (cov.get('count'), len(items))
    opp_payload=json.loads(fetch(f'https://{host}/opportunity.json', force_ipv4=force_ipv4)[1])
    assert len(opp_payload.get('top') or []) >= 20
    loc_payload=json.loads(fetch(f'https://{host}/locations.json', force_ipv4=force_ipv4)[1])
    assert len(loc_payload.get('listings') or []) == len(items)

    print('QA_CLEAN_LAYERS_OK host=', host, 'listings=', len(items), 'local_primary=', local_primary, 'local_multi=', local_multi, 'opportunity=', opp)

for h in HOSTS:
    resolve_report(h)

# Hostinger hostname has historically exposed an IPv6 trap; force IPv4 for that regression path.
qa_host(HOSTS[0], force_ipv4=True)
# Canonical sslip hostname is IPv4-only; use normal resolution.
qa_host(HOSTS[1], force_ipv4=False)
PY
