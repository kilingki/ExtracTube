"""Multi-chunk batched P3 CTX2 visual candidate selection with midpoint visual probe fallback."""

import base64
from decimal import Decimal
from . import prompts, frames
from .store import wire
from .visual_policy import warning
from .semantic import ranges
from .timestamps import decimal, seconds
from .errors import VisionError, InferenceJobFailed

MAX_BATCH_CHUNKS = 10
MAX_BATCH_CHARS = 6000
MIN_PROBE_SECONDS = Decimal('15')
MAX_PROBE_BATCH = 4


def split(sentences, start, end):
    result = []
    a = start
    while a < end:
        z = a
        chars = 0
        while z < end and chars + len(sentences[z]['text']) <= 6000:
            chars += len(sentences[z]['text'])
            z += 1
        if z == a:
            z = a + 1
        result.append((a, z))
        a = z
    return result


def batch_chunks(items, sentences):
    batches = []
    current = []
    chars = 0
    for item in items:
        row = item[2]
        c_chars = sum(
            len(sentences[i]['text']) for i in range(row['sentence_start'], row['sentence_end'])
        )
        if current and (len(current) >= MAX_BATCH_CHUNKS or chars + c_chars > MAX_BATCH_CHARS):
            batches.append(current)
            current = []
            chars = 0
        current.append(item)
        chars += c_chars
    if current:
        batches.append(current)
    return batches


