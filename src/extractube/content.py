"""Stage 3 lifecycle: verified handoff -> source plan -> writing -> standalone article."""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import copy
import math
import uuid
from . import article, content_plan, infer, prompts, store, writing
from .errors import ContentError, InferencePaused, InferenceJobFailed
from .settings import ContentSettings
from .media import validate_video_id

POLICY = 'extractube.content.source_preserving.v2'
PARSER = 'extractube.content.strict.v2'
COMPLETE = ('complete', 'complete_with_warnings', 'empty')
CODES = (
    'content.py',
    'content_plan.py',
    'writing.py',
    'article.py',
    'infer.py',
    'prompts.py',
    'settings.py',
    'store.py',
    'errors.py',
    'pipeline.py',
)


def parse_response(body, status, spec, thinking):
    import jsonschema

    validators = {
        'content.outline': content_plan.validate_outline,
        'content.write': writing.validate,
        'content.repair': writing.validate_repair,
    }
    try:
        raw = store.loads(body)
        if status == 200:
            message = raw['choices'][0]['message']
            if message.get('reasoning') or message.get('reasoning_content'):
                raise ValueError('unexpected_reasoning')
            if infer.content_text(message.get('content')).lstrip().startswith('```'):
                raise ValueError('code_fence')
            observed = raw.get('usage', {}).get('prompt_tokens')
            if observed is not None and observed != spec['token_plan']['input_tokens']:
                raise ValueError(
                    f'tokenizer_runtime_mismatch:{observed}!={spec["token_plan"]["input_tokens"]}'
                )
        return infer.parse_response(
            body, status, spec, thinking, validator=validators[spec['task']]
        )
    except jsonschema.ValidationError as exc:
        raise ValueError('schema:' + exc.message[:400]) from exc
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise ValueError('content_response_shape') from exc


def snapshot(settings, counter):
    codes = {name: store.sha(Path(__file__).parent / name) for name in CODES}
    return {
        'schema_version': 'extractube.content.config.v1',
        'policy': POLICY,
        'settings': asdict(settings),
        'code_sha256': codes,
        'tokenizer': counter.identity,
        'parser_policy': PARSER,
        'projections': {
            key: {'code': codes, 'tokenizer': counter.identity, 'policy': POLICY}
            for key in ('outline', 'write', 'repair')
        },
    }


def load_input(work, video_id, vision_run_id=None, expected_inputs=None):
    from . import handoff

    if vision_run_id is None:
        vision_run_id = store.read(work / 'vision/current.json')['run_id']
    handoff.verify_locked(work, video_id, vision_run_id)
    directory = store.safe(work, f'vision/runs/{vision_run_id}')
    result = store.read(directory / 'result.json')
    source = store.read(store.HashCache().check(work, result['handoff']))
    if (
        source['schema_version'] != 'extractube.handoff.v1'
        or source['policy_id'] != 'extractube.stage2.image_first.v1'
    ):
        raise ContentError('지원하지 않는 제품 handoff입니다.', kind='input')
    original = store.read(directory / 'input.json')
    selected = {
        'vision_run_id': vision_run_id,
        'vision_result': store.ref(work, directory / 'result.json'),
        'handoff': result['handoff'],
        'stage1_identities': original['stage1_identities'],
    }
    if expected_inputs and any(selected.get(k) != v for k, v in expected_inputs.items()):
        raise ContentError('pipeline 반환 이후 입력이 변경되었습니다.', kind='input')
    critical = [
        {k: r[k] for k in ('path', 'sha256')} for r in original['stage1_upstream_records'].values()
    ]
    critical += [selected['vision_result'], selected['handoff']] + [
        a['image'] for a in source['assets']
    ]
    doc = {
        'schema_version': 'extractube.content.input.v1',
        'video_id': video_id,
        **selected,
        'critical_refs': critical,
    }
    doc['input_digest'] = store.digest(doc)
    return doc, content_plan.source_bundle(source)


