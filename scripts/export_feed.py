#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exporte le feed du produit immo : rental_listings + enrichment + detail.

Un seul fichier de sortie, un seul contrat. L'interface ne lit QUE ca.
Remplace la chaine de patchs qui injectaient du HTML dans index.html.

Sortie : artifacts/app/feed.json
  {meta: {...}, listings: [...], movements: {...}, sources: [...]}
"""
from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone, timedelta
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from profils import PROFILS, scorer  # type: ignore[import-not-found]  # noqa: E402
except ModuleNotFoundError:
    # The personalised profile file is intentionally untracked. Tests, reviews
    # and fresh deployments still need a deterministic non-secret fallback.
    import importlib.util
    _profiles_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profils.example.py')
    _profiles_spec = importlib.util.spec_from_file_location('profils_example', _profiles_path)
    if _profiles_spec is None or _profiles_spec.loader is None:
        raise
    _profiles_module = importlib.util.module_from_spec(_profiles_spec)
    _profiles_spec.loader.exec_module(_profiles_module)
    PROFILS, scorer = _profiles_module.PROFILS, _profiles_module.scorer
import geo_quartiers as gq  # noqa: E402
from enrich_source_details_v3 import llm_input_hash  # noqa: E402
from src.publication_policy import evaluate_publication  # noqa: E402
from src.public_feed_dedup import deduplicate_public_feed  # noqa: E402
from src.description_observability import assess_description  # noqa: E402

DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')
EVENTS_DB = os.environ.get('IMMO_EVENTS_DB', '/opt/data/artifacts/immo-alerts/history.sqlite')
ROOT = '/opt/data/projects/reunion-immo-search'
OUT = os.environ.get('IMMO_FEED_OUT', ROOT + '/artifacts/app/feed.json')
APP_ROOT = Path(
    os.environ.get("IMMO_APP_PATH") or str(Path(OUT).resolve().parent)
).resolve()


def app_file(*parts: str) -> Path:
    path = APP_ROOT.joinpath(*parts).resolve(strict=False)
    try:
        path.relative_to(APP_ROOT)
    except ValueError as exc:
        raise ValueError(f"candidate app path escapes root: {path}") from exc
    return path


def local_media_exists(value: object) -> bool:
    if not value:
        return False
    return app_file(str(value).lstrip("/\\")).exists()

# distances precalculees quartier -> point de reference.
# Le point de reference lui-meme reste dans ce fichier COTE SERVEUR et n'est
# jamais recopie dans le feed servi : seules les durees en sortent.
DIST = ROOT + '/config/distances_quartiers.json'

COMMUNES = ['Saint-Denis', 'Sainte-Marie', 'Sainte-Suzanne', 'Saint-André']
SERVE_COMMUNES = ['Saint-Denis', 'Sainte-Marie']

# ordre de confiance, du plus precis au moins precis
PRECISION_RANK = {
    'adresse_exacte': 5,
    'rue': 4,
    'residence': 3,
    'point_carte': 3,
    'quartier': 2,
    'commune': 1,
    'inconnu': 0,
}
PRECISION_LABEL = {
    'adresse_exacte': 'Adresse exacte',
    'rue': 'Rue',
    'residence': 'Résidence',
    'point_carte': 'Point cartographique',
    'quartier': 'Quartier',
    'commune': 'Commune seule',
    'inconnu': 'Localisation inconnue',
}


def norm(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFD', str(s)).encode('ascii', 'ignore').decode()
    s = s.lower().replace(chr(39), ' ')
    s = re.sub(r'\bst\b', 'saint', s)
    s = re.sub(r'\bste\b', 'sainte', s)
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


EXCLUDED_COMMUNE_NORMS = {norm('Saint-André')}
PUBLICATION_MIN_SURFACE_M2 = 65.0
PUBLICATION_MAX_RENT_EUR = 1700
DETAIL_READ_MIN_CHARS = 80
EXCLUDED_QUARTIER_LABELS = {
    'providence': 'Providence',
    'saint-francois': 'Saint-François',
}
DESCRIPTION_EXCLUDED_QUARTIERS = (
    'providence', 'la-providence', 'saint-francois',
)
DESCRIPTION_LOCATION_RE = re.compile(
    r'\b(?:situe(?:e|es|s)?\s+a|situe(?:e|es|s)?\s+au|a|au|aux|location(?:\s+(?:de|d|un|une|appartement|studio|maison|t[0-9]|f[0-9]|meuble|meublee)){0,8}|louer\s+a)\s+(?P<q>(?:la\s+)?providence|saint\s+francois)\b'
)
DESCRIPTION_REPERE_RE = re.compile(
    r'\b(?:vue|face|proche|pres|minutes?\s+de|a\s+\d+\s*(?:min|minutes?)\s+de|acces|route|lycee\s+de|chu\s+de|secteur|preference|souhait)\b'
)


def excluded_quartier_from_field(quartier):
    n = norm(quartier)
    if not n:
        return None
    for needle, label in EXCLUDED_QUARTIER_LABELS.items():
        if needle in n:
            return label
    return None


def residential_flag_looks_like_amenity_false_positive(title, reasons):
    """Le flag non-résidentiel historique est trop large sur parking/garage/terrain.

    Il confond parfois une commodité d'un logement avec une annonce de parking,
    garage ou terrain. En cas de doute, on garde le logement visible.
    """
    title_n = norm(title).replace('-', ' ')
    if not re.search(r'\b(t[1-6]|f[1-6]|studio|appartement|maison|villa|duplex)\b', title_n):
        return False
    joined = ' '.join(str(x) for x in (reasons or [])).lower()
    weak = any(k in joined for k in ('strong_keyword=parking', 'strong_keyword=garage', 'strong_keyword=terrain'))
    strong_non_res = any(k in joined for k in (
        'property_type=commercial', 'property_type=box', 'local commercial',
        'local professionnel', 'bureau', 'bureaux', 'locaux commerciaux'))
    return weak and not strong_non_res


def excluded_quartier_from_description(title, description, location_label=None):
    """Exclusion prudente: seulement si le quartier apparait comme lieu.

    Les mentions de repere (vue/proche/CHU/lycee/secteur souhait...) ne cachent
    jamais une annonce. Le titre est inclus parce que certains portails y portent
    la tournure de lieu (ex. "LOCATION - ST-DENIS MONTGAILLARD").
    """
    raw_title = str(title or '')
    hay = ' '.join(str(x or '') for x in (title, description, location_label))
    txt = norm(hay).replace('-', ' ')
    title_norm = norm(raw_title).replace('-', ' ')


    for needle, label in ((r'\b(?:la\s+)?providence\b', 'Providence'),
                          (r'\bsaint\s+francois\b', 'Saint-François')):
        for m in re.finditer(needle, txt):
            before = txt[max(0, m.start() - 100):m.start()]
            if DESCRIPTION_REPERE_RE.search(before):
                continue
            if re.search(r'\b(?:quartier|secteur|situe(?:e|es|s)?\s+a\s+saint\s+denis|location\b.{0,80}\bsaint\s+denis|loue\b.{0,80}\bsaint\s+denis)\b', before):
                return label

    for m in DESCRIPTION_LOCATION_RE.finditer(txt):
        before = txt[max(0, m.start() - 70):m.start()]
        if DESCRIPTION_REPERE_RE.search(before):
            continue
        q = norm(m.group('q'))
        return {
            'providence': 'Providence',
            'la-providence': 'Providence',
            'saint-francois': 'Saint-François',
        }.get(q)
    return None


def detail_text_ok(description_full, min_chars=DETAIL_READ_MIN_CHARS):
    """A detail page is read only when HTTP content produced useful text."""
    return len(str(description_full or '').strip()) >= min_chars



def _parse_observed_at(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def description_quality_payload(description, detail, fallback_seen_at=None):
    attempted_at = (
        _parse_observed_at(detail.get("description_attempted_at") or detail.get("fetched_at"))
        if detail else None
    )
    version = "detail-enrich-v1" if attempted_at else "source-card-v1"
    attempted_at = attempted_at or _parse_observed_at(fallback_seen_at)
    full_text_evidence = bool(detail and detail.get("description_full_text_evidence"))
    observation = assess_description(
        description,
        extractor_version=version,
        attempted_at=attempted_at,
        http_status=detail.get("http_status") if detail else None,
        full_text_evidence=full_text_evidence,
    )
    return {
        "status": observation.status,
        "length": observation.length,
        "sha256": observation.sha256,
        "extractor_version": observation.extractor_version,
        "attempted_at": observation.attempted_at.isoformat() if observation.attempted_at else None,
        "succeeded_at": observation.succeeded_at.isoformat() if observation.succeeded_at else None,
        "markers": list(observation.markers),
        "full_text_evidence": observation.full_text_evidence,
    }


def description_detail_read(detail, quality):
    """True only when the published text is proven to come from a full detail block."""
    return bool(
        detail
        and detail.get("http_status") == 200
        and quality.get("full_text_evidence")
        and quality.get("status") in {"fetched_complete", "source_short_complete"}
    )

def normalize_published_at(value, anchor_seen_at=None):
    text = str(value or '').strip()
    if not text:
        return None
    anchor = _parse_observed_at(anchor_seen_at) or datetime.now(timezone.utc)
    folded = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    relative = re.search(r"il y a\s+(\d+)\s*(h|heure|heures|j|jour|jours|sem|semaine|semaines|mois)\b", folded)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit.startswith('h'):
            parsed = anchor - timedelta(hours=amount)
        elif unit.startswith('j'):
            parsed = anchor - timedelta(days=amount)
        elif unit.startswith('sem'):
            parsed = anchor - timedelta(weeks=amount)
        else:
            parsed = anchor - timedelta(days=30 * amount)
    elif folded == 'hier':
        parsed = anchor - timedelta(days=1)
    else:
        parsed = _parse_observed_at(text)
    if parsed is None or parsed.year < 2000 or parsed > anchor + timedelta(days=1):
        return None
    return parsed.isoformat()
def public_rule_violation(listing):
    """Return the first non-destructive public publication exclusion reason."""
    rent_value = as_int(listing.get('rent'))
    if rent_value is None:
        return 'loyer_inconnu'
    if rent_value > PUBLICATION_MAX_RENT_EUR:
        return 'loyer_sup_1700'
    surface_value = as_float(listing.get('surface'))
    if surface_value is None:
        return 'surface_inconnue'
    if surface_value < PUBLICATION_MIN_SURFACE_M2:
        return 'surface_inf_65'
    return None


def active_public_listings(listings):
    """The public feed is an availability feed; history lives in movements/pages."""
    return [item for item in listings if item.get('active') is True]

def reconciliation_product_payload(active_input, excluded_ids, eligible_items, visible_items):
    """Account for every active identity from DB input to one public card/link."""
    if not isinstance(excluded_ids, Mapping):
        raise TypeError('excluded_ids must be a mapping of identity to reason')
    normalized_excluded_ids = {}
    for raw_identity, raw_reason in excluded_ids.items():
        identity = str(raw_identity or '').strip()
        reason = str(raw_reason or '').strip()
        if not identity or not reason:
            raise ValueError('excluded_ids requires non-empty identities and reasons')
        if identity in normalized_excluded_ids:
            raise ValueError(f'duplicate excluded identity after normalization: {identity}')
        normalized_excluded_ids[identity] = reason
    normalized_excluded_ids = dict(sorted(normalized_excluded_ids.items()))
    policy_exclusions = Counter(normalized_excluded_ids.values())

    eligible_ids = [str(item.get('id') or '') for item in eligible_items]
    visible_ids = [str(item.get('id') or '') for item in visible_items]
    visible_set = set(visible_ids)
    eligible_set = set(eligible_ids)
    linked_ids = set()
    invalid_also_on_links = 0
    for item in visible_items:
        links = item.get('also_on') or []
        if not isinstance(links, list):
            invalid_also_on_links += 1
            continue
        for link in links:
            if not isinstance(link, dict) or not str(link.get('id') or '').strip():
                invalid_also_on_links += 1
                continue
            linked_ids.add(str(link['id']).strip())
    hidden_ids = [identity for identity in eligible_ids if identity in linked_ids and identity not in visible_set]
    unexplained_eligible_ids = sorted(eligible_set - visible_set - set(hidden_ids))
    unexpected_also_on_ids = sorted(linked_ids - eligible_set)
    fields = {
        'missing_title': sum(1 for item in visible_items if not item.get('title')),
        'missing_rent': sum(1 for item in visible_items if as_int(item.get('rent')) is None),
        'missing_surface': sum(1 for item in visible_items if as_float(item.get('surface')) is None),
        'missing_commune': sum(1 for item in visible_items if not item.get('commune')),
        'missing_description': sum(1 for item in visible_items if not str(item.get('description') or '').strip()),
        'missing_photo': sum(
            1 for item in visible_items
            if not str(item.get('image') or '').strip()
            and not any(
                str(value or '').strip()
                for value in (item.get('images') if isinstance(item.get('images'), list) else [])
            )
        ),
    }
    # A visible card without description or photo is never an acceptable,
    # explained loss. Other gaps remain explicit accounting evidence.
    hard_visible_requirements = {'missing_description', 'missing_photo'}
    explanations = {
        key: value for key, value in fields.items()
        if value and key not in hard_visible_requirements
    }
    return {
        'active_input': int(active_input),
        'policy_exclusions': {str(k): int(v) for k, v in sorted(policy_exclusions.items())},
        'excluded_ids': normalized_excluded_ids,
        'eligible': len(eligible_ids),
        'dedup_hidden': len(hidden_ids),
        'visible': len(visible_ids),
        'eligible_ids': eligible_ids,
        'visible_ids': visible_ids,
        'also_on_ids': hidden_ids,
        'fields': fields,
        'unexplained_eligible_ids': unexplained_eligible_ids,
        'unexpected_also_on_ids': unexpected_also_on_ids,
        'invalid_also_on_links': invalid_also_on_links,
        'field_explanations': explanations,
        'field_explanation_reasons': {
            'missing_title': 'source_title_missing',
            'missing_rent': 'publication_policy_should_exclude',
            'missing_surface': 'publication_policy_should_exclude',
            'missing_commune': 'publication_policy_should_exclude',
        },
    }



def canonical_public_feed(listings):
    """Hide only strong, obvious cross-source duplicates from the V2 feed.

    The historical DB-level `is_canonical` is intentionally not used as a hard
    publication filter here: its older duplicate_key can be too weak for product
    hiding. This V2 policy is narrower and non-destructive: exact same normalized
    title + commune + rent + surface + rooms, seen on multiple sources. Suspects
    and near matches remain visible.
    """
    source_priority = {'zimo': 0, 'leboncoin': 1, 'seloger': 2, 'bienici': 3, 'ofim': 4, 'ofim_rss': 5}
    for item in listings:
        item['display_canonical'] = True
        item['canonical_display_id'] = item.get('id')
        item['dedup_decision'] = item.get('dedup_decision') or ''
        item['dedup_reason'] = item.get('dedup_reason') or ''

    buckets = defaultdict(list)
    for item in listings:
        rent_value = as_int(item.get('rent'))
        surface_value = as_float(item.get('surface'))
        if rent_value is None or surface_value is None:
            continue
        key = (
            norm(item.get('title')),
            norm(item.get('commune')),
            rent_value,
            round(surface_value, 1),
            item.get('rooms') or '',
        )
        if key[0] and key[1]:
            buckets[key].append(item)

    groups = []
    for key, members in buckets.items():
        sources = sorted(set(str(x.get('source') or '') for x in members))
        if len(members) < 2 or len(sources) < 2:
            continue

        def rank(x):
            return (
                source_priority.get(str(x.get('source') or ''), 99),
                0 if x.get('image') else 1,
                -len(str(x.get('description') or '')),
                str(x.get('id') or ''),
            )

        canonical = sorted(members, key=rank)[0]
        canonical_id = canonical.get('id')
        member_ids = [x.get('id') for x in members]
        for item in members:
            item['canonical_display_id'] = canonical_id
            item['dedup_decision'] = 'auto_duplicate' if item is not canonical else 'canonical'
            item['dedup_reason'] = 'doublon fort: même titre, commune, loyer, surface et pièces sur plusieurs sources'
            if item is not canonical:
                item['display_canonical'] = False
        groups.append({'canonical_id': canonical_id, 'member_ids': member_ids, 'sources': sources, 'signature': key})

    visible = [item for item in listings if item.get('display_canonical') is not False]
    return visible, {
        'policy': 'V2 exact-signature strong duplicates only; suspects/near matches stay visible',
        'groups': len(groups),
        'hidden_rows': len(listings) - len(visible),
        'examples': groups[:20],
    }


def coverage_from_feed(now, listings):
    active = [x for x in listings if x.get('active')]
    communes = Counter(x.get('commune') or 'Non renseignée' for x in active)
    regions = Counter('Nord-Est' for _ in active)
    precision = Counter(x.get('location_precision_label') or x.get('location_precision') or 'Inconnue' for x in active)
    rents = [as_int(x.get('rent')) for x in active if as_int(x.get('rent')) is not None]
    surfaces = [as_float(x.get('surface')) for x in active if as_float(x.get('surface')) is not None]
    return {
        'generated_at': now.isoformat(),
        'population': 'feed.json listings actives apres filtres produit',
        'count': len(active),
        'cities': dict(communes.most_common()),
        'regions': dict(regions.most_common()),
        'geo_confidence': {'commune': precision.get('Commune seule', 0), 'by_label': dict(precision.most_common())},
        'price_min': min(rents) if rents else None,
        'price_max': max(rents) if rents else None,
        'surface_min': min(surfaces) if surfaces else None,
        'surface_max': max(surfaces) if surfaces else None,
    }


def load_llm_extractions(c):
    """Return latest grounded LLM extraction per listing.

    The table is optional: fresh deployments and test DBs may not have it yet.
    Values are only used when their input_hash still matches the current source
    text, so stale extraction never overrides a newer description.
    """
    latest = {}
    try:
        rows = c.execute(
            '''
            SELECT source_site, source_id, extracted_at, model, input_hash, fields_json
            FROM listing_llm_extraction
            WHERE COALESCE(grounded, 1)=1
            ORDER BY extracted_at ASC
            '''
        )
    except sqlite3.OperationalError:
        return latest
    for r in rows:
        try:
            fields = json.loads(r['fields_json'] or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(fields, dict):
            continue
        latest[(r['source_site'], str(r['source_id']))] = {
            'model': r['model'],
            'extracted_at': r['extracted_at'],
            'input_hash': r['input_hash'],
            'fields': fields,
        }
    return latest


def as_int(v):
    if v in (None, ''):
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def as_float(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_price_changes(since_iso):
    """Dernier price_changed utile par annonce, lu dans l'historique.

    Garde produit côté export: on ne prépare que les baisses. La garde finale
    `new == rent courant` reste appliquée au moment d'émettre le listing, car
    elle dépend du prix affiché par l'annonce active.
    """
    latest = {}
    if not os.path.exists(EVENTS_DB):
        return latest
    h = None
    try:
        h = sqlite3.connect('file:%s?mode=ro' % EVENTS_DB, uri=True)
        h.row_factory = sqlite3.Row
        rows = h.execute(
            '''
            SELECT event_id, listing_id, event_at, old_value, new_value
            FROM listing_events
            WHERE event_type='price_changed' AND event_at >= ?
            ORDER BY event_at ASC, event_id ASC
            ''',
            (since_iso,),
        ).fetchall()
    except sqlite3.Error:
        return latest
    finally:
        try:
            if h:
                h.close()
        except Exception:
            pass
    for r in rows:
        old = as_int(r['old_value'])
        new = as_int(r['new_value'])
        if old is None or new is None or new >= old:
            continue
        # Garde anti-mensonge: certains scrapers basculent de loyer total vers
        # charges/montant partiel et créent de fausses "baisses" de plusieurs
        # milliers d'euros (ex. 3450 -> 450). Une baisse >50% est trop fragile
        # pour être affichée comme opportunité sans validation humaine.
        if old > 0 and ((old - new) / old) > 0.50:
            continue
        latest[str(r['listing_id'])] = {
            'ancien': old,
            'new': new,
            'delta': new - old,
            'depuis': str(r['event_at'])[:10],
            'event_id': r['event_id'],
        }
    return latest


def market_counts_from_history():
    """Signal marche des 7 derniers jours issu directement de listing_events."""
    if not os.path.exists(EVENTS_DB):
        return {'retirees_7j': 0}
    since_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    h = None
    try:
        h = sqlite3.connect('file:%s?mode=ro' % EVENTS_DB, uri=True)
        h.row_factory = sqlite3.Row
        row = h.execute(
            '''
            SELECT COUNT(*) AS n
            FROM listing_events
            WHERE event_type='disappeared' AND event_at >= ?
            ''',
            (since_iso,),
        ).fetchone()
    except sqlite3.Error:
        return {'retirees_7j': 0}
    finally:
        try:
            if h:
                h.close()
        except Exception:
            pass
    return {'retirees_7j': int(row['n'] if row else 0)}

def load_movement_events(since_iso):
    """Recent transitions stay separate from the active-only inventory."""
    if not os.path.exists(EVENTS_DB):
        return []
    h = None
    try:
        h = sqlite3.connect('file:%s?mode=ro' % EVENTS_DB, uri=True)
        h.row_factory = sqlite3.Row
        rows = h.execute(
            '''
            SELECT e.event_id, e.listing_id, e.event_type, e.event_at,
                   e.details_json, c.raw_json
            FROM listing_events e
            LEFT JOIN listing_current c ON c.id=e.listing_id
            WHERE e.event_type IN ('new','disappeared','reappeared')
              AND e.event_at >= ?
            ORDER BY e.event_at DESC, e.event_id DESC
            ''', (since_iso,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if h:
            h.close()
    events = []
    for row in rows:
        try:
            item = json.loads(row['raw_json'] or '{}')
        except (TypeError, ValueError):
            item = {}
        try:
            details = json.loads(row['details_json'] or '{}')
        except (TypeError, ValueError):
            details = {}
        item = dict(item)
        if not evaluate_publication(item).eligible:
            continue

        item.setdefault('rent', item.get('rent_eur', item.get('price')))
        item.setdefault('surface', item.get('surface_m2'))
        item.setdefault('image', item.get('image_url'))
        item.setdefault('id', row['listing_id'])
        item.setdefault('title', details.get('title'))
        item.setdefault('url', details.get('url'))
        item.update({'event_id': row['event_id'], 'event_type': row['event_type'],
                     'event_at': row['event_at']})
        events.append(item)
    return events


def commune_of(city_norm, enrich_city):
    for c in COMMUNES:
        if norm(enrich_city) == norm(c) or city_norm == norm(c):
            return c
    # Avant : on renvoyait enrich_city tel quel en dernier repli, ce qui
    # affichait des communes hors perimetre (ex. "Plaine Des Cafres") comme
    # si elles etaient valides. On tente d'abord un signal fiable (alias
    # commune, code postal) via geo_quartiers ; sinon on dit franchement
    # qu'on ne sait pas plutot que d'inventer une commune.
    return None


def bathroom_state(detail: dict) -> dict:
    """Public, non-filtering bathroom display state.

    Moufadal arbitration 2026-07-30: a bathtub is a preference, never an
    exclusion criterion. We only expose what we know so cards can display one of
    four human states without inventing a filter.
    """
    bathtub = detail.get('bathtub')
    nb_sdb = detail.get('nb_sdb')
    if bathtub == 1:
        return {'state': 'baignoire', 'label': 'baignoire'}
    if bathtub == 0:
        return {'state': 'douche_seulement', 'label': 'douche seulement'}
    if nb_sdb:
        return {'state': 'salle_de_bain_equipement_inconnu', 'label': 'salle de bain, équipement non précisé'}
    return {'state': 'non_precise', 'label': 'non précisé'}


# Valeurs qui ne veulent rien dire : elles ne doivent pas devenir un libelle.
# NB : norm() remplace les separateurs par des TIRETS -- ces cles doivent donc
# etre ecrites avec des tirets, sinon la comparaison echoue silencieusement.
VIDES = {'zone-non-precisee', 'non-precisee', 'non-precise', 'region-non-precisee',
         'non-renseigne', 'autre', 'inconnu', 'na', ''}


def quartier_propre(brut, commune):
    """Les portails stockent le quartier sous la forme
    'Saint-Denis - Providence - Les Camelias'. On ne garde que la partie
    QUARTIER (la commune est deja affichee a cote), et on jette les
    valeurs vides deguisees en libelle."""
    if not brut:
        return None
    s = str(brut).strip()
    if norm(s) in VIDES:
        return None
    if ' - ' in s:
        bouts = [b.strip() for b in s.split(' - ') if b.strip()]
        # on retire les bouts qui sont la commune elle-meme
        bouts = [b for b in bouts if norm(b) != norm(commune)]
        if not bouts:
            return None
        # le dernier bout est le plus precis, mais on garde au plus 2 niveaux
        s = ' · '.join(bouts[-2:]) if len(bouts) > 1 else bouts[0]
    if norm(s) == norm(commune) or norm(s) in VIDES:
        return None
    return s


def main():
    c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    c.row_factory = sqlite3.Row

    detail = {}
    try:
        for r in c.execute('select * from listing_detail'):
            detail[(r['source_site'], r['source_id'])] = dict(r)
    except sqlite3.OperationalError:
        pass

    # --- PHOTOS : uniquement nos copies locales. Decision Moufadal 27/07 :
    # l'app ne doit JAMAIS dependre d'un lien externe. Preuve du besoin : 35
    # photos zimo renvoient desormais un placeholder de 266 o a la source.
    # Le manifeste est produit par scripts/cache_photos.py (idempotent).
    # Les chemins sont ABSOLUS : l'app est servie depuis /v2/ aujourd'hui et
    # depuis / demain -- un chemin relatif casserait a la bascule.
    thumbs = {}
    galeries_man = {}
    try:
        man = json.loads(app_file('photos_manifest.json').read_text(encoding='utf-8'))
        for cle, v in (man.get('photos') or {}).items():
            site, _, sid = cle.partition(':')
            k = (site, sid)
            loc = v.get('local')
            if loc and local_media_exists(loc):
                thumbs[k] = loc
            locs = ['/' + str(u).lstrip('/') for u in (v.get('locals') or [])
                    if u and local_media_exists(u)]
            if len(locs) > 1:
                # Nouvelle source canonique de galerie : manifeste local,
                # produit par cache_photos.py depuis listing_detail.photo_urls.
                galeries_man[k] = locs
    except (OSError, ValueError):
        pass
    # Filet : anciennes vignettes referencees par le pipeline precedent et pas
    # encore reprises par le manifeste. On ne perd rien de ce qui existe deja.
    # Trouve le 27/07 (soir), remonte par Moufadal apres coup ("je pouvais pas
    # toutes les regarder") : ce meme fichier legacy contient DEJA une galerie
    # multi-photos par annonce (local_image_urls, alimentee chaque jour par
    # enrich_listing_galleries.py) -- jamais reprise par ce pipeline-ci, qui
    # n'a toujours servi qu'UNE photo. On la recupere ici, sans y toucher.
    galleries = {}
    try:
        old = json.loads(app_file('listings.json').read_text(encoding='utf-8'))
        items = old if isinstance(old, list) else (old.get('items') or old.get('listings') or [])
        for it in items:
            k2 = (it.get('source'), str(it.get('source_id')))
            u = it.get('local_image_url')
            if k2 not in thumbs and u and local_media_exists(u):
                thumbs[k2] = '/' + u.lstrip('/')
            urls = it.get('local_image_urls')
            if isinstance(urls, list) and len(urls) > 1:
                ok = ['/' + u2.lstrip('/') for u2 in urls
                      if u2 and local_media_exists(u2)]
                if len(ok) > 1:
                    galleries[k2] = ok
    except (OSError, ValueError):
        pass

    dist = {}
    try:
        with open(DIST, encoding='utf-8') as f:
            dist = json.load(f).get('quartiers', {})
    except (OSError, ValueError):
        pass
    dist_norm = {norm(k): v for k, v in dist.items()}

    def trajet(l):
        """Temps de trajet voiture. On dit TOUJOURS si c'est mesure ou estime."""
        if l.get('lat') and l.get('lon'):
            # coordonnees reelles : on approche par le quartier le plus proche
            best, bd = None, 1e9
            for k, v in dist.items():
                d = ((v['lat'] - l['lat']) ** 2 + (v['lon'] - l['lon']) ** 2) ** 0.5
                if d < bd:
                    best, bd = v, d
            if best and bd < 0.045:  # ~5 km
                return {'minutes': best['minutes'], 'km': best['km'], 'estime': False}
        # Les libelles nettoyes peuvent etre composes ("Bois de Nefles · Sainte-Clotilde")
        # et la commune seule n'est pas une cle de la table (qui contient
        # "Saint-Denis centre"). On essaie donc chaque morceau, puis la commune,

        # puis "<commune> centre".
        essais = []
        for champ in ('quartier', 'location_label'):
            v = l.get(champ)
            if v:
                essais.extend(m.strip() for m in str(v).split('·'))
                essais.append(v)
        if l.get('commune'):
            essais.append(l['commune'])
            essais.append('%s centre' % l['commune'])
        for e in essais:
            v = dist_norm.get(norm(e))
            if v:
                return {'minutes': v['minutes'], 'km': v['km'], 'estime': True}
        return None

    enrich = {}
    for r in c.execute('select * from listing_product_enrichment'):
        enrich[(r['source_site'], r['source_id'])] = dict(r)
    llm_extractions = load_llm_extractions(c)

    now = datetime.now(timezone.utc)
    d7 = (now - timedelta(days=7)).isoformat()
    d14 = (now - timedelta(days=14)).isoformat()
    d30 = (now - timedelta(days=30)).isoformat()
    price_changes = load_price_changes(d14)
    market_history = market_counts_from_history()

    listings = []
    hors_perimetre = 0
    filtres_produit = Counter()
    diagnostics_actifs = Counter()
    reconciliation_active_input = 0
    reconciliation_excluded_ids = {}

    def compter_exclusion(regle, listing):
        filtres_produit[regle] += 1
        if listing.get('active'):
            filtres_produit[regle + '_actives'] += 1
            identity = str(listing.get('id') or '')
            if identity and identity not in reconciliation_excluded_ids:
                decision = evaluate_publication(listing)
                reconciliation_excluded_ids[identity] = (
                    decision.reason if not decision.eligible else regle
                )

    for r in c.execute('select * from rental_listings order by seen_last_at desc'):
        k = (r['source_site'], r['source_id'])
        e = enrich.get(k, {})
        d = detail.get(k, {})
        row_active = bool(r['is_active'])
        row_identity = '%s:%s' % (r['source_site'], r['source_id'])
        if row_active:
            reconciliation_active_input += 1

        # Trouve le 27/07 (soir) : les scrapers n'ont pas de filtre commune a
        # la source (ex. zimo/citya ramenent toute l'ile) -- la base peut donc
        # contenir des annonces hors Nord+Est en attendant le prochain passage
        # de scripts/scope/purge_nord_est.py. On ne les affiche jamais dans le
        # feed public (mais on ne les supprime pas de la base ici : lecture
        # seule, rien d'irreversible).
        if gq.looks_out_of_scope(r['city'], r['district'], e.get('city_normalized'), e.get('zone_normalized')):
            hors_perimetre += 1
            if row_active:
                normalized_city = e.get('city_normalized')
                if not normalized_city or norm(normalized_city) in VIDES:
                    normalized_city = r['city']
                normalized_zone = e.get('zone_normalized') or r['district']
                policy_input = {
                    'surface_m2': r['surface_m2'],
                    'rent_eur': r['rent_eur'],
                    'commune': normalized_city,
                    'quartier': normalized_zone,
                    'property_type': e.get('property_type_normalized') or r['property_type'],
                    'title': r['title'],
                    'description': d.get('description_full') or r['description'],
                    'residential': (
                        bool(e.get('is_residential'))
                        if e.get('is_residential') is not None
                        else None
                    ),
                }
                decision = evaluate_publication(policy_input)
                reconciliation_excluded_ids[row_identity] = (
                    decision.reason if not decision.eligible else 'manifest_outside_scope'
                )
            continue

        lat, lon = d.get('lat'), d.get('lon')
        commune = commune_of(norm(r['city']), e.get('city_normalized'))
        if not commune:
            # Repli 27/07 : la commune declaree par le portail est parfois
            # vide/mal formee (code postal seul, "individuel Saint-Denis",
            # nom de quartier sans la commune...). On cherche un signal
            # commune non ambigu dans les champs bruts + le texte de
            # l'annonce avant d'abandonner.
            commune = gq.infer_commune(
                r['city'], r['district'], e.get('city_normalized'),
                e.get('zone_normalized'), r['title'])
        quartier = (quartier_propre(e.get('zone_normalized'), commune)
                    or quartier_propre(r['district'], commune)
                    or gq.infer_quartier(
                        commune, r['title'],
                        d.get('description_full') or r['description'],
                        r['district'], e.get('zone_normalized')))
        source_text = d.get('description_full') or r['description'] or ''
        description_quality = description_quality_payload(source_text, d, r['seen_last_at'])
        llm = llm_extractions.get((r['source_site'], str(r['source_id'])))
        llm_fields = None
        llm_status = 'absent'
        if llm:
            if llm.get('input_hash') == llm_input_hash(source_text):
                fields = llm.get('fields') or {}
                llm_fields = {
                    'quartier_precis': fields.get('quartier_precis'),
                    'proximites': fields.get('proximites') if isinstance(fields.get('proximites'), list) else [],
                    'routes_axes': fields.get('routes_axes') if isinstance(fields.get('routes_axes'), list) else [],
                    'points_repere': fields.get('points_repere') if isinstance(fields.get('points_repere'), list) else [],
                    'model': llm.get('model'),
                    'extracted_at': llm.get('extracted_at'),
                    'input_hash': llm.get('input_hash'),
                }
                llm_status = 'fresh'
                if not quartier and fields.get('quartier_precis'):
                    quartier = fields.get('quartier_precis')
            else:
                llm_status = 'stale'

        # --- localisation : on prend le SIGNAL LE PLUS PRECIS disponible, et on
        # dit toujours d'ou il vient. Jamais d'invention.
        prec = d.get('precision') or 'inconnu'
        label = None
        if d.get('address'):
            label = d['address']
        elif d.get('street'):
            label = d['street']
        elif d.get('residence'):
            label = 'Résidence %s' % d['residence']
        elif quartier:
            label = quartier
            prec = 'quartier'
        elif commune and norm(commune) not in VIDES:
            label = commune
            prec = 'commune'
        else:
            label = None
            prec = 'inconnu'

        k = (r['source_site'], str(r['source_id']))
        pc = price_changes.get('%s:%s' % (r['source_site'], r['source_id']))
        changement_prix = None
        rent_current = as_int(r['rent_eur'])
        if pc and rent_current is not None and pc['new'] == rent_current and bool(r['is_active']):
            changement_prix = {
                'ancien': pc['ancien'],
                'delta': pc['delta'],
                'depuis': pc['depuis'],
            }
        try:
            residential_reasons = json.loads(e.get('residential_reasons_json') or '[]')
        except (TypeError, ValueError):
            residential_reasons = []
        raw_residential = bool(e.get('is_residential', 1))
        weak_non_residential_flag = (
            not raw_residential
            and residential_flag_looks_like_amenity_false_positive(r['title'], residential_reasons)
        )
        public_residential = raw_residential or weak_non_residential_flag

        listing = {
            'id': '%s:%s' % (r['source_site'], r['source_id']),
            'source': r['source_site'],
            'url': r['canonical_url'] or r['url'],
            'title': r['title'],
            'active': bool(r['is_active']),
            'seen_first': r['seen_first_at'],
            'seen_last': r['seen_last_at'],
            'published': normalize_published_at(r['published_at'], r['seen_first_at']),
            # prix / surface
            'rent': r['rent_eur'],
            'changement_prix': changement_prix,
            'charges': d.get('charges_eur') if d.get('charges_eur') is not None else r['charges_eur'],
            'surface': r['surface_m2'],
            'rooms': r['rooms'],
            'bedrooms': d.get('bedrooms') if d.get('bedrooms') is not None else r['bedrooms'],
            'type': e.get('property_type_normalized') or r['property_type'],
            'residential': public_residential,
            'residential_raw': raw_residential,
            'canonical': bool(e.get('is_canonical', 1)),
            'agency': r['agency_or_owner'],
            # localisation — le coeur de la demande du 27/07
            'commune': commune if commune and norm(commune) not in VIDES else None,
            'quartier': quartier,
            'location_label': label,
            'location_precision': prec,
            'location_precision_rank': PRECISION_RANK.get(prec, 0),
            'location_precision_label': PRECISION_LABEL.get(prec, prec),
            'location_source': d.get('geo_source'),
            'lat': lat,
            'lon': lon,
            'address': d.get('address'),
            'street': d.get('street'),
            'residence': d.get('residence'),
            # criteres fins, enfin disponibles
            'floor': d.get('floor'),
            'elevator': d.get('has_elevator'),
            'bathtub': d.get('bathtub'),
            'bathroom': bathroom_state(d),
            'furnished': d.get('furnished'),
            # confort interieur (demande du 27/07)
            'nb_sdb': d.get('nb_sdb'), 'nb_wc': d.get('nb_wc'),
            'wc_separe': d.get('wc_separe'), 'niveaux': d.get('niveaux'),
            'jardin': d.get('jardin'), 'veranda': d.get('veranda'),
            'terrasse': d.get('terrasse'), 'parking': d.get('parking'),
            'piscine': d.get('piscine'), 'clim': d.get('clim'),
            # media / texte — UNIQUEMENT notre copie locale. Pas de repli sur
            # l'URL distante : un lien qui meurt chez le portail (cas zimo) ne
            # doit plus jamais casser une carte.
            'image': thumbs.get(k),
            'image_locale': k in thumbs,
            'images': galeries_man.get(k) or galleries.get(k)
                      or ([thumbs[k]] if k in thumbs else []),
            'description': source_text,
            'description_quality': description_quality,
            'detail_fetched': bool(d.get('http_status') == 200),
            # HTTP 200 alone is transport evidence; structural proof is required.
            'detail_read': description_detail_read(d, description_quality),
            'llm_extraction': llm_fields,
            'llm_extraction_status': llm_status,
        }

        if listing['active']:
            diagnostics_actifs['depart_actives'] += 1
            if norm(listing.get('commune')) == norm('Saint-Denis'):
                diagnostics_actifs['saint_denis_depart'] += 1
                if norm(listing.get('quartier')) == norm('Sainte-Marie'):
                    diagnostics_actifs['saint_denis_quartier_sainte_marie'] += 1
                q_diag = excluded_quartier_from_field(listing.get('quartier'))
                if q_diag:
                    diagnostics_actifs['saint_denis_quartier_champ'] += 1
                    diagnostics_actifs['saint_denis_quartier_champ:' + q_diag] += 1
                elif excluded_quartier_from_description(
                        listing.get('title'), listing.get('description'), listing.get('location_label')):
                    diagnostics_actifs['saint_denis_quartier_description'] += 1
            if norm(listing.get('commune')) in EXCLUDED_COMMUNE_NORMS:
                diagnostics_actifs['commune_saint_andre'] += 1
            if listing['residential_raw'] is False:
                diagnostics_actifs['non_residentiel_flag_brut'] += 1
            if weak_non_residential_flag:
                diagnostics_actifs['non_residentiel_flag_faible_garde'] += 1
            if listing['residential'] is False:
                diagnostics_actifs['non_residentiel_servi_exclu'] += 1
            violation = public_rule_violation(listing)
            if violation:
                diagnostics_actifs[violation] += 1

        # --- filtres produit publics (non destructifs DB, mais annonces non servies)
        # Ordre volontaire: chaque annonce est comptee dans la premiere regle qui
        # l'ecarte, pour que le rapport ne mente pas par double comptage.
        if listing['residential'] is False:
            compter_exclusion('non_residentiel', listing)
            continue
        violation = public_rule_violation(listing)
        if violation:
            compter_exclusion(violation, listing)
            continue
        if norm(listing.get('commune')) in EXCLUDED_COMMUNE_NORMS:
            compter_exclusion('commune_saint_andre', listing)
            continue
        if norm(listing.get('commune')) == norm('Saint-Denis'):
            q_exclu = excluded_quartier_from_field(listing.get('quartier'))
            if q_exclu:
                compter_exclusion('saint_denis_quartier_champ', listing)
                compter_exclusion('saint_denis_quartier_champ:' + q_exclu, listing)
                continue
            q_desc = excluded_quartier_from_description(
                listing.get('title'), listing.get('description'), listing.get('location_label'))
            if q_desc:
                compter_exclusion('saint_denis_quartier_description', listing)
                compter_exclusion('saint_denis_quartier_description:' + q_desc, listing)
                continue

        publication = evaluate_publication(listing)
        if not publication.eligible:
            compter_exclusion(publication.reason or 'publication_policy', listing)
            continue

        listings.append(listing)

    reconciliation_eligible = list(active_public_listings(listings))
    # --- trajet + score par profil ---
    for l in listings:
        l['trajet'] = trajet(l)
        l['profils'] = {}
        for cle in PROFILS:
            s = scorer(l, cle)
            # on ne rattache l'annonce a un profil que si elle a un sens pour lui
            if s['score'] >= 45:
                l['profils'][cle] = s
        l['meilleur_profil'] = (
            max(l['profils'].items(), key=lambda kv: kv[1]['score'])[0]
            if l['profils'] else None)
        l['meilleur_score'] = (
            l['profils'][l['meilleur_profil']]['score'] if l['meilleur_profil'] else 0)
        j = (datetime.fromisoformat(l['seen_first']).astimezone(timezone.utc)
             if l['seen_first'] else None)
        l['fraiche'] = bool(j and (now - j).days <= 3)

    # --- mouvements : ce que Moufadal veut voir sans se prendre la tete
    new7 = [x for x in listings if (x['seen_first'] or '') >= d7]
    gone = [x for x in listings if not x['active']]
    gone7 = [x for x in gone if (x['seen_last'] or '') >= d7]
    movement_events = load_movement_events((now - timedelta(days=30)).isoformat())

    movements = {
        'nouvelles_7j': sum(1 for x in movement_events if x['event_type'] == 'new' and x['event_at'] >= d7),
        'disparues_total': len(gone),
        'reapparues_7j': sum(1 for x in movement_events if x['event_type'] == 'reappeared' and x['event_at'] >= d7),
        'events': movement_events,
        'disparues_7j': sum(1 for x in movement_events if x['event_type'] == 'disappeared' and x['event_at'] >= d7),
        'actives': sum(1 for x in listings if x['active']),
    }
    # Do not mix withdrawn inventory into the public availability feed. Historical
    # counts remain in `movements` and the dedicated changes/history artifacts.
    listings = active_public_listings(listings)
    listings, dedup_report = deduplicate_public_feed(
        listings,
        # Validate the files belonging to this exact candidate product. OUT is
        # redirected by build_product_v2 for transactional and temporary builds.
        photo_root=os.path.dirname(os.path.abspath(OUT)),
    )
    reconciliation_product = reconciliation_product_payload(
        reconciliation_active_input,
        reconciliation_excluded_ids,
        reconciliation_eligible,
        listings,
    )
    # Backward-compatible metadata for older audits/UI while the canonical
    # implementation lives in src.public_feed_dedup.
    dedup_display = {
        'policy': 'src.public_feed_dedup conservative high-confidence duplicates; ambiguity stays visible',
        'groups': dedup_report.get('groups', 0),
        'hidden_rows': dedup_report.get('hidden_duplicates', 0),
        'input': dedup_report.get('input', 0),
        'visible': dedup_report.get('visible', len(listings)),
    }
    movements['actives'] = len(listings)


    # --- sante des sources
    per_source = defaultdict(lambda: {'total': 0, 'actives': 0, 'detail_lu': 0, 'dernier': ''})
    for x in listings:
        s = per_source[x['source']]
        s['total'] += 1
        s['actives'] += 1 if x['active'] else 0
        s['detail_lu'] += 1 if x['detail_read'] else 0
        if (x['seen_last'] or '') > s['dernier']:
            s['dernier'] = x['seen_last']
    sources = [dict(nom=k, **v) for k, v in sorted(per_source.items())]

    prec_counts = Counter(x['location_precision'] for x in listings)
    description_counts = Counter(
        (x.get('description_quality') or {}).get('status', 'unknown')
        for x in listings
    )
    meta = {
        'genere_le': now.isoformat(),
        'perimetre': SERVE_COMMUNES,
        'hors_perimetre_exclues': hors_perimetre,
        'exclusions_produit': dict(filtres_produit),
        'diagnostics_actifs_avant_filtres': dict(diagnostics_actifs),
        'descriptions': dict(description_counts),
        'deduplication': dedup_report,
        'reconciliation_product': reconciliation_product,
        'total': len(listings),
        'actives': movements['actives'],
        'avec_point_carte': sum(1 for x in listings if x['lat']),
        'avec_trajet': sum(1 for x in listings if x['trajet']),
        'avec_photo_locale': sum(1 for x in listings if x['image']),
        'avec_llm_extraction': sum(1 for x in listings if x.get('llm_extraction_status') == 'fresh'),
        'llm_extraction_stale': sum(1 for x in listings if x.get('llm_extraction_status') == 'stale'),
        'actives_sans_photo': sum(1 for x in listings if x['active'] and not x['image']),
        'detail_lu': sum(1 for x in listings if x['detail_read']),
        'fraiches': sum(1 for x in listings if x['fraiche'] and x['active']),
        'marche': {
            'retirees_7j': movements['disparues_7j'],
            'nouvelles_7j': movements['nouvelles_7j'],
            'actives': movements['actives'],
        },
        'dedup_display': dedup_display,
        'precision': {PRECISION_LABEL.get(k, k): v for k, v in prec_counts.most_common()},
        'profils': {
            k: {'nom': v['nom'],
                'resume': '%d–%d € · ≥ %d m² · %d ch. min'
                          % (v['loyer_min'], v['loyer_max'], v['surface_min'],
                             v['chambres_min']),
                'correspondances': sum(1 for x in listings
                                       if x['active'] and k in x['profils'])}
            for k, v in PROFILS.items()
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    coverage_out = os.path.join(os.path.dirname(OUT), 'coverage.json')
    coverage = coverage_from_feed(now, listings)
    if coverage['count'] != movements['actives']:
        raise RuntimeError('coverage count mismatch: %s != %s' % (coverage['count'], movements['actives']))

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'listings': listings, 'movements': movements,
                   'sources': sources}, f, ensure_ascii=False, separators=(',', ':'))
    with open(coverage_out, 'w', encoding='utf-8') as f:
        json.dump(coverage, f, ensure_ascii=False, separators=(',', ':'))

    print('feed ecrit : %s (%.2f Mo)' % (OUT, os.path.getsize(OUT) / 1e6))
    print('coverage ecrit : %s (count=%d)' % (coverage_out, coverage['count']))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print('mouvements :', json.dumps(movements, ensure_ascii=False))


if __name__ == '__main__':
    main()
