"""T4 E05: Attribute-First micro-windowed generation with Hangul Jamo n-gram quote guard, visual-note scope lock, and bounded 1-shot primary repair."""
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone
import argparse
import copy
import fcntl
import hashlib
import html
import json
import re
import subprocess
import time
import uuid

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_e00 as b
if sys.path[0] != str(Path(__file__).resolve().parent):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = b.HERE
ROOT = b.ROOT
E03_EVIDENCE_RUN = HERE / 'runs/20261102T043755Z_e03_7eb65a'
PARENT = HERE / 'runs/20261103T022753Z_e04_af35e7'
CAMPAIGN_FILE = HERE / 'e05_campaign.json'

POLICY_ID = 'E05.attribute_first_jamo_quote_guard_and_local_repair.v1'
MAX_AUTO_REPAIRS = 2
MAX_REVIEW_REPAIRS = 2
MAX_TOTAL_REPAIRS = 4

REDUPLICATIVE_WHITELIST = {
    '하나하나', '차례차례', '사이사이', '구석구석', '가지가지',
    '부분부분', '하루하루', '마디마디', '순간순간', '조금조금', '따로따로',
    '끼리끼리', '단계단계',
}

_KO_PARTICLES = (
    '에서는', '으로서', '으로써', '로부터', '까지도', '이라는', '라고는',
    '에서', '으로', '부터', '까지', '처럼', '보다', '마다', '조차', '마저', '이나', '거나', '이란', '란',
    '은', '는', '이', '가', '을', '를', '에', '로', '와', '과', '의', '도', '만',
)

_UI_QUOTE_SUFFIXES = (
    '하기', '버튼', '입력란', '항목', '메뉴', '기능', '화면', '영역', '탭', '란', '창',
)

_ACTION_SUMMARY_NOUNS = {
    '포함', '제외', '삭제', '유지', '권고', '강조', '중요', '필수', '생략', '반복',
    '진입', '이동', '선택', '입력', '복사', '편집', '수집', '확인', '설정', '설명',
    '비교', '요약', '제거', '추가', '활용', '조사', '보호', '모방', '창업', '등록',
}

_CHO = [chr(x) for x in range(0x1100, 0x1113)]
_JUNG = [chr(x) for x in range(0x1161, 0x1176)]
_JONG = [''] + [chr(x) for x in range(0x11A8, 0x11C3)]

VISUAL_NOTE_PROJECTIONS = {
    'A1': (
        "시연 화면의 메모장 편집 결과에서는 '욕실 뚫어뻥 액체' 등 무관 항목이 제거되고 "
        "'변기 뚫어뻥 기구', '압력 뚫어뻥', '뚫어뻥', '미니 뚫어뻥' 네 항목이 남는다. "
        "이 구간([134, 135]) 본문은 위 시연 편집 결과로만 한정해 서술하며, 불명확한 원시 음성 전사 조각을 다시 분해해 "
        "특정 단어 단독 제외·유지 같은 별도 일반 규칙 문장을 덧붙이지 않는다(원시 발화 모호성은 unresolved에만 기록하고 프레임 시각은 본문에 적지 않는다)."
    ),
    'A2': (
        "시연의 검색 결과 화면에 표시된 실제 UI 라벨은 '연관검색어'다. "
        "화면 라벨 명칭으로만 자연스럽게 사용하고 원시 전사 오류어나 프레임 시각은 본문에 노출하지 않는다(원시 전사 모호성은 unresolved에만 기록)."
    ),
}

SYSTEM_PROMPT = """제공된 영상 원문(assigned_sentences)에 근거하여 독자가 영상 시청 없이도 내용을 완전히 이해할 수 있는 한국어 독립 설명문의 한 섹션을 작성한다.

[1. 생성 순서와 문단 구성 (Attribute-First)]
- 각 문단 객체를 생성할 때 반드시 source_indices → claim_mode → (해당 시 amendment_refs) → key_anchors → text 순서로 결정한다.
- paragraph_plan의 최소 문단 수(min_paragraphs)와 문단당 최대 문장 수(max_sentences_per_paragraph)를 지키며, 원문 시간순으로 자연스럽게 단락을 나눈다.
- 한 문단에 여러 단계를 뭉뚱그려 세부 조건·예시를 요약 생략하지 않는다. key_anchors에는 '복사', '입력', '삭제', '클릭' 같은 일반 동작 동사를 넣지 말고, 오직 해당 source_indices에 포함된 구체적인 고유명사·UI 메뉴명·포함 예시·제외(삭제) 대상 예시 단어·아라비아 수치만 각각 별도 항목으로 빠짐없이 추출하며(한 문장 안에 넣어야 할 예시와 지워야 할 예시 단어가 함께 있으면 두 예시 단어를 모두 key_anchors에 포함), 추출한 모든 항목을 해당 문단 text 안에 반드시 서술한다.
- key_anchors에 예시 어구(특히 의도적 오타 예시나 주의가 붙은 명칭)를 적을 때부터 일부 음절도 앞 문단의 표준어에 동화시키지 말고 해당 assigned_sentences 문장에 적힌 모든 음절 글자 그대로 복사해 적으며, text에서도 동일한 철자로 작은따옴표('...') 인용한다. 통계 수치·연도·인원·개수·비율(한글 수사 발화 포함)은 아라비아 숫자(예: 2023년, 53.8%, 20개)로 표기해 key_anchors와 text의 숫자를 일치시킨다.
- 각 문단은 해당 source_indices에 실제로 등장하는 대상과 예시만 서술한다. 후반 문장에서 별도로 소개되는 세부 예시나 오타 예시를 앞 문장의 기본 판매 상품이나 최초 검색 입력어에 소급 대입하지 않는다.
- previous_context_readonly는 앞선 정의나 지시어('앞에서 설명한 ~' 등)를 이해하기 위한 읽기 전용 참고 문맥이므로 현재 섹션의 새 본문으로 중복 집필하지 않는다.

[2. 문체와 화면·도구 전환의 목적지 명시]
- 모든 본문 문장은 한국어 평서형 설명체('~한다', '~할 수 있다', '~이다')로 작성한다. '~합니다/~습니다/~세요' 체나 '화자가 ~라고 말한다', '원문에서', '원문에서는', '전사본에서' 같은 메타 중계 표현은 독자용 text에 절대 쓰지 않는다(전사 한계 설명은 오직 unresolved의 reason에만 기록한다).
- 화면·도구 전환이 있는 절차는 이전 작업 직후 어느 화면·입력 위치에서 다음 동작을 수행하는지 목적지 UI 명칭을 생략하지 않고 명시한다(예: 사이트 내 기능 복사 → 메모장 붙여넣기 및 불필요 어구 편집 → 사이트/브라우저 검색창에 기본 검색어를 입력하고 엔터 실행 → 검색 결과 화면의 추가 검색어 복사 → 검색 화면 목록에서 하위 키워드를 클릭해 세부 결과 진입(메모장은 텍스트 편집기이므로 클릭 진입은 검색 화면에서 수행) → 최종 태그 입력란에 하나씩 입력).

[3. 귀속·의심 전사·시각 보완의 4가지 원칙]
1) 확인된 절차·수치·예시·의도적 오타 그대로 보존: 절차, 조건, 예외, 통계 수치, 포함 예시와 제외 예시를 빠짐없이 보존한다. 구체적인 검색어 예시·제외 키워드 예시·의도적 오타 예시는 작은따옴표('...')로 감싸며, 특히 화자가 '철자가 틀린 검색어' 등으로 설명하는 의도적 오타 예시는 일부 음절이 앞선 발음 오인식과 닮았더라도 절대 앞선 정상 상품명과 섞어 제3의 합성어를 만들지 말고 해당 문장에 적힌 모든 음절 철자 그대로 인용한다.
2) 화자의 평가·권고·추정·전언 귀속: 효과·중요도 평가('힘이 크다', '더 좋다', '중요하다', '노출에 유리하다'), 개수·방식 권고('꼭 채워 넣어라', '추천한다'), 개인 경험·추정·전언('~라고 알고 있다', '~해 준다고 한다', '예전에는/이제는')이 포함된 문단은 claim_mode를 speaker_view 또는 mixed_procedure_and_view로 지정하고, 일반 공식 정책으로 단정하지 않고 '이 설명에서는 … 중요하게 보며 … 권한다', '화자는 … 효과적이라고 설명한다'처럼 자연스럽게 귀속한다.
3) 의심 전사·음운 뭉개짐·비문 어절의 전역 보호(cautions 표시 유무와 무관, 시각 보완이 없는 경우): 문장에 cautions가 붙어 있거나, cautions가 없더라도 음성 인식 오류·발음 축약으로 어미·받침·단어가 비정상적으로 깨진 어절이나 비문 조각이 있으면 상식이나 외부 지식으로 비슷하게 들리는 다른 정식 명칭·표준 용어로 임의 치환해 본문에 쓰지 않는다(따옴표 유무와 무관). 동시에 검증된 실제 상품명이나 UI 필수 항목으로 확정 나열하지도 않는다. 단, 같은 섹션의 다른 문장이나 verified_visual_notes에서 동일 대상의 실제 정상 명칭이 이미 확인되는 단순 발음 오인식(앞 문장에서는 발음이 뭉개졌으나 뒤 문장이나 시각 노트에서 정상 명칭이 확인되는 기본 상품명 등)은 깨진 오인식 음절을 실제 상품명처럼 따옴표 인용하지 말고 섹션 내 확인된 정상 명칭으로 일관되게 서술한다(화자가 '철자가 틀린 검색어'라고 명시해 소개하는 의도적 오타 예시만 그 오타 철자 그대로 인용). 확인 근거가 없는 불명확 구간은 앞뒤 문맥과 확인된 절차로 뒷받침되는 의미 범위만 서술하고('원문에서' 같은 메타 언급 금지), 반드시 해당 문장 번호와 불명확한 어절 사유를 unresolved에 기록한다. 단독 발화·복합 어구의 부정('아니죠/아니니까')과 가능성('될 수 있겠죠') 범위를 임의로 쪼개어 무근거 유지/삭제 목록을 만들지 않는다.
4) 검증된 시각 시연 노트(verified_visual_notes가 있는 경우): '시연에서는 …'처럼 노트에 제시된 시연 결과 범위로만 한정해 자연스러운 설명문으로 서술하고 amendment_refs에 연결한다. 시각 노트가 결합된 문장의 불명확한 음성 전사 조각을 다시 분해·해석하여 별도 일반 제외/유지 규칙이나 메타 설명 문장을 덧붙이지 않으며, 발화의 모호성은 unresolved에만 기록한다. 프레임 시각('몇 초')이나 검수 과정은 독자 본문에 절대 적지 않는다.

[4. 부가 기록 및 출력 제약]
- editorial_candidates에는 본문에서 다듬어 생략한 순수 인사말·단순 전환 추임새만 기록하며(cautions가 붙은 문장이나 실질 설명 문장은 넣지 않음), 중요한 정보를 이 배열이나 unresolved로 빼돌려 생략하지 않는다.
- 제공되지 않은 그림·이미지·도표를 본문에서 지칭하지 않는다. 독자용 text에는 내부 ID(p_u09, c_u00, A1, V01 등), 타임스탬프(초), HTML 태그, 마크다운 코드 펜스를 절대 노출하지 않는다. JSON Schema만 출력한다."""