class Context:
    cache_namespace = 'content'
    parser_policy = PARSER
    preserve_failures = True
    parse_response = staticmethod(parse_response)

    def __init__(self, work, directory, settings, config, input_doc, state, counter, log):
        self.work = work
        self.directory = directory
        self.run_id = directory.name
        self.settings = settings
        self.config = config
        self.input = input_doc
        self.state = state
        self.counter = counter
        self.log = log
        self.hashes = store.HashCache()
        self.critical_refs = input_doc['critical_refs']
        self.invocation_calls = 0
        self.reused_jobs = 0
        self.jobs = infer.Jobs(self)

    def allow_reuse(self, stage):
        return True

    def require_reuse(self, stage):
        return False

    def save_state(self):
        self.state['updated_at'] = datetime.now(timezone.utc).isoformat()
        store.write(self.directory / 'state.json', self.state)

    def check_inputs(self):
        for reference in self.critical_refs:
            self.hashes.check(self.work, reference)
        meta = store.read(self.work / 'metadata.json')
        if any(
            store.identity(meta['results'][k]) != v
            for k, v in self.input['stage1_identities'].items()
        ):
            raise ContentError('실행 중 Stage 1 identity가 변경되었습니다.', kind='input')
        for name, expected in self.config['code_sha256'].items():
            if store.sha(Path(__file__).parent / name) != expected:
                raise ContentError('실행 코드가 변경되었습니다.', kind='input')

    def invoke(self, stage, task, body, spec, dependencies):
        measurement = content_plan.check_budget(body, self.settings, self.counter)
        spec = {**spec, 'token_plan': measurement}
        if self.log:
            self.log(f'[content.{stage}] input_tokens={measurement["input_tokens"]}')
        value, job_ref, status = self.jobs.invoke(
            stage, task, None, spec, dependencies, request_body=body
        )
        return {'value': value, 'job_ref': job_ref, 'spec': {'task': task, **spec}}


def recover(work):
    """Unknown calls block all new runs, including changed-code attempts to bypass them."""
    for directory in sorted((work / 'content/runs').glob('*')):
        if not store.RUN.fullmatch(directory.name) or not (directory / 'state.json').is_file():
            continue
        state = store.read(directory / 'state.json')
        infer.recover_calls(work, directory, state, parser=parse_response, namespace='content')
        for path in (directory / 'jobs').glob('*.json'):
            for attempt in store.read(path)['attempts']:
                record = store.safe(work, attempt['path']) / 'record.json'
                if record.is_file() and store.read(record)['status'] == 'unknown':
                    state['execution_status'] = 'blocked_unknown'
                    store.write(directory / 'state.json', state)
                    raise ContentError(
                        f'미확정 호출: {directory.name}/{attempt["call_id"]}. 자동 재전송하지 않습니다.',
                        kind='blocked_unknown',
                    )


def load_settings(video_id, data_root, run_id):
    validate_video_id(video_id)
    if not store.RUN.fullmatch(run_id):
        raise ContentError('run ID 오류', kind='usage')
    return ContentSettings(
        **store.read(store.safe(Path(data_root) / video_id, f'content/runs/{run_id}/config.json'))[
            'settings'
        ]
    )


def _outline_body(settings, sentences, start, end):
    schema = content_plan.outline_schema(start, end)
    payload = {
        'target_start': start,
        'target_end': end,
        'sentences': [
            {'index': s['sentence_index'], 'text': s['text']} for s in sentences[start:end]
        ],
    }
    return schema, content_plan.request_body(
        settings, prompts.CONTENT_OUTLINE, payload, schema, 'content_outline'
    )


