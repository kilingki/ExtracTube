"""Large-window boundary proposals, conflict provenance and batched forward verification."""

from . import prompts
from .store import wire, digest
from .timestamps import decimal, span
from .errors import VisionError

WINDOW_SENTENCES = 120
WINDOW_CHARS = 12000
OVERLAP_SENTENCES = 10
VERIFY_MAX_BOUNDARIES = 12
VERIFY_MAX_SENTENCES = 120
VERIFY_MAX_CHARS = 12000


def chunks(sentences):
    result = []
    start = 0
    n = len(sentences)
    while start < n:
        end = start
        chars = 0
        while (
            end < n
            and end - start < WINDOW_SENTENCES
            and chars + len(sentences[end]['text']) <= WINDOW_CHARS
        ):
            chars += len(sentences[end]['text'])
            end += 1
        if end == start:
            raise VisionError(
                f'문장 {start}이 {WINDOW_CHARS}자 한도를 넘습니다.', kind='input_budget'
            )
        result.append(
            {
                'chunk_index': len(result),
                'sentence_start': start,
                'sentence_end': end,
                'char_count': chars,
                'job_ref': None,
            }
        )
        if end == n:
            break
        start = max(start + 1, end - OVERLAP_SENTENCES)
    return result


def _sentence_item_span(s):
    items = s['source_chunk']['items']
    if len(items) == 1:
        return items[0]['start_time'], items[0]['end_time']
    return min(it['start_time'] for it in items), max(it['end_time'] for it in items)


def lines(sentences, mark=()):
    marked = set(mark)
    out = []
    for s in sentences:
        idx = s['sentence_index']
        if idx in marked:
            out.append(f'>>> 후보 경계 [{idx}] <<<')
        st, et = _sentence_item_span(s)
        out.append(f'[{idx}] {st}-{et} {s["text"]}')
    return '\n'.join(out)


def conflict_groups(merged, chunk_rows, n):
    disagreements = set()
    for candidate in merged:
        i = candidate['sentence_index']
        comparable = [
            c['chunk_index'] for c in chunk_rows if c['sentence_start'] < i < c['sentence_end']
        ]
        proposed = {p['chunk_index'] for p in candidate['sources']}
        if (
            len(comparable) >= 2
            and any(j in proposed for j in comparable)
            and any(j not in proposed for j in comparable)
        ):
            disagreements.add(i)
    groups = []
    for i in sorted(c['sentence_index'] for c in merged):
        if groups and i - groups[-1][-1] <= 2:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [
        {
            'cluster_index': k,
            'member_indices': g,
            'disagreement_indices': [i for i in g if i in disagreements],
            'context_start': max(0, g[0] - 8),
            'context_end': min(n, g[-1] + 9),
            'decisions': None,
            'job_ref': None,
        }
        for k, g in enumerate(g for g in groups if len(g) > 1 or g[0] in disagreements)
    ]


def context(sentences, left_start, boundary, right_end):
    left = sentences[left_start:boundary]
    right = sentences[boundary:right_end]

    def chars():
        return sum(len(s['text']) for s in left + right)

    kind = 'full'
    if len(left) + len(right) > 40 or chars() > 6000:
        kind = 'window12'
        left = left[-12:]
        right = right[:12]
        while chars() > 6000:
            if len(left) == len(right) == 1:
                raise VisionError('VERIFY 최소 문맥이 6000자를 넘습니다.', kind='input_budget')
            ld = boundary - 1 - left[0]['sentence_index'] if len(left) > 1 else -1
            rd = right[-1]['sentence_index'] - boundary if len(right) > 1 else -1
            if rd >= ld:
                right = right[:-1]
            else:
                left = left[1:]
    if not left or not right:
        raise VisionError('VERIFY 문맥이 비었습니다.', kind='validation')
    return (
        {
            'kind': kind,
            'left_start': left[0]['sentence_index'],
            'left_end': boundary,
            'right_start': boundary,
            'right_end': right[-1]['sentence_index'] + 1,
            'char_count': chars(),
        },
        left,
        right,
    )