def current():
    return HERE / 'runs' / b.read(CAMPAIGN_FILE)['run_id']


def paragraph_bounds(unit):
    sents = unit['required_source_sentences']
    n = len(sents)
    chars = sum(len(s['text'].strip()) for s in sents)
    min_p = min(n, max(1, min(4, (chars + 249) // 250)))
    max_p = min(24, max(n, min_p))
    max_span = n if min_p == 1 else min(n - (min_p - 1), max(12, (n + min_p - 1) // min_p + 2))
    return {'num_sentences': n, 'source_chars': chars, 'min_paragraphs': min_p, 'max_paragraphs': max_p, 'max_span': max_span}


def unit_amendments(unit, raw_amendments):
    if unit.get('video_id') != 'Fw-8hZhnH3Q':
        return []
    ids = {s['sentence_index'] for s in unit['required_source_sentences']}
    matched = []
    for a in raw_amendments:
        overlap = sorted(set(a['source_indices']) & ids)
        if overlap:
            matched.append({
                'ref': a['id'],
                'source_indices': overlap,
                'scope': VISUAL_NOTE_PROJECTIONS.get(a['id'], a['constraints']),
            })
    return matched


def schema(unit, raw_amendments=None):
    ids = [s['sentence_index'] for s in unit['required_source_sentences']]
    bounds = paragraph_bounds(unit)
    amends = unit_amendments(unit, raw_amendments or [])

    def obj(p):
        return {'type': 'object', 'properties': p, 'required': list(p), 'additionalProperties': False}

    def arr(s, n, minimum=0):
        return {'type': 'array', 'items': s, 'maxItems': n, 'minItems': minimum}

    para_idx = {**arr({'type': 'integer', 'enum': ids}, bounds['max_span'], 1), 'uniqueItems': True}
    edit_idx = {**arr({'type': 'integer', 'enum': ids}, min(len(ids), 3), 1), 'uniqueItems': True}
    unres_idx = {**arr({'type': 'integer', 'enum': ids}, len(ids), 1), 'uniqueItems': True}

    para_props = {
        'source_indices': para_idx,
        'claim_mode': {
            'type': 'string',
            'enum': [
                'procedure_or_fact',
                'speaker_view',
                'mixed_procedure_and_view',
                'scoped_demonstration_or_uncertain_stt',
            ],
        },
    }
    if amends:
        amend_ids = [a['ref'] for a in amends]
        para_props['amendment_refs'] = {
            **arr({'type': 'string', 'enum': amend_ids}, len(amend_ids), 0),
            'uniqueItems': True,
        }
    para_props['key_anchors'] = arr({'type': 'string', 'minLength': 1, 'maxLength': 80}, 12, 1)
    para_props['text'] = {'type': 'string', 'minLength': 1, 'maxLength': 4000}

    edit_note = obj({'source_indices': edit_idx, 'reason': {'type': 'string', 'minLength': 1, 'maxLength': 600}})
    unres_note = obj({'source_indices': unres_idx, 'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000}})
    return obj({
        'unit_id': {'type': 'string', 'enum': [unit['unit_id']]},
        'paragraphs': arr(obj(para_props), bounds['max_paragraphs'], bounds['min_paragraphs']),
        'editorial_candidates': arr(edit_note, 16, 0),
        'unresolved': arr(unres_note, 16, 0),
    })


def _strip_quotes(text):
    return re.sub(r"'[^']*'|\"[^\"]*\"|“[^”]*”|‘[^’]*’", '', text)


def _to_jamo(s):
    out = []
    for ch in s:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            idx = code - 0xAC00
            out.append(_CHO[idx // 588])
            out.append(_JUNG[(idx % 588) // 28])
            jong = _JONG[idx % 28]
            if jong:
                out.append(jong)
        else:
            out.append(ch)
    return ''.join(out)


def _strip_particle(tok):
    for p in _KO_PARTICLES:
        if len(tok) > len(p) + 1 and tok.endswith(p):
            return tok[:-len(p)]
    return tok


def _build_quote_pools(unit, amend_keys):
    sents = [s.get('text', '') for s in unit['required_source_sentences']]
    prevs = [s.get('text', '') for s in unit.get('previous_context', {}).get('sentences', [])]
    extra = [VISUAL_NOTE_PROJECTIONS.get(k, '') for k in amend_keys]
    hier = unit.get('section_hierarchy', {})
    titles = [hier.get('root_title', ''), hier.get('leaf_title', '')]
    segments = sents + prevs + extra + titles
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
                ng1 = ''.join(htoks[i:i + k])
                ng2 = ''.join(stoks[i:i + k])
                if 2 <= len(ng1) <= 18:
                    ngrams.add(ng1)
                if 2 <= len(ng2) <= 18:
                    ngrams.add(ng2)
    return compact, compact_np, ngrams


def _word_grounded(w, compact, compact_np):
    cw = w
    for suf in _UI_QUOTE_SUFFIXES:
        if len(cw) > len(suf) + 1 and cw.endswith(suf):
            cw = cw[:-len(suf)]
            break
    cw = _strip_particle(cw)
    if cw in compact or cw in compact_np:
        return True
    if len(cw) >= 2 and cw.endswith('다'):
        stem = cw[:-1]
        if stem.endswith('하') and len(stem) >= 2:
            base = stem[:-1]
            if any((base + e) in compact for e in ('하', '해', '한', '할', '함')):
                return True
        if any((stem + e) in compact for e in ('다', '고', '며', '면', '니', '는', '은', '던', '어', '아', '게', '지', '죠', '요', '음', '기')):
            return True
    return False


def _find_near_miss_quotes(text, compact, compact_np, ngrams):
    errs = []
    pattern = r"'([^'\n]{2,24})'|\"([^\"\n]{2,24})\"|“([^”\n]{2,24})”|‘([^’\n]{2,24})’"
    for m in re.finditer(pattern, text):
        q = next(g for g in m.groups() if g is not None).strip()
        cq = re.sub(r'\s+', '', q)
        if not re.fullmatch(r'[가-힣]{2,16}', cq):
            continue
        if _word_grounded(cq, compact, compact_np):
            continue
        words = [w for w in q.split() if w]
        if len(words) >= 2 and all(_word_grounded(w, compact, compact_np) for w in words):
            continue
        jq = _to_jamo(cq)
        length = len(cq)
        best_r, best_cand = 0.0, ''
        for cand in ngrams:
            if abs(len(cand) - length) > 1:
                continue
            ratio = SequenceMatcher(None, jq, _to_jamo(cand)).ratio()
            if ratio > best_r:
                best_r, best_cand = ratio, cand
        thresh = 0.75 if length <= 3 else 0.74
        if best_r >= thresh:
            errs.append(f"near_miss_unverified_quote:'{q}'~'{best_cand}'({best_r:.3f})")
    return errs


_KO_NATIVE_NUM_EQUIV = {
    '1': ('하루', '한', '하나', '일', '첫', '당일'),
    '2': ('이틀', '두', '둘', '이'),
    '3': ('사흘', '세', '셋', '삼'),
    '4': ('나흘', '네', '넷', '사'),
    '5': ('닷새', '다섯', '오'),
}


def _check_anchor(anchor, text, compact_pool='', unresolved_concat=''):
    clean_a = anchor.strip().strip("'\"“”‘’")
    if not clean_a:
        raise ValueError('empty_key_anchor')
    nums = re.findall(r'\d+(?:\.\d+)?', clean_a.replace(',', ''))
    norm_t = text.replace(',', '')
    for num in nums:
        if not re.search(r'(?<!\d)' + re.escape(num) + r'(?!\d)', norm_t):
            if num in _KO_NATIVE_NUM_EQUIV and any(w in norm_t for w in _KO_NATIVE_NUM_EQUIV[num]):
                continue
            raise ValueError('missing_numeric_anchor:' + anchor)
    alnum_t = re.sub(r'[^가-힣A-Za-z0-9]', '', text)
    if (
        compact_pool
        and re.fullmatch(r'[가-힣]{2,8}', clean_a)
        and not clean_a.endswith(('기', '함', '됨'))
        and clean_a not in _ACTION_SUMMARY_NOUNS
        and clean_a in compact_pool
        and clean_a not in unresolved_concat
    ):
        if clean_a not in alnum_t and clean_a[:2] not in alnum_t:
            raise ValueError('missing_noun_anchor:' + clean_a)
    compact_t = re.sub(r'[\s,]', '', text)
    compact_a = re.sub(r'[\s,]', '', clean_a)
    return clean_a in text or compact_a in compact_t


def _has_polite_sentence_ending(unquoted):
    if re.search(r'(?:세요|해요|돼요|예요|이에요|거든요|잖아요|네요)[.!?](?:\s|$)', unquoted):
        return True
    for m in re.finditer(r'([가-힣])니다[.!?](?:\s|$)', unquoted):
        ch = ord(m.group(1))
        if 0xAC00 <= ch <= 0xD7A3 and (ch - 0xAC00) % 28 == 17:
            return True
    return False


def validate(value, unit, raw_amendments=None):
    if raw_amendments is None:
        if CAMPAIGN_FILE.exists() and (current() / 'source_amendments.json').exists():
            raw_amendments = b.read(current() / 'source_amendments.json')
        elif (PARENT / 'source_amendments.json').exists():
            raw_amendments = b.read(PARENT / 'source_amendments.json')
        elif (E03_EVIDENCE_RUN / 'source_amendments.json').exists():
            raw_amendments = b.read(E03_EVIDENCE_RUN / 'source_amendments.json')
    raw_amendments = raw_amendments or []

    b.prior.validate_schema(value, schema(unit, raw_amendments))
    sents = unit['required_source_sentences']
    ids = {s['sentence_index'] for s in sents}
    bounds = paragraph_bounds(unit)
    amends = {a['ref']: set(a['source_indices']) for a in unit_amendments(unit, raw_amendments)}
    caution_ids = {i for c in unit.get('resolved_cautions', []) for i in c.get('sentence_indices', [])}
    link_targets = {i for lk in unit.get('internal_source_links', []) for i in lk.get('to_sentence_indices', [])}

    prev_sents = unit.get('previous_context', {}).get('sentences', [])
    source_concat = ' '.join(
        [s.get('text', '') for s in sents]
        + [s.get('text', '') for s in prev_sents]
        + [VISUAL_NOTE_PROJECTIONS.get(k, '') for k in amends]
    )
    source_has_seconds = bool(re.search(r'(?:\d+|[일이삼사오육칠팔구십백]+)\s*초', ' '.join(s.get('text', '') for s in sents + prev_sents)))
    compact_pool, compact_np_pool, ngram_pool = _build_quote_pools(unit, list(amends.keys()))

    seen = set()
    para_seen = set()
    edit_seen = set()

    for key in ['paragraphs', 'editorial_candidates', 'unresolved']:
        for item in value[key]:
            sids = item['source_indices']
            if sids != sorted(set(sids)):
                raise ValueError('index_order:' + key)
            seen.update(sids)
            if key == 'paragraphs':
                para_seen.update(sids)
            elif key == 'editorial_candidates':
                edit_seen.update(sids)
            content_text = item.get('text', item.get('reason', ''))
            if not content_text.strip() or re.search(r'<[/!a-zA-Z]|' + chr(96) * 3, content_text):
                raise ValueError('non_plain_text:' + key)

    if seen != ids:
        raise ValueError('source_coverage')
    if not para_seen:
        raise ValueError('empty_paragraph_coverage')
    if caution_ids & (edit_seen - para_seen):
        raise ValueError('caution_in_editorial_candidates')
    if len(edit_seen - para_seen) > max(2, len(ids) // 3):
        raise ValueError('excessive_editorial_omission')

    starts = []
    paraphrased_anchors = []
    para_errors = []
    unresolved_concat = re.sub(r'[^가-힣A-Za-z0-9]', '', ' '.join(x.get('reason', '') for x in value['unresolved']))
    for p_idx, p in enumerate(value['paragraphs']):
        sids = p['source_indices']
        if len(sids) > bounds['max_span']:
            raise ValueError('paragraph_span_exceeded')
        core = [i for i in sids if i not in link_targets] or sids
        starts.append(min(core))

        arefs = p.get('amendment_refs', [])
        if len(set(arefs)) != len(arefs):
            raise ValueError('duplicate_amendment_refs')
        for ref in arefs:
            if ref not in amends or not (amends[ref] & set(sids)):
                raise ValueError('amendment_ref_out_of_scope:' + str(ref))

        text = p['text']
        for anchor in p['key_anchors']:
            try:
                if not _check_anchor(anchor, text, compact_pool, unresolved_concat):
                    paraphrased_anchors.append({'paragraph_index': p_idx, 'anchor': anchor})
            except ValueError as exc:
                para_errors.append(str(exc))

        for m in re.finditer(r'([가-힣]{2,4})\1', text):
            dup = m.group(0)
            if dup not in REDUPLICATIVE_WHITELIST and dup not in source_concat:
                para_errors.append('stutter_corruption:' + dup)

        if re.search(r'(?<![A-Za-z0-9_])(?:p_u\d+|c_u\d+|V0\d|A[12]|source_indices|claim_mode|key_anchors|amendment_refs|unresolved|editorial_candidates)(?![A-Za-z0-9_])', text):
            para_errors.append('internal_id_leak')

        if not source_has_seconds and re.search(r'\d+\s*초(?:(?![가-힣])|(?:에|에는|에서|의|부터|까지|와|경|쯤|간|동안)(?![가-힣]))', text):
            para_errors.append('forensic_timestamp_leak')

        para_errors.extend(_find_near_miss_quotes(text, compact_pool, compact_np_pool, ngram_pool))

        unquoted = _strip_quotes(text)
        if re.search(r'원문에서는|원문에서\b', unquoted):
            para_errors.append('meta_source_relay')
        if _has_polite_sentence_ending(unquoted):
            para_errors.append('polite_ending_style_violation')

    if para_errors:
        raise ValueError(' | '.join(dict.fromkeys(para_errors)))

    if starts != sorted(starts):
        raise ValueError('non_monotonic_paragraph_order')

    blocks = [{'kind': 'paragraph', **p} for p in value['paragraphs']]
    return {'unit_id': unit['unit_id'], 'content': value, 'blocks': blocks, 'paraphrased_anchors': paraphrased_anchors, 'quote_unresolved': []}


def parse(raw, status, unit, raw_amendments=None):
    if status != 200:
        raise ValueError('http_error:' + str(status))
    outer = b.prior.loads(raw.decode())
    choices = outer.get('choices', [])
    if len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
        raise ValueError('incomplete_generation:' + str(choices[0].get('finish_reason') if choices else None))
    msg = choices[0]['message']
    if msg.get('reasoning') or msg.get('reasoning_content'):
        raise ValueError('unexpected_reasoning')
    return validate(b.prior.loads(msg['content']), unit, raw_amendments), outer


_EXT_PARTICLES = tuple(
    sorted(set(list(_KO_PARTICLES) + ['이라고', '라고', '이라서', '라서', '이란', '란', '이라', '라']), key=len, reverse=True)
)
_PRED_SUFFIXES = (
    '습니다', '겠습니다', '드립니다', '드릴게요', '드리고요', '드려요',
    '시면', '시기', '시고', '셔야', '보시면은', '가지고',
    '하다', '되다', '있다', '없다', '이다', '니다', '세요', '해요', '돼요', '예요',
    '거예요', '건데요', '거든요', '잖아요', '는데요', '볼게요', '볼까요', '렸죠',
    '가능한지', '불가능한지', '살아남고', '그러니까', '마찬가지', '엄청나게', '간략하게', '형식적인',
)


def _strip_particle_ext(tok):
    for p in _EXT_PARTICLES:
        if len(tok) > len(p) + 1 and tok.endswith(p):
            return tok[:-len(p)]
    return tok


def _is_compound_noun_stem(st):
    return len(st) >= 4 and not st.endswith(_PRED_SUFFIXES)


def _detect_intrinsic_stt_cautions(unit, units_in_phase):
    stem_counts = {}
    for u in units_in_phase:
        for s in u['required_source_sentences']:
            for tok in re.findall(r'[가-힣]{4,}', s.get('text', '')):
                st = _strip_particle_ext(tok)
                if _is_compound_noun_stem(st):
                    stem_counts[st] = stem_counts.get(st, 0) + 1
    frequent_stems = {st: cnt for st, cnt in stem_counts.items() if cnt >= 2}

    intrinsic_by_idx = {}
    for s in unit['required_source_sentences']:
        sid = s['sentence_index']
        txt = s.get('text', '')
        notes = []
        for tok in re.findall(r'[가-힣]{4,}', txt):
            st = _strip_particle_ext(tok)
            if _is_compound_noun_stem(st) and stem_counts.get(st) == 1:
                for fst in frequent_stems:
                    if len(fst) == len(st) and fst != st:
                        diff_syl = sum(1 for c1, c2 in zip(st, fst) if c1 != c2)
                        r = SequenceMatcher(None, _to_jamo(st), _to_jamo(fst)).ratio()
                        if diff_syl == 1 and r >= 0.78:
                            notes.append(
                                f"내재적 전사 이상 감지: 어절 '{st}'은 동일 문서 내 반복 등장 용어와 1음절만 다른 단일 출현 변이형(자모 유사도 {r:.2f})이므로, 다른 표준 용어로 임의 교정해 인용하지 말고 '{st}' 철자 그대로 인용하거나 확실한 사실만 자연스러운 평서문으로 서술한 뒤('원문에서' 같은 메타 표현 금지), 이 문장 인덱스를 반드시 unresolved에 기록한다."
                            )
        words = re.findall(r'[가-힣]{2,}', txt)
        subj_clauses = []
        for idx_w, w in enumerate(words):
            if w.endswith(('이', '가', '은', '는', '이라고', '라고')) and len(_strip_particle_ext(w)) >= 2:
                st = _strip_particle_ext(w)
                if st not in ('거라', '거예', '하는', '있는', '보시', '여기', '이거', '그냥', '말이'):
                    pred = words[idx_w + 1] if idx_w + 1 < len(words) else ''
                    subj_clauses.append((st, pred))
        if len(subj_clauses) >= 4:
            for i in range(len(subj_clauses) - 3):
                s1, p1 = subj_clauses[i]
                s2, p2 = subj_clauses[i + 1]
                s3, p3 = subj_clauses[i + 2]
                s4, p4 = subj_clauses[i + 3]
                if s1 == s3 and s2 == s4 and s1 != s2 and (p1 != p3 or p2 != p4):
                    notes.append(
                        f"내재적 전사 이상 감지: 한 문장 내 주어절 교차 재시작('{s1}'/'{s2}') 및 서술어 호응 충돌('{p1}'·'{p3}'/'{p2}'·'{p4}')이 있는 음성 인식 오류 의심 구간이다. 불확실한 조건(높고 낮음 등)을 임의로 추측·단정하지 말고 앞뒤 문맥상 확인된 비유와 결과만 자연스러운 평서문으로 서술한 뒤('원문에서' 같은 메타 표현 금지), 이 문장 인덱스를 반드시 unresolved에 기록한다."
                    )
                    break
        if notes:
            intrinsic_by_idx[sid] = notes
    return intrinsic_by_idx


def project_unit_payload(unit, units_in_phase, outline, raw_amendments, repair_note=None):
    idx_in_phase = next(i for i, u in enumerate(units_in_phase) if u['unit_id'] == unit['unit_id'])
    prev_title = units_in_phase[idx_in_phase - 1]['section_hierarchy']['leaf_title'] if idx_in_phase > 0 else None
    next_title = units_in_phase[idx_in_phase + 1]['section_hierarchy']['leaf_title'] if idx_in_phase + 1 < len(units_in_phase) else None
    bounds = paragraph_bounds(unit)
    amends = unit_amendments(unit, raw_amendments)

    cautions_by_idx = {}
    for c in unit.get('resolved_cautions', []):
        for sid in c.get('sentence_indices', []):
            cautions_by_idx.setdefault(sid, []).append(c['guidance'])

    intrinsic_by_idx = _detect_intrinsic_stt_cautions(unit, units_in_phase)
    for sid, notes in intrinsic_by_idx.items():
        if sid not in cautions_by_idx:
            cautions_by_idx[sid] = notes

    amends_by_idx = {}
    for a in amends:
        for sid in a['source_indices']:
            amends_by_idx.setdefault(sid, []).append({'ref': a['ref'], 'scope': a['scope']})

    assigned = []
    for s in unit['required_source_sentences']:
        sid = s['sentence_index']
        row = {'index': sid, 'text': s['text']}
        if sid in amends_by_idx:
            row['verified_visual_notes'] = amends_by_idx[sid]
        elif sid in cautions_by_idx:
            row['cautions'] = cautions_by_idx[sid]
        assigned.append(row)

    prev_ctx = unit.get('previous_context', {})
    prev_readonly = [{'index': s['sentence_index'], 'text': s['text']} for s in prev_ctx.get('sentences', [])]
    prev_links = [
        {
            'from_index': lk['from_sentence_index'],
            'refers_to_indices': lk['to_sentence_indices'],
            'relation': lk['relation'],
        }
        for lk in prev_ctx.get('links_from_current_unit', [])
    ]
    internal_links = [
        {
            'from_index': lk['from_sentence_index'],
            'refers_to_indices': lk['to_sentence_indices'],
            'relation': lk['relation'],
        }
        for lk in unit.get('internal_source_links', [])
    ]

    root_titles = [r['title'] for r in outline.get('roots', [])] if isinstance(outline, dict) and 'roots' in outline else []
    payload = {
        'video_id': unit['video_id'],
        'document_roots_overview': root_titles,
        'section_context': {
            'unit_position': unit['section_hierarchy']['unit_position'],
            'root_title': unit['section_hierarchy']['root_title'],
            'leaf_title': unit['section_hierarchy']['leaf_title'],
            'prev_leaf_title': prev_title,
            'next_leaf_title': next_title,
        },
        'paragraph_plan': {
            'min_paragraphs': bounds['min_paragraphs'],
            'max_sentences_per_paragraph': bounds['max_span'],
        },
        'previous_context_readonly': prev_readonly,
        'previous_context_links': prev_links,
        'internal_back_references': internal_links,
        'assigned_sentences': assigned,
    }
    if repair_note:
        payload['local_repair_directive'] = repair_note
    return payload


def make_body(payload, unit, raw_amendments):
    wire_schema = b.runtime_schema(schema(unit, raw_amendments))
    body = {
        'model': 'qwen3.8-27b',
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': b.wire(payload).decode()},
        ],
        'temperature': 0,
        'max_tokens': 4096,
        'stream': False,
        'chat_template_kwargs': {'enable_thinking': False},
        'response_format': {
            'type': 'json_schema',
            'json_schema': {'name': 'stage3_t4_e05_unit', 'strict': True, 'schema': wire_schema},
        },
    }
    if len(b.wire(body)) > 4194304:
        raise ValueError('request_limit:' + unit['unit_id'])
    return body


def build_jobs():
    b.bound_sha(b.INPUT, b.INPUT_SHA)
    d = b.read(b.INPUT)
    if not all(d[k] is True for k in ['stage_gate_accepted', 'T3_complete', 't4_ready']) or d['reader_policy'] != 'excluded':
        raise ValueError('input_gate')
    confref = d['validation_only']['resolved_confirmation_dossiers_ref']
    b.bound_sha(ROOT / confref['path'], confref['sha256'])
    conf = b.read(ROOT / confref['path'])

    primary = d['generation_units']
    confirm = conf['units']
    b.verify_units(primary, d['primary_video_id'], [0, 217])
    b.verify_units(confirm, conf['video_id'], [134, 192])

    if b.read(E03_EVIDENCE_RUN / 'evidence_review.json')['decision'] != 'accepted':
        raise ValueError('parent_evidence_not_accepted')
    raw_amendments = b.read(E03_EVIDENCE_RUN / 'source_amendments.json')

    jobs = []
    for phase, units, outline in [
        ('primary', primary, d['document_outline']),
        ('confirmation', confirm, conf['document_outline']),
    ]:
        for unit in units:
            payload = project_unit_payload(unit, units, outline, raw_amendments)
            body = make_body(payload, unit, raw_amendments)
            jobs.append({'job_id': unit['unit_id'], 'phase': phase, 'unit': unit, 'body': body, 'bounds': paragraph_bounds(unit)})
    return d, conf, raw_amendments, jobs


def check_runtime_and_tokens(jobs):
    grammar = (
        "import json,sys,xgrammar;"
        "[xgrammar.Grammar.from_json_schema(j['body']['response_format']['json_schema']['schema']) for j in json.load(sys.stdin)];"
        "print('23 grammars valid')"
    )
    g = subprocess.run(['docker', 'exec', '-i', b.old.CONTAINER, 'python3', '-c', grammar], input=b.wire(jobs), capture_output=True, timeout=180)
    if g.returncode:
        raise ValueError('runtime_grammar_failed:' + g.stderr.decode()[-2000:])
    tp = b.old.token_plan(jobs)
    return g.stdout.decode().strip(), tp


def dry_check():
    b.identity(PARENT)
    _, _, _, jobs = build_jobs()
    grammar_msg, tp = check_runtime_and_tokens(jobs)
    toks = [row['input_tokens'] for row in tp['jobs']]
    summary = {
        'status': 'offline_dry_check_passed_zero_http_calls',
        'candidate': 'E05',
        'policy': POLICY_ID,
        'jobs': len(jobs),
        'grammar_check': grammar_msg,
        'min_input_tokens': min(toks),
        'max_input_tokens': max(toks),
        'total_input_tokens_all_23': sum(toks),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def prepare():
    prior_run_path = PARENT
    extra_dirs = []
    prompt_rev = 'P0'
    b.identity(PARENT)
    pc = b.read(PARENT / 'config.json')
    previous = pc['prior_records'] + b.old.records(PARENT)
    if len(previous) != 15 or b.read(PARENT / 'reviews/G1.json')['decision'] != 'rejected':
        raise ValueError('parent_status:' + str(len(previous)))
    for rel, h in b.read(PARENT / 'result_manifest.json').items():
        if rel != 'result_manifest.json':
            b.bound_sha(PARENT / rel, h)

    critical = dict(pc['critical_files'])
    if CAMPAIGN_FILE.exists():
        prev_run = current()
        close_path = prev_run / 'closeout.json'
        manifest_path = prev_run / 'result_manifest.json'
        prev_cfg = b.read(prev_run / 'config.json')
        rr = b.old.records(prev_run)
        is_prior_halt = (
            close_path.exists()
            and manifest_path.exists()
            and prev_cfg.get('prompt_revision') in ('P0', 'R1', 'R2', 'R3')
            and len(rr) <= 25
            and b.read(close_path).get('T4_complete') is False
        )
        if not is_prior_halt:
            raise ValueError('existing_E05')
        for rel, h in b.read(manifest_path).items():
            if rel != 'result_manifest.json':
                b.bound_sha(prev_run / rel, h)
        critical.update(prev_cfg.get('critical_files', {}))
        previous = prev_cfg['prior_records'] + rr
        prior_run_path = prev_run
        extra_dirs.append(prev_run)
        rev_map = {'P0': 'R1', 'R1': 'R2', 'R2': 'R3', 'R3': 'R4'}
        prompt_rev = rev_map[prev_cfg.get('prompt_revision')]

    d, _, raw_amendments, jobs = build_jobs()
    grammar_msg, tp = check_runtime_and_tokens(jobs)

    for p in PARENT.rglob('*'):
        if p.is_file():
            critical[str(p)] = b.sha(p)
    for ed in extra_dirs:
        for p in ed.rglob('*'):
            if p.is_file():
                critical[str(p)] = b.sha(p)
    for p in [HERE / 'run_e05.py', HERE / 'test_e05.py']:
        critical[str(p)] = b.sha(p)

    rid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_e05_' + uuid.uuid4().hex[:6]
    run = HERE / 'runs' / rid
    run.mkdir(parents=True)

    g1_own_cap = 3
    g2_own_cap = 7
    max_own_calls = 23 + MAX_TOTAL_REPAIRS
    g1_family_cap = max(27, len(previous) + g1_own_cap)
    g2_family_cap = max(27, len(previous) + g2_own_cap)
    full_family_cap = len(previous) + max_own_calls
    config = {
        'candidate': 'E05',
        'policy': POLICY_ID,
        'prompt_revision': prompt_rev,
        'schema_revisions': 0,
        'endpoint': pc['endpoint'],
        'prior_run': str(prior_run_path),
        'prior_records': previous,
        'runtime_grammar_check': grammar_msg,
        'critical_files': critical,
        'limits': {
            'family_calls_before_G1': g1_family_cap,
            'family_calls_after_G1_for_G2': g2_family_cap,
            'family_calls_if_G2_accepted': full_family_cap,
            'family_http_seconds': 7200,
            'g1_diagnostic_calls': g1_own_cap,
            'g1_diagnostic_http_seconds': 900,
            'g2_diagnostic_calls': g2_own_cap,
            'g2_diagnostic_http_seconds': 2100,
            'baseline_candidate_calls': 23,
            'max_candidate_calls': max_own_calls,
            'max_auto_validator_repairs': MAX_AUTO_REPAIRS,
            'max_reviewed_local_repairs': MAX_REVIEW_REPAIRS,
            'max_total_primary_repairs': MAX_TOTAL_REPAIRS,
            'confirmation_repairs': 0,
            'timeout': 300,
            'retry': 0,
        },
        'token_identity': {k: tp[k] for k in ['transformers', 'processor', 'template_sha256', 'runtime_environment']},
        'authorization': '2026-11-03 user: E05 final T4 candidate execution, verification, generalization evaluation, and T5 readiness check',
    }

    for name, obj in [
        ('input.json', d),
        ('source_amendments.json', raw_amendments),
        ('config.json', config),
        ('plan.json', jobs),
        ('token_plan.json', tp),
    ]:
        b.create(run / name, obj)
    (run / 'spec_snapshot.md').write_bytes(b.SPEC.read_bytes())
    (run / 'source_code').mkdir()
    for name in ['run_e05.py', 'test_e05.py']:
        (run / 'source_code' / name).write_bytes((HERE / name).read_bytes())
    b.create(run / 'state.json', {
        'frozen': {n: b.sha(run / n) for n in ['config.json', 'plan.json', 'token_plan.json', 'input.json', 'source_amendments.json', 'spec_snapshot.md']}
    })
    b.write(CAMPAIGN_FILE, {'run_id': rid})
    print(b.wire({'run': str(run), 'prior_calls': len(previous), 'jobs': len(jobs)}).decode())


original_reserve = b.old.reserve


def reserve(records, timeout=300, max_calls=27, max_seconds=7200):
    run = current()
    own = b.old.records(run)
    cfg = b.read(run / 'config.json')['limits']
    if (run / 'continuation_full.json').exists():
        x = b.read(run / 'continuation_full.json')
        b.bound_sha(run / 'reviews/G1.json', x['G1_review_sha256'])
        b.bound_sha(run / 'reviews/G2.json', x['G2_review_sha256'])
        b.review_ok(run, 'G1', b.GATES['G1'])
        b.review_ok(run, 'G2', b.GATES['G2'])
        if len(own) >= cfg['max_candidate_calls']:
            raise ValueError('candidate_calls_exceeded')
        return min(
            original_reserve(records, 300, cfg['family_calls_if_G2_accepted'], 7200),
            original_reserve(own, 300, cfg['max_candidate_calls'], 7200),
        )
    if (run / 'continuation_g2.json').exists():
        x = b.read(run / 'continuation_g2.json')
        b.bound_sha(run / 'reviews/G1.json', x['G1_review_sha256'])
        b.review_ok(run, 'G1', b.GATES['G1'])
        if len(own) >= cfg['g2_diagnostic_calls']:
            raise ValueError('g2_gate_not_accepted')
        return min(
            original_reserve(records, 300, cfg['family_calls_after_G1_for_G2'], 7200),
            original_reserve(own, 300, cfg['g2_diagnostic_calls'], cfg['g2_diagnostic_http_seconds']),
        )
    if len(own) >= cfg['g1_diagnostic_calls']:
        raise ValueError('g1_diagnostic_gate_not_accepted')
    return min(
        original_reserve(records, 300, cfg['family_calls_before_G1'], 7200),
        original_reserve(own, 300, cfg['g1_diagnostic_calls'], cfg['g1_diagnostic_http_seconds']),
    )


def _repair_counts(own_records):
    archived = [r for r in own_records if r.get('superseded')]
    auto_c = sum(1 for r in archived if r.get('repair_kind') == 'auto_validator_repair')
    rev_c = sum(1 for r in archived if r.get('repair_kind') == 'reviewed_local_repair')
    return len(archived), auto_c, rev_c


def _archive_attempt1(run, jid, status_label, repair_kind, note):
    arch_j = run / 'journal' / f'{jid}__attempt1.json'
    if arch_j.exists():
        raise ValueError('unit_repair_limit_exceeded:' + jid)
    rec1 = b.read(run / 'journal' / f'{jid}.json')
    rec1.update(
        attempt=1,
        superseded=True,
        status=status_label,
        repair_kind=repair_kind,
        repair_note=note,
    )
    b.create(arch_j, rec1)
    (run / 'journal' / f'{jid}.json').unlink()
    for sub, ext in [
        ('requests', '.json'),
        ('responses', '.body'),
        ('responses', '.headers.json'),
        ('parsed', '.json'),
    ]:
        src = run / sub / f'{jid}{ext}'
        if src.exists():
            dst = run / sub / f'{jid}__attempt1{ext}'
            if dst.exists():
                raise ValueError('immutable_exists:' + str(dst))
            src.rename(dst)


def _build_repair_job(run, job, directive):
    d = b.read(run / 'input.json')
    raw_amendments = b.read(run / 'source_amendments.json')
    primary_units = d['generation_units']
    payload = project_unit_payload(job['unit'], primary_units, d['document_outline'], raw_amendments, repair_note=directive)
    body = make_body(payload, job['unit'], raw_amendments)
    rjob = {'job_id': job['job_id'], 'phase': job['phase'], 'unit': job['unit'], 'body': body, 'bounds': job['bounds']}
    b.old.token_plan([rjob])
    return rjob


def _execute_single_attempt(run, c, job, attempt_no=1, repair_kind=None):
    jid = job['job_id']
    p = run / 'journal' / f'{jid}.json'
    if p.exists():
        raise ValueError('already_sent_no_resend:' + jid)
    own = b.old.records(run)
    if any(r['status'] in ('started', 'unknown', 'failed') for r in own):
        raise ValueError('prior_technical_failure')

    timeout = reserve(c['prior_records'] + own, 300, 27, 7200)
    request = b.wire(job['body'])
    b.create(run / 'requests' / f'{jid}.json', job['body'])
    rec = {
        'job_id': jid,
        'attempt': attempt_no,
        'status': 'started',
        'reserved_seconds': timeout,
        'request_sha256': hashlib.sha256(request).hexdigest(),
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    if repair_kind:
        rec['repaired_via'] = repair_kind
    b.create(p, rec)
    start = time.monotonic()
    raw_amendments = b.read(run / 'source_amendments.json')
    raw = b''
    try:
        def headers(status, values):
            b.create(run / 'responses' / f'{jid}.headers.json', {'status': status, 'headers': values})
        resp = b.old.HttpTransport().send(c['endpoint'], request, timeout, 4194304, headers)
        raw = resp['body']
        (run / 'responses' / f'{jid}.body').write_bytes(raw)
        rec.update(http_status=resp['status'], response_sha256=hashlib.sha256(raw).hexdigest(), response_complete=resp['complete'])
        try:
            outer = b.prior.loads(raw.decode())
            rec.update(usage=outer.get('usage'), finish_reason=outer.get('choices', [{}])[0].get('finish_reason'))
        except Exception:
            outer = None
        if not resp['complete']:
            rec.update(status='unknown', error=resp['error'])
        else:
            try:
                parsed, outer = parse(raw, resp['status'], job['unit'], raw_amendments)
                observed = outer['usage']['prompt_tokens']
                expected = job['token_plan']['input_tokens']
                if observed > 12000 or observed + 4096 + 1024 > 32768 or observed != expected:
                    raise ValueError(f'token_count_mismatch:{observed}!={expected}')
                b.create(run / 'parsed' / f'{jid}.json', parsed)
                rec.update(status='success')
            except Exception as exc:
                rec.update(status='failed', error=str(exc))
    except Exception as exc:
        rec.update(status='unknown', error=type(exc).__name__ + ':' + str(exc))
    rec['http_seconds'] = round(time.monotonic() - start, 6)
    b.write(p, rec)
    return rec


def send_e05(jid):
    run = current()
    c = b.identity(run)
    jobs = b.read(run / 'plan.json')
    job = next(j for j in jobs if j['job_id'] == jid)
    primary = [j['job_id'] for j in jobs if j['phase'] == 'primary']
    if jid not in b.GATES['G1']:
        b.review_ok(run, 'G1', b.GATES['G1'])
    if jid not in b.ORDER:
        b.review_ok(run, 'G2', b.GATES['G2'])
    if job['phase'] == 'confirmation':
        if not (run / 'confirmation_freeze.json').exists():
            raise ValueError('confirmation_not_frozen')
        fz = b.read(run / 'confirmation_freeze.json')
        b.review_ok(run, 'primary', primary)
        b.bound_sha(run / 'reviews/primary.json', fz['primary_review_sha256'])
        b.bound_sha(run / 'plan.json', fz['plan_sha256'])
        b.bound_sha(HERE / 'run_e05.py', fz['code_sha256'])

    rec = _execute_single_attempt(run, c, job, attempt_no=1)
    can_auto_repair = (
        rec['status'] == 'failed'
        and job['phase'] == 'primary'
        and not (run / 'confirmation_freeze.json').exists()
        and rec.get('http_status') == 200
        and rec.get('finish_reason') == 'stop'
        and rec.get('response_complete') is True
        and not str(rec.get('error', '')).startswith(('http_error:', 'incomplete_generation:', 'unexpected_reasoning', 'token_count_mismatch'))
    )
    if can_auto_repair:
        own_now = b.old.records(run)
        tot_r, auto_r, _ = _repair_counts(own_now)
        if auto_r < MAX_AUTO_REPAIRS and tot_r < MAX_TOTAL_REPAIRS and not (run / 'journal' / f'{jid}__attempt1.json').exists():
            err_msg = rec.get('error', '')
            bad_quotes = re.findall(r"near_miss_unverified_quote:'([^']+)'", err_msg)
            def _mask_bad_quotes(s):
                out = s
                for bq in bad_quotes:
                    out = out.replace(bq, '[합성어금지:assigned_sentences의실제오타철자그대로인용]')
                return out
            prior_paras = []
            try:
                raw_outer = b.prior.loads((run / 'responses' / f'{jid}.body').read_text(encoding='utf-8'))
                raw_inner = b.prior.loads(raw_outer['choices'][0]['message']['content'])
                prior_paras = [
                    {
                        'source_indices': x.get('source_indices', []),
                        'key_anchors': [_mask_bad_quotes(a) for a in x.get('key_anchors', [])],
                        'text': _mask_bad_quotes(x.get('text', '')),
                    }
                    for x in raw_inner.get('paragraphs', [])
                ]
            except Exception:
                prior_paras = []
            directive = {
                'trigger': 'cpu_validator_rejection',
                'validation_errors': err_msg.split(' | '),
                'rejected_unverified_quotes_do_not_output': bad_quotes,
                'prior_paragraphs': prior_paras,
                'instruction': (
                    "직전 초안(prior_paragraphs)에서 validation_errors로 지적된 항목만 정확히 국소 수정하라. "
                    "1) rejected_unverified_quotes_do_not_output에 적힌 어구는 원문에 없는 잘못된 합성어이므로 절대 다시 출력하지 말고, "
                    "prior_paragraphs에서 '[합성어금지:assigned_sentences의실제오타철자그대로인용]'으로 표시된 자리만 해당 assigned_sentences 문장에 실제로 적힌 오타 철자 그대로 교체하라. "
                    "반면 prior_paragraphs에 그대로 남아 있는 정상 확인된 기본 판매 상품명이나 UI 라벨('뚫어뻥', '연관검색어' 등)은 깨진 원시 음성 전사어로 되돌리지 말고 그대로 유지하라. "
                    "2) missing_noun_anchor 또는 missing_numeric_anchor로 지적된 누락 예시(제외 예시·포함 예시 등)와 수치를 해당 문단 text 안에 문맥 및 화자 평가·권고 귀속에 맞게 빠짐없이 서술하라. "
                    "3) 독자용 text 안에 '원문에서', '원문에서는', '전사본에서' 같은 메타 표현을 절대 쓰지 말고(전사 모호성은 unresolved의 reason에만 기록), 한국어 평서형 설명체('~한다/~이다')를 유지하라."
                ),
            }
            _archive_attempt1(run, jid, 'repaired_validation_failure', 'auto_validator_repair', err_msg)
            rjob = _build_repair_job(run, job, directive)
            rec = _execute_single_attempt(run, c, rjob, attempt_no=2, repair_kind='auto_validator_repair')

    render(run)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def repair_e05(jid, reason):
    if not reason or not reason.strip():
        raise ValueError('repair_reason_required')
    run = current()
    c = b.identity(run)
    if (run / 'confirmation_freeze.json').exists():
        raise ValueError('frozen_no_repairs_allowed')
    jobs = b.read(run / 'plan.json')
    job = next(j for j in jobs if j['job_id'] == jid)
    if job['phase'] != 'primary':
        raise ValueError('confirmation_repair_prohibited')
    p = run / 'journal' / f'{jid}.json'
    if not p.exists() or b.read(p).get('status') != 'success':
        raise ValueError('repair_requires_completed_attempt1')
    own_now = b.old.records(run)
    tot_r, _, rev_r = _repair_counts(own_now)
    if rev_r >= MAX_REVIEW_REPAIRS or tot_r >= MAX_TOTAL_REPAIRS:
        raise ValueError('reviewed_repair_budget_exceeded')
    if (run / 'journal' / f'{jid}__attempt1.json').exists():
        raise ValueError('unit_repair_limit_exceeded:' + jid)

    prev_parsed = b.read(run / 'parsed' / f'{jid}.json')['content']
    directive = {
        'trigger': 'reviewed_local_semantic_repair',
        'defect_summary': reason.strip(),
        'prior_paragraphs': [
            {'source_indices': x['source_indices'], 'text': x['text']}
            for x in prev_parsed.get('paragraphs', [])
        ],
        'instruction': (
            "위 defect_summary에 지적된 원문 대조 결함만 assigned_sentences와 cautions·verified_visual_notes 근거 범위 안에서 정확히 바로잡고, "
            "나머지 문단의 정상 절차·수치·예시·귀속과 평서형 설명체는 누락 없이 보존하라."
        ),
    }
    _archive_attempt1(run, jid, 'superseded_by_local_repair', 'reviewed_local_repair', reason.strip())
    rjob = _build_repair_job(run, job, directive)
    rec = _execute_single_attempt(run, c, rjob, attempt_no=2, repair_kind='reviewed_local_repair')
    render(run)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def extend_g2():
    run = current()
    b.identity(run)
    b.review_ok(run, 'G1', b.GATES['G1'])
    cap = b.read(run / 'config.json')['limits']['family_calls_after_G1_for_G2']
    b.create(run / 'continuation_g2.json', {
        'G1_review_sha256': b.sha(run / 'reviews/G1.json'),
        'unlocked_gate': 'G2',
        'family_calls_cap': cap,
    })


def extend_full():
    run = current()
    b.identity(run)
    b.review_ok(run, 'G1', b.GATES['G1'])
    b.review_ok(run, 'G2', b.GATES['G2'])
    cap = b.read(run / 'config.json')['limits']['family_calls_if_G2_accepted']
    b.create(run / 'continuation_full.json', {
        'G1_review_sha256': b.sha(run / 'reviews/G1.json'),
        'G2_review_sha256': b.sha(run / 'reviews/G2.json'),
        'unlocked_gate': 'G3_and_G4',
        'family_calls_cap': cap,
        'family_http_seconds': 7200,
    })


def freeze():
    run = current()
    b.identity(run)
    ids = [j['job_id'] for j in b.read(run / 'plan.json') if j['phase'] == 'primary']
    b.review_ok(run, 'primary', ids)
    b.create(run / 'confirmation_freeze.json', {
        'primary_review_sha256': b.sha(run / 'reviews/primary.json'),
        'plan_sha256': b.sha(run / 'plan.json'),
        'code_sha256': b.sha(HERE / 'run_e05.py'),
    })


def render(run):
    jobs = b.read(run / 'plan.json')
    readable = []
    comparison = []
    draft = []
    for j in jobs:
        p = run / 'parsed' / (j['job_id'] + '.json')
        if not p.exists():
            continue
        v = b.read(p)
        title = j['unit']['section_hierarchy']['leaf_title']
        draft.append({'phase': j['phase'], 'job_id': j['job_id'], 'title': title, **v})
        content = '<h2>' + html.escape(title) + '</h2>' + ''.join(
            '<p>' + html.escape(block['text']) + '</p>' for block in v['blocks']
        )
        if j['phase'] == 'primary':
            readable.append(content)
        sources = '\n'.join(f"[{s['sentence_index']}] {s['text']}" for s in j['unit']['required_source_sentences'])
        comparison.append(
            f"<h2>{html.escape(j['phase'])} / {html.escape(j['job_id'])} — {html.escape(title)}</h2>"
            f"<pre>{html.escape(sources)}</pre>{content}"
            f"<pre>{html.escape(json.dumps(v['content'], ensure_ascii=False, indent=2))}</pre>"
        )
    style = (
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<style>body{max-width:860px;margin:24px auto;padding:18px;line-height:1.85;font-family:sans-serif}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:12px;border-radius:6px}</style>'
    )
    (run / 'result.html').write_text(style + ''.join(readable))
    (run / 'review.html').write_text(style + ''.join(comparison))
    primary_draft = [d for d in draft if d['phase'] == 'primary']
    confirmation_draft = [d for d in draft if d['phase'] == 'confirmation']
    b.write(run / 'draft.json', {'scope': 'primary_only_reader_draft', 'units': primary_draft})
    (run / 'draft.md').write_text(
        '\n\n'.join('## ' + d['title'] + '\n\n' + '\n\n'.join(x['text'] for x in d['blocks']) for d in primary_draft)
    )
    if confirmation_draft:
        b.write(run / 'confirmation/draft.json', {'scope': 'confirmation_only_separate_from_primary', 'units': confirmation_draft})
        (run / 'confirmation/draft.md').write_text(
            '\n\n'.join('## ' + d['title'] + '\n\n' + '\n\n'.join(x['text'] for x in d['blocks']) for d in confirmation_draft)
        )


def _rel_ref(path):
    p = Path(path).resolve()
    try:
        rel = str(p.relative_to(ROOT.resolve()))
    except ValueError:
        rel = str(p)
    return {'path': rel, 'sha256': b.sha(p)}


def build_t5_input(run, d, c, jobs, own, allrr):
    primary_jobs = [j for j in jobs if j['phase'] == 'primary']
    confirm_jobs = [j for j in jobs if j['phase'] == 'confirmation']
    active_by_id = {
        j['job_id']: b.read(run / 'journal' / f"{j['job_id']}.json")
        for j in jobs
        if (run / 'journal' / f"{j['job_id']}.json").exists()
    }
    tot_r, auto_r, rev_r = _repair_counts(own)

    deferred_by_id = {}
    selected_units = []
    for order_idx, j in enumerate(primary_jobs):
        jid = j['job_id']
        unit = j['unit']
        parsed_path = run / 'parsed' / (jid + '.json')
        parsed = b.read(parsed_path)
        content = parsed['content']
        for asset in unit.get('assets', {}).get('resolved_assets', []):
            aid = asset['asset_id']
            if aid not in deferred_by_id:
                deferred_by_id[aid] = {
                    **asset,
                    'asset_action': unit['assets'].get('asset_action', 'defer'),
                    'referenced_by_units': [],
                    'used_as_t4_prose_fact': False,
                    't5_responsibility': 'decide_placement_or_omission_and_verify_mobile_legibility',
                }
            deferred_by_id[aid]['referenced_by_units'].append(jid)

        paragraphs = []
        for p_idx, p_obj in enumerate(content['paragraphs']):
            row = {
                'paragraph_index': p_idx,
                'source_indices': p_obj['source_indices'],
                'claim_mode': p_obj['claim_mode'],
                'key_anchors': p_obj['key_anchors'],
                'text': p_obj['text'],
            }
            if 'amendment_refs' in p_obj:
                row['amendment_refs'] = p_obj['amendment_refs']
            paragraphs.append(row)

        selected_units.append({
            'unit_order': order_idx,
            'unit_id': jid,
            'video_id': unit['video_id'],
            'section_hierarchy': unit['section_hierarchy'],
            'source_range': unit['source_range'],
            'attempt': active_by_id[jid].get('attempt', 1),
            'response_sha256': active_by_id[jid]['response_sha256'],
            'parsed_sha256': b.sha(parsed_path),
            'paragraphs': paragraphs,
            'editorial_candidates': content['editorial_candidates'],
            'unresolved': content['unresolved'],
            'previous_context_links': unit.get('previous_context', {}).get('links_from_current_unit', []),
            'internal_source_links': unit.get('internal_source_links', []),
            'asset_policy': {
                'asset_ids': unit.get('assets', {}).get('asset_ids', []),
                'asset_action': unit.get('assets', {}).get('asset_action', 'skip'),
            },
        })

    raw_amends = b.read(run / 'source_amendments.json')
    return {
        'schema_version': 'extractube.stage3.t5.input.experimental.v1',
        'stage_gate_accepted': True,
        'T4_complete': True,
        't5_ready': True,
        'user_accepted': False,
        'final_article_user_accepted': False,
        'selected_t4_candidate': 'E05',
        'policy': c['policy'],
        'primary_video_id': d['primary_video_id'],
        'primary_sentence_range': d['primary_sentence_range'],
        'primary_sentence_count': d['primary_sentence_count'],
        'primary_unit_count': len(primary_jobs),
        'reader_policy': d['reader_policy'],
        'document_outline': d['document_outline'],
        'source_amendments_applied': [
            {**a, 'writer_projection': VISUAL_NOTE_PROJECTIONS.get(a['id'], a.get('constraints', ''))}
            for a in raw_amends
        ],
        'visual_and_asset_state': {
            'reader_policy': d['reader_policy'],
            'deferred_assets': list(deferred_by_id.values()),
            'visual_limitations': [
                'VLM reader output excluded across Stage 3 (reader_policy=excluded).',
                'V01 remains deferred from T3/T4 without unverified figure references in prose; T5 decides placement and mobile display.',
                'A1 (indices 134-135) and A2 (index 137) are scoped visual demonstration observations from E03 Gate A3, not speech re-transcription.',
            ],
        },
        'upstream_refs': {
            't4_input': _rel_ref(b.INPUT) if b.INPUT.exists() else {'path': str(b.INPUT), 'sha256': b.INPUT_SHA},
            'primary_alignment_sentences': d['generation_units'][0]['required_source_sentences'][0]['source_ref'],
            't4_config': _rel_ref(run / 'config.json'),
            't4_plan': _rel_ref(run / 'plan.json'),
            't4_source_amendments': _rel_ref(run / 'source_amendments.json'),
            't4_draft_json': _rel_ref(run / 'draft.json'),
            't4_draft_md': _rel_ref(run / 'draft.md'),
            'confirmation_freeze': _rel_ref(run / 'confirmation_freeze.json'),
            'confirmation_draft_json': _rel_ref(run / 'confirmation/draft.json'),
            'reviews': {g: _rel_ref(run / 'reviews' / f'{g}.json') for g in ['G1', 'G2', 'primary', 'confirmation']},
        },
        'selected_units': selected_units,
        'confirmation_validation_summary': {
            'video_id': confirm_jobs[0]['unit']['video_id'] if confirm_jobs else None,
            'sentence_range': [
                confirm_jobs[0]['unit']['source_range']['sentence_start'],
                confirm_jobs[-1]['unit']['source_range']['sentence_end'],
            ] if confirm_jobs else [],
            'unit_count': len(confirm_jobs),
            'confirmation_repairs': 0,
            'mixed_into_primary_article': False,
            'confirmation_draft_ref': _rel_ref(run / 'confirmation/draft.json'),
        },
        'ledger_and_cost': {
            'candidate_calls': len(own),
            'baseline_units': len(jobs),
            'total_primary_repairs': tot_r,
            'auto_validator_repairs': auto_r,
            'reviewed_local_repairs': rev_r,
            'confirmation_repairs': 0,
            'candidate_http_seconds': round(sum(r.get('http_seconds', 0) for r in own), 6),
            'candidate_usage': {k: sum((r.get('usage') or {}).get(k, 0) for r in own) for k in ['prompt_tokens', 'completion_tokens', 'total_tokens']},
            'family_calls': len(allrr),
            'family_http_seconds': round(sum(r.get('http_seconds', 0) for r in allrr), 6),
            'retries': 0,
        },
        't5_responsibilities': [
            'adjacent_section_transition_and_terminology_consistency_check',
            'full_article_bidirectional_source_verification_without_whole_article_llm_regeneration',
            'deferred_asset_V01_placement_and_caption_packaging',
            'self_contained_single_file_mobile_first_html_rendering_and_viewport_verification',
        ],
    }


def closeout():
    run = current()
    c = b.identity(run)
    own = b.old.records(run)
    if own:
        render(run)
    allrr = c['prior_records'] + own
    jobs = b.read(run / 'plan.json')
    active_ok = all(
        (run / 'journal' / f"{j['job_id']}.json").exists()
        and b.read(run / 'journal' / f"{j['job_id']}.json").get('status') == 'success'
        for j in jobs
    )
    no_bad_records = not any(r.get('status') in ('started', 'unknown', 'failed') for r in own)
    tot_r, auto_r, rev_r = _repair_counts(own)
    confirm_ids = {j['job_id'] for j in jobs if j['phase'] == 'confirmation'}
    no_confirm_repairs = not any(r.get('superseded') and r.get('job_id') in confirm_ids for r in own)
    passed = (
        len(own) >= 23
        and active_ok
        and no_bad_records
        and no_confirm_repairs
        and tot_r <= MAX_TOTAL_REPAIRS
        and auto_r <= MAX_AUTO_REPAIRS
        and rev_r <= MAX_REVIEW_REPAIRS
        and (run / 'confirmation_freeze.json').exists()
    )
    for gate, ids in [
        ('G1', b.GATES['G1']),
        ('G2', b.GATES['G2']),
        ('primary', [j['job_id'] for j in jobs if j['phase'] == 'primary']),
        ('confirmation', [j['job_id'] for j in jobs if j['phase'] == 'confirmation']),
    ]:
        try:
            b.review_ok(run, gate, ids)
        except (OSError, ValueError):
            passed = False

    if passed:
        d = b.read(run / 'input.json')
        b.create(run / 't5_input.json', build_t5_input(run, d, c, jobs, own, allrr))

    active_ids = {
        j['job_id'] for j in jobs
        if (run / 'journal' / f"{j['job_id']}.json").exists()
    }
    result = {
        'candidate': 'E05',
        'policy': c['policy'],
        'decision': 'accepted' if passed else 'rejected_or_incomplete',
        'T4_complete': passed,
        't5_ready': passed,
        't5_input_generated': passed,
        'user_accepted': False,
        'final_article_user_accepted': False,
        'new_calls': len(own),
        'total_primary_repairs': tot_r,
        'auto_validator_repairs': auto_r,
        'reviewed_local_repairs': rev_r,
        'confirmation_repairs': 0,
        'new_http_seconds': round(sum(r.get('http_seconds', 0) for r in own), 6),
        'family_calls': len(allrr),
        'family_http_seconds': round(sum(r.get('http_seconds', 0) for r in allrr), 6),
        'usage': {k: sum((r.get('usage') or {}).get(k, 0) for r in own) for k in ['prompt_tokens', 'completion_tokens', 'total_tokens']},
        'unexecuted_jobs': [j['job_id'] for j in jobs if j['job_id'] not in active_ids],
    }
    b.create(run / 'closeout.json', result)
    b.create(run / 'acceptance.json', result)
    b.create(run / 'result_manifest.json', {str(p.relative_to(run)): b.sha(p) for p in run.rglob('*') if p.is_file()})
    print(b.wire(result).decode())


b.current = current
b.parse = parse
b.render = render
b.old.reserve = reserve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['dry-check', 'prepare', 'send', 'repair', 'verify', 'extend-g2', 'extend-full', 'freeze', 'render', 'closeout'])
    ap.add_argument('--job')
    ap.add_argument('--reason')
    a = ap.parse_args()
    with (HERE / 'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.action == 'dry-check':
            dry_check()
        elif a.action == 'prepare':
            prepare()
        elif a.action == 'send':
            send_e05(a.job)
        elif a.action == 'repair':
            repair_e05(a.job, a.reason)
        elif a.action == 'extend-g2':
            extend_g2()
        elif a.action == 'extend-full':
            extend_full()
        elif a.action == 'freeze':
            freeze()
        elif a.action == 'render':
            render(current())
        elif a.action == 'closeout':
            closeout()
        else:
            b.identity(current())
            print('identity_verified_no_inference')


if __name__ == '__main__':
    main()
