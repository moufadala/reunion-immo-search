#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path('/opt/data/projects/reunion-immo-search')
DB = Path(os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db'))
OUTDIR = ROOT / 'artifacts' / 'v3-detail-recovery-20260625'
RAW_ROOT = Path('/opt/data/artifacts/realestate/multi_sources/raw')
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36'
BOILERPLATE_PATTERNS = [
    "L'annonce a bien été ajoutée à vos favoris",
    "Annonce publiée le",
    "Proposée par",
]
TARGET_SOURCES = ['superimmo', 'locamoi', 'domimmo', '97immo', 'citya', 'zimo', 'fnaim']


def utcstamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def clean_text(s: str | None) -> str:
    if not s:
        return ''
    s = html.unescape(str(s))
    s = s.replace('\u00a0', ' ').replace('\r', '\n')
    s = re.sub(r'<\s*br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n\s*\n+', '\n\n', s)
    return s.strip(' \n\t-')


# Trouve le 28/07 : les 90 annonces fnaim recuperees par la pagination du
# 27/07 ont TOUTES un gabarit generique auto-genere par fnaim.re lui-meme
# ("Annonce immobiliere de location <type> <pieces> pieces de € sur
# <ville> - avec <agence>"), 87-141 caracteres -- au-dessus du seuil de 80,
# donc jamais detecte comme "sparse" malgre son inutilite totale. C'est
# exactement le meme defaut deja identifie et corrige le 27/07 matin pour
# fnaim_description(), mais is_sparse() ne le reconnaissait pas comme
# candidat a l'enrichissement -> la fonction de description existait deja,
# elle n'etait simplement jamais appelee sur ces lignes.
_GABARIT_GENERIQUE = re.compile(r'^annonce immobili[eè]re de location', re.I)


def is_sparse(existing: str | None, title: str | None = None) -> bool:
    e = clean_text(existing)
    t = clean_text(title).lower()
    if not e:
        return True
    if len(e) < 80:
        return True
    if t and e.lower() == t:
        return True
    if "L'annonce a bien été ajoutée à vos favoris" in e:
        return True
    if _GABARIT_GENERIQUE.match(e):
        return True
    return False


def has_bad_boilerplate(s: str) -> bool:
    low = s.lower()
    return any(p.lower() in low for p in BOILERPLATE_PATTERNS)


def should_update(existing: str | None, new: str | None, title: str | None = None) -> tuple[bool, str]:
    e = clean_text(existing)
    n = clean_text(new)
    if len(n) < 80:
        return False, 'new_too_short'
    if has_bad_boilerplate(n):
        return False, 'new_has_boilerplate'
    # If existing text contains known scraper boilerplate, a shorter cleaned text is
    # still an improvement as long as it is substantial and boilerplate-free.
    if has_bad_boilerplate(e) and len(n) >= 80:
        return True, 'accepted_cleaned_boilerplate'
    if not is_sparse(e, title) and len(n) <= len(e) + 80:
        return False, 'existing_not_sparse_and_new_not_much_longer'
    if len(n) <= len(e):
        return False, 'new_not_longer'
    return True, 'accepted'


LLM_DEFAULT_PROVIDER = (os.environ.get('IMMO_LLM_PROVIDER') or ('openrouter' if os.environ.get('OPENROUTER_API_KEY') else 'anthropic')).strip().lower()


def default_llm_model(provider: str) -> str:
    env_model = os.environ.get('IMMO_LLM_MODEL')
    if env_model:
        return env_model
    provider = (provider or '').strip().lower()
    return 'anthropic/claude-haiku-4.5' if provider == 'openrouter' else 'claude-haiku-4-5-20251001'


LLM_DEFAULT_MODEL = default_llm_model(LLM_DEFAULT_PROVIDER)
LLM_LIST_FIELDS = ('proximites', 'routes_axes', 'points_repere')
LLM_SCALAR_FIELDS = ('quartier_precis',)
LLM_TOOL = {
    'name': 'extract_listing_signals',
    'description': 'Extrait uniquement les signaux explicitement présents dans le texte source dune annonce immobilière.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'quartier_precis': {'type': ['string', 'null'], 'description': 'Quartier/localité exact explicitement citée, sinon null.'},
            'proximites': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Écoles, commerces, transports, plage, services explicitement cités.'},
            'routes_axes': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Routes, axes, accès rapides explicitement cités.'},
            'points_repere': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Résidences, bâtiments, lieux-dits, repères explicitement cités.'},
        },
        'required': ['quartier_precis', 'proximites', 'routes_axes', 'points_repere'],
        'additionalProperties': False,
    },
}


