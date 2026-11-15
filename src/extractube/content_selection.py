"""Stage 3 E02 editorial visual selection integrated via shared product Jobs/ledger."""

from __future__ import annotations

import base64
import copy
from . import prompts
from .store import digest, sha, wire

POLICY = 'r1.editorial_visual_selection.e02.reverse_compaction.v1'
SCHEMA_VERSION = 'extractube.content.visual_select.v1'


def selection_schema(n):
    """Return the strict JSON schema for a batch of n paragraph figures."""
    row = {
        'type': 'object',
        'additionalProperties': False,
        'required': ['index', 'decision', 'covered_by', 'reason'],
        'properties': {
            'index': {'type': 'integer', 'enum': list(range(n))},
            'decision': {'type': 'string', 'enum': ['keep', 'omit', 'uncertain']},
            'covered_by': {'anyOf': [{'type': 'null'}, {'type': 'integer', 'enum': list(range(n))}]},
            'reason': {'type': 'string', 'minLength': 1, 'maxLength': 180},
        },
    }
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['frames'],
        'properties': {
            'frames': {'type': 'array', 'minItems': n, 'maxItems': n, 'items': row},
        },
    }


def validate_selection(value, spec):
    """Validate and intra-batch-flatten an E02 visual selection response."""
    import jsonschema

    targets = spec['targets']
    n = len(targets)
    schema = spec.get('schema') or selection_schema(n)
    jsonschema.validate(value, schema)
    rows = [copy.deepcopy(row) for row in value['frames']]
    if sorted(row['index'] for row in rows) != list(range(n)):
        raise ValueError('index_coverage')
    rows.sort(key=lambda row: row['index'])
    row_map = {row['index']: row for row in rows}
    for row in rows:
        if not row['reason'].strip():
            raise ValueError('empty_reason')
        if row['decision'] == 'omit':
            target = row['covered_by']
            if target is None or target == row['index']:
                raise ValueError('invalid_replacement')
            seen = {row['index']}
            curr = target
            while curr is not None and row_map[curr]['decision'] == 'omit':
                if curr in seen:
                    raise ValueError('cycle_in_replacement')
                seen.add(curr)
                curr = row_map[curr]['covered_by']
            if curr is None or row_map[curr]['decision'] != 'keep':
                raise ValueError('invalid_replacement')
            row['covered_by'] = curr
        elif row['covered_by'] is not None:
            raise ValueError('unexpected_replacement')
    return {'frames': rows}


def make_body(settings, paragraph, batch, context_text, image_payloads):
    """Build the multimodal JSON request body matching R1 E02."""
    header = prompts.CONTENT_VISUAL_SELECT + '\n본문:\n' + paragraph['text'] + '\n대응 원문:\n' + context_text
    content = [{'type': 'text', 'text': header}]
    for index, (fig, b64) in enumerate(zip(batch, image_payloads)):
        content.append(
            {'type': 'text', 'text': f'image index={index}; video_seconds={fig["actual_video_seconds"]}'}
        )
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}})
    return {
        'model': settings.qwen_body_model,
        'messages': [{'role': 'user', 'content': content}],
        'temperature': 0,
        'max_tokens': 2048,
        'stream': False,
        'chat_template_kwargs': {'enable_thinking': False},
        'response_format': {
            'type': 'json_schema',
            'json_schema': {
                'name': 'r1_selection',
                'strict': True,
                'schema': selection_schema(len(batch)),
            },
        },
    }