def _outline_slices(sentences, settings, counter):
    n = len(sentences)
    if n == 0:
        return []
    _, full_body = _outline_body(settings, sentences, 0, n)
    try:
        content_plan.check_budget(full_body, settings, counter)
        return [(0, n)]
    except ContentError as exc:
        if exc.kind not in ('input_too_large', 'body') or n == 1:
            raise
    slices = []
    start = 0
    while start < n:
        _, min_body = _outline_body(settings, sentences, start, start + 1)
        content_plan.check_budget(min_body, settings, counter)
        if start + 1 == n:
            slices.append((start, n))
            break
        _, rem_body = _outline_body(settings, sentences, start, n)
        try:
            content_plan.check_budget(rem_body, settings, counter)
            slices.append((start, n))
            break
        except ContentError as exc:
            if exc.kind not in ('input_too_large', 'body'):
                raise
        lo, hi = start + 1, n - 1
        best = start + 1
        while lo <= hi:
            mid = (lo + hi) // 2
            _, mid_body = _outline_body(settings, sentences, start, mid)
            try:
                content_plan.check_budget(mid_body, settings, counter)
                best = mid
                lo = mid + 1
            except ContentError as exc:
                if exc.kind not in ('input_too_large', 'body'):
                    raise
                hi = mid - 1
        slices.append((start, best))
        start = best
    return slices


def _effective_max_repairs(settings, unit_count):
    if settings.max_auto_repairs == 0:
        return 0
    return max(settings.max_auto_repairs, math.ceil(max(1, unit_count) * 0.25))


def _draft(ctx, unit, bundle, *, max_repairs=None):
    path = ctx.directory / 'drafts' / f'{unit["unit_id"]}.json'
    if path.exists():
        return store.read(path)
    spec = {'schema': writing.schema(unit), 'unit': unit}
    material = writing.payload(unit, bundle['source_url'])
    body = content_plan.request_body(
        ctx.settings, prompts.CONTENT_WRITE, material, spec['schema'], 'content_write'
    )
    dependencies = {'input': ctx.input['source_digest'], 'unit': store.digest(unit)}
    failure_path = ctx.directory / 'drafts' / f'{unit["unit_id"]}.failure.json'
    try:
        if failure_path.exists():
            failure = store.read(failure_path)
        else:
            result = ctx.invoke('write', 'content.write', body, spec, dependencies)
            store.write(path, result, immutable=True)
            return result
    except InferenceJobFailed as exc:
        record = store.read(exc.diagnostic / 'record.json')
        if (
            record['http_status'] != 200
            or not record['response_complete']
            or record['finish_reason'] != 'stop'
        ):
            raise
        failure = {
            'call': store.ref(ctx.work, exc.diagnostic / 'record.json'),
            'error': record['error_code'],
            'response': store.ref(ctx.work, exc.diagnostic / 'response.body'),
        }
        store.write(failure_path, failure, immutable=True)
    authorized = ctx.directory / 'drafts' / f'{unit["unit_id"]}.repair.json'
    repair_limit = _effective_max_repairs(ctx.settings, 1) if max_repairs is None else max_repairs
    if not authorized.exists():
        count = len(list((ctx.directory / 'drafts').glob('*.repair.json')))
        if count >= repair_limit:
            raise ContentError('자동 국소 수리 상한에 도달했습니다.', kind='validation')
        store.write(authorized, {'failure': failure, 'unit_id': unit['unit_id']}, immutable=True)
    raw = store.read(ctx.hashes.check(ctx.work, failure['response']))
    repair_guides = {
        'paragraph_order': '문단들의 원문 순서(paragraph_order)를 지켜 이전 문단의 문장보다 앞선 문장을 다음 문단에서 반복하거나 앞세우지 않는다.',
        'paragraph_span': '한 문단에 너무 많은 문장을 묶지 말고 paragraph_plan의 max_sentences_per_paragraph 이하로 나눈다.',
        'source_coverage': '담당 assigned_sentences의 모든 문장이 빠짐없이 문단 또는 editorial/unresolved에 배정되도록 한다.',
    }
    err_base = failure['error'].split(':', 1)[0]
    instruction = repair_guides.get(err_base, '담당 원문을 보존하고 지적된 계약 결함만 수정한다.')
    if failure['error'].startswith('source_coverage:missing_'):
        instruction = f"{repair_guides['source_coverage']} 누락 문장 번호: {failure['error'].split(':', 1)[1]}"
    material['local_repair_directive'] = {
        'error': failure['error'],
        'previous_response': infer.content_text(raw['choices'][0]['message'].get('content')),
        'instruction': instruction,
    }
    body = content_plan.request_body(
        ctx.settings, prompts.CONTENT_WRITE, material, spec['schema'], 'content_write'
    )
    repair_spec = {**spec, 'soft_anchor_fallback': True}
    result = ctx.invoke(
        'write',
        'content.write',
        body,
        repair_spec,
        {**dependencies, 'repair': store.digest(failure)},
    )
    store.write(path, result, immutable=True)
    return result