def run(ctx, sentences, units):
    unit_records = []
    candidates = []
    warnings = []
    offset = decimal(ctx.input['audio_to_video_offset_seconds'])
    duration = decimal(ctx.input['stream']['duration'])
    by_unit = []
    pending = []
    for unit in units:
        uid = unit['unit_id']
        chunks = []
        parts = split(sentences, unit['sentence_start'], unit['sentence_end'])
        for k, (a, z) in enumerate(parts):
            cid = f'{uid}:c{k}'
            before = list(range(max(0, a - 2), a))
            after = list(range(z, min(len(sentences), z + 2)))
            ar, vr = ranges(sentences, a, z, offset)
            valid = (
                decimal(vr['start']) >= 0 and decimal(vr['start']) < decimal(vr['end']) <= duration
            )
            row = {
                'chunk_id': cid,
                'unit_id': uid,
                'chunk_index': k,
                'sentence_start': a,
                'sentence_end': z,
                'context_before_indices': before,
                'context_after_indices': after,
                'audio_range': ar,
                'video_range': vr,
                'range_status': 'valid' if valid else 'invalid',
                'decision': None,
                'reason': None,
                'evidence_sentence_indices': None,
                'inference_status': 'failed',
                'candidate_source': None,
                'model_inference_success': False,
                'job_ref': None,
                'probe_status': 'not_run',
                'probe_decision': None,
                'probe_visual_kind': None,
                'probe_reason': None,
                'probe_frame': None,
                'probe_job_ref': None,
                'warnings': [],
            }
            if len(sentences[a]['text']) > 6000:
                exc = InferenceJobFailed('한 문장 6000자 초과', kind='input_budget')
                row['warnings'].append(warning('t2_failed', 't2', cid, str(exc)))
                row['failure_ref'] = ctx.failure_ref(exc)
            else:
                pending.append((unit, len(parts), row))
            chunks.append(row)
        by_unit.append((unit, chunks))

    def projected(indices):
        return [{'sentence_index': i, 'text': sentences[i]['text']} for i in indices]

    for batch in batch_chunks(pending, sentences):
        chunk_entries = []
        spec_chunks = []
        dep_items = []
        for unit, part_count, row in batch:
            a, z = row['sentence_start'], row['sentence_end']
            allowed = list(range(a, z))
            chunk_entries.append(
                {
                    'chunk_id': row['chunk_id'],
                    'unit_index': unit['unit_index'],
                    'unit_sentence_range': f'[{unit["sentence_start"]},{unit["sentence_end"]})',
                    'chunk_index_1based': row['chunk_index'] + 1,
                    'chunk_count': part_count,
                    'allowed_evidence_indices': allowed,
                    'context_before': projected(row['context_before_indices']),
                    'sentences': projected(allowed),
                    'context_after': projected(row['context_after_indices']),
                }
            )
            spec_chunks.append({'chunk_id': row['chunk_id'], 'allowed': allowed})
            dep_items.append(
                {
                    'chunk_id': row['chunk_id'],
                    'unit': [unit['sentence_start'], unit['sentence_end']],
                    'range': [a, z],
                    'context': [row['context_before_indices'], row['context_after_indices']],
                }
            )
        prompt = prompts.render(
            prompts.T2_BATCH_CTX2, {'청크 목록 JSON': wire(chunk_entries).decode()}
        )
        try:
            parsed, jr, status = ctx.jobs.invoke(
                't2',
                't2',
                prompt,
                {'chunks': spec_chunks},
                {'sentences': ctx.input['stage1_identities']['sentences'], 'batch': dep_items},
            )
            for _, _, row in batch:
                row['job_ref'] = jr
                if status == 'budget_exhausted':
                    row.update(inference_status='budget_exhausted', candidate_source='budget_guard')
                    row['warnings'].append(
                        warning(
                            't2_budget_guard',
                            't2',
                            row['chunk_id'],
                            'reasoning 예산 소진으로 정책 후보를 보존합니다.',
                        )
                    )
                else:
                    decision_obj = parsed['chunks'][row['chunk_id']]
                    row.update(
                        **decision_obj,
                        inference_status='success',
                        candidate_source='batch_no_thinking',
                        model_inference_success=True,
                    )
        except InferenceJobFailed as exc:
            for _, _, row in batch:
                row['warnings'].append(warning('t2_failed', 't2', row['chunk_id'], str(exc)))
                row['failure_ref'] = ctx.failure_ref(exc)
    probe_items = []
    for unit, chunks in by_unit:
        for row in chunks:
            if (
                row['inference_status'] == 'success'
                and row['decision'] == 'not_needed'
                and row['range_status'] == 'valid'
            ):
                vr = row['video_range']
                span_sec = decimal(vr['end']) - decimal(vr['start'])
                if span_sec >= MIN_PROBE_SECONDS:
                    try:
                        img_ref = frames.extract_probe(ctx, row['chunk_id'], vr['start'], vr['end'])
                        probe_items.append((unit, row, img_ref))
                    except (VisionError, OSError, ValueError, frames._rgb.ExtractError) as exc:
                        row['probe_status'] = 'failed'
                        row['warnings'].append(
                            warning('t2_probe_failed', 't2', row['chunk_id'], str(exc))
                        )
    for i in range(0, len(probe_items), MAX_PROBE_BATCH):
        pbatch = probe_items[i : i + MAX_PROBE_BATCH]
        cids = [row['chunk_id'] for _, row, _ in pbatch]
        prompt = prompts.render(prompts.T2_PROBE_V1, {'chunk_ids_json': wire(cids).decode()})
        content = [{'type': 'text', 'text': prompt}]
        images = []
        dep_probes = []
        for unit, row, img_ref in pbatch:
            path = frames.check_image(ctx, img_ref)
            vr = row['video_range']
            mid = seconds((decimal(vr['start']) + decimal(vr['end'])) / Decimal(2))
            snippet = ' '.join(
                sentences[idx]['text'] for idx in range(row['sentence_start'], row['sentence_end'])
            )[:320]
            label = f'chunk_id: {row["chunk_id"]}\nvideo_seconds: {mid}\ntranscript_excerpt: {snippet}\n'
            content.extend(
                [
                    {'type': 'text', 'text': label},
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': 'data:image/png;base64,'
                            + base64.b64encode(path.read_bytes()).decode('ascii')
                        },
                    },
                ]
            )
            images.append(img_ref)
            dep_probes.append(
                {
                    'chunk_id': row['chunk_id'],
                    'video_range': vr,
                    'range': [row['sentence_start'], row['sentence_end']],
                    'image': img_ref,
                }
            )
        ctx.emit(f'[t2.probe] chunks {i+1}-{i+len(pbatch)}/{len(probe_items)}')
        try:
            p_parsed, p_jr, _ = ctx.jobs.invoke(
                't2',
                't2.probe',
                content,
                {'chunk_ids': cids},
                {'sentences': ctx.input['stage1_identities']['sentences'], 'probes': dep_probes},
                images=images,
            )
            for _, row, img_ref in pbatch:
                p_dec = p_parsed['probes'][row['chunk_id']]
                row.update(
                    probe_status='success',
                    probe_decision=p_dec['decision'],
                    probe_visual_kind=p_dec['visual_kind'],
                    probe_reason=p_dec['reason'],
                    probe_frame=img_ref,
                    probe_job_ref=p_jr,
                )
                if p_dec['decision'] in ('visual_info', 'uncertain'):
                    row['candidate_source'] = 'visual_probe'
                    if p_dec['decision'] == 'uncertain':
                        row['warnings'].append(
                            warning('t2_probe_uncertain', 't2', row['chunk_id'], p_dec['reason'])
                        )
        except InferenceJobFailed as exc:
            for _, row, img_ref in pbatch:
                row.update(probe_status='failed', probe_frame=img_ref)
                row['warnings'].append(warning('t2_probe_failed', 't2', row['chunk_id'], str(exc)))
                row['probe_failure_ref'] = ctx.failure_ref(exc)
    for unit, chunks in by_unit:
        uid = unit['unit_id']
        for row in chunks:
            cid = row['chunk_id']
            k = row['chunk_index']
            a, z = row['sentence_start'], row['sentence_end']
            ar, vr = row['audio_range'], row['video_range']
            valid = row['range_status'] == 'valid'
            selected = row['decision'] in ('needed', 'uncertain') or row['candidate_source'] in (
                'budget_guard',
                'visual_probe',
            )
            if selected:
                retention = (
                    'reasoning_budget_exhausted'
                    if row['candidate_source'] == 'budget_guard'
                    else (
                        'visual_probe_' + row['probe_decision']
                        if row['candidate_source'] == 'visual_probe'
                        else None
                    )
                )
                candidates.append(
                    {
                        'candidate_id': cid,
                        'unit_id': uid,
                        'chunk_index': k,
                        'sentence_start': a,
                        'sentence_end': z,
                        'audio_range': ar,
                        'requested_video_range': vr,
                        'effective_video_range': vr if valid else None,
                        'range_status': row['range_status'],
                        'candidate_source': row['candidate_source'],
                        'model_inference_success': row['model_inference_success'],
                        'decision': row['decision'],
                        'reason': (
                            row['probe_reason']
                            if row['candidate_source'] == 'visual_probe'
                            else row['reason']
                        ),
                        'evidence_sentence_indices': row['evidence_sentence_indices'],
                        'source_job_ref': row['probe_job_ref'] or row['job_ref'],
                        'retention_reason': retention,
                    }
                )
                if not valid:
                    row['warnings'].append(
                        warning(
                            'invalid_video_range',
                            't2',
                            cid,
                            '원래 범위를 보존하며 화면 처리를 생략합니다.',
                        )
                    )
                if row['decision'] == 'uncertain':
                    row['warnings'].append(
                        warning(
                            't2_uncertain', 't2', cid, '시각 후보 판단의 불확실성을 보존합니다.'
                        )
                    )
            warnings.extend(row['warnings'])
        has_candidate = any(c['unit_id'] == uid for c in candidates)
        all_not_needed = all(
            c['decision'] == 'not_needed'
            and c['candidate_source'] != 'visual_probe'
            and c['inference_status'] == 'success'
            for c in chunks
        )
        failed = any(c['inference_status'] == 'failed' for c in chunks)
        unit_records.append(
            {
                'unit_id': uid,
                'chunk_ids': [c['chunk_id'] for c in chunks],
                'selection_state': (
                    'selected'
                    if has_candidate
                    else ('not_selected' if all_not_needed else 'unresolved')
                ),
                'execution_status': (
                    'partial' if failed and has_candidate else ('failed' if failed else 'ok')
                ),
                'chunks': chunks,
            }
        )
    if sentences:
        warnings.append(
            warning(
                't2_selected_scope',
                't2',
                detail='발화 단서로 선정한 후보만 화면 분석하며 후보 밖 시각 자료는 누락될 수 있습니다.',
            )
        )
    return {'units': unit_records, 'candidates': candidates}, warnings