def resolve_decisions(figs, kept_ids, decisions_by_id):
    """Resolve replacement chains and verify every omit reaches a final retained figure."""
    fig_ids = [f['asset_id'] for f in figs]
    fig_id_set = set(fig_ids)
    kept_set = set(kept_ids)
    if not kept_set <= fig_id_set:
        raise ValueError('unknown_kept_asset')
    resolved = {}
    for aid, rec in decisions_by_id.items():
        if aid not in fig_id_set:
            raise ValueError('unknown_decision_asset')
        if rec['decision'] == 'uncertain' and aid not in kept_set:
            raise ValueError('uncertain_not_kept')
        if rec['decision'] == 'omit' and aid in kept_set:
            raise ValueError('omitted_asset_still_kept')
        covered = rec.get('covered_by')
        if rec['decision'] != 'omit' and covered is not None:
            raise ValueError('unexpected_replacement')
        if covered is not None and covered not in fig_id_set:
            raise ValueError('unknown_replacement_id')
        target = covered
        seen = {aid}
        while target is not None and target not in kept_set:
            if target in seen or target not in decisions_by_id:
                raise ValueError('replacement_chain')
            seen.add(target)
            target = decisions_by_id[target].get('covered_by')
            if target is not None and target not in fig_id_set:
                raise ValueError('unknown_replacement_id')
        if rec['decision'] == 'omit' and (target is None or target not in kept_set):
            raise ValueError('replacement_missing')
        resolved[aid] = {**rec, 'final_covered_by': target}
    ordered = [resolved[aid] for aid in fig_ids if aid in resolved]
    return ordered


def paragraph_identity(settings, counter, paragraph, bundle):
    """Compute a content/policy/runtime identity digest for one paragraph's selection."""
    sources = bundle['sentences']
    return digest(
        {
            'policy': settings.visual_selection_policy,
            'schema_version': SCHEMA_VERSION,
            'prompt_sha256': digest(prompts.CONTENT_VISUAL_SELECT),
            'module_sha256': sha(__file__),
            'max_pixels': settings.visual_selection_max_pixels,
            'paragraph_id': paragraph['paragraph_id'],
            'text': paragraph['text'],
            'source_indices': paragraph['source_indices'],
            'source_texts': [sources[i]['text'] for i in paragraph['source_indices']],
            'figures': [
                {
                    'asset_id': f['asset_id'],
                    'actual_video_seconds': f['actual_video_seconds'],
                    'image': f['image'],
                }
                for f in paragraph['figures']
            ],
            'tokenizer': counter.identity,
        }
    )


def _run_e02_compaction(figs, step_fn, policy):
    """Run or replay E02 reverse 2-pass compaction using a supplied batch stepper."""
    before_assets = [f['asset_id'] for f in figs]
    if len(figs) < 2:
        return {
            'policy': policy,
            'before_assets': before_assets,
            'kept': before_assets,
            'decisions': [],
            'calls': [],
            'rounds': 0,
        }
    kept = {}
    decisions = {}
    calls = []
    pool = list(figs)
    rounds = 0
    for pass_index in range(2):
        rounds += 1
        carry = None
        end = len(pool)
        while end > 0:
            start = max(0, end - (3 if carry is not None else 4))
            batch = pool[start:end] + ([carry] if carry is not None else [])
            end = start
            call_entry, rows = step_fn(pass_index, batch)
            calls.append(call_entry)
            job_key = call_entry['job_ref']['job_key']
            for fig, row in zip(batch, rows):
                aid = fig['asset_id']
                if row['decision'] == 'omit':
                    kept.pop(aid, None)
                else:
                    kept[aid] = fig
                decisions[aid] = {
                    'asset_id': aid,
                    'decision': row['decision'],
                    'reason': row['reason'],
                    'covered_by': batch[row['covered_by']]['asset_id']
                    if row['covered_by'] is not None
                    else None,
                    'call': job_key,
                    'pass_index': pass_index,
                }
            carry = next((fig for fig in batch if fig['asset_id'] in kept), None)
        new_pool = [fig for fig in figs if fig['asset_id'] in kept]
        if len(pool) <= 4 or len(new_pool) == len(pool) or len(new_pool) < 2:
            break
        pool = new_pool
    kept_ids = [fig['asset_id'] for fig in figs if fig['asset_id'] in kept]
    resolved = resolve_decisions(figs, kept_ids, decisions)
    return {
        'policy': policy,
        'before_assets': before_assets,
        'kept': kept_ids,
        'decisions': resolved,
        'calls': calls,
        'rounds': rounds,
    }


