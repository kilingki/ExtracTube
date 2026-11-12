"""Grounded writing and bounded review edits, independent of storage and transport."""

import copy
import re
import unicodedata
from difflib import SequenceMatcher
from .content_plan import obj, array
from .store import digest


def bounds(unit):
    n = len(unit['sentences'])
    span = min(18, max(12, n))
    minimum = max(1, (n + span - 1) // span)
    return {
        'min_paragraphs': minimum,
        'max_paragraphs': min(24, max(n, minimum)),
        'max_sentences_per_paragraph': span,
    }


def schema(unit):
    ids = [s['sentence_index'] for s in unit['sentences']]
    b = bounds(unit)
    note_indices = array({'type': 'integer', 'enum': ids}, len(ids), 1)
    para_indices = array({'type': 'integer', 'enum': ids}, b['max_sentences_per_paragraph'], 1)
    note = obj(
        {
            'source_indices': note_indices,
            'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
        }
    )
    paragraph = obj(
        {
            'source_indices': para_indices,
            'claim_mode': {
                'type': 'string',
                'enum': ['source_fact', 'procedure', 'speaker_view', 'mixed_procedure_and_view'],
            },
            'key_anchors': array({'type': 'string', 'minLength': 1, 'maxLength': 120}, 32),
            'text': {'type': 'string', 'minLength': 1, 'maxLength': 6000},
        }
    )
    return obj(
        {
            'unit_id': {'type': 'string', 'enum': [unit['unit_id']]},
            'paragraphs': array(paragraph, b['max_paragraphs'], b['min_paragraphs']),
            'editorial_candidates': array(note, 16),
            'unresolved': array(note, 16),
        }
    )


def payload(unit, source_url):
    return {
        'unit_id': unit['unit_id'],
        'root_title': unit['root_title'],
        'section_title': unit['title'],
        'assigned_sentences': [
            {'index': s['sentence_index'], 'text': s['text']} for s in unit['sentences']
        ],
        'previous_context_readonly': [
            {'index': s['sentence_index'], 'text': s['text']} for s in unit['previous_context']
        ],
        'paragraph_plan': bounds(unit),
        'document_context': {
            'delivery': 'standalone_html',
            'available_resources': [{'kind': 'original_video', 'url': source_url}],
            'unprovided_resources_must_not_be_assumed': True,
        },
    }


def plain(text):
    if not text.strip() or re.search(r'<[/!a-zA-Z]|```', text):
        raise ValueError('non_plain_text')


_KO_PARTICLES = (
    '에서는',
    '으로서',
    '으로써',
    '로부터',
    '까지도',
    '이라는',
    '라고는',
    '에서',
    '으로',
    '부터',
    '까지',
    '처럼',
    '보다',
    '마다',
    '조차',
    '마저',
    '이나',
    '거나',
    '이란',
    '란',
    '은',
    '는',
    '이',
    '가',
    '을',
    '를',
    '에',
    '로',
    '와',
    '과',
    '의',
    '도',
    '만',
)
_UI_QUOTE_SUFFIXES = (
    '하기',
    '버튼',
    '입력란',
    '항목',
    '메뉴',
    '기능',
    '화면',
    '영역',
    '탭',
    '란',
    '창',
)
_KO_NATIVE_NUM_EQUIV = {
    '1': ('하루', '한', '하나', '일', '첫', '당일'),
    '2': ('이틀', '두', '둘', '이'),
    '3': ('사흘', '세', '셋', '삼'),
    '4': ('나흘', '네', '넷', '사'),
    '5': ('닷새', '다섯', '오'),
    '6': ('엿새', '여섯', '육', '륙'),
    '7': ('이레', '일곱', '칠'),
    '8': ('여드레', '여덟', '팔'),
    '9': ('아흐레', '아홉', '구'),
    '10': ('열흘', '열', '십'),
    '11': ('열한', '열하나', '십일'),
    '12': ('열두', '열둘', '십이'),
    '13': ('열세', '열셋', '십삼'),
    '14': ('열네', '열넷', '십사'),
    '15': ('열다섯', '십오'),
    '16': ('열여섯', '십육', '십륙'),
    '17': ('열일곱', '십칠'),
    '18': ('열여덟', '십팔'),
    '19': ('열아홉', '십구'),
    '20': ('스물', '스무', '이십'),
    '30': ('서른', '삼십'),
    '40': ('마흔', '사십'),
    '50': ('쉰', '오십'),
}
_SINO_DIGITS = {1: '일', 2: '이', 3: '삼', 4: '사', 5: '오', 6: '육', 7: '칠', 8: '팔', 9: '구'}
_SINO_CHAR_VAL = {
    '일': 1,
    '이': 2,
    '삼': 3,
    '사': 4,
    '오': 5,
    '육': 6,
    '륙': 6,
    '칠': 7,
    '팔': 8,
    '구': 9,
}
_SINO_UNIT_VAL = {'십': 10, '백': 100, '천': 1000}
_SINO_BIG_UNIT = {'만': 10000, '억': 100000000}
_VOWEL_NDA_STEMS = {
    '된다': ('되', '돼', '된', '될', '됨'),
    '간다': ('가', '간', '갈', '감'),
    '온다': ('오', '와', '온', '올', '옴'),
    '본다': ('보', '봐', '본', '볼', '봄'),
    '준다': ('주', '줘', '준', '줄', '줌'),
    '한다': ('하', '해', '한', '할', '함'),
    '난다': ('나', '난', '날', '남'),
    '산다': ('사', '산', '살', '삼'),
    '쓴다': ('쓰', '써', '쓴', '쓸', '씀'),
    '만든다': ('만들', '만든', '만듦'),
}
_DEMONSTRATIVE_PROWORDS = {
    '이렇게',
    '그렇게',
    '저렇게',
    '이렇듯',
    '그렇듯',
    '이런',
    '그런',
    '저런',
    '이것',
    '그것',
    '저것',
    '이거',
    '그거',
    '이게',
    '그게',
    '여기',
    '거기',
}
_ACTION_SUMMARY_NOUNS = {
    '포함',
    '제외',
    '삭제',
    '유지',
    '권고',
    '강조',
    '중요',
    '필수',
    '생략',
    '반복',
    '진입',
    '이동',
    '선택',
    '입력',
    '복사',
    '편집',
    '수집',
    '확인',
    '설정',
    '설명',
    '비교',
    '요약',
    '제거',
    '추가',
    '활용',
    '조사',
    '보호',
    '모방',
    '창업',
    '등록',
    '조심',
    '주의',
    '참고',
    '권장',
    '예시',
    '사례',
    '경우',
    '정도',
    '부분',
    '방식',
    '상태',
    '수준',
    '느낌',
    '의미',
    '생각',
    '이유',
    '결과',
    '문제',
    '과정',
    '단계',
    '오래',
    '왜냐면',
    '왜냐하면',
    '그리고',
    '하지만',
    '그러나',
    '그래서',
    '따라서',
    '결국',
    '먼저',
    '바로',
    '매우',
    '정말',
    '진짜',
    '아주',
    '조금',
    '약간',
    '계속',
    '자꾸',
    '다시',
    '이미',
    '아직',
    '거의',
    '무조건',
    '케바케',
}


def _sino_under_10000(n):
    parts = []
    for unit_val, unit_ch in ((1000, '천'), (100, '백'), (10, '십'), (1, '')):
        d, n = divmod(n, unit_val)
        if not d:
            continue
        if unit_ch and d == 1:
            parts.append(unit_ch)
        else:
            parts.append(_SINO_DIGITS[d] + unit_ch)
    return ''.join(parts)


def _korean_number_variants(number_str):
    variants = set(_KO_NATIVE_NUM_EQUIV.get(number_str, ()))
    if '.' in number_str or not number_str.isdigit():
        return variants
    n = int(number_str)
    if n == 0:
        variants.update(('영', '공', '제로'))
        return variants
    if n > 999999999999:
        return variants
    big_parts = []
    rem = n
    for big_val, big_ch in ((100000000, '억'), (10000, '만')):
        q, rem = divmod(rem, big_val)
        if q:
            sub = _sino_under_10000(q)
            big_parts.append(('' if q == 1 and big_ch == '만' and n < 20000 else sub) + big_ch)
    if rem:
        big_parts.append(_sino_under_10000(rem))
    sino = ''.join(big_parts)
    if sino:
        variants.add(sino)
        if '십육' in sino:
            variants.add(sino.replace('십육', '십륙'))
    if 10000 <= n < 20000:
        tail = _sino_under_10000(n - 10000) if n > 10000 else ''
        variants.add('일만' + tail)
    for unit_val, unit_ch in ((100000000, '억'), (10000, '만'), (1000, '천'), (100, '백')):
        if n >= unit_val and n % unit_val == 0:
            q = n // unit_val
            variants.add(f'{q}{unit_ch}')
            if unit_ch in ('만', '억'):
                for sub_val, sub_ch in ((1000, '천'), (100, '백'), (10, '십')):
                    if q >= sub_val and q % sub_val == 0:
                        variants.add(f'{q // sub_val}{sub_ch}{unit_ch}')
    return variants


def _hangul_num_in_text(clean_a, norm_t):
    m = re.match(
        r'^([일삼사오육륙칠팔구십백천만억이]{2,6})(?:퍼센트|프로|개|원|명|번|회|차|일|월|년|배|분의)?$',
        clean_a,
    )
    if not m:
        return False
    num_part = m.group(1)
    total = 0
    section = 0
    cur = 0
    matched_any = False
    for ch in num_part:
        if ch in _SINO_CHAR_VAL:
            cur = _SINO_CHAR_VAL[ch]
            matched_any = True
        elif ch in _SINO_UNIT_VAL:
            section += (cur or 1) * _SINO_UNIT_VAL[ch]
            cur = 0
            matched_any = True
        elif ch in _SINO_BIG_UNIT:
            section += cur
            total += (section or 1) * _SINO_BIG_UNIT[ch]
            section = 0
            cur = 0
            matched_any = True
        else:
            return False
    val = total + section + cur
    if not matched_any or val <= 0:
        return False
    return bool(re.search(r'(?<!\d)' + re.escape(str(val)) + r'(?!\d)', norm_t))


def _strip_particle(tok):
    for p in _KO_PARTICLES:
        if len(tok) > len(p) + 1 and tok.endswith(p):
            return tok[: -len(p)]
    return tok


def _word_grounded(w, compact, compact_np):
    cw = w
    for suf in _UI_QUOTE_SUFFIXES:
        if len(cw) > len(suf) + 1 and cw.endswith(suf):
            cw = cw[: -len(suf)]
            break
    cw = _strip_particle(cw)
    if cw in compact or cw in compact_np:
        return True
    for form, roots in _VOWEL_NDA_STEMS.items():
        if cw.endswith(form):
            prefix = cw[: -len(form)]
            if any((prefix + r) in compact for r in roots):
                return True
    if len(cw) >= 3 and cw.endswith('는다'):
        vstem = cw[:-2]
        if any(
            (vstem + e) in compact
            for e in (
                '는다',
                '는',
                '네',
                '어',
                '아',
                '고',
                '며',
                '면',
                '니',
                '은',
                '던',
                '지',
                '죠',
                '요',
                '음',
                '기',
                '거',
                '잖',
            )
        ):
            return True
    if len(cw) >= 2 and cw.endswith('다'):
        stem = cw[:-1]
        if stem.endswith('하') and len(stem) >= 2:
            base = stem[:-1]
            if any((base + e) in compact for e in ('하', '해', '한', '할', '함')):
                return True
        if any(
            (stem + e) in compact
            for e in (
                '다',
                '고',
                '며',
                '면',
                '니',
                '는',
                '은',
                '던',
                '어',
                '아',
                '게',
                '지',
                '죠',
                '요',
                '음',
                '기',
                '네',
                '거',
                '잖',
            )
        ):
            return True
    return False


def quote_errors(text, sources):
    """Conservative E05-style Hangul near-miss detector; not a semantic quality score."""
    segments = [sources] if isinstance(sources, str) else list(sources)
    raw_all = ' '.join(segments)
    compact = re.sub(r'[^가-힣A-Za-z0-9]', '', raw_all)
    all_toks = re.findall(r'[가-힣A-Za-z0-9]+', raw_all)
    compact_np = ''.join(_strip_particle(t) for t in all_toks)
    ngrams = set()
    for seg in segments:
        htoks = re.findall(r'[가-힣]+', seg)
        stoks = [_strip_particle(t) for t in htoks]
        n = len(htoks)
        for i in range(n):
            for k in range(1, min(6, n - i + 1)):
                for ng in (''.join(htoks[i : i + k]), ''.join(stoks[i : i + k])):
                    if 2 <= len(ng) <= 18:
                        ngrams.add(ng)
    errors = []
    pattern = r"'([^'\n]{2,24})'|\"([^\"\n]{2,24})\"|“([^”\n]{2,24})”|‘([^’\n]{2,24})’"
    for match in re.finditer(pattern, text):
        q = next(g for g in match.groups() if g is not None).strip()
        cq = re.sub(r'\s+', '', q)
        if not re.fullmatch(r'[가-힣]{2,16}', cq):
            continue
        if _word_grounded(cq, compact, compact_np):
            continue
        words = [w for w in q.split() if w]
        if len(words) >= 2 and all(_word_grounded(w, compact, compact_np) for w in words):
            continue
        tail = text[match.end() :]
        is_indirect = bool(re.match(r'^(?:고|라고|는|라는|며|라며|던)(?:[^가-힣]|$)', tail))
        if is_indirect and len(words) >= 2:
            content_words = [
                w
                for w in words
                if _strip_particle(w) not in _DEMONSTRATIVE_PROWORDS
                and w not in _DEMONSTRATIVE_PROWORDS
            ]
            if content_words and all(_word_grounded(w, compact, compact_np) for w in content_words):
                continue
        jq = unicodedata.normalize('NFD', cq)
        length = len(cq)
        best_r, best_cand = 0.0, ''
        for cand in ngrams:
            if abs(len(cand) - length) > 1:
                continue
            ratio = SequenceMatcher(None, jq, unicodedata.normalize('NFD', cand)).ratio()
            if ratio > best_r:
                best_r, best_cand = ratio, cand
        thresh = 0.75 if length <= 3 else 0.74
        if thresh <= best_r < 1:
            errors.append(f'near_miss_quote:{q}~{best_cand}')
    return errors


def validate(value, spec):
    import jsonschema

    jsonschema.validate(value, spec['schema'])
    unit = spec['unit']
    ids = {s['sentence_index'] for s in unit['sentences']}
    soft_fallback = bool(spec.get('soft_anchor_fallback'))
    seen = set()
    paragraph_ids = set()
    edited = set()
    starts = []
    source = [s['text'] for s in unit['sentences'] + unit['previous_context']] + [
        unit.get('root_title', ''),
        unit.get('title', ''),
    ]
    compact_pool = re.sub(r'[^가-힣A-Za-z0-9]', '', ' '.join(source))
    unresolved_concat = ' '.join(r.get('reason', '') for r in value['unresolved'])
    unresolved_indices = {idx for r in value['unresolved'] for idx in r.get('source_indices', [])}
    soft_notes = []
    for kind in ('paragraphs', 'editorial_candidates', 'unresolved'):
        for row in value[kind]:
            indices = row['source_indices']
            if indices != sorted(set(indices)) or not set(indices) <= ids:
                raise ValueError('source_indices')
            seen.update(indices)
            plain(row.get('text', row.get('reason', '')))
            if kind == 'editorial_candidates':
                edited.update(indices)
            if kind != 'paragraphs':
                continue
            paragraph_ids.update(indices)
            starts.append(min(indices))
            if len(indices) > bounds(unit)['max_sentences_per_paragraph']:
                raise ValueError('paragraph_span')
            text = row['text']
            if re.search(
                r'\b(?:source_indices|claim_mode|key_anchors|editorial_candidates)\b', text
            ):
                raise ValueError('internal_id_leak')
    if seen != ids or not paragraph_ids:
        missing = sorted(ids - seen)
        raise ValueError(f'source_coverage:missing_{missing}' if missing else 'source_coverage')
    if len(edited - paragraph_ids) > max(2, len(ids) // 3):
        raise ValueError('excessive_editorial_omission')
    if starts != sorted(starts):
        raise ValueError('paragraph_order')
    for row in value['paragraphs']:
        indices = row['source_indices']
        text = row['text']
        norm_t = text.replace(',', '')
        alnum_t = re.sub(r'[^가-힣A-Za-z0-9]', '', text)
        para_soft = []
        for anchor in row['key_anchors']:
            clean_a = anchor.strip().strip("'\"“”‘’")
            if not clean_a:
                raise ValueError('empty_key_anchor')
            for number in re.findall(r'\d+(?:\.\d+)?', clean_a.replace(',', '')):
                if not re.search(r'(?<!\d)' + re.escape(number) + r'(?!\d)', norm_t):
                    if any(w in norm_t for w in _korean_number_variants(number)):
                        continue
                    if soft_fallback:
                        para_soft.append('missing_numeric_anchor:' + anchor)
                        continue
                    raise ValueError('missing_numeric_anchor:' + anchor)
            if (
                compact_pool
                and re.fullmatch(r'[가-힣]{2,8}', clean_a)
                and not clean_a.endswith(
                    ('기', '함', '됨', '다', '요', '죠', '네', '음', '듯', '뿐', '님')
                )
                and not clean_a.endswith(
                    ('습니다', '입니다', '합니다', '됩니다', '어요', '아요', '네요', '군요')
                )
                and clean_a not in _ACTION_SUMMARY_NOUNS
                and clean_a in compact_pool
                and clean_a not in unresolved_concat
                and not (set(indices) & unresolved_indices)
            ):
                compound_ok = (
                    len(clean_a) >= 3 and clean_a[-2:] in alnum_t and clean_a[0] in alnum_t
                )
                if (
                    clean_a not in alnum_t
                    and clean_a[:2] not in alnum_t
                    and not compound_ok
                    and not _hangul_num_in_text(clean_a, norm_t)
                ):
                    if soft_fallback:
                        para_soft.append('missing_noun_anchor:' + clean_a)
                        continue
                    raise ValueError('missing_noun_anchor:' + clean_a)
        errors = quote_errors(text, source)
        if errors:
            if soft_fallback:
                para_soft.extend(errors)
            else:
                raise ValueError(';'.join(errors))
        if para_soft:
            soft_notes.append((list(indices), '; '.join(dict.fromkeys(para_soft))))
    for note_indices, detail in soft_notes:
        if len(value['unresolved']) >= 16:
            break
        reason_text = f'자동 수리 후 표기 확인 필요({detail})'[:1000]
        if not any(
            r['source_indices'] == note_indices and r['reason'] == reason_text
            for r in value['unresolved']
        ):
            value['unresolved'].append({'source_indices': note_indices, 'reason': reason_text})
    return value


def repair_schema(indices):
    return obj(
        {
            'action': {'type': 'string', 'enum': ['replace', 'keep', 'unresolved']},
            'evidence_indices': array({'type': 'integer', 'enum': indices}, len(indices), 0),
            'replacement': {'type': 'string', 'maxLength': 1000},
            'reason': {'type': 'string', 'minLength': 1, 'maxLength': 300},
        }
    )


def validate_repair(value, spec):
    import jsonschema

    jsonschema.validate(value, spec['schema'])
    if value['action'] == 'replace':
        plain(value['replacement'])
        if not value['evidence_indices']:
            raise ValueError('replacement_requires_evidence')
    elif value['replacement']:
        raise ValueError('non_replace_text')
    if len(value['evidence_indices']) != len(set(value['evidence_indices'])):
        raise ValueError('duplicate_evidence')
    sources = ' '.join(
        s['text'] for s in spec['evidence'] if s['sentence_index'] in value['evidence_indices']
    )
    compact_sources = re.sub(r'\s+', '', sources)
    for quote in re.findall(r"'([^']+)'|‘([^’]+)’", value['replacement']):
        q = next(s for s in quote if s)
        if q not in sources and re.sub(r'\s+', '', q) not in compact_sources:
            raise ValueError('unverified_repair_quote')
    if re.search(r'https?://', value['replacement']):
        raise ValueError('new_url')
    return value


def ticket_target(article, ticket, bundle, *, base_sha256=None):
    allowed = {digest(article)} | ({base_sha256} if base_sha256 else set())
    if ticket.get('video_id') != article['video_id'] or ticket.get('article_sha256') not in allowed:
        raise ValueError('review_article_identity')
    paragraphs = {p['paragraph_id']: p for s in article['sections'] for p in s['paragraphs']}
    paragraph = paragraphs[ticket['paragraph_id']]
    a, z = ticket['span']
    if type(a) is not int or type(z) is not int or not 0 <= a < z <= len(paragraph['text']):
        raise ValueError('review_span')
    if paragraph['text'][a:z] != ticket['original']:
        raise ValueError('review_original')
    indices = ticket['evidence_indices']
    if (
        not indices
        or indices != sorted(set(indices))
        or not set(indices) <= set(paragraph['source_indices'])
    ):
        raise ValueError('review_evidence_ownership')
    if not isinstance(ticket.get('observation'), str) or not ticket['observation'].strip():
        raise ValueError('review_observation')
    return paragraph, [bundle['sentences'][i] for i in indices]


def apply_patch(article, ticket, proposal, acceptance, *, base_sha256=None):
    """A stored semantic review, including adjacent context, selects a proposal."""
    allowed = {digest(article)} | ({base_sha256} if base_sha256 else set())
    if not (
        acceptance.get('decision') == 'accepted'
        and acceptance.get('reviewer')
        and acceptance.get('proposal_sha256') == digest(proposal)
        and acceptance.get('article_sha256') in allowed
        and acceptance.get('paragraph_and_neighbors_reviewed') is True
    ):
        raise ValueError('patch_not_reviewed')
    out = copy.deepcopy(article)
    paragraph = next(
        p
        for s in out['sections']
        for p in s['paragraphs']
        if p['paragraph_id'] == ticket['paragraph_id']
    )
    a, z = ticket['span']
    if paragraph['text'][a:z] != ticket['original']:
        raise ValueError('patch_target_changed')
    if proposal['action'] == 'replace':
        paragraph['text'] = paragraph['text'][:a] + proposal['replacement'] + paragraph['text'][z:]
    return out
