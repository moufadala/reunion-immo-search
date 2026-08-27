"""La porte QA galerie compte les photos DISTINCTES, pas les fichiers.

Regression 2026-08-27 (candidate-only, run 20260827T160741Z) :
bienici:ag971031-474984932 renvoyait photo_1 et photo_2 byte-identiques.
cache_photos.py nomme les vignettes d'apres l'URL -> deux fichiers, un seul
contenu. Le feed public collapse le doublon (src.photo_gallery), l'audit
comparait au brut -> `manifeste 20 -> feed 19` et rc=1 sur un produit correct.
"""
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'audit_product_v2', ROOT / 'scripts' / 'audit_product_v2.py')
audit_product_v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_product_v2)

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 60


def _app(tmp_path, images, locals_, contenus):
    app = tmp_path / 'app'
    (app / 'thumbs').mkdir(parents=True)
    (app / 'v2').mkdir()
    for nom, contenu in contenus.items():
        (app / 'thumbs' / nom).write_bytes(contenu)
    (app / 'v2' / 'index.html').write_text('<html></html>', encoding='utf-8')
    (app / 'v2' / 'feed.json').write_text('{}', encoding='utf-8')
    (app / 'feed.json').write_text(json.dumps({'listings': [{
        'id': 'bienici:x1', 'source': 'bienici', 'active': True,
        'image': images[0], 'images': images,
    }]}), encoding='utf-8')
    (app / 'photos_manifest.json').write_text(json.dumps({'photos': {
        'bienici:x1': {'local': locals_[0], 'locals': locals_},
    }}), encoding='utf-8')
    return app


def _run(app, capsys):
    argv = sys.argv
    sys.argv = ['audit_product_v2.py', str(app)]
    try:
        code = audit_product_v2.main()
    finally:
        sys.argv = argv
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_doublon_de_contenu_ne_bloque_pas(tmp_path, capsys):
    """Deux fichiers, un seul contenu : le feed a raison de n'en exposer qu'un."""
    app = _app(
        tmp_path,
        images=['/thumbs/a.jpg', '/thumbs/c.jpg'],
        locals_=['/thumbs/a.jpg', '/thumbs/b.jpg', '/thumbs/c.jpg'],
        contenus={'a.jpg': JPEG, 'b.jpg': JPEG, 'c.jpg': JPEG + b'\x01'},
    )
    code, rapport = _run(app, capsys)
    assert rapport['manifest_to_feed_losses'] == []
    assert rapport['manifest_doublons_contenu'] == 1
    assert code == 0, rapport['erreurs']


def test_vraie_perte_de_galerie_bloque_toujours(tmp_path, capsys):
    """Une photo distincte presente en local mais absente du feed reste bloquante."""
    app = _app(
        tmp_path,
        images=['/thumbs/a.jpg'],
        locals_=['/thumbs/a.jpg', '/thumbs/c.jpg'],
        contenus={'a.jpg': JPEG, 'c.jpg': JPEG + b'\x01'},
    )
    code, rapport = _run(app, capsys)
    assert rapport['manifest_to_feed_losses'] == ['bienici:x1: manifeste 2 -> feed 1']
    assert code == 1
