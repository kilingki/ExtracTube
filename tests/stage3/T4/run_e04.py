"""T4 E04: Attribute-First adaptive micro-windowed generation with inline cautions and CPU guardrails."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import copy
import fcntl
import hashlib
import html
import json
import math
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
PARENT = HERE / 'runs/20261102T043755Z_e03_7eb65a'
CAMPAIGN_FILE = HERE / 'e04_campaign.json'

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

VISUAL_NOTE_PROJECTIONS = {
    'A1': (
        "시연 화면의 메모장 편집 결과에서는 '욕실 뚫어뻥 액체' 등 무관 항목이 제거되고 "
        "'변기 뚫어뻥 기구', '압력 뚫어뻥', '뚫어뻥', '미니 뚫어뻥' 네 항목이 남는다. "
        "해당 시연 예시로만 서술하며 프레임 초 단위 시각이나 전후 시각 비교 문구는 본문에 적지 않는다."
    ),
    'A2': (
        "시연의 검색 결과 화면에 표시된 실제 UI 라벨은 '연관검색어'다. "
        "화면 라벨 명칭으로만 자연스럽게 사용하고 프레임 초 단위 시각이나 전사 오류어는 본문에 노출하지 않는다."
    ),
}

SYSTEM_PROMPT = """제공된 영상 원문(assigned_sentences)에 근거하여 독자가 영상 시청 없이도 내용을 완전히 이해할 수 있는 한국어 독립 설명문의 한 섹션을 작성한다.

[1. 생성 순서와 문단 구성 (Attribute-First)]
- 각 문단 객체를 생성할 때 반드시 source_indices → claim_mode → (해당 시 amendment_refs) → key_anchors → text 순서로 결정한다.
- paragraph_plan의 최소 문단 수(min_paragraphs)와 문단당 최대 문장 수(max_sentences_per_paragraph)를 지키며, 원문 시간순으로 자연스럽게 단락을 나눈다.
- 한 문단에 여러 단계를 뭉뚱그려 세부 조건·예시를 요약 생략하지 않는다. key_anchors에는 해당 source_indices에서 빠짐없이 반영할 핵심 수치·고유명사·예시 어구·UI 명칭을 짧은 명사/수치 단위로 먼저 추출하고, 추출한 핵심 정보와 수치를 해당 문단 text 안에 반드시 서술한다.
- 각 문단은 해당 source_indices에 실제로 등장하는 대상과 예시만 서술한다. 후반 문장에서 별도로 소개되는 세부 예시나 오타 예시를 앞 문장의 기본 판매 상품이나 최초 검색 입력어에 소급 대입하지 않는다.
- previous_context_readonly는 앞선 정의나 지시어('앞에서 설명한 ~' 등)를 이해하기 위한 읽기 전용 참고 문맥이므로 현재 섹션의 새 본문으로 중복 집필하지 않는다.

[2. 문체와 동작 대상 분리]
- 모든 본문 문장은 한국어 평서형 설명체('~한다', '~할 수 있다', '~이다')로 작성한다. '~합니다/~습니다/~세요' 체나 '화자가 ~라고 말한다', '원문에서는' 같은 발화 중계 표현은 쓰지 않는다.
- 화면·도구 전환이 있는 절차는 어디에서 무엇을 조작해 어디로 옮기는지(예: 사이트 내 기능 복사 → 메모장 붙여넣기 및 편집 → 브라우저 검색창에 기본 검색어 입력 후 엔터 실행 → 검색 결과 화면의 추가 검색어 복사 → 하위 키워드 클릭 진입 → 최종 입력란에 입력) 주체와 동작 대상을 혼동 없이 정확히 구분한다.