def llm_input_hash(source_text: str) -> str:
    return hashlib.sha256(clean_text(source_text).encode('utf-8')).hexdigest()


def normalize_grounding_text(value: str | None) -> str:
    text = clean_text(value).lower()
    text = ''.join(ch for ch in unicodedata.normalize('NFKD', text) if not unicodedata.combining(ch))
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def is_grounded(value: str | None, source_text: str) -> bool:
    v = normalize_grounding_text(value)
    if not v:
        return False
    source_norm = normalize_grounding_text(source_text)
    # Boundary-aware phrase match: a hallucinated short token like "mer" must
    # not be accepted just because it is inside "commerce". This keeps the
    # anti-hallucination guard faithful to "littéralement présent".
    return re.search(r'(?<![a-z0-9])' + re.escape(v) + r'(?![a-z0-9])', source_norm) is not None


def llm_fields_have_values(fields: dict[str, Any]) -> bool:
    return any(bool(fields.get(k)) for k in (*LLM_SCALAR_FIELDS, *LLM_LIST_FIELDS))


def grounded_llm_fields(payload: dict[str, Any], source_text: str) -> tuple[dict[str, Any], list[str]]:
    accepted: dict[str, Any] = {'quartier_precis': None, 'proximites': [], 'routes_axes': [], 'points_repere': []}
    rejected: list[str] = []
    for field in LLM_SCALAR_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and is_grounded(value, source_text):
            accepted[field] = clean_text(value)
        elif value:
            rejected.append(str(value))
    for field in LLM_LIST_FIELDS:
        raw = payload.get(field) or []
        if not isinstance(raw, list):
            raw = []
        for value in raw:
            if isinstance(value, str) and is_grounded(value, source_text):
                cleaned = clean_text(value)
                if cleaned and cleaned not in accepted[field]:
                    accepted[field].append(cleaned)
            elif value:
                rejected.append(str(value))
    return accepted, rejected


def extract_tool_payload(response: Any) -> dict[str, Any]:
    # Anthropic native SDK shape used by the original implementation.
    content = response.get('content') if isinstance(response, dict) else getattr(response, 'content', None)
    for block in content or []:
        if isinstance(block, dict):
            typ = block.get('type')
            name = block.get('name')
            inp = block.get('input')
        else:
            typ = getattr(block, 'type', None)
            name = getattr(block, 'name', None)
            inp = getattr(block, 'input', None)
        if typ == 'tool_use' and name == 'extract_listing_signals' and isinstance(inp, dict):
            return inp

    # OpenRouter/OpenAI-compatible shape: choices[].message.tool_calls[].
    choices = response.get('choices') if isinstance(response, dict) else None
    for choice in choices or []:
        msg = choice.get('message') or {}
        for call in msg.get('tool_calls') or []:
            fn = call.get('function') or {}
            if fn.get('name') != 'extract_listing_signals':
                continue
            args = fn.get('arguments')
            if isinstance(args, dict):
                return args
            if isinstance(args, str):
                try:
                    parsed = json.loads(args)
                except Exception:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
    return {}