def _review(ctx, base, bundle, review):
    current = base
    records = []
    base_sha = store.digest(base)
    tickets = review.get('tickets', []) if review else []
    if len(tickets) > ctx.settings.max_review_repairs:
        raise ContentError('검수 수리 상한 초과', kind='usage')
    for i, ticket in enumerate(tickets):
        paragraph, evidence = writing.ticket_target(current, ticket, bundle, base_sha256=base_sha)
        a, z = ticket['span']
        schema = writing.repair_schema(ticket['evidence_indices'])
        payload = {
            'draft_span': paragraph['text'][a:z],
            'paragraph': paragraph,
            'source': evidence,
            'review_observation': ticket['observation'],
            'document_context': {
                'delivery': 'standalone_html',
                'available_resources': ['original_video_link'],
            },
        }
        body = content_plan.request_body(
            ctx.settings, prompts.CONTENT_REPAIR, payload, schema, 'content_repair'
        )
        result = ctx.invoke(
            'repair',
            'content.repair',
            body,
            {'schema': schema, 'evidence': evidence},
            {'article': store.digest(current), 'ticket': store.digest(ticket)},
        )
        acceptance = ticket.get('acceptance')
        selected = False
        if acceptance:
            current = writing.apply_patch(
                current, ticket, result['value'], acceptance, base_sha256=base_sha
            )
            selected = True
        row = {'ticket': ticket, **result, 'selected': selected}
        records.append(row)
        store.write(ctx.directory / f'reviews/{i:04d}.json', row, immutable=True)
    return current, records


def _load_or_build_outline(ctx, bundle):
    """Reuse an outline or generate bounded slices, retaining each source job."""
    directory = ctx.directory
    settings = ctx.settings
    counter = ctx.counter
    input_doc = ctx.input
    if bundle['sentences']:
        path = directory / 'outline.json'
        if path.exists():
            outline_record = store.read(path)
        else:
            slices = _outline_slices(bundle['sentences'], settings, counter)
            if len(slices) == 1:
                s_start, s_end = slices[0]
                schema, body = _outline_body(settings, bundle['sentences'], s_start, s_end)
                outline_record = ctx.invoke(
                    'outline',
                    'content.outline',
                    body,
                    {'schema': schema, 'target': [s_start, s_end]},
                    {'input': input_doc['source_digest']},
                )
            else:
                part_records = []
                merged_sections = []
                for s_start, s_end in slices:
                    schema, body = _outline_body(settings, bundle['sentences'], s_start, s_end)
                    part = ctx.invoke(
                        'outline',
                        'content.outline',
                        body,
                        {'schema': schema, 'target': [s_start, s_end]},
                        {'input': input_doc['source_digest'], 'target': [s_start, s_end]},
                    )
                    part_records.append(part)
                    merged_sections.extend(part['value']['sections'])
                outline_record = {'value': {'sections': merged_sections}, 'parts': part_records}
            store.write(path, outline_record, immutable=True)
        outline = outline_record['value']
    else:
        outline = {'sections': []}
        store.write(directory / 'outline.json', {'value': outline}, immutable=True)
    return outline


def _prepare_writing(ctx, bundle, outline):
    """Check all writing requests before sending the first one."""
    directory = ctx.directory
    settings = ctx.settings
    counter = ctx.counter
    plan = content_plan.generation_plan(bundle, outline)
    store.write(directory / 'generation_plan.json', plan, immutable=True)
    # Preflight every leaf before sending any writing request.
    measurements = []
    for unit in plan['units']:
        body = content_plan.request_body(
            settings,
            prompts.CONTENT_WRITE,
            writing.payload(unit, bundle['source_url']),
            writing.schema(unit),
            'content_write',
        )
        measurements.append(content_plan.check_budget(body, settings, counter))
    max_repairs = _effective_max_repairs(settings, len(plan['units']))
    store.write(
        directory / 'plan.json',
        {
            'generation_units': len(plan['units']),
            'max_auto_repairs': max_repairs,
            'writing_tokens': measurements,
        },
        immutable=True,
    )
    return plan, max_repairs