[3. 귀속·의심 전사·시각 보완의 4가지 원칙]
1) 확인된 절차·수치·예시·의도적 오타: 원문의 절차, 조건, 예외, 통계 수치, 제외 예시를 빠짐없이 보존한다. 원문에서 화자가 '철자가 틀린 검색어' 등으로 명시해 설명하는 의도적 오타 예시는 일부 음절도 표준어로 부분 교정하지 말고 해당 원문 문장에 적힌 음절 철자 그대로 인용해 보존한다.
2) 화자의 평가·권고·추정·전언: 효과·중요도 평가('힘이 크다', '더 좋다', '중요하다'), 개수·방식 권고('꼭 채워 넣어라', '추천한다'), 개인 경험·추정·전언('~라고 알고 있다', '~해 준다고 한다', '예전에는/이제는')이 포함된 문단은 claim_mode를 speaker_view 또는 mixed_procedure_and_view로 지정하고, 본문에서도 일반 공식 규칙으로 단정하지 않고 '이 설명에서는 … 중요하게 보며 … 권한다', '화자는 … 효과적이라고 설명한다'처럼 필요한 문장에 자연스럽게 귀속한다.
3) 주의(cautions)가 붙은 의심 전사·비정상 혼입 어구(시각 보완이 없는 경우): 음성 인식 오류로 발음이 뭉개진 불분명 어절이나 맥락과 무관한 단어들이 비정상적으로 나열된 전사 혼입은 상식이나 외부 지식으로 다른 정식 명칭으로 임의 교정하지 않으며, 동시에 검증된 실제 상품명이나 UI 필수 항목으로 확정 나열하지도 않는다. 같은 섹션 내 확인된 시연 절차·문맥으로 확실히 뒷받침되는 범위까지만 일반화하거나 원문 표현 범위를 한정해 서술하고, 불확실한 문장 번호와 사유는 반드시 unresolved에 기록한다. 단독 발화·복합 어구의 부정('아니죠/아니니까')과 가능성('될 수 있겠죠') 범위를 임의로 쪼개어 무근거 유지/삭제 목록을 만들지 않는다.
4) 검증된 시각 시연 노트(verified_visual_notes가 있는 경우): '시연에서는 …'처럼 해당 시연 범위로 한정해 자연스러운 설명문으로 서술하고 amendment_refs에 연결한다. 프레임 시각('몇 초')이나 검수 비교 과정은 독자 본문에 절대 적지 않는다.