def response_usage(response: Any) -> dict[str, Any]:
    usage = response.get('usage') if isinstance(response, dict) else getattr(response, 'usage', None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        out = dict(usage)
        # Normalize OpenAI/OpenRouter names to the DB fields used by this script.
        if 'input_tokens' not in out and 'prompt_tokens' in out:
            out['input_tokens'] = out.get('prompt_tokens')
        if 'output_tokens' not in out and 'completion_tokens' in out:
            out['output_tokens'] = out.get('completion_tokens')
        return out
    return {k: getattr(usage, k) for k in ('input_tokens', 'output_tokens') if hasattr(usage, k)}


def build_llm_messages(row: Any, source_text: str) -> list[dict[str, Any]]:
    return [
        {
            'role': 'user',
            'content': (
                "Annonce immobilière Réunion. Extrais uniquement les informations "
                "littéralement présentes dans le texte. N'infère rien. Si absent: null ou [].\n\n"
                f"Source: {row['source_site']} / {row['source_id']}\n"
                f"Titre: {clean_text(row['title'])}\n"
                f"Ville connue: {clean_text(row['city'])}\n\n"
                f"Texte source:\n{clean_text(source_text)[:3500]}"
            ),
        }
    ]


def llm_extract_listing_signals(row: Any, source_text: str, *, client: Any = None, model: str = LLM_DEFAULT_MODEL) -> tuple[dict[str, Any], dict[str, Any]]:
    text = clean_text(source_text)
    if len(text) < 80:
        return {}, {'llm': 'skipped_no_text'}
    if client is None:
        return {}, {'llm': 'disabled', 'model': model, 'input_hash': llm_input_hash(text)}
    response = client.messages.create(
        model=model,
        max_tokens=512,
        temperature=0,
        tools=[LLM_TOOL],
        tool_choice={'type': 'tool', 'name': 'extract_listing_signals'},
        messages=build_llm_messages(row, text),
    )
    payload = extract_tool_payload(response)
    fields, rejected = grounded_llm_fields(payload, text)
    return fields, {'llm': 'ok', 'provider': getattr(client, 'provider_name', 'anthropic'), 'model': model, 'input_hash': llm_input_hash(text), 'usage': response_usage(response), 'rejected_ungrounded': rejected}


class OpenRouterLLMClient:
    provider_name = 'openrouter'

    def __init__(self, api_key: str, *, referer: str | None = None, title: str = 'reunion-immo-search'):
        self.api_key = api_key
        self.referer = referer or 'https://immo.148.230.103.174.sslip.io/'
        self.title = title
        self.messages = self

    @staticmethod
    def _openrouter_tool(tool: dict[str, Any]) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': tool['name'],
                'description': tool.get('description', ''),
                'parameters': tool.get('input_schema', {}),
            },
        }

    @staticmethod
    def _openrouter_tool_choice(choice: dict[str, Any]) -> dict[str, Any]:
        if choice.get('type') == 'tool':
            return {'type': 'function', 'function': {'name': choice.get('name')}}
        return choice

    def create(self, **kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)
        payload['tools'] = [self._openrouter_tool(t) for t in payload.get('tools', [])]
        if isinstance(payload.get('tool_choice'), dict):
            payload['tool_choice'] = self._openrouter_tool_choice(payload['tool_choice'])
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=body,
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'HTTP-Referer': self.referer,
                'X-OpenRouter-Title': self.title,
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode('utf-8', errors='replace')[:1000]
            raise RuntimeError(f'OpenRouter API error {exc.code}: {raw}') from exc


def create_llm_client(provider: str = LLM_DEFAULT_PROVIDER) -> Any:
    provider = (provider or '').lower()
    if provider == 'openrouter':
        key = os.environ.get('OPENROUTER_API_KEY')
        if not key:
            raise RuntimeError('OPENROUTER_API_KEY missing; refusing OpenRouter LLM live calls')
        return OpenRouterLLMClient(key)
    if provider == 'anthropic':
        try:
            from anthropic import Anthropic  # type: ignore
        except Exception as exc:
            raise RuntimeError('anthropic SDK missing; run with --llm-dry-run or use IMMO_LLM_PROVIDER=openrouter') from exc
        if not os.environ.get('ANTHROPIC_API_KEY'):
            raise RuntimeError('ANTHROPIC_API_KEY missing; refusing Anthropic LLM live calls')
        client = Anthropic()
        setattr(client, 'provider_name', 'anthropic')
        return client
    raise RuntimeError(f'Unsupported IMMO_LLM_PROVIDER: {provider!r}')