def _publish_article(ctx, bundle, outline, plan, drafts, review):
    """Assemble, review, and verify artifacts before publishing the pointer."""
    work = ctx.work
    directory = ctx.directory
    state = ctx.state
    input_doc = ctx.input
    video_id = work.name
    meta = store.read(work / 'metadata.json')
    title = meta.get('title') or (
        outline['sections'][0]['title'] if outline['sections'] else video_id
    )
    built = article.assemble(bundle, outline, plan, drafts, title)
    built, reviews = _review(ctx, built, bundle, review)
    article.validate(built, bundle, plan)
    unresolved = sum(len(s['unresolved']) for s in built['sections'])
    quality = 'revise' if unresolved or reviews else 'unreviewed'
    warnings = bool(bundle['warnings'] or bundle['omitted_audio_chunks'] or unresolved)
    status = (
        'empty'
        if not bundle['sentences']
        else ('complete_with_warnings' if warnings else 'complete')
    )
    calls, elapsed, reserved = infer.budget_from_journals(directory)
    state['budget'].update(http_calls=calls, http_seconds=elapsed, reserved_calls=reserved)
    report = {
        'schema_version': 'extractube.content.result.v1',
        'run_id': directory.name,
        'video_id': video_id,
        'execution_status': status,
        'quality_status': quality,
        'user_accepted': False,
        'quality_review_performed': False,
        'source_sentence_count': len(bundle['sentences']),
        'generation_units': len(plan['units']),
        'paragraphs': sum(len(s['paragraphs']) for s in built['sections']),
        'assets': len(built['asset_placements']),
        'unresolved': unresolved,
        'coverage': bundle['coverage'],
        'warnings': bundle['warnings'],
        'budget': copy.deepcopy(state['budget']),
        'input_digest': input_doc['input_digest'],
        'code_policy': POLICY,
        'review_candidates': len(reviews),
    }
    store.write(directory / 'article.json', built, immutable=True)
    store.write(directory / 'article.html', article.render(built, work), raw=True, immutable=True)
    store.write(
        directory / 'review.html',
        article.render(built, work, bundle=bundle, report=report),
        raw=True,
        immutable=True,
    )
    report['artifacts'] = {
        p.relative_to(directory).as_posix(): store.ref(work, p)
        for p in directory.rglob('*')
        if p.is_file() and p.name not in ('state.json', 'result.json')
    }
    store.write(directory / 'result.json', report, immutable=True)
    ctx.check_inputs()
    verify_locked(work, video_id, directory.name)
    state.update(execution_status=status, quality_status=quality)
    ctx.save_state()
    store.write(
        work / 'content/current.json',
        {
            'run_id': directory.name,
            'input_digest': input_doc['input_digest'],
            'quality_status': quality,
            'result': store.ref(work, directory / 'result.json'),
        },
    )