def verify_batches(sentences, boundaries):
    batches = []
    n = len(sentences)
    p = 0
    m = len(boundaries)
    while p < m:
        b0 = boundaries[p]
        prev_edge = boundaries[p - 1] if p > 0 else 0
        next_edge = boundaries[p + 1] if p + 1 < m else n
        left_chars = sum(len(s['text']) for s in sentences[prev_edge:b0])
        right_chars = sum(len(s['text']) for s in sentences[b0:next_edge])
        span_start = (
            max(prev_edge, b0 - 12) if (b0 - prev_edge > 24 or left_chars > 4000) else prev_edge
        )
        span_end = (
            min(next_edge, b0 + 12) if (next_edge - b0 > 24 or right_chars > 4000) else next_edge
        )
        base_chars = sum(len(s['text']) for s in sentences[span_start:span_end])
        if span_end - span_start > VERIFY_MAX_SENTENCES or base_chars > 6000:
            meta, left, right = context(sentences, prev_edge, b0, next_edge)
            block = left + right
            batches.append(
                {
                    'targets': [b0],
                    'span_start': block[0]['sentence_index'],
                    'span_end': block[-1]['sentence_index'] + 1,
                    'char_count': meta['char_count'],
                    'kind': meta['kind'],
                    'sentences': block,
                }
            )
            p += 1
            continue
        targets = [b0]
        q = p + 1
        while q < m and len(targets) < VERIFY_MAX_BOUNDARIES:
            bq = boundaries[q]
            after_q = boundaries[q + 1] if q + 1 < m else n
            tail_chars = sum(len(s['text']) for s in sentences[bq:after_q])
            cand_end = (
                min(after_q, bq + 12) if (after_q - bq > 24 or tail_chars > 4000) else after_q
            )
            if cand_end <= bq or cand_end - span_start > VERIFY_MAX_SENTENCES:
                break
            cand_chars = sum(len(s['text']) for s in sentences[span_start:cand_end])
            if cand_chars > VERIFY_MAX_CHARS:
                break
            targets.append(bq)
            span_end = cand_end
            q += 1
        block = sentences[span_start:span_end]
        chars = sum(len(s['text']) for s in block)
        batches.append(
            {
                'targets': targets,
                'span_start': span_start,
                'span_end': span_end,
                'char_count': chars,
                'kind': 'batch',
                'sentences': block,
            }
        )
        p = q
    return batches


def ranges(sentences, start, end, offset):
    a = min(decimal(it['start_time']) for it in sentences[start]['source_chunk']['items'])
    z = max(decimal(it['end_time']) for it in sentences[end - 1]['source_chunk']['items'])
    return span(a, z, 'audio_start'), span(a + offset, z + offset, 'video_start')


def validate_units(sentences, units):
    at = 0
    for k, u in enumerate(units):
        if (
            u['unit_index'] != k
            or u['sentence_start'] != at
            or not at < u['sentence_end'] <= len(sentences)
            or u['sentence_indices'] != list(range(at, u['sentence_end']))
        ):
            raise VisionError('T1 문장이 누락/중복되거나 순서가 다릅니다.', kind='validation')
        at = u['sentence_end']
    if at != len(sentences):
        raise VisionError('T1 전체 문장 보존 실패입니다.', kind='validation')


