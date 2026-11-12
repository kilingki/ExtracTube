"""Unified single-pass VLM representative frame selection, asset eligibility, and cross-unit deduplication."""

import base64
from . import prompts, frames, screen_changes as changes
from .store import wire
from .visual_policy import warning
from .errors import InferenceJobFailed


def batch_items(items, size=4):
    return [items[i : i + size] for i in range(0, len(items), size)]


def content(ctx, candidate, associations, frame_map, sentences, context=()):
    targets = [a['frame_id'] for a in associations]
    projected = [
        {k: s[k] for k in ('sentence_index', 'text', 'audio_start', 'audio_end')}
        for s in sentences[candidate['sentence_start'] : candidate['sentence_end']]
    ]
    prompt = prompts.render(
        prompts.T4_UNIFIED_V1,
        {
            'candidate_id': candidate['candidate_id'],
            'sentences_json': wire(projected).decode(),
            'target_ids_json': wire(targets).decode(),
        },
    )
    result = [{'type': 'text', 'text': prompt}]
    images = []
    for a in [*context, *associations]:
        f = frame_map[a['frame_id']]
        path = frames.check_image(ctx, f['vlm'])
        frames.check_image(ctx, f['full'])
        role = 'context_confirmed_use' if a in context else 'target'
        label = f'frame_id: {f["frame_id"]}\nrole: {role}\nvideo_seconds: {f["video_seconds"]}\nsegment_index: {a["segment_index"]}\n'
        result.extend(
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
        images.extend([f['vlm'], f['full']])
    return result, images


def classify(ctx, candidate, associations, frame_map, sentences, batch_index, context=()):
    request, images = content(ctx, candidate, associations, frame_map, sentences, context)
    spec = {
        'targets': [a['frame_id'] for a in associations],
        'context_ids': [a['frame_id'] for a in context],
    }
    parsed, jr, status = ctx.jobs.invoke(
        't4',
        't4',
        request,
        spec,
        {
            'candidate': candidate,
            'association_ids': [a['association_id'] for a in associations],
            'context_ids': spec['context_ids'],
            'sentences': ctx.input['stage1_identities']['sentences'],
            'images': images,
        },
        images=images,
    )
    batch = {
        'candidate_id': candidate['candidate_id'],
        'target_ids': spec['targets'],
        'association_ids': [a['association_id'] for a in associations],
        'context_ids': spec['context_ids'],
        'context_association_ids': [a['association_id'] for a in context],
        'batch_index': batch_index,
        'status': 'success',
        'job_ref': jr,
    }
    return parsed['frames'], batch


def representative_segments(payload):
    vsegs = payload.get('visual_segments')
    if vsegs is None:
        vsegs = changes.consolidate_segments(
            payload['candidate_id'],
            payload['samples'],
            payload['differences'],
            payload['boundaries'],
            payload['segments'],
        )
    for vs in vsegs:
        rep = vs.get('representative_sample_index')
        if rep is not None:
            yield vs, payload['samples'][rep]


def run(ctx, sentences, candidates, t3):
    rep_samples = [
        sample
        for doc in t3.values()
        if doc['execution_status'] != 'failed'
        for _, sample in representative_segments(doc['payload'])
    ]
    frame_map = frames.extract(ctx, rep_samples)
    associations = []
    decisions = []
    batches = []
    warnings = []
    carry = None
    for candidate in candidates:
        cid = candidate['candidate_id']
        doc = t3.get(cid)
        if doc is None or doc['execution_status'] == 'failed':
            continue
        p = doc['payload']
        local = []
        for vs, sample in representative_segments(p):
            fid = sample['frame_id']
            ok = fid in frame_map
            req_indices = sorted(
                {ri for si in vs['sample_indices'] for ri in p['samples'][si]['request_indices']}
            )
            a = {
                'association_id': cid + '@' + fid,
                'candidate_id': cid,
                'unit_id': candidate['unit_id'],
                'segment_id': vs['visual_segment_id'],
                'segment_index': vs['visual_segment_index'],
                'source_b05_segment_indices': vs['source_segment_indices'],
                'frame_id': fid,
                'representative_sample_index': vs['representative_sample_index'],
                'source_sample_indices': vs['sample_indices'],
                'source_request_indices': req_indices,
                'extraction_status': 'ok' if ok else 'failed',
                't4_status': 'not_run' if ok else 'failed',
                't4_decision': None,
                't4_duplicate_of': None,
                't4_relevance': None,
                't4_readability': None,
                't4_completeness': None,
                't4_visual_kind': None,
                't4_caption': None,
                't4_reason': None,
                't4_job_ref': None,
            }
            associations.append(a)
            if ok:
                local.append(a)
            else:
                warnings.append(warning('frame_decode_failed', 't4', a['association_id']))
        at = 0
        bi = 0
        while at < len(local):
            can_context = (
                carry is not None
                and frame_map[carry['frame_id']]['pts'] < frame_map[local[at]['frame_id']]['pts']
                and carry['frame_id'] not in {x['frame_id'] for x in local[at : at + 3]}
            )
            context = [carry] if can_context else []
            group = local[at : at + 4 - len(context)]
            ctx.emit(
                f'[t4] {cid} targets {at+1}-{at+len(group)}/{len(local)} context={len(context)}'
            )
            try:
                parsed, batch = classify(ctx, candidate, group, frame_map, sentences, bi, context)
                for a, row in zip(group, parsed):
                    a.update(
                        t4_status='success',
                        t4_decision=row['decision'],
                        t4_duplicate_of=row['duplicate_of'],
                        t4_relevance=row['relevance'],
                        t4_readability=row['readability'],
                        t4_completeness=row['completeness'],
                        t4_visual_kind=row['visual_kind'],
                        t4_caption=row['caption'],
                        t4_reason=row['reason'],
                        t4_job_ref=batch['job_ref'],
                    )
                    if row['decision'] == 'use':
                        carry = a
            except InferenceJobFailed as exc:
                batch = {
                    'candidate_id': cid,
                    'target_ids': [a['frame_id'] for a in group],
                    'association_ids': [a['association_id'] for a in group],
                    'context_ids': [a['frame_id'] for a in context],
                    'context_association_ids': [a['association_id'] for a in context],
                    'batch_index': bi,
                    'status': 'failed',
                    'job_ref': None,
                    'failure_ref': ctx.failure_ref(exc),
                }
                for a in group:
                    a['t4_status'] = 'failed'
                    a['t4_reason'] = str(exc)
                    warnings.append(warning('t4_failed', 't4', a['association_id'], str(exc)))
            batches.append(batch)
            at += len(group)
            bi += 1
        for a in associations:
            if a['candidate_id'] == cid:
                decisions.append(
                    {
                        k: a[k]
                        for k in (
                            'association_id',
                            'frame_id',
                            't4_status',
                            't4_decision',
                            't4_duplicate_of',
                            't4_relevance',
                            't4_readability',
                            't4_completeness',
                            't4_visual_kind',
                            't4_caption',
                            't4_reason',
                            't4_job_ref',
                        )
                    }
                )
                if a['t4_decision'] == 'uncertain':
                    warnings.append(
                        warning('t4_uncertain', 't4', a['association_id'], a['t4_reason'])
                    )
    return (
        {'frames': list(frame_map.values()), 'associations': associations},
        {'batches': batches, 'decisions': decisions},
        warnings,
    )
