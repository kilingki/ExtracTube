"""Source-preserving plans and bounded, CPU-only request token accounting."""

from pathlib import Path
from .errors import ContentError
from .store import digest, sha, wire


def obj(properties):
    return {
        'type': 'object',
        'properties': properties,
        'required': list(properties),
        'additionalProperties': False,
    }


def array(items, maximum, minimum=0):
    return {'type': 'array', 'items': items, 'minItems': minimum, 'maxItems': maximum}


MAX_LEAF_SENTENCES = 28
MAX_LEAF_CHARS = 3500


def outline_schema(start, end):
    child = obj(
        {
            'start_sentence': {'type': 'integer', 'minimum': start, 'maximum': end - 1},
            'title': {'type': 'string', 'minLength': 1, 'maxLength': 60},
        }
    )
    root = obj(
        {
            'start_sentence': {'type': 'integer', 'minimum': start, 'maximum': end - 1},
            'title': {'type': 'string', 'minLength': 1, 'maxLength': 60},
            'children': array(child, 24),
        }
    )
    return obj({'sections': array(root, 24, 1)})


def validate_outline(value, spec):
    import jsonschema

    jsonschema.validate(value, spec['schema'])
    start, end = spec['target']
    roots = value['sections']
    if (
        not roots
        or type(roots[0]['start_sentence']) is not int
        or roots[0]['start_sentence'] != start
    ):
        raise ValueError('outline_partition')
    prev = None
    for node in roots:
        s = node['start_sentence']
        if type(s) is not int or not (start <= s < end) or (prev is not None and s <= prev):
            raise ValueError('outline_partition')
        if not node['title'].strip():
            raise ValueError('outline_title')
        prev = s
    normalized = []
    for i, node in enumerate(roots):
        a = node['start_sentence']
        z = roots[i + 1]['start_sentence'] if i + 1 < len(roots) else end
        children_in = node['children']
        norm_children = []
        if children_in:
            c_prev = None
            for c in children_in:
                cs = c['start_sentence']
                if (
                    type(cs) is not int
                    or not (a <= cs < z)
                    or (c_prev is not None and cs <= c_prev)
                ):
                    raise ValueError('outline_partition')
                if not c['title'].strip():
                    raise ValueError('outline_title')
                c_prev = cs
            if children_in[0]['start_sentence'] > a:
                norm_children.append(
                    {
                        'sentence_start': a,
                        'sentence_end': children_in[0]['start_sentence'],
                        'title': node['title'].strip(),
                    }
                )
            for j, c in enumerate(children_in):
                ca = c['start_sentence']
                cz = children_in[j + 1]['start_sentence'] if j + 1 < len(children_in) else z
                norm_children.append(
                    {'sentence_start': ca, 'sentence_end': cz, 'title': c['title'].strip()}
                )
        normalized.append(
            {
                'sentence_start': a,
                'sentence_end': z,
                'title': node['title'].strip(),
                'children': norm_children,
            }
        )
    return {'sections': normalized}


def source_bundle(handoff):
    rows = [s for unit in handoff['units'] for s in unit['sentences']]
    if [s['sentence_index'] for s in rows] != list(range(len(rows))):
        raise ContentError('원문 문장 순서/담당 범위가 다릅니다.', kind='input')
    return {
        'schema_version': 'extractube.content.source.v1',
        'video_id': handoff['video_id'],
        'sentences': rows,
        'units': handoff['units'],
        'assets': handoff['assets'],
        'coverage': handoff['coverage'],
        'omitted_audio_chunks': handoff['omitted_audio_chunks'],
        'warnings': handoff['warnings'],
        'usage_policy': handoff['usage_policy'],
        'source': handoff['source'],
        'source_url': handoff['source_url'],
    }


