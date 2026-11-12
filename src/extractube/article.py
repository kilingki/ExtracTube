"""Deterministic article assembly and standalone, escaped HTML rendering."""

import base64
import html
import io
from urllib.parse import urlsplit
from PIL import Image
from .store import HashCache, digest

_WEBP_CACHE = {}


def _inline_webp_b64(raw, sha256):
    key = (sha256, len(raw))
    cached = _WEBP_CACHE.get(key)
    if cached is not None:
        return cached
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        w, h = im.size
        if max(w, h) > 1280:
            if w >= h:
                nw, nh = 1280, max(1, round(h * 1280 / w))
            else:
                nh, nw = 1280, max(1, round(w * 1280 / h))
            im = im.resize((nw, nh), Image.Resampling.LANCZOS)
        if im.mode not in ('RGB', 'RGBA'):
            im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
        buf = io.BytesIO()
        im.save(buf, format='WEBP', quality=85, method=4)
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    if len(_WEBP_CACHE) >= 2048:
        _WEBP_CACHE.clear()
    _WEBP_CACHE[key] = encoded
    return encoded


STYLE = '''*{box-sizing:border-box}body{margin:0;background:#f8f7f3;color:#242829;font:18px/1.85 system-ui,sans-serif}main{max-width:850px;margin:auto;padding:32px 22px 80px;overflow-wrap:anywhere}h1{font-size:2em;line-height:1.35}h2{margin-top:2.6em;border-top:1px solid #ccd1ce;padding-top:1.2em;line-height:1.5}h3{margin-top:1.8em;line-height:1.5}p{margin:1.2em 0}a{color:#226454}nav{padding:18px;background:#eef1ec}figure{margin:28px 0}img{max-width:100%;height:auto}figcaption{font-size:.85em;color:#56615a}button{font:inherit;cursor:pointer}figure button{border:0;padding:0;background:none;max-width:100%}dialog{max-width:96vw;max-height:96vh;padding:12px}dialog .original{overflow:auto;max-height:80vh;max-width:90vw}dialog img{max-width:none}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:20px 0}@media(max-width:400px){main{padding:20px 16px 50px}h1{font-size:1.6em}}'''
SCRIPT = '''document.querySelectorAll('[data-full]').forEach(b=>b.addEventListener('click',()=>{const d=document.getElementById('original');d.querySelector('img').src=b.querySelector('img').src;d.showModal()}));document.getElementById('close-original')?.addEventListener('click',()=>document.getElementById('original').close());'''


def assemble(bundle, outline, plan, drafts, title):
    sections = []
    for unit, draft in zip(plan['units'], drafts):
        if draft['unit_id'] != unit['unit_id']:
            raise ValueError('draft_order')
        paragraphs = [
            {**p, 'paragraph_id': f'{unit["unit_id"]}-p{i:03d}', 'figures': []}
            for i, p in enumerate(draft['paragraphs'])
        ]
        sections.append(
            {
                'unit_id': unit['unit_id'],
                'root_index': unit['root_index'],
                'root_title': unit['root_title'],
                'title': unit['title'],
                'paragraphs': paragraphs,
                'unresolved': draft['unresolved'],
                'editorial_candidates': draft['editorial_candidates'],
            }
        )
    article = {
        'schema_version': 'extractube.content.article.v1',
        'video_id': bundle['video_id'],
        'title': title,
        'source_url': bundle['source_url'],
        'sections': sections,
        'asset_placements': [],
        'coverage': bundle['coverage'],
        'omitted_audio_chunks': bundle['omitted_audio_chunks'],
    }
    assets = {a['asset_id']: a for a in bundle['assets']}
    used = set()
    offset = float(bundle['source']['audio_to_video_offset_seconds'])
    all_paragraphs = [p for section in sections for p in section['paragraphs']]
    for unit in bundle['units']:
        ids = {s['sentence_index'] for s in unit['sentences']}
        for association in unit['asset_refs']:
            aid = association['asset_id']
            if aid in used:
                continue
            candidates = []
            for order, p in enumerate(all_paragraphs):
                overlap = ids & set(p['source_indices'])
                if overlap:
                    distance = min(
                        abs(
                            float(bundle['sentences'][i]['audio_start'])
                            + offset
                            - float(association['actual_video_seconds'])
                        )
                        for i in overlap
                    )
                    candidates.append((distance, order, p))
            if not candidates:
                continue
            target = min(candidates, key=lambda row: row[:2])[2]
            figure = {
                **association,
                'image': assets[aid]['image'],
                'paragraph_id': target['paragraph_id'],
                'display_review': 'unreviewed',
                'caption_is_verified_transcription': False,
            }
            target['figures'].append(figure)
            article['asset_placements'].append(figure)
            used.add(aid)
    return article