def ensure_llm_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS listing_llm_extraction (
            source_site TEXT NOT NULL,
            source_id TEXT NOT NULL,
            extracted_at TEXT NOT NULL,
            model TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            fields_json TEXT NOT NULL,
            grounded INTEGER NOT NULL DEFAULT 1,
            input_tokens INTEGER,
            output_tokens INTEGER,
            PRIMARY KEY (source_site, source_id, model)
        )
        """
    )


def get_cached_llm_extraction(con: sqlite3.Connection, row: Any, *, model: str, input_hash: str) -> dict[str, Any] | None:
    try:
        hit = con.execute(
            """
            SELECT fields_json, input_hash, extracted_at, input_tokens, output_tokens
            FROM listing_llm_extraction
            WHERE source_site=? AND source_id=? AND model=?
            """,
            (row['source_site'], row['source_id'], model),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not hit or hit['input_hash'] != input_hash:
        return None
    try:
        fields = json.loads(hit['fields_json'] or '{}')
    except json.JSONDecodeError:
        return None
    if not isinstance(fields, dict):
        return None
    return {
        'fields': fields,
        'meta': {
            'llm': 'cached',
            'model': model,
            'input_hash': input_hash,
            'extracted_at': hit['extracted_at'],
            'usage': {'input_tokens': hit['input_tokens'], 'output_tokens': hit['output_tokens']},
        },
    }


def upsert_llm_extraction(con: sqlite3.Connection, row: Any, *, model: str, fields: dict[str, Any], meta: dict[str, Any], extracted_at: str) -> None:
    usage_raw = meta.get('usage')
    usage: dict[str, Any] = usage_raw if isinstance(usage_raw, dict) else {}
    con.execute(
        """
        INSERT INTO listing_llm_extraction(source_site, source_id, extracted_at, model, input_hash, fields_json, grounded, input_tokens, output_tokens)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_site, source_id, model) DO UPDATE SET
            extracted_at=excluded.extracted_at,
            input_hash=excluded.input_hash,
            fields_json=excluded.fields_json,
            grounded=excluded.grounded,
            input_tokens=excluded.input_tokens,
            output_tokens=excluded.output_tokens
        """,
        (
            row['source_site'], row['source_id'], extracted_at, model, meta.get('input_hash', ''),
            json.dumps(fields, ensure_ascii=False, sort_keys=True), 1,
            usage.get('input_tokens'), usage.get('output_tokens'),
        ),
    )


def request_text(url: str, *, referer: str | None = None, timeout: int = 25) -> tuple[int, str]:
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7',
        'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
    }
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1_500_000)
        charset = resp.headers.get_content_charset() or 'utf-8'
        return resp.status, raw.decode(charset, 'replace')


def extract_jsonld(html_text: str) -> list[Any]:
    out = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, re.I | re.S):
        body = html.unescape(m.group(1)).strip()
        try:
            data = json.loads(body)
        except Exception:
            continue
        if isinstance(data, list):
            out.extend(data)
        else:
            out.append(data)
    return out


def extract_meta_description(html_text: str) -> str:
    for rx in [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']',
    ]:
        m = re.search(rx, html_text, re.I | re.S)
        if m:
            return clean_text(m.group(1))
    return ''


def raw_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def superimmo_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    raw = raw_json(row['raw_json_path'])
    text = clean_text(raw.get('text') or '')
    if not text:
        return '', {'method': 'raw_text_missing'}
    # Cut the known card/listing boilerplate and keep the prose after the location token.
    candidates = []
    for pat in [
        r'\([0-9]{5}\)\s+(.+)$',
        r'(?:Appartement|Maison|Villa|Studio)\s*[•-][^\n]{0,120}\)\s+(.+)$',
        r'\b[0-9 ]+\s*€\s*(?:CC|HC)?\s+(?:Appartement|Maison|Villa|Studio)[^A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]{0,10}(.+)$',
    ]:
        m = re.search(pat, text, re.I | re.S)
        if m:
            candidates.append(clean_text(m.group(1)))
    # Fallback: strip up to the second source_id/title-ish location if possible.
    for marker in ['Beau ', 'A louer ', 'À louer ', 'Dans ', 'Situé ', 'Studio ', 'Appartement ']:
        idx = text.find(marker)
        if idx > 80:
            candidates.append(clean_text(text[idx:]))
    candidates = [c for c in candidates if len(c) >= 80 and not has_bad_boilerplate(c)]
    best = max(candidates, key=len, default='')
    return best, {'method': 'superimmo_raw_clean', 'raw_len': len(text)}


def locamoi_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    status, body = request_text(row['url'], referer='https://locamoi.fr/')
    best = ''
    images: list[str] = []
    for data in extract_jsonld(body):
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            typ = item.get('@type')
            if typ == 'RealEstateListing' or 'RealEstate' in str(typ):
                best = clean_text(item.get('description') or best)
                img = item.get('image')
                if isinstance(img, list):
                    images.extend(str(x) for x in img if x)
                elif img:
                    images.append(str(img))
    if not best:
        best = extract_meta_description(body)
    best = re.sub(r'\s*Voir moins\s*$', '', best).strip()
    # Locamoi JSON-LD sometimes duplicates exact paragraph around "Voir moins".
    if len(best) > 200:
        half = len(best) // 2
        if SequenceMatcher(None, best[:half], best[half:]).ratio() > 0.88:
            best = best[:half].strip()
    return best, {'method': 'locamoi_jsonld', 'http_status': status, 'image_count': len(set(images))}


def domimmo_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    def clean_domimmo_desc(s: str) -> str:
        text = clean_text(s)
        # Domimmo/Keldom can append agency/legal syndication footer. Keep source
        # prose above it instead of rejecting the whole detail description.
        footers = [
            'Cette offre de location est proposée',
            'Cette annonce vous est proposée par',
            'Extrait de notre barème',
            'Les informations sur les risques auxquels ce bien est exposé',
            'Géorisques',
        ]
        for marker in footers:
            pos = text.find(marker)
            if pos > 120:
                text = text[:pos].strip()
        return clean_text(text)

    def fetch(params: dict[str, str]) -> tuple[int, list[Any], str]:
        url = 'https://www.keldom.com/api/domimmo/offers?' + urllib.parse.urlencode(params)
        status, body = request_text(url, referer='https://www.domimmo.com/')
        payload = json.loads(body)
        if isinstance(payload, list):
            data = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get('items'), list):
                data = payload['items']
            elif isinstance(payload.get('data'), list):
                data = payload['data']
            else:
                data = [payload]
        else:
            return status, [], 'unsupported_schema'
        return status, data, url

    title = clean_text(row['title'])
    expected_id = str(row['source_id'])
    price = row['rent_eur']
    surf = row['surface_m2']
    attempts: list[dict[str, Any]] = []

    # Exact ID is the most reliable Keldom/Domimmo lookup. Keyword-only search can
    # miss exact listings or rank a wrong generic "Location Appartement 2 pièces".
    query_plan = [
        {'id': expected_id},
        {'motscles': title, 'limit': '8'},
    ]
    # If title search is too generic, add price/city as extra probes. Keldom
    # ignores unknown params harmlessly; scoring below still protects updates.
    if row['city']:
        query_plan.append({'motscles': f"{title} {row['city']}", 'limit': '12'})
    if price is not None:
        query_plan.append({'motscles': str(int(float(price))), 'limit': '12'})

    best = None
    best_score = -1.0
    best_status = None
    total_matches = 0
    for params in query_plan:
        try:
            status, data, used_url = fetch(params)
        except Exception as e:
            attempts.append({'params': params, 'error': repr(e)})
            continue
        total_matches += len(data)
        attempts.append({'params': params, 'http_status': status, 'matches': len(data), 'url': used_url})
        for item in data:
            if not isinstance(item, dict):
                continue
            score = 0.0
            if str(item.get('id')) == expected_id:
                score += 3.0
            score += SequenceMatcher(None, title.lower(), clean_text(item.get('title')).lower()).ratio()
            if price is not None and item.get('price') is not None and abs(float(price) - float(item.get('price'))) <= 5:
                score += 0.5
            if surf is not None and item.get('surface_habitable') is not None and abs(float(surf) - float(item.get('surface_habitable'))) <= 1.0:
                score += 0.5
            if score > best_score:
                best, best_score, best_status = item, score, status
        # Exact id hit is enough; do not keep searching broad queries that may
        # produce a worse false-positive candidate.
        if best and str(best.get('id')) == expected_id:
            break

    if not best:
        return '', {'method': 'domimmo_keldom_api', 'attempts': attempts, 'matches': total_matches, 'best_score': best_score}
    exact = str(best.get('id')) == expected_id
    if not exact and best_score < 1.75:
        return '', {'method': 'domimmo_keldom_api', 'attempts': attempts, 'matches': total_matches, 'best_score': best_score, 'reject': 'low_match'}
    desc = clean_domimmo_desc(best.get('description') or '')
    return desc, {'method': 'domimmo_keldom_api', 'http_status': best_status, 'attempts': attempts, 'matches': total_matches, 'best_score': round(best_score, 3), 'matched_id': best.get('id'), 'photo_count': len(best.get('photos') or [])}


def immo97_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    status, body = request_text(row['url'], referer='https://www.97immo.com/')
    meta = extract_meta_description(body)
    desc = ''
    m = re.search(r'<div[^>]+class=["\'][^"\']*descriptif[^"\']*["\'][^>]*>(.*?)</div>\s*</div>', body, re.I | re.S)
    if m:
        desc = clean_text(m.group(1))
    # grab text around the Descriptif section if class regex misses nested divs
    if len(desc) < 80:
        i = body.lower().find('descriptif')
        if i >= 0:
            desc = clean_text(body[i:i+5000])
    if len(desc) < len(meta):
        desc = meta
    return desc, {'method': '97immo_html_descriptif', 'http_status': status, 'meta_len': len(meta)}


def citya_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    status, body = request_text(row['url'], referer='https://www.citya.com/')
    meta = extract_meta_description(body)
    snippets = []
    for label in ['détails du prix', 'Dépôt de garantie', 'Honoraires charge locataire', 'Libre le']:
        i = body.lower().find(label.lower())
        if i >= 0:
            snippets.append(clean_text(body[max(0, i-200):i+1200]))
    combined = clean_text(meta + '\n' + '\n'.join(snippets))
    return combined, {'method': 'citya_meta_price_details', 'http_status': status, 'meta_len': len(meta)}


def zimo_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    attempts = []
    source_id = str(row['source_id'])
    # Zimo hides the visible page description behind a rewarded/ad unlock UI, but
    # the Stimulus controller exposes a public JSON endpoint in the server HTML:
    #   /tools/listing/content/<uuid> -> {"content": "full source prose"}
    # Prefer it over the meta description, because the meta text is truncated.
    content_url = f'https://www.zimo.fr/tools/listing/content/{source_id}'
    try:
        status, body = request_text(content_url, referer=row['url'], timeout=20)
        attempts.append({'kind': 'content_endpoint', 'url': content_url, 'status': status, 'len': len(body)})
        data = json.loads(body)
        desc = clean_text(data.get('content') if isinstance(data, dict) else '')
        # Some agency-fed descriptions append generic syndication/footer text that
        # trips the global boilerplate guard. Keep the source prose above it.
        for tail in ["Cette annonce vous est proposée par", "Nos tarifs"]:
            pos = desc.find(tail)
            if pos > 120:
                desc = desc[:pos].strip()
        if len(desc) >= 80:
            return desc, {'method': 'zimo_content_endpoint', 'attempts': attempts}
    except urllib.error.HTTPError as e:
        attempts.append({'kind': 'content_endpoint', 'url': content_url, 'status': e.code, 'error': str(e)})
    except Exception as e:
        attempts.append({'kind': 'content_endpoint', 'url': content_url, 'error': repr(e)})

    # Fallback: public detail HTML meta description. This is better than the card
    # fallback but often truncated with an ellipsis.
    for referer in ['https://www.zimo.fr/', 'https://www.zimo.fr/location/dom-tom/la-reunion']:
        try:
            status, body = request_text(row['url'], referer=referer, timeout=20)
            attempts.append({'kind': 'detail_html', 'referer': referer, 'status': status, 'len': len(body)})
            meta = extract_meta_description(body)
            if len(meta) > 80:
                return meta, {'method': 'zimo_http_meta', 'attempts': attempts}
        except urllib.error.HTTPError as e:
            attempts.append({'kind': 'detail_html', 'referer': referer, 'status': e.code, 'error': str(e)})
        except Exception as e:
            attempts.append({'kind': 'detail_html', 'referer': referer, 'error': repr(e)})
        time.sleep(0.5)
    return '', {'method': 'zimo_content_endpoint_then_meta', 'attempts': attempts, 'blocked': True}


def fnaim_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    """Le scraper d'origine (detail_listing() dans
    realestate_multi_sources_scraper.py) retombe sur meta/og:description, un
    gabarit SEO court et parfois casse ("de e" au lieu du prix). Le vrai
    texte de l'annonce vit dans un bloc bien identifie -- on le prefere."""
    status, body = request_text(row['url'], referer='https://www.fnaim.re/')
    meta = extract_meta_description(body)
    desc = ''
    m = re.search(r'<div[^>]+class=["\'][^"\']*property__description-content[^"\']*["\'][^>]*>(.*?)</div>',
                 body, re.I | re.S)
    if m:
        desc = clean_text(m.group(1))
    if len(desc) < len(meta):
        desc = meta
    return desc, {'method': 'fnaim_html_descriptif', 'http_status': status, 'meta_len': len(meta)}