def run(
    video_id,
    settings,
    *,
    data_root=Path('data'),
    vision_run_id=None,
    resume_run_id=None,
    review_path=None,
    expected_inputs=None,
    resume_budget_limits=None,
    counter=None,
    log=print,
):
    video_id = validate_video_id(video_id)
    work = Path(data_root).resolve() / video_id
    if not work.is_dir():
        raise ContentError('기존 작업 폴더가 없습니다.', kind='input')
    if resume_run_id and not store.RUN.fullmatch(resume_run_id):
        raise ContentError('run ID 오류', kind='usage')
    if resume_budget_limits and not resume_run_id:
        raise ContentError('예산 변경에는 resume이 필요합니다.', kind='usage')
    lock = store.WorkLock(work)
    try:
        recover(work)
        input_doc, bundle = load_input(work, video_id, vision_run_id, expected_inputs)
        review = store.read(Path(review_path)) if review_path else None
        input_doc['source_digest'] = input_doc['input_digest']
        input_doc['review'] = review
        input_doc['input_digest'] = store.digest(
            {k: v for k, v in input_doc.items() if k != 'input_digest'}
        )
        counter = counter or content_plan.TokenCounter(settings.tokenizer_path)
        config = snapshot(settings, counter)
        directory = None
        if resume_run_id:
            directory = store.safe(work, f'content/runs/{resume_run_id}')
            if (
                store.read(directory / 'input.json') != input_doc
                or store.read(directory / 'config.json') != config
            ):
                raise ContentError(
                    '재개 입력/코드/설정이 다릅니다. 새 run이 필요합니다.', kind='input'
                )
            state = store.read(directory / 'state.json')
            if state['execution_status'] in COMPLETE:
                verify_locked(work, video_id, resume_run_id)
                return result(work, directory)
            if state['execution_status'] not in ('pending', 'running', 'paused_budget'):
                raise ContentError('이 run은 재개할 수 없습니다.', kind='usage')
            for key, value in (resume_budget_limits or {}).items():
                field = {
                    'max_inference_calls': 'max_calls',
                    'max_http_seconds': 'max_http_seconds',
                }.get(key)
                if (
                    field is None
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value < state['budget'][field]
                ):
                    raise ContentError(
                        '재개 예산은 기존 유한한 한도 이상이어야 합니다.', kind='usage'
                    )
                from dataclasses import replace

                settings = replace(settings, **{key: value})
                state['limit_changes'].append(
                    {'field': field, 'before': state['budget'][field], 'after': value}
                )
                state['budget'][field] = value
        else:
            for path in (work / 'content/runs').glob('*/state.json'):
                old = store.read(path)
                if (
                    old['execution_status'] in ('pending', 'running', 'paused_budget')
                    and store.read(path.parent / 'input.json')['input_digest']
                    == input_doc['input_digest']
                ):
                    raise ContentError(
                        f'미완결 run을 --resume {path.parent.name}으로 재개하십시오.',
                        kind='paused_budget',
                    )
            current = work / 'content/current.json'
            if current.exists():
                pointer = store.read(current)
                previous = store.safe(work, f'content/runs/{pointer["run_id"]}')
                if (
                    pointer['input_digest'] == input_doc['input_digest']
                    and store.read(previous / 'config.json') == config
                ):
                    verify_locked(work, video_id, pointer['run_id'])
                    return result(work, previous)
            run_id = (
                datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:12]
            )
            directory = work / 'content/runs' / run_id
            directory.mkdir(parents=True)
            state = {
                'execution_status': 'pending',
                'quality_status': 'unreviewed',
                'budget': {
                    'max_calls': settings.max_inference_calls,
                    'max_http_seconds': settings.max_http_seconds,
                    'http_calls': 0,
                    'http_seconds': 0,
                    'reserved_calls': 0,
                },
                'blocked_call_ids': [],
                'limit_changes': [],
            }
            for name, value in [
                ('input', input_doc),
                ('config', config),
                ('source_bundle', bundle),
            ]:
                store.write(directory / f'{name}.json', value, immutable=True)
            store.write(directory / 'state.json', state)
        ctx = Context(work, directory, settings, config, input_doc, state, counter, log)
        state['execution_status'] = 'running'
        ctx.save_state()
        try:
            outline = _load_or_build_outline(ctx, bundle)
            plan, max_repairs = _prepare_writing(ctx, bundle, outline)
            drafts = [
                _draft(ctx, u, bundle, max_repairs=max_repairs)['value'] for u in plan['units']
            ]
            _publish_article(ctx, bundle, outline, plan, drafts, review)
        except InferencePaused as exc:
            state['execution_status'] = exc.status
            ctx.save_state()
        except KeyboardInterrupt:
            ctx.save_state()
            raise
        except Exception as exc:
            state.update(
                execution_status='failed',
                error={'kind': getattr(exc, 'kind', 'validation'), 'message': str(exc)},
            )
            ctx.save_state()
            raise
        return result(work, directory, ctx.invocation_calls, ctx.reused_jobs)
    finally:
        lock.release()