def render(article, work, *, bundle=None, report=None):
    esc = lambda value: html.escape(str(value), quote=True)
    hashes = HashCache()
    parts = [
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<title>{esc(article["title"])}</title><style>{STYLE}</style></head><body><main>',
        f'<h1>{esc(article["title"])}</h1>',
    ]
    url = article['source_url']
    if urlsplit(url).scheme in ('http', 'https'):
        parts.append(f'<a href="{esc(url)}">원본 영상</a>')
    parts.append('<nav aria-label="목차"><details><summary>목차</summary><ol>')
    roots = {}
    for section in article['sections']:
        roots.setdefault(section['root_index'], section['root_title'])
    for index, title in roots.items():
        parts.append(f'<li><a href="#section-{index}">{esc(title)}</a></li>')
    parts.append('</ol></details></nav>')
    last = None
    last_sub = None
    for section in article['sections']:
        if section['root_index'] != last:
            parts.append(
                f'<h2 id="section-{section["root_index"]}">{esc(section["root_title"])}</h2>'
            )
            last = section['root_index']
            last_sub = None
        if (
            section['title'] != section['root_title']
            and (section['root_index'], section['title']) != last_sub
        ):
            parts.append(f'<h3>{esc(section["title"])}</h3>')
            last_sub = (section['root_index'], section['title'])
        for p in section['paragraphs']:
            parts.append(f'<p data-paragraph="{esc(p["paragraph_id"])}">{esc(p["text"])}</p>')
            for f in p['figures']:
                path = hashes.check(work, f['image'])
                raw = path.read_bytes()
                if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('figure_not_png')
                data = _inline_webp_b64(raw, f['image']['sha256'])
                parts.append(
                    f'<figure><button data-full aria-label="원본 이미지 확대"><img src="data:image/webp;base64,{data}" alt="{esc(f["caption"])}"></button><figcaption>{esc(f["caption"])}</figcaption></figure>'
                )
    if bundle is not None:
        parts.append('<hr><h2>검토 자료</h2><details><summary>원문 전체</summary>')
        for s in bundle['sentences']:
            parts.append(f'<p>{s["sentence_index"]}: {esc(s["text"])}</p>')
        parts.append('</details>')
        if report is not None:
            import json

            parts.append('<pre>' + esc(json.dumps(report, ensure_ascii=False, indent=2)) + '</pre>')
    parts.append(
        '</main><dialog id="original"><button id="close-original">닫기</button><div class="original"><img alt="원본 이미지"></div></dialog><script>'
        + SCRIPT
        + '</script></body></html>'
    )
    return ''.join(parts).encode('utf-8')


def validate(article, bundle, plan):
    if article['video_id'] != bundle['video_id'] or len(article['sections']) != len(plan['units']):
        raise ValueError('article_binding')
    ids = []
    for section, unit in zip(article['sections'], plan['units']):
        if any(section[k] != unit[k] for k in ('unit_id', 'root_index', 'root_title', 'title')):
            raise ValueError('article_structure')
        allowed = {s['sentence_index'] for s in unit['sentences']}
        seen = set()
        for kind in ('paragraphs', 'unresolved', 'editorial_candidates'):
            for row in section[kind]:
                if not set(row['source_indices']) <= allowed:
                    raise ValueError('article_source')
                seen.update(row['source_indices'])
        if seen != allowed:
            raise ValueError('article_coverage')
        ids.extend(p['paragraph_id'] for p in section['paragraphs'])
    if len(ids) != len(set(ids)):
        raise ValueError('paragraph_ids')
    return {
        'structural_valid': True,
        'article_digest': digest(article),
        'quality_review_performed': False,
    }