def run(ctx, sentences):
    rows = chunks(sentences)
    proposals = []
    merged = []
    clusters = []
    after = []
    verifications = []
    trace = {
        'chunks': rows,
        'proposals': proposals,
        'merged_candidates': merged,
        'conflict_clusters': clusters,
        'boundaries_after_conflict': after,
        'verifications': verifications,
    }
    ctx.trace = trace
    deps = {'sentences': ctx.input['stage1_identities']['sentences']}
    for row in rows:
        start, end = row['sentence_start'], row['sentence_end']
        if end - start == 1:
            continue
        prompt = prompts.render(prompts.T1_BOUNDARY, {'문장 줄': lines(sentences[start:end])})
        parsed, jr, status = ctx.jobs.invoke(
            't1',
            't1.boundary',
            prompt,
            {'allowed': list(range(start + 1, end))},
            {**deps, 'range': [start, end]},
            retry=1,
        )
        row['job_ref'] = jr
        for proposal in parsed['boundaries']:
            proposals.append({**proposal, 'chunk_index': row['chunk_index'], 'job_ref': jr})
    by_index = {}
    for p in proposals:
        by_index.setdefault(p['sentence_index'], []).append(p)
    merged.extend({'sentence_index': i, 'sources': by_index[i]} for i in sorted(by_index))
    clusters.extend(conflict_groups(merged, rows, len(sentences)))
    conflict_members = {i for g in clusters for i in g['member_indices']}
    moved = {
        i: [{'source_sentence_index': i, 'sources': by_index[i], 'decision': None, 'job_ref': None}]
        for i in by_index
        if i not in conflict_members
    }
    for group in clusters:
        members = group['member_indices']
        a, z = group['context_start'], group['context_end']
        candidate_lines = []
        for i in members:
            sources = by_index[i]
            proposed = sorted({p['chunk_index'] for p in sources})
            comparable = [
                c['chunk_index'] for c in rows if c['sentence_start'] < i < c['sentence_end']
            ]
            candidate_lines.append(
                wire(
                    {
                        'sentence_index': i,
                        'proposed_chunks': proposed,
                        'not_proposed_comparable_chunks': [
                            j for j in comparable if j not in proposed
                        ],
                        'reasons': [
                            {'chunk_index': p['chunk_index'], 'reason': p['reason']}
                            for p in sources
                        ],
                    }
                ).decode()
            )
        prompt = prompts.render(
            prompts.T1_CONFLICT_C2,
            {'후보 줄': '\n'.join(candidate_lines), '문맥 문장 줄': lines(sentences[a:z])},
        )
        parsed, jr, status = ctx.jobs.invoke(
            't1',
            't1.conflict',
            prompt,
            {'allowed': members, 'move_allowed': list(range(max(1, a + 1), z))},
            {**deps, 'proposals': digest(candidate_lines)},
            retry=1,
        )
        group.update(decisions=parsed['decisions'], job_ref=jr)
        contributions = []
        for decision in parsed['decisions']:
            i = decision['sentence_index']
            if decision['action'] != 'drop':
                contributions.append(
                    (
                        decision.get('to_index', i),
                        {
                            'source_sentence_index': i,
                            'sources': by_index[i],
                            'decision': decision,
                            'job_ref': jr,
                        },
                    )
                )
        for i, contribution in contributions:
            moved.setdefault(i, []).append(contribution)
    after.extend({'sentence_index': i, 'contributions': moved[i]} for i in sorted(moved))
    boundaries = sorted(moved)
    kept = []
    for batch in verify_batches(sentences, boundaries):
        targets = batch['targets']
        ctx_meta = {
            'kind': batch['kind'],
            'span_start': batch['span_start'],
            'span_end': batch['span_end'],
            'targets': targets,
            'char_count': batch['char_count'],
        }
        prompt = prompts.render(
            prompts.T1_VERIFY_BATCH,
            {
                '후보 경계 목록': wire(targets).decode(),
                '문장 줄': lines(batch['sentences'], mark=targets),
            },
        )
        parsed, jr, status = ctx.jobs.invoke(
            't1',
            't1.verify',
            prompt,
            {'targets': targets},
            {**deps, 'verify_batch': ctx_meta},
            retry=1,
        )
        for item in parsed['verifications']:
            i = item['sentence_index']
            verifications.append(
                {
                    'sentence_index': i,
                    'action': item['action'],
                    'reason': item['reason'],
                    'context': ctx_meta,
                    'job_ref': jr,
                }
            )
            if item['action'] == 'keep':
                kept.append(i)
    units = []
    edges = [0] + kept + [len(sentences)] if sentences else []
    offset = decimal(ctx.input['audio_to_video_offset_seconds'])
    by_verify = {v['sentence_index']: v for v in verifications}
    for k, (a, z) in enumerate(zip(edges, edges[1:])):
        ar, vr = ranges(sentences, a, z, offset)
        units.append(
            {
                'unit_id': f'{ctx.video_id}:u{k}',
                'unit_index': k,
                'sentence_start': a,
                'sentence_end': z,
                'audio_range': ar,
                'video_range': vr,
                'sentence_indices': list(range(a, z)),
                'start_reason': '영상 시작' if k == 0 else by_verify[a]['reason'],
                'verification_ref': None if k == 0 else by_verify[a]['job_ref'],
            }
        )
    validate_units(sentences, units)
    return {'units': units, 'final_boundaries': kept}, trace