def select_paragraph(ctx, paragraph, bundle):
    """Run E02 reverse 2-pass visual compaction for a single paragraph."""
    sources = bundle['sentences']
    context_text = '\n'.join(sources[i]['text'] for i in paragraph['source_indices'])

    def step(pass_index, batch):
        images = [f['image'] for f in batch]
        image_payloads = [
            base64.b64encode(ctx.hashes.check(ctx.work, img).read_bytes()).decode('ascii')
            for img in images
        ]
        body = make_body(ctx.settings, paragraph, batch, context_text, image_payloads)
        targets = [f['asset_id'] for f in batch]
        spec = {
            'schema': selection_schema(len(batch)),
            'targets': targets,
            'paragraph_id': paragraph['paragraph_id'],
            'pass_index': pass_index,
        }
        dependencies = {
            'source_indices': paragraph['source_indices'],
            'image_order': [
                {'asset_id': f['asset_id'], 'sha256': f['image']['sha256']} for f in batch
            ],
        }
        invoked = ctx.invoke(
            'visual_select',
            'content.visual_select',
            body,
            spec,
            dependencies,
            images=images,
        )
        call_entry = {
            'job_ref': invoked['job_ref'],
            'spec': invoked['spec'],
            'value': invoked['value'],
            'pass_index': pass_index,
            'targets': targets,
        }
        return call_entry, invoked['value']['frames']

    out = _run_e02_compaction(paragraph['figures'], step, ctx.settings.visual_selection_policy)
    return {'paragraph_id': paragraph['paragraph_id'], **out}


def replay_paragraph(paragraph, calls, policy):
    """Deterministically reconstruct selection decisions from verified job records."""
    it = iter(calls)

    def step(pass_index, batch):
        try:
            call = next(it)
        except StopIteration as exc:
            raise ValueError('visual_selection_missing_call') from exc
        if call['pass_index'] != pass_index or call['targets'] != [f['asset_id'] for f in batch]:
            raise ValueError('visual_selection_call_batch_mismatch')
        return call, call['value']['frames']

    out = _run_e02_compaction(paragraph['figures'], step, policy)
    if next(it, None) is not None:
        raise ValueError('visual_selection_extra_calls')
    return {'paragraph_id': paragraph['paragraph_id'], **out}


def apply_selection(article_doc, paragraph_records):
    """Return a copy of article_doc keeping only selected figures without altering prose/structure."""
    updated = copy.deepcopy(article_doc)
    by_pid = {rec['paragraph_id']: rec for rec in paragraph_records}
    all_paragraphs = [p for section in updated['sections'] for p in section['paragraphs']]
    if len(by_pid) != len(all_paragraphs):
        raise ValueError('selection_paragraph_count_mismatch')
    placements = []
    for paragraph in all_paragraphs:
        pid = paragraph['paragraph_id']
        if pid not in by_pid:
            raise ValueError(f'selection_missing_paragraph:{pid}')
        rec = by_pid[pid]
        before = [f['asset_id'] for f in paragraph['figures']]
        if rec['before_assets'] != before:
            raise ValueError(f'selection_before_mismatch:{pid}')
        keep_set = set(rec['kept'])
        if not keep_set <= set(before):
            raise ValueError(f'selection_unknown_kept:{pid}')
        paragraph['figures'] = [f for f in paragraph['figures'] if f['asset_id'] in keep_set]
        if [f['asset_id'] for f in paragraph['figures']] != rec['kept']:
            raise ValueError(f'selection_order_mismatch:{pid}')
        placements.extend(paragraph['figures'])
    updated['asset_placements'] = placements
    return updated