def _split_leaf_range(rows, a, z, max_sentences=MAX_LEAF_SENTENCES, max_chars=MAX_LEAF_CHARS):
    total_chars = sum(len(rows[i]['text']) for i in range(a, z))
    if (z - a) <= max_sentences and total_chars <= max_chars:
        return [(a, z)]
    parts = max(
        (z - a + max_sentences - 1) // max_sentences, (total_chars + max_chars - 1) // max_chars
    )
    step_sentences = min(max_sentences, max(1, (z - a + parts - 1) // parts))
    spans = []
    cur = a
    while cur < z:
        end = cur
        chars = 0
        while (
            end < z
            and (end - cur) < step_sentences
            and (chars + len(rows[end]['text']) <= max_chars or end == cur)
        ):
            chars += len(rows[end]['text'])
            end += 1
        spans.append((cur, end))
        cur = end
    return spans


def generation_plan(bundle, outline):
    rows = bundle['sentences']
    units = []
    for root_index, root in enumerate(outline['sections']):
        for leaf in root['children'] or [root]:
            for a, z in _split_leaf_range(rows, leaf['sentence_start'], leaf['sentence_end']):
                units.append(
                    {
                        'unit_id': f'u{len(units):04d}',
                        'root_index': root_index,
                        'root_title': root['title'],
                        'title': leaf['title'],
                        'sentence_start': a,
                        'sentence_end': z,
                        'sentences': rows[a:z],
                        'previous_context': rows[max(0, a - 2) : a],
                        'cautions': [],
                        'verified_visual_notes': [],
                        'previous_context_policy': 'two_adjacent_sentences_readonly',
                        'long_range_reference_status': 'not_reviewed',
                    }
                )
    if [s['sentence_index'] for u in units for s in u['sentences']] != list(range(len(rows))):
        raise ContentError('작성 계획에 누락/중복이 있습니다.', kind='validation')
    return {
        'schema_version': 'extractube.content.plan.v1',
        'units': units,
        'reader_policy': 'excluded',
        'preserve_all_information': True,
    }


class TokenCounter:
    """Local tokenizer only; never imports a model or manages its service."""

    def __init__(self, path):
        if not path or not Path(path).is_dir():
            raise ContentError('로컬 --tokenizer-path가 필요합니다.', kind='usage')
        from transformers import AutoTokenizer
        import transformers

        root = Path(path).resolve()
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(root), local_files_only=True, trust_remote_code=False
        )
        names = (
            'tokenizer.json',
            'tokenizer_config.json',
            'chat_template.jinja',
            'config.json',
            'vocab.json',
            'merges.txt',
        )
        self.identity = {
            'transformers': transformers.__version__,
            'files': {n: sha(root / n) for n in names if (root / n).is_file()},
            'template': digest(self.tokenizer.chat_template),
        }
        if not self.tokenizer.chat_template:
            raise ContentError('tokenizer chat template이 없습니다.', kind='usage')

    def count(self, body):
        encoded = self.tokenizer.apply_chat_template(
            body['messages'],
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
            return_dict=True,
        )
        return len(encoded['input_ids'])


def check_budget(body, settings, counter):
    count = counter.count(body)
    if type(count) is not int or count < 0:
        raise ContentError('정확한 입력 토큰 수가 필요합니다.', kind='input')
    if (
        count > settings.input_token_limit
        or count + body['max_tokens'] + settings.safety_tokens > settings.context_limit
    ):
        raise ContentError(
            f'입력 토큰 한도 초과: {count}. 원문을 삭제하지 않고 미전송 종료합니다.',
            kind='input_too_large',
        )
    if len(wire(body)) > settings.max_request_bytes:
        raise ContentError('요청 byte 한도 초과', kind='body')
    return {
        'input_tokens': count,
        'output_reserve': body['max_tokens'],
        'safety_tokens': settings.safety_tokens,
        'context_limit': settings.context_limit,
        'tokenizer_identity': counter.identity,
    }


def request_body(settings, system, payload, schema, name):
    return {
        'model': settings.qwen_body_model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': wire(payload).decode()},
        ],
        'temperature': 0,
        'max_tokens': settings.output_tokens,
        'stream': False,
        'chat_template_kwargs': {'enable_thinking': False},
        'response_format': {
            'type': 'json_schema',
            'json_schema': {'name': name, 'strict': True, 'schema': schema},
        },
    }
