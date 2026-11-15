"""Stage 3 E02 editorial visual selection algorithm and product integration tests."""

from __future__ import annotations

import base64
import copy
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'stage2'))
from support import FakeTransport, VIDEO_ID, adapters, seed, settings as visual_settings

from extractube import article, content, content_plan, content_selection, infer, pipeline, prompts, store, vision
from extractube.errors import ContentError, InferenceJobFailed
from extractube.settings import ContentSettings, Settings


MODEL_DIR = Path('/home/kjh/workspace/models/llm/tf/qwen3.8-27b')
SAVED_E02_ROOT = Path(__file__).resolve().parents[1] / 'revision/R1/runs/20261111_e02'


class FixedCounter:
    identity = {'fixture_tokenizer': '1'}

    def count(self, body):
        return 100


class Stage2AllUseTransport(FakeTransport):
    """Stage 2 FakeTransport variation returning multiple distinct 'use' frames per candidate."""

    def __init__(self, target_use_count=6):
        super().__init__()
        self.target_use_count = target_use_count

    def send(self, url, body, timeout, limit, on_headers):
        request = store.loads(body)
        content_val = request['messages'][0]['content']
        schema_name = request.get('response_format', {}).get('json_schema', {}).get('name', '')
        if not isinstance(content_val, str) and schema_name != 't2_probe':
            import re

            self.calls.append(request)
            ids = [
                re.search(r'frame_id: (.*)', content_val[i]['text']).group(1)
                for i in range(1, len(content_val), 2)
                if 'role: target\n' in content_val[i]['text']
            ]
            rows = []
            for fid in ids:
                rows.append(
                    {
                        'frame_id': fid,
                        'decision': 'use',
                        'duplicate_of': None,
                        'relevance': 'yes',
                        'readability': 'sufficient',
                        'completeness': 'sufficient',
                        'visual_kind': 'ui',
                        'caption': f'화면 캡처 {fid[-6:]}',
                        'reason': '설명 화면',
                    }
                )
            parsed = {'frames': rows}
            raw = store.wire({'choices': [{'finish_reason': 'stop', 'message': {'content': store.wire(parsed).decode()}}]})
            on_headers(200, {'Content-Length': str(len(raw))})
            return {'status': 200, 'body': raw, 'complete': True}
        return super().send(url, body, timeout, limit, on_headers)


def distinct_fake_extract(ctx, samples):
    """Extract distinct per-pts RGB PNGs so image SHA-256 and order changes are observable."""
    from extractube import frames

    result = {}
    for s in samples:
        p = ctx.work / f'vision/cache/fixture/{s["pts"]}.png'
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            color = (s['pts'] % 251, (s['pts'] * 7) % 251, (s['pts'] * 13) % 251)
            Image.new('RGB', (160, 90), color).save(p)
        r = frames.image_ref(ctx.work, p)
        log = ctx.directory / f't4/extractions/{s["pts"]}.json'
        store.write(log, {'pts': s['pts']}, immutable=True)
        result[s['frame_id']] = {
            'frame_id': s['frame_id'],
            'stream_index': 0,
            'pts': s['pts'],
            'time_base': s['time_base'],
            'video_seconds': s['video_seconds'],
            'source_video': ctx.input['stage1_identities']['video'],
            'full': r,
            'vlm': r,
            'display_transform': 'fixture',
            'extraction_job_ref': {
                'job_key': store.digest(s),
                'source_run_id': ctx.run_id,
                'result': store.ref(ctx.work, log),
            },
        }
    return result