FETCHERS = {
    'superimmo': superimmo_description,
    'locamoi': locamoi_description,
    'domimmo': domimmo_description,
    '97immo': immo97_description,
    'citya': citya_description,
    'zimo': zimo_description,
    'fnaim': fnaim_description,
}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DB))
    ap.add_argument('--sources', default=','.join(TARGET_SOURCES))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=0.8)
    ap.add_argument('--report', default='')
    ap.add_argument('--llm-dry-run', action='store_true', help='write LLM extraction prompts to JSONL without network calls')
    ap.add_argument('--llm', action='store_true', help='enable live LLM extraction and persist grounded fields (off by default)')
    ap.add_argument('--llm-model', default='', help='LLM model id; default follows --llm-provider unless IMMO_LLM_MODEL is set')
    ap.add_argument('--llm-provider', default=LLM_DEFAULT_PROVIDER, choices=['openrouter', 'anthropic'])
    ap.add_argument('--llm-report', default='', help='JSONL path for LLM dry-run/live metadata')
    ap.add_argument('--llm-limit', type=int, default=40, help='maximum LLM candidates/calls this run; pass 0 explicitly for unlimited')
    ap.add_argument('--llm-refresh', action='store_true', help='ignore existing matching input_hash cache and call the LLM again')
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.dry_run and args.llm:
        raise SystemExit('Refusing --dry-run with --llm live calls; use --llm-dry-run for zero-network prompt review, or remove --dry-run to persist live extraction.')
    if not args.llm_model:
        args.llm_model = default_llm_model(args.llm_provider)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f'DB not found: {db}')
    stamp = utcstamp()
    report = Path(args.report) if args.report else OUTDIR / f'detail_enrichment_{stamp}.jsonl'
    summary_path = report.with_suffix('.summary.json')
    backup = None
    if not args.dry_run:
        backup = db.with_name(f'{db.name}.bak-detail-v3-{stamp}')
        shutil.copy2(db, backup)

    sources = [s.strip() for s in args.sources.split(',') if s.strip()]
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = []
    for src in sources:
        q = """
        SELECT source_site, source_id, url, title, city, description, raw_json_path, rent_eur, surface_m2
        FROM rental_listings
        WHERE source_site=? AND COALESCE(is_active,1)=1
        ORDER BY length(COALESCE(description,'')) ASC
        """
        for r in con.execute(q, (src,)):
            if src == 'superimmo' or is_sparse(r['description'], r['title']):
                rows.append(r)
    if args.limit:
        rows = rows[:args.limit]

    counts: dict[str, int] = {'attempted': 0, 'accepted': 0, 'updated': 0, 'rejected': 0, 'errors': 0, 'blocked': 0, 'skipped_no_fetcher': 0, 'llm_candidates': 0, 'llm_dry_run': 0, 'llm_called': 0, 'llm_cached': 0, 'llm_saved': 0, 'llm_errors': 0}
    by_source: dict[str, dict[str, int]] = {}
    llm_client = create_llm_client(args.llm_provider) if args.llm else None
    if args.llm and not args.dry_run:
        ensure_llm_table(con)
    llm_report = Path(args.llm_report) if args.llm_report else OUTDIR / f'llm_extraction_{stamp}.jsonl'
    llm_log_handle = llm_report.open('w', encoding='utf-8') if (args.llm_dry_run or args.llm) else None
    llm_seen = 0

    with report.open('w', encoding='utf-8') as log:
        for idx, row in enumerate(rows, 1):
            src = row['source_site']
            by_source.setdefault(src, {'attempted': 0, 'accepted': 0, 'updated': 0, 'rejected': 0, 'errors': 0, 'blocked': 0})
            counts['attempted'] += 1
            by_source[src]['attempted'] += 1
            rec: dict[str, Any] = {'idx': idx, 'source': src, 'source_id': row['source_id'], 'url': row['url'], 'old_len': len(clean_text(row['description'])), 'dry_run': args.dry_run}
            fetcher = FETCHERS.get(src)
            if not fetcher:
                counts['skipped_no_fetcher'] += 1
                rec['action'] = 'skip'; rec['reason'] = 'no_fetcher'
                log.write(json.dumps(rec, ensure_ascii=False) + '\n'); log.flush()
                continue
            try:
                new_desc, meta = fetcher(row)
                ok, reason = should_update(row['description'], new_desc, row['title'])
                source_text = clean_text(new_desc) or clean_text(row['description'])
                rec.update({'new_len': len(clean_text(new_desc)), 'reason': reason, 'meta': meta, 'preview': clean_text(new_desc)[:240]})
                if meta.get('blocked'):
                    counts['blocked'] += 1; by_source[src]['blocked'] += 1
                if source_text and (args.llm_dry_run or args.llm) and (not args.llm_limit or llm_seen < args.llm_limit):
                    llm_fields, llm_meta = llm_extract_listing_signals(row, source_text, client=None, model=args.llm_model)
                    if llm_meta.get('llm') == 'skipped_no_text':
                        rec['llm'] = {'fields': llm_fields, 'meta': llm_meta}
                    else:
                        counts['llm_candidates'] += 1
                        llm_seen += 1
                        if args.llm_dry_run and llm_log_handle:
                            llm_log_handle.write(json.dumps({
                                'dry_run': True,
                                'source': src,
                                'source_id': row['source_id'],
                                'input_hash': llm_input_hash(source_text),
                                'tool': LLM_TOOL,
                                'messages': build_llm_messages(row, source_text),
                            }, ensure_ascii=False) + '\n')
                            llm_log_handle.flush()
                            counts['llm_dry_run'] += 1
                        if args.llm:
                            try:
                                input_hash = llm_input_hash(source_text)
                                cached = None if args.llm_refresh else get_cached_llm_extraction(con, row, model=args.llm_model, input_hash=input_hash)
                                if cached:
                                    fields = cached['fields']
                                    llm_meta = cached['meta']
                                    counts['llm_cached'] += 1
                                else:
                                    fields, llm_meta = llm_extract_listing_signals(row, source_text, client=llm_client, model=args.llm_model)
                                    if llm_meta.get('llm') == 'ok':
                                        counts['llm_called'] += 1
                                rec['llm'] = {'fields': fields, 'meta': llm_meta}
                                if llm_log_handle:
                                    llm_log_handle.write(json.dumps({'source': src, 'source_id': row['source_id'], 'fields': fields, 'meta': llm_meta}, ensure_ascii=False) + '\n')
                                    llm_log_handle.flush()
                                if llm_fields_have_values(fields) and not args.dry_run and llm_meta.get('llm') != 'cached':
                                    upsert_llm_extraction(con, row, model=args.llm_model, fields=fields, meta=llm_meta, extracted_at=datetime.now(timezone.utc).isoformat())
                                    counts['llm_saved'] += 1
                            except Exception as llm_exc:
                                counts['llm_errors'] += 1
                                rec['llm_error'] = repr(llm_exc)
                if ok:
                    counts['accepted'] += 1; by_source[src]['accepted'] += 1
                    rec['action'] = 'accept_dry_run' if args.dry_run else 'update_db'
                    if not args.dry_run:
                        con.execute(
                            "UPDATE rental_listings SET description=?, content_hash=? WHERE source_site=? AND source_id=?",
                            (clean_text(new_desc), None, row['source_site'], row['source_id']),
                        )
                        counts['updated'] += 1; by_source[src]['updated'] += 1
                else:
                    counts['rejected'] += 1; by_source[src]['rejected'] += 1
                    rec['action'] = 'reject'
            except Exception as e:
                counts['errors'] += 1; by_source[src]['errors'] += 1
                rec.update({'action': 'error', 'error': repr(e)})
            if not args.dry_run:
                # Agentic resume: persist each listing's accepted description/LLM
                # cache independently. A later crash resumes from remaining sparse
                # rows instead of repaying the whole prefix of work.
                con.commit()
            log.write(json.dumps(rec, ensure_ascii=False) + '\n')
            log.flush()
            if idx < len(rows):
                time.sleep(args.sleep)
    if llm_log_handle:
        llm_log_handle.close()
    if not args.dry_run:
        con.commit()
    summary = {'generated_at': datetime.now(timezone.utc).isoformat(), 'dry_run': args.dry_run, 'db': str(db), 'backup': str(backup) if backup else None, 'report': str(report), 'counts': counts, 'by_source': by_source}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