def result(work, directory, calls=0, reused=0):
    from .pipeline import ContentResult

    state = store.read(directory / 'state.json')
    report = store.read(directory / 'result.json') if (directory / 'result.json').exists() else {}
    return ContentResult(
        video_id=work.name,
        work_dir=work,
        run_id=directory.name,
        execution_status=state['execution_status'],
        quality_status=report.get('quality_status', 'unreviewed'),
        article_path=directory / 'article.json' if report else None,
        html_path=directory / 'article.html' if report else None,
        report_path=directory / 'result.json' if report else directory / 'state.json',
        inference_calls=calls,
        reused_jobs=reused,
        unresolved=report.get('unresolved', 0),
    )


def verify(video_id, *, data_root=Path('data'), run_id=None):
    work = Path(data_root).resolve() / validate_video_id(video_id)
    lock = store.WorkLock(work)
    try:
        return verify_locked(work, video_id, run_id)
    finally:
        lock.release()


def verify_locked(work, video_id, run_id=None, _seen=None):
    pointer = (
        store.read(work / 'content/current.json')
        if (work / 'content/current.json').exists()
        else None
    )
    if run_id is None:
        if pointer is None:
            raise ContentError('완료된 콘텐츠가 없습니다.', kind='validation')
        run_id = pointer['run_id']
    if not isinstance(run_id, str) or not store.RUN.fullmatch(run_id):
        raise ContentError('run ID 오류', kind='usage')
    directory = store.safe(work, f'content/runs/{run_id}')
    hashes = store.HashCache()
    seen = set(_seen or ())
    if run_id in seen:
        raise ValueError('render_derivation_cycle')
    seen.add(run_id)
    if pointer and pointer['run_id'] == run_id:
        hashes.check(work, pointer['result'])
    report = store.read(directory / 'result.json')
    if report['execution_status'] not in COMPLETE:
        raise ContentError('미완료 콘텐츠', kind='validation')
    for reference in report['artifacts'].values():
        hashes.check(work, reference)
    if 'render_source' in report:
        origin = report['render_source']
        hashes.check(work, origin['result'])
        verify_locked(work, video_id, origin['run_id'], seen)
        source = store.safe(work, f'content/runs/{origin["run_id"]}')
        built = store.read(directory / 'article.json')
        if built != store.read(source / 'article.json'):
            raise ValueError('render_source_article')
        if article.render(built, work) != (directory / 'article.html').read_bytes():
            raise ValueError('render_html')
        return {
            'ok': True,
            'run_id': run_id,
            'quality_status': report['quality_status'],
            'quality_review_performed': False,
            'http_calls': 0,
        }
    saved = store.read(directory / 'input.json')
    fresh, bundle = load_input(work, video_id, saved['vision_run_id'])
    fresh['source_digest'] = fresh['input_digest']
    fresh['review'] = saved['review']
    fresh['input_digest'] = store.digest({k: v for k, v in fresh.items() if k != 'input_digest'})
    if fresh != saved:
        raise ContentError('콘텐츠 upstream이 변경되었습니다.', kind='input')
    if store.read(directory / 'source_bundle.json') != bundle:
        raise ContentError('원문 bundle 변조', kind='validation')
    config = store.read(directory / 'config.json')
    checker = SimpleNamespace(work=work, hashes=hashes, parse_response=parse_response)
    outline_record = store.read(directory / 'outline.json')
    outline = outline_record['value']

    def check_job(record):
        job = record['job_ref']
        entry = store.read(hashes.check(work, job['result']))
        value, status = infer.validate_entry(checker, entry, record['spec'])
        if status != 'succeeded' or value != record['value']:
            raise ValueError('artifact_job_mismatch')
        return value

    if bundle['sentences']:
        if 'parts' in outline_record:
            parts_checked = []
            cursor = 0
            for part in outline_record['parts']:
                val = check_job(part)
                ps, pe = part['spec']['target']
                if ps != cursor or not ps < pe <= len(bundle['sentences']):
                    raise ValueError('outline_slice_partition')
                parts_checked.extend(val['sections'])
                cursor = pe
            if cursor != len(bundle['sentences']) or {'sections': parts_checked} != outline:
                raise ValueError('outline_parts_mismatch')
        else:
            if outline_record['spec']['target'] != [0, len(bundle['sentences'])]:
                raise ValueError('outline_target_mismatch')
            check_job(outline_record)
    plan = content_plan.generation_plan(bundle, outline)
    if plan != store.read(directory / 'generation_plan.json'):
        raise ValueError('generation_plan_changed')
    drafts = []
    for u in plan['units']:
        d_rec = store.read(directory / f'drafts/{u["unit_id"]}.json')
        if (
            d_rec['spec'].get('soft_anchor_fallback')
            and not (directory / f'drafts/{u["unit_id"]}.repair.json').is_file()
        ):
            raise ValueError('unauthorized_soft_anchor_fallback')
        drafts.append(check_job(d_rec))
    built = store.read(directory / 'article.json')
    expected = article.assemble(bundle, outline, plan, drafts, built['title'])
    base_sha = store.digest(expected)
    for path in sorted((directory / 'reviews').glob('*.json')):
        row = store.read(path)
        writing.ticket_target(expected, row['ticket'], bundle, base_sha256=base_sha)
        check_job(row)
        if row['selected']:
            expected = writing.apply_patch(
                expected,
                row['ticket'],
                row['value'],
                row['ticket']['acceptance'],
                base_sha256=base_sha,
            )
    if expected != built:
        raise ValueError('article_not_derived_from_drafts')
    article.validate(built, bundle, plan)
    if config['code_sha256']['article.py'] == store.sha(Path(__file__).with_name('article.py')):
        if article.render(built, work) != (directory / 'article.html').read_bytes():
            raise ValueError('html_article_mismatch')
    calls, elapsed, reserved = infer.budget_from_journals(directory)
    if (
        reserved
        or report['budget']['http_calls'] != calls
        or abs(report['budget']['http_seconds'] - elapsed) > 1e-6
    ):
        raise ValueError('content_budget_mismatch')
    if report['quality_status'] not in ('unreviewed', 'revise'):
        raise ValueError('unearned_quality_acceptance')
    return {
        'ok': True,
        'run_id': run_id,
        'quality_status': report['quality_status'],
        'quality_review_performed': False,
        'source_sentence_count': len(bundle['sentences']),
        'http_calls': calls,
    }