class ProductStage3Transport:
    """Mock VLM transport serving outline, write, repair, and programmable E02 selection."""

    def __init__(self, *, mode='keep_last_per_batch', fail_on_vs_call=None, unknown_on_vs_call=None):
        self.mode = mode
        self.fail_on_vs_call = fail_on_vs_call
        self.unknown_on_vs_call = unknown_on_vs_call
        self.calls = []
        self.vs_calls = []

    def send(self, url, body, timeout, limit, on_headers):
        request = store.loads(body)
        self.calls.append(request)
        name = request['response_format']['json_schema']['name']
        if name == 'content_outline':
            payload = store.loads(request['messages'][1]['content'])
            start = payload.get('target_start', 0)
            value = {'sections': [{'start_sentence': start, 'title': '입력과 저장', 'children': []}]}
        elif name == 'content_repair':
            payload = store.loads(request['messages'][1]['content'])
            value = {
                'action': 'keep',
                'evidence_indices': [s['sentence_index'] for s in payload['source']],
                'replacement': '',
                'reason': '원문을 유지한다.',
            }
        elif name == 'r1_selection':
            vs_idx = len(self.vs_calls) + 1
            items = request['messages'][0]['content']
            secs = [item['text'] for item in items if item['type'] == 'text' and item['text'].startswith('image index=')]
            n = len(secs)
            self.vs_calls.append({'index': vs_idx, 'n': n, 'labels': secs, 'request': request})
            if self.unknown_on_vs_call == vs_idx:
                raise OSError('simulated timeout/unknown on visual_select')
            if self.fail_on_vs_call == vs_idx:
                # Cycle or illegal covered_by
                value = {
                    'frames': [
                        {'index': i, 'decision': 'omit', 'covered_by': (i + 1) % n, 'reason': '순환 오류 테스트'}
                        for i in range(n)
                    ]
                }
            elif self.mode == 'keep_all':
                value = {
                    'frames': [
                        {'index': i, 'decision': 'keep', 'covered_by': None, 'reason': '고유 정보 유지'}
                        for i in range(n)
                    ]
                }
            elif self.mode == 'keep_first_rest_uncertain':
                value = {
                    'frames': [
                        {'index': i, 'decision': 'keep' if i == 0 else 'uncertain', 'covered_by': None, 'reason': '불확실 보존'}
                        for i in range(n)
                    ]
                }
            elif self.mode == 'omit_all_but_last':
                # In pass 0 omits indices 0..n-2 covered_by n-1 -> leaves carry only (< 2 after pass 0 unless >= 5? wait carry propagates!)
                value = {
                    'frames': [
                        {
                            'index': i,
                            'decision': 'keep' if i == n - 1 else 'omit',
                            'covered_by': None if i == n - 1 else n - 1,
                            'reason': '최종 화면이 앞선 상태를 포함',
                        }
                        for i in range(n)
                    ]
                }
            elif self.mode == 'two_pass_reduce':
                # Batch 1 & 2 omit half -> > 1 survives, <=4? No: keep index 0 and last index when n>=3
                keep_indices = {0, n - 1} if n >= 3 else {n - 1}
                rep = max(keep_indices)
                value = {
                    'frames': [
                        {
                            'index': i,
                            'decision': 'keep' if i in keep_indices else 'omit',
                            'covered_by': None if i in keep_indices else rep,
                            'reason': '대표 화면 유지' if i in keep_indices else '부분 상태 생략',
                        }
                        for i in range(n)
                    ]
                }
            else:
                value = {
                    'frames': [
                        {
                            'index': i,
                            'decision': 'keep' if i == n - 1 else 'omit',
                            'covered_by': None if i == n - 1 else n - 1,
                            'reason': '대표 화면 유지' if i == n - 1 else '중간 상태 생략',
                        }
                        for i in range(n)
                    ]
                }
        else:
            payload = store.loads(request['messages'][1]['content'])
            indices = [s['index'] for s in payload['assigned_sentences']]
            value = {
                'unit_id': payload['unit_id'],
                'paragraphs': [
                    {
                        'source_indices': indices,
                        'claim_mode': 'procedure',
                        'key_anchors': [],
                        'text': '입력 칸을 확인하고 저장한다.',
                    }
                ],
                'editorial_candidates': [],
                'unresolved': [],
            }
        raw = store.wire(
            {
                'choices': [{'finish_reason': 'stop', 'message': {'content': store.wire(value).decode()}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50},
            }
        )
        on_headers(200, {'Content-Length': str(len(raw))})
        return {'status': 200, 'body': raw, 'complete': True}


class VisualSelectionAlgorithmTests(unittest.TestCase):
    def test_resume_checks_saved_selection_against_job_before_publication(self):
        paragraph = {'paragraph_id': 'p', 'text': 'body', 'source_indices': [0],
                     'figures': self._figs(2)}
        rows = [{'index': i, 'decision': 'keep', 'covered_by': None, 'reason': 'keep'}
                for i in range(2)]
        call = {'pass_index': 0, 'targets': [f['asset_id'] for f in paragraph['figures']],
                'job_ref': {'job_key': 'job', 'result': {}}, 'spec': {},
                'value': {'frames': rows}}
        rec = content_selection.replay_paragraph(paragraph, [call], content_selection.POLICY)
        ctx = SimpleNamespace(work=Path('.'), settings=ContentSettings(),
                              hashes=SimpleNamespace(check=lambda *args: Path('result')))
        with patch.object(store, 'read', return_value={}), patch.object(
            infer, 'validate_entry', return_value=({'frames': rows}, 'succeeded')
        ):
            content._verify_visual_record(ctx, paragraph, rec)
            tampered = copy.deepcopy(rec)
            tampered['kept'] = []
            with self.assertRaisesRegex(ContentError, 'replay'):
                content._verify_visual_record(ctx, paragraph, tampered)
            tampered = copy.deepcopy(rec)
            tampered['calls'][0]['value']['frames'][0]['reason'] = 'changed'
            with self.assertRaisesRegex(ContentError, '호출 결과'):
                content._verify_visual_record(ctx, paragraph, tampered)

    def _figs(self, n):
        return [
            {
                'asset_id': f'vid:v0:pts{i}',
                'actual_video_seconds': f'{i}.000000000',
                'image': {'path': f'vision/cache/{i}.png', 'sha256': f'{i:064d}'},
            }
            for i in range(n)
        ]

    def test_zero_and_single_figure_make_zero_calls(self):
        for n in (0, 1):
            figs = self._figs(n)
            calls = []
            res = content_selection._run_e02_compaction(
                figs,
                lambda p, b: calls.append((p, b)),
                content_selection.POLICY,
            )
            self.assertEqual(calls, [])
            self.assertEqual(res['kept'], [f['asset_id'] for f in figs])
            self.assertEqual(res['decisions'], [])
            self.assertEqual(res['rounds'], 0)

    def test_reverse_compaction_batch_slices_carry_and_max_two_passes(self):
        # 9 figures -> pass 0 batches: [5,6,7,8], [2,3,4,carry0], [0,1,carry1]
        figs = self._figs(9)
        observed = []

        def stepper(pass_idx, batch):
            observed.append((pass_idx, [f['asset_id'] for f in batch]))
            n = len(batch)
            # Pass 0 keeps batch[0] & batch[n-1] (except carry deduplicated), compacting 9 -> 6 (>4 -> triggers pass 1)
            # Wait: let's keep the non-carry earliest + carry in batch so 6 unique survive pass 0, triggering pass 1!
            rows = []
            for idx in range(n):
                if pass_idx == 0 and idx == 1 and n > 2:
                    rows.append({'index': idx, 'decision': 'omit', 'covered_by': 0, 'reason': '생략'})
                else:
                    rows.append({'index': idx, 'decision': 'keep', 'covered_by': None, 'reason': '유지'})
            spec = {'targets': [f['asset_id'] for f in batch]}
            validated = content_selection.validate_selection({'frames': rows}, spec)
            return {'job_ref': {'job_key': f'k{pass_idx}_{len(observed)}'}}, validated['frames']

        res = content_selection._run_e02_compaction(figs, stepper, content_selection.POLICY)
        # Pass 0 reverse windows: [5,6,7,8], carry='pts5' -> [2,3,4,5], carry='pts2' -> [0,1,2]
        pass0 = [ids for pi, ids in observed if pi == 0]
        self.assertEqual(
            pass0,
            [
                ['vid:v0:pts5', 'vid:v0:pts6', 'vid:v0:pts7', 'vid:v0:pts8'],
                ['vid:v0:pts2', 'vid:v0:pts3', 'vid:v0:pts4', 'vid:v0:pts5'],
                ['vid:v0:pts0', 'vid:v0:pts1', 'vid:v0:pts2'],
            ],
        )
        # 6 survived pass 0 (pts0, pts2, pts4, pts5, pts7, pts8), so pass 1 ran on those 6 in reverse!
        pass1 = [ids for pi, ids in observed if pi == 1]
        self.assertEqual(
            pass1,
            [
                ['vid:v0:pts4', 'vid:v0:pts5', 'vid:v0:pts7', 'vid:v0:pts8'],
                ['vid:v0:pts0', 'vid:v0:pts2', 'vid:v0:pts4'],
            ],
        )
        self.assertEqual(res['rounds'], 2)
        self.assertTrue(all(pi in (0, 1) for pi, _ in observed))

    def test_termination_conditions_and_uncertain_preservation(self):
        # Case A: len(pool) <= 4 -> stops after 1 pass even if reduced to 2
        figs4 = self._figs(4)
        rounds_seen = []

        def step4(pass_idx, batch):
            rounds_seen.append(pass_idx)
            rows = [
                {'index': 0, 'decision': 'omit', 'covered_by': 3, 'reason': '중복'},
                {'index': 1, 'decision': 'omit', 'covered_by': 3, 'reason': '중복'},
                {'index': 2, 'decision': 'uncertain', 'covered_by': None, 'reason': '불확실'},
                {'index': 3, 'decision': 'keep', 'covered_by': None, 'reason': '유지'},
            ]
            return {'job_ref': {'job_key': 'k'}}, content_selection.validate_selection({'frames': rows}, {'targets': range(4)})['frames']

        res4 = content_selection._run_e02_compaction(figs4, step4, content_selection.POLICY)
        self.assertEqual(rounds_seen, [0])
        self.assertEqual(res4['rounds'], 1)
        # 'uncertain' at index 2 is NOT auto-deleted!
        self.assertEqual(res4['kept'], ['vid:v0:pts2', 'vid:v0:pts3'])

        # Case B: len(new_pool) == len(pool) (no reductions on 6 items) -> stops after pass 0
        rounds_b = []

        def step_keep_all(pass_idx, batch):
            rounds_b.append(pass_idx)
            rows = [{'index': i, 'decision': 'uncertain' if i == 1 else 'keep', 'covered_by': None, 'reason': '보존'} for i in range(len(batch))]
            return {'job_ref': {'job_key': f'k{len(rounds_b)}'}}, rows

        res_b = content_selection._run_e02_compaction(self._figs(6), step_keep_all, content_selection.POLICY)
        self.assertEqual(res_b['rounds'], 1)
        self.assertTrue(all(r == 0 for r in rounds_b))
        self.assertEqual(len(res_b['kept']), 6)

        # Case C: len(new_pool) < 2 -> stops after pass 0
        rounds_c = []

        def step_one_left(pass_idx, batch):
            rounds_c.append(pass_idx)
            n = len(batch)
            rows = [
                {'index': i, 'decision': 'keep' if i == n - 1 else 'omit', 'covered_by': None if i == n - 1 else n - 1, 'reason': '대표'}
                for i in range(n)
            ]
            return {'job_ref': {'job_key': f'k{len(rounds_c)}'}}, rows

        res_c = content_selection._run_e02_compaction(self._figs(6), step_one_left, content_selection.POLICY)
        self.assertEqual(res_c['rounds'], 1)
        self.assertEqual(res_c['kept'], ['vid:v0:pts5'])

    def test_validate_selection_rejects_illegal_cycles_and_flattens_chains(self):
        spec = {'targets': ['a', 'b', 'c']}
        # Valid intra-batch chain: 0 -> 1 -> 2 flattens to 0 -> 2
        ok = content_selection.validate_selection(
            {
                'frames': [
                    {'index': 2, 'decision': 'keep', 'covered_by': None, 'reason': '완성 화면'},
                    {'index': 1, 'decision': 'omit', 'covered_by': 2, 'reason': '중간 상태'},
                    {'index': 0, 'decision': 'omit', 'covered_by': 1, 'reason': '초기 상태'},
                ]
            },
            spec,
        )
        self.assertEqual([r['index'] for r in ok['frames']], [0, 1, 2])
        self.assertEqual(ok['frames'][0]['covered_by'], 2)

        bad_cases = [
            # Self-reference
            [{'index': 0, 'decision': 'omit', 'covered_by': 0, 'reason': 'x'}, {'index': 1, 'decision': 'keep', 'covered_by': None, 'reason': 'y'}, {'index': 2, 'decision': 'keep', 'covered_by': None, 'reason': 'z'}],
            # Omit without covered_by
            [{'index': 0, 'decision': 'omit', 'covered_by': None, 'reason': 'x'}, {'index': 1, 'decision': 'keep', 'covered_by': None, 'reason': 'y'}, {'index': 2, 'decision': 'keep', 'covered_by': None, 'reason': 'z'}],
            # Cycle 0 -> 1 -> 0
            [{'index': 0, 'decision': 'omit', 'covered_by': 1, 'reason': 'x'}, {'index': 1, 'decision': 'omit', 'covered_by': 0, 'reason': 'y'}, {'index': 2, 'decision': 'keep', 'covered_by': None, 'reason': 'z'}],
            # Omit -> uncertain (non-keep!)
            [{'index': 0, 'decision': 'omit', 'covered_by': 1, 'reason': 'x'}, {'index': 1, 'decision': 'uncertain', 'covered_by': None, 'reason': 'y'}, {'index': 2, 'decision': 'keep', 'covered_by': None, 'reason': 'z'}],
            # Keep with covered_by != None
            [{'index': 0, 'decision': 'keep', 'covered_by': 1, 'reason': 'x'}, {'index': 1, 'decision': 'keep', 'covered_by': None, 'reason': 'y'}, {'index': 2, 'decision': 'keep', 'covered_by': None, 'reason': 'z'}],
            # Duplicate index
            [{'index': 0, 'decision': 'keep', 'covered_by': None, 'reason': 'x'}, {'index': 0, 'decision': 'keep', 'covered_by': None, 'reason': 'y'}, {'index': 2, 'decision': 'keep', 'covered_by': None, 'reason': 'z'}],
        ]
        for frames in bad_cases:
            with self.assertRaises(Exception):
                content_selection.validate_selection({'frames': frames}, spec)

    def test_resolve_decisions_rejects_unknown_ids_cycles_and_unkept_uncertain(self):
        figs = self._figs(3)
        # Unknown decision asset ID
        with self.assertRaises(ValueError):
            content_selection.resolve_decisions(
                figs,
                ['vid:v0:pts2'],
                {'vid:v0:UNKNOWN': {'asset_id': 'vid:v0:UNKNOWN', 'decision': 'keep', 'covered_by': None}},
            )
        # Unknown covered_by ID
        with self.assertRaises(ValueError):
            content_selection.resolve_decisions(
                figs,
                ['vid:v0:pts2'],
                {'vid:v0:pts0': {'asset_id': 'vid:v0:pts0', 'decision': 'omit', 'covered_by': 'vid:v0:NOPE'}},
            )
        # Cross-batch cycle
        with self.assertRaises(ValueError):
            content_selection.resolve_decisions(
                figs,
                ['vid:v0:pts2'],
                {
                    'vid:v0:pts0': {'asset_id': 'vid:v0:pts0', 'decision': 'omit', 'covered_by': 'vid:v0:pts1'},
                    'vid:v0:pts1': {'asset_id': 'vid:v0:pts1', 'decision': 'omit', 'covered_by': 'vid:v0:pts0'},
                },
            )
        # Uncertain dropped from kept must raise
        with self.assertRaises(ValueError):
            content_selection.resolve_decisions(
                figs,
                ['vid:v0:pts2'],
                {'vid:v0:pts0': {'asset_id': 'vid:v0:pts0', 'decision': 'uncertain', 'covered_by': None}},
            )


from contextlib import ExitStack, contextmanager
from extractube.timestamps import decimal

STREAM_10S = {
    'index': 0,
    'width': 160,
    'height': 90,
    'codec_name': 'h264',
    'time_base': {'numerator': 1, 'denominator': 1000},
    'time_base_fraction': __import__('fractions').Fraction(1, 1000),
    'start_time': decimal(10),
    'duration': decimal(10),
    'args': [],
    'elapsed_seconds': 0,
}


def seed_long(root):
    work = root / VIDEO_ID
    work.mkdir(parents=True)
    (work / 'source').mkdir()
    (work / 'audio').mkdir()
    (work / 'source/video.mp4').write_bytes(b'synthetic-adapter-video-10s')
    texts = [
        '도입.',
        '첫 번째 입력 칸을 엽니다.',
        '두 번째 값을 입력합니다.',
        '세 번째 옵션을 고릅니다.',
        '네 번째 결과를 확인합니다.',
        '다섯 번째 요약 표를 봅니다.',
        '여섯 번째 설정을 적용합니다.',
        '마지막으로 저장합니다.',
    ]
    chunks = [
        {
            'index': i * 3,
            'text': text,
            'language': 'Korean',
            'items': [{'text': text, 'start_time': i, 'end_time': i + 1}],
        }
        for i, text in enumerate(texts)
    ]
    sentence = {
        'time_reference': 'audio_start',
        'overlap_deduplicated': True,
        'sentence_policy_id': 'fixture-v1',
        'audio': {'sample_rate': 16000, 'channels': 1, 'num_samples': 160000},
        'chunks': chunks,
        'omitted_chunks': [],
    }
    store.write(work / 'audio/alignment_sentences.json', sentence)
    records = {}
    chain = {'video': (), 'wav': ('video',), 'asr': ('wav',), 'fa': ('wav', 'asr'), 'sentences': ('fa',)}
    for kind, parents in chain.items():
        relative = (
            'source/video.mp4'
            if kind == 'video'
            else ('audio/alignment_sentences.json' if kind == 'sentences' else f'audio/{kind}.json')
        )
        p = work / relative
        if not p.exists():
            store.write(p, {'fixture': kind})
        records[kind] = store.result_record(
            kind=kind,
            relative_path=relative,
            file_hash=store.sha(p),
            upstream=[store.identity(records[k]) for k in parents],
            settings={},
            media={
                'video_start_time_seconds': 10,
                'source_audio_start_time_seconds': 10.25,
                'audio_to_video_offset_seconds': 0.25,
            }
            if kind == 'video'
            else None,
        )
    store.write(
        work / 'metadata.json',
        {
            'video_id': VIDEO_ID,
            'source_url': 'https://example.test/watch?v=Fixture1234',
            'results': records,
            'video_start_time_seconds': 10,
            'source_audio_start_time_seconds': 10.25,
            'audio_to_video_offset_seconds': 0.25,
        },
    )
    return work


@contextmanager
def long_adapters(transport):
    with ExitStack() as stack:
        stack.enter_context(adapters(transport))
        stack.enter_context(patch('extractube.visual_input.changes.probe_video', return_value=STREAM_10S.copy()))
        stack.enter_context(
            patch(
                'extractube.visual_input.changes.confirm_end',
                return_value={'last_pts': 19900, 'last_video_seconds': '9.900000000'},
            )
        )
        stack.enter_context(patch('extractube.frames.extract', side_effect=distinct_fake_extract))
        yield


class VisualSelectionProductIntegrationTests(unittest.TestCase):
    def _seed_multifig(self, root):
        work = seed_long(root)
        transport = Stage2AllUseTransport()
        with long_adapters(transport):
            vis = vision.run(VIDEO_ID, visual_settings(), data_root=root, log=None)
        self.assertTrue(vis.stage3_usable)
        return work, vis

    def test_default_enabled_and_common_paths_direct_single_batch(self):
        self.assertEqual(
            ContentSettings().visual_selection_policy,
            'r1.editorial_visual_selection.e02.reverse_compaction.v1',
        )
        # Compare direct content.run vs pipeline.run vs pipeline.run_batch on identical fixtures
        outcomes = {}
        for mode in ('direct', 'single', 'batch'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                work, vis = self._seed_multifig(root)
                vs_transport = ProductStage3Transport(mode='two_pass_reduce')
                cfg = ContentSettings(max_inference_calls=20, max_http_seconds=3000)
                if mode == 'direct':
                    with patch.object(infer, 'make_transport', return_value=vs_transport):
                        res = content.run(VIDEO_ID, cfg, data_root=root, counter=FixedCounter(), log=None)
                elif mode == 'single':
                    with long_adapters(Stage2AllUseTransport()), patch.object(
                        pipeline, '_reusable', return_value=True
                    ), patch('extractube.media.resolve_video_id', return_value=VIDEO_ID), patch.object(
                        infer, 'make_transport', return_value=vs_transport
                    ):
                        r = pipeline.run(
                            'https://example.test/watch?v=' + VIDEO_ID,
                            Settings(data_root=root),
                            through='stage3',
                            vision_settings=visual_settings(),
                            content_settings=cfg,
                            video_id=VIDEO_ID,
                            counter=FixedCounter(),
                            log=None,
                        )
                        res = r.content
                else:
                    with long_adapters(Stage2AllUseTransport()), patch.object(
                        pipeline, '_reusable', return_value=True
                    ), patch('extractube.media.resolve_video_id', return_value=VIDEO_ID), patch.object(
                        infer, 'make_transport', return_value=vs_transport
                    ):
                        b = pipeline.run_batch(
                            ['https://example.test/watch?v=' + VIDEO_ID],
                            Settings(data_root=root),
                            through='stage3',
                            vision_settings=visual_settings(),
                            content_settings=cfg,
                            counter=FixedCounter(),
                            log=None,
                        )
                        res = b.items[0].result.content
                verified = content.verify(VIDEO_ID, data_root=root, run_id=res.run_id)
                self.assertTrue(verified['ok'])
                art = store.read(res.article_path)
                vs_doc = store.read(res.article_path.parent / 'visual_selection.json')
                report = store.read(res.report_path)
                self.assertEqual(vs_doc['policy'], content_selection.POLICY)
                self.assertGreater(vs_doc['before_assets'], 4)
                self.assertLess(vs_doc['after_assets'], vs_doc['before_assets'])
                self.assertEqual(report['assets'], vs_doc['after_assets'])
                self.assertEqual(report['visual_selection']['after_assets'], len(art['asset_placements']))
                # Figures in sections match asset_placements identically
                flat_figs = [f for s in art['sections'] for p in s['paragraphs'] for f in p['figures']]
                self.assertEqual(flat_figs, art['asset_placements'])
                # Prose, TOC, captions, and image SHA-256 unchanged; render_only makes 0 calls
                html_text = res.html_path.read_text(encoding='utf-8')
                self.assertIn('<nav aria-label="목차">', html_text)
                self.assertIn('입력 칸을 확인하고 저장한다.', html_text)
                calls_before_render = len(vs_transport.calls)
                rendered = content.render_only(VIDEO_ID, data_root=root, run_id=res.run_id)
                self.assertEqual(rendered.inference_calls, 0)
                self.assertEqual(len(vs_transport.calls), calls_before_render)
                outcomes[mode] = (vs_doc['kept'] if 'kept' in vs_doc else vs_doc['after_asset_ids'], len(vs_transport.vs_calls))
        self.assertEqual(outcomes['direct'], outcomes['single'])
        self.assertEqual(outcomes['single'], outcomes['batch'])

    def test_budget_pause_and_resume_mid_selection_preserves_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_multifig(root)
            transport = ProductStage3Transport(mode='two_pass_reduce')
            # First run: enough for outline(1) + write(1) + first visual_select batch(1) = 3 calls total, pause before 2nd vs batch!
            low = ContentSettings(max_inference_calls=3, max_http_seconds=3000)
            with patch.object(infer, 'make_transport', return_value=transport):
                first = content.run(VIDEO_ID, low, data_root=root, counter=FixedCounter(), log=None)
                self.assertEqual(first.execution_status, 'paused_budget')
                self.assertEqual(len(transport.calls), 3)
                self.assertEqual(len(transport.vs_calls), 1)
                self.assertFalse((root / VIDEO_ID / 'content/current.json').exists())
                self.assertFalse((root / VIDEO_ID / 'content/runs' / first.run_id / 'article.json').exists())

                # Resume with higher budget: outline(1) + write(1) + batch1(1) are reused, only remaining batches sent!
                second = content.run(
                    VIDEO_ID,
                    low,
                    data_root=root,
                    resume_run_id=first.run_id,
                    resume_budget_limits={'max_inference_calls': 15},
                    counter=FixedCounter(),
                    log=None,
                )
                self.assertIn(second.execution_status, content.COMPLETE)
                report = store.read(second.report_path)
                self.assertEqual(report['budget']['http_calls'], len(transport.calls))
                self.assertEqual(
                    report['visual_selection']['selection_calls'],
                    len(transport.vs_calls),
                )
                self.assertTrue(content.verify(VIDEO_ID, data_root=root, run_id=second.run_id)['ok'])

    def test_unknown_and_failed_selection_preserve_and_do_not_promote(self):
        for kind in ('unknown', 'invalid'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._seed_multifig(root)
                transport = ProductStage3Transport(
                    unknown_on_vs_call=1 if kind == 'unknown' else None,
                    fail_on_vs_call=1 if kind == 'invalid' else None,
                )
                cfg = ContentSettings(max_inference_calls=20, max_http_seconds=3000)
                with patch.object(infer, 'make_transport', return_value=transport):
                    if kind == 'unknown':
                        res = content.run(VIDEO_ID, cfg, data_root=root, counter=FixedCounter(), log=None)
                        self.assertEqual(res.execution_status, 'blocked_unknown')
                        with self.assertRaises(ContentError) as caught:
                            content.run(VIDEO_ID, cfg, data_root=root, counter=FixedCounter(), log=None)
                        self.assertEqual(caught.exception.kind, 'blocked_unknown')
                    else:
                        with self.assertRaises(InferenceJobFailed):
                            content.run(VIDEO_ID, cfg, data_root=root, counter=FixedCounter(), log=None)
                self.assertFalse((root / VIDEO_ID / 'content/current.json').exists())

    def test_cache_invalidation_on_policy_text_source_bytes_and_order(self):
        figs = [
            {
                'asset_id': f'a{i}',
                'actual_video_seconds': f'{i}.0',
                'image': {'path': f'p{i}.png', 'sha256': f'{i:064x}'},
            }
            for i in range(2)
        ]
        paragraph = {'paragraph_id': 'u0-p000', 'text': '본문 텍스트', 'source_indices': [0], 'figures': figs}
        bundle = {'sentences': [{'sentence_index': 0, 'text': '원문 문장'}]}
        s1 = ContentSettings()
        base = content_selection.paragraph_identity(s1, FixedCounter(), paragraph, bundle)

        # Changing text, source, image hash, image order, or max_pixels must alter identity
        p_text = copy.deepcopy(paragraph)
        p_text['text'] = '다른 본문 텍스트'
        self.assertNotEqual(base, content_selection.paragraph_identity(s1, FixedCounter(), p_text, bundle))

        b_src = {'sentences': [{'sentence_index': 0, 'text': '변경된 원문 문장'}]}
        self.assertNotEqual(base, content_selection.paragraph_identity(s1, FixedCounter(), paragraph, b_src))

        p_hash = copy.deepcopy(paragraph)
        p_hash['figures'][0]['image']['sha256'] = 'f' * 64
        self.assertNotEqual(base, content_selection.paragraph_identity(s1, FixedCounter(), p_hash, bundle))

        p_order = copy.deepcopy(paragraph)
        p_order['figures'].reverse()
        self.assertNotEqual(base, content_selection.paragraph_identity(s1, FixedCounter(), p_order, bundle))

        s2 = replace(s1, visual_selection_max_pixels=131072)
        self.assertNotEqual(base, content_selection.paragraph_identity(s2, FixedCounter(), paragraph, bundle))

        # Policy snapshot projection changes when policy disabled vs enabled
        c_on = content.snapshot(s1, FixedCounter())
        c_off = content.snapshot(replace(s1, visual_selection_policy='none'), FixedCounter())
        self.assertNotEqual(c_on['projections']['visual_select'], c_off['projections']['visual_select'])

    def test_legacy_config_compatibility_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_multifig(root)
            transport = ProductStage3Transport(mode='two_pass_reduce')
            legacy_cfg = ContentSettings(
                max_inference_calls=15,
                max_http_seconds=3000,
                visual_selection_policy='none',
            )
            with patch.object(infer, 'make_transport', return_value=transport):
                legacy_run = content.run(VIDEO_ID, legacy_cfg, data_root=root, counter=FixedCounter(), log=None)
                run_dir = root / VIDEO_ID / 'content/runs' / legacy_run.run_id
                # Strip new fields from config.json to simulate pre-E02 persisted config.json
                raw_cfg = store.read(run_dir / 'config.json')
                raw_cfg['settings'].pop('visual_selection_policy')
                raw_cfg['settings'].pop('visual_selection_max_pixels')
                raw_cfg['projections'].pop('visual_select')
                raw_cfg['code_sha256'].pop('content_selection.py')
                store.write(run_dir / 'config.json', raw_cfg)
                rep = store.read(run_dir / 'result.json')
                rep['artifacts']['config.json'] = store.ref(root / VIDEO_ID, run_dir / 'config.json')
                store.write(run_dir / 'result.json', rep)
                cur = store.read(root / VIDEO_ID / 'content/current.json')
                cur['result'] = store.ref(root / VIDEO_ID, run_dir / 'result.json')
                store.write(root / VIDEO_ID / 'content/current.json', cur)
                # load_settings must NOT silently enable E02!
                reloaded = content.load_settings(VIDEO_ID, root, legacy_run.run_id)
                self.assertEqual(reloaded.visual_selection_policy, 'none')
                # Legacy verify passes
                self.assertTrue(content.verify(VIDEO_ID, data_root=root, run_id=legacy_run.run_id)['ok'])
                # Legacy paused run cannot be resumed cleanly under new config; new run runs E02 cleanly
                legacy_state = store.read(run_dir / 'state.json')
                legacy_state['execution_status'] = 'paused_budget'
                store.write(run_dir / 'state.json', legacy_state)
                with self.assertRaises(ContentError) as caught:
                    content.run(VIDEO_ID, reloaded, data_root=root, resume_run_id=legacy_run.run_id, counter=FixedCounter(), log=None)
                self.assertEqual(caught.exception.kind, 'input')
                # New E02 run succeeds cleanly without being blocked by legacy paused run
                e02_run = content.run(
                    VIDEO_ID,
                    ContentSettings(max_inference_calls=20, max_http_seconds=3000),
                    data_root=root,
                    counter=FixedCounter(),
                    log=None,
                )
                self.assertIn(e02_run.execution_status, content.COMPLETE)
                self.assertTrue(content.verify(VIDEO_ID, data_root=root, run_id=e02_run.run_id)['ok'])
                # Tampering visual_selection.json or asset_placements is rejected by verify
                e02_dir = root / VIDEO_ID / 'content/runs' / e02_run.run_id
                art = store.read(e02_dir / 'article.json')
                art['asset_placements'] = []
                store.write(e02_dir / 'article.json', art)
                with self.assertRaises(Exception):
                    content.verify(VIDEO_ID, data_root=root, run_id=e02_run.run_id)

    def test_saved_e02_replay_and_token_counter_through_product_run(self):
        if not MODEL_DIR.is_dir():
            self.skipTest('Local Qwen tokenizer directory not available')
        sel_file = SAVED_E02_ROOT / 'selected/7EJ-xotXiSE/u0002-p000.json'
        if not sel_file.is_file():
            self.skipTest('Saved E02 fixture not available')
        sel_saved = json.loads(sel_file.read_text(encoding='utf-8'))
        call_keys = sel_saved['calls']
        # Extract the exact source paragraph text, context sentences, and base64 PNGs from saved E02 calls!
        first_req = json.loads((SAVED_E02_ROOT / 'calls' / call_keys[0] / 'request.json').read_text(encoding='utf-8'))
        header_text = first_req['messages'][0]['content'][0]['text']
        body_and_ctx = header_text.split('\n본문:\n', 1)[1]
        para_text, ctx_text = body_and_ctx.split('\n대응 원문:\n', 1)
        ctx_lines = [line for line in ctx_text.splitlines() if line.strip()]

        # Collect image bytes & video_seconds for every asset_id from the saved calls
        asset_images = {}
        asset_secs = {}
        saved_responses = []
        for ck in call_keys:
            req = json.loads((SAVED_E02_ROOT / 'calls' / ck / 'request.json').read_text(encoding='utf-8'))
            rec = json.loads((SAVED_E02_ROOT / 'calls' / ck / 'record.json').read_text(encoding='utf-8'))
            resp_raw = (SAVED_E02_ROOT / 'calls' / ck / 'response.body').read_bytes()
            saved_responses.append((req, rec, resp_raw))
            content_items = req['messages'][0]['content']
            # Map text labels to image_url bytes in order
            pairs = []
            for idx in range(1, len(content_items), 2):
                lbl = content_items[idx]['text']
                sec = lbl.split('video_seconds=', 1)[1]
                b64 = content_items[idx + 1]['image_url']['url'].split(',', 1)[1]
                pairs.append((sec, base64.b64decode(b64)))
            asset_images[ck] = pairs
            for sec, raw_png in pairs:
                asset_secs[sec] = raw_png

        # Build the 5 saved decision entries sorted chronologically by video_seconds
        saved_by_aid = {d['asset_id']: d for d in sel_saved['decisions']}
        # Map batch order inside calls to deduce exact asset_id <-> (video_seconds, png_bytes)
        ordered_aids = [d['asset_id'] for d in sorted(sel_saved['decisions'], key=lambda r: int(r['asset_id'].split('pts')[-1]))]
        # Verify using the known 5 asset IDs sorted by pts: batch 0 in call 0 has indices 1..4 ([pts1293292, pts1338337, pts1458457, pts1578577])
        # and call 1 has [pts1068067, carry]
        aid_to_png = {}
        aid_to_sec = {}
        for i, aid in enumerate(ordered_aids[1:]):
            sec, raw_png = asset_images[call_keys[0]][i]
            aid_to_sec[aid] = sec
            aid_to_png[aid] = raw_png
        aid_to_sec[ordered_aids[0]], aid_to_png[ordered_aids[0]] = asset_images[call_keys[1]][0]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / VIDEO_ID
            work.mkdir(parents=True)
            (work / 'source').mkdir()
            (work / 'audio').mkdir()
            (work / 'source/video.mp4').write_bytes(b'replay-video')
            chunks = []
            for idx, line in enumerate(ctx_lines):
                chunks.append(
                    {
                        'index': idx,
                        'text': line,
                        'language': 'Korean',
                        'items': [{'text': line, 'start_time': idx, 'end_time': idx + 1}],
                    }
                )
            sentence = {
                'time_reference': 'audio_start',
                'overlap_deduplicated': True,
                'sentence_policy_id': 'fixture-v1',
                'audio': {'sample_rate': 16000, 'channels': 1, 'num_samples': 64000},
                'chunks': chunks,
                'omitted_chunks': [],
            }
            store.write(work / 'audio/alignment_sentences.json', sentence)
            records = {}
            chain = {'video': (), 'wav': ('video',), 'asr': ('wav',), 'fa': ('wav', 'asr'), 'sentences': ('fa',)}
            for kind, parents in chain.items():
                rel = 'source/video.mp4' if kind == 'video' else ('audio/alignment_sentences.json' if kind == 'sentences' else f'audio/{kind}.json')
                p = work / rel
                if not p.exists():
                    store.write(p, {'fixture': kind})
                records[kind] = store.result_record(
                    kind=kind,
                    relative_path=rel,
                    file_hash=store.sha(p),
                    upstream=[store.identity(records[k]) for k in parents],
                    settings={},
                    media={'video_start_time_seconds': 0, 'source_audio_start_time_seconds': 0, 'audio_to_video_offset_seconds': 0}
                    if kind == 'video'
                    else None,
                )
            store.write(
                work / 'metadata.json',
                {
                    'video_id': VIDEO_ID,
                    'source_url': 'https://example.test/watch?v=replay',
                    'results': records,
                    'video_start_time_seconds': 0,
                    'source_audio_start_time_seconds': 0,
                    'audio_to_video_offset_seconds': 0,
                },
            )
            # Build minimal valid vision artifacts passing handoff.verify_locked or mock load_input cleanly to test full Stage 3 content.run
            assets = []
            asset_refs = []
            for aid in ordered_aids:
                img_path = work / 'vision/cache/replay' / f'{aid.split(":")[-1]}.png'
                img_path.parent.mkdir(parents=True, exist_ok=True)
                img_path.write_bytes(aid_to_png[aid])
                iref = store.ref(work, img_path)
                # Include image dimensions like Stage 2 ref
                with Image.open(img_path) as im:
                    iref['width'], iref['height'] = im.size
                iref['format'] = 'png'
                iref['color'] = 'rgb24'
                assets.append({'asset_id': aid, 'image': iref})
                asset_refs.append(
                    {
                        'asset_id': aid,
                        'association_id': f'assoc@{aid}',
                        'caption': 'saved e02 replay frame',
                        'actual_video_seconds': aid_to_sec[aid],
                    }
                )
            bundle_in = {
                'video_id': VIDEO_ID,
                'units': [
                    {
                        'unit_id': 'u0000',
                        'title': '리플레이 단원',
                        'sentences': [
                            {
                                'sentence_index': idx,
                                'source_chunk_index': idx,
                                'audio_start': str(idx),
                                'audio_end': str(idx + 1),
                                'text': line,
                                'language': 'Korean',
                                'source_chunk': chunks[idx],
                            }
                            for idx, line in enumerate(ctx_lines)
                        ],
                        'asset_refs': asset_refs,
                    }
                ],
                'assets': assets,
                'coverage': 'complete',
                'omitted_audio_chunks': [],
                'warnings': [],
                'usage_policy': 'source_preserving',
                'source': {'audio_to_video_offset_seconds': '0'},
                'source_url': 'https://example.test/watch?v=replay',
            }
            fake_vr = work / 'vision/runs/20261115T000000Z_replay000001/result.json'
            store.write(fake_vr, {'ok': True})
            input_doc = {
                'schema_version': 'extractube.content.input.v1',
                'video_id': VIDEO_ID,
                'vision_run_id': fake_vr.parent.name,
                'vision_result': store.ref(work, fake_vr),
                'handoff': store.ref(work, fake_vr),
                'stage1_identities': {k: store.identity(v) for k, v in records.items()},
                'critical_refs': [a['image'] for a in assets],
            }
            input_doc['input_digest'] = store.digest(input_doc)
            replay_bundle = content_plan.source_bundle(bundle_in)

            real_counter = content_plan.TokenCounter(str(MODEL_DIR), max_pixels=262144)

            class ReplayTransport:
                def __init__(self):
                    self.vs_seen = 0

                def send(self, url, body, timeout, limit, on_headers):
                    request = store.loads(body)
                    name = request['response_format']['json_schema']['name']
                    if name == 'content_outline':
                        value = {'sections': [{'start_sentence': 0, 'title': '리플레이 제목', 'children': []}]}
                        raw = store.wire(
                            {
                                'choices': [{'finish_reason': 'stop', 'message': {'content': store.wire(value).decode()}}],
                                'usage': {'prompt_tokens': real_counter.count(request), 'completion_tokens': 20},
                            }
                        )
                    elif name == 'content_write':
                        value = {
                            'unit_id': 'u0000',
                            'paragraphs': [
                                {
                                    'source_indices': list(range(len(ctx_lines))),
                                    'claim_mode': 'source_fact',
                                    'key_anchors': [],
                                    'text': para_text,
                                }
                            ],
                            'editorial_candidates': [],
                            'unresolved': [],
                        }
                        raw = store.wire(
                            {
                                'choices': [{'finish_reason': 'stop', 'message': {'content': store.wire(value).decode()}}],
                                'usage': {'prompt_tokens': real_counter.count(request), 'completion_tokens': 40},
                            }
                        )
                    elif name == 'r1_selection':
                        exp_req, exp_rec, resp_raw = saved_responses[self.vs_seen]
                        # Verify product request matches saved E02 request byte/content-for-content!
                        self.vs_seen += 1
                        assert request['messages'][0]['content'][0]['text'] == exp_req['messages'][0]['content'][0]['text']
                        assert len(request['messages'][0]['content']) == len(exp_req['messages'][0]['content'])
                        assert real_counter.count(request) == exp_rec['input']['tokens']
                        raw = resp_raw
                    else:
                        raise AssertionError(name)
                    on_headers(200, {'Content-Length': str(len(raw))})
                    return {'status': 200, 'body': raw, 'complete': True}

            replay_transport = ReplayTransport()
            with patch.object(
                content,
                'load_input',
                side_effect=lambda *args, **kwargs: (copy.deepcopy(input_doc), copy.deepcopy(replay_bundle)),
            ), patch.object(infer, 'make_transport', return_value=replay_transport):
                res = content.run(
                    VIDEO_ID,
                    ContentSettings(tokenizer_path=str(MODEL_DIR), max_inference_calls=20, max_http_seconds=3000),
                    data_root=root,
                    vision_run_id=fake_vr.parent.name,
                    counter=real_counter,
                    log=None,
                )
                self.assertTrue(content.verify(VIDEO_ID, data_root=root, run_id=res.run_id)['ok'])
            self.assertEqual(replay_transport.vs_seen, len(saved_responses))
            vs_doc = store.read(res.article_path.parent / 'visual_selection.json')
            self.assertEqual(vs_doc['after_asset_ids'], sel_saved['kept'])
            actual_decisions = {d['asset_id']: d for d in vs_doc['paragraphs'][0]['decisions']}
            for aid, sdec in saved_by_aid.items():
                self.assertEqual(actual_decisions[aid]['decision'], sdec['decision'])
                self.assertEqual(actual_decisions[aid]['covered_by'], sdec['covered_by'])
                self.assertEqual(actual_decisions[aid]['final_covered_by'], sdec['final_covered_by'])


if __name__ == '__main__':
    unittest.main()