[4. 부가 기록 및 출력 제약]
- editorial_candidates에는 본문에서 다듬어 생략한 순수 인사말·화면 전환 추임새만 기록하며(cautions가 붙은 문장이나 실질 설명 문장은 넣지 않음), 중요한 정보를 이 배열이나 unresolved로 빼돌려 생략하지 않는다.
- 독자용 text에는 내부 ID(p_u09, A1, V01 등), 타임스탬프(초), HTML 태그, 마크다운 코드 펜스를 절대 노출하지 않는다. JSON Schema만 출력한다."""


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
    para_props['key_anchors'] = arr({'type': 'string', 'minLength': 1, 'maxLength': 80}, 8, 1)
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


def _check_anchor(anchor, text):
    clean_a = anchor.strip().strip("'\"“”‘’")
    if not clean_a:
        raise ValueError('empty_key_anchor')
    nums = re.findall(r'\d+(?:\.\d+)?', clean_a.replace(',', ''))
    norm_t = text.replace(',', '')
    for num in nums:
        if not re.search(r'(?<!\d)' + re.escape(num) + r'(?!\d)', norm_t):
            raise ValueError('missing_numeric_anchor:' + anchor)
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
            if not _check_anchor(anchor, text):
                paraphrased_anchors.append({'paragraph_index': p_idx, 'anchor': anchor})

        for m in re.finditer(r'([가-힣]{2,4})\1', text):
            dup = m.group(0)
            if dup not in REDUPLICATIVE_WHITELIST and dup not in source_concat:
                raise ValueError('stutter_corruption:' + dup)

        if re.search(r'(?<![A-Za-z0-9_])(?:p_u\d+|c_u\d+|V0\d|A[12]|source_indices|claim_mode|key_anchors|amendment_refs|unresolved|editorial_candidates)(?![A-Za-z0-9_])', text):
            raise ValueError('internal_id_leak')

        if not source_has_seconds and re.search(r'\d+\s*초(?:(?![가-힣])|(?:에|에는|에서|의|부터|까지|와|경|쯤|간|동안)(?![가-힣]))', text):
            raise ValueError('forensic_timestamp_leak')

        unquoted = _strip_quotes(text)
        if re.search(r'원문에서는|원문에서\b', unquoted):
            raise ValueError('meta_source_relay')
        if _has_polite_sentence_ending(unquoted):
            raise ValueError('polite_ending_style_violation')

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

    amends_by_idx = {}
    for a in amends:
        for sid in a['source_indices']:
            amends_by_idx.setdefault(sid, []).append({'ref': a['ref'], 'scope': a['scope']})

    assigned = []
    for s in unit['required_source_sentences']:
        sid = s['sentence_index']
        row = {'index': sid, 'text': s['text']}
        if sid in cautions_by_idx:
            row['cautions'] = cautions_by_idx[sid]
        if sid in amends_by_idx:
            row['verified_visual_notes'] = amends_by_idx[sid]
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

    if b.read(PARENT / 'evidence_review.json')['decision'] != 'accepted':
        raise ValueError('parent_evidence_not_accepted')
    raw_amendments = b.read(PARENT / 'source_amendments.json')

    jobs = []
    for phase, units, outline in [
        ('primary', primary, d['document_outline']),
        ('confirmation', confirm, conf['document_outline']),
    ]:
        for unit in units:
            payload = project_unit_payload(unit, units, outline, raw_amendments)
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
                    'json_schema': {'name': 'stage3_t4_e04_unit', 'strict': True, 'schema': wire_schema},
                },
            }
            if len(b.wire(body)) > 4194304:
                raise ValueError('request_limit:' + unit['unit_id'])
            jobs.append({'job_id': unit['unit_id'], 'phase': phase, 'unit': unit, 'body': body, 'bounds': paragraph_bounds(unit)})
    return d, raw_amendments, jobs


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
    _, _, jobs = build_jobs()
    grammar_msg, tp = check_runtime_and_tokens(jobs)
    toks = [row['input_tokens'] for row in tp['jobs']]
    summary = {
        'status': 'offline_dry_check_passed_zero_http_calls',
        'jobs': len(jobs),
        'grammar_check': grammar_msg,
        'min_input_tokens': min(toks),
        'max_input_tokens': max(toks),
        'total_input_tokens_all_23': sum(toks),
        'per_job': [
            {
                'job_id': j['job_id'],
                'phase': j['phase'],
                'input_tokens': row['input_tokens'],
                **j['bounds'],
            }
            for j, row in zip(jobs, tp['jobs'])
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def prepare():
    prior_run_path = PARENT
    extra_dirs = []
    b.identity(PARENT)
    pc = b.read(PARENT / 'config.json')
    base_previous = pc['prior_records'] + b.old.records(PARENT)
    previous = list(base_previous)
    prompt_rev = 'P0'

    if CAMPAIGN_FILE.exists():
        prev_run = current()
        close_path = prev_run / 'closeout.json'
        manifest_path = prev_run / 'result_manifest.json'
        prev_cfg = b.read(prev_run / 'config.json')
        rr = b.old.records(prev_run)
        is_preflight = any(r.get('status') == 'failed' and str(r.get('error', '')).startswith('missing_key_anchor:') for r in rr)
        is_g1_r1 = (
            len(rr) == 2
            and all(r.get('status') == 'success' for r in rr)
            and prev_cfg.get('prompt_revision', 'P0') == 'P0'
            and (prev_run / 'reviews/G1.json').exists()
            and b.read(prev_run / 'reviews/G1.json').get('decision') == 'rejected'
        )
        if not close_path.exists() or not manifest_path.exists() or not (is_preflight or is_g1_r1):
            raise ValueError('existing_E04')
        for rel, h in b.read(manifest_path).items():
            if rel != 'result_manifest.json':
                b.bound_sha(prev_run / rel, h)
        previous = prev_cfg['prior_records'] + rr
        prior_run_path = prev_run
        for p_str in prev_cfg.get('critical_files', {}):
            p_obj = Path(p_str)
            if p_obj.is_relative_to(HERE / 'runs') and p_obj.parents[0] != PARENT:
                if p_obj.parents[0] not in extra_dirs and (HERE / 'runs') in p_obj.parents:
                    run_anc = next(a for a in p_obj.parents if a.parent == HERE / 'runs')
                    if run_anc != PARENT and run_anc not in extra_dirs:
                        extra_dirs.append(run_anc)
        if prev_run not in extra_dirs:
            extra_dirs.append(prev_run)
        if is_g1_r1 or prev_cfg.get('prompt_revision') == 'R1':
            prompt_rev = 'R1'

    if len(previous) not in (8, 9, 11, 13) or b.read(PARENT / 'reviews/G1.json')['decision'] != 'rejected':
        raise ValueError('parent_status')

    d, raw_amendments, jobs = build_jobs()
    grammar_msg, tp = check_runtime_and_tokens(jobs)

    critical = dict(pc['critical_files'])
    for p in PARENT.rglob('*'):
        if p.is_file():
            critical[str(p)] = b.sha(p)
    for ed in extra_dirs:
        for p in ed.rglob('*'):
            if p.is_file():
                critical[str(p)] = b.sha(p)
    for p in [HERE / 'run_e04.py', HERE / 'test_e04.py']:
        critical[str(p)] = b.sha(p)

    rid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_e04_' + uuid.uuid4().hex[:6]
    run = HERE / 'runs' / rid
    run.mkdir(parents=True)

    full_family_cap = len(previous) + 23
    config = {
        'candidate': 'E04',
        'policy': 'E04.attribute_first_adaptive_window_and_cpu_guard.v1',
        'prompt_revision': prompt_rev,
        'schema_revisions': 0,
        'endpoint': pc['endpoint'],
        'prior_run': str(prior_run_path),
        'prior_records': previous,
        'runtime_grammar_check': grammar_msg,
        'critical_files': critical,
        'limits': {
            'family_calls_before_G1': 27,
            'family_calls_after_G1_for_G2': 27,
            'family_calls_if_G2_accepted': full_family_cap,
            'family_http_seconds': 7200,
            'g1_diagnostic_calls': 2,
            'g1_diagnostic_http_seconds': 600,
            'g2_diagnostic_calls': 5,
            'g2_diagnostic_http_seconds': 1500,
            'candidate_calls': 23,
            'quality_revisions': 0,
            'timeout': 300,
            'retry': 0,
        },
        'token_identity': {k: tp[k] for k in ['transformers', 'processor', 'template_sha256', 'runtime_environment']},
        'authorization': '2026-11-03 E04 configured; gated execution G1(2) -> G2(3) -> G3(11) -> freeze -> G4(7)',
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
    for name in ['run_e04.py', 'test_e04.py']:
        (run / 'source_code' / name).write_bytes((HERE / name).read_bytes())
    b.create(run / 'state.json', {
        'frozen': {n: b.sha(run / n) for n in ['config.json', 'plan.json', 'token_plan.json', 'input.json', 'source_amendments.json', 'spec_snapshot.md']}
    })
    b.write(CAMPAIGN_FILE, {'run_id': rid})
    print(b.wire({'run': str(run), 'prior_calls': len(previous), 'jobs': len(jobs), 'tokens': tp['jobs']}).decode())


original_reserve = b.old.reserve


def reserve(records, timeout=300, max_calls=27, max_seconds=7200):
    run = current()
    own = b.old.records(run)
    if (run / 'continuation_full.json').exists():
        x = b.read(run / 'continuation_full.json')
        b.bound_sha(run / 'reviews/G1.json', x['G1_review_sha256'])
        b.bound_sha(run / 'reviews/G2.json', x['G2_review_sha256'])
        b.review_ok(run, 'G1', b.GATES['G1'])
        b.review_ok(run, 'G2', b.GATES['G2'])
        if len(own) >= 23:
            raise ValueError('candidate_calls_exceeded')
        cap = b.read(run / 'config.json')['limits']['family_calls_if_G2_accepted']
        return original_reserve(records, 300, cap, 7200)
    if (run / 'continuation_g2.json').exists():
        x = b.read(run / 'continuation_g2.json')
        b.bound_sha(run / 'reviews/G1.json', x['G1_review_sha256'])
        b.review_ok(run, 'G1', b.GATES['G1'])
        if len(own) >= 5:
            raise ValueError('g2_gate_not_accepted')
        return min(original_reserve(records, 300, 27, 7200), original_reserve(own, 300, 5, 1500))
    if len(own) >= 2:
        raise ValueError('g1_diagnostic_gate_not_accepted')
    return min(original_reserve(records, 300, 27, 7200), original_reserve(own, 300, 2, 600))


def extend_g2():
    run = current()
    b.identity(run)
    b.review_ok(run, 'G1', b.GATES['G1'])
    b.create(run / 'continuation_g2.json', {
        'G1_review_sha256': b.sha(run / 'reviews/G1.json'),
        'unlocked_gate': 'G2',
        'family_calls_cap': 27,
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
        'code_sha256': b.sha(HERE / 'run_e04.py'),
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
    rec_by_id = {r['job_id']: r for r in own}

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
            'response_sha256': rec_by_id[jid]['response_sha256'],
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
        'selected_t4_candidate': 'E04',
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
            'mixed_into_primary_article': False,
            'confirmation_draft_ref': _rel_ref(run / 'confirmation/draft.json'),
        },
        'ledger_and_cost': {
            'candidate_calls': len(own),
            'candidate_http_seconds': round(sum(r.get('http_seconds', 0) for r in own), 6),
            'candidate_usage': {k: sum((r.get('usage') or {}).get(k, 0) for r in own) for k in ['prompt_tokens', 'completion_tokens', 'total_tokens']},
            'family_calls': len(allrr),
            'family_http_seconds': round(sum(r.get('http_seconds', 0) for r in allrr), 6),
            'quality_revisions': 0,
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
    passed = len(own) >= 23 and all(r['status'] == 'success' for r in own) and {r['job_id'] for r in own} >= {j['job_id'] for j in jobs}
    if not (run / 'confirmation_freeze.json').exists():
        passed = False
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

    result = {
        'candidate': 'E04',
        'policy': c['policy'],
        'decision': 'accepted' if passed else 'rejected_or_incomplete',
        'T4_complete': passed,
        't5_ready': passed,
        't5_input_generated': passed,
        'user_accepted': False,
        'final_article_user_accepted': False,
        'new_calls': len(own),
        'new_http_seconds': round(sum(r.get('http_seconds', 0) for r in own), 6),
        'family_calls': len(allrr),
        'family_http_seconds': round(sum(r.get('http_seconds', 0) for r in allrr), 6),
        'usage': {k: sum((r.get('usage') or {}).get(k, 0) for r in own) for k in ['prompt_tokens', 'completion_tokens', 'total_tokens']},
        'unexecuted_jobs': [j['job_id'] for j in jobs if j['job_id'] not in {r['job_id'] for r in own}],
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
    ap.add_argument('action', choices=['dry-check', 'prepare', 'send', 'verify', 'extend-g2', 'extend-full', 'freeze', 'render', 'closeout'])
    ap.add_argument('--job')
    a = ap.parse_args()
    with (HERE / 'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.action == 'dry-check':
            dry_check()
        elif a.action == 'prepare':
            prepare()
        elif a.action == 'send':
            b.send(a.job)
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