def render_only(video_id, *, data_root=Path('data'), run_id=None):
    work = Path(data_root).resolve() / validate_video_id(video_id)
    lock = store.WorkLock(work)
    try:
        if run_id is None:
            run_id = store.read(work / 'content/current.json')['run_id']
        verify_locked(work, video_id, run_id)
        source = store.safe(work, f'content/runs/{run_id}')
        old = store.read(source / 'result.json')
        built = store.read(source / 'article.json')
        new_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:12]
        directory = work / 'content/runs' / new_id
        report = {
            k: copy.deepcopy(v) for k, v in old.items() if k not in ('artifacts', 'render_source')
        }
        report.update(
            run_id=new_id,
            render_source={'run_id': run_id, 'result': store.ref(work, source / 'result.json')},
            budget={'http_calls': 0, 'http_seconds': 0, 'reserved_calls': 0},
            renderer_sha256=store.sha(Path(__file__).with_name('article.py')),
        )
        store.write(directory / 'article.json', built, immutable=True)
        store.write(
            directory / 'article.html', article.render(built, work), raw=True, immutable=True
        )
        store.write(
            directory / 'state.json',
            {
                'execution_status': report['execution_status'],
                'quality_status': report['quality_status'],
            },
        )
        report['artifacts'] = {
            p.name: store.ref(work, p) for p in directory.iterdir() if p.name != 'state.json'
        }
        store.write(directory / 'result.json', report, immutable=True)
        verify_locked(work, video_id, new_id)
        # Render derivations do not replace the generation pointer or its resumable settings.
        return result(work, directory)
    finally:
        lock.release()
