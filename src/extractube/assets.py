"""Deterministic original-only asset promotion from unified visual selection decisions."""

from .frames import check_image
from .timestamps import actual, decimal
from .errors import VisionError


def omission(row):
    if row['decision'] == 'failed':
        return 't4_failed'
    if row['decision'] == 'uncertain':
        return 't4_uncertain'
    if row['decision'] == 'use':
        return None
    if row.get('duplicate_of'):
        return 't4_duplicate'
    if row['relevance'] == 'no':
        return 't5_not_relevant'
    if row['readability'] == 'insufficient':
        return 't5_unreadable'
    if row['completeness'] == 'insufficient':
        return 't5_incomplete'
    return 't5_omitted'


def run(ctx, sentences, units, candidates, frame_doc, t2, t3):
    frame_map = {f['frame_id']: f for f in frame_doc['frames']}
    associations = frame_doc['associations']
    decisions = []
    batches = []
    assets = {}
    warnings = []
    by_cand = {c['candidate_id']: c for c in candidates}
    by_unit = {u['unit_id']: u for u in units}
    for a in associations:
        if a['extraction_status'] != 'ok':
            continue
        c = by_cand[a['candidate_id']]
        unit = by_unit[a['unit_id']]
        if a['t4_status'] == 'success':
            row = {
                'association_id': a['association_id'],
                'frame_id': a['frame_id'],
                'decision': a['t4_decision'],
                'duplicate_of': a.get('t4_duplicate_of'),
                'relevance': a['t4_relevance'],
                'readability': a['t4_readability'],
                'completeness': a['t4_completeness'],
                'visual_kind': a['t4_visual_kind'],
                'caption': a['t4_caption'],
                'reason': a['t4_reason'],
                'source_t4_decision': a['t4_decision'],
                'job_ref': a['t4_job_ref'],
            }
        else:
            row = {
                'association_id': a['association_id'],
                'frame_id': a['frame_id'],
                'decision': 'failed',
                'duplicate_of': None,
                'relevance': None,
                'readability': None,
                'completeness': None,
                'visual_kind': None,
                'caption': None,
                'reason': a.get('t4_reason') or 't4_failed',
                'source_t4_decision': None,
                'job_ref': None,
            }
        row['omission_code'] = omission(row)
        if row['decision'] == 'use':
            f = frame_map[a['frame_id']]
            check_image(ctx, f['full'])
            check_image(ctx, f['vlm'])
            t = actual(f['pts'], f['time_base'], ctx.input['stream']['start_time'])
            for r in (c['effective_video_range'], unit['video_range']):
                if not decimal(r['start']) <= t < decimal(r['end']):
                    raise VisionError('단위/후보 밖 자산 승격입니다.', kind='validation')
            asset = assets.setdefault(
                f['frame_id'],
                {
                    'asset_id': f['frame_id'],
                    'kind': 'original_frame',
                    'image': f['full'],
                    'frame_id': f['frame_id'],
                    'association_ids': [],
                    'transform': 'none',
                    'verified_text': [],
                    'latex': None,
                    'table_html': None,
                    'regions': [],
                },
            )
            asset['association_ids'].append(a['association_id'])
        decisions.append(row)
    unit_visuals = visuals(units, candidates, associations, decisions, frame_map, t2, t3)
    ctx.emit(
        '[t5] '
        + ' '.join(
            f'{k}={sum(d["decision"]==k for d in decisions)}'
            for k in ('use', 'omit', 'uncertain', 'failed')
        )
    )
    return {
        'batches': batches,
        'decisions': decisions,
        'assets': list(assets.values()),
        'unit_visuals': unit_visuals,
    }, warnings


def visuals(units, candidates, associations, decisions, frame_map, t2, t3):
    output = []
    decision_map = {d['association_id']: d for d in decisions}
    for u in units:
        uid = u['unit_id']
        cs = [c for c in candidates if c['unit_id'] == uid]
        aa = [a for a in associations if a['unit_id'] == uid]
        chunks = next(v['chunks'] for v in t2['units'] if v['unit_id'] == uid)
        not_requested = bool(chunks) and all(
            c['inference_status'] == 'success'
            and c['decision'] == 'not_needed'
            and c.get('candidate_source') != 'visual_probe'
            for c in chunks
        )
        omissions = []
        partial = False
        refs = []
        for c in chunks:
            if c['inference_status'] == 'failed':
                partial = True
                omissions.append({'code': 't2_failed', 'subject_id': c['chunk_id']})
            if c['decision'] == 'uncertain' or c.get('probe_decision') == 'uncertain':
                partial = True
        for c in cs:
            cid = c['candidate_id']
            if c['range_status'] == 'invalid':
                partial = True
                omissions.append({'code': 'invalid_video_range', 'subject_id': cid})
            elif cid not in t3 or t3[cid]['execution_status'] == 'failed':
                partial = True
                omissions.append({'code': 't3_failed', 'subject_id': cid})
            elif t3[cid]['warnings']:
                partial = True
                omissions.extend(
                    {'code': w['code'], 'subject_id': cid} for w in t3[cid]['warnings']
                )
        for a in aa:
            aid = a['association_id']
            d = decision_map.get(aid)
            if a['extraction_status'] != 'ok':
                partial = True
                omissions.append({'code': 'frame_decode_failed', 'subject_id': aid})
            elif d is None or a['t4_status'] != 'success' or d['decision'] == 'failed':
                partial = True
                omissions.append({'code': 't4_failed', 'subject_id': aid})
            elif d['decision'] == 'use':
                refs.append(
                    {
                        'asset_id': a['frame_id'],
                        'association_id': aid,
                        'caption': d['caption'],
                        'actual_video_seconds': frame_map[a['frame_id']]['video_seconds'],
                    }
                )
            else:
                om_entry = {'code': d['omission_code'], 'subject_id': aid}
                if d.get('duplicate_of'):
                    om_entry['representative_frame_id'] = d['duplicate_of']
                omissions.append(om_entry)
                if d['decision'] == 'uncertain':
                    partial = True
        if not_requested:
            omissions.append({'code': 't2_not_needed', 'subject_id': uid})
        output.append(
            {
                'unit_id': uid,
                'candidate_ids': [c['candidate_id'] for c in cs],
                'asset_refs': refs,
                'visual_status': (
                    'available'
                    if refs
                    else (
                        'not_requested'
                        if not_requested
                        else ('unresolved' if partial else 'omitted')
                    )
                ),
                'coverage_status': (
                    'not_requested' if not_requested else ('partial' if partial else 'complete')
                ),
                'omission_reasons': omissions,
                'warnings': [],
            }
        )
    return output
